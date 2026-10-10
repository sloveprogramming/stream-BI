"""字段处理算子：选择 / 重命名 / 类型转换 / 派生字段。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.flow.errors import FlowCompileError
from app.flow.operators.base import Operator, register
from app.flow.sqlutil import q, q_alias, qlist, validate_expr

CAST_TYPES = ("text", "integer", "bigint", "numeric", "double precision", "date", "timestamptz", "boolean")


class SelectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(min_length=1, description="要保留的列（按此顺序输出）")


@register
class FieldSelect(Operator):
    type = "field.select"
    category = "字段"
    title = "字段选择"
    description = "只保留指定列，并确定后续算子的列顺序"
    Params = SelectParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        return f"SELECT {qlist(p.columns)} FROM {ctx.input(node)}"

    def output_columns(self, ctx, node, input_columns):
        return list(self.parse(node).columns)


class RenameParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str] = Field(min_length=1, description="旧列名 → 新列名")


@register
class FieldRename(Operator):
    type = "field.rename"
    category = "字段"
    title = "字段重命名"
    description = "统一命名（如 order_amt → order_amount）；中文展示名请填到数据集的字段注释里"
    Params = RenameParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        cols = ctx.input_columns(node)
        unknown = set(p.mapping) - set(cols)
        if unknown:
            raise FlowCompileError(f"要重命名的列不存在：{sorted(unknown)}")
        parts = [
            f"{q(c)} AS {q_alias(p.mapping[c])}" if c in p.mapping else q(c) for c in cols
        ]
        return f"SELECT {', '.join(parts)} FROM {ctx.input(node)}"

    def output_columns(self, ctx, node, input_columns):
        p = self.parse(node)
        if input_columns is None:
            return None
        return [p.mapping.get(c, c) for c in input_columns]


class CastParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    casts: dict[str, str] = Field(
        min_length=1, description=f"列 → 目标类型的映射，可选值：{', '.join(CAST_TYPES)}"
    )


@register
class FieldCast(Operator):
    type = "field.cast"
    category = "字段"
    title = "类型转换"
    description = "把列转成指定类型（常用于 ERP 里以文本存储的金额/日期）"
    Params = CastParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        cols = ctx.input_columns(node)
        unknown = set(p.casts) - set(cols)
        if unknown:
            raise FlowCompileError(f"要转换的列不存在：{sorted(unknown)}")
        for target in p.casts.values():
            if target not in CAST_TYPES:
                raise FlowCompileError(
                    f"不支持的转换类型 {target!r}，可选：{', '.join(CAST_TYPES)}"
                )
        parts = [
            f"CAST({q(c)} AS {p.casts[c]}) AS {q(c)}" if c in p.casts else q(c)
            for c in cols
        ]
        return f"SELECT {', '.join(parts)} FROM {ctx.input(node)}"

    def output_columns(self, ctx, node, input_columns):
        return input_columns


class DeriveParams(BaseModel):
    """派生字段：口径来源**二选一**。

    - `expr`  —— 裸 SQL 表达式（临时计算、一次性口径）
    - `metric` —— 引用指标字典里的口径（**口径类字段应当走这条**）

    为什么强制二选一，而不是"metric 优先、expr 兜底"：两者同时存在时，
    看代码的人无法判断实际生效的是哪个 —— **"看起来是 A、实际是 B" 的配置
    正是口径事故的来源**。宁可报错让人明确选一个。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="新列名")
    expr: str | None = Field(
        default=None,
        description="SQL 表达式，如 amount - discount；与 metric 二选一",
    )
    metric: str | None = Field(
        default=None,
        description="指标字典里的指标名（口径由字典定义，改了字典下次编译生效）；与 expr 二选一",
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "DeriveParams":
        if bool(self.expr) == bool(self.metric):
            raise ValueError(
                "expr 与 metric 必须二选一：口径类字段请引用指标（metric），"
                "临时计算才写表达式（expr）。两者都填时无法判断以谁为准。"
            )
        return self


def _check_metric_columns(ctx, node, metric) -> None:
    """指标口径用到的列必须在上游输出里。

    不拦的话，错误要等到 dbt 执行时才由 PostgreSQL 抛出"列不存在"，
    而且报的是物理列名 —— 排错人要自己反推是哪条流程把列弄丢了。
    编译期拦住，直接说清"哪个指标、缺哪些列、上游现在有什么"。

    上游列信息推导不出来时（`columns` 为空）不做判断：
    没有依据的拦截会造成"明明能跑却被拒"，比漏拦更糟。
    """
    upstream_ids = ctx.upstream_ids(node)
    if len(upstream_ids) != 1:
        return
    upstream = ctx.columns.get(upstream_ids[0])
    if not upstream:
        return
    known = set(upstream)
    missing = [c for c in metric.columns if c not in known]
    if missing:
        raise FlowCompileError(
            f"指标「{metric.label}」({metric.name}) 的口径 {metric.expr} 需要列 "
            f"{', '.join(missing)}，但上游节点 {upstream_ids[0]} 的输出里没有。"
            f"该上游现有列：{', '.join(upstream)}。"
            f"请在流程里保留这些列（例如调整「字段选择」），或换用其它指标。"
        )


@register
class FieldDerive(Operator):
    type = "field.derive"
    category = "字段"
    title = "派生字段"
    description = (
        "算出一个新列。口径类字段（毛利率、动销率…）请填 metric 引用指标字典，"
        "临时计算才填 expr；两者只能填一个。"
    )
    Params = DeriveParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.metric is not None:
            # 未知指标 / 数据集未上架在这里报错，并带出可用指标清单
            metric = ctx.metric(p.metric)
            _check_metric_columns(ctx, node, metric)
            expr = metric.expr
        else:
            validate_expr(p.expr)
            expr = p.expr
        return f"SELECT *, ({expr}) AS {q_alias(p.name)} FROM {ctx.input(node)}"

    def output_columns(self, ctx, node, input_columns):
        if input_columns is None:
            return None
        return [*input_columns, self.parse(node).name]
