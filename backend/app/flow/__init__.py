"""数据编排层（flow）。

对外只暴露四样东西，其余是内部实现：

    from app.flow.dsl import FlowDSL          # 流程定义（也是 LLM 的结构化输出 schema）
    from app.flow.compile import compile_flow # 编译成 dbt model（权限在这里落地）
    from app.flow.operators import operator_catalog   # 算子目录（喂前端 / 喂 LLM）
"""

from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput  # noqa: F401
from app.flow.errors import (  # noqa: F401
    FlowCompileError,
    FlowError,
    FlowExecutionError,
    FlowPermissionError,
    FlowValidationError,
)

__all__ = [
    "FlowDSL",
    "FlowEdge",
    "FlowNode",
    "FlowOutput",
    "FlowError",
    "FlowValidationError",
    "FlowCompileError",
    "FlowPermissionError",
    "FlowExecutionError",
]
