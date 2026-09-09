import csv
import io
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.access.scoped_query import build_scoped_sql
from app.api.deps import Context, Principal, get_ctx, get_principal
from app.models import AuditLog, Dataset, DatasetGrant
from app.settings import get_settings

router = APIRouter(prefix="/datasets", tags=["export"])


def _grant_for(session, principal: Principal, ds: Dataset) -> DatasetGrant:
    if principal.user.role_id is None:
        raise HTTPException(status_code=403, detail="no grant")
    grant = (
        session.query(DatasetGrant)
        .filter_by(role_id=principal.user.role_id, dataset_id=ds.id)
        .first()
    )
    if grant is None:
        raise HTTPException(status_code=403, detail="no grant")
    return grant


@router.get("/{name}/export")
def export_rows(
    name: str,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    principal: Principal = Depends(get_principal),
    ctx: Context = Depends(get_ctx),
):
    from sqlalchemy import text

    started = time.perf_counter()
    max_rows = ctx.settings.export_max_rows
    with ctx.session_factory() as session:
        ds = session.query(Dataset).filter_by(name=name, enabled=True).first()
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        grant = _grant_for(session, principal, ds)
        sql, params = build_scoped_sql(ds, grant, {}, max_rows, 0)
        with ctx.pg_engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise HTTPException(status_code=400, detail=f"导出行数超过上限 {max_rows}")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if format == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(grant.field_whitelist or list(ds.columns))
            writer.writerows(rows)
            content = buf.getvalue().encode("utf-8-sig")
            media = "text/csv; charset=utf-8"
            filename = f"{ds.name}_{stamp}.csv"
        else:
            from openpyxl import Workbook

            wb = Workbook(write_only=True)
            ws = wb.create_sheet()
            ws.append(grant.field_whitelist or list(ds.columns))
            for row in rows:
                ws.append(list(row))
            out = io.BytesIO()
            wb.save(out)
            content = out.getvalue()
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{ds.name}_{stamp}.xlsx"

        duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(
            AuditLog(
                user_id=principal.user.id,
                api_key_id=principal.api_key.id,
                channel="export",
                dataset=ds.name,
                action="export",
                row_count=len(rows),
                duration_ms=duration_ms,
                detail={"format": format},
            )
        )
        session.commit()
        return Response(
            content=content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
