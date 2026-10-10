"""执行结果体检 + 有界重写（Reflection / self-correction）。

研究结论（Reflect-SQL / ReSQL）：把**执行失败的原因**喂回模型让它改写，
能显著提高成功率（BIRD 上带反射 64.92% → 72.03%，修正成功率约 64%）。
但重试必须**有界**，否则模型会在同一个坑里反复打转、把成本烧光。

同时这里**刻意保守**：返回 0 行不一定错（「昨天有没有退款」本来就可能是 0 行）。
所以 0 行只作为「软信号」重试一次，并明确告诉模型这是结果可疑而非必然错误。

`generate` / `execute` 都靠注入，便于单测；本模块不依赖任何 LLM 或数据库实现。
"""
from dataclasses import dataclass, field

DEFAULT_MAX_ATTEMPTS = 2

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
STATUS_GUARD_REJECTED = "guard_rejected"
STATUS_LLM_ERROR = "llm_error"


@dataclass
class SqlAttempt:
    sql: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    status: str = STATUS_OK
    problem: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def strip_sql(sql: str) -> str:
    sql = (sql or "").strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def inspect_result(columns: list, rows: list) -> str | None:
    """结果体检：返回问题描述；None 表示看起来正常。"""
    if not columns:
        return "查询没有返回任何列"
    if not rows:
        return "查询执行成功但返回 0 行"
    for index, name in enumerate(columns):
        if all(row[index] is None for row in rows):
            return f"列 {name} 的所有取值都是空值"
    return None


def build_feedback(attempt: SqlAttempt) -> str:
    """把上一版的问题转成给模型的改写指令。"""
    if attempt.status == STATUS_GUARD_REJECTED:
        return (
            f"\n\n上一版 SQL 被安全闸门拒绝：{attempt.problem}\n"
            "请改写：只使用授权的表和字段，且必须是单条 SELECT。"
        )
    if attempt.status == STATUS_ERROR:
        return f"\n\n上一版 SQL 执行报错：{attempt.problem}\n请根据报错信息改写。"
    if attempt.status == STATUS_EMPTY:
        return (
            f"\n\n上一版 SQL 能执行，但结果可疑：{attempt.problem}\n"
            "（也有可能是业务上本就如此。）请检查筛选条件、时间范围与关联键，"
            "确认无误就保持原样，否则改写。"
        )
    return ""


def query_with_retry(generate, execute, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> SqlAttempt:
    """生成 → 执行 → 体检；不合格就把原因喂回模型重写，最多 `max_attempts` 次。

    - `generate(feedback: str) -> str`：feedback 为空串表示第一次；
    - `execute(sql) -> (columns, rows)`：抛异常表示执行失败。
    """
    feedback = ""
    last = SqlAttempt()
    for attempt_no in range(1, max_attempts + 1):
        try:
            sql = strip_sql(generate(feedback))
        except Exception as exc:  # noqa: BLE001
            return SqlAttempt(
                sql=last.sql, status=STATUS_LLM_ERROR,
                problem=f"模型调用失败：{exc}"[:300], attempts=attempt_no,
            )
        if not sql:
            return SqlAttempt(
                sql="", status=STATUS_LLM_ERROR, problem="模型没有返回 SQL", attempts=attempt_no
            )
        try:
            columns, rows = execute(sql)
        except Exception as exc:  # noqa: BLE001 —— 由调用方区分守卫拦截与执行错误
            status = _classify(exc)
            last = SqlAttempt(
                sql=sql, columns=[], rows=[], status=status,
                problem=str(exc)[:300], attempts=attempt_no,
            )
        else:
            problem = inspect_result(columns, rows)
            last = SqlAttempt(
                sql=sql, columns=columns, rows=rows,
                status=STATUS_OK if problem is None else STATUS_EMPTY,
                problem=problem or "", attempts=attempt_no,
            )
            if problem is None:
                return last
        feedback = build_feedback(last)
    return last


def _classify(exc: Exception) -> str:
    """安全闸门拒绝与普通执行错误要分开，反馈措辞不同。"""
    name = type(exc).__name__
    return STATUS_GUARD_REJECTED if "Guard" in name else STATUS_ERROR
