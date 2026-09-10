"""自动发现 PostgreSQL 中 ods 模式的新表并注册为数据集。

ERP 定时同步会按需在 `ods` 模式建表。本模块扫描 information_schema，
把尚未登记为 Dataset 的表自动注册，使前端在几秒内即可看到新数据集。

可见性：自动注册的数据集默认对所有具备授权的用户开放查询；管理员可见全部数据集。
"""
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models import Dataset

logger = logging.getLogger(__name__)

_COLUMNS_SQL = text(
    "SELECT c.table_name, c.column_name "
    "FROM information_schema.columns c "
    "JOIN information_schema.tables t "
    "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
    "WHERE c.table_schema = :schema AND t.table_type = 'BASE TABLE' "
    "ORDER BY c.table_name, c.ordinal_position"
)

_PK_SQL = text(
    "SELECT tc.table_name, kcu.column_name "
    "FROM information_schema.table_constraints tc "
    "JOIN information_schema.key_column_usage kcu "
    "  ON tc.constraint_name = kcu.constraint_name "
    " AND tc.table_schema = kcu.table_schema "
    "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = :schema "
    "ORDER BY tc.table_name, kcu.ordinal_position"
)


def _scan(pg_engine: Engine, schema: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """返回 {表名: [业务字段...]} 与 {表名: [主键字段...]}。

    以下划线开头的内部字段（如 `_synced_at`）不纳入数据集字段。
    """
    with pg_engine.connect() as conn:
        col_rows = conn.execute(_COLUMNS_SQL, {"schema": schema}).all()
        pk_rows = conn.execute(_PK_SQL, {"schema": schema}).all()

    tables: dict[str, list[str]] = {}
    for table_name, column_name in col_rows:
        if str(column_name).startswith("_"):
            continue
        tables.setdefault(table_name, []).append(column_name)

    pk_map: dict[str, list[str]] = {}
    for table_name, column_name in pk_rows:
        pk_map.setdefault(table_name, []).append(column_name)
    return tables, pk_map


def discover_datasets(session_factory, pg_engine: Engine, schema: str = "ods") -> list[dict]:
    """把 ods 模式下未注册的表登记为 Dataset，返回本次新建的数据集清单。"""
    tables, pk_map = _scan(pg_engine, schema)
    if not tables:
        return []

    created: list[dict] = []
    with session_factory() as session:
        existing_rows = session.query(Dataset).all()
        existing_tables = {d.source_table for d in existing_rows}
        used_names = {d.name for d in existing_rows}

        for table_name, columns in tables.items():
            source_table = f"{schema}.{table_name}"
            if source_table in existing_tables or not columns:
                continue
            pk = [c for c in pk_map.get(table_name, []) if c in columns] or [columns[0]]
            name = table_name[:64]
            suffix = 2
            while name in used_names:
                name = f"{table_name[:60]}_{suffix}"
                suffix += 1
            session.add(
                Dataset(
                    name=name,
                    title=table_name,
                    source_table=source_table,
                    pk_columns=pk,
                    columns=list(columns),
                    description="ERP 同步自动发现",
                )
            )
            used_names.add(name)
            existing_tables.add(source_table)
            created.append(
                {"name": name, "source_table": source_table, "column_count": len(columns)}
            )

        if created:
            session.commit()

    if created:
        logger.info(
            "自动发现 %d 个新数据集: %s", len(created), [c["name"] for c in created]
        )
    return created
