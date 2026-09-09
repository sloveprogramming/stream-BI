from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import Context, get_ctx
from app.models import SyncHistory, SyncTask
from app.sync.alert import send_alert
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/tasks")
def list_tasks(ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        tasks = session.query(SyncTask).order_by(SyncTask.id).all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "source_table": t.source_table,
                "target_table": t.target_table,
                "cron": t.cron,
                "enabled": t.enabled,
                "watermark": t.watermark.isoformat() if t.watermark else None,
            }
            for t in tasks
        ]


@router.post("/tasks/{task_id}/run")
def run_task(task_id: int, ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        task = session.get(SyncTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        try:
            result = run_with_retry(
                lambda: run_sync(
                    session,
                    ctx.erp_engine,
                    ctx.pg_engine,
                    task,
                    trigger="manual",
                    batch_size=ctx.settings.sync_batch_size,
                ),
                attempts=ctx.settings.sync_retry_attempts,
                base_delay=ctx.settings.sync_retry_base_delay,
            )
        except Exception as exc:  # noqa: BLE001
            send_alert(
                ctx.settings.alert_webhook_url,
                "同步任务失败",
                f"task_id={task_id} error={exc}",
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "rows_pulled": 0,
                "rows_written": 0,
                "error": str(exc)[:500],
            }
        return {"task_id": task_id, **result}


@router.get("/tasks/{task_id}/history")
def task_history(
    task_id: int, limit: int = Query(20, le=100), ctx: Context = Depends(get_ctx)
):
    with ctx.session_factory() as session:
        if session.get(SyncTask, task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        rows = (
            session.query(SyncHistory)
            .filter(SyncHistory.task_id == task_id)
            .order_by(SyncHistory.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": h.id,
                "trigger": h.trigger,
                "status": h.status,
                "started_at": h.started_at.isoformat() if h.started_at else None,
                "finished_at": h.finished_at.isoformat() if h.finished_at else None,
                "rows_pulled": h.rows_pulled,
                "rows_written": h.rows_written,
                "error": h.error,
            }
            for h in rows
        ]
