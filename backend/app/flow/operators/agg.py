"""聚合计算算子：分组聚合 / 窗口函数 / 行列转换。"""
from pydantic import BaseModel, ConfigDict, Field

from app.flow.errors import FlowCompileError
from app.flow.operators.base import Operator, register
from app.flow.operators.join import _unique_alias
from app.flow.sqlutil import q, q_alias, qlist, sql_literal

AGG_FUNCS = ("sum", "count", "count_distinct", "avg", "min", "max")


class GroupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: list[str] = Field(default_factory=list, description="分组列（留空 = 整体聚合）")
    aggs: dict[str, str] = Field(
        min_length=1, description=f"列 → 聚合函数（{', '.join(AGG_FUNCS)}）"
    )


@register
class AggGroup(Operator):
    type = "agg.group"
    category = "聚合"
    title = "分组聚合"
    description = "按维度汇总指标（如 按日 × 仓库 汇总销售额）"
    Params = GroupParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        for col, fn in p.aggs.items():
            if fn not in AGG_FUNCS:
                raise FlowCompileError(
                    f"不支持的聚合函数 {fn!r}（列 {col}），可选：{', '.join(AGG_FUNCS)}"
                )
        parts = [q(c) for c in p.group_by]
        for col, fn in p.aggs.items():
            alias = f"{col}_{fn}"
            if fn == "count_distinct":
                parts.append(f"COUNT(DISTINCT {q(col)}) AS {q(alias)}")
            else:
                parts.append(f"{fn.upper()}({q(col)}) AS {q(alias)}")

        sql = f"SELECT {', '.join(parts)} FROM {ctx.input(node)}"
        if p.group_by:
            sql += f" GROUP BY {qlist(p.group_by)}"
        return sql

    def output_columns(self, ctx, node, input_columns):
        p = self.parse(node)
        return [*p.group_by, *[f"{c}_{fn}" for c, fn in p.aggs.items()]]


class WindowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    func: str = Field(
        description="窗口函数：sum / avg / count / min / max / row_number / rank / lag / lead"
    )
    column: str | None = Field(default=None, description="参与计算的列（row_number/rank 不用填）")
    partition_by: list[str] = Field(default_factory=list, description="分区列")
    order_by: list[str] = Field(default_factory=list, description="排序列")
    name: str = Field(description="结果列名")
    descending: bool = Field(default=False, description="排序是否倒序")


@register
class AggWindow(Operator):
    type = "agg.window"
    category = "聚合"
    title = "窗口计算"
    description = "在不减少行数的前提下算累计/排名/环比（如 累计销售额、按仓库内排名）"
    Params = WindowParams

    _FUNCS = ("sum", "avg", "count", "min", "max", "row_number", "rank", "dense_rank", "lag", "lead")

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.func not in self._FUNCS:
            raise FlowCompileError(f"不支持的窗口函数 {p.func!r}，可选：{', '.join(self._FUNCS)}")

        arg = f"({q(p.column)})" if p.column else "()"
        clauses: list[str] = []
        if p.partition_by:
            clauses.append(f"PARTITION BY {qlist(p.partition_by)}")
        if p.order_by:
            direction = " DESC" if p.descending else ""
            clauses.append("ORDER BY " + ", ".join(f"{q(c)}{direction}" for c in p.order_by))
        over = (" " + " ".join(clauses)) if clauses else ""

        return (
            f"SELECT *, {p.func.upper()}{arg} OVER ({over.strip()}) AS {q_alias(p.name)} "
            f"FROM {ctx.input(node)}"
        )

    def output_columns(self, ctx, node, input_columns):
        if input_columns is None:
            return None
        return [*input_columns, self.parse(node).name]


class PivotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: list[str] = Field(min_length=1, description="行维度列")
    pivot_column: str = Field(description="要展开成列的字段")
    values: list[str] = Field(min_length=1, description="要展开的具体值（需显式列出）")
    value_column: str = Field(description="被聚合的数值列")
    aggfunc: str = Field(default="sum", description="聚合函数：sum / count / avg / min / max")


@register
class AggPivot(Operator):
    type = "agg.pivot"
    category = "聚合"
    title = "行列转换"
    description = "把某个字段的值展开成列（如 按仓库横向展开）；用 FILTER 实现，不依赖扩展"
    Params = PivotParams

    _FUNCS = ("sum", "count", "avg", "min", "max")

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.aggfunc not in self._FUNCS:
            raise FlowCompileError(
                f"不支持的聚合函数 {p.aggfunc!r}，可选：{', '.join(self._FUNCS)}"
            )
        fn = p.aggfunc.upper()
        used: set[str] = set()
        parts = [q(c) for c in p.index]
        for v in p.values:
            alias = _unique_alias(f"{p.pivot_column}_{v}", used)
            parts.append(
                f"{fn}({q(p.value_column)}) FILTER "
                f"(WHERE {q(p.pivot_column)} = {sql_literal(v)}) AS {q(alias)}"
            )
        return (
            f"SELECT {', '.join(parts)} FROM {ctx.input(node)} "
            f"GROUP BY {qlist(p.index)}"
        )

    def output_columns(self, ctx, node, input_columns):
        p = self.parse(node)
        used: set[str] = set()
        return [*p.index, *[_unique_alias(f"{p.pivot_column}_{v}", used) for v in p.values]]
