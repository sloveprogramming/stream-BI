"""SQL 安全闸门：只读、单语句、表/字段权限校验。

## 表名解析（方案 C：双向映射）

模型按 prompt 写的是**数据集名**（如 `demo_sales`），而权限与重写用的是
**物理表名**（如 `demo.sales_order`）。本闸门通过 `table_refs.build_alias_map`
把两种写法都解析到同一张物理表，再校验权限。

**安全要点**：别名表构建时就会拒绝歧义（两个引用名指向不同物理表），
所以这里解析出的物理表是**唯一确定**的 —— 不存在"猜错表"的可能。
解析不到一律拒绝，不给"宽松放行"的后门。
"""
import sqlglot
from sqlglot import exp

from app.assistant.table_refs import AmbiguousTableError, build_alias_map, normalize


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


def _table_key(table: exp.Table) -> str:
    """SQL 里一个表引用归一化后的名字（`db.name` 或 `name`，小写）。"""
    if table.db:
        return normalize(f"{table.db}.{table.name}")
    return normalize(table.name)


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

    # 引用名 → 物理表名。构建失败（歧义）直接拒绝：宁可报错，不要猜。
    try:
        alias_map = build_alias_map(tables)
    except AmbiguousTableError as exc:
        raise SqlGuardError(str(exc))

    # 物理表名 → 可见字段集合
    by_source = {t["source_table"]: set(t["columns"]) for t in tables}

    aliases: set[str] = set()
    for select in expr.find_all(exp.Select):
        for proj in select.expressions:
            if proj.alias:
                aliases.add(proj.alias)

    # 表别名/引用名 → 物理表名，供字段归属判断用
    alias_map2: dict[str, str] = {}
    for table in expr.find_all(exp.Table):
        key = _table_key(table)
        source = alias_map.get(key)
        if source is None:
            raise SqlGuardError(f"无权限的表: {key}")
        if source not in by_source:
            # 理论不可达（alias_map 由同一份 tables 构建），防御性保留
            raise SqlGuardError(f"无权限的表: {key}")
        alias_map2[normalize(table.alias_or_name)] = source
        alias_map2[key] = source

    for col in expr.find_all(exp.Column):
        if col.name in aliases:
            continue
        if col.table:
            source = alias_map2.get(normalize(col.table))
            if source is None:
                # 引用了没在 FROM 里出现的限定名，交给执行器报错更准确
                continue
            if normalize(col.name) not in {normalize(c) for c in by_source[source]}:
                raise SqlGuardError(f"无权限的字段: {col.table}.{col.name}")
        elif not any(
            normalize(col.name) in {normalize(c) for c in cols}
            for cols in by_source.values()
        ):
            raise SqlGuardError(f"无权限的字段: {col.name}")
