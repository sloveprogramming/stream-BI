"""检查本机 PG 里与 ERP 同步相关的记录（不连 ERP）。

用于确认：我启动服务做冒烟时，ERP 自动同步是否真的拉过数据。
只看本地库，不碰 ERP。
"""
from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    n_tasks = c.execute(text("SELECT count(*) FROM sync_tasks")).scalar()
    print("sync_tasks:", n_tasks)
    rows = c.execute(
        text("SELECT id, name, source_table, enabled FROM sync_tasks ORDER BY id LIMIT 10")
    ).fetchall()
    for r in rows:
        print("   ", dict(r._mapping))

    n_hist = c.execute(text("SELECT count(*) FROM sync_history")).scalar()
    print()
    print("sync_history:", n_hist)
    hist = c.execute(
        text(
            "SELECT id, task_id, trigger, status, rows_pulled, rows_written, "
            "started_at FROM sync_history ORDER BY id DESC LIMIT 10"
        )
    ).fetchall()
    for r in hist:
        print("   ", dict(r._mapping))
e.dispose()
