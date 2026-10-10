"""口径歧义检测：**确定性地**判断一个问题是否踩到了多口径。

为什么不用大模型判断：design 里写得很直白 ——「让模型在 prompt 里自行判断该用哪个口径，
那正是当前的问题所在，它会自信地给出一个错的数」。
所以口径歧义必须由字典本身判定，模型只负责在被问到时理解用户的选择。

判定规则（纯字符串，可单测、可解释）：

1. 问题里出现的「业务词」命中了几个指标？
2. 少于两个 → 没歧义，照常查；
3. 命中多个 → 再看问题里有没有**区分词**（只属于其中某一个指标的别名）。
   有区分词说明用户已经说清了，不算歧义；
4. 没有区分词 → 歧义，必须反问。

例（「销售额」同时是支付口径与回款口径的同义词）：

    问「上月销售额」      → 命中两个、无区分词 → 反问
    问「上月回款销售额」  → 「回款」只属于回款口径 → 不歧义
    问「毛利率是多少」    → 只命中一个 → 不歧义
"""
from __future__ import annotations

from app.metrics.loader import Metric, MetricDictionary

# 单字别名太容易误伤（指标名/同义词里出现"额""率"这类字），不参与匹配
_MIN_ALIAS_LEN = 2


def _matched_metrics(dictionary: MetricDictionary, text: str) -> list[Metric]:
    hits: list[Metric] = []
    for metric in dictionary.metrics:
        if metric.name in dictionary.unavailable:
            continue  # 不可用的指标不该参与歧义判定，否则会把问题引到死路上
        aliases = [
            a.casefold()
            for a in metric.aliases()
            if a and len(a) >= _MIN_ALIAS_LEN
        ]
        if any(alias in text for alias in aliases):
            hits.append(metric)
    return hits


def ambiguous_metrics(
    dictionary: MetricDictionary, question: str
) -> tuple[Metric, ...]:
    """返回问题踩到的多个口径；没有歧义时返回空元组。"""
    text = (question or "").casefold()
    if not text or not dictionary:
        return ()

    hits = _matched_metrics(dictionary, text)
    if len(hits) < 2:
        return ()

    alias_sets = {
        metric.name: {
            a.casefold() for a in metric.aliases() if a and len(a) >= _MIN_ALIAS_LEN
        }
        for metric in hits
    }
    for metric in hits:
        exclusive = alias_sets[metric.name] - {
            alias
            for other in hits
            if other is not metric
            for alias in alias_sets[other.name]
        }
        # 问题里出现了只有它能解释的词 → 用户已经说清了，不必再问
        if any(word in text for word in exclusive):
            return ()
    return tuple(hits)


def clarification_for(metrics: tuple[Metric, ...]) -> dict:
    """把歧义指标变成一次反问，结构与 Agent 的 `clarification` 保持一致。"""
    options: list[str] = []
    for metric in metrics:
        note = metric.description or metric.expr
        options.append(f"{metric.label}（{note}）")
    return {
        "question": "这个问题涉及多个口径，请确认要采用哪一个：",
        "options": options[:4],
    }


__all__ = ["ambiguous_metrics", "clarification_for"]
