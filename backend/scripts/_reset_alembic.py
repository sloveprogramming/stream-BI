from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.begin() as c:
    # 把 alembic 版本退回上一个 revision，让 upgrade 能重跑本迁移
    c.execute(text("DELETE FROM alembic_version WHERE version_num = 'a1b2c3d4e5f6'"))
    c.execute(text("INSERT INTO alembic_version (version_num) VALUES ('f3a9b4c2d5e8')"))
print("alembic_version reset to f3a9b4c2d5e8")
e.dispose()
