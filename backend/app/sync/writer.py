from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import quote_ident, quote_qualified


def upsert_rows(pg_engine: Engine, target_table: str, rows: list[dict], pk_columns: list[str]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    all_cols = cols + ["_synced_at"]
    col_sql = ", ".join(quote_ident(c) for c in all_cols)
    pk_sql = ", ".join(quote_ident(c) for c in pk_columns)
    update_sql = ", ".join(
        f"{quote_ident(c)} = EXCLUDED.{quote_ident(c)}" for c in cols if c not in pk_columns
    )
    if update_sql:
        conflict = f"ON CONFLICT ({pk_sql}) DO UPDATE SET {update_sql}, _synced_at = now()"
    else:
        conflict = f"ON CONFLICT ({pk_sql}) DO NOTHING"
    sql = text(
        f"INSERT INTO {quote_qualified(target_table)} ({col_sql}) VALUES (:{', :'.join(all_cols)}) "
        f"{conflict}"
    )
    params = [{**row, "_synced_at": datetime.now(timezone.utc)} for row in rows]
    with pg_engine.begin() as conn:
        conn.execute(sql, params)
    return len(rows)
