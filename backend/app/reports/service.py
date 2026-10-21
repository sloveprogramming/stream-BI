from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.access.grants import resolve_grant
from app.access.scoped_query import build_scoped_sql
from app.models import Dataset, ReportRun, ReportTask, User

# 报表导出目录的默认值。**用绝对路径**：相对路径依赖进程 cwd，
# 换个目录启动（或从别处 import）就不知写到哪去了。
# 定位到 backend/exports（app/reports/service.py -> app -> backend）。
DEFAULT_EXPORTS_DIR = Path(__file__).resolve().parents[2] / "exports"


def exports_dir(settings=None) -> Path:
    """导出目录。`BISQL_EXPORT_DIR` 可覆盖（受限环境指到可写位置）。

    不传 `settings` 时自己去取 `get_settings()` —— 让"写文件"与"读文件"
    两条路径拿到同一个目录，否则下载会找不到刚生成的文件。
    """
    if settings is None:
        from app.settings import get_settings

        settings = get_settings()
    configured = getattr(settings, "export_dir", "")
    return Path(configured) if configured else DEFAULT_EXPORTS_DIR


def generate_report_file(task: ReportTask) -> ReportRun:
    """按任务生成报表文件（走权限裁剪：任务 owner 的角色授权）。"""
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    target_dir = exports_dir(settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    with sf() as session:
        ds = session.get(Dataset, task.dataset_id)
        run = ReportRun(task_id=task.id, file_name="", status="failed")
        try:
            owner = session.get(User, task.owner_user_id)
            grant = resolve_grant(session, owner, task.dataset_id)
            if grant is None:
                raise ValueError("owner 无该数据集授权")
            sql, params = build_scoped_sql(ds, grant, task.filters or {}, settings.export_max_rows, 0)
            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchmany(settings.export_max_rows)
            columns = grant.field_whitelist or list(ds.columns)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            file_name = f"report_{task.id}_{stamp}.{task.format}"
            if task.format == "csv":
                import csv

                content_buf = (target_dir / file_name).open(
                    "w", newline="", encoding="utf-8-sig"
                )
                writer = csv.writer(content_buf)
                writer.writerow(columns)
                writer.writerows(rows)
                content_buf.close()
            else:
                from openpyxl import Workbook

                wb = Workbook(write_only=True)
                ws = wb.create_sheet()
                ws.append(columns)
                for row in rows:
                    ws.append(list(row))
                wb.save(target_dir / file_name)
            run.file_name = file_name
            run.row_count = len(rows)
            run.status = "success"
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error = str(exc)[:2000]
        session.add(run)
        session.commit()
        return run


def run_report_task(task: ReportTask) -> ReportRun:
    """调度入口：开新会话执行任务（供 APScheduler 调用）。"""
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        fresh = session.get(ReportTask, task.id)
        return generate_report_file(fresh)
