"""归档历次测试留下的数据集（用已有的 enabled 字段，不删数据）。

## 为什么用 enabled 而不是删除

`Dataset.enabled=False` 已经存在，并且：
- `query_visible_datasets` 已经过滤掉它 → 列表、问数、MCP、导出全部自动不可见
- 数据行还在 → 审计、授权记录不受影响，随时可恢复
- 不需要迁移 → 不动表结构

这正是"归档"的语义，没必要新增字段。

## 判定规则（两条命中其一即归档）

1. **名字匹配测试前缀**（mcpd_ / repd_ / asst_ / cc_ / …）；
2. **多个数据集指向同一张物理表**（重复注册）—— 这是最可靠的信号：
   测试反复建表注册，导致 `ods.mcp_t` 被注册了 186 次。

**刻意不看"有没有授权"**：测试残留的授权记录同样是垃圾
（524 个数据集各挂着 1 条测试授权），用它会漏掉全部目标。

先 dry-run 看清单，加 --apply 才真改。恢复只需把 enabled 改回 true。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db import make_engine

# 测试残留的前缀（来自审计脚本的实际统计）
TEST_PREFIXES = (
    "mcpd_", "repd_", "asst_", "cc_", "t_", "direct_", "export_",
    "sales_order_", "au_", "gl_", "rg_", "tt_", "xt_", "sc_", "del_",
    "m4_", "flow_src", "inventory_snapshot", "purchase_order",
)

# 明确保留的（真实业务 / 演示数据）
KEEP = ("demo_sales", "demo_inventory", "demo_purchase", "sales_order",
        "e2e_semantic_dedup")


def main() -> int:
    apply = "--apply" in sys.argv
    engine = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")

    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT d.id, d.name, d.source_table, d.enabled
            FROM datasets d ORDER BY d.id
        """)).fetchall()
        # 同一物理表被注册了几次 —— 重复注册是最可靠的"测试残留"信号
        dup = {
            r._mapping["source_table"]: r._mapping["n"]
            for r in c.execute(text("""
                SELECT source_table, count(*) AS n FROM datasets
                GROUP BY source_table HAVING count(*) > 1
            """)).fetchall()
        }

    targets, kept = [], []
    for r in rows:
        m = r._mapping
        name, table = m["name"], m["source_table"]
        if name in KEEP:
            kept.append((name, "真实/演示数据"))
            continue
        if not m["enabled"]:
            continue  # 已归档，不重复处理

        # 命中规则 1：测试前缀
        if any(name.startswith(p) for p in TEST_PREFIXES):
            targets.append((m["id"], name))
            continue
        # 命中规则 2：同一物理表被注册多次
        if dup.get(table, 0) > 1:
            targets.append((m["id"], name, f"与另外 {dup[table] - 1} 个数据集共用 {table}"))
            continue
        kept.append((name, "未被规则命中"))

    print(f"数据集总数      : {len(rows)}")
    print(f"将归档          : {len(targets)}")
    print(f"保留            : {len(kept)}")
    print()
    print("保留清单：")
    for item in kept:
        name, why = item[0], item[1]
        print(f"  {name:28s} {why}")
    print()
    print("将归档（前 20 个示例）：")
    for t in targets[:20]:
        extra = f"   ← {t[2]}" if len(t) > 2 else ""
        print(f"  {t[1]}{extra}")
    if len(targets) > 20:
        print(f"  ... 另外 {len(targets) - 20} 个")

    if not apply:
        print()
        print("这是 dry-run。要真正归档请加 --apply")
        engine.dispose()
        return 0

    if targets:
        ids = [t[0] for t in targets]
        with engine.begin() as c:
            c.execute(
                text("UPDATE datasets SET enabled = false WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        print()
        print(f"已归档 {len(ids)} 个数据集")

    with engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM datasets WHERE enabled")).scalar()
        print(f"当前启用中的数据集: {left}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
