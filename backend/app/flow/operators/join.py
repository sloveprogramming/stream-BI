"""关联算子：JOIN 与 UNION。"""
from pydantic import BaseModel, ConfigDict, Field

from app.flow.errors import FlowCompileError
from app.flow.operators.base import Operator, register
from app.flow.sqlutil import q, sanitize_ident


class MergeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_id: str = Field(description="作为左表的上游节点 id")
    right_id: str = Field(description="作为右表的上游节点 id")
    how: str = Field(default="left", description="连接方式：inner / left / right / full")
    on: list[tuple[str, str]] = Field(min_length=1, description="关联条件 [(左列, 右列)]")
    right_columns: list[str] = Field(
        default_factory=list, description="要从右表带过来的列（留空 = 只带左表）"
    )
    suffix: str | None = Field(
        default=None, description="右表列加后缀，避免与左表重名（如 _供应商）"
    )


@register
class JoinMerge(Operator):
    type = "join.merge"
    category = "关联"
    title = "关联合并"
    description = "把两个上游按条件 JOIN；明确指定左/右表与要带的列，避免出现重复列名"
    Params = MergeParams

    _HOWS = ("inner", "left", "right", "full")

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        if p.how not in self._HOWS:
            raise FlowCompileError(f"how 只能是 {self._HOWS}，收到 {p.how!r}")

        ups = ctx.dsl.upstream_ids(node.id)
        if sorted([p.left_id, p.right_id]) != sorted(ups):
            raise FlowCompileError(
                f"left_id/right_id 必须正好是本节点的两个上游 {ups}，"
                f"收到 [{p.left_id!r}, {p.right_id!r}]"
            )

        on = " AND ".join(f"l.{q(a)} = r.{q(b)}" for a, b in p.on)
        select = ["l.*"]
        for c in p.right_columns:
            alias = f"{c}{p.suffix}" if p.suffix else c
            select.append(f"r.{q(c)} AS {q(alias)}")
        return (
            f"SELECT {', '.join(select)} "
            f"FROM {ctx.cte(p.left_id)} AS l "
            f"{p.how.upper()} JOIN {ctx.cte(p.right_id)} AS r ON {on}"
        )

    def output_columns(self, ctx, node, input_columns):
        p = self.parse(node)
        left = ctx.columns.get(p.left_id)
        if left is None:
            return None
        return [*left, *[(f"{c}{p.suffix}" if p.suffix else c) for c in p.right_columns]]


class UnionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[str] = Field(min_length=2, description="要纵向合并的上游节点 id（按顺序）")
    distinct: bool = Field(default=True, description="True = UNION 去重；False = UNION ALL 保留全部")


@register
class JoinUnion(Operator):
    type = "join.union"
    category = "关联"
    title = "纵向合并"
    description = "把多个结构相同的上游上下拼起来（去重或保留全部）"
    Params = UnionParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        ups = ctx.dsl.upstream_ids(node.id)
        if sorted(p.inputs) != sorted(ups):
            raise FlowCompileError(
                f"inputs 必须正好是本节点的全部上游 {ups}，收到 {p.inputs!r}"
            )
        kw = "UNION" if p.distinct else "UNION ALL"
        return f" {kw} ".join(f"SELECT * FROM {ctx.cte(i)}" for i in p.inputs)

    def output_columns(self, ctx, node, input_columns):
        p = self.parse(node)
        return ctx.columns.get(p.inputs[0])


def _unique_alias(base: str, used: set[str]) -> str:
    """透视列名去重：`a-b` 与 `a_b` 规范化后会撞，撞了就加序号。"""
    name = sanitize_ident(base)
    if name not in used:
        used.add(name)
        return name
    i = 2
    while f"{name}_{i}" in used:
        i += 1
    used.add(f"{name}_{i}")
    return f"{name}_{i}"
