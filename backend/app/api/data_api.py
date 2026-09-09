import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.access.scoped_query import build_count_sql, build_scoped_sql
from app.api.deps import Context, Principal, get_ctx, get_principal
from app.models import AuditLog, Dataset, DatasetGrant

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("")
def list_datasets(
    principal: Principal = Depends(get_principal),
    ctx: Context = Depends(get_ctx),
):
    user = principal.user
    if user.role_id is None:
        return []
    with ctx.session_factory() as session:
        rows = (
            session.query(Dataset)
            .join(DatasetGrant, DatasetGrant.dataset_id == Dataset.id)
            .filter(
                DatasetGrant.role_id == user.role_id,
                Dataset.enabled.is_(True),
            )
            .order_by(Dataset.id)
            .all()
        )
        return [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "columns": d.columns,
            }
            for d in rows
        ]


@router.get("/{name}/rows")
def query_rows(
    name: str,
    request: Request,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_principal),
    ctx: Context = Depends(get_ctx),
):
    started = time.perf_counter()
    filters = {
        k: v for k, v in request.query_params.items() if k not in ("limit", "offset")
    }
    with ctx.session_factory() as session:
        ds = session.query(Dataset).filter_by(name=name, enabled=True).first()
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        if principal.user.role_id is None:
            raise HTTPException(status_code=403, detail="no grant")
        grant = (
            session.query(DatasetGrant)
            .filter_by(role_id=principal.user.role_id, dataset_id=ds.id)
            .first()
        )
        if grant is None:
            raise HTTPException(status_code=403, detail="no grant")
        try:
            sql, params = build_scoped_sql(ds, grant, filters, limit, offset)
            count_sql, count_params = build_count_sql(ds, grant, filters)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        with ctx.pg_engine.connect() as conn:
            total = conn.execute(text_exec(count_sql), count_params).scalar()
            result = conn.execute(text_exec(sql), params)
            rows = [dict(r._mapping) for r in result]

        duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(
            AuditLog(
                user_id=principal.user.id,
                api_key_id=principal.api_key.id,
                channel="api",
                dataset=ds.name,
                action="query",
                row_count=len(rows),
                duration_ms=duration_ms,
                detail={"filters": filters, "limit": limit, "offset": offset},
            )
        )
        session.commit()
        return {
            "dataset": ds.name,
            "columns": grant.field_whitelist or list(ds.columns),
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


def text_exec(sql: str):
    from sqlalchemy import text

    return text(sql)
