"""把 SQL 中的表引用重写为带行级规则的子查询，保证联表查询也不越权。

## 两种引用名都要认（方案 C）

模型按 prompt 写的是**数据集名**（`demo_sales`），但子查询的 FROM 必须是
**真实物理表**（`demo.sales_order`）。所以这里先用 `table_refs.build_alias_map`
把引用名解析成物理表名，再据此注入行级规则。

**安全关键**：物理表名是从别名表解析出来的，而别名表在构建时就拒绝歧义 ——
所以"用 A 的名字读到 B 表的规则"这种越权不可能发生。解析不到的表原样保留
（闸门已经拦过一轮，这里不重复报错）。

用语法树改写而不是字符串替换：避免表名互为前缀时替换出错。
"""
import re

import sqlglot
from sqlglot import exp

from app.access.scoped_query import check_ident
from app.assistant.table_refs import AmbiguousTableError, build_alias_map, normalize

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

    每张被引用的表被替换为 (SELECT 白名单字段 FROM 物理表 WHERE 行级规则)，
    行级过滤先于 JOIN 生效；隐藏字段不会出现在子查询里。
    """
    expr = sqlglot.parse_one(sql)
    try:
        alias_map = build_alias_map(tables)
    except AmbiguousTableError:
        # 与闸门保持一致：歧义时不做任何改写，让它落到闸门那里报可读的错
        alias_map = {}

    by_source = {t["source_table"]: (i, t) for i, t in enumerate(tables)}
    params: dict = {}

    for table in list(expr.find_all(exp.Table)):
        key = normalize(f"{table.db}.{table.name}") if table.db else normalize(table.name)
        source = alias_map.get(key)
        if source is None:
            continue
        entry = by_source.get(source)
        if entry is None:
            continue
        index, spec = entry
        cols = ", ".join(f'"{c}"' for c in spec["columns"])
        where_sql, rule_params = _render_rules(
            spec["row_rules"] or [], prefix=f"r_{index}_"
        )
        params.update(rule_params)
        # ★ 子查询的 FROM 用**物理表名**，不是模型写的引用名
        inner = sqlglot.parse_one(f"SELECT {cols} FROM {source}{where_sql}")
        alias = table.alias or table.name
        table.replace(
            exp.Subquery(
                this=inner,
                alias=exp.TableAlias(this=exp.to_identifier(alias)),
            )
        )

    rendered = expr.sql(dialect="postgres")
    # sqlglot 输出 pyformat 占位符，转回 SQLAlchemy text() 使用的 :name 形式
    rendered = re.sub(r"%\((\w+)\)s", r":\1", rendered)
    return rendered, params


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
