"""流程的增删改查与版本管理。"""
import pytest

from app.flow import service
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput
from app.flow.errors import FlowNotFoundError, FlowPermissionError, FlowValidationError
from app.models import DataFlow


def make_dsl(env, *, name=None, select=None, share_with=None, target=None):
    suffix = env["suffix"]
    nodes = [FlowNode(id="src", type="source.dataset", params={"dataset_id": env["ds"]})]
    edges = []
    prev = "src"
    if select:
        nodes.append(FlowNode(id="pick", type="field.select", params={"columns": select}))
        edges.append(FlowEdge(source=prev, target="pick"))
        prev = "pick"
    nodes.append(FlowNode(id="out", type="sink.materialize", params={}))
    edges.append(FlowEdge(source=prev, target="out"))
    return FlowDSL(
        name=name or f"flowt_{suffix}",
        title="测试流程",
        description="单测用",
        nodes=nodes,
        edges=edges,
        output=FlowOutput(
            target_name=target or f"flowt_{suffix}",
            target_schema="dwd",
            pk_columns=["order_id"],
            share_with=share_with or [],
        ),
    )


def test_create_flow_stores_first_version(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        flow = service.create_flow(s, dsl=make_dsl(env), owner=load_user(env["owner"]))
        flow_id = flow.id
        assert flow.current_version == 1
        assert flow.target_table == f"flowt_{env['suffix']}"
        version = service.get_version(s, flow)
        assert version.version == 1
        assert version.dsl["name"] == flow.name
        # 保存即编译：产物留档，人可以直接看生成的 SQL
        assert "{{ config(" in version.compiled_sql
        assert version.dbt_selector == f"flowt_{env['suffix']}"
        assert version.input_datasets == [{"id": env["ds"], "name": env["ds_name"]}]
    assert flow_id


def test_create_flow_rejects_duplicate_name(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        owner = load_user(env["owner"])
        service.create_flow(s, dsl=make_dsl(env), owner=owner)
        with pytest.raises(FlowValidationError, match="流程名已存在"):
            service.create_flow(s, dsl=make_dsl(env), owner=owner)


def test_create_flow_rejects_source_without_grant(flow_env, load_user):
    """保存即编译 → 源数据集没授权，在保存时就该被挡下，而不是等半夜跑批。"""
    env, sf, _ = flow_env
    with sf() as s:
        with pytest.raises(FlowPermissionError, match="没有授权"):
            service.create_flow(s, dsl=make_dsl(env), owner=load_user(env["other"]))
        # 失败不该留下半个流程
        assert s.query(DataFlow).filter_by(name=f"flowt_{env['suffix']}").first() is None


def test_get_flow_hides_other_users_flow(flow_env, load_user):
    """无权看到的流程统一 404，不泄露存在性。"""
    env, sf, _ = flow_env
    with sf() as s:
        flow = service.create_flow(s, dsl=make_dsl(env), owner=load_user(env["owner"]))
        flow_id = flow.id
        with pytest.raises(FlowNotFoundError):
            service.get_flow(s, flow_id, load_user(env["other"]))
        # 管理员可见
        assert service.get_flow(s, flow_id, load_user(env["admin"])).id == flow_id


def test_save_version_keeps_history(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        owner = load_user(env["owner"])
        flow = service.create_flow(s, dsl=make_dsl(env), owner=owner)
        service.save_version(
            s, flow, dsl=make_dsl(env, select=["order_id", "warehouse"]), actor=owner
        )
        assert flow.current_version == 2
        versions = service.list_versions(s, flow)
        assert [v.version for v in versions] == [2, 1]  # 倒序
        # 老版本仍在，且内容没被改写
        v1, v2 = service.get_version(s, flow, 1), service.get_version(s, flow, 2)
        assert [n["type"] for n in v1.dsl["nodes"]] == ["source.dataset", "sink.materialize"]
        assert [n["type"] for n in v2.dsl["nodes"]] == [
            "source.dataset",
            "field.select",
            "sink.materialize",
        ]


def test_rollback_creates_new_version_with_old_content(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        owner = load_user(env["owner"])
        flow = service.create_flow(s, dsl=make_dsl(env), owner=owner)
        v1_dsl = service.get_version(s, flow).dsl
        service.save_version(
            s, flow, dsl=make_dsl(env, select=["order_id"]), actor=owner
        )
        service.rollback(s, flow, 1, actor=owner)
        assert flow.current_version == 3
        # 回滚 = 用旧 DSL 再存一版，历史一条不删
        assert service.get_version(s, flow, 3).dsl == v1_dsl
        assert [v.version for v in service.list_versions(s, flow)] == [3, 2, 1]


def test_edit_denied_for_non_owner(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        flow = service.create_flow(s, dsl=make_dsl(env), owner=load_user(env["owner"]))
        with pytest.raises(FlowPermissionError, match="只有流程 owner"):
            service.save_version(s, flow, dsl=make_dsl(env), actor=load_user(env["other"]))
        with pytest.raises(FlowPermissionError):
            service.set_enabled(s, flow, False, actor=load_user(env["other"]))
        # 管理员可以
        service.set_enabled(s, flow, False, actor=load_user(env["admin"]))
        assert flow.enabled is False


def test_max_nodes_guard(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        with pytest.raises(FlowValidationError, match="超过上限"):
            service.create_flow(
                s, dsl=make_dsl(env), owner=load_user(env["owner"]), max_nodes=1
            )


def test_list_flows_scoped_by_owner(flow_env, load_user):
    env, sf, _ = flow_env
    with sf() as s:
        service.create_flow(s, dsl=make_dsl(env), owner=load_user(env["owner"]))
        assert [f.name for f in service.list_flows(s, load_user(env["owner"]))] == [
            f"flowt_{env['suffix']}"
        ]
        assert service.list_flows(s, load_user(env["other"])) == []
        assert f"flowt_{env['suffix']}" in [
            f.name for f in service.list_flows(s, load_user(env["admin"]))
        ]
