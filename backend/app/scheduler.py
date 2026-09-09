import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models import SyncTask
from app.settings import get_settings
from app.sync.alert import send_alert
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry

logger = logging.getLogger(__name__)


def job_context():
    from app.db import make_engine, make_session_factory
    from app.sync.puller import make_erp_engine

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    erp_engine = make_erp_engine(
        settings.erp_database_url, settings.erp_query_timeout_seconds
    )
    session_factory = make_session_factory(pg_engine)
    return session_factory, erp_engine, pg_engine, settings


def execute_task(task_id: int, trigger: str) -> None:
    session_factory, erp_engine, pg_engine, settings = job_context()
    try:
        with session_factory() as session:
            task = session.get(SyncTask, task_id)
            if task is None or not task.enabled:
                return
            try:
                run_with_retry(
                    lambda: run_sync(
                        session,
                        erp_engine,
                        pg_engine,
                        task,
                        trigger,
                        batch_size=settings.sync_batch_size,
                    ),
                    attempts=settings.sync_retry_attempts,
                    base_delay=settings.sync_retry_base_delay,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("sync task %s failed: %s", task_id, exc)
                send_alert(
                    settings.alert_webhook_url,
                    "同步任务失败",
                    f"task_id={task_id} name={task.name} error={exc}",
                )
    finally:
        erp_engine.dispose()
        pg_engine.dispose()


def refresh_jobs(scheduler: BackgroundScheduler, session_factory) -> None:
    with session_factory() as session:
        tasks = session.query(SyncTask).all()
    enabled_ids: set[int] = set()
    for task in tasks:
        if not task.enabled:
            continue
        enabled_ids.add(task.id)
        scheduler.add_job(
            execute_task,
            CronTrigger.from_crontab(task.cron, timezone="Asia/Hong_Kong"),
            id=f"sync_{task.id}",
            args=[task.id, "cron"],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("sync_") and int(job.id.removeprefix("sync_")) not in enabled_ids:
            job.remove()
