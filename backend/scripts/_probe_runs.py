from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    rows = c.execute(
        text(
            "SELECT id, task_id, file_name, status, left(coalesce(error,''), 400) AS err "
            "FROM report_runs ORDER BY id DESC LIMIT 5"
        )
    ).fetchall()
    for r in rows:
        print(dict(r._mapping))
        print("---")
e.dispose()
