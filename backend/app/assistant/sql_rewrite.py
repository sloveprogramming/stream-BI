"""把 SQL 中的表引用重写为带行级规则的子查询，保证联表查询也不越权。

使用 SQL 语法树改写，避免表名互为前缀时字符串替换出错。
"""
import re

import sqlglot
from sqlglot import exp

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

    每张被引用的表被替换为 (SELECT 白名单字段 FROM 表 WHERE 行级规则)，
    行级过滤先于 JOIN 生效；隐藏字段不会出现在子查询里。
    """
    expr = sqlglot.parse_one(sql)
    by_table = {t["source_table"]: (i, t) for i, t in enumerate(tables)}
    params: dict = {}

    for table in list(expr.find_all(exp.Table)):
        full = f"{table.db}.{table.name}" if table.db else table.name
        entry = by_table.get(full)
        if entry is None:
            continue
        index, spec = entry
        cols = ", ".join(f'"{c}"' for c in spec["columns"])
        where_sql, rule_params = _render_rules(
            spec["row_rules"] or [], prefix=f"r_{index}_"
        )
        params.update(rule_params)
        inner = sqlglot.parse_one(f"SELECT {cols} FROM {full}{where_sql}")
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
