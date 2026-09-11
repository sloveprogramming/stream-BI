"""ERP 表结构变更时，自动演进 ods 层与数据集字段。

背景（原本是个静默数据丢失的坑）：
- `ensure_ods_table` 只有 `CREATE TABLE IF NOT EXISTS`，**没有 ALTER**；
- 同步任务的 `columns` 是创建时固定的，之后不会更新；
- `discovery` 只负责注册新表，不会刷新已有数据集的 `columns`。

于是 ERP 给表加一个字段后，新字段永远同步不过来，**而且不报错**。

本模块对比 ERP 实际字段与「同步任务 / ods 表 / 数据集」三处的差异并自动补齐：

    ERP 新字段 → ALTER TABLE ods.x ADD COLUMN → 追加 task.columns → 追加 dataset.columns

保守策略（都是有意的）：
- **只加列，绝不删列**：删列会丢数据、破坏已注册数据集，只记 warning；
- **类型不一致只告警，不自动 ALTER TYPE**：存量数据转换失败会阻塞整个同步；
- 新列一律可空（存量行没有值）；
- 字段名不合法（非 `[A-Za-z_][A-Za-z0-9_]*`）的列跳过并告警，不影响其他列。
"""
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models import Dataset, SyncTask
from app.sync.ods import mysql_type_to_pg
from app.sync.spec import quote_ident, quote_qualified

logger = logging.getLogger(__name__)

_ERP_COLUMNS_SQL = text(
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_ODS_COLUMNS_SQL = text(
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = :schema"
)


def missing_columns(existing: list[str], erp_columns: list[str]) -> list[str]:
    """ERP 有、但本地（任务配置或 ods 表）没有的业务字段。"""
    known = set(existing or [])
    return [c for c in erp_columns if c not in known and not c.startswith("_")]


def removed_columns(task_columns: list[str], erp_columns: list[str]) -> list[str]:
    """任务里配了、但 ERP 已经删掉的字段（只告警，不删列）。"""
    actual = set(erp_columns or [])
    return [c for c in (task_columns or []) if c not in actual]


def scan_erp_columns(erp_engine: Engine) -> dict[str, dict[str, str]]:
    """{表名: {字段名: MySQL 类型}}。"""
    result: dict[str, dict[str, str]] = {}
    with erp_engine.connect() as conn:
        for table_name, column_name, data_type in conn.execute(_ERP_COLUMNS_SQL).all():
            result.setdefault(table_name, {})[column_name] = (data_type or "text").lower()
    return result


def scan_ods_columns(pg_engine: Engine, schema: str) -> dict[str, set[str]]:
    """{ods 表名(不带 schema): {字段名}}。"""
    result: dict[str, set[str]] = {}
    with pg_engine.connect() as conn:
        for table_name, column_name in conn.execute(
            _ODS_COLUMNS_SQL, {"schema": schema}
        ).all():
            result.setdefault(table_name, set()).add(column_name)
    return result


def _target_table(task, schema: str) -> str:
    target = task.target_table
    if "." not in target:
        target = f"{schema}.{target}"
    return target


def evolve_sync_schemas(
    erp_engine: Engine, pg_engine: Engine, session_factory, schema: str = "ods"
) -> dict:
    """对比并补齐字段差异；返回 {"evolved": [...], "warnings": [...]}。"""
    erp_columns = scan_erp_columns(erp_engine)
    ods_columns = scan_ods_columns(pg_engine, schema)

    evolved: list[dict] = []
    warnings: list[str] = []

    with session_factory() as session:
        tasks = session.query(SyncTask).all()
        datasets = session.query(Dataset).all()

        for task in tasks:
            erp_cols = erp_columns.get(task.source_table)
            if not erp_cols:
                warnings.append(
                    f"{task.source_table}: ERP 已无此表，同步任务 {task.name} 可能已失效"
                )
                continue

            task_cols = list(task.columns or [])
            gone = removed_columns(task_cols, list(erp_cols))
            if gone:
                warnings.append(
                    f"{task.source_table}: ERP 已删除字段 {gone}，"
                    f"ods 列保留不删（如需清理请人工处理）"
                )
            # 与 ods 实际列也比较一次，兼修「任务有、表没有」的历史漂移
            try:
                target = _target_table(task, schema)
                table_short = target.split(".")[-1]
                local_cols = set(task_cols) | (ods_columns.get(table_short) or set())
                # 去掉 ods 自建列，避免把 _synced_at 当成"差异"
                local_cols = {c for c in local_cols if not c.startswith("_")}
            except Exception:  # noqa: BLE001
                warnings.append(f"{task.source_table}: 目标表名 {task.target_table!r} 非法，跳过")
                continue

            add = missing_columns(sorted(local_cols), list(erp_cols))
            if not add:
                continue

            valid: list[tuple[str, str, str]] = []
            for col in add:
                try:
                    valid.append((col, quote_ident(col), mysql_type_to_pg(erp_cols[col])))
                except ValueError:
                    warnings.append(f"{task.source_table}: 字段名非法，跳过 {col!r}")
            if not valid:
                continue

            try:
                ddl = ", ".join(
                    f"ADD COLUMN IF NOT EXISTS {quoted} {pg_type}"
                    for _, quoted, pg_type in valid
                )
                with pg_engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {quote_qualified(target)} {ddl}"))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{task.source_table}: 加列失败 {exc}")
                logger.exception("ods 加列失败: %s", target)
                continue

            added_names = [c for c, _, _ in valid]
            task.columns = task_cols + added_names
            ds_updated = []
            for ds in datasets:
                if ds.source_table == target:
                    ds.columns = list(ds.columns or []) + added_names
                    ds_updated.append(ds.name)
            evolved.append(
                {
                    "table": task.source_table,
                    "target_table": target,
                    "task_id": task.id,
                    "added": added_names,
                    "datasets": ds_updated,
                }
            )

        if evolved or warnings:
            session.commit()

    for item in evolved:
        logger.info(
            "ERP 结构演进：%s 新增字段 %s（数据集 %s）",
            item["table"],
            item["added"],
            item["datasets"],
        )
    for warn in warnings:
        logger.warning("ERP 结构演进告警：%s", warn)

    return {"evolved": evolved, "warnings": warnings}
