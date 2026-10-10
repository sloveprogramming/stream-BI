"""执行结果体检与有界重写的测试（纯函数 + 注入假实现）。"""

from app.assistant.verify import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_GUARD_REJECTED,
    STATUS_LLM_ERROR,
    STATUS_OK,
    build_feedback,
    inspect_result,
    query_with_retry,
    strip_sql,
)


class FakeGuardError(Exception):
    pass


# ---------------------------------------------------------------- 体检


def test_inspect_result_passes_normal_result():
    assert inspect_result(["id", "amount"], [[1, 10], [2, 20]]) is None


def test_inspect_result_flags_empty_rows():
    assert "0 行" in inspect_result(["id"], [])


def test_inspect_result_flags_all_null_column():
    assert "空值" in inspect_result(["id", "amount"], [[1, None], [2, None]])


def test_inspect_result_flags_missing_columns():
    assert inspect_result([], []) is not None


def test_strip_sql_removes_trailing_semicolons():
    assert strip_sql(" SELECT 1 ; ; ") == "SELECT 1"


# ---------------------------------------------------------------- 重写


def test_retry_succeeds_on_first_attempt():
    calls = []

    def generate(feedback):
        calls.append(feedback)
        return "SELECT 1"

    attempt = query_with_retry(generate, lambda sql: (["id"], [[1]]), max_attempts=2)
    assert attempt.ok and attempt.attempts == 1
    assert calls == [""]


def test_retry_feeds_error_back_and_recovers():
    seen = []

    def generate(feedback):
        seen.append(feedback)
        return "SELECT bad" if len(seen) == 1 else "SELECT good"

    def execute(sql):
        if sql == "SELECT bad":
            raise RuntimeError("column \"bad\" does not exist")
        return ["id"], [[1]]

    attempt = query_with_retry(generate, execute, max_attempts=2)
    assert attempt.ok and attempt.attempts == 2
    assert seen[0] == ""
    assert "does not exist" in seen[1]  # 报错原文喂回模型


def test_retry_treats_empty_result_as_soft_signal():
    seen = []

    def generate(feedback):
        seen.append(feedback)
        return "SELECT 1" if len(seen) == 1 else "SELECT 2"

    results = iter([([], []), (["id"], [[1]])])
    attempt = query_with_retry(generate, lambda sql: next(results), max_attempts=2)
    assert attempt.ok and attempt.attempts == 2
    assert "结果可疑" in seen[1]


def test_retry_classifies_guard_rejection():
    seen = []

    def generate(feedback):
        seen.append(feedback)
        return "SELECT secret FROM x"

    def execute(sql):
        raise FakeGuardError("越权字段")

    attempt = query_with_retry(generate, execute, max_attempts=2)
    assert not attempt.ok
    assert attempt.status == STATUS_GUARD_REJECTED
    assert attempt.attempts == 2
    assert "安全闸门" in seen[1]


def test_retry_stops_at_max_attempts():
    counter = {"n": 0}

    def execute(sql):
        counter["n"] += 1
        raise RuntimeError("boom")

    attempt = query_with_retry(lambda fb: "SELECT 1", execute, max_attempts=3)
    assert attempt.status == STATUS_ERROR
    assert attempt.attempts == 3
    assert counter["n"] == 3


def test_retry_reports_llm_failure():
    def generate(feedback):
        raise RuntimeError("模型超时")

    attempt = query_with_retry(generate, lambda sql: (["id"], [[1]]), max_attempts=2)
    assert attempt.status == STATUS_LLM_ERROR
    assert "模型超时" in attempt.problem


def test_retry_rejects_blank_sql():
    attempt = query_with_retry(lambda fb: "   ", lambda sql: (["id"], [[1]]))
    assert attempt.status == STATUS_LLM_ERROR
    assert "没有返回 SQL" in attempt.problem


def test_retry_keeps_last_good_payload_for_diagnostics():
    attempt = query_with_retry(
        lambda fb: "SELECT 1", lambda sql: (["id"], [[1], [2]]), max_attempts=1
    )
    assert attempt.status == STATUS_OK
    assert attempt.columns == ["id"] and len(attempt.rows) == 2


def test_build_feedback_wording_differs_by_status():
    from app.assistant.verify import SqlAttempt

    assert "安全闸门" in build_feedback(SqlAttempt(status=STATUS_GUARD_REJECTED))
    assert "报错" in build_feedback(SqlAttempt(status=STATUS_ERROR))
    assert "结果可疑" in build_feedback(SqlAttempt(status=STATUS_EMPTY))
    assert build_feedback(SqlAttempt(status=STATUS_OK)) == ""
