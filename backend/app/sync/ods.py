from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import SyncSpec, quote_ident

_TYPE_MAP = [
    ("bigint", "bigint"),
    ("int", "integer"),
    ("smallint", "smallint"),
    ("tinyint", "smallint"),
    ("decimal", "numeric"),
    ("numeric", "numeric"),
    ("double", "double precision"),
    ("float", "double precision"),
    ("datetime", "timestamptz"),
    ("timestamp", "timestamptz"),
    ("date", "date"),
    ("char", "text"),
    ("text", "text"),
    ("blob", "bytea"),
    ("json", "jsonb"),
]


def mysql_type_to_pg(mysql_type: str) -> str:
    t = mysql_type.lower()
    for prefix, pg in _TYPE_MAP:
        if t.startswith(prefix):
            return pg
    return "text"


def _pg_type_from_value(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, float):
        return "double precision"
    if isinstance(value, Decimal):
        return "numeric"
    if isinstance(value, datetime):
        return "timestamptz"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "bytea"
    if isinstance(value, (dict, list)):
        return "jsonb"
    return "text"


def _column_types(erp_engine: Engine, spec: SyncSpec) -> dict[str, str]:
    """MySQL 走 information_schema；其他方言（测试用 sqlite）取一行样本推断。"""
    if erp_engine.dialect.name == "mysql":
        with erp_engine.connect() as erp_conn:
            rows = erp_conn.execute(
                text(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                    "ORDER BY ORDINAL_POSITION"
                ),
                {"t": spec.source_table},
            ).all()
        mapped = {name: mysql_type_to_pg(dtype or "text") for name, dtype in rows}
        if all(c in mapped for c in spec.columns):
            return {c: mapped[c] for c in spec.columns}
    cols = ", ".join(quote_ident(c) for c in spec.columns)
    with erp_engine.connect() as erp_conn:
        sample = (
            erp_conn.execute(
                text(f"SELECT {cols} FROM {quote_ident(spec.source_table)} LIMIT 1")
            )
            .mappings()
            .first()
        )
    return {
        c: _pg_type_from_value(sample.get(c) if sample else None) for c in spec.columns
    }


def ensure_ods_table(erp_engine: Engine, pg_engine: Engine, spec: SyncSpec, target_table: str) -> None:
    schema, _, table = target_table.rpartition(".")
    schema = schema or "ods"
    types = _column_types(erp_engine, spec)
    col_defs = [f"{quote_ident(c)} {types[c]}" for c in spec.columns]
    pk = ", ".join(quote_ident(c) for c in spec.pk_columns)
    ddl = (
        f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema)}; "
        f"CREATE TABLE IF NOT EXISTS {quote_ident(schema)}.{quote_ident(table)} "
        f"({', '.join(col_defs)}, _synced_at timestamptz NOT NULL DEFAULT now(), "
        f"PRIMARY KEY ({pk}))"
    )
    with pg_engine.begin() as pg_conn:
        pg_conn.execute(text(ddl))
