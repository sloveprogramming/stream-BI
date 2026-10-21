"""把旺店通出库数据导入中台 PG（ods.wdt_* 两张表）。

## 为什么先做这个

岗位看板（运营/销售）需要真实业务数据。当前 PG 里除演示数据外
没有任何业务数据，看板会是空壳。

## 设计取舍

1. **幂等**：按主键 UPSERT，可重复跑，不用先清表。
2. **增量**：记录 `_synced_at`，配合 `updated_at` 水位线（与 ERP 同步同构）。
3. **只读源**：SQLite 以 `immutable=1` 打开，绝不写源文件。
4. **中文列注释**：导入时一并写进 `datasets.column_comments`，
   业务看板上直接显示「销售金额」而不是 `paid`。

## 用法

    python scripts/import_wdt.py            # 全量导入
    python scripts/import_wdt.py --limit 1000   # 先小批试跑
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import text

from app.db import make_engine, make_session_factory
from app.models import DEFAULT_TENANT_SLUG, Dataset, Tenant

SRC_DB = Path(r"D:\工作仓\BIsql\旺店通 - 副本\stockout_orders.db")

# ---------- 订单主表 ----------
ORDERS_DDL = """
DROP TABLE IF EXISTS ods.wdt_sales_order CASCADE;
CREATE TABLE ods.wdt_sales_order (
    order_no          text PRIMARY KEY,
    src_order_no      text,
    order_type_name   text,
    shop_name         text,
    warehouse_name    text,
    receiver_province text,
    receiver_city     text,
    logistics_name    text,
    salesman_name     text,
    trade_status      text,
    consign_status    text,
    refund_status     text,
    goods_count       integer,
    paid              numeric(14,2),
    discount          numeric(14,2),
    goods_total_cost  numeric(14,2),
    post_amount       numeric(14,2),
    pay_time          timestamp,
    consign_time      timestamp,
    to_deliver_time   timestamp,
    created           timestamp,
    modified          timestamp,
    _synced_at        timestamp
);
"""

# ---------- 订单明细 ----------
DETAILS_DDL = """
DROP TABLE IF EXISTS ods.wdt_sales_order_detail CASCADE;
CREATE TABLE ods.wdt_sales_order_detail (
    id            bigint PRIMARY KEY,
    order_no      text NOT NULL,
    spec_no       text,
    spec_name     text,
    goods_name    text,
    brand_name    text,
    goods_count   integer,
    sell_price    numeric(14,2),
    paid          numeric(14,2),
    discount      numeric(14,2),
    cost_price    numeric(14,2),
    refund_status text,
    created       timestamp,
    modified      timestamp,
    _synced_at    timestamp
);
CREATE INDEX IF NOT EXISTS ix_wdt_detail_order ON ods.wdt_sales_order_detail(order_no);
"""

ORDERS_COLS = [
    "order_no", "src_order_no", "order_type_name", "shop_name", "warehouse_name",
    "receiver_province", "receiver_city", "logistics_name", "salesman_name",
    "trade_status", "consign_status", "refund_status", "goods_count",
    "paid", "discount", "goods_total_cost", "post_amount",
    "pay_time", "consign_time", "to_deliver_time", "created", "modified",
]
DETAIL_COLS = [
    "id", "order_no", "spec_no", "spec_name", "goods_name", "brand_name",
    "goods_count", "sell_price", "paid", "discount", "cost_price",
    "refund_status", "created", "modified",
]

ORDER_COMMENTS = {
    "order_no": "订单号", "src_order_no": "原始交易号", "order_type_name": "订单类型",
    "shop_name": "店铺", "warehouse_name": "发货仓库", "receiver_province": "收货省份",
    "receiver_city": "收货城市", "logistics_name": "物流公司", "salesman_name": "业务员",
    "trade_status": "交易状态", "consign_status": "发货状态", "refund_status": "退款状态",
    "goods_count": "商品件数", "paid": "实付金额", "discount": "优惠金额",
    "goods_total_cost": "商品总成本", "post_amount": "邮费",
    "pay_time": "付款时间", "consign_time": "发货时间", "to_deliver_time": "签收时间",
    "created": "创建时间", "modified": "更新时间",
}
DETAIL_COMMENTS = {
    "id": "明细ID", "order_no": "订单号", "spec_no": "规格编码", "spec_name": "规格名称",
    "goods_name": "商品名称", "brand_name": "品牌", "goods_count": "商品数量",
    "sell_price": "售价", "paid": "实付金额", "discount": "优惠金额",
    "cost_price": "成本单价", "refund_status": "退款状态",
    "created": "创建时间", "modified": "更新时间",
}


def _num(v) -> float | None:
    """SQLite 里这些列是 text/real 混合，统一转 numeric。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _ts(v) -> str | None:
    """旺店通时间是 'YYYY-MM-DD HH:MM:SS'；非法值返回 None（不入库）。

    要挡的不只是空串和 '0000-00-00' —— 真实数据里还有中文占位符
    （如 `to_deliver_time = '送货时间不限'`）。所以**先校验格式**，
    只接受真能解析成日期的值，其它一律 None。
    """
    if not v or not isinstance(v, str):
        return None
    s = v.strip()[:19]
    if s.startswith("0000-00-00"):
        return None
    # 必须是 YYYY-MM-DD[ HH:MM:SS] 且能真解析
    try:
        datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return s


def fetch_rows(cur, table: str, cols: list[str], limit: int | None):
    """按需取列；分批避免一次性把 20 万行读进内存。"""
    sel = ", ".join(f'"{c}"' for c in cols)
    sql = f"SELECT {sel} FROM {table}"
    if limit:
        sql += f" LIMIT {limit}"
    cur.execute(sql)
    while True:
        batch = cur.fetchmany(2000)
        if not batch:
            return
        yield batch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只导入前 N 行（试跑用）")
    args = ap.parse_args()

    if not SRC_DB.exists():
        print(f"源库不存在: {SRC_DB}")
        return 1

    # 只读打开，绝不写源文件
    import sqlite3

    src = sqlite3.connect(f"file:{SRC_DB}?immutable=1", uri=True)
    cur = src.cursor()

    pg = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
    sf = make_session_factory(pg)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with pg.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(text(ORDERS_DDL))
        conn.execute(text(DETAILS_DDL))
    print("建表完成（DROP + CREATE，幂等）")

    # ---- 订单主表 ----
    t0 = time.perf_counter()
    n_orders = 0
    with pg.begin() as conn:
        for batch in fetch_rows(cur, "stockout_orders", ORDERS_COLS, args.limit):
            params = []
            for r in batch:
                m = dict(zip(ORDERS_COLS, r))
                params.append({
                    "order_no": m["order_no"],
                    "src_order_no": m.get("src_order_no"),
                    "order_type_name": m.get("order_type_name"),
                    "shop_name": m.get("shop_name") or "未知店铺",
                    "warehouse_name": m.get("warehouse_name") or "未知仓库",
                    "receiver_province": m.get("receiver_province") or "未知省份",
                    "receiver_city": m.get("receiver_city") or "未知城市",
                    "logistics_name": m.get("logistics_name") or "未知物流",
                    "salesman_name": m.get("salesman_name") or "未分配",
                    "trade_status": m.get("trade_status"),
                    "consign_status": m.get("consign_status"),
                    "refund_status": m.get("refund_status"),
                    "goods_count": _int(m.get("goods_count")),
                    "paid": _num(m.get("paid")),
                    "discount": _num(m.get("discount")),
                    "goods_total_cost": _num(m.get("goods_total_cost")),
                    "post_amount": _num(m.get("post_amount")),
                    "pay_time": _ts(m.get("pay_time")),
                    "consign_time": _ts(m.get("consign_time")),
                    "to_deliver_time": _ts(m.get("to_deliver_time")),
                    "created": _ts(m.get("created")),
                    "modified": _ts(m.get("modified")),
                    "_synced_at": now,
                })
            conn.execute(text(f"""
                INSERT INTO ods.wdt_sales_order
                  ({", ".join(ORDERS_COLS + ['_synced_at'])})
                VALUES ({", ".join(f":{c}" for c in ORDERS_COLS + ['_synced_at'])})
                ON CONFLICT (order_no) DO UPDATE SET
                  src_order_no = EXCLUDED.src_order_no,
                  order_type_name = EXCLUDED.order_type_name,
                  shop_name = EXCLUDED.shop_name,
                  warehouse_name = EXCLUDED.warehouse_name,
                  receiver_province = EXCLUDED.receiver_province,
                  receiver_city = EXCLUDED.receiver_city,
                  logistics_name = EXCLUDED.logistics_name,
                  salesman_name = EXCLUDED.salesman_name,
                  trade_status = EXCLUDED.trade_status,
                  consign_status = EXCLUDED.consign_status,
                  refund_status = EXCLUDED.refund_status,
                  goods_count = EXCLUDED.goods_count,
                  paid = EXCLUDED.paid,
                  discount = EXCLUDED.discount,
                  goods_total_cost = EXCLUDED.goods_total_cost,
                  post_amount = EXCLUDED.post_amount,
                  pay_time = EXCLUDED.pay_time,
                  consign_time = EXCLUDED.consign_time,
                  to_deliver_time = EXCLUDED.to_deliver_time,
                  created = EXCLUDED.created,
                  modified = EXCLUDED.modified,
                  _synced_at = EXCLUDED._synced_at
            """), params)
            n_orders += len(params)
    print(f"订单主表: {n_orders} 行  {time.perf_counter() - t0:.1f}s")

    # ---- 订单明细 ----
    t0 = time.perf_counter()
    n_detail = 0
    with pg.begin() as conn:
        for batch in fetch_rows(cur, "stockout_order_details", DETAIL_COLS, args.limit):
            params = []
            for r in batch:
                m = dict(zip(DETAIL_COLS, r))
                params.append({
                    "id": _int(m.get("id")),
                    "order_no": m["order_no"],
                    "spec_no": m.get("spec_no"),
                    "spec_name": m.get("spec_name") or m.get("goods_name"),
                    "goods_name": m.get("goods_name"),
                    "brand_name": m.get("brand_name") or "未知品牌",
                    "goods_count": _int(m.get("goods_count")),
                    "sell_price": _num(m.get("sell_price")),
                    "paid": _num(m.get("paid")),
                    "discount": _num(m.get("discount")),
                    "cost_price": _num(m.get("cost_price")),
                    "refund_status": m.get("refund_status"),
                    "created": _ts(m.get("created")),
                    "modified": _ts(m.get("modified")),
                    "_synced_at": now,
                })
            conn.execute(text(f"""
                INSERT INTO ods.wdt_sales_order_detail
                  ({", ".join(DETAIL_COLS + ['_synced_at'])})
                VALUES ({", ".join(f":{c}" for c in DETAIL_COLS + ['_synced_at'])})
                ON CONFLICT (id) DO UPDATE SET
                  order_no = EXCLUDED.order_no,
                  spec_no = EXCLUDED.spec_no,
                  spec_name = EXCLUDED.spec_name,
                  goods_name = EXCLUDED.goods_name,
                  brand_name = EXCLUDED.brand_name,
                  goods_count = EXCLUDED.goods_count,
                  sell_price = EXCLUDED.sell_price,
                  paid = EXCLUDED.paid,
                  discount = EXCLUDED.discount,
                  cost_price = EXCLUDED.cost_price,
                  refund_status = EXCLUDED.refund_status,
                  created = EXCLUDED.created,
                  modified = EXCLUDED.modified,
                  _synced_at = EXCLUDED._synced_at
            """), params)
            n_detail += len(params)
    print(f"订单明细: {n_detail} 行  {time.perf_counter() - t0:.1f}s")
    src.close()

    # ---- 注册为数据集（带中文列注释，default 租户） ----
    with sf() as s:
        tenant = s.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
        tid = tenant.id if tenant else None
        specs = [
            ("wdt_sales_order", "销售订单", "ods.wdt_sales_order",
             ["order_no"], ORDERS_COLS, ORDER_COMMENTS,
             "旺店通出库订单：销售运营的核心事实表"),
            ("wdt_sales_order_detail", "销售订单明细", "ods.wdt_sales_order_detail",
             ["id"], DETAIL_COLS, DETAIL_COMMENTS,
             "旺店通出库订单明细：商品级销售与成本"),
        ]
        for name, title, table, pk, cols, comments, desc in specs:
            ds = s.query(Dataset).filter_by(name=name).first()
            if ds is None:
                ds = Dataset(name=name)
                s.add(ds)
            ds.title = title
            ds.source_table = table
            ds.pk_columns = pk
            ds.columns = cols
            ds.column_comments = comments
            ds.description = desc
            ds.tenant_id = tid
            ds.enabled = True
        s.commit()
        print("\n已注册数据集: wdt_sales_order / wdt_sales_order_detail")

    # ---- 校验 ----
    with pg.connect() as c:
        for t in ("wdt_sales_order", "wdt_sales_order_detail"):
            n = c.execute(text(f"SELECT count(*) FROM ods.{t}")).scalar()
            print(f"  校验 ods.{t}: {n} 行")
        rows = c.execute(text("""
            SELECT shop_name, count(*) AS orders, sum(paid)::numeric(16,2) AS gmv
            FROM ods.wdt_sales_order
            WHERE refund_status <> '2'
            GROUP BY shop_name ORDER BY gmv DESC LIMIT 8
        """)).fetchall()
        print("\n  分店铺 GMV（前 8，供看板对照）：")
        for r in rows:
            print(f"    {r[0]:28s} {r[1]:>7} 单  ¥{r[2]:>12,.2f}")
    pg.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
