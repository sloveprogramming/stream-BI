"""盘点 577 个数据集的来历，为"清理还是隔离"提供依据。

按名字前缀分类，并统计每个来源的行数与物理表是否存在。
只读，不删任何东西。
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db import make_engine

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")

with e.connect() as c:
    rows = c.execute(text(
        "SELECT d.id, d.name, d.source_table, d.title, d.enabled, d.tenant_id, "
        "(SELECT count(*) FROM information_schema.tables t "
        " WHERE t.table_schema||'.'||t.table_name = d.source_table) AS tbl_exists, "
        "(SELECT count(*) FROM dataset_grants g WHERE g.dataset_id = d.id) AS grants "
        "FROM datasets d ORDER BY d.id"
    )).fetchall()
    print("数据集总数:", len(rows))

    # 按前缀归类
    def bucket(name: str) -> str:
        for p in ("mcpd_", "repd_", "asst_", "cc_", "t_", "direct_", "export_",
                  "sales_order_", "au_", "gl_", "demo_", "flow_", "e2e_",
                  "inventory", "purchase", "sc_", "tt_", "xt_", "rg_"):
            if name.startswith(p):
                return p + "*"
        return "(其他)"

    cnt = Counter(bucket(r._mapping["name"]) for r in rows)
    print("\n按名字前缀分类：")
    for k, v in cnt.most_common():
        print(f"  {k:18s} {v:4d} 个")

    # 物理表不存在的（孤儿数据集）
    missing = [r._mapping for r in rows if not r._mapping["tbl_exists"]]
    print(f"\n物理表已不存在（孤儿）: {len(missing)} 个")
    for m in missing[:10]:
        print(f"  {m['name']:26s} -> {m['source_table']}")

    # 有授权的（说明有人在用）
    withgrant = [r._mapping for r in rows if r._mapping["grants"]]
    print(f"\n有授权记录的: {len(withgrant)} 个")

    # 表里真的有数据的
    print("\n有数据的表（行数 > 0）：")
    real = []
    for r in rows:
        m = r._mapping
        if not m["tbl_exists"]:
            continue
        try:
            n = c.execute(text(f'SELECT count(*) FROM {m["source_table"]}')).scalar()
        except Exception:
            continue
        if n and n > 0:
            real.append((m["name"], m["source_table"], n))
    real.sort(key=lambda x: -x[2])
    for name, tbl, n in real[:25]:
        print(f"  {name:26s} {tbl:34s} {n:>8d} 行")
    print(f"  ... 共 {len(real)} 个非空数据集")
e.dispose()
