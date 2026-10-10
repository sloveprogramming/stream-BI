"""把启用的监控任务挂进 APScheduler（与同步任务/定时报表同一套调度器）。"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models import MonitorTask

JOB_PREFIX = "monitor_"


def refresh_monitor_jobs(scheduler: BackgroundScheduler, session_factory) -> None:
    with session_factory() as session:
        tasks = session.query(MonitorTask).filter_by(enabled=True).all()

    enabled_ids: set[int] = set()
    for task in tasks:
        enabled_ids.add(task.id)
        scheduler.add_job(
            _execute,
            CronTrigger.from_crontab(task.cron, timezone="Asia/Hong_Kong"),
            id=f"{JOB_PREFIX}{task.id}",
            args=[task.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    for job in list(scheduler.get_jobs()):
        if job.id.startswith(JOB_PREFIX):
            try:
                job_id = int(job.id.removeprefix(JOB_PREFIX))
            except ValueError:
                continue
            if job_id not in enabled_ids:
                job.remove()


def _execute(task_id: int) -> None:
    from app.db import make_engine, make_session_factory
    from app.monitor.runner import run_monitor
    from app.settings import get_settings

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    session_factory = make_session_factory(pg_engine)
    try:
        run_monitor(task_id, session_factory, pg_engine, settings)
    finally:
        pg_engine.dispose()
