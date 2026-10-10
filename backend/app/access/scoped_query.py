import re

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
    "like": "LIKE",
}


def check_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return name


def _quote(name: str) -> str:
    return '"' + check_ident(name) + '"'


def _build_where(dataset, grant, filters: dict) -> tuple[list[str], dict]:
    params: dict = {}
    where: list[str] = []
    for i, rule in enumerate(grant.row_rules or []):
        field = check_ident(rule["field"])
        op = _OPS[rule["op"]]
        params[f"r_{i}"] = rule["value"]
        where.append(f"{_quote(field)} {op} :r_{i}")
    for field, value in filters.items():
        check_ident(field)
        params[f"f_{field}"] = value
        where.append(f"{_quote(field)} = :f_{field}")
    return where, params


def build_count_sql(dataset, grant, filters: dict) -> tuple[str, dict]:
    """与 build_scoped_sql 同口径的 COUNT 语句（用于 total）。"""
    where, params = _build_where(dataset, grant, filters)
    sql = f"SELECT COUNT(*) FROM {dataset.source_table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, params


def build_scoped_sql(
    dataset, grant, filters: dict, limit: int | None = None, offset: int = 0
) -> tuple[str, dict]:
    """返回经权限裁剪的 SELECT 语句。字段=白名单；行=规则 AND 用户过滤；值全部参数化。

    `limit=None` 表示不限行数（供物化类场景使用，如数据编排落表）；
    传整数时行为与既有调用完全一致，末尾追加 `LIMIT :limit OFFSET :offset`。
    """
    columns = grant.field_whitelist or list(dataset.columns)
    for c in columns:
        check_ident(c)
    unknown = set(filters) - set(dataset.columns)
    if unknown:
        raise ValueError(f"未知过滤字段: {sorted(unknown)}")
    for c in dataset.pk_columns:
        check_ident(c)

    where, params = _build_where(dataset, grant, filters)
    col_sql = ", ".join(_quote(c) for c in columns)
    sql = f"SELECT {col_sql} FROM {dataset.source_table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = ", ".join(_quote(c) for c in dataset.pk_columns)
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset
    return sql, params
