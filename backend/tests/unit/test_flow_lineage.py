"""血缘：事件合规、成功/失败的 emit 语义、双向追溯（含环与深度）。

追溯测试直接塞边数据（不走 dbt）—— 追溯逻辑只关心「流程输入 → 产物」与
「数据集 → 物理源」两类边的形状，与边是怎么跑出来的无关。
"""
import uuid

import pytest
import sqlalchemy as sa

from app.flow import lineage, service
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.lineage import downstream, upstream
from app.models import (
    DataFlow,
    DataFlowVersion,
    Dataset,
    LineageEvent,
    User,
    new_version_id,
)
from app.flow.runner import DbtRunResult, run_flow

from dbt_shadow import shadow_dbt_project


class _Settings:
    # 落盘落到影子项目：真实 backend/dbt/models/generated 在本机沙箱里只读
    flow_dbt_project_dir = shadow_dbt_project()
    flow_generated_subdir = "models/generated"
    flow_dbt_target = "prod"
    flow_max_nodes = 50


class FakeDbt:
    """照抄 test_flow_runner 的假 dbt：建带列的表并灌两行。

    产物表必须有列 —— 列探查为空时 runner 会按设计报错（S1 闭环修的坑）。
    """

    def __init__(self, engine, table):
        self.engine = engine
        self.table = table
        self.columns = ("order_id", "warehouse")
        self.rows = (("1", "德清"), ("2", "德清"))

    def __call__(self, project_dir, model_name, *, target):
        schema, name = self.table.split(".")
        cols_sql = ", ".join(f'"{c}" text' for c in self.columns)
        with self.engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}"."{name}"'))
            conn.execute(sa.text(f'CREATE TABLE "{schema}"."{name}" ({cols_sql})'))
            for row in self.rows:
                holders = ", ".join(f":v{i}" for i in range(len(row)))
                conn.execute(
                    sa.text(f'INSERT INTO "{schema}"."{name}" VALUES ({holders})'),
                    {f"v{i}": v for i, v in enumerate(row)},
                )
        return DbtRunResult(True, "")


class BrokenDbt:
    def __call__(self, project_dir, model_name, *, target):
        return DbtRunResult(False, "compilation error")


# ---------------------------------------------------------------- 事件结构


def test_run_event_follows_openlineage_shape():
    payload = lineage.build_run_event(
        flow_name="clean_orders",
        run_id="fr_123",
        inputs=["sales_order", "inventory"],
        output="dwd_orders",
    )
    assert payload["eventType"] == "COMPLETE"
    assert payload["job"]["name"] == "flow:clean_orders"
    assert payload["run"]["runId"] == "fr_123"
    assert [i["name"] for i in payload["inputs"]] == ["inventory", "sales_order"]  # 排序稳定
    assert payload["outputs"][0]["name"] == "dwd_orders"
    assert payload["producer"].startswith("https://")


def test_input_names_normalize_the_dict_shape():
    """input_datasets 存的是 [{id, name}] —— 事件里必须归一成名字（OpenLineage 合规）。"""
    assert lineage._input_names([{"id": 1, "name": "sales"}, "manual"]) == [
        "manual",
        "sales",
    ]
    assert lineage._input_names(None) == []
    assert lineage._input_names([{"id": 2}]) == []  # 没有名字的条目丢弃


def test_emit_uses_stable_job_and_run_ids(flow_env, load_user):
    env, sf, _engine = flow_env
    dsl = FlowDSL(
        name=f"flowt_{env['suffix']}",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": env["ds"]}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="out")],
        output=FlowOutput(target_name=f"flowt_{env['suffix']}"),
    )
    with sf() as session:
        flow = service.create_flow(session, dsl=dsl, owner=load_user(env["owner"]))
        event = lineage.emit_run_event(
            session,
            flow=flow,
            run_id="fr_abc",
            inputs=[{"id": env["ds"], "name": env["ds_name"]}],
            output=flow.target_table,
        )
        session.commit()
        assert event.id is not None
        row = session.get(LineageEvent, event.id)
        assert row.job_name == f"flow:flowt_{env['suffix']}"
        assert row.run_id == "fr_abc"
        assert row.payload["inputs"][0]["name"] == env["ds_name"]


# ---------------------------------------------------------------- runner 集成


def test_success_run_emits_exactly_one_complete_event(flow_env, load_user):
    env, sf, engine = flow_env
    dsl = FlowDSL(
        name=f"flowt_{env['suffix']}",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": env["ds"]}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="out")],
        output=FlowOutput(target_name=f"flowt_{env['suffix']}"),
    )
    with sf() as session:
        flow = service.create_flow(session, dsl=dsl, owner=load_user(env["owner"]))
        flow_id = flow.id

    with sf() as session:
        user = load_user(env["owner"])
        flow = service.get_flow(session, flow_id, user)
        run = run_flow(
            session,
            flow,
            pg_engine=engine,
            settings=_Settings(),
            trigger_user_id=env["owner"],
            dbt_runner=FakeDbt(engine, f"dwd.flowt_{env['suffix']}"),
        )
        assert run.status == "ok", run.error
        events = session.query(LineageEvent).filter_by(flow_id=flow_id).all()
        assert len(events) == 1
        payload = events[0].payload
        assert payload["eventType"] == "COMPLETE"
        assert payload["run"]["runId"] == run.run_id
        assert [i["name"] for i in payload["inputs"]] == [env["ds_name"]]
        assert payload["outputs"][0]["name"] == f"flowt_{env['suffix']}"


def test_failed_run_emits_no_lineage_event(flow_env, load_user):
    """血缘只记「真的发生了的加工」：失败运行不是一次加工。"""
    env, sf, engine = flow_env
    dsl = FlowDSL(
        name=f"flowt_{env['suffix']}",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": env["ds"]}),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[FlowEdge(source="src", target="out")],
        output=FlowOutput(target_name=f"flowt_{env['suffix']}"),
    )
    with sf() as session:
        flow = service.create_flow(session, dsl=dsl, owner=load_user(env["owner"]))
        flow_id = flow.id

    with sf() as session:
        user = load_user(env["owner"])
        flow = service.get_flow(session, flow_id, user)
        run = run_flow(
            session,
            flow,
            pg_engine=engine,
            settings=_Settings(),
            trigger_user_id=env["owner"],
            dbt_runner=BrokenDbt(),
        )
        assert run.status == "failed"
        assert session.query(LineageEvent).filter_by(flow_id=flow_id).count() == 0


# ---------------------------------------------------------------- 双向追溯


@pytest.fixture
def lineage_owner(pg_session_factory):
    """临时用户 + 统一清理（塞的行与用户一起删，不污染本地库）。"""
    suffix = uuid.uuid4().hex[:8]
    created: dict = {"flows": [], "datasets": [], "user": None}

    with pg_session_factory() as session:
        user = User(username=f"lin_{suffix}", password_hash="x")
        session.add(user)
        session.commit()
        created["user"] = user.id

    yield created["user"], created

    with pg_session_factory() as session:
        session.query(DataFlowVersion).filter(
            DataFlowVersion.flow_id.in_(created["flows"] or [0])
        ).delete(synchronize_session=False)
        session.query(DataFlow).filter(
            DataFlow.id.in_(created["flows"] or [0])
        ).delete(synchronize_session=False)
        session.query(Dataset).filter(
            Dataset.name.in_(created["datasets"] or ["__none__"])
        ).delete(synchronize_session=False)
        session.query(User).filter_by(id=created["user"]).delete(synchronize_session=False)
        session.commit()


def _seed_chain(owner_id, created, pg_session_factory):
    """A --(flowF)--> B --(flowG)--> C，A 的物理源是 ods.src_a。塞的行记录进 created 供清理。"""
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as session:
        for name, src in (("A", "ods.src_a"), ("B", ""), ("C", "")):
            ds = Dataset(
                name=f"{name}_{suffix}",
                enabled=True,
                source_table=src,
                columns=[],
                pk_columns=[],
            )
            session.add(ds)
            created["datasets"].append(ds.name)
        session.flush()
        names = {
            n: session.query(Dataset).filter_by(name=f"{n}_{suffix}").first().name
            for n in ("A", "B", "C")
        }
        for fname, target, inputs in (
            ("F", names["B"], [names["A"]]),
            ("G", names["C"], [names["B"]]),
        ):
            flow = DataFlow(
                name=f"{fname}_{suffix}",
                title=fname,
                owner_user_id=owner_id,
                current_version=1,
                target_schema="dwd",
                target_table=target,
                enabled=True,
            )
            session.add(flow)
            session.flush()
            created["flows"].append(flow.id)
            session.add(
                DataFlowVersion(
                    flow_id=flow.id,
                    version=1,
                    version_id=new_version_id(),
                    dsl={},
                    compiled_sql="",
                    compiled_yaml="",
                    dbt_selector=target,
                    input_datasets=inputs,
                )
            )
        session.commit()
    return {"a": names["A"], "b": names["B"], "c": names["C"]}


def test_upstream_walks_to_the_physical_leaf(lineage_owner, pg_session_factory):
    owner_id, created = lineage_owner
    chain = _seed_chain(owner_id, created, pg_session_factory)
    with pg_session_factory() as session:
        result = upstream(session, chain["c"], depth=3)
    layers = result["layers"]
    assert [n["name"] for n in layers[0]] == [chain["b"]]
    assert layers[0][0]["via"].startswith("G_")
    assert [n["name"] for n in layers[1]] == [chain["a"]]
    assert [n["name"] for n in layers[2]] == ["ods.src_a"]
    assert layers[2][0]["leaf"] is True
    assert layers[2][0]["source_table"] == "ods.src_a"


def test_downstream_answers_the_impact_question(lineage_owner, pg_session_factory):
    """影响分析：改 A 会砸到 B 与 C。"""
    owner_id, created = lineage_owner
    chain = _seed_chain(owner_id, created, pg_session_factory)
    with pg_session_factory() as session:
        result = downstream(session, chain["a"], depth=3)
    layers = result["layers"]
    assert [n["name"] for n in layers[0]] == [chain["b"]]
    assert layers[0][0]["via"].startswith("F_")
    assert [n["name"] for n in layers[1]] == [chain["c"]]


def test_lineage_cycle_terminates_without_duplicates(lineage_owner, pg_session_factory):
    """误配置成环时：到已访问节点即停，且不重复报告。"""
    owner_id, created = lineage_owner
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as session:
        for name in ("D", "E"):
            ds = Dataset(
                name=f"{name}_{suffix}",
                enabled=True,
                source_table="",
                columns=[],
                pk_columns=[],
            )
            session.add(ds)
            created["datasets"].append(ds.name)
        session.flush()
        d = session.query(Dataset).filter_by(name=f"D_{suffix}").first().name
        e = session.query(Dataset).filter_by(name=f"E_{suffix}").first().name
        for fname, target, inputs in (("H", e, [d]), ("I", d, [e])):
            flow = DataFlow(
                name=f"{fname}_{suffix}",
                title=fname,
                owner_user_id=owner_id,
                current_version=1,
                target_schema="dwd",
                target_table=target,
                enabled=True,
            )
            session.add(flow)
            session.flush()
            created["flows"].append(flow.id)
            session.add(
                DataFlowVersion(
                    flow_id=flow.id,
                    version=1,
                    version_id=new_version_id(),
                    dsl={},
                    compiled_sql="",
                    compiled_yaml="",
                    dbt_selector=target,
                    input_datasets=inputs,
                )
            )
        session.commit()

    with pg_session_factory() as session:
        result = upstream(session, d, depth=6)

    names = [n["name"] for layer in result["layers"] for n in layer]
    assert len(names) == len(set(names))  # 无重复
    # D ← I ← E ← H ← D 成环：层 1 报告 E；E 的上游是起点 D（已访问）→ 到环即停。
    # 起点本身不重复报告 —— 这正是防环语义（spec：不重复报告同一节点）
    assert names == [e]


def test_depth_limits_the_walk(lineage_owner, pg_session_factory):
    owner_id, created = lineage_owner
    chain = _seed_chain(owner_id, created, pg_session_factory)
    with pg_session_factory() as session:
        result = upstream(session, chain["c"], depth=1)
    assert len(result["layers"]) == 1
    assert [n["name"] for n in result["layers"][0]] == [chain["b"]]
