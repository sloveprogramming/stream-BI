"""按用户的有效授权收集数据集结构，生成给大模型的 schema 上下文。

准确率的第一杠杆是**上下文质量**，不是 prompt 措辞。这里做了三件事：
1. 注入 ERP 侧的中文列注释（`Dataset.column_comments`）—— 让模型知道 `amount` 是「金额」而不是猜；
2. 样例给到 3 行、并以 `列=值` 形式展开（比裸数组更难错位）；
3. 明确「写 SQL 用表名，不要加 ods. 前缀」，省掉一类低级错误。

取样例行时**同样走权限裁剪器**（字段白名单 + 行级规则），否则会把越权行的真实值带进 prompt。
"""
from app.access.grants import resolve_grant
from app.access.scoped_query import build_scoped_sql
from app.models import Dataset

_SAMPLE_LIMIT = 3
_VALUE_MAX_LEN = 50
# 字段太多时退化为纯逗号列表，避免 prompt 被单表撑爆
_MAX_COMMENTED_COLUMNS = 40


def build_schema_context(session_factory, pg_engine, user) -> dict:
    """只收集该用户有权限的数据集；返回 tables 列表与渲染好的 prompt。"""
    with session_factory() as session:
        datasets = (
            session.query(Dataset)
            .filter_by(enabled=True)
            .order_by(Dataset.id)
            .all()
        )
        tables = []
        for ds in datasets:
            grant = resolve_grant(session, user, ds.id)
            if grant is None:
                continue
            columns = grant.field_whitelist or list(ds.columns)
            comments = ds.column_comments or {}
            tables.append(
                {
                    "name": ds.name,
                    "title": ds.title,
                    "description": ds.description,
                    "source_table": ds.source_table,
                    "columns": columns,
                    "column_comments": {
                        c: comments[c] for c in columns if comments.get(c)
                    },
                    "pk_columns": list(ds.pk_columns or []),
                    "samples": _sample_rows(pg_engine, ds, grant),
                    "row_rules": grant.row_rules or [],
                }
            )
    return {"tables": tables, "prompt": _render_prompt(tables)}


def _sample_rows(pg_engine, dataset, grant, limit: int = _SAMPLE_LIMIT):
    """样例数据走权限裁剪器（字段白名单 + 行级规则）。

    不能直接裸查源表：否则带行级规则的账号会把越权行的真实值带进 prompt，
    被模型复述出来就是数据泄露。
    """
    from sqlalchemy import text

    columns = grant.field_whitelist or list(dataset.columns)
    if not columns:
        return []
    try:
        sql, params = build_scoped_sql(dataset, grant, {}, limit, 0)
        with pg_engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [[str(v)[:_VALUE_MAX_LEN] for v in row] for row in rows]
    except Exception:
        return []


def _render_prompt(tables) -> str:
    lines = [
        "可用数据集如下。写 SQL 时请使用「表名」，不要加 ods. 之类的库名前缀；",
        "只能使用列出的表和字段；表之间的 JOIN 键请依据字段名与中文注释自行推理。",
        "",
    ]
    for table in tables:
        title = f" —— {table['title']}" if table.get("title") else ""
        lines.append(f"表 {table['name']}{title}")
        if table.get("description"):
            lines.append(f"  说明：{table['description']}")
        comments = table.get("column_comments") or {}
        columns = table["columns"]
        if comments and len(columns) <= _MAX_COMMENTED_COLUMNS:
            rendered = ", ".join(
                f"{c}（{comments[c]}）" if comments.get(c) else c for c in columns
            )
        else:
            rendered = ", ".join(columns)
        lines.append(f"  字段：{rendered}")
        if table.get("pk_columns"):
            lines.append(f"  主键：{', '.join(table['pk_columns'])}")
        samples = table.get("samples") or []
        if samples:
            lines.append("  样例数据（列=值）：")
            for row in samples:
                pairs = ", ".join(
                    f"{column}={value}" for column, value in zip(columns, row)
                )
                lines.append(f"    {pairs}")
        lines.append("")
    return "\n".join(lines).rstrip()
