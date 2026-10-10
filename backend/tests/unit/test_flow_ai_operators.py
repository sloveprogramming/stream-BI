"""AI 算子：分类 / 判定 / 抽取，以及成本三控（去重、缓存、分批）。

不联网：把「怎么问模型」那层打桩，验证的是数据加工与成本逻辑。
"""
import pytest

from app.ai.operators import run_llm_batch
from app.flow.dsl import FlowNode
from app.flow.errors import FlowExecutionError, FlowValidationError
from app.flow.operators import get_operator
from app.models import AiLlmCache


def node(op_type: str, **params) -> FlowNode:
    return FlowNode(id="ai1", type=op_type, params=params)


@pytest.fixture
def stub_classify(monkeypatch):
    """把分类调用换成假的，并记录每次实际问了哪些文本。"""
    from app.flow.operators import ai as ai_ops

    calls: list[list[str]] = []

    def _fake(values, *, labels, instructions, criteria=""):
        calls.append(list(values))
        return {v: labels[0] for v in values}

    monkeypatch.setattr(ai_ops, "_call_classify", _fake)
    return calls


# ---------- 分类 ----------


def test_classify_appends_label_column(stub_classify):
    op = get_operator("llm.classify")
    cols, rows = op.run_python(
        node=node(
            "llm.classify",
            input_column="title",
            labels=["坚果", "果干"],
            output_column="category",
        ),
        columns=["id", "title"],
        rows=[[1, "碧根果"], [2, "蔓越莓干"]],
    )
    assert cols == ["id", "title", "category"]
    assert rows == [[1, "碧根果", "坚果"], [2, "蔓越莓干", "坚果"]]


def test_classify_dedupes_identical_text(stub_classify):
    """同一段文本只问一次模型 —— 商品表里标题重复很常见，这是最直接的成本控制。"""
    op = get_operator("llm.classify")
    _, rows = op.run_python(
        node=node("llm.classify", input_column="title", labels=["A", "B"]),
        columns=["title"],
        rows=[["碧根果"], ["碧根果"], ["核桃"]],
    )
    assert sorted(stub_classify[0]) == ["核桃", "碧根果"]
    assert len(rows) == 3  # 输出行数与输入一致，去重只影响调用次数


def test_classify_rejects_unknown_input_column(stub_classify):
    op = get_operator("llm.classify")
    with pytest.raises(FlowExecutionError, match="找不到输入列"):
        op.run_python(
            node=node("llm.classify", input_column="nope", labels=["A", "B"]),
            columns=["title"],
            rows=[["x"]],
        )


def test_classify_rejects_non_ascii_output_column(stub_classify):
    """输出列会变成物理列名，必须 ASCII —— 中文展示名属于字段注释。"""
    op = get_operator("llm.classify")
    with pytest.raises(FlowValidationError, match="输出列名"):
        op.run_python(
            node=node("llm.classify", input_column="title", labels=["A", "B"], output_column="类目"),
            columns=["title"],
            rows=[["x"]],
        )


def test_classify_rejects_single_label():
    """只有一个标签的分类没有意义，参数校验就该挡下。"""
    from app.flow.errors import FlowValidationError as E

    op = get_operator("llm.classify")
    with pytest.raises(E, match="参数不合法"):
        op.parse(node("llm.classify", input_column="t", labels=["只有一个"]))


# ---------- 判定 ----------


def test_judge_uses_criteria(stub_classify):
    op = get_operator("llm.judge")
    cols, rows = op.run_python(
        node=node(
            "llm.judge",
            input_column="note",
            criteria="收货仓与订单仓不一致即为串货",
            labels=["疑似串货", "正常"],
            output_column="verdict",
        ),
        columns=["order_id", "note"],
        rows=[[1, "德清发安徽"]],
    )
    assert cols == ["order_id", "note", "verdict"]
    assert rows[0][2] == "疑似串货"


# ---------- 抽取 ----------


def test_extract_creates_one_column_per_field(monkeypatch):
    from app.flow.operators import ai as ai_ops

    def _fake(values, *, fields):
        return {v: {name: f"{name}-value" for name in fields} for v in values}

    monkeypatch.setattr(ai_ops, "_call_extract", _fake)
    op = get_operator("llm.extract")
    cols, rows = op.run_python(
        node=node("llm.extract", input_column="msg", fields={"city": "收货城市", "contact": "联系人"}),
        columns=["id", "msg"],
        rows=[[1, "发杭州张三收"]],
    )
    assert cols == ["id", "msg", "city", "contact"]
    assert rows == [[1, "发杭州张三收", "city-value", "contact-value"]]


# ---------- 批量管道：去重 / 缓存 / 分批 ----------


def test_run_llm_batch_dedupes_and_chunks():
    seen: list[list[str]] = []

    def per_batch(batch):
        seen.append(list(batch))
        return {v: v.upper() for v in batch}

    result = run_llm_batch(
        values=["a", "a", "b", "c", "d", "e"],
        cache_key="t1",
        per_batch=per_batch,
        batch_size=2,
    )
    assert result == {"a": "A", "b": "B", "c": "C", "d": "D", "e": "E"}
    assert sorted(len(c) for c in seen) == [1, 2, 2]  # 5 个唯一值按 2 一批


def test_run_llm_batch_handles_missing_results():
    """模型漏答某条时留给调用方 None，而不是让整批失败。"""

    def per_batch(batch):
        return {batch[0]: "ok"}

    result = run_llm_batch(values=["x", "y"], cache_key="t2", per_batch=per_batch)
    assert result["x"] == "ok"
    assert result["y"] is None


def test_run_llm_batch_normalizes_none_to_empty_string():
    seen: list[list[str]] = []

    def per_batch(batch):
        seen.append(list(batch))
        return {v: "L" for v in batch}

    run_llm_batch(values=[None, None, "a"], cache_key="t3", per_batch=per_batch)
    assert sorted(seen[0]) == ["", "a"]


def test_second_run_hits_cache_and_skips_model(flow_env, monkeypatch):
    """缓存要真的省调用：同输入第二次不再问模型。"""
    from app.flow.operators import ai as ai_ops

    env, sf, _ = flow_env
    suffix = env["suffix"]
    calls: list[list[str]] = []

    def _fake(values, *, labels, instructions, criteria=""):
        calls.append(list(values))
        return {v: labels[0] for v in values}

    monkeypatch.setattr(ai_ops, "_call_classify", _fake)
    op = get_operator("llm.classify")
    # instructions 里带 suffix，让 cache_key 只属于本次测试，便于清理
    spec = node(
        "llm.classify",
        input_column="title",
        labels=["坚果", "果干"],
        instructions=f"test-{suffix}",
    )

    with sf() as session:
        op.run_python(node=spec, columns=["title"], rows=[["碧根果"], ["核桃"]], session=session)
        session.commit()
    assert len(calls) == 1

    with sf() as session:
        op.run_python(node=spec, columns=["title"], rows=[["碧根果"], ["核桃"]], session=session)
        session.commit()
    assert len(calls) == 1  # 没有再问模型


def test_cache_key_changes_when_labels_change(flow_env, monkeypatch):
    """参数变了不能复用旧缓存 —— 否则改了标签却拿到旧标签，静默出错。"""
    from app.flow.operators import ai as ai_ops

    env, sf, _ = flow_env
    calls: list[list[str]] = []

    def _fake(values, *, labels, instructions, criteria=""):
        calls.append(list(values))
        return {v: labels[0] for v in values}

    monkeypatch.setattr(ai_ops, "_call_classify", _fake)
    op = get_operator("llm.classify")
    base = {"input_column": "title", "instructions": f"k-{env['suffix']}"}

    with sf() as session:
        op.run_python(
            node=node("llm.classify", labels=["坚果", "果干"], **base),
            columns=["title"], rows=[["碧根果"]], session=session,
        )
        session.commit()
        op.run_python(
            node=node("llm.classify", labels=["坚果", "果干", "饮料"], **base),
            columns=["title"], rows=[["碧根果"]], session=session,
        )
        session.commit()
    assert len(calls) == 2  # 标签集合变了，必须重新问


def test_cache_rows_written(flow_env, monkeypatch):
    from app.flow.operators import ai as ai_ops

    env, sf, _ = flow_env
    monkeypatch.setattr(
        ai_ops,
        "_call_classify",
        lambda values, **kw: {v: "L" for v in values},
    )
    op = get_operator("llm.classify")
    with sf() as session:
        op.run_python(
            node=node("llm.classify", input_column="t", labels=["A", "B"],
                      instructions=f"row-{env['suffix']}"),
            columns=["t"], rows=[["x"]], session=session,
        )
        session.commit()
        rows = session.query(AiLlmCache).filter(
            AiLlmCache.cache_key.like(f"%row-{env['suffix']}%")
        ).all()
        assert len(rows) == 1
        assert rows[0].output == "L"
