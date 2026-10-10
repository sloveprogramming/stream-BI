"""算子编译上下文。

**权限红线只在这一个入口上**：算子想拿源数据，只能调 `scoped_source()`。
它由编译器注入，内部强制走 `resolve_grant` + `build_scoped_sql`。
算子层拿不到"裸表名"，因此**不存在"某个算子忘了加行级规则"这种可能** ——
这是把权限做成结构性保证，而不是靠人守规范。

另外承担两件事：
- 生成 CTE 名（由节点 id 规范化而来，保证是合法标识符且不重复）；
- 沿 DAG 传播列信息（`columns`），让 `rename`/`cast` 这类需要列清单的算子
  在 `filter` 之后也能正常工作。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.flow.dsl import FlowDSL, FlowNode
from app.flow.errors import FlowCompileError, FlowValidationError
from app.flow.sqlutil import check_ident, sanitize_ident


@dataclass
class CompileCtx:
    dsl: FlowDSL
    # 节点 id -> 该节点输出的列名清单（None = 推导不出来）
    columns: dict[str, list[str] | None] = field(default_factory=dict)
    # 由编译器注入：把一个 source 节点编译成「带行级权限的 SELECT」
    source_loader: Callable[[FlowNode], str] | None = None
    # 本次编译实际套用的行级规则快照，编译器收集后写进 flow_runs
    grant_snapshot: dict[str, list] = field(default_factory=dict)

    # ---- CTE 名 ----

    def cte(self, node_id: str) -> str:
        return f"op_{sanitize_ident(node_id)}"

    # ---- 上下游 ----

    def upstream_ids(self, node: FlowNode) -> list[str]:
        return self.dsl.upstream_ids(node.id)

    def inputs(self, node: FlowNode) -> list[str]:
        ups = [self.cte(i) for i in self.upstream_ids(node)]
        if not ups:
            raise FlowCompileError(f"算子 {node.type} 缺少输入")
        return ups

    def input(self, node: FlowNode) -> str:
        """单输入算子取上游 CTE 名。"""
        ups = self.inputs(node)
        if len(ups) != 1:
            raise FlowCompileError(
                f"算子 {node.type} 只接受一个输入，实际收到 {len(ups)} 个"
            )
        return ups[0]

    # ---- 列信息 ----

    def columns_of(self, node_id: str) -> list[str]:
        cols = self.columns.get(node_id)
        if not cols:
            raise FlowCompileError(
                f"节点 {node_id} 的列信息不可用。"
                f"请在其前面接一个「字段选择」算子明确列，或改用不依赖列清单的算子。"
            )
        return cols

    def input_columns(self, node: FlowNode) -> list[str]:
        """单输入算子取上游的列清单。"""
        ups = self.upstream_ids(node)
        if len(ups) != 1:
            raise FlowCompileError(
                f"算子 {node.type} 只接受一个输入，实际收到 {len(ups)} 个"
            )
        return self.columns_of(ups[0])

    # ---- 源数据（唯一入口）----

    def scoped_source(self, node: FlowNode) -> str:
        if self.source_loader is None:
            raise FlowCompileError("source_loader 未注入，拒绝编译源节点")
        return self.source_loader(node)

    def external_source(self, node: FlowNode) -> str:
        """Python（AI）算子产出的物化表。

        AI 算子不产 SQL（调 LLM 出的结果是数据，不是表达式），
        所以由 runner 先跑 Python、把结果落成表，再把表名写回节点参数，
        这里只负责把它引用进来供下游 dbt 模型 JOIN。
        """
        table = (node.params or {}).get("materialized_table")
        if not table:
            raise FlowCompileError(
                f"算子 {node.type} 需要先由 Python 执行并物化结果，才能被下游引用"
            )
        parts = str(table).split(".")
        if len(parts) != 2:
            raise FlowCompileError(f"物化表名格式应为 schema.table，收到 {table!r}")
        try:
            return f'SELECT * FROM "{check_ident(parts[0])}"."{check_ident(parts[1])}"'
        except ValueError as exc:
            raise FlowValidationError(str(exc)) from exc
