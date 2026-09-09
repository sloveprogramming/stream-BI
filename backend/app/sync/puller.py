from collections.abc import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.sync.spec import SyncSpec, quote_ident


def make_erp_engine(url: str, query_timeout: int) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "read_timeout": query_timeout},
    )


def build_select_sql(spec: SyncSpec, watermark) -> tuple[str, dict]:
    cols = ", ".join(quote_ident(c) for c in spec.columns)
    table = quote_ident(spec.source_table)
    params: dict = {}
    where = ""
    if watermark is not None and spec.incremental_field is not None:
        where = f" WHERE {quote_ident(spec.incremental_field)} > :watermark"
        params["watermark"] = watermark
    order = " ORDER BY " + ", ".join(quote_ident(c) for c in spec.pk_columns)
    return f"SELECT {cols} FROM {table}{where}{order}", params


def iter_rows(
    erp_engine: Engine, spec: SyncSpec, watermark, batch_size: int
) -> Iterator[list[dict]]:
    sql, params = build_select_sql(spec, watermark)
    with erp_engine.connect() as conn:
        result = conn.execution_options(stream_results=True, yield_per=batch_size).execute(
            text(sql), params
        )
        while batch := result.fetchmany(batch_size):
            yield [dict(row._mapping) for row in batch]
