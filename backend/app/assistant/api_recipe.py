"""把数据助手生成的 SQL 反解成「可直接调用的接口」调用方案。

只对能一一对应的查询给方案：单表、无 JOIN/子查询/UNION、无 GROUP BY/聚合/DISTINCT、
WHERE 全部是等值 AND 条件。这类查询等价于平台的
`GET /api/v1/datasets/{name}/rows?字段=值&limit=&offset=`（等价于 MCP 的 query_dataset）。

其余情况返回 callable=False + 原因，避免给出与本次结果不一致的调用方式。
"""
from typing import Any

import sqlglot
from sqlglot import exp

_MAX_REST_LIMIT = 500  # /datasets/{name}/rows 的 limit 上限
_MAX_MCP_LIMIT = 200  # MCP query_dataset 的 limit 上限


def _not_callable(reason: str) -> dict[str, Any]:
    return {"callable": False, "reason": reason}


def _unwrap(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _collect_eq(node, out: dict) -> bool:
    """收集顶层等值条件；出现非等值/非 AND 组合即判定不可对应。"""
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return _collect_eq(node.left, out) and _collect_eq(node.right, out)
    if isinstance(node, exp.EQ):
        left, right = _unwrap(node.left), _unwrap(node.right)
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            out[left.name] = right.this
            return True
        if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            out[right.name] = left.this
            return True
    return False


def _literal_int(node) -> int | None:
    node = _unwrap(node)
    if isinstance(node, exp.Literal):
        try:
            return int(node.this)
        except (TypeError, ValueError):
            return None
    return None


def _order_columns(expr) -> list[str]:
    order = expr.args.get("order")
    if order is None:
        return []
    cols = []
    for item in order.expressions:
        inner = item.this if isinstance(item, exp.Ordered) else item
        inner = _unwrap(inner)
        if isinstance(inner, exp.Column):
            cols.append(inner.name)
    return cols


def build_api_recipe(sql: str, tables: list[dict]) -> dict[str, Any] | None:
    """返回接口调用方案；无法对应时返回 {"callable": False, "reason": ...}。"""
    try:
        expr = sqlglot.parse_one(sql.rstrip().rstrip(";"))
    except Exception:  # noqa: BLE001 —— 解析失败就不给方案
        return None
    if not isinstance(expr, exp.Select):
        return _not_callable("不是单条 SELECT 查询")

    if expr.find(exp.Join) is not None:
        return _not_callable("涉及多表联查（JOIN），接口按单个数据集查询，无法一一对应")
    if expr.find(exp.Subquery) is not None:
        return _not_callable("包含子查询")
    if expr.find(exp.Union) is not None:
        return _not_callable("包含 UNION")
    if expr.args.get("group") is not None:
        return _not_callable("包含分组统计（GROUP BY），需要按维度聚合，接口按明细返回")
    if expr.args.get("having") is not None:
        return _not_callable("包含 HAVING")
    if expr.args.get("distinct") is not None:
        return _not_callable("包含 DISTINCT")
    agg = getattr(exp, "AggFunc", None)
    if agg is not None and expr.find(agg) is not None:
        return _not_callable("包含聚合函数（SUM/COUNT 等）")

    used = list(expr.find_all(exp.Table))
    if len(used) != 1:
        return _not_callable("引用了多张表")
    table = used[0]
    # 与闸门/重写器同一套解析：模型可能写数据集名，也可能写物理表名
    from app.assistant.table_refs import AmbiguousTableError, build_alias_map, normalize

    try:
        alias_map = build_alias_map(tables)
    except AmbiguousTableError as exc:
        return _not_callable(str(exc))
    key = normalize(f"{table.db}.{table.name}") if table.db else normalize(table.name)
    source = alias_map.get(key)
    spec = next((t for t in tables if t["source_table"] == source), None)
    if spec is None:
        return _not_callable("未能定位到已注册的数据集")
    full = spec["source_table"]

    filters: dict[str, str] = {}
    where = expr.find(exp.Where)
    if where is not None and not _collect_eq(where.this, filters):
        return _not_callable("筛选条件不是等值匹配（含范围/模糊/OR 等），接口只支持等值过滤")
    unknown = set(filters) - set(spec["columns"])
    if unknown:
        return _not_callable("筛选字段不在该数据集可见字段内")

    limit_node = expr.args.get("limit")
    offset_node = expr.args.get("offset")
    sql_limit = _literal_int(limit_node.expression) if limit_node is not None else None
    sql_offset = _literal_int(offset_node.expression) if offset_node is not None else 0

    caveats: list[str] = []
    order_cols = _order_columns(expr)
    pk_columns = list(spec.get("pk_columns") or [])
    if order_cols and sql_limit is not None and set(order_cols) - set(pk_columns):
        # 排序 + 截断 = 取 TopN，接口固定按主键排序，结果集会不同，宁可不给
        return _not_callable(
            "按非主键字段排序后取前若干行（TopN），接口固定按主键排序，结果集会与本次不同"
        )
    if sql_limit is None:
        rest_limit = _MAX_REST_LIMIT
        caveats.append(
            f"本次查询没有 LIMIT，接口单次最多返回 {_MAX_REST_LIMIT} 行，建议用 limit/offset 分页。"
        )
    elif sql_limit > _MAX_REST_LIMIT:
        rest_limit = _MAX_REST_LIMIT
        caveats.append(
            f"本次 LIMIT 为 {sql_limit}，超过接口上限，接口侧已收敛为 {_MAX_REST_LIMIT} 行，请分页取全。"
        )
    else:
        rest_limit = sql_limit
    if sql_offset:
        caveats.append(f"已保留 offset={sql_offset}。")
    if order_cols and set(order_cols) - set(pk_columns):
        caveats.append("接口固定按数据集主键排序，返回顺序可能与本次结果不同。")
    if filters:
        caveats.append("接口返回该数据集全部可见字段（本次可能只选了部分列）。")

    params: dict[str, str] = {k: str(v) for k, v in filters.items()}
    params["limit"] = str(rest_limit)
    if sql_offset:
        params["offset"] = str(sql_offset)

    return {
        "callable": True,
        "dataset": spec["name"],
        "dataset_title": spec.get("title") or spec["name"],
        "source_table": full,
        "endpoint": f"/api/v1/datasets/{spec['name']}/rows",
        "method": "GET",
        "params": params,
        "filters": filters,
        "limit": rest_limit,
        "offset": sql_offset,
        "mcp": {
            "tool": "query_dataset",
            "arguments": {
                "name": spec["name"],
                "filters": filters,
                "limit": min(rest_limit, _MAX_MCP_LIMIT),
            },
        },
        "caveats": caveats,
    }
