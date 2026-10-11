"""质量校验算子。

Part A 锁 **SQL 形状**（黄金测试）；Part B 在**真 PostgreSQL** 上跑行为 ——
三值逻辑（NULL 不算值域违规）是这类 SQL 最容易写错的地方，只看形状看不出来。
"""
import pytest
import sqlalchemy as sa

from app.flow.context import CompileCtx
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.errors import FlowCompileError, FlowValidationError
from app.flow.operators import get_operator

COLS = ["id", "order_id", "warehouse", "amount", "status"]


def build(params, cols=COLS):
    src = FlowNode(id="src", type="source.dataset", params={"dataset_id": 1})
    op = FlowNode(id="op", type="quality.assert", params=params)
    nodes = [src, op, FlowNode(id="out", type="sink.materialize", params={})]
    edges = [FlowEdge(source="src", target="op"), FlowEdge(source="op", target="out")]
    dsl = FlowDSL(name="t", nodes=nodes, edges=edges, output=FlowOutput(target_name="tbl"))
    ctx = CompileCtx(dsl=dsl, columns={"src": list(cols)})
    return ctx, dsl.node_map()["op"]


def sql_of(params, **kw):
    ctx, node = build(params, **kw)
    return get_operator("quality.assert").compile_sql(ctx, node)


# ---------------------------------------------------------------- SQL 形状


def test_not_null_generates_violation_case():
    sql = sql_of({"not_null": ["amount"]})
    assert 'CASE WHEN "amount" IS NULL THEN \'amount 不得为空\' END' in sql
    # passed 必须由 reason 推导（同一份条件，不可能互相矛盾）
    assert '"qa_reason" = \'\'' in sql


def test_unique_uses_window_function():
    sql = sql_of({"unique": [["order_id", "warehouse"]]})
    assert 'COUNT(*) OVER (PARTITION BY "order_id", "warehouse") > 1' in sql
    assert "组合重复" in sql


def test_rule_generates_is_false_check():
    sql = sql_of({"rules": [{"column": "amount", "op": "ge", "value": 0}]})
    assert '("amount" >= 0) IS FALSE' in sql
    assert "应 ≥ 0" in sql


def test_in_expands_list_literals():
    sql = sql_of({"rules": [{"column": "status", "op": "in", "value": ["已发货", "已签收"]}]})
    assert '"status" IN (\'已发货\', \'已签收\')' in sql


def test_quote_in_value_is_escaped_into_description():
    """描述会进 SQL 字面量 —— 值里的单引号必须被转义，否则就是注入口。"""
    sql = sql_of({"rules": [{"column": "status", "op": "eq", "value": "o'brien"}]})
    assert "o''brien" in sql
    assert sql.count("'o'brien") == 0


def test_output_columns_flow_downstream():
    ctx, node = build({"not_null": ["amount"]})
    op = get_operator("quality.assert")
    assert op.output_columns(ctx, node, ["a", "b"]) == ["a", "b", "qa_passed", "qa_reason"]


def test_custom_output_columns_are_respected():
    ctx, node = build({"not_null": ["amount"], "passed_column": "ok", "reason_column": "why"})
    op = get_operator("quality.assert")
    assert op.output_columns(ctx, node, ["a"]) == ["a", "ok", "why"]
    sql = get_operator("quality.assert").compile_sql(ctx, node)
    assert '"ok"' in sql and '"why"' in sql


# ---------------------------------------------------------------- 参数校验


def test_no_rules_rejected():
    with pytest.raises(FlowValidationError, match="至少配置一条规则"):
        sql_of({})


def test_in_with_scalar_rejected():
    with pytest.raises(FlowValidationError, match="非空列表"):
        sql_of({"rules": [{"column": "status", "op": "in", "value": "已发货"}]})


def test_scalar_op_with_list_rejected():
    with pytest.raises(FlowValidationError, match="标量"):
        sql_of({"rules": [{"column": "amount", "op": "ge", "value": [1]}]})


def test_same_output_names_rejected():
    with pytest.raises(FlowValidationError, match="不能同名"):
        sql_of(
            {
                "not_null": ["amount"],
                "passed_column": "flag",
                "reason_column": "flag",
            }
        )


def test_unknown_operator_value_rejected():
    with pytest.raises(FlowValidationError, match="参数不合法"):
        sql_of({"rules": [{"column": "amount", "op": "between", "value": 1}]})


# ---------------------------------------------------------------- 编译期检查


def test_missing_column_rejected_at_compile():
    with pytest.raises(FlowCompileError, match="不存在"):
        sql_of({"not_null": ["nope"]})


def test_output_clash_with_upstream_rejected():
    """上游已有 qa_passed → 必须报错；静默覆盖不报错，只会给错的结果。"""
    with pytest.raises(FlowCompileError, match="同名"):
        sql_of({"not_null": ["amount"]}, cols=COLS + ["qa_passed"])


# ---------------------------------------------------------------- 行为（真 PostgreSQL）


@pytest.fixture
def pg(pg_engine):
    """把算子 SQL 包在 VALUES CTE 外直接跑，返回 dict 行。"""

    def _run(params, rows):
        ctx, node = build(params)
        sql = get_operator("quality.assert").compile_sql(ctx, node)
        names = ", ".join(f'"{c}"' for c in COLS)
        values = ", ".join(
            "(" + ", ".join(_sql_value(v) for v in row) + ")" for row in rows
        )
        full = f"WITH op_src AS (SELECT * FROM (VALUES {values}) AS t({names})) {sql}"
        with pg_engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sa.text(full))]

    return _run


def _sql_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def test_null_does_not_trigger_range_violation(pg):
    """spec 显式场景：NULL 是「未知」不是「违规」，值域规则对它宽容。"""
    rows = pg(
        {"rules": [{"column": "amount", "op": "ge", "value": 0}]},
        [(1, "o1", "德清", None, "x"), (2, "o2", "德清", -5, "x")],
    )
    assert rows[0]["qa_passed"] is True
    assert rows[0]["qa_reason"] == ""
    assert rows[1]["qa_passed"] is False
    assert "应 ≥ 0" in rows[1]["qa_reason"]


def test_null_is_caught_by_not_null(pg):
    rows = pg({"not_null": ["amount"]}, [(1, "o1", "德清", None, "x")])
    assert rows[0]["qa_passed"] is False
    assert rows[0]["qa_reason"] == "amount 不得为空"


def test_unique_flags_every_row_in_the_group(pg):
    rows = pg(
        {"unique": [["order_id", "warehouse"]]},
        [(1, "o1", "德清", 10, "x"), (2, "o1", "德清", 20, "x"), (3, "o2", "安徽", 30, "x")],
    )
    assert [r["qa_passed"] for r in rows] == [False, False, True]
    assert "组合重复" in rows[0]["qa_reason"]
    assert "order_id、warehouse" in rows[0]["qa_reason"]


def test_multiple_violations_merge_into_one_reason(pg):
    rows = pg(
        {
            "not_null": ["amount"],
            "rules": [
                {"column": "amount", "op": "ge", "value": 0},
                {"column": "status", "op": "eq", "value": "已发货"},
            ],
        },
        [(1, "o1", "德清", -5, "已取消")],
    )
    reason = rows[0]["qa_reason"]
    assert "应 ≥ 0" in reason and "应等于" in reason
    assert rows[0]["qa_passed"] is False


def test_all_pass_gives_empty_reason(pg):
    rows = pg(
        {"not_null": ["amount"], "rules": [{"column": "amount", "op": "ge", "value": 0}]},
        [(1, "o1", "德清", 10, "x")],
    )
    assert rows[0]["qa_passed"] is True
    assert rows[0]["qa_reason"] == ""


def test_row_count_is_preserved(pg):
    """不删行：违规行也在输出里，只是带着原因（拦截交给下游过滤算子）。"""
    rows = pg(
        {"rules": [{"column": "amount", "op": "ge", "value": 0}]},
        [(1, "o1", "德清", 10, "x"), (2, "o2", "德清", -1, "x"), (3, "o3", "安徽", -2, "x")],
    )
    assert len(rows) == 3
    assert sorted(r["id"] for r in rows) == [1, 2, 3]
