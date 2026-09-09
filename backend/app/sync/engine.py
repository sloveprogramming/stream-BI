import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import SyncHistory
from app.sync.ods import ensure_ods_table
from app.sync.puller import iter_rows
from app.sync.spec import quote_qualified, spec_from_task
from app.sync.writer import upsert_rows


def _row_hash(row: dict) -> str:
    return hashlib.sha1(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def run_sync(
    session: Session,
    erp_engine: Engine,
    pg_engine: Engine,
    task,
    trigger: str,
    batch_size: int = 5000,
) -> dict:
    spec = spec_from_task(task)
    history = SyncHistory(task_id=task.id, trigger=trigger, status="failed")
    session.add(history)
    session.commit()
    result = {"status": "failed", "rows_pulled": 0, "rows_written": 0, "error": None}
    try:
        ensure_ods_table(erp_engine, pg_engine, spec, task.target_table)
        pulled = written = 0
        new_watermark = task.watermark
        if spec.incremental_field is not None:
            for batch in iter_rows(erp_engine, spec, task.watermark, batch_size):
                written += upsert_rows(pg_engine, task.target_table, batch, spec.pk_columns)
                pulled += len(batch)
                batch_max = max(b[spec.incremental_field] for b in batch)
                if new_watermark is None or batch_max > new_watermark:
                    new_watermark = batch_max
        else:
            existing: dict = {}
            with pg_engine.connect() as conn:
                for row in conn.execute(text(f"SELECT * FROM {quote_qualified(task.target_table)}")):
                    d = dict(row._mapping)
                    d.pop("_synced_at", None)
                    key = tuple(d[c] for c in spec.pk_columns)
                    existing[key] = _row_hash(d)
            for batch in iter_rows(erp_engine, spec, None, batch_size):
                to_write = []
                for row in batch:
                    key = tuple(row[c] for c in spec.pk_columns)
                    if existing.get(key) != _row_hash(row):
                        to_write.append(row)
                if to_write:
                    written += upsert_rows(pg_engine, task.target_table, to_write, spec.pk_columns)
                pulled += len(batch)
        if spec.incremental_field is not None and new_watermark is not None:
            if getattr(new_watermark, "tzinfo", None) is None:
                new_watermark = new_watermark.replace(tzinfo=timezone.utc)
            task.watermark = new_watermark
        history.status = "success"
        result.update(status="success", rows_pulled=pulled, rows_written=written)
    except Exception as exc:  # noqa: BLE001 —— 记录后向上抛，交由重试层处理
        history.error = str(exc)[:2000]
        history.finished_at = datetime.now(timezone.utc)
        result["error"] = str(exc)[:2000]
        session.commit()
        raise
    history.rows_pulled = pulled
    history.rows_written = written
    history.finished_at = datetime.now(timezone.utc)
    session.commit()
    return result
