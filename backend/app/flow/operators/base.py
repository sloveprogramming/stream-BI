"""算子基类与注册表。

每个算子 = **纯函数 + 参数模型（Pydantic）**。这一条同时买到三样东西：

- 可单测：给定参数 → 断言生成的 SQL，不需要数据库；
- 可换引擎：同一个 `compile_sql` 既喂 dbt（PostgreSQL），也喂 DuckDB 做单步预览；
- 可当工具被 LLM 调用：`operator_catalog()` 直接产出 JSON Schema，
  自然语言生成流程时把目录塞给模型即可。

参数用 Pydantic 声明 → 前端配置表单按 JSON Schema 自动渲染，
**所以加一个新算子不需要改前端**。这是这套设计能长期维护的关键。
"""
from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel

from app.flow.errors import FlowValidationError

if TYPE_CHECKING:
    from app.flow.context import CompileCtx
    from app.flow.dsl import FlowNode


class Operator(ABC):
    """算子基类。子类只需声明元数据 + 实现 `compile_sql`（或 `run_python`）。"""

    type: ClassVar[str] = ""
    category: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    Params: ClassVar[type[BaseModel]] = BaseModel
    # sql = 编译成 dbt model 的一部分；python = 由 runner 单独执行
    engine: ClassVar[Literal["sql", "python"]] = "sql"

    def parse(self, node: "FlowNode") -> BaseModel:
        """解析并校验节点参数。参数不合法在这里报错，不等到跑 SQL 才炸。"""
        try:
            return self.Params(**(node.params or {}))
        except Exception as exc:  # noqa: BLE001 —— pydantic 的异常类型很多
            raise FlowValidationError(f"算子 {self.type} 参数不合法：{exc}") from exc

    def compile_sql(self, ctx: "CompileCtx", node: "FlowNode") -> str:
        raise NotImplementedError(f"算子 {self.type} 是 Python 算子，不支持 SQL 编译")

    def run_python(
        self,
        *,
        node: "FlowNode",
        columns: list[str],
        rows: list[list],
        session=None,
    ) -> tuple[list[str], list[list]]:
        """Python 算子的执行入口：吃上游的 (列名, 行)，吐出要物化的 (列名, 行)。

        为什么不给它数据库连接：AI 算子只需要"数据 + 模型"，不需要自己取数。
        取数由执行器统一走权限裁剪器，算子这一层拿不到绕过权限的手段。
        """
        raise NotImplementedError(f"算子 {self.type} 是 SQL 算子，不支持 Python 执行")

    def output_columns(
        self, ctx: "CompileCtx", node: "FlowNode", input_columns: list[str] | None
    ) -> list[str] | None:
        """声明本节点输出的列清单，供下游算子使用。

        默认原样透传（过滤/去重这类不改变列的算子不用重写）。
        改列的算子（选择/重命名/派生/聚合/透视）要重写，
        这样 `filter → rename` 这种链路才不会因为"列信息丢了"而报错。
        """
        return input_columns


REGISTRY: dict[str, Operator] = {}


def register(op_cls: type[Operator]) -> type[Operator]:
    """算子注册装饰器。

    注意：`@register` 装饰的是**类**，而注册表里存**实例** ——
    算子都是无状态的，启动时实例化一次即可，
    否则 `op.compile_sql(ctx, node)` 会把 ctx 当成 self，报参数缺失。
    """
    if not getattr(op_cls, "type", ""):
        raise ValueError(f"{op_cls.__name__} 未声明 type")
    if op_cls.type in REGISTRY:
        raise ValueError(f"算子 type 重复：{op_cls.type}")
    REGISTRY[op_cls.type] = op_cls()
    return op_cls


def get_operator(type_: str) -> Operator:
    try:
        return REGISTRY[type_]
    except KeyError:
        raise FlowValidationError(f"未知算子：{type_!r}") from None


def operator_catalog() -> list[dict]:
    """算子目录：前端面板 + LLM 工具清单共用同一份。"""
    return [
        {
            "type": op.type,
            "category": op.category,
            "title": op.title,
            "description": op.description,
            "engine": op.engine,
            "isSource": op.type.startswith("source."),
            "isSink": op.type.startswith("sink."),
            "paramsSchema": op.Params.model_json_schema(),
        }
        for op in sorted(REGISTRY.values(), key=lambda o: (o.category, o.type))
    ]


__all__ = ["Operator", "REGISTRY", "get_operator", "operator_catalog", "register"]
