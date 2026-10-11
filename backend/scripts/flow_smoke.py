"""数据编排端到端冒烟：真 dbt 跑两条完整流程。

用途：改了编译器 / 执行器 / dbt 配置 / 指标字典之后，一条命令确认整条链路还能跑通
（pytest 用的是假 dbt，不覆盖 dbt 本身的行为）。

    cd backend
    python scripts/flow_smoke.py            # 跑一次，保留产物
    python scripts/flow_smoke.py --cleanup  # 跑完清理

跑两条流程：

1. **权限链** 造源表 `ods.e2e_src` 并注册为数据集，给 admin 只授「德清仓」行级权限，
   建流程 去重 → 过滤 → 物化到 `dwd.e2e_sales_clean`；
2. **口径链** 用指标字典锚定的 `sales_order`，建流程
   `source → 派生字段(metric=gross_margin) → 物化`，
   确认编译产物里出现的是**指标公式**、且算出来的毛利率与源数据吻合。

**重点看四件事**：
- 第一条流程的物化结果里不该出现非德清仓的行（行级规则继承生效）；
- dbt 日志里表名必须是 `dwd.e2e_sales_clean`，不是 `dwd_dwd.*`
  （后者说明 `macros/generate_schema_name.sql` 没生效）；
- 第二条流程生成的 SQL 里必须有 `(amount - cost) / nullif(amount, 0)`
  （说明口径来自字典，而不是手写）；
- 毛利率算出来是 40% / 25% / 10%。

`--cleanup` 只清冒烟自己造的东西。指标字典锚定的演示数据集（`sales_order` 等）
由 `scripts/seed_metrics_demo.py` 管理，要清它请跑
`python scripts/seed_metrics_demo.py --cleanup`。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # backend/
sys.path.insert(0, _HERE)  # scripts/ —— 复用指标演示数据集的准备逻辑

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
from seed_metrics_demo import prepare as prepare_demo_datasets  # noqa: E402

SOURCE_TABLE = "ods.e2e_src"
DATASET_NAME = "e2e_src"
FLOW_NAME = "e2e_sales_clean"
TARGET = "dwd.e2e_sales_clean"
WAREHOUSE = "德清"

METRIC_FLOW = "e2e_gross_margin"
METRIC_TARGET = "dwd.e2e_gross_margin"
METRIC_DATASET = "sales_order"
# 源数据 (amount, cost) 依次为 (100,60) (200,150) (50,45)
EXPECTED_MARGIN = {1: 0.4, 2: 0.25, 3: 0.1}

QA_FLOW = "e2e_quality"
QA_TARGET = "dwd.e2e_quality"
# 权限链过滤后的德清行 amount 为 20 与 30：25 的上限让 30 必然违规
QA_AMOUNT_CAP = 25


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


def build_metric_dsl(dataset_id: int) -> FlowDSL:
    """派生字段不写公式，只填指标名 —— 口径由 `metrics.yml` 定义。"""
    return FlowDSL(
        name=METRIC_FLOW,
        title="引用指标口径",
        description="派生字段引用指标字典里的「毛利率」",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": dataset_id}),
            FlowNode(
                id="margin",
                type="field.derive",
                params={"name": "gross_margin", "metric": "gross_margin"},
            ),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[
            FlowEdge(source="src", target="margin"),
            FlowEdge(source="margin", target="out"),
        ],
        output=FlowOutput(
            target_name=METRIC_FLOW, target_schema="dwd", pk_columns=["order_id"]
        ),
    )


def build_qa_dsl(dataset_id: int) -> FlowDSL:
    """质量链：过滤后的德清订单做质量校验（一条必过规则 + 一条必违规则）。

    质量算子不删行：违规行带着原因进产物，行数必须与上游一致。
    """
    return FlowDSL(
        name=QA_FLOW,
        title="质量校验冒烟",
        description="非空 / 值域校验，违规行被标记而不删行",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": dataset_id}),
            FlowNode(
                id="dedup",
                type="clean.dedup",
                params={"keys": ["order_id"], "keep": "last", "order_by": "amount"},
            ),
            FlowNode(id="filt", type="clean.filter", params={"expr": "amount > 15"}),
            FlowNode(
                id="qa",
                type="quality.assert",
                params={
                    "not_null": ["amount"],
                    "rules": [
                        {"column": "amount", "op": "ge", "value": 0},
                        {"column": "amount", "op": "le", "value": QA_AMOUNT_CAP},
                    ],
                },
            ),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[
            FlowEdge(source="src", target="dedup"),
            FlowEdge(source="dedup", target="filt"),
            FlowEdge(source="filt", target="qa"),
            FlowEdge(source="qa", target="out"),
        ],
        output=FlowOutput(target_name=QA_FLOW, target_schema="dwd", pk_columns=["order_id"]),
    )


def _admin(session) -> User:
    admin = session.query(User).filter_by(username="admin").first()
    if admin is None:
        raise SystemExit("本地库里没有 admin 用户，先跑 python -m app.bootstrap admin <密码>")
    return admin


def prepare(engine, sf) -> tuple[int, int, int]:
    """准备源数据。返回 (e2e 数据集 id, sales_order 数据集 id, admin id)。"""
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

    # 指标字典锚定的是业务数据集，这里把它准备好（幂等）
    prepare_demo_datasets(engine, sf)

    with sf() as session:
        admin = _admin(session)

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

        sales = session.query(Dataset).filter_by(name=METRIC_DATASET).first()
        if sales is None:
            raise SystemExit(f"演示数据集 {METRIC_DATASET} 未准备好")
        return dataset.id, sales.id, admin.id


def _drop_flow(session, name: str) -> None:
    flow = session.query(DataFlow).filter_by(name=name).first()
    if flow is None:
        return
    session.query(FlowRun).filter(FlowRun.flow_id == flow.id).delete()
    session.query(DataFlowVersion).filter(DataFlowVersion.flow_id == flow.id).delete()
    session.delete(flow)


def _drop_dataset(session, name: str) -> None:
    dataset = session.query(Dataset).filter_by(name=name).first()
    if dataset is None:
        return
    session.query(DatasetGrant).filter_by(dataset_id=dataset.id).delete()
    session.delete(dataset)


def cleanup(engine, sf) -> None:
    with sf() as session:
        _drop_flow(session, FLOW_NAME)
        _drop_flow(session, METRIC_FLOW)
        _drop_flow(session, QA_FLOW)
        session.flush()
        # 产物数据集由流程注册，名字就是产物表名
        _drop_dataset(session, FLOW_NAME)
        _drop_dataset(session, METRIC_FLOW)
        _drop_dataset(session, QA_FLOW)
        _drop_dataset(session, DATASET_NAME)
        session.commit()
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {TARGET}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {METRIC_TARGET}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {QA_TARGET}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {SOURCE_TABLE}"))


def run_one(sf, engine, settings, admin_id: int, dsl: FlowDSL, label: str):
    """跑一条流程并打印结果；返回 FlowRun。"""
    with sf() as session:
        admin = session.get(User, admin_id)
        _drop_flow(session, dsl.name)
        session.commit()

        flow = service.create_flow(session, dsl=dsl, owner=admin)
        run = run_flow(
            session,
            flow,
            pg_engine=engine,
            settings=settings,
            triggered_by="manual",
            trigger_user_id=admin_id,
        )
    print(f"[{label}] status        : {run.status}")
    print(f"[{label}] rows          : {run.rows_out}")
    print(f"[{label}] error         : {run.error}")
    print(f"[{label}] grant_snapshot: {run.grant_snapshot}")
    print(f"[{label}] dataset_id    : {flow.output_dataset_id}")
    print(f"[{label}] warnings      : {run.meta.get('warnings')}")
    return run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="跑完清理冒烟自身的产物")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)

    e2e_id, sales_id, admin_id = prepare(engine, sf)
    failures: list[str] = []

    # ---------- 流程 1：权限链 ----------
    run = run_one(sf, engine, settings, admin_id, build_dsl(e2e_id), "权限链")
    if run.status != "ok":
        failures.append(f"权限链流程失败：{run.error}")
    else:
        schema, table = TARGET.split(".")
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(f'SELECT * FROM "{schema}"."{table}" ORDER BY order_id')
            ).fetchall()
        print(f"权限链 materialized: {rows}")
        leaked = [r for r in rows if WAREHOUSE not in str(r)]
        if leaked:
            failures.append(f"行级权限泄漏：{leaked}")

    # ---------- 流程 2：口径链 ----------
    margin_run = run_one(sf, engine, settings, admin_id, build_metric_dsl(sales_id), "口径链")
    if margin_run.status != "ok":
        failures.append(f"口径链流程失败：{margin_run.error}")
    else:
        sql_files = margin_run.meta.get("generated_files") or []
        if sql_files:
            model_sql = open(sql_files[0], encoding="utf-8").read()
            if "(amount - cost) / nullif(amount, 0)" not in model_sql:
                failures.append("编译产物里没有指标公式 —— 指标没有被展开")
            if '"metric"' in model_sql:
                failures.append("编译产物里残留了指标名 —— 应该在编译期就展开成公式")
        schema, table = METRIC_TARGET.split(".")
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    f'SELECT order_id, gross_margin FROM "{schema}"."{table}" '
                    f"ORDER BY order_id"
                )
            ).fetchall()
        print(f"口径链 materialized: {rows}")
        for order_id, value in rows:
            expected = EXPECTED_MARGIN.get(order_id)
            if expected is None:
                continue
            if value is None or abs(float(value) - expected) > 1e-6:
                failures.append(
                    f"订单 {order_id} 的毛利率 {value} 与期望 {expected} 不符"
                )

    # ---------- 流程 3：质量链 ----------
    qa_run = run_one(sf, engine, settings, admin_id, build_qa_dsl(e2e_id), "质量链")
    if qa_run.status != "ok":
        failures.append(f"质量链流程失败：{qa_run.error}")
    else:
        schema, table = QA_TARGET.split(".")
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    f"SELECT order_id, amount, qa_passed, qa_reason "
                    f'FROM "{schema}"."{table}" ORDER BY order_id'
                )
            ).fetchall()
        print(f"质量链 materialized: {rows}")
        if len(rows) != 2:
            failures.append(f"质量校验不应删行：期望 2 行，实际 {len(rows)}")
        by_order = {row[0]: row for row in rows}
        if by_order.get(2) is not None and by_order[2][2] is not True:
            failures.append(f"订单 2（amount=20）应通过校验：{by_order[2]}")
        if by_order.get(4) is not None:
            if by_order[4][2] is not False:
                failures.append(f"订单 4（amount=30）应被标记违规：{by_order[4]}")
            if f"应 ≤ {QA_AMOUNT_CAP}" not in (by_order[4][3] or ""):
                failures.append(f"违规原因不可读：{by_order[4][3]!r}")

    if args.cleanup:
        cleanup(engine, sf)
        print("cleaned up（指标演示数据集保留，要清请跑 seed_metrics_demo.py --cleanup）")

    engine.dispose()

    if failures:
        for item in failures:
            print(f"!! {item}")
        return 2
    return 0 if run.status == "ok" and margin_run.status == "ok" and qa_run.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
