"""评测框架的纯函数测试（不依赖数据库与模型）。"""
from decimal import Decimal

from app.assistant.eval import (
    STATUS_MISMATCH,
    STATUS_OK,
    CaseResult,
    normalize_value,
    parse_cases,
    render,
    results_match,
    summarize,
)


def test_normalize_value_unifies_numbers():
    assert normalize_value(10) == normalize_value(10.0) == normalize_value(Decimal("10"))
    assert normalize_value(10.5) == "10.5"
    assert normalize_value(None) == ""
    assert normalize_value(True) == "True"
    assert normalize_value("  华东 ") == "华东"


def test_results_match_ignores_row_order():
    ok, note = results_match([[1, "华东"], [2, "华北"]], [[2, "华北"], [1, "华东"]])
    assert ok and note == ""


def test_results_match_detects_row_count_diff():
    ok, note = results_match([[1]], [[1], [2]])
    assert not ok and "行数不同" in note


def test_results_match_falls_back_on_column_order():
    ok, note = results_match([["华东", 3]], [[3, "华东"]])
    assert ok and "列顺序不同" in note


def test_results_match_detects_real_mismatch():
    ok, note = results_match([[1, "华东"]], [[1, "华南"]])
    assert not ok and "不一致" in note


def test_summarize_counts_and_tags():
    cases = [
        {"id": "a", "question": "q1", "tags": ["聚合"]},
        {"id": "b", "question": "q2", "tags": ["聚合", "多表"]},
        {"id": "c", "question": "q3", "tags": ["多表"]},
    ]
    results = [
        CaseResult(case_id="a", question="q1", status=STATUS_OK),
        CaseResult(case_id="b", question="q2", status=STATUS_MISMATCH, detail="行数不同"),
        CaseResult(case_id="c", question="q3", status=STATUS_OK),
    ]
    report = summarize(cases, results, 1200)
    assert report.total == 3 and report.correct == 2
    assert report.accuracy == 0.6667
    assert report.by_status == {STATUS_OK: 2, STATUS_MISMATCH: 1}
    assert report.by_tag["聚合"] == {"total": 2, "correct": 1, "accuracy": 0.5}
    assert report.by_tag["多表"] == {"total": 2, "correct": 1, "accuracy": 0.5}
    assert [f["id"] for f in report.failures] == ["b"]


def test_summarize_handles_empty():
    report = summarize([], [], 0)
    assert report.total == 0 and report.accuracy == 0.0 and report.failures == []


def test_summarize_defaults_tag_for_untagged_case():
    report = summarize([{"id": "a", "question": "q"}], [CaseResult("a", "q", STATUS_OK)], 0)
    assert "未分类" in report.by_tag


def test_render_contains_key_numbers():
    cases = [{"id": "a", "question": "q1", "tags": ["聚合"]}]
    results = [CaseResult("a", "q1", STATUS_OK)]
    text = render(summarize(cases, results, 500))
    assert "准确率：100.0%" in text
    assert "按标签" in text


def test_parse_cases_and_filter_by_tag():
    text = (
        '[{"id":"a","question":"q","tags":["聚合"]},'
        '{"id":"b","question":"q","tags":["多表"]}]'
    )
    assert len(parse_cases(text)) == 2
    assert [c["id"] for c in parse_cases(text, "多表")] == ["b"]
    assert parse_cases(text, "不存在") == []


def test_parse_cases_rejects_non_array():
    import pytest

    with pytest.raises(ValueError):
        parse_cases('{"id": "a"}')
