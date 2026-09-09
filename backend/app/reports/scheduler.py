from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models import ReportTask
from app.reports.service import run_report_task


def refresh_report_jobs(scheduler: BackgroundScheduler, session_factory) -> None:
    with session_factory() as session:
        tasks = session.query(ReportTask).filter_by(enabled=True).all()
    enabled_ids: set[int] = set()
    for task in tasks:
        enabled_ids.add(task.id)
        scheduler.add_job(
            _execute,
            CronTrigger.from_crontab(task.cron, timezone="Asia/Hong_Kong"),
            id=f"report_{task.id}",
            args=[task.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("report_") and int(job.id.removeprefix("report_")) not in enabled_ids:
            job.remove()


def _execute(task_id: int) -> None:
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        task = session.get(ReportTask, task_id)
        if task is None or not task.enabled:
            return
        from app.reports.service import generate_report_file

        generate_report_file(task)
    engine.dispose()
