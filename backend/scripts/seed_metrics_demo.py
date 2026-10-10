"""给指标字典准备演示数据集。

指标定义（`dbt/models/marts/metrics.yml`）锚定的是**业务数据集**，
而本项目的数据集是**运行时上架**的（同步任务 / 自动发现）。所以空库启动时，
3 个示例指标会被标记为「暂不可用」—— 这不阻塞任何东西，但也演示不了。

这个脚本把锚点补齐：

    建 ods 表 → 造几行数据 → 注册为数据集 → 授权给 admin

    cd backend
    python scripts/seed_metrics_demo.py            # 准备（幂等，可重复跑）
    python scripts/seed_metrics_demo.py --cleanup  # 清干净

它同时也是 `scripts/flow_smoke.py` 的前置：冒烟里那条"引用指标"的流程
要读 `sales_order`。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from app.db import make_engine, make_session_factory  # noqa: E402
from app.models import Dataset, DatasetGrant, User  # noqa: E402
from app.settings import get_settings  # noqa: E402

# (数据集名, 物理表, 列, 主键, 建表列定义, 数据)
SPECS = [
    (
        "sales_order",
        "ods.sales_order",
        ["order_id", "warehouse", "amount", "cost"],
        ["order_id"],
        "order_id int primary key, warehouse text, amount numeric, cost numeric",
        # 毛利率依次为 40% / 25% / 10%
        [
            (1, "德清", 100, 60),
            (2, "安徽", 200, 150),
            (3, "德清", 50, 45),
        ],
    ),
    (
        "inventory_snapshot",
        "ods.inventory_snapshot",
        ["sku", "warehouse", "stock_qty", "sold_qty"],
        ["sku", "warehouse"],
        "sku text, warehouse text, stock_qty int, sold_qty int, "
        "primary key (sku, warehouse)",
        # 动销率依次为 20% / 50%
        [
            ("A001", "德清", 80, 20),
            ("A002", "德清", 50, 50),
        ],
    ),
    (
        "purchase_order",
        "ods.purchase_order",
        ["po_id", "supplier", "total_qty", "on_time_qty"],
        ["po_id"],
        "po_id int primary key, supplier text, total_qty int, on_time_qty int",
        # 到货及时率依次为 90% / 100%
        [
            (1, "供应商甲", 100, 90),
            (2, "供应商乙", 50, 50),
        ],
    ),
]


def prepare(engine, session_factory) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        for _name, table, _columns, _pk, ddl, values in SPECS:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(sa.text(f"CREATE TABLE {table} ({ddl})"))
            holders = ", ".join(":" + f"c{i}" for i in range(len(values[0])))
            payload = [{f"c{i}": v for i, v in enumerate(row)} for row in values]
            conn.execute(sa.text(f"INSERT INTO {table} VALUES ({holders})"), payload)

    with session_factory() as session:
        admin = session.query(User).filter_by(username="admin").first()
        if admin is None:
            raise SystemExit(
                "本地库里没有 admin 用户，先跑 python -m app.bootstrap admin <密码>"
            )
        for name, table, columns, pk, _ddl, _values in SPECS:
            ds = session.query(Dataset).filter_by(name=name).first()
            if ds is None:
                ds = Dataset(name=name, enabled=True)
                session.add(ds)
            ds.title = name
            ds.description = "指标字典演示数据集（scripts/seed_metrics_demo.py）"
            ds.source_table = table
            ds.columns = list(columns)
            ds.pk_columns = list(pk)
            ds.enabled = True
            session.flush()

            session.query(DatasetGrant).filter_by(dataset_id=ds.id).delete()
            session.add(
                DatasetGrant(
                    user_id=admin.id,
                    dataset_id=ds.id,
                    row_rules=[],
                    field_whitelist=None,
                )
            )
        session.commit()


def cleanup(engine, session_factory) -> None:
    with session_factory() as session:
        for name, *_rest in SPECS:
            ds = session.query(Dataset).filter_by(name=name).first()
            if ds is not None:
                session.query(DatasetGrant).filter_by(dataset_id=ds.id).delete()
                session.delete(ds)
        session.commit()
    with engine.begin() as conn:
        for _name, table, *_rest in SPECS:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="指标字典演示数据集")
    parser.add_argument("--cleanup", action="store_true", help="删除数据集与 ods 表")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        if args.cleanup:
            cleanup(engine, session_factory)
            print(f"已清理 {len(SPECS)} 个演示数据集")
            return 0
        prepare(engine, session_factory)
        for name, table, columns, _pk, _ddl, _values in SPECS:
            print(f"  {name:<20} <- {table}  ({', '.join(columns)})")

        # 立刻验证一遍：指标字典现在应该没有「未上架」告警了
        from app.metrics import load_for

        with session_factory() as session:
            dictionary = load_for(session)
        print(f"\n指标字典：{len(dictionary)} 个（{dictionary.path}）")
        for metric in dictionary.metrics:
            mark = "可用" if metric.name not in dictionary.unavailable else "未上架"
            print(f"  [{mark}] {metric.label:<8} {metric.name:<24} {metric.expr}")
        for note in dictionary.warnings:
            print(f"  ⚠ {note}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
