"""把启用的巡检规则挂进 APScheduler（与同步/报表/监控同一套调度器）。"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models import DataQualityRule

JOB_PREFIX = "quality_"


def refresh_quality_jobs(scheduler: BackgroundScheduler, session_factory) -> None:
    """按当前启用的规则重挂任务；规则被禁用/删除时摘掉对应任务。"""
    with session_factory() as session:
        tasks = session.query(DataQualityRule).filter_by(enabled=True).all()
        task_ids = [t.id for t in tasks]

    for task_id in task_ids:
        with session_factory() as session:
            rule = session.get(DataQualityRule, task_id)
            if rule is None:
                continue
            cron = rule.cron
        scheduler.add_job(
            _execute,
            CronTrigger.from_crontab(cron, timezone="Asia/Hong_Kong"),
            id=f"{JOB_PREFIX}{task_id}",
            args=[task_id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    for job in list(scheduler.get_jobs()):
        if not job.id.startswith(JOB_PREFIX):
            continue
        try:
            job_id = int(job.id.removeprefix(JOB_PREFIX))
        except ValueError:
            continue
        if job_id not in set(task_ids):
            job.remove()


def _execute(rule_id: int) -> None:
    """调度入口：开新 engine 跑（后台线程不能复用请求的 engine）。"""
    from app.db import make_engine, make_session_factory
    from app.quality_runner import run_rule
    from app.settings import get_settings

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    session_factory = make_session_factory(pg_engine)
    try:
        run_rule(rule_id, session_factory=session_factory, pg_engine=pg_engine)
    finally:
        pg_engine.dispose()
