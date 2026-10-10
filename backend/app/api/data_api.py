import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.access.grants import query_visible_datasets, resolve_grant
from app.access.scoped_query import build_count_sql, build_scoped_sql
from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import AuditLog, Dataset

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("")
def list_datasets(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    user = principal.user
    with ctx.session_factory() as session:
        # 管理员可见全部数据集（含自动发现的新表），普通用户仅见已授权的
        rows = query_visible_datasets(session, user).all()
        return [
            {
                "id": d.id,
                "name": d.name,
                "title": d.title,
                "description": d.description,
                "columns": d.columns,
                "column_count": len(d.columns or []),
            }
            for d in rows
        ]


@router.get("/{name}/rows")
def query_rows(
    name: str,
    request: Request,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    principal: AnyPrincipal = Depends(get_any_principal),
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
        grant = resolve_grant(session, principal.user, ds.id)
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
                api_key_id=getattr(principal.api_key, 'id', None),
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

