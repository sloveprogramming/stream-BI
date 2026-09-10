"""把 SQL 中的表引用重写为带行级规则的子查询，保证联表查询也不越权。"""
from app.access.scoped_query import check_ident

_OP = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
    "like": "LIKE",
}


def rewrite_sql(sql: str, tables: list[dict]) -> tuple[str, dict]:
    """返回 (重写后的 SQL, 行级规则参数字典)。

    每张表被替换为 (SELECT 白名单字段 FROM 表 WHERE 行级规则)，
    因此行级过滤先于 JOIN 生效。
    """
    out = sql
    params: dict = {}
    for table_index, t in enumerate(tables):
        if t["source_table"] not in out:
            continue
        cols = ", ".join(f'"{c}"' for c in t["columns"])
        where_sql, rule_params = _render_rules(
            t["row_rules"] or [], prefix=f"r_{table_index}_"
        )
        params.update(rule_params)
        inner = f'(SELECT {cols} FROM {t["source_table"]}{where_sql})'
        out = out.replace(t["source_table"], inner)
    return out, params


def _render_rules(rules, prefix: str) -> tuple[str, dict]:
    if not rules:
        return "", {}
    parts = []
    params: dict = {}
    for i, rule in enumerate(rules):
        field = check_ident(rule["field"])
        op = _OP[rule["op"]]
        key = f"{prefix}{i}"
        params[key] = rule["value"]
        parts.append(f'"{field}" {op} :{key}')
    return " WHERE " + " AND ".join(parts), params
