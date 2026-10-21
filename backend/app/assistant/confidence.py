"""问数回答的置信度判定。

## 为什么不让模型自评

让 LLM 给自己的回答打置信度，正是"自信地给出一个错的数"的来源 ——
它对"我不确定"没有可靠的自我认知，而且它的不确定感与事实正确性不相关。

所以这里全部用**确定性信号**，可解释、可单测：

| 信号 | 含义 |
|---|---|
| `metric_hit` | 问题里的业务词命中了指标字典（口径有依据） |
| `fewshot_hit` | 命中了人工验证过的样例（写法有先例） |
| `sql_attempts` | SQL 改写重试次数（>1 说明第一次就写错了） |
| `evidence_rows` | 证据行数（0 行 = 没有问题可答的数据） |
| `failed_steps` | 失败的子问题数（有子问题没答上） |
| `reflect_enough` | Agent 自检认为证据是否充分（仅作降档信号，不作升档） |

## 判定规则

从高往低走，命中即定档：

- **low**：证据行数为 0，或有子问题失败，或 reflect 明确判定不充分；
- **medium**：发生过 SQL 重试，或没命中指标字典也没命中样例（口径靠模型猜）；
- **high**：其余（SQL 一次成功、口径有依据）。

`reflect_enough=False` 会**降档但不会升档**：模型说"够了"不代表真的够，
但模型说"不够"是个有价值的警告。
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

_LEVELS = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}


@dataclass(frozen=True)
class Confidence:
    """一次回答的置信度。`reasons` 是机器可读的原因，前端原样展示。"""

    level: str
    reasons: tuple[str, ...] = ()

    @property
    def score(self) -> int:
        return _LEVELS.get(self.level, 1)

    def to_dict(self) -> dict:
        return {"level": self.level, "reasons": list(self.reasons)}


@dataclass
class ConfidenceSignals:
    """判定用的输入信号。全部由调用方从真实执行过程采集。"""

    evidence_rows: int = 0
    failed_steps: int = 0
    sql_attempts: int = 1
    metric_hit: bool = False
    fewshot_hit: bool = False
    reflect_enough: bool | None = None
    # 补充说明（例如"口径未命中字典，按字面理解处理"），原样进 reasons
    notes: list[str] = field(default_factory=list)


def assess(signals: ConfidenceSignals) -> Confidence:
    """按确定性信号判定置信度。纯函数，无 IO。"""
    reasons: list[str] = []

    # ---- low：证据层面就站不住 ----
    if signals.evidence_rows <= 0:
        reasons.append("没有任何查询结果作为证据")
    if signals.failed_steps > 0:
        reasons.append(f"有 {signals.failed_steps} 个子问题查询失败")
    if signals.reflect_enough is False:
        reasons.append("自检认为证据不足以回答目标")
    if reasons:
        return Confidence(CONFIDENCE_LOW, tuple(reasons))

    # ---- medium：能答，但过程有瑕疵 ----
    if signals.sql_attempts > 1:
        reasons.append(f"SQL 改写了 {signals.sql_attempts - 1} 次才通过校验")
    if not signals.metric_hit:
        reasons.append("未命中指标字典，口径按字面理解")
    if not signals.fewshot_hit:
        reasons.append("未命中人工验证过的样例")
    if reasons:
        return Confidence(CONFIDENCE_MEDIUM, tuple(reasons))

    # ---- high：一次成功 + 口径有依据 ----
    reasons.append("SQL 一次通过校验")
    reasons.append("口径命中指标字典")
    return Confidence(CONFIDENCE_HIGH, tuple(reasons))


def collect_signals_from_plan(plan: list, *, metric_hit: bool, fewshot_hit: bool,
                              reflect_enough: bool | None = None,
                              max_sql_attempts: int = 1) -> ConfidenceSignals:
    """从 Agent 的 plan 里采集信号（steps 的 output 里有 attempts / row_count）。"""
    evidence_rows = 0
    failed_steps = 0
    sql_attempts = 1
    for step in plan or []:
        status = step.get("status")
        row_count = int(step.get("row_count") or 0)
        if status == "done":
            evidence_rows += row_count
        elif status == "failed":
            failed_steps += 1
        # attempts 存在 step 上（verify 模块写入）
        attempts = int(step.get("attempts") or 1)
        sql_attempts = max(sql_attempts, attempts)
    return ConfidenceSignals(
        evidence_rows=evidence_rows,
        failed_steps=failed_steps,
        sql_attempts=sql_attempts,
        metric_hit=metric_hit,
        fewshot_hit=fewshot_hit,
        reflect_enough=reflect_enough,
    )
