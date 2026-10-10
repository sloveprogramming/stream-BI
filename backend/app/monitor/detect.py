"""统计基线判定（纯函数：不碰数据库、不碰模型，便于测试）。

只用最朴素的两种规则，**刻意不上时序模型** —— 先验证「无人值守跑监控」这件事
本身有没有价值，有价值再谈模型。

- `pct_change`：与上一期比，涨跌幅超过阈值即命中；
- `zscore`：与历史均值比，偏离超过 N 倍标准差即命中（需要足够样本）。
"""
import statistics
from dataclasses import dataclass

RULE_PCT_CHANGE = "pct_change"
RULE_ZSCORE = "zscore"

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_BOTH = "both"

MIN_ZSCORE_SAMPLES = 3


@dataclass
class Verdict:
    is_anomaly: bool
    reason: str = ""
    score: float = 0.0
    baseline: float | None = None


def _decide(score: float, reason: str, baseline: float | None, threshold: float, direction: str) -> Verdict:
    if direction == DIRECTION_UP:
        hit = score >= threshold
    elif direction == DIRECTION_DOWN:
        hit = score <= -threshold
    else:
        hit = abs(score) >= threshold
    return Verdict(bool(hit), reason, round(float(score), 4), baseline)


def evaluate(
    current: float | None,
    history: list,
    rule: str = RULE_PCT_CHANGE,
    threshold: float = 20.0,
    direction: str = DIRECTION_BOTH,
) -> Verdict:
    """`history` 为**按时间升序**的历史值（不含 current）。

    基线不足时返回 `is_anomaly=False` 并说明原因 —— 宁可漏报，不要用不足的样本瞎报。
    """
    if current is None:
        return Verdict(False, "当前值缺失")
    values = [float(v) for v in (history or []) if v is not None]

    if rule == RULE_ZSCORE:
        if len(values) < MIN_ZSCORE_SAMPLES:
            return Verdict(False, f"历史样本不足（{len(values)}/{MIN_ZSCORE_SAMPLES}），先积累基线")
        mean = statistics.fmean(values)
        stdev = statistics.pstdev(values)
        if stdev == 0:
            return Verdict(False, "历史值没有波动，无法计算 z 分数", baseline=mean)
        score = (float(current) - mean) / stdev
        return _decide(score, f"z 分数 {score:+.2f}（历史均值 {mean:.4g}）", mean, threshold, direction)

    if not values:
        return Verdict(False, "还没有历史值，先积累基线")
    baseline = values[-1]
    if baseline == 0:
        return Verdict(False, "上一期值为 0，环比无法计算", baseline=baseline)
    pct = (float(current) - baseline) / abs(baseline) * 100
    return _decide(pct, f"环比 {pct:+.2f}%（上期 {baseline:.4g}）", baseline, threshold, direction)
