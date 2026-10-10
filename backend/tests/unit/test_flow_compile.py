"""编译链路 + **权限回归**。

本文件最重要的两条：
- `test_source_table_never_appears_without_row_rules` —— 源表在 SQL 里永远跟在 WHERE 后面；
- `test_no_grant_aborts_compile` —— 拿不到授权必须中止，绝不降级成「不过滤继续跑」。

这两条一旦回归，整个"编排不绕过权限"的设计就废了，所以写成硬断言。
"""
from pydantic import BaseModel
import pytest

from app.flow.compile import compile_flow, validate_flow
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.errors import FlowCompileError, FlowPermissionError, FlowValidationError


class _DS:
    """假数据集（只覆盖编译路径上用到的字段）。"""

    def __init__(self, id_, name, columns, source_table="ods.sales_order", enabled=True):
        self.id = id_
        self.name = name
        self.columns = columns
        self.pk_columns = ["order_id"]
        self.source_table = source_table
        self.enabled = enabled


class _Grant:
    def __init__(self, row_rules=None, field_whitelist=None):
        self.row_rules = row_rules or []
        self.field_whitelist = field_whitelist


class _Session:
    def __init__(self, datasets):
        self._d = {d.id: d for d in datasets}

    def get(self, model, pk):
        return self._d.get(pk)


DS = _DS(1, "sales_order", ["order_id", "warehouse", "amount", "cost"])
DEDUP_RULE = {"field": "warehouse", "op": "eq", "value": "德清"}


def _dsl(nodes=None, edges=None, target="sales_order_clean", schema="dwd"):
    nodes = nodes or [
        FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
        FlowNode(
            id="dedup",
            type="clean.dedup",
            params={"keys": ["order_id"], "keep": "last", "order_by": "amount"},
        ),
        FlowNode(id="filt", type="clean.filter", params={"expr": "amount > 0"}),
        FlowNode(id="out", type="sink.materialize", params={}),
    ]
    edges = edges or [
        FlowEdge(source="src", target="dedup"),
        FlowEdge(source="dedup", target="filt"),
        FlowEdge(source="filt", target="out"),
    ]
    return FlowDSL(
        name="clean_orders",
        title="销售订单清洗",
        nodes=nodes,
        edges=edges,
        output=FlowOutput(target_name=target, target_schema=schema),
    )


def _patch(monkeypatch, grant):
    monkeypatch.setattr("app.flow.compile.resolve_grant", lambda s, u, i: grant)


# ---------- 权限回归（最重要） ----------


def test_source_table_never_appears_without_row_rules(monkeypatch):
    """源表在 SQL 里必须永远跟在 WHERE 后面 —— 这是"不绕过权限"的机器可验形式。"""
    _patch(monkeypatch, _Grant(row_rules=[DEDUP_RULE]))
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())

    idx = compiled.model_sql.index("FROM ods.sales_order")
    assert compiled.model_sql[idx:].startswith('FROM ods.sales_order WHERE "warehouse" = \'德清\'')


def test_row_rules_are_inlined_into_every_source(monkeypatch):
    _patch(monkeypatch, _Grant(row_rules=[DEDUP_RULE, {"field": "amount", "op": "gt", "value": 0}]))
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert '"warehouse" = \'德清\'' in compiled.model_sql
    # 数值型值走数字字面量，不加引号
    assert '"amount" > 0' in compiled.model_sql
    assert ":r_0" not in compiled.model_sql  # 绑定参数必须已被内联


def test_no_grant_aborts_compile(monkeypatch):
    _patch(monkeypatch, None)
    with pytest.raises(FlowPermissionError, match="没有授权"):
        compile_flow(_dsl(), session=_Session([DS]), owner=object())


def test_field_whitelist_is_applied(monkeypatch):
    """列级权限同样不能被编排绕过。"""
    _patch(monkeypatch, _Grant(field_whitelist=["order_id", "amount"]))
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert '"cost"' not in compiled.model_sql
    assert '"warehouse"' not in compiled.model_sql


def test_grant_snapshot_recorded_for_audit(monkeypatch):
    """同一条流程不同权限跑出不同结果，必须记下当时用的是谁的行权。"""
    _patch(monkeypatch, _Grant(row_rules=[DEDUP_RULE]))
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert compiled.grant_snapshot == {"sales_order": [DEDUP_RULE]}


def test_input_datasets_recorded_for_lineage(monkeypatch):
    _patch(monkeypatch, _Grant())
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert compiled.input_datasets == [{"id": 1, "name": "sales_order"}]


# ---------- 编译产物结构 ----------


def test_model_sql_shape(monkeypatch):
    _patch(monkeypatch, _Grant(row_rules=[DEDUP_RULE]))
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    sql = compiled.model_sql

    assert sql.startswith("{{ config(materialized='table', schema='dwd', alias='sales_order_clean') }}")
    assert "请勿手改" in sql
    assert "WITH op_src AS (" in sql
    assert "op_dedup AS (" in sql
    assert "op_filt AS (" in sql
    assert "op_out AS (" in sql
    assert sql.rstrip().endswith("SELECT * FROM op_out")
    assert 'DISTINCT ON ("order_id")' in sql
    assert 'WHERE amount > 0' in sql


def test_target_and_model_name(monkeypatch):
    _patch(monkeypatch, _Grant())
    compiled = compile_flow(_dsl(schema="ads", target="daily_sales"), session=_Session([DS]), owner=object())
    assert compiled.target == "ads.daily_sales"
    assert compiled.model_name == "daily_sales"


def test_schema_yml_lists_output_columns(monkeypatch):
    _patch(monkeypatch, _Grant())
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert "version: 2" in compiled.schema_yml
    assert "- name: sales_order_clean" in compiled.schema_yml
    assert "      - name: order_id" in compiled.schema_yml


def test_used_operators_reported(monkeypatch):
    _patch(monkeypatch, _Grant())
    compiled = compile_flow(_dsl(), session=_Session([DS]), owner=object())
    assert compiled.used_operators == ["clean.dedup", "clean.filter", "sink.materialize", "source.dataset"]


# ---------- 错误路径 ----------


def test_missing_dataset_id(monkeypatch):
    _patch(monkeypatch, _Grant())
    dsl = _dsl(
        nodes=[
            FlowNode(id="src", type="source.dataset", params={}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="out")],
    )
    with pytest.raises(FlowCompileError, match="缺少 dataset_id"):
        compile_flow(dsl, session=_Session([DS]), owner=object())


def test_unknown_dataset(monkeypatch):
    _patch(monkeypatch, _Grant())
    with pytest.raises(FlowCompileError, match="数据集不存在或已停用"):
        compile_flow(_dsl(), session=_Session([]), owner=object())


def test_disabled_dataset(monkeypatch):
    _patch(monkeypatch, _Grant())
    disabled = _DS(1, "sales_order", ["order_id"], enabled=False)
    with pytest.raises(FlowCompileError, match="数据集不存在或已停用"):
        compile_flow(_dsl(), session=_Session([disabled]), owner=object())


def test_validate_flow_rejects_unknown_operator():
    dsl = _dsl(
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
            FlowNode(id="x", type="clean.nope", params={}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="x"), FlowEdge(source="x", target="out")],
    )
    with pytest.raises(FlowValidationError, match="未知算子"):
        validate_flow(dsl)


def test_validate_flow_rejects_bad_params():
    dsl = _dsl(
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
            FlowNode(id="d", type="clean.dedup", params={"keys": []}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="d"), FlowEdge(source="d", target="out")],
    )
    with pytest.raises(FlowValidationError, match="参数不合法"):
        validate_flow(dsl)


def test_python_operator_needs_materialized_table(monkeypatch):
    """AI 算子的接缝：不产 SQL，必须先由 Python 执行并物化结果。"""
    from app.flow.operators.base import Operator, REGISTRY

    class _Fake(Operator):
        type = "test.python_op"
        category = "测试"
        title = "假 Python 算子"
        description = "仅用于验证 python 引擎的接缝"
        engine = "python"

        class Params(BaseModel):
            pass

    REGISTRY["test.python_op"] = _Fake()
    try:
        _patch(monkeypatch, _Grant())
        dsl = _dsl(
            nodes=[
                FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
                FlowNode(id="ai", type="test.python_op", params={}),
                FlowNode(id="out", type="sink.materialize", params={}),
            ],
            edges=[FlowEdge(source="src", target="ai"), FlowEdge(source="ai", target="out")],
        )
        with pytest.raises(FlowCompileError, match="需要先由 Python 执行并物化"):
            compile_flow(dsl, session=_Session([DS]), owner=object())
    finally:
        REGISTRY.pop("test.python_op", None)


def test_python_operator_uses_materialized_table(monkeypatch):
    from app.flow.operators.base import Operator, REGISTRY

    class _Fake2(Operator):
        type = "test.python_op2"
        category = "测试"
        title = "假 Python 算子 2"
        description = "已验证物化后应被引用"
        engine = "python"

        class Params(BaseModel):
            pass

    REGISTRY["test.python_op2"] = _Fake2()
    try:
        _patch(monkeypatch, _Grant())
        dsl = _dsl(
            nodes=[
                FlowNode(id="src", type="source.dataset", params={"dataset_id": 1}),
                FlowNode(
                    id="ai",
                    type="test.python_op2",
                    params={"materialized_table": "dwd._ai_labels"},
                ),
                FlowNode(id="out", type="sink.materialize", params={}),
            ],
            edges=[FlowEdge(source="src", target="ai"), FlowEdge(source="ai", target="out")],
        )
        compiled = compile_flow(dsl, session=_Session([DS]), owner=object())
        assert 'SELECT * FROM "dwd"."_ai_labels"' in compiled.model_sql
    finally:
        REGISTRY.pop("test.python_op2", None)
