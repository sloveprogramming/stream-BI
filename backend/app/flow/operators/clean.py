"""清洗算子：过滤 / 去重 / 空值填充 / 字符串清洗。

「过滤」和「去重」是这套编排最常被点名的两个算子，单测覆盖最厚。
"""
from pydantic import BaseModel, ConfigDict, Field

from app.flow.errors import FlowCompileError
from app.flow.operators.base import Operator, register
from app.flow.sqlutil import q, qlist, sql_literal, validate_expr


class FilterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expr: str = Field(description="过滤条件表达式，如 status != '已取消' AND amount > 0")


@register
class CleanFilter(Operator):
    type = "clean.filter"
    category = "清洗"
    title = "过滤"
    description = "按条件筛掉不要的行（表达式经 sqlglot 校验，禁止多语句堆叠）"
    Params = FilterParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        validate_expr(p.expr)
        return f"SELECT * FROM {ctx.input(node)} WHERE {p.expr}"


class DedupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str] = Field(min_length=1, description="判重的列（主键或业务唯一键）")
    keep: str = Field(
        default="last", description="保留策略：first 保留最早 / last 保留最新 / none 只去完全重复行"
    )
    order_by: str | None = Field(
        default=None, description="判定'最早/最新'依据的列，通常是 update_time"
    )


@register
class CleanDedup(Operator):
    type = "clean.dedup"
    category = "清洗"
    title = "去重"
    description = "按指定列去重，可保留最新一条；不指定排序依据时保留任意一条"
    Params = DedupParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.keep not in ("first", "last", "none"):
            raise FlowCompileError(f"keep 只能是 first/last/none，收到 {p.keep!r}")
        keys = qlist(p.keys)
        src = ctx.input(node)

        if p.keep == "none":
            # 完全重复行去重，不关心"保留哪条"
            return f"SELECT DISTINCT * FROM {src}"

        # DISTINCT ON 要求 ORDER BY 以 DISTINCT ON 的表达式开头
        if p.order_by:
            if p.order_by in p.keys:
                raise FlowCompileError(
                    f"排序依据 {p.order_by!r} 同时出现在判重列里，无法判定先后，请换一列"
                )
            direction = "ASC" if p.keep == "first" else "DESC"
            order = f"{keys}, {q(p.order_by)} {direction}"
        else:
            order = keys
        return f"SELECT DISTINCT ON ({keys}) * FROM {src} ORDER BY {order}"


class FillNullParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fills: dict[str, str | int | float | bool | None] = Field(
        min_length=1, description="列 → 填充值（空值时用该值替代）"
    )


@register
class CleanFillNull(Operator):
    type = "clean.fill_null"
    category = "清洗"
    title = "空值填充"
    description = "把 NULL 替换成固定值（如 0 或 '未知'）"
    Params = FillNullParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        cols = ctx.input_columns(node)
        unknown = set(p.fills) - set(cols)
        if unknown:
            raise FlowCompileError(f"要填充的列不存在：{sorted(unknown)}")
        parts = [
            f"COALESCE({q(c)}, {sql_literal(p.fills[c])}) AS {q(c)}"
            if c in p.fills
            else q(c)
            for c in cols
        ]
        return f"SELECT {', '.join(parts)} FROM {ctx.input(node)}"


class TrimParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(min_length=1, description="要清洗的文本列")
    mode: str = Field(
        default="trim",
        description="trim 去首尾空格 / lower 转小写 / upper 转大写 / collapse_space 多个空白压成一个",
    )


@register
class CleanTrim(Operator):
    type = "clean.trim"
    category = "清洗"
    title = "文本清洗"
    description = "去空格、统一大小写、压缩连续空白（ERP 文本字段脏值的常规处理）"
    Params = TrimParams

    _MODES = ("trim", "lower", "upper", "collapse_space")

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.mode not in self._MODES:
            raise FlowCompileError(f"mode 只能是 {self._MODES}，收到 {p.mode!r}")
        cols = ctx.input_columns(node)
        unknown = set(p.columns) - set(cols)
        if unknown:
            raise FlowCompileError(f"要清洗的列不存在：{sorted(unknown)}")

        def _expr(col: str) -> str:
            if p.mode == "trim":
                return f"BTRIM({q(col)})"
            if p.mode == "lower":
                return f"LOWER({q(col)})"
            if p.mode == "upper":
                return f"UPPER({q(col)})"
            return f"REGEXP_REPLACE({q(col)}, '\\s+', ' ', 'g')"

        parts = [
            f"{_expr(c)} AS {q(c)}" if c in p.columns else q(c) for c in cols
        ]
        return f"SELECT {', '.join(parts)} FROM {ctx.input(node)}"
