from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import Dataset, DatasetGrant, ReportRun, ReportTask

router = APIRouter(tags=["reports"])

EXPORTS_DIR = Path("exports")


def _require_admin_web(principal: AnyPrincipal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


@router.post("/my/report-tasks", status_code=201)
def create_report_task(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """用户创建自己的定时报表（服务端按 owner 的角色授权裁剪）。"""
    with ctx.session_factory() as session:
        ds = session.get(Dataset, body.get("dataset_id"))
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        if principal.user.role_id is None or not session.query(DatasetGrant).filter_by(
            role_id=principal.user.role_id, dataset_id=ds.id
        ).first():
            raise HTTPException(status_code=403, detail="no grant")
        if session.query(ReportTask).filter_by(name=body["name"]).first():
            raise HTTPException(status_code=409, detail="report task exists")
        task = ReportTask(
            name=body["name"],
            dataset_id=ds.id,
            format=body.get("format", "xlsx"),
            cron=body.get("cron", "0 8 * * *"),
            filters=body.get("filters") or {},
            owner_user_id=principal.user.id,
        )
        session.add(task)
        session.commit()
        return {"id": task.id, "name": task.name}


@router.get("/my/report-tasks")
def list_my_report_tasks(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        tasks = (
            session.query(ReportTask)
            .filter_by(owner_user_id=principal.user.id)
            .order_by(ReportTask.id)
            .all()
        )
        return [
            {
                "id": t.id, "name": t.name, "dataset_id": t.dataset_id,
                "format": t.format, "cron": t.cron, "enabled": t.enabled,
            }
            for t in tasks
        ]


@router.delete("/my/report-tasks/{task_id}")
def delete_my_report_task(
    task_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        task = session.get(ReportTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="not found")
        if task.owner_user_id != principal.user.id and not principal.user.is_admin:
            raise HTTPException(status_code=403, detail="not your task")
        session.query(ReportRun).filter_by(task_id=task_id).delete()
        session.delete(task)
        session.commit()
        return {"deleted": task_id}


@router.get("/admin/report-tasks")
def list_all_report_tasks(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin_web(principal)
    with ctx.session_factory() as session:
        tasks = session.query(ReportTask).order_by(ReportTask.id).all()
        return [
            {
                "id": t.id, "name": t.name, "dataset_id": t.dataset_id,
                "format": t.format, "cron": t.cron, "enabled": t.enabled,
                "owner_user_id": t.owner_user_id,
            }
            for t in tasks
        ]


@router.get("/my/reports")
def my_reports(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        rows = (
            session.query(ReportRun)
            .join(ReportTask, ReportRun.task_id == ReportTask.id)
            .filter(ReportTask.owner_user_id == principal.user.id)
            .order_by(ReportRun.id.desc())
            .limit(100)
            .all()
        )
        return [
            {
                "id": r.id, "task_id": r.task_id, "file_name": r.file_name,
                "row_count": r.row_count, "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.get("/my/reports/{run_id}/download")
def download_report(
    run_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        run = session.get(ReportRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="report not found")
        task = session.get(ReportTask, run.task_id)
        if task is None or task.owner_user_id != principal.user.id:
            raise HTTPException(status_code=403, detail="not your report")
        path = EXPORTS_DIR / run.file_name
        if not path.exists():
            raise HTTPException(status_code=404, detail="file expired")
        return FileResponse(path, filename=run.file_name)
