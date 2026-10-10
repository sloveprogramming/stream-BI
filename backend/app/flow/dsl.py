"""流程 DSL —— 一份定义，三处复用。

同一份 Pydantic 模型同时承担三件事：

1. **后端校验**：保存、编译、运行前都过一遍；
2. **前端 JSON Schema**：算子面板与配置表单由 `operators.operator_catalog()` 生成，
   加算子不用改前端；
3. **LLM 结构化输出 schema**：`ai/nl2flow.py` 的「自然语言 → 流程」直接复用这份定义。

三处共用一份定义，才不会各自漂移 —— 这是上一版设计里最容易埋坑的地方。
"""
from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.flow.errors import FlowValidationError
from app.flow.sqlutil import sanitize_ident

SOURCE_PREFIX = "source."
SINK_PREFIX = "sink."


class FlowNode(BaseModel):
    """画布上的一个算子实例。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1)  # 如 "clean.dedup"，须存在于算子注册表
    params: dict = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class FlowOutput(BaseModel):
    """产物落点与权限。"""

    model_config = ConfigDict(extra="forbid")

    materialization: Literal["table", "view"] = "table"
    target_schema: Literal["dwd", "dws", "ads"] = "dwd"
    target_name: str  # 仅表名，不含 schema
    # 产物的粒度（主键）：由建流程的人声明。数据集分页/去重都依赖它，
    # 留空会导致查询没有稳定排序，所以强烈建议填。
    pk_columns: list[str] = Field(default_factory=list)
    register_dataset: bool = True
    # 产物默认只有 owner 可见；要给别人看得显式列在这里
    share_with: list[int] = Field(default_factory=list)


class FlowDSL(BaseModel):
    """一张完整的数据编排图。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    title: str = ""
    description: str = ""
    nodes: list[FlowNode]
    edges: list[FlowEdge] = Field(default_factory=list)
    output: FlowOutput

    # ---- 便捷视图 ----

    @property
    def sink(self) -> FlowNode:
        """唯一的输出节点（校验保证有且仅有一个）。"""
        return next(n for n in self.nodes if n.type.startswith(SINK_PREFIX))

    def node_map(self) -> dict[str, FlowNode]:
        return {n.id: n for n in self.nodes}

    def upstream_ids(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def source_nodes(self) -> list[FlowNode]:
        return [n for n in self.nodes if n.type.startswith(SOURCE_PREFIX)]

    # ---- 校验 ----

    @model_validator(mode="after")
    def _validate_graph(self) -> "FlowDSL":
        ids = [n.id for n in self.nodes]
        if not ids:
            raise FlowValidationError("流程至少要有一个节点")
        if len(ids) != len(set(ids)):
            raise FlowValidationError("节点 id 重复")

        # CTE 名由 id 派生，规范化后也不能撞（否则编译出的 SQL 会静默取错表）
        sanitized = [sanitize_ident(i) for i in ids]
        if len(sanitized) != len(set(sanitized)):
            raise FlowValidationError("节点 id 规范化后重复，请换一个 id")

        known = set(ids)
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise FlowValidationError(f"边引用了不存在的节点: {e.source} -> {e.target}")
            if e.source == e.target:
                raise FlowValidationError(f"节点不能连接自己: {e.source}")

        sources = self.source_nodes()
        sinks = [n for n in self.nodes if n.type.startswith(SINK_PREFIX)]
        if not sources:
            raise FlowValidationError("流程至少需要一个输入节点（source.*）")
        if len(sinks) != 1:
            raise FlowValidationError(
                f"流程必须且只能有一个输出节点（sink.*），当前 {len(sinks)} 个"
            )

        indeg: dict[str, int] = {i: 0 for i in ids}
        outdeg: dict[str, int] = {i: 0 for i in ids}
        for e in self.edges:
            indeg[e.target] += 1
            outdeg[e.source] += 1

        source_ids = {n.id for n in sources}
        orphan = [i for i in ids if indeg[i] == 0 and i not in source_ids]
        if orphan:
            raise FlowValidationError(f"以下节点没有输入，且不是 source: {orphan}")
        dangling = [i for i in ids if outdeg[i] == 0 and i not in {s.id for s in sinks}]
        if dangling:
            raise FlowValidationError(f"以下节点没有下游，也没有接到输出: {dangling}")

        # 无环（Kahn 走不通即说明有环）
        topological_order(self.nodes, self.edges)

        # 产物表名必须合法（直接进 SQL 标识符位）
        sanitize_target = sanitize_ident(self.output.target_name)
        if sanitize_target != self.output.target_name:
            raise FlowValidationError(
                f"产物表名不合法: {self.output.target_name!r}（只允许字母/数字/下划线，且不以数字开头）"
            )
        return self


def topological_order(nodes: list[FlowNode], edges: list[FlowEdge]) -> list[FlowNode]:
    """Kahn 拓扑排序，返回确定性的执行顺序（同层按 nodes 给定顺序）。"""
    order_index = {n.id: i for i, n in enumerate(nodes)}
    indeg = {n.id: 0 for n in nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        adj[e.source].append(e.target)
        indeg[e.target] += 1

    by_id = {n.id: n for n in nodes}
    ready = deque(sorted((i for i, d in indeg.items() if d == 0), key=order_index.__getitem__))
    out: list[FlowNode] = []
    while ready:
        nid = ready.popleft()
        out.append(by_id[nid])
        for nxt in adj[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    if len(out) != len(nodes):
        raise FlowValidationError("流程存在环，无法确定执行顺序")
    return out
