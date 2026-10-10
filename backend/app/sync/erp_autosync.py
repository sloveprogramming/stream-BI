"""扫描 ERP(MySQL) 库中的表，自动创建同步任务。

背景：`ods` 侧的新表能被自动发现上架为数据集，但「哪些 ERP 表要同步」原本靠人工
配置，所以 ERP 新建的表永远到不了中台。本模块补上这一环：

    ERP 新表 --(本模块自动建 SyncTask)--> cron 拉取 --> ods 表 -->(discovery)--> 数据集

实现要点：
- 只读 ERP 的 information_schema 元数据，不写 ERP、不写业务数据；
- 主键取 MySQL PRIMARY KEY，没主键的表跳过（upsert 需要冲突目标，硬拉会重复）；
- 增量字段自动识别常见命名（modified / update_time / gmt_modified ...），
  识别不到则退化为「全量比对」（见 sync/engine.py），不会丢数据；
- 跳过下划线开头的列（如 `_synced_at`），避免与 ods 层自建的同名列冲突；
- 已有同名任务（无论是否启用）的表不再重复创建，尊重人工的停用操作；
- 单次创建数量受 max_tables 限制，防止误配时把一个几百张表的 ERP 全量拉过来。
"""
import fnmatch
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models import SyncTask

logger = logging.getLogger(__name__)

_TABLES_SQL = text(
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
    "ORDER BY TABLE_NAME"
)

_COLUMNS_SQL = text(
    "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_PK_SQL = text(
    "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'PRIMARY' "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

# ERP 里的中文列注释是问数准确率的高价值线索（模型据此理解字段业务含义），必须捞回来
_COLUMN_COMMENTS_SQL = text(
    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() "
    "  AND COLUMN_COMMENT IS NOT NULL AND COLUMN_COMMENT <> '' "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_INCREM_CANDIDATES = (
    "modified",
    "updated_at",
    "update_time",
    "updated",
    "gmt_modified",
    "gmt_modified_time",
    "last_modified",
    "modify_time",
    "last_update",
)

AUTO_SUFFIX = "__auto"


def pick_incremental_field(columns: list[str]) -> str | None:
    """从字段里挑一个增量字段；挑不到返回 None（走全量比对）。"""
    lowered = {c.lower(): c for c in columns}
    for candidate in _INCREM_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    for low, original in lowered.items():
        if low.endswith("_modified") or low.startswith("modified"):
            return original
    return None


def _split_patterns(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def plan_sync_tasks(
    tables: list[str],
    columns: dict[str, list[str]],
    pk_map: dict[str, list[str]],
    existing_source_tables: set[str],
    include: str = "",
    exclude: str = "",
    max_tables: int = 100,
) -> dict:
    """纯决策函数（不碰数据库，便于测试）。

    返回 {"create": [ {table, pk_columns, columns, incremental_field} ... ],
          "skipped_no_pk": [...], "skipped_existing": [...], "capped": bool}
    """
    include_pats = _split_patterns(include)
    exclude_pats = _split_patterns(exclude)
    to_create: list[dict] = []
    skipped_no_pk: list[str] = []
    skipped_existing: list[str] = []
    capped = False

    for table in tables:
        if include_pats and not _matches_any(table, include_pats):
            continue
        if exclude_pats and _matches_any(table, exclude_pats):
            continue
        if table in existing_source_tables:
            skipped_existing.append(table)
            continue
        # 下划线开头的列是内部字段，不进 ods 数据集
        cols = [c for c in columns.get(table, []) if not c.startswith("_")]
        pk = list(pk_map.get(table) or [])
        if not cols or not pk or not set(pk).issubset(cols):
            skipped_no_pk.append(table)
            continue
        if len(to_create) >= max_tables:
            capped = True
            break
        to_create.append(
            {
                "table": table,
                "pk_columns": pk,
                "columns": cols,
                "incremental_field": pick_incremental_field(cols),
            }
        )

    return {
        "create": to_create,
        "skipped_no_pk": skipped_no_pk,
        "skipped_existing": skipped_existing,
        "capped": capped,
    }


def scan_erp_metadata(erp_engine: Engine):
    """读 ERP 的库名、表清单、字段清单、主键清单。"""
    with erp_engine.connect() as conn:
        db_name = conn.execute(text("SELECT DATABASE()")).scalar()
        tables = [r[0] for r in conn.execute(_TABLES_SQL).all()]
        columns: dict[str, list[str]] = {}
        for table_name, column_name in conn.execute(_COLUMNS_SQL).all():
            columns.setdefault(table_name, []).append(column_name)
        pk_map: dict[str, list[str]] = {}
        for table_name, column_name in conn.execute(_PK_SQL).all():
            pk_map.setdefault(table_name, []).append(column_name)
    return db_name, tables, columns, pk_map


def scan_column_comments(erp_engine: Engine) -> dict[str, dict[str, str]]:
    """读 ERP 字段的中文注释（只取有注释的）：`{表名: {字段: 注释}}`。"""
    result: dict[str, dict[str, str]] = {}
    with erp_engine.connect() as conn:
        for table_name, column_name, comment in conn.execute(_COLUMN_COMMENTS_SQL).all():
            value = str(comment or "").strip()
            if value:
                result.setdefault(table_name, {})[column_name] = value
    return result


def sync_column_comments(erp_engine: Engine, session_factory) -> dict:
    """把 ERP 的字段注释回填到 `Dataset.column_comments`。

    桥接关系：ERP 表名 = `SyncTask.source_table` → `SyncTask.target_table`（ods 表）
    = `Dataset.source_table`。只回填「数据集里确实存在的列」，且内容没变就不写库。
    """
    from app.models import Dataset

    comments = scan_column_comments(erp_engine)
    if not comments:
        return {"commented_tables": 0, "updated_datasets": 0}

    with session_factory() as session:
        target_to_source = {
            t.target_table: t.source_table for t in session.query(SyncTask).all()
        }
        updated = 0
        for dataset in session.query(Dataset).all():
            erp_table = target_to_source.get(dataset.source_table)
            if erp_table is None:
                continue
            by_column = comments.get(erp_table) or {}
            picked = {
                column: by_column[column]
                for column in (dataset.columns or [])
                if by_column.get(column)
            }
            if picked and picked != (dataset.column_comments or {}):
                dataset.column_comments = picked
                updated += 1
        if updated:
            session.commit()
    if updated:
        logger.info("ERP 字段注释回填：%s 个数据集已更新", updated)
    return {"commented_tables": len(comments), "updated_datasets": updated}


def discover_sync_tasks(erp_engine: Engine, session_factory, settings) -> dict:
    """扫描 ERP 并为新表创建同步任务，返回本次新建的任务 id 与名称。"""
    db_name, tables, columns, pk_map = scan_erp_metadata(erp_engine)
    with session_factory() as session:
        existing = {t.source_table for t in session.query(SyncTask).all()}
        plan = plan_sync_tasks(
            tables,
            columns,
            pk_map,
            existing,
            include=getattr(settings, "erp_autosync_include", ""),
            exclude=getattr(settings, "erp_autosync_exclude", ""),
            max_tables=int(getattr(settings, "erp_autosync_max_tables", 100)),
        )
        cron = getattr(settings, "erp_autosync_cron", "*/30 * * * *")
        created: list[dict] = []
        for item in plan["create"]:
            task = SyncTask(
                name=item["table"] + AUTO_SUFFIX,
                source_table=item["table"],
                target_table=f"ods.{item['table']}",
                pk_columns=item["pk_columns"],
                columns=item["columns"],
                incremental_field=item["incremental_field"],
                cron=cron,
                enabled=True,
            )
            session.add(task)
            session.flush()
            created.append(
                {
                    "id": task.id,
                    "name": task.name,
                    "source_table": task.source_table,
                    "incremental_field": task.incremental_field,
                }
            )
        if created:
            session.commit()

    if plan["skipped_no_pk"]:
        logger.warning(
            "ERP 表因缺少主键被跳过（无法 upsert）：%s", plan["skipped_no_pk"]
        )
    if plan["capped"]:
        logger.warning(
            "ERP 待同步表超过上限 %s，本次只创建了前 %s 个；"
            "请用 BISQL_ERP_AUTOSYNC_INCLUDE/EXCLUDE 收窄范围。",
            getattr(settings, "erp_autosync_max_tables", 100),
            len(created),
        )
    if created:
        logger.info(
            "ERP 自动同步：库 %s 新建 %s 个同步任务 -> %s",
            db_name,
            len(created),
            [c["source_table"] for c in created],
        )
    try:
        # 顺带把中文列注释回填给数据集：问数时模型据此理解字段业务含义
        comment_stats = sync_column_comments(erp_engine, session_factory)
    except Exception:  # noqa: BLE001 —— 注释回填失败不该影响同步任务的创建
        logger.exception("ERP 字段注释回填失败")
        comment_stats = {"commented_tables": 0, "updated_datasets": 0}
    return {
        "database": db_name,
        "erp_tables": len(tables),
        "created": created,
        "skipped_no_pk": plan["skipped_no_pk"],
        "capped": plan["capped"],
        "column_comments": comment_stats,
    }
