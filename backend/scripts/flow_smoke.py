"""数据编排端到端冒烟：真 dbt 跑一条完整流程。

用途：改了编译器 / 执行器 / dbt 配置之后，一条命令确认整条链路还能跑通
（pytest 用的是假 dbt，不覆盖 dbt 本身的行为）。

    cd backend
    python scripts/flow_smoke.py            # 跑一次，保留产物
    python scripts/flow_smoke.py --cleanup  # 跑完清理干净

它会：
1. 造一张源表 `ods.e2e_src` 并注册为数据集；
2. 给 admin 用户只授「德清仓」行级权限；
3. 建一条流程 去重 → 过滤 → 物化到 `dwd.e2e_sales_clean`；
4. 用真 dbt 执行，打印运行状态、行数、产物列，并把物化结果查出来。

**重点看两件事**：
- 物化结果里不该出现非德清仓的行（行级规则继承生效）；
- dbt 日志里表名必须是 `dwd.e2e_sales_clean`，不是 `dwd_dwd.*`
  （后者说明 `macros/generate_schema_name.sql` 没生效）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from app.db import make_engine, make_session_factory  # noqa: E402
from app.flow import service  # noqa: E402
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput  # noqa: E402
from app.flow.runner import run_flow  # noqa: E402
from app.models import (  # noqa: E402
    DataFlow,
    DataFlowVersion,
    Dataset,
    DatasetGrant,
    FlowRun,
    User,
)
from app.settings import get_settings  # noqa: E402

SOURCE_TABLE = "ods.e2e_src"
DATASET_NAME = "e2e_src"
FLOW_NAME = "e2e_sales_clean"
TARGET = "dwd.e2e_sales_clean"
WAREHOUSE = "德清"


def build_dsl(dataset_id: int) -> FlowDSL:
    return FlowDSL(
        name=FLOW_NAME,
        title="端到端冒烟",
        description="去重 → 过滤 → 物化",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": dataset_id}),
            FlowNode(
                id="dedup",
                type="clean.dedup",
                params={"keys": ["order_id"], "keep": "last", "order_by": "amount"},
            ),
            FlowNode(id="filt", type="clean.filter", params={"expr": "amount > 15"}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[
            FlowEdge(source="src", target="dedup"),
            FlowEdge(source="dedup", target="filt"),
            FlowEdge(source="filt", target="out"),
        ],
        output=FlowOutput(
            target_name=FLOW_NAME, target_schema="dwd", pk_columns=["order_id"]
        ),
    )


def prepare(engine, sf) -> tuple[int, int]:
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {SOURCE_TABLE}"))
        conn.execute(
            sa.text(
                f"CREATE TABLE {SOURCE_TABLE} "
                "(order_id int primary key, warehouse text, amount numeric)"
            )
        )
        conn.execute(
            sa.text(
                f"INSERT INTO {SOURCE_TABLE} VALUES "
                f"(1,'{WAREHOUSE}',10),(2,'{WAREHOUSE}',20),(3,'安徽',30),(4,'{WAREHOUSE}',30)"
            )
        )

    with sf() as session:
        admin = session.query(User).filter_by(username="admin").first()
        if admin is None:
            raise SystemExit("本地库里没有 admin 用户，先跑 python -m app.bootstrap admin <密码>")

        dataset = session.query(Dataset).filter_by(name=DATASET_NAME).first()
        if dataset is None:
            dataset = Dataset(name=DATASET_NAME, enabled=True)
            session.add(dataset)
        dataset.source_table = SOURCE_TABLE
        dataset.pk_columns = ["order_id"]
        dataset.columns = ["order_id", "warehouse", "amount"]
        session.flush()

        session.query(DatasetGrant).filter_by(dataset_id=dataset.id).delete()
        # 只授德清仓：物化结果里不该出现安徽的行
        session.add(
            DatasetGrant(
                user_id=admin.id,
                dataset_id=dataset.id,
                row_rules=[{"field": "warehouse", "op": "eq", "value": WAREHOUSE}],
                field_whitelist=None,
            )
        )
        session.commit()
        return dataset.id, admin.id


def cleanup(engine, sf) -> None:
    with sf() as session:
        flow = session.query(DataFlow).filter_by(name=FLOW_NAME).first()
        if flow is not None:
            session.query(FlowRun).filter(FlowRun.flow_id == flow.id).delete()
            session.query(DataFlowVersion).filter(DataFlowVersion.flow_id == flow.id).delete()
            session.delete(flow)
        dataset = session.query(Dataset).filter_by(name=DATASET_NAME).first()
        if dataset is not None:
            session.query(DatasetGrant).filter_by(dataset_id=dataset.id).delete()
            session.delete(dataset)
        session.commit()
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {TARGET}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {SOURCE_TABLE}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="跑完清理源表与产物")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)

    dataset_id, admin_id = prepare(engine, sf)

    with sf() as session:
        admin = session.get(User, admin_id)
        stale = session.query(DataFlow).filter_by(name=FLOW_NAME).first()
        if stale is not None:
            session.query(FlowRun).filter(FlowRun.flow_id == stale.id).delete()
            session.query(DataFlowVersion).filter(DataFlowVersion.flow_id == stale.id).delete()
            session.delete(stale)
            session.commit()

        flow = service.create_flow(session, dsl=build_dsl(dataset_id), owner=admin)
        run = run_flow(
            session,
            flow,
            pg_engine=engine,
            settings=settings,
            triggered_by="manual",
            trigger_user_id=admin_id,
        )
        print(f"status        : {run.status}")
        print(f"rows          : {run.rows_out}")
        print(f"error         : {run.error}")
        print(f"grant_snapshot: {run.grant_snapshot}")
        print(f"dataset_id    : {flow.output_dataset_id}")
        print(f"warnings      : {run.meta.get('warnings')}")

    if run.status == "ok":
        schema, table = TARGET.split(".")
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(f'SELECT * FROM "{schema}"."{table}" ORDER BY order_id')
            ).fetchall()
        print(f"materialized  : {rows}")
        leaked = [r for r in rows if WAREHOUSE not in str(r)]
        if leaked:
            print(f"!! 行级权限泄漏：{leaked}")
            return 2

    if args.cleanup:
        cleanup(engine, sf)
        print("cleaned up")

    engine.dispose()
    return 0 if run.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
