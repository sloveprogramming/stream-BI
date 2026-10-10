"""统计基线判定的纯函数测试。"""
from app.monitor.detect import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    RULE_PCT_CHANGE,
    RULE_ZSCORE,
    evaluate,
)


def test_pct_change_flags_large_drop():
    verdict = evaluate(80.0, [100.0], rule=RULE_PCT_CHANGE, threshold=10, direction=DIRECTION_DOWN)
    assert verdict.is_anomaly
    assert verdict.score == -20.0
    assert "环比" in verdict.reason


def test_pct_change_ignores_small_move():
    verdict = evaluate(98.0, [100.0], threshold=10)
    assert not verdict.is_anomaly


def test_direction_up_ignores_a_drop():
    verdict = evaluate(50.0, [100.0], threshold=10, direction=DIRECTION_UP)
    assert not verdict.is_anomaly


def test_direction_both_flags_either_way():
    assert evaluate(60.0, [100.0], threshold=10).is_anomaly
    assert evaluate(140.0, [100.0], threshold=10).is_anomaly


def test_no_history_is_not_an_anomaly():
    verdict = evaluate(10.0, [], rule=RULE_PCT_CHANGE)
    assert not verdict.is_anomaly
    assert "积累基线" in verdict.reason


def test_zero_baseline_is_not_an_anomaly():
    verdict = evaluate(10.0, [0.0], rule=RULE_PCT_CHANGE)
    assert not verdict.is_anomaly
    assert "无法计算" in verdict.reason


def test_missing_current_value_is_not_an_anomaly():
    assert not evaluate(None, [1.0, 2.0]).is_anomaly


def test_zscore_needs_enough_samples():
    verdict = evaluate(999.0, [10.0, 11.0], rule=RULE_ZSCORE, threshold=2)
    assert not verdict.is_anomaly
    assert "样本不足" in verdict.reason


def test_zscore_flags_outlier():
    history = [10.0, 10.2, 9.8, 10.1, 9.9]
    verdict = evaluate(30.0, history, rule=RULE_ZSCORE, threshold=2, direction=DIRECTION_UP)
    assert verdict.is_anomaly
    assert verdict.score > 2


def test_zscore_without_variance_is_not_an_anomaly():
    verdict = evaluate(5.0, [5.0, 5.0, 5.0], rule=RULE_ZSCORE, threshold=2)
    assert not verdict.is_anomaly
    assert "没有波动" in verdict.reason


def test_history_ignores_none_values():
    verdict = evaluate(120.0, [None, 100.0, None], threshold=10)
    assert verdict.is_anomaly
    assert verdict.baseline == 100.0
