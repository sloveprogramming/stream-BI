"""按用户的有效授权收集数据集结构，生成给大模型的 schema 上下文。"""
from app.access.grants import get_effective_grant
from app.models import Dataset

_SAMPLE_LIMIT = 3
_VALUE_MAX_LEN = 50


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
            grant = get_effective_grant(session, user, ds.id)
            if grant is None:
                continue
            columns = grant.field_whitelist or list(ds.columns)
            tables.append(
                {
                    "name": ds.name,
                    "source_table": ds.source_table,
                    "columns": columns,
                    "samples": _sample_rows(pg_engine, ds.source_table, columns),
                    "row_rules": grant.row_rules or [],
                }
            )
    return {"tables": tables, "prompt": _render_prompt(tables)}


def _sample_rows(pg_engine, source_table, columns, limit: int = _SAMPLE_LIMIT):
    from sqlalchemy import text

    if not columns:
        return []
    cols = ", ".join(f'"{c}"' for c in columns)
    try:
        with pg_engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT {cols} FROM {source_table} LIMIT :n"), {"n": limit}
            ).fetchall()
        return [[str(v)[:_VALUE_MAX_LEN] for v in row] for row in rows]
    except Exception:
        return []


def _render_prompt(tables) -> str:
    lines = ["可用数据集（只能使用下列表和字段，JOIN 键依据字段名与语义自行推理）："]
    for t in tables:
        lines.append(
            f"- 表 {t['name']}（{t['source_table']}）字段: {', '.join(t['columns'])}"
        )
        if t["samples"]:
            lines.append(f"  样例数据: {t['samples'][0]}")
    return "\n".join(lines)
