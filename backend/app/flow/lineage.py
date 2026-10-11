"""血缘：事件构造与双向追溯。

边只有两类，且数据早就存在 —— 本模块做的是**组织与暴露**：

1. 流程边：流程最新版 `input_datasets` → 产物（`data_flow_versions` + `data_flows`）；
2. 物理边：数据集 → `source_table`（如 `ods.sales_order`）。

读法分工：

- **事件（写）**：编排成功后一条 OpenLineage RunEvent —— "那次运行用了什么输入、
  产出了什么"，是事实记录，与运行记录同事务落库，永不重算；
- **追溯（读）**：从当前状态 BFS 重算分层图 —— "现在这条链长什么样"。
  历史版本不参与追溯：血缘跟"现在是什么"走，不跟"曾经是什么"走。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import DataFlow, DataFlowVersion, Dataset, LineageEvent
from app.models.lineage import EVENT_COMPLETE, NAMESPACE

PRODUCER = "https://github.com/sloveprogramming/stream-BI"
MAX_DEPTH = 6


def _input_names(raw) -> list[str]:
    """归一化流程输入。

    `data_flow_versions.input_datasets` 存的是 `[{"id":…, "name":…}]`（编译产物
    顺手带了 id），而血缘词表只需要名字 —— 两处消费方（事件、追溯）都在这里归一，
    将来格式再变只改这一处。
    """
    names: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return sorted(set(names))


def build_run_event(
    *,
    flow_name: str,
    run_id: str,
    inputs: list[str],
    output: str,
    event_time: datetime | None = None,
) -> dict:
    """OpenLineage RunEvent。dataset 词表与问数/授权共用数据集名，跨功能对得上。"""
    return {
        "eventType": EVENT_COMPLETE,
        "eventTime": (event_time or datetime.now(timezone.utc)).isoformat(),
        "producer": PRODUCER,
        "run": {"runId": run_id, "facets": {}},
        "job": {"namespace": NAMESPACE, "name": f"flow:{flow_name}", "facets": {}},
        "inputs": [{"namespace": NAMESPACE, "name": n} for n in sorted(inputs)],
        "outputs": [{"namespace": NAMESPACE, "name": output}],
    }


def emit_run_event(
    session: Session,
    *,
    flow: DataFlow,
    run_id: str,
    inputs: list,
    output: str,
) -> LineageEvent:
    """成功路径的事件落库。由 runner 在成功分支调用，与运行记录同事务提交。

    `inputs` 接受原始的 `input_datasets`（字典或字符串混合），内部归一化成名字。
    """
    payload = build_run_event(
        flow_name=flow.name, run_id=run_id, inputs=_input_names(inputs), output=output
    )
    event = LineageEvent(
        event_type=EVENT_COMPLETE,
        job_name=payload["job"]["name"],
        run_id=run_id,
        flow_id=flow.id,
        payload=payload,
    )
    session.add(event)
    return event


def _flow_index(session: Session) -> dict[str, dict]:
    """产物表名 → {name, inputs}（每条流程取**当前版本**）。"""
    index: dict[str, dict] = {}
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
    for flow in flows:
        version = by_flow.get(flow.id)
        if version is None:
            continue
        index[flow.target_table] = {
            "name": flow.name,
            "inputs": _input_names(version.input_datasets),
        }
    return index


def _source_tables(session: Session) -> dict[str, str]:
    rows = session.query(Dataset).all()
    return {ds.name: ds.source_table for ds in rows if ds.source_table}


def _layers(
    frontier: list[tuple[str, str | None]],
    *,
    expand,
    depth: int,
) -> list[list[dict]]:
    """BFS 分层。每个节点只在**首次发现**的层出现（spec：不重复报告）。"""
    visited: set[str] = {name for name, _ in frontier}
    layers: list[list[dict]] = []
    for _ in range(max(1, min(depth, MAX_DEPTH))):
        layer: list[dict] = []
        next_frontier: list[tuple[str, str | None]] = []
        for name, via in frontier:
            for node in expand(name, via):
                if node["name"] in visited:
                    continue
                visited.add(node["name"])
                layer.append(node)
                next_frontier.append((node["name"], None))
        if not layer:
            break
        layers.append(layer)
        frontier = next_frontier
    return layers


def upstream(session: Session, dataset_name: str, *, depth: int = 3) -> dict:
    """向上追溯：这个数据集由什么加工而来，一直追到物理源表。"""
    flows = _flow_index(session)
    sources = _source_tables(session)

    def expand(name: str, _via: str | None) -> list[dict]:
        flow = flows.get(name)
        if flow is not None:
            return [
                {"name": inp, "via": flow["name"], "source_table": None}
                for inp in flow["inputs"]
            ]
        # 不是任何流程的产物 → 物理叶子
        src = sources.get(name)
        if src:
            return [{"name": src, "via": None, "source_table": src, "leaf": True}]
        return []

    layers = _layers([(dataset_name, None)], expand=expand, depth=depth)
    return {"dataset": dataset_name, "direction": "upstream", "layers": layers}


def downstream(session: Session, dataset_name: str, *, depth: int = 3) -> dict:
    """向下追溯（影响分析）：改这个数据集会砸到哪些下游产物。"""
    flows = _flow_index(session)
    consumers: dict[str, list[dict]] = {}
    for target, info in flows.items():
        for inp in info["inputs"]:
            consumers.setdefault(inp, []).append(
                {"name": info["name"], "target": target}
            )

    def expand(name: str, _via: str | None) -> list[dict]:
        return [
            {"name": c["target"], "via": c["name"], "source_table": None}
            for c in consumers.get(name, [])
        ]

    layers = _layers([(dataset_name, None)], expand=expand, depth=depth)
    return {"dataset": dataset_name, "direction": "downstream", "layers": layers}
