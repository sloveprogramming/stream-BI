from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    print("active:", c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE state='active'")).scalar())
    print("total :", c.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar())
    print("max   :", c.execute(text("SELECT setting FROM pg_settings WHERE name='max_connections'")).scalar())
    print()
    rows = c.execute(
        text(
            "SELECT pid, state, wait_event_type, wait_event, "
            "now() - query_start AS dur, left(query, 60) AS q "
            "FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start"
        )
    ).fetchall()
    for r in rows:
        print(dict(r._mapping))
e.dispose()
