"""指标血缘：一个指标被谁用、改了会砸到谁。

## 为什么从 versions 重算，不建表

与 `app/flow/lineage.py` 同一个取舍：**图是现状的视图，可以随时重算**。
"哪些流程引用了这个指标"这件事的答案就在 `data_flow_versions.dsl` 里
（`field.derive` 节点的 `params.metric`），建一张 `metric_usage` 表只会
多一处需要同步维护的状态，而同步漏了比没有更糟。

代价是每次查询要扫一遍流程版本。流程数量级在几十条以内，可接受；
真到了扫不动的量级，再加缓存或物化表（接口不变）。

## 边

    指标 ──被引用──> 流程 ──产出──> 产物数据集 ──被消费──> 下游

前两段是本模块新接的（读 `data_flow_versions`），第三段直接复用
`app/flow/lineage.downstream` —— 不重写 BFS。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.flow import lineage as flow_lineage
from app.metrics import MetricNotFoundError, load_for
from app.models import DataFlow, DataFlowVersion, Dataset

# 引用指标的算子类型（将来有别的算子能引用指标，加到这里）
METRIC_REFERENCING_OPERATORS = ("field.derive",)


def _metric_refs(version: DataFlowVersion) -> list[str]:
    """一个流程版本里引用了哪些指标名。"""
    dsl = version.dsl or {}
    names: list[str] = []
    for node in dsl.get("nodes") or []:
        if node.get("type") not in METRIC_REFERENCING_OPERATORS:
            continue
        name = (node.get("params") or {}).get("metric")
        if name and name not in names:
            names.append(str(name))
    return names


def metric_usage(session: Session, metric_name: str) -> list[dict]:
    """哪些流程（当前版本）引用了这个指标。

    返回 [{flow_id, flow_name, target_table, output_dataset, tenant_id}]，
    按流程名排序，便于稳定展示。
    """
    flows = session.query(DataFlow).all()
    versions = (
        session.query(DataFlowVersion)
        .filter(
            DataFlowVersion.version == DataFlow.current_version,
            DataFlowVersion.flow_id.in_([f.id for f in flows] or [0]),
        )
        .all()
    )
    by_flow = {v.flow_id: v for v in versions}

    usage: list[dict] = []
    for flow in flows:
        version = by_flow.get(flow.id)
        if version is None:
            continue
        if metric_name not in _metric_refs(version):
            continue
        output_dataset = None
        if flow.output_dataset_id:
            ds = session.get(Dataset, flow.output_dataset_id)
            output_dataset = ds.name if ds else None
        usage.append(
            {
                "flow_id": flow.id,
                "flow_name": flow.name,
                "flow_title": flow.title,
                "target_table": flow.target_table,
                "output_dataset": output_dataset,
                "tenant_id": flow.tenant_id,
                "enabled": flow.enabled,
            }
        )
    return sorted(usage, key=lambda item: item["flow_name"])


def metric_lineage(session: Session, metric_name: str, *, depth: int = 3) -> dict:
    """指标的完整血缘：被哪些流程引用 → 产物是什么 → 产物的下游是谁。"""
    dictionary = load_for(session)
    try:
        metric = dictionary.get(metric_name)
    except MetricNotFoundError:
        # 让上层区分"指标不存在"与"没有引用"
        raise

    usage = metric_usage(session, metric_name)

    # 每个产物的下游（影响分析）。复用 flow/lineage 的 BFS，不重写。
    for item in usage:
        item["downstream"] = flow_lineage.downstream(
            session, item["target_table"], depth=depth
        )["layers"]

    return {
        "metric": {
            "name": metric.name,
            "label": metric.label,
            "owner": metric.owner,
            "dataset": metric.dataset,
            "expr": metric.expr,
        },
        "used_by": usage,
        "used_by_count": len(usage),
    }
