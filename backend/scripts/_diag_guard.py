"""诊断：为什么 demo_sales 明明在 schema_context 里，问数却报「无权限的表」。

对比两处的表名集合：
- schema_context（喂给模型的）用数据集 name
- validate_sql（安全闸门）用 source_table

如果两者不一致，就会出现"模型看到的表名 ≠ 闸门认可的表名"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.assistant.schema_context import build_schema_context
from app.assistant.sql_guard import validate_sql
from app.db import make_engine, make_session_factory
from app.models import User

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
sf = make_session_factory(e)

with sf() as s:
    u = s.query(User).filter_by(username="admin").first()
    sc = build_schema_context(sf, e, u, u.tenant_id)
    tables = sc["tables"]
    print("schema_context 表数:", len(tables))
    print()
    print("demo_* 在 schema_context 里：")
    for t in tables:
        if t["name"].startswith("demo_"):
            print("   name =", t["name"], " source_table =", t["source_table"])
    print()
    # 闸门认哪些名字？看 demo_sales 的两种写法
    for sql in (
        "SELECT region, SUM(amount) FROM demo_sales GROUP BY region",
        "SELECT region, SUM(amount) FROM demo.sales_order GROUP BY region",
    ):
        try:
            validate_sql(sql, tables)
            print(f"  通过  : {sql[:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  拒绝  : {sql[:60]}\n          -> {exc}")
    print()
    # prompt 到底多长
    print("prompt 长度:", len(sc["prompt"]), "字符")
    print("prompt 前 300 字:")
    print(sc["prompt"][:300])
    print("...")
    print("prompt 里是否含 demo_sales:", "demo_sales" in sc["prompt"])
e.dispose()
