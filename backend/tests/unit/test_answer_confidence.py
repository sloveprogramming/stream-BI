"""问数回答置信度：三档判定与原因可解释性。

核心主张：**置信度由确定性信号判定，不交给模型自评**。
所以这个文件里没有一处 mock LLM —— 全部是纯函数断言。
"""
from app.assistant.confidence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Confidence,
    ConfidenceSignals,
    assess,
    collect_signals_from_plan,
)


# ---------------------------------------------------------------- 三档判定


def test_high_when_everything_clean():
    conf = assess(ConfidenceSignals(
        evidence_rows=12, failed_steps=0, sql_attempts=1,
        metric_hit=True, fewshot_hit=True,
    ))
    assert conf.level == CONFIDENCE_HIGH
    assert conf.reasons


def test_low_when_no_evidence_rows():
    conf = assess(ConfidenceSignals(evidence_rows=0, metric_hit=True, fewshot_hit=True))
    assert conf.level == CONFIDENCE_LOW
    assert any("没有任何查询结果" in r for r in conf.reasons)


def test_low_when_some_step_failed():
    conf = assess(ConfidenceSignals(
        evidence_rows=5, failed_steps=2, metric_hit=True, fewshot_hit=True
    ))
    assert conf.level == CONFIDENCE_LOW
    assert any("2 个子问题" in r for r in conf.reasons)


def test_reflect_not_enough_forces_low():
    """自检说证据不足 → 降档（模型说"够了"不作为升档依据）。"""
    conf = assess(ConfidenceSignals(
        evidence_rows=50, reflect_enough=False, metric_hit=True, fewshot_hit=True
    ))
    assert conf.level == CONFIDENCE_LOW
    assert any("自检" in r for r in conf.reasons)


def test_reflect_enough_does_not_upgrade():
    """reflect_enough=True 不会把本来该低档的抬上去。"""
    conf = assess(ConfidenceSignals(
        evidence_rows=0, reflect_enough=True, metric_hit=True, fewshot_hit=True
    ))
    assert conf.level == CONFIDENCE_LOW


def test_medium_when_sql_retried():
    conf = assess(ConfidenceSignals(
        evidence_rows=3, sql_attempts=3, metric_hit=True, fewshot_hit=True
    ))
    assert conf.level == CONFIDENCE_MEDIUM
    assert any("改写" in r for r in conf.reasons)


def test_medium_when_no_metric_hit():
    """没命中指标字典 = 口径靠模型猜，降一档。"""
    conf = assess(ConfidenceSignals(
        evidence_rows=3, metric_hit=False, fewshot_hit=True
    ))
    assert conf.level == CONFIDENCE_MEDIUM
    assert any("指标字典" in r for r in conf.reasons)


def test_medium_when_no_fewshot():
    conf = assess(ConfidenceSignals(
        evidence_rows=3, metric_hit=True, fewshot_hit=False
    ))
    assert conf.level == CONFIDENCE_MEDIUM
    assert any("样例" in r for r in conf.reasons)


# ---------------------------------------------------------------- 结构与序列化


def test_score_ordering():
    assert Confidence(CONFIDENCE_HIGH).score > Confidence(CONFIDENCE_MEDIUM).score
    assert Confidence(CONFIDENCE_MEDIUM).score > Confidence(CONFIDENCE_LOW).score


def test_to_dict_is_json_ready():
    d = Confidence(CONFIDENCE_HIGH, ("a", "b")).to_dict()
    assert d == {"level": "high", "reasons": ["a", "b"]}


def test_reasons_never_empty():
    """任何一档都要给出原因 —— 用户看到 low 得知道为什么。"""
    cases = [
        ConfidenceSignals(evidence_rows=0),
        ConfidenceSignals(evidence_rows=5, metric_hit=False, fewshot_hit=False),
        ConfidenceSignals(evidence_rows=5, metric_hit=True, fewshot_hit=True),
    ]
    for signals in cases:
        assert assess(signals).reasons, f"{signals} 没有给出原因"


# ---------------------------------------------------------------- 从 plan 采集


def test_collect_from_plan_sums_rows_and_counts_failures():
    plan = [
        {"status": "done", "row_count": 10, "attempts": 1},
        {"status": "done", "row_count": 5, "attempts": 2},
        {"status": "failed", "row_count": 0, "attempts": 2},
    ]
    s = collect_signals_from_plan(plan, metric_hit=True, fewshot_hit=True)
    assert s.evidence_rows == 15
    assert s.failed_steps == 1
    assert s.sql_attempts == 2


def test_collect_from_empty_plan():
    s = collect_signals_from_plan([], metric_hit=False, fewshot_hit=False)
    assert s.evidence_rows == 0
    assert s.failed_steps == 0
    assert s.sql_attempts == 1


def test_collect_handles_missing_attempts():
    """老数据没有 attempts 字段时按 1 次算，不报错。"""
    s = collect_signals_from_plan(
        [{"status": "done", "row_count": 1}], metric_hit=True, fewshot_hit=True
    )
    assert s.sql_attempts == 1


def test_plan_with_rows_gives_high_when_clean():
    plan = [{"status": "done", "row_count": 8, "attempts": 1, "metric_hit": True,
             "fewshot_hit": True}]
    s = collect_signals_from_plan(
        plan, metric_hit=True, fewshot_hit=True, reflect_enough=True
    )
    assert assess(s).level == CONFIDENCE_HIGH
