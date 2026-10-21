"""找出真正有数据的数据集（行数 > 100），供问数测试用。

只看本地 PG，不连 ERP。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db import make_engine

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    # 从 pg_class 拿近似行数，避免对每张表做 count(*)
    rows = c.execute(text("""
        SELECT c.relname AS tbl,
               c.reltuples::bigint AS approx_rows,
               n.nspname AS schema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname = 'ods'
        ORDER BY c.reltuples DESC
        LIMIT 15
    """)).fetchall()
    print("ods 层行数最多的表：")
    for r in rows:
        print(f"  {r._mapping['schema']}.{r._mapping['tbl']:38s} ~{r._mapping['approx_rows']} 行")

    print()
    # 这些表里哪些已注册成数据集
    ds = c.execute(text("""
        SELECT d.name, d.source_table, d.title, d.column_comments
        FROM datasets d
        WHERE d.enabled = true
        ORDER BY d.id DESC
        LIMIT 20
    """)).fetchall()
    print("最近注册的数据集：")
    for r in ds:
        cm = r._mapping["column_comments"] or {}
        print(f"  {r._mapping['name']:28s} <- {r._mapping['source_table']:34s} 中文列名={len(cm)}")

    print()
    tot = c.execute(text("SELECT count(*) FROM datasets")).scalar()
    withcm = c.execute(text(
        "SELECT count(*) FROM datasets WHERE column_comments IS NOT NULL "
        "AND column_comments::text <> '{}'"
    )).scalar()
    print(f"数据集总数 {tot}，其中带中文列注释的 {withcm} 个")
e.dispose()
