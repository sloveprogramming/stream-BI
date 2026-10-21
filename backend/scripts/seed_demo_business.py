"""造一份「像真实业务」的测试数据，供 Agent / 问数验证使用。

为什么不用现有数据集：库里 575 个数据集全是历次测试的残留
（每个 3 行、无中文列注释），拿它们验证 Agent 只能证明"链路通"，
证明不了"答得对"。

这份数据刻意做成有业务形状的样子：
- 三张表：销售订单、库存快照、采购在途
- 中文列注释（让问数能理解字段含义）
- 几百到几千行，足够验证聚合/分组/多表

归属 default 租户，admin 直接可见。全部落在本地 PG，不碰 ERP。
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db import make_engine, make_session_factory
from app.models import DEFAULT_TENANT_SLUG, Dataset, Tenant

PG = "postgresql+psycopg://bisql:bisql@localhost:55432/bisql"

REGIONS = ["华东", "华北", "华南", "西南"]
WAREHOUSES = ["德清仓", "临安仓", "广州仓", "成都仓"]
CHANNELS = ["天猫", "京东", "抖音", "线下"]
CATEGORIES = ["坚果炒货", "果干蜜饯", "糕点烘焙", "肉类零食"]
SKUS = [
    ("A41100049", "开心果盐焗味 225g", "坚果炒货"),
    ("A41100050", "碧根果奶油味 200g", "坚果炒货"),
    ("A41100051", "巴旦木原味 180g", "坚果炒货"),
    ("B52200011", "芒果干 100g", "果干蜜饯"),
    ("B52200012", "草莓干 80g", "果干蜜饯"),
    ("C63300021", "蛋黄酥 6 枚装", "糕点烘焙"),
    ("C63300022", "桃酥 400g", "糕点烘焙"),
    ("D74400031", "猪肉脯 200g", "肉类零食"),
    ("D74400032", "牛肉粒 150g", "肉类零食"),
]

DDL = """
DROP TABLE IF EXISTS demo.sales_order;
DROP TABLE IF EXISTS demo.inventory_snapshot;
DROP TABLE IF EXISTS demo.purchase_intransit;
CREATE SCHEMA IF NOT EXISTS demo;

CREATE TABLE demo.sales_order (
    order_id      integer PRIMARY KEY,
    order_date    date        NOT NULL,
    region        text        NOT NULL,
    warehouse     text        NOT NULL,
    channel       text        NOT NULL,
    sku           text        NOT NULL,
    sku_name      text        NOT NULL,
    category      text        NOT NULL,
    qty           integer     NOT NULL,
    amount        numeric(12,2) NOT NULL,
    cost          numeric(12,2) NOT NULL,
    update_time   timestamp   NOT NULL
);

CREATE TABLE demo.inventory_snapshot (
    id            serial PRIMARY KEY,
    snap_date     date        NOT NULL,
    warehouse     text        NOT NULL,
    sku           text        NOT NULL,
    sku_name      text        NOT NULL,
    stock_qty     integer     NOT NULL,
    update_time   timestamp   NOT NULL
);

CREATE TABLE demo.purchase_intransit (
    id            serial PRIMARY KEY,
    po_no         text        NOT NULL,
    sku           text        NOT NULL,
    sku_name      text        NOT NULL,
    qty           integer     NOT NULL,
    supplier      text        NOT NULL,
    expect_date   date        NOT NULL,
    status        text        NOT NULL
);
"""

# 中文列注释：让问数知道 amount 是"销售金额"而不是猜
COMMENTS = {
    "sales_order": {
        "order_id": "订单号", "order_date": "订单日期", "region": "销售大区",
        "warehouse": "发货仓库", "channel": "销售渠道", "sku": "商品编码",
        "sku_name": "商品名称", "category": "商品类目", "qty": "销售数量",
        "amount": "销售金额", "cost": "销售成本", "update_time": "更新时间",
    },
    "inventory_snapshot": {
        "snap_date": "快照日期", "warehouse": "仓库", "sku": "商品编码",
        "sku_name": "商品名称", "stock_qty": "库存数量", "update_time": "更新时间",
    },
    "purchase_intransit": {
        "po_no": "采购单号", "sku": "商品编码", "sku_name": "商品名称",
        "qty": "在途数量", "supplier": "供应商", "expect_date": "预计到货日期",
        "status": "采购状态",
    },
}


def main() -> int:
    engine = make_engine(PG)
    rnd = random.Random(20260713)

    with engine.begin() as conn:
        conn.execute(text(DDL))

        # ---- 销售订单：90 天 × 4 区 × 4 仓 × 4 渠道，约 2000 行 ----
        today = date(2026, 7, 13)
        rows = []
        oid = 100000
        for d in range(90):
            day = today - timedelta(days=89 - d)
            for _ in range(rnd.randint(18, 26)):
                sku, sname, cat = rnd.choice(SKUS)
                qty = rnd.randint(1, 40)
                price = rnd.uniform(18, 89)
                amount = round(qty * price, 2)
                cost = round(amount * rnd.uniform(0.45, 0.78), 2)
                rows.append({
                    "order_id": oid, "order_date": day,
                    "region": rnd.choice(REGIONS), "warehouse": rnd.choice(WAREHOUSES),
                    "channel": rnd.choice(CHANNELS), "sku": sku, "sku_name": sname,
                    "category": cat, "qty": qty, "amount": amount, "cost": cost,
                    "update_time": f"{day} 18:00:00",
                })
                oid += 1
        conn.execute(text("""
            INSERT INTO demo.sales_order
              (order_id,order_date,region,warehouse,channel,sku,sku_name,category,
               qty,amount,cost,update_time)
            VALUES (:order_id,:order_date,:region,:warehouse,:channel,:sku,:sku_name,
                    :category,:qty,:amount,:cost,:update_time)
        """), rows)
        print(f"sales_order       : {len(rows)} 行")

        # ---- 库存快照：最新一天，每仓每 SKU 一行 ----
        inv = []
        for wh in WAREHOUSES:
            for sku, sname, _ in SKUS:
                inv.append({
                    "snap_date": today, "warehouse": wh, "sku": sku,
                    "sku_name": sname, "stock_qty": rnd.randint(0, 900),
                    "update_time": f"{today} 07:00:00",
                })
        conn.execute(text("""
            INSERT INTO demo.inventory_snapshot
              (snap_date,warehouse,sku,sku_name,stock_qty,update_time)
            VALUES (:snap_date,:warehouse,:sku,:sku_name,:stock_qty,:update_time)
        """), inv)
        print(f"inventory_snapshot: {len(inv)} 行")

        # ---- 采购在途 ----
        po = []
        for i in range(40):
            sku, sname, _ = rnd.choice(SKUS)
            po.append({
                "po_no": f"PO2026{i:04d}", "sku": sku, "sku_name": sname,
                "qty": rnd.randint(50, 800), "supplier": rnd.choice(
                    ["临安山货", "广州鲜果", "成都食品", "上海贸易"]),
                "expect_date": today + timedelta(days=rnd.randint(1, 30)),
                "status": rnd.choice(["在途", "在途", "已发货", "待发货"]),
            })
        conn.execute(text("""
            INSERT INTO demo.purchase_intransit
              (po_no,sku,sku_name,qty,supplier,expect_date,status)
            VALUES (:po_no,:sku,:sku_name,:qty,:supplier,:expect_date,:status)
        """), po)
        print(f"purchase_intransit: {len(po)} 行")

    # ---- 注册为数据集（default 租户，admin 可见）----
    sf = make_session_factory(engine)
    with sf() as s:
        tenant = s.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
        tid = tenant.id if tenant else None

        specs = [
            ("demo_sales", "销售订单明细", "demo.sales_order",
             ["order_id"], ["order_id", "order_date", "region", "warehouse",
                            "channel", "sku", "sku_name", "category",
                            "qty", "amount", "cost", "update_time"]),
            ("demo_inventory", "库存快照", "demo.inventory_snapshot",
             ["id"], ["id", "snap_date", "warehouse", "sku", "sku_name",
                      "stock_qty", "update_time"]),
            ("demo_purchase", "采购在途", "demo.purchase_intransit",
             ["id"], ["id", "po_no", "sku", "sku_name", "qty", "supplier",
                      "expect_date", "status"]),
        ]
        for name, title, table, pk, cols in specs:
            ds = s.query(Dataset).filter_by(name=name).first()
            if ds is None:
                ds = Dataset(name=name)
                s.add(ds)
            ds.title = title
            ds.source_table = table
            ds.pk_columns = pk
            ds.columns = cols
            ds.description = f"演示数据：{title}"
            ds.column_comments = COMMENTS[table.split(".")[-1]]
            ds.tenant_id = tid
            ds.enabled = True
        s.commit()
        print("\n已注册数据集：demo_sales / demo_inventory / demo_purchase")
        print("（均归属 default 租户，带中文列注释）")

    # 校验
    with engine.connect() as c:
        for t in ("sales_order", "inventory_snapshot", "purchase_intransit"):
            n = c.execute(text(f"SELECT count(*) FROM demo.{t}")).scalar()
            print(f"  校验 demo.{t}: {n} 行")
        r = c.execute(text(
            "SELECT region, sum(amount)::numeric(14,2) FROM demo.sales_order "
            "GROUP BY region ORDER BY 2 DESC"
        )).fetchall()
        print("\n  分区域销售额（供答案对照）：")
        for row in r:
            print(f"    {row[0]}: {row[1]}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
