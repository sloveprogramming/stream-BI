"""派生字段引用指标口径 —— 编排侧的口径一致性。

本文件的核心断言只有一句：**DSL 里存的是指标名，编译产物里是公式**。
前者让人改一处就全局生效，后者让产物自解释、可追溯。
"""
import pytest
import yaml

from app.flow.compile import validate_flow
from app.flow.context import CompileCtx
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.errors import FlowCompileError, FlowValidationError
from app.flow.operators import get_operator
from app.metrics import load_dictionary

FORMULA = "(amount - cost) / nullif(amount, 0)"


def _dictionary(workdir, entries=None, **kwargs):
    entries = entries or [
        {
            "name": "gross_margin",
            "label": "毛利率",
            "expr": FORMULA,
            "dataset": "sales_order",
            "owner": "财务共享中心",
            "synonyms": ["毛利"],
        }
    ]
    path = workdir / "metrics.yml"
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "metrics_dictionary": entries},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_dictionary(path, **kwargs)


def _dsl(params, node_type="field.derive"):
    return FlowDSL(
        name="orders",
        title="订单",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
            FlowNode(id="op", type=node_type, params=params),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[
            FlowEdge(source="src", target="op"),
            FlowEdge(source="op", target="out"),
        ],
        output=FlowOutput(target_name="tbl"),
    )


def _ctx(workdir, params=None, cols=("order_id", "amount", "cost"), **kwargs):
    dsl = _dsl(params or {"name": "margin", "metric": "gross_margin"})
    ctx = CompileCtx(
        dsl=dsl,
        columns={"src": list(cols)},
        metrics=_dictionary(workdir, **kwargs),
    )
    return ctx, dsl.node_map()["op"], dsl


def _sql(ctx, node):
    return get_operator("field.derive").compile_sql(ctx, node)


# ---------------------------------------------------------------- 展开


def test_metric_expands_to_the_defined_formula(workdir):
    ctx, node, _ = _ctx(workdir)
    assert _sql(ctx, node) == f'SELECT *, ({FORMULA}) AS "margin" FROM op_src'


def test_metric_reference_is_not_written_as_a_bare_column(workdir):
    """展开的是**公式**，不是把指标名当列名 —— 后者只有在数据集恰好有同名列时才碰巧能跑。"""
    ctx, node, _ = _ctx(workdir)
    assert 'AS "gross_margin"' not in _sql(ctx, node)


def test_dsl_keeps_the_metric_name(workdir):
    """DSL 里存的仍是指标名：改字典 → 下次编译生效，不用动流程。"""
    _, _, dsl = _ctx(workdir)
    params = dsl.node_map()["op"].params
    assert params["metric"] == "gross_margin"
    assert "expr" not in params or params["expr"] is None


def test_formula_change_takes_effect_on_the_next_compile(workdir):
    """口径变更不需要改流程（spec 的显式场景）。"""
    changed = FORMULA.replace("amount, 0", "amount, 999")
    ctx, node, _ = _ctx(
        workdir,
        entries=[
            {
                "name": "gross_margin",
                "label": "毛利率",
                "expr": changed,
                "dataset": "sales_order",
                "owner": "财务共享中心",
            }
        ],
    )
    assert changed in _sql(ctx, node)


def test_expr_still_works_when_no_metric_is_used(workdir):
    """裸表达式这条路不能因为加了指标而退化。"""
    ctx, node, _ = _ctx(workdir, params={"name": "gross", "expr": "amount - cost"})
    assert _sql(ctx, node) == 'SELECT *, (amount - cost) AS "gross" FROM op_src'


# ---------------------------------------------------------------- 参数互斥


@pytest.mark.parametrize(
    "params, keyword",
    [
        ({"name": "margin", "expr": "amount - cost", "metric": "gross_margin"}, "二选一"),
        ({"name": "margin"}, "二选一"),
        ({"name": "margin", "expr": "", "metric": ""}, "二选一"),
    ],
)
def test_exactly_one_source_is_required(workdir, params, keyword):
    """两者都填时无法判断以谁为准 —— 那种"看着是 A 实际是 B"的配置正是口径事故的来源。"""
    ctx, node, _ = _ctx(workdir, params=params)
    with pytest.raises(FlowValidationError, match=keyword):
        _sql(ctx, node)


def test_mutual_exclusion_reported_by_validate_flow(workdir):
    """参数级问题在保存前校验就暴露，不等编译。"""
    with pytest.raises(FlowValidationError, match="二选一"):
        validate_flow(_dsl({"name": "margin", "expr": "amount", "metric": "gross_margin"}))


# ---------------------------------------------------------------- 错误路径


def test_unknown_metric_lists_available_names(workdir):
    ctx, node, _ = _ctx(workdir, params={"name": "margin", "metric": "gross_margn"})
    with pytest.raises(FlowValidationError) as excinfo:
        _sql(ctx, node)
    message = str(excinfo.value)
    assert "未知指标" in message
    assert "gross_margin" in message  # 可用清单必须带出来


def test_unknown_metric_rejected_at_validate_stage():
    """保存/校验阶段就要拒绝，不能拖到执行。"""
    with pytest.raises(FlowValidationError, match="未知指标"):
        validate_flow(_dsl({"name": "margin", "metric": "not_a_metric"}))


def test_metric_on_unregistered_dataset_explains_environment(workdir):
    """数据集没上架是环境问题，错误信息要指向"去注册数据集"，不是"去改指标名"。"""
    ctx, node, _ = _ctx(workdir, datasets={})
    with pytest.raises(FlowValidationError, match="尚未上架"):
        _sql(ctx, node)


def test_missing_upstream_column_fails_at_compile(workdir):
    """流程把指标要用的列丢了 —— 必须在编译期报，并说清缺哪列。"""
    ctx, node, _ = _ctx(workdir, cols=("order_id", "amount"))  # 少了 cost
    with pytest.raises(FlowCompileError) as excinfo:
        _sql(ctx, node)

    message = str(excinfo.value)
    assert "gross_margin" in message
    assert "cost" in message
    assert "order_id, amount" in message  # 现有列也报出来，省得再去翻


def test_missing_column_check_is_skipped_when_columns_unknown(workdir):
    """列信息推导不出来时不拦截 —— 没有依据的拦截比漏拦更糟。"""
    ctx, node, _ = _ctx(workdir)
    ctx.columns.pop("src")
    assert FORMULA in _sql(ctx, node)


def test_metric_columns_become_part_of_output_columns(workdir):
    """派生列要沿 DAG 往下传，否则下游 rename/select 会因为"列信息丢了"报错。"""
    ctx, node, _ = _ctx(workdir)
    op = get_operator("field.derive")
    assert op.output_columns(ctx, node, ["order_id", "amount", "cost"]) == [
        "order_id",
        "amount",
        "cost",
        "margin",
    ]
