"""SQL 安全闸门：只读、单语句、表/字段权限校验。"""
import sqlglot
from sqlglot import exp


class SqlGuardError(Exception):
    """SQL 未通过安全校验。"""


_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
)
_WRITE_KEYWORDS = (
    "truncate",
    "grant",
    "revoke",
    "copy",
    "into outfile",
)


def validate_sql(sql: str, tables: list[dict]) -> None:
    """校验 SQL 只读且只引用有权限的表与字段；不通过抛 SqlGuardError。"""
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if ";" in sql:
        raise SqlGuardError("只允许单条查询")
    try:
        exprs = sqlglot.parse(sql)
    except Exception as exc:  # noqa: BLE001
        raise SqlGuardError(f"SQL 解析失败: {exc}")
    if len(exprs) != 1 or not isinstance(exprs[0], (exp.Select, exp.Union)):
        raise SqlGuardError("只允许 SELECT 查询")
    expr = exprs[0]
    for node in expr.walk():
        if isinstance(node, _WRITE_NODES):
            raise SqlGuardError("检测到写操作，已拒绝")
    lowered = f" {expr.sql().lower()} "
    for word in _WRITE_KEYWORDS:
        if f" {word} " in lowered:
            raise SqlGuardError(f"检测到危险语句: {word}")

    allowed = {t["source_table"]: set(t["columns"]) for t in tables}
    aliases: set[str] = set()
    for select in expr.find_all(exp.Select):
        for proj in select.expressions:
            if proj.alias:
                aliases.add(proj.alias)
    alias_map: dict[str, str] = {}
    for table in expr.find_all(exp.Table):
        full = f"{table.db}.{table.name}" if table.db else table.name
        if full not in allowed:
            raise SqlGuardError(f"无权限的表: {full}")
        alias_map[table.alias_or_name] = full
        alias_map[full] = full

    for col in expr.find_all(exp.Column):
        if col.name in aliases:
            continue
        if col.table:
            full = alias_map.get(col.table)
            if full is not None and col.name not in allowed[full]:
                raise SqlGuardError(f"无权限的字段: {col.table}.{col.name}")
        elif not any(col.name in cols for cols in allowed.values()):
            raise SqlGuardError(f"无权限的字段: {col.name}")
