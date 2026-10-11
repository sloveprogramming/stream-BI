"""质量校验算子：把「数据合不合格」变成数据流程里的一个节点。

三类规则（质量六维度里画布能表达的部分）：

- `not_null`  **完整性**：这些列不得为空；
- `unique`    **唯一性**：这些列组合不得重复（窗口函数计数，支持组合键）；
- `rules`     **有效性**：值域 / 格式（结构化 op+value，**不收裸 SQL 片段** —— 注入的口子不开）。

输出 `qa_passed`（是否通过）+ `qa_reason`（违规原因，人能读懂）两个附加列，
**不删行、不中止** —— 要拦住脏数据，下游接一个「过滤 qa_passed = true」即可，
与 `dedup.semantic`「标记不删行」是同一个设计哲学：删行不可逆，标记可解释。

两类规则职责分离（spec 显式场景）：值域规则对 NULL 宽容（NULL 是"未知"不是"违规"，
PostgreSQL 三值逻辑的自然结果），空值是否算问题由 `not_null` 负责。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.flow.errors import FlowCompileError
from app.flow.operators.base import Operator, register
from app.flow.sqlutil import q, q_alias, sql_literal

_OP_SQL = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "like": "LIKE"}
_OP_LABEL = {
    "eq": "应等于",
    "ne": "应不等于",
    "gt": "应大于",
    "ge": "应 ≥",
    "lt": "应 <",
    "le": "应 ≤",
    "like": "应匹配",
    "in": "应属于",
}


class QualityRule(BaseModel):
    """一条有效性规则。op 与 value 的匹配在解析阶段就拦（spec 显式场景）。"""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="被检查的列")
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "like", "in"] = Field(description="比较操作符")
    value: Any = Field(description="比较值：in 用非空列表，其余用标量")

    @model_validator(mode="after")
    def _value_matches_op(self):
        if self.op == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("op=in 时 value 必须是非空列表")
        elif isinstance(self.value, list):
            raise ValueError(f"op={self.op} 时 value 应是标量，不是列表")
        return self


class QualityAssertParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    not_null: list[str] = Field(default_factory=list, description="完整性：这些列不得为空")
    unique: list[list[str]] = Field(
        default_factory=list, description="唯一性：这些列组合不得重复（单列写 [[\"col\"]]）"
    )
    rules: list[QualityRule] = Field(default_factory=list, description="有效性：值域/格式规则")
    passed_column: str = Field(default="qa_passed", description="通过标记写进哪个新列")
    reason_column: str = Field(default="qa_reason", description="违规原因写进哪个新列")

    @model_validator(mode="after")
    def _sane_config(self):
        if not (self.not_null or self.unique or self.rules):
            raise ValueError("至少配置一条规则（not_null / unique / rules 之一），否则校验没有意义")
        if self.passed_column == self.reason_column:
            raise ValueError("passed_column 与 reason_column 不能同名")
        return self


def _safe_text(text: str) -> str:
    """违规描述要进 SQL 字符串字面量 —— 单引号必须翻倍，否则就是注入口。"""
    return text.replace("'", "''")


def _show(value: Any) -> str:
    """违规描述里展示的值：数字直接显示，字符串去引号（描述本身在 SQL 字面量里）。"""
    return str(value)


@register
class QualityAssert(Operator):
    type = "quality.assert"
    category = "质量"
    title = "质量校验"
    description = (
        "按规则逐行检查数据质量（非空 / 组合唯一 / 值域），输出是否通过与违规原因，"
        "不删行、不中止；要拦住不合规的行，下游接一个「过滤 qa_passed = true」即可"
    )
    Params = QualityAssertParams

    def compile_sql(self, ctx, node) -> str:
        p = self.parse(node)
        columns = ctx.input_columns(node)

        # 输出列与上游冲突 → 宁可报错也不静默覆盖（覆盖不会报错，只会给错的结果）
        for name in (p.passed_column, p.reason_column):
            if name in columns:
                raise FlowCompileError(
                    f"算子 {self.type} 的输出列 {name!r} 与上游已有列同名 —— 会覆盖原列，"
                    f"请换一个输出列名"
                )
        # 被引用的列必须存在（编译期报，别等 dbt 才炸）
        referenced = list(p.not_null) + [c for group in p.unique for c in group]
        referenced += [r.column for r in p.rules]
        missing = sorted({c for c in referenced if c not in columns})
        if missing:
            raise FlowCompileError(
                f"算子 {self.type} 引用了上游不存在的列：{missing}；上游实际列：{columns}"
            )

        violations = [
            f"CASE WHEN {q(col)} IS NULL THEN '{_safe_text(col + ' 不得为空')}' END"
            for col in p.not_null
        ]
        for group in p.unique:
            keys = ", ".join(q(c) for c in group)
            label = "、".join(group)
            violations.append(
                f"CASE WHEN COUNT(*) OVER (PARTITION BY {keys}) > 1 "
                f"THEN '{_safe_text(label + ' 组合重复')}' END"
            )
        violations.extend(self._rule_violation(rule) for rule in p.rules)

        # CONCAT_WS 跳过 NULL：通过的规则不产描述；全过时结果为空串
        reason_expr = (
            "COALESCE(NULLIF(TRIM(BOTH '；' FROM "
            f"CONCAT_WS('；', {', '.join(violations)})), ''), '')"
        )
        # reason 与 passed 必须出自同一份条件 —— passed 由 reason 推导，
        # 结构上不可能出现"标记通过却带原因"的矛盾（design 决策 1）
        inner = f"SELECT *, {reason_expr} AS {q_alias(p.reason_column)} FROM {ctx.input(node)}"
        return f"SELECT *, ({q_alias(p.reason_column)} = '') AS {q_alias(p.passed_column)} FROM ({inner}) _qa"

    def _rule_violation(self, rule: QualityRule) -> str:
        """一条有效性规则 → 「违规时才出现的描述」。

        违规 = 表达式结果为 FALSE；NULL 不算违规（design 决策 2），
        用 `IS FALSE` 让三值逻辑自然工作。
        """
        col = q(rule.column)
        if rule.op == "in":
            values = ", ".join(sql_literal(v) for v in rule.value)
            expr = f"({col} IN ({values}))"
            detail = f"{rule.column} {_OP_LABEL['in']} ({', '.join(_show(v) for v in rule.value)})"
        else:
            expr = f"({col} {_OP_SQL[rule.op]} {sql_literal(rule.value)})"
            detail = f"{rule.column} {_OP_LABEL[rule.op]} {_show(rule.value)}"
        return f"CASE WHEN {expr} IS FALSE THEN '{_safe_text(detail + '（不满足）')}' END"

    def output_columns(self, ctx, node, input_columns: list[str]) -> list[str]:
        p = self.parse(node)
        return [*input_columns, p.passed_column, p.reason_column]
