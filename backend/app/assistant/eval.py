"""数据助手评测：以「执行结果一致」判定对错（execution accuracy）。

为什么用执行结果而不是比对 SQL 文本：同一条正确查询可以有无数种写法，
比文本会把「对的」判成错的。做法与 BIRD/Spider 的 EX 口径一致。

用法（在 backend 目录下，用项目 venv）：

    python -m app.assistant.eval --dump-datasets            # 先看有哪些数据集与字段，便于出题
    python -m app.assistant.eval --cases evals/cases.json   # 跑全量
    python -m app.assistant.eval --cases evals/cases.json --tag 聚合
    python -m app.assistant.eval --cases evals/cases.json --user zhangsan --no-save

用例格式（JSON 数组，`golden_sql` 用**数据集名**写，不要写 ods. 物理表名）：

    [
      {
        "id": "sales_001",
        "question": "上周华东区的销售额是多少",
        "golden_sql": "SELECT sum(amount) FROM sales_order WHERE region = '华东'",
        "tags": ["单表", "聚合"]
      }
    ]

判对口径：
- 行数相同、列数相同、行内容集合相同（行序无关）；
- 若行内容集合不一致，再试一次「行内列序无关」的比较，通过则记为 ok 并在 detail 说明；
- 生成 SQL 被安全闸门拦下 → `guard_rejected`；执行报错 → `exec_error`。
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.assistant.llm import ROLE_PLANNER, ROLE_SQL, generate_sql, model_for
from app.assistant.prompts import system_prompt, user_prompt
from app.assistant.schema_context import build_schema_context
from app.assistant.tools import SqlGuardError, run_scoped_sql
from app.settings import get_settings

EVAL_TIMEOUT_SECONDS = 30

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_EXEC_ERROR = "exec_error"
STATUS_GUARD_REJECTED = "guard_rejected"
STATUS_LLM_ERROR = "llm_error"


@dataclass
class CaseResult:
    case_id: str
    question: str
    status: str
    generated_sql: str = ""
    golden_sql: str = ""
    detail: str = ""
    elapsed_ms: int = 0


@dataclass
class EvalReport:
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    duration_ms: int = 0
    by_status: dict = None  # type: ignore[assignment]
    by_tag: dict = None  # type: ignore[assignment]
    failures: list = None  # type: ignore[assignment]

    def __post_init__(self):
        self.by_status = self.by_status or {}
        self.by_tag = self.by_tag or {}
        self.failures = self.failures or []


# ---------------------------------------------------------------- 结果比对


def normalize_value(value):
    """把结果值归一化成字符串，避免 10 / 10.0 / Decimal('10') 被当成不同。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value).strip()


def _rows_to_multiset(rows, sort_cells: bool = False):
    out = []
    for row in rows:
        cells = [normalize_value(v) for v in row]
        if sort_cells:
            cells = sorted(cells)
        out.append(tuple(cells))
    return sorted(out)


def results_match(model_rows, golden_rows) -> tuple[bool, str]:
    """返回 (是否一致, 说明)。行序无关；列序不一致时做一次宽松比对。"""
    if len(model_rows) != len(golden_rows):
        return False, f"行数不同：模型 {len(model_rows)} / 期望 {len(golden_rows)}"
    if model_rows and golden_rows:
        if len(model_rows[0]) != len(golden_rows[0]):
            return False, f"列数不同：模型 {len(model_rows[0])} / 期望 {len(golden_rows[0])}"
    if _rows_to_multiset(model_rows) == _rows_to_multiset(golden_rows):
        return True, ""
    if _rows_to_multiset(model_rows, True) == _rows_to_multiset(golden_rows, True):
        return True, "列顺序不同，但结果集合一致"
    return False, "结果集合不一致"


# ---------------------------------------------------------------- 执行


def _strip_sql(sql: str) -> str:
    sql = (sql or "").strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def run_case(case: dict, user, session_factory, pg_engine, max_rows: int) -> CaseResult:
    started = time.perf_counter()
    question = case.get("question", "")
    result = CaseResult(case_id=case.get("id", "?"), question=question, status=STATUS_OK)
    result.golden_sql = _strip_sql(case.get("golden_sql", ""))
    try:
        schema = build_schema_context(session_factory, pg_engine, user)
    except Exception as exc:  # noqa: BLE001
        result.status = STATUS_EXEC_ERROR
        result.detail = f"构建 schema 失败：{exc}"[:300]
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    try:
        generated = _strip_sql(
            generate_sql(system_prompt(), user_prompt(schema["prompt"], question))
        )
        result.generated_sql = generated
    except Exception as exc:  # noqa: BLE001
        result.status = STATUS_LLM_ERROR
        result.detail = f"模型调用失败：{exc}"[:300]
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    try:
        _, model_rows = run_scoped_sql(
            pg_engine, generated, schema["tables"], max_rows, EVAL_TIMEOUT_SECONDS
        )
    except SqlGuardError as exc:
        result.status = STATUS_GUARD_REJECTED
        result.detail = str(exc)[:300]
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result
    except Exception as exc:  # noqa: BLE001
        result.status = STATUS_EXEC_ERROR
        result.detail = f"执行失败：{exc}"[:300]
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    try:
        _, golden_rows = run_scoped_sql(
            pg_engine, result.golden_sql, schema["tables"], max_rows, EVAL_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        result.status = STATUS_EXEC_ERROR
        result.detail = f"期望 SQL 执行失败（请检查用例本身）：{exc}"[:300]
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    ok, note = results_match(model_rows, golden_rows)
    result.status = STATUS_OK if ok else STATUS_MISMATCH
    result.detail = note
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


# ---------------------------------------------------------------- 汇总

def summarize(cases: list[dict], results: list[CaseResult], duration_ms: int) -> EvalReport:
    report = EvalReport(
        total=len(results),
        correct=sum(1 for r in results if r.status == STATUS_OK),
        duration_ms=duration_ms,
    )
    report.accuracy = round(report.correct / report.total, 4) if report.total else 0.0

    by_status: dict = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    report.by_status = by_status

    tag_total: dict = {}
    tag_ok: dict = {}
    for case, r in zip(cases, results):
        for tag in case.get("tags") or ["未分类"]:
            tag_total[tag] = tag_total.get(tag, 0) + 1
            if r.status == STATUS_OK:
                tag_ok[tag] = tag_ok.get(tag, 0) + 1
    report.by_tag = {
        tag: {
            "total": tag_total[tag],
            "correct": tag_ok.get(tag, 0),
            "accuracy": round(tag_ok.get(tag, 0) / tag_total[tag], 4),
        }
        for tag in sorted(tag_total)
    }
    report.failures = [
        {
            "id": r.case_id,
            "status": r.status,
            "question": r.question,
            "detail": r.detail,
            "generated_sql": r.generated_sql,
            "golden_sql": r.golden_sql,
        }
        for r in results
        if r.status != STATUS_OK
    ]
    return report


def render(report: EvalReport) -> str:
    lines = [
        f"用例总数：{report.total}    正确：{report.correct}    准确率：{report.accuracy:.1%}"
        f"    耗时：{report.duration_ms / 1000:.1f}s",
        "",
        "按结果状态：",
    ]
    for status, count in sorted(report.by_status.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {status:<16} {count}")
    if report.by_tag:
        lines.append("")
        lines.append("按标签：")
        for tag, stat in report.by_tag.items():
            lines.append(
                f"  {tag:<16} {stat['correct']}/{stat['total']}  ({stat['accuracy']:.1%})"
            )
    if report.failures:
        lines.append("")
        lines.append("失败明细：")
        for item in report.failures:
            lines.append(f"  [{item['status']}] {item['id']}  {item['question']}")
            if item["detail"]:
                lines.append(f"      原因：{item['detail']}")
            if item["generated_sql"]:
                lines.append(f"      模型：{item['generated_sql'][:200]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口

def parse_cases(text: str, tag: str | None = None) -> list[dict]:
    """解析用例 JSON 文本，可按标签过滤。纯函数，便于单测（不碰文件系统）。"""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("用例文件必须是 JSON 数组")
    if tag:
        data = [c for c in data if tag in (c.get("tags") or [])]
    return data


def load_cases(path: str, tag: str | None = None) -> list[dict]:
    return parse_cases(Path(path).read_text(encoding="utf-8"), tag)


def resolve_user(session_factory, username: str | None):
    from app.models import User

    with session_factory() as session:
        if username:
            user = session.query(User).filter_by(username=username).first()
            if user is None:
                raise SystemExit(f"用户不存在：{username}")
            return user
        user = session.query(User).filter_by(is_admin=True).order_by(User.id).first()
        if user is None:
            raise SystemExit("没有管理员账号，请用 --user 指定评测账号")
        return user


def dump_datasets(session_factory, pg_engine, user) -> None:
    schema = build_schema_context(session_factory, pg_engine, user)
    print(f"当前可见数据集 {len(schema['tables'])} 个（账号：{user.username}）")
    for t in schema["tables"]:
        print(f"\n- {t['name']}  ({t['source_table']})  字段 {len(t['columns'])} 个")
        print(f"  {', '.join(t['columns'])}")
        if t["row_rules"]:
            print(f"  行级规则：{t['row_rules']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BIsql 数据助手评测")
    ap.add_argument("--cases", default="evals/cases.json", help="用例 JSON 路径")
    ap.add_argument("--user", help="用哪个账号的权限跑（默认取第一个管理员）")
    ap.add_argument("--tag", help="只跑带该标签的用例")
    ap.add_argument("--dump-datasets", action="store_true", help="打印可见数据集与字段后退出")
    ap.add_argument("--no-save", action="store_true", help="不写入 eval_runs 表")
    args = ap.parse_args(argv)

    from app.db import make_engine, make_session_factory

    settings = get_settings()
    pg_engine = make_engine(settings.database_url)
    session_factory = make_session_factory(pg_engine)
    try:
        user = resolve_user(session_factory, args.user)
        if args.dump_datasets:
            dump_datasets(session_factory, pg_engine, user)
            return 0

        cases = load_cases(args.cases, args.tag)
        if not cases:
            print("没有可跑的用例。先用 --dump-datasets 看看有哪些数据集，再填 evals/cases.json")
            return 1

        started = time.perf_counter()
        results = [
            run_case(case, user, session_factory, pg_engine, settings.assistant_max_rows)
            for case in cases
        ]
        duration_ms = int((time.perf_counter() - started) * 1000)
        report = summarize(cases, results, duration_ms)
        print(render(report))

        if not args.no_save:
            _save(report, session_factory, user)
        return 0
    finally:
        pg_engine.dispose()


def _save(report: EvalReport, session_factory, user) -> None:
    from app.models import EvalRun

    try:
        with session_factory() as session:
            session.add(
                EvalRun(
                    username=getattr(user, "username", None),
                    sql_model=model_for(ROLE_SQL),
                    planner_model=model_for(ROLE_PLANNER),
                    total=report.total,
                    correct=report.correct,
                    accuracy=report.accuracy,
                    duration_ms=report.duration_ms,
                    detail={
                        "by_status": report.by_status,
                        "by_tag": report.by_tag,
                        "failures": report.failures,
                    },
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 —— 落库失败不影响评测结果输出
        print(f"\n[警告] 评测结果未写入 eval_runs：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
