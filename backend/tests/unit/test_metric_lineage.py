"""指标血缘：一个指标被谁用、改了会砸到谁。

血缘的价值在影响分析，所以断言集中在"引用关系真的被找出来了"：
无引用 / 单层引用 / 指标不存在报错 / 只认当前版本。
"""
import uuid

import pytest
import sqlalchemy as sa

from app.metrics import MetricNotFoundError
from app.metrics.lineage import metric_lineage, metric_usage
from app.models import DataFlow, DataFlowVersion, Dataset, User, new_version_id


def _dsl_with_metric(metric_name: str | None, *, source_dataset: str = "ods.ml_t"):
    """一个最小流程 DSL：source → (可选 field.derive 引用指标) → sink。"""
    nodes = [
        {"id": "src", "type": "source.dataset",
         "params": {"dataset_id": 1}},
        {"id": "out", "type": "sink.materialize", "params": {}},
    ]
    edges = [{"from": "src", "to": "out"}]
    if metric_name:
        nodes.insert(1, {
            "id": "d1", "type": "field.derive",
            "params": {"metric": metric_name, "alias": "gm"},
        })
        edges = [
            {"from": "src", "to": "d1"},
            {"from": "d1", "to": "out"},
        ]
    return {"name": "f", "nodes": nodes, "edges": edges,
            "output": {"target_schema": "dwd", "target_table": "t",
                       "materialization": "table"}}


@pytest.fixture
def ml_env(pg_session_factory, pg_engine):
    """两条流程：一条引用指标、一条不引用。"""
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        owner = User(username=f"mlu_{suffix}", password_hash="x")
        s.add(owner)
        s.flush()

        f1 = DataFlow(name=f"mlf1_{suffix}", owner_user_id=owner.id,
                      target_schema="dwd", target_table=f"mlf1_{suffix}",
                      current_version=1)
        f2 = DataFlow(name=f"mlf2_{suffix}", owner_user_id=owner.id,
                      target_schema="dwd", target_table=f"mlf2_{suffix}",
                      current_version=1)
        s.add_all([f1, f2])
        s.flush()
        s.add_all([
            DataFlowVersion(flow_id=f1.id, version=1, version_id=new_version_id(),
                            dsl=_dsl_with_metric("gross_margin"),
                            input_datasets=[{"id": 1, "name": "sales_order"}]),
            DataFlowVersion(flow_id=f2.id, version=1, version_id=new_version_id(),
                            dsl=_dsl_with_metric(None),
                            input_datasets=[{"id": 1, "name": "sales_order"}]),
        ])
        s.commit()
        ids = {"owner": owner.id, "f1": f1.id, "f2": f2.id, "suffix": suffix}

    yield ids, pg_session_factory, pg_engine

    with pg_session_factory() as s:
        flow_ids = [ids["f1"], ids["f2"]]
        s.query(DataFlowVersion).filter(
            DataFlowVersion.flow_id.in_(flow_ids)
        ).delete(synchronize_session=False)
        s.query(DataFlow).filter(DataFlow.id.in_(flow_ids)).delete(
            synchronize_session=False
        )
        s.query(User).filter_by(id=ids["owner"]).delete(synchronize_session=False)
        s.commit()


def test_usage_finds_referencing_flow_only(ml_env):
    """只有真正引用了该指标的流程出现在 used_by 里。"""
    ids, sf, _ = ml_env
    with sf() as s:
        usage = metric_usage(s, "gross_margin")
    names = {u["flow_name"] for u in usage}
    assert f"mlf1_{ids['suffix']}" in names
    assert f"mlf2_{ids['suffix']}" not in names


def test_usage_empty_for_unreferenced_metric(ml_env):
    ids, sf, _ = ml_env
    with sf() as s:
        assert metric_usage(s, "never_used_metric") == []


def test_usage_reports_target_and_tenant(ml_env):
    ids, sf, _ = ml_env
    with sf() as s:
        usage = metric_usage(s, "gross_margin")
    assert usage, "应当找到引用"
    item = usage[0]
    assert item["target_table"] == f"mlf1_{ids['suffix']}"
    assert item["flow_id"] == ids["f1"]
    assert "tenant_id" in item


def test_lineage_includes_metric_definition(ml_env):
    """血缘响应要带上指标本身的口径与负责人 —— 影响分析要能回答"该问谁"。"""
    ids, sf, _ = ml_env
    with sf() as s:
        out = metric_lineage(s, "gross_margin")
    assert out["metric"]["name"] == "gross_margin"
    assert out["metric"]["owner"]
    assert out["metric"]["expr"]
    assert out["used_by_count"] == len(out["used_by"])


def test_lineage_includes_downstream_layers(ml_env):
    """每个产物都要带下游分层（影响分析的核心）。"""
    ids, sf, _ = ml_env
    with sf() as s:
        out = metric_lineage(s, "gross_margin")
    assert out["used_by"], "应当有引用的流程"
    for item in out["used_by"]:
        assert "downstream" in item
        assert isinstance(item["downstream"], list)


def test_lineage_unknown_metric_raises(ml_env):
    """指标不存在要明确报错（上层翻成 404），不能静默返回空。"""
    ids, sf, _ = ml_env
    with sf() as s:
        with pytest.raises(MetricNotFoundError):
            metric_lineage(s, "definitely_not_a_metric")


def test_only_current_version_counts(pg_session_factory, pg_engine):
    """只认当前版本：旧版本引用了指标，但当前版本没引用 → 不算引用。"""
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        owner = User(username=f"mvu_{suffix}", password_hash="x")
        s.add(owner)
        s.flush()
        flow = DataFlow(name=f"mvc_{suffix}", owner_user_id=owner.id,
                        target_schema="dwd", target_table=f"mvc_{suffix}",
                        current_version=2)
        s.add(flow)
        s.flush()
        s.add_all([
            # v1 引用了指标
            DataFlowVersion(flow_id=flow.id, version=1, version_id=new_version_id(),
                            dsl=_dsl_with_metric("gross_margin"),
                            input_datasets=[]),
            # v2（当前）不再引用
            DataFlowVersion(flow_id=flow.id, version=2, version_id=new_version_id(),
                            dsl=_dsl_with_metric(None), input_datasets=[]),
        ])
        s.commit()
        fid, oid = flow.id, owner.id

    try:
        with pg_session_factory() as s:
            usage = metric_usage(s, "gross_margin")
        assert all(u["flow_id"] != fid for u in usage), "旧版本不该算引用"
    finally:
        with pg_session_factory() as s:
            s.query(DataFlowVersion).filter_by(flow_id=fid).delete(
                synchronize_session=False
            )
            s.query(DataFlow).filter_by(id=fid).delete(synchronize_session=False)
            s.query(User).filter_by(id=oid).delete(synchronize_session=False)
            s.commit()


def test_metric_api_404_for_unknown(ml_env):
    """接口层把未知指标翻成 404。"""
    from fastapi import HTTPException

    from app.api.metric_api import get_metric_lineage
    from app.api.deps import Context

    ids, sf, engine = ml_env

    class _P:
        user = None
        tenant = None

    ctx = Context(session_factory=sf, erp_engine=None, pg_engine=engine, settings=None)
    with pytest.raises(HTTPException) as exc:
        get_metric_lineage("no_such_metric_xyz", principal=_P(), ctx=ctx)
    assert exc.value.status_code == 404
