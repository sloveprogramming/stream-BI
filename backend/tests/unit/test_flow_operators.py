"""算子库的 SQL 生成。

这些是**黄金测试**：断言算子生成的 SQL 原文。
SQL 是这套编排对外最关键的产物（要交给 dbt 跑、要给人看），
改了措辞就该有人注意到。
"""
import pytest

from app.flow.context import CompileCtx
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.errors import FlowCompileError, FlowValidationError
from app.flow.operators import get_operator, operator_catalog, REGISTRY

COLS = ["id", "amount", "status"]


def build(op_type: str, params: dict, cols=COLS):
    """构造最小图：source → op → sink（op 本身是 sink 时就不再加末端 sink）。"""
    src = FlowNode(id="src", type="source.dataset", params={"dataset_id": 1})
    op = FlowNode(id="op", type=op_type, params=params)
    nodes = [src, op]
    edges = [FlowEdge(source="src", target="op")]
    if not op_type.startswith("sink."):
        sink = FlowNode(id="out", type="sink.materialize", params={})
        nodes.append(sink)
        edges.append(FlowEdge(source="op", target="out"))
    dsl = FlowDSL(name="t", nodes=nodes, edges=edges, output=FlowOutput(target_name="tbl"))
    ctx = CompileCtx(dsl=dsl, columns={"src": list(cols)})
    return ctx, dsl.node_map()["op"]


def build_join(params: dict, op_type: str = "join.merge",
               left_cols=("id", "amount"), right_cols=("id", "name")):
    """构造 a,b → op → sink 的双输入图。"""
    nodes = [
        FlowNode(id="a", type="source.dataset", params={"dataset_id": 1}),
        FlowNode(id="b", type="source.dataset", params={"dataset_id": 2}),
        FlowNode(id="op", type=op_type, params=params),
        FlowNode(id="out", type="sink.materialize", params={}),
    ]
    edges = [
        FlowEdge(source="a", target="op"),
        FlowEdge(source="b", target="op"),
        FlowEdge(source="op", target="out"),
    ]
    dsl = FlowDSL(name="t", nodes=nodes, edges=edges, output=FlowOutput(target_name="tbl"))
    ctx = CompileCtx(dsl=dsl, columns={"a": list(left_cols), "b": list(right_cols)})
    return ctx, dsl.node_map()["op"]


def sql_of(op_type, params, **kw):
    ctx, node = build(op_type, params, **kw)
    return get_operator(op_type).compile_sql(ctx, node)


# ---------- 注册表 ----------


def test_catalog_exposes_json_schema_for_frontend():
    catalog = {o["type"]: o for o in operator_catalog()}
    assert "clean.dedup" in catalog
    schema = catalog["clean.dedup"]["paramsSchema"]
    assert "keys" in schema["properties"]
    assert catalog["clean.dedup"]["isSink"] is False


def test_every_operator_declares_metadata():
    for type_, op in REGISTRY.items():
        assert op.title, f"{type_} 缺 title"
        assert op.description, f"{type_} 缺 description"
        assert op.category, f"{type_} 缺 category"


def test_unknown_operator_is_rejected():
    with pytest.raises(FlowValidationError, match="未知算子"):
        get_operator("clean.nope")


# ---------- 清洗 ----------


def test_filter():
    assert sql_of("clean.filter", {"expr": "amount > 0"}) == (
        "SELECT * FROM op_src WHERE amount > 0"
    )


def test_filter_rejects_stacked_statements():
    # 用户/LLM 写的表达式不能带第二条语句
    with pytest.raises(FlowValidationError, match="分号"):
        sql_of("clean.filter", {"expr": "1=1; DROP TABLE ods.sales_order"})


def test_dedup_keep_last_with_order():
    assert sql_of("clean.dedup", {"keys": ["id"], "keep": "last", "order_by": "amount"}) == (
        'SELECT DISTINCT ON ("id") * FROM op_src ORDER BY "id", "amount" DESC'
    )


def test_dedup_keep_first():
    assert sql_of("clean.dedup", {"keys": ["id"], "keep": "first", "order_by": "amount"}) == (
        'SELECT DISTINCT ON ("id") * FROM op_src ORDER BY "id", "amount" ASC'
    )


def test_dedup_without_order_keeps_arbitrary_row():
    assert sql_of("clean.dedup", {"keys": ["id"]}) == (
        'SELECT DISTINCT ON ("id") * FROM op_src ORDER BY "id"'
    )


def test_dedup_none_means_full_row_distinct():
    assert sql_of("clean.dedup", {"keys": ["id"], "keep": "none"}) == (
        "SELECT DISTINCT * FROM op_src"
    )


def test_dedup_rejects_order_by_inside_keys():
    with pytest.raises(FlowCompileError, match="无法判定先后"):
        sql_of("clean.dedup", {"keys": ["id", "amount"], "keep": "last", "order_by": "amount"})


def test_fill_null_escapes_literal():
    assert sql_of("clean.fill_null", {"fills": {"status": "未知"}}) == (
        'SELECT "id", "amount", COALESCE("status", \'未知\') AS "status" FROM op_src'
    )


def test_fill_null_rejects_unknown_column():
    with pytest.raises(FlowCompileError, match="列不存在"):
        sql_of("clean.fill_null", {"fills": {"nope": 0}})


def test_trim():
    assert sql_of("clean.trim", {"columns": ["status"]}) == (
        'SELECT "id", "amount", BTRIM("status") AS "status" FROM op_src'
    )


def test_trim_rejects_unknown_mode():
    with pytest.raises(FlowCompileError, match="mode 只能是"):
        sql_of("clean.trim", {"columns": ["status"], "mode": "shout"})


# ---------- 字段 ----------


def test_select():
    assert sql_of("field.select", {"columns": ["id", "amount"]}) == (
        'SELECT "id", "amount" FROM op_src'
    )


def test_rename():
    assert sql_of("field.rename", {"mapping": {"amount": "order_amount"}}) == (
        'SELECT "id", "amount" AS "order_amount", "status" FROM op_src'
    )


def test_rename_rejects_non_ascii_and_points_to_metadata():
    """中文展示名属于字段注释，不属于物理列名 —— 报错要能指路，不能只说"非法"。"""
    with pytest.raises(FlowCompileError, match="字段注释"):
        sql_of("field.rename", {"mapping": {"amount": "订单金额"}})


def test_derive_rejects_non_ascii_alias():
    with pytest.raises(FlowCompileError, match="字段注释"):
        sql_of("field.derive", {"name": "净额", "expr": "amount - 1"})


def test_cast():
    assert sql_of("field.cast", {"casts": {"amount": "numeric"}}) == (
        'SELECT "id", CAST("amount" AS numeric) AS "amount", "status" FROM op_src'
    )


def test_cast_rejects_unsupported_type():
    with pytest.raises(FlowCompileError, match="不支持的转换类型"):
        sql_of("field.cast", {"casts": {"amount": "money"}})


def test_derive():
    assert sql_of("field.derive", {"name": "net", "expr": "amount - 1"}) == (
        'SELECT *, (amount - 1) AS "net" FROM op_src'
    )


def test_derive_rejects_bad_expression():
    with pytest.raises(FlowValidationError):
        sql_of("field.derive", {"name": "net", "expr": "amount +"})


# ---------- 列信息沿 DAG 传播 ----------


def test_rename_after_filter_still_knows_columns():
    """列信息要能穿过不改变列的算子（否则 filter → rename 会莫名报错）。"""
    ctx, node = build("clean.filter", {"expr": "amount > 0"})
    filt = get_operator("clean.filter")
    ctx.columns["op"] = filt.output_columns(ctx, node, ctx.columns["src"])
    assert ctx.columns["op"] == COLS

    rename_node = FlowNode(id="rn", type="field.rename", params={"mapping": {"amount": "营业额"}})
    ctx.dsl.nodes.append(rename_node)
    ctx.dsl.edges.append(FlowEdge(source="op", target="rn"))
    assert get_operator("field.rename").output_columns(ctx, rename_node, COLS) == [
        "id",
        "营业额",
        "status",
    ]


def test_operator_needing_columns_gives_actionable_error():
    # 模拟列信息丢失（例如上游是自定义 SQL）：错误信息要能指导用户怎么改
    with pytest.raises(FlowCompileError, match="列信息不可用"):
        sql_of("clean.fill_null", {"fills": {"amount": 0}}, cols=[])


# ---------- 聚合 ----------


def test_group():
    assert sql_of("agg.group", {"group_by": ["status"], "aggs": {"amount": "sum"}}) == (
        'SELECT "status", SUM("amount") AS "amount_sum" FROM op_src GROUP BY "status"'
    )


def test_group_count_distinct():
    assert sql_of("agg.group", {"aggs": {"id": "count_distinct"}}) == (
        'SELECT COUNT(DISTINCT "id") AS "id_count_distinct" FROM op_src'
    )


def test_group_rejects_bad_function():
    with pytest.raises(FlowCompileError, match="不支持的聚合函数"):
        sql_of("agg.group", {"aggs": {"amount": "median"}})


def test_window():
    assert sql_of(
        "agg.window",
        {"func": "sum", "column": "amount", "partition_by": ["status"], "order_by": ["id"], "name": "cum"},
    ) == (
        'SELECT *, SUM("amount") OVER (PARTITION BY "status" ORDER BY "id") AS "cum" FROM op_src'
    )


def test_window_row_number_needs_no_column():
    assert sql_of(
        "agg.window",
        {"func": "row_number", "order_by": ["amount"], "name": "rn", "descending": True},
    ) == 'SELECT *, ROW_NUMBER() OVER (ORDER BY "amount" DESC) AS "rn" FROM op_src'


def test_pivot():
    assert sql_of(
        "agg.pivot",
        {"index": ["id"], "pivot_column": "status", "values": ["A", "B"], "value_column": "amount"},
    ) == (
        'SELECT "id", '
        'SUM("amount") FILTER (WHERE "status" = \'A\') AS "status_A", '
        'SUM("amount") FILTER (WHERE "status" = \'B\') AS "status_B" '
        'FROM op_src GROUP BY "id"'
    )


def test_pivot_aliases_are_deduplicated():
    """`a-b` 与 `a_b` 规范化后会撞，必须各自能落成唯一列名。"""
    ctx, node = build("agg.pivot", {
        "index": ["id"], "pivot_column": "c", "values": ["a-b", "a_b"], "value_column": "amount",
    })
    cols = get_operator("agg.pivot").output_columns(ctx, node, COLS)
    assert len(set(cols)) == len(cols)


# ---------- 关联 ----------


def test_merge():
    ctx, node = build_join({
        "left_id": "a", "right_id": "b", "how": "left",
        "on": [["id", "id"]], "right_columns": ["name"], "suffix": "_r",
    })
    assert get_operator("join.merge").compile_sql(ctx, node) == (
        'SELECT l.*, r."name" AS "name_r" FROM op_a AS l LEFT JOIN op_b AS r ON l."id" = r."id"'
    )


def test_merge_rejects_wrong_left_right():
    with pytest.raises(FlowCompileError, match="必须正好是本节点的两个上游"):
        ctx, node = build_join({
            "left_id": "a", "right_id": "ghost", "on": [["id", "id"]],
        })
        get_operator("join.merge").compile_sql(ctx, node)


def test_union():
    ctx, node = build_join({"inputs": ["a", "b"]}, op_type="join.union")
    assert get_operator("join.union").compile_sql(ctx, node) == (
        "SELECT * FROM op_a UNION SELECT * FROM op_b"
    )


def test_union_all():
    ctx, node = build_join({"inputs": ["a", "b"], "distinct": False}, op_type="join.union")
    assert get_operator("join.union").compile_sql(ctx, node) == (
        "SELECT * FROM op_a UNION ALL SELECT * FROM op_b"
    )


# ---------- 输出 ----------


def test_sink_is_identity():
    assert sql_of("sink.materialize", {}) == "SELECT * FROM op_src"
