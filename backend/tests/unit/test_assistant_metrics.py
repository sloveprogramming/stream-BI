"""问数侧的口径对齐。

两条主张，各有硬断言：

1. **口径必须进上下文**（模型没有企业先验知识，不给它就只能猜）；
2. **口径歧义由字典判定，不交给模型**（交给模型 = 它会自信地给一个错的数）。
   第 2 条用一个"被调用就报错"的假 LLM 来证：歧义时模型一次都不该被问到。
"""
import yaml

from app.assistant.agent import AgentContext, build_graph, stream_agent
from app.assistant.prompts import user_prompt
from app.metrics import load_dictionary

FORMULA = "(amount - cost) / nullif(amount, 0)"


def _dictionary(workdir, entries):
    path = workdir / "metrics.yml"
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "metrics_dictionary": entries},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_dictionary(path)


def _gross_margin():
    return {
        "name": "gross_margin",
        "label": "毛利率",
        "synonyms": ["毛利", "毛利率"],
        "expr": FORMULA,
        "dataset": "sales_order",
        "owner": "财务共享中心",
        "description": "支付口径下的毛利率",
    }


def _two_quotas():
    return [
        {
            "name": "sales_amount_paid",
            "label": "支付口径销售额",
            "synonyms": ["销售额", "支付"],
            "expr": "sum(paid_amount)",
            "dataset": "sales_order",
            "owner": "财务共享中心",
            "description": "客户已支付的金额",
        },
        {
            "name": "sales_amount_collected",
            "label": "回款口径销售额",
            "synonyms": ["销售额", "回款"],
            "expr": "sum(collected_amount)",
            "dataset": "sales_order",
            "owner": "财务共享中心",
            "description": "实际收到款的金额",
        },
    ]


# ---------------------------------------------------------------- 注入


def test_user_prompt_carries_definition_and_synonyms(workdir):
    dictionary = _dictionary(workdir, [_gross_margin()])
    prompt = user_prompt("表 sales_order 字段 …", "上月毛利率", metrics_prompt=dictionary.render())

    assert FORMULA in prompt  # 口径本体
    assert "gross_margin" in prompt  # 机器名：模型写 SQL 时用它对应的公式
    context_part = prompt.split("用户问题：")[0]
    assert "毛利" in context_part  # 同义词要出现在上下文里，否则"毛利"这类说法对不上


def test_metrics_block_sits_before_the_question(workdir):
    """口径是"规则"，必须排在问题之前 —— 排在后面容易被模型当成补充说明忽略。"""
    dictionary = _dictionary(workdir, [_gross_margin()])
    prompt = user_prompt("schema", "问题", metrics_prompt=dictionary.render())
    assert prompt.index(FORMULA) < prompt.index("用户问题：")


def test_user_prompt_is_unsalted_when_no_metrics():
    prompt = user_prompt("schema", "问题")
    assert prompt == "schema\n\n用户问题：问题"


def test_schema_context_carries_the_metric_block(flow_env, load_user):
    """口径与表结构**一起**构建 —— 三个消费方（chat / agent / eval）自动都拿到，
    不用各自去加载字典（那是"两处各解析一次、迟早不一致"的老路）。"""
    from app.assistant.schema_context import build_schema_context

    ids, session_factory, pg_engine = flow_env
    schema = build_schema_context(session_factory, pg_engine, load_user(ids["owner"]))

    assert "metrics" in schema
    assert isinstance(schema["metrics"], str)


# ---------------------------------------------------------------- 歧义反问


class _ExplodingLlm:
    """被调用就报错。用来证明"这一步根本不需要模型"。"""

    def sql(self, system, user):  # pragma: no cover - 走到这里就是失败
        raise AssertionError("口径歧义必须先反问，不该生成 SQL")

    def json(self, system, user):  # pragma: no cover
        raise AssertionError("口径歧义该由字典判定，不该问模型")

    def text(self, system, user):  # pragma: no cover
        raise AssertionError("口径歧义必须先反问，不该直接给结论")


def _run(goal, dictionary):
    ctx = AgentContext(
        session_factory=None,
        pg_engine=None,
        user=None,
        metrics_dictionary=dictionary,
    )
    final = {}
    for node, update in stream_agent(goal, ctx, llm=_ExplodingLlm()):
        if node == "__final__":
            final = update
    return final


def test_ambiguous_question_is_clarified_without_calling_the_model(workdir):
    """「销售额」同时是支付口径与回款口径的说法 → 必须反问，且不默认选一个。"""
    final = _run("上月销售额是多少", _dictionary(workdir, _two_quotas()))

    assert final.get("next") == "end"
    clarification = final.get("clarification") or {}
    assert clarification.get("question")
    assert len(clarification.get("options") or []) == 2
    joined = " ".join(clarification["options"])
    assert "支付" in joined and "回款" in joined


def test_clarify_records_which_metrics_triggered_it(workdir):
    """审计要能看出反问是因为哪几个指标 —— 否则事后无从复盘。"""
    final = _run("上月销售额是多少", _dictionary(workdir, _two_quotas()))
    steps = final.get("steps") or []
    clarify = [s for s in steps if s.get("node") == "clarify"]
    assert clarify, "应当留下一条 clarify 轨迹"
    assert set(clarify[0]["output"]["metrics"]) == {
        "sales_amount_paid",
        "sales_amount_collected",
    }


def test_disambiguated_question_does_not_stop_here(workdir):
    """问题里带了「回款」 → 已说清，clarify 不该在这里终止。

    这一步验证字典判定**没有误报歧义**：clarify 应当放行（next="plan"），
    并把"还缺不缺范围条件"交给模型判断。
    """
    calls = []

    class _Recorder:
        def sql(self, system, user):  # pragma: no cover
            raise AssertionError("不该走到 SQL 生成")

        def json(self, system, user):
            calls.append(system[:8])
            return {"ambiguous": False}

        def text(self, system, user):  # pragma: no cover
            raise AssertionError("不该走到回答")

    ctx = AgentContext(
        session_factory=None,
        pg_engine=None,
        user=None,
        metrics_dictionary=_dictionary(workdir, _two_quotas()),
    )
    graph = build_graph(ctx, _Recorder())
    initial = {"goal": "上月回款销售额", "plan": [], "steps": []}

    # 只取第一个节点就返回：后面 plan 需要数据库，本测试不进入
    for chunk in graph.stream(initial, {"recursion_limit": 10}, stream_mode="updates"):
        for node, update in (chunk or {}).items():
            if node == "clarify":
                assert update.get("next") == "plan", "「回款」已消歧，不该反问口径"
                assert calls, "没有口径歧义时，仍应交由模型判断其它澄清事项"
                return
    raise AssertionError("clarify 节点没有被执行")
