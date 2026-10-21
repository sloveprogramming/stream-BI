from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    rows = c.execute(
        text("SELECT id, name, kind, is_default FROM model_providers ORDER BY id")
    ).fetchall()
    print("model_providers 共", len(rows), "条：")
    for r in rows:
        print("  ", dict(r._mapping))
    print()
    n = c.execute(
        text("SELECT count(*) FROM model_providers WHERE kind='llm'")
    ).scalar()
    print("kind='llm' 条数:", n, "(>0 则新建的不会成为默认 → 测试失败)")
e.dispose()
