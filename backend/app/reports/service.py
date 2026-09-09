from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.access.scoped_query import build_scoped_sql
from app.models import Dataset, DatasetGrant, ReportRun, ReportTask, User

EXPORTS_DIR = Path("exports")


def generate_report_file(task: ReportTask) -> ReportRun:
    """按任务生成报表文件（走权限裁剪：任务 owner 的角色授权）。"""
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    EXPORTS_DIR.mkdir(exist_ok=True)
    with sf() as session:
        ds = session.get(Dataset, task.dataset_id)
        run = ReportRun(task_id=task.id, file_name="", status="failed")
        try:
            owner = session.get(User, task.owner_user_id)
            grant = (
                session.query(DatasetGrant)
                .filter_by(role_id=owner.role_id, dataset_id=task.dataset_id)
                .first()
            )
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

                content_buf = Path(EXPORTS_DIR / file_name).open("w", newline="", encoding="utf-8-sig")
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
                wb.save(EXPORTS_DIR / file_name)
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
