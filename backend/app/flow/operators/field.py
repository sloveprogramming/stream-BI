"""字段处理算子：选择 / 重命名 / 类型转换 / 派生字段。"""
from pydantic import BaseModel, ConfigDict, Field

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
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="新列名")
    expr: str = Field(description="SQL 表达式，如 amount - discount")


@register
class FieldDerive(Operator):
    type = "field.derive"
    category = "字段"
    title = "派生字段"
    description = "用表达式算出一个新列；口径类字段建议直接引用指标字典里的定义"
    Params = DeriveParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        validate_expr(p.expr)
        return f"SELECT *, ({p.expr}) AS {q_alias(p.name)} FROM {ctx.input(node)}"

    def output_columns(self, ctx, node, input_columns):
        if input_columns is None:
            return None
        return [*input_columns, self.parse(node).name]
