"""数据质量巡检执行器：三类规则的检查逻辑。

## 安全

表名/字段名全部走 `access.scoped_query.check_ident`（ASCII 标识符白名单），
值全部参数化。**不接受任何拼接进来的用户输入** —— 规则的 target_field /
timestamp_field 虽然是管理员填的，也要当不可信处理：填错了报错，
而不是拼进 SQL。

## 取数口径

巡检跑在 `owner_user_id` 的身份下（与监控任务同一设计）：行级规则决定
"巡检看到的是哪部分数据"。这样巡检结果与业务人员看到的一致 ——
否则巡检说"行数正常"，业务人员看到的是空表。

新鲜度优先用规则指定的 `timestamp_field`；没指定时回退到常见更新时间列
（modified/update_time/gmt_modified/updated_at）里第一个存在的。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text

from app.access.grants import resolve_grant
from app.access.scoped_query import check_ident
from app.models import (
    RESULT_ANOMALY,
    RESULT_FAILED,
    RESULT_OK,
    RULE_FRESHNESS,
    RULE_NULL_RATE,
    RULE_ROW_COUNT,
)
from app.models.quality import DataQualityResult, DataQualityRule

# freshness 回退用的更新时间字段候选（按优先级）
_FRESHNESS_FALLBACK_FIELDS = ("modified", "update_time", "gmt_modified", "updated_at")

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "like": "LIKE"}


class QualityCheckError(Exception):
    """巡检规则配置不合法（字段不存在等）。属于配置错误，要明确报出来。"""


@dataclass
class CheckOutcome:
    status: str
    value: float | None = None
    baseline: float | None = None
    # 环比变化百分比（仅 row_count 规则有值）
    change_pct: float | None = None
    message: str = ""
    error: str | None = None
    elapsed_ms: int = 0


# ---------------------------------------------------------------- 工具


def _quote_table(source_table: str) -> str:
    """把 `ods.sales_order` 转成 `"ods"."sales_order"`，两段都过标识符校验。

    `check_ident` 抛 `ValueError`，这里统一翻成 `QualityCheckError` ——
    调用方只需要处理一种异常，而且"配置写错了"与"程序出错"要能区分开。
    """
    parts = source_table.split(".")
    if len(parts) != 2:
        raise QualityCheckError(
            f"source_table 必须是 schema.table，实际是 {source_table!r}"
        )
    try:
        schema, table = (check_ident(p) for p in parts)
    except ValueError as exc:
        raise QualityCheckError(str(exc)) from exc
    return f'"{schema}"."{table}"'


def _ident(name: str, what: str) -> str:
    """校验一个标识符（字段名等），失败时给可读的原因。"""
    try:
        return check_ident(name)
    except ValueError as exc:
        raise QualityCheckError(f"{what} {name!r} 不是合法标识符：{exc}") from exc


def _op_sql(op: str) -> str:
    if op not in _OPS:
        raise QualityCheckError(f"非法操作符: {op!r}")
    return _OPS[op]


def _pick_freshness_field(rule: DataQualityRule, columns: list[str]) -> str:
    """新鲜度字段：规则指定优先，否则从常见名里挑一个存在的。"""
    if rule.timestamp_field:
        return _ident(rule.timestamp_field, "timestamp_field")
    for candidate in _FRESHNESS_FALLBACK_FIELDS:
        if candidate in (columns or []):
            return _ident(candidate, "时间列")
    raise QualityCheckError(
        "未指定 timestamp_field，且数据集里没有可识别的更新时间列"
        f"（候选：{', '.join(_FRESHNESS_FALLBACK_FIELDS)}）。"
        "请在规则里显式指定 timestamp_field。"
    )


def _scoped_where(rule: DataQualityRule, session, owner) -> tuple[str, dict]:
    """行级规则的 WHERE 片段与参数。无授权时抛错（由调用方处理成 failed）。"""
    grant = resolve_grant(session, owner, rule.dataset_id)
    if grant is None:
        raise QualityCheckError("规则 owner 对该数据集没有授权，无法巡检")
    params: dict[str, object] = {}
    clauses: list[str] = []
    for i, r in enumerate(getattr(grant, "row_rules", None) or []):
        field = _ident(r["field"], "行级规则字段")
        key = f"dq_r_{i}"
        params[key] = r["value"]
        clauses.append(f'"{field}" {_op_sql(r["op"])} :{key}')
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _scalar(pg_engine, sql: str, params: dict):
    with pg_engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


# ---------------------------------------------------------------- 三类规则


def _check_freshness(rule, *, table, where, params, columns, pg_engine,
                     session=None) -> CheckOutcome:
    """最新数据的更新时间距现在多少小时。超过 threshold 小时即告警。"""
    field = _pick_freshness_field(rule, columns)
    sql = f'SELECT EXTRACT(EPOCH FROM (now() - MAX("{field}")))/3600 FROM {table}{where}'
    hours = _scalar(pg_engine, sql, params)
    if hours is None:
        return CheckOutcome(RESULT_OK, value=None, message="表里没有数据，跳过新鲜度检查")
    hours = float(hours)
    threshold = float(rule.threshold)
    if hours > threshold:
        return CheckOutcome(
            RESULT_ANOMALY,
            value=round(hours, 2),
            message=f"最新数据距现在 {hours:.1f} 小时，超过阈值 {threshold:.0f} 小时",
        )
    return CheckOutcome(
        RESULT_OK,
        value=round(hours, 2),
        message=f"最新数据距现在 {hours:.1f} 小时（阈值 {threshold:.0f} 小时）",
    )


def _check_row_count(rule, *, table, where, params, columns, pg_engine,
                     session=None) -> CheckOutcome:
    """当前行数 vs 上一期行数的环比涨跌幅。超过 threshold% 即告警。

    `value` 存**当前行数**（不是百分比）—— 下一期要拿它做基线，
    存百分比就丢了绝对值，环比会一期比一期虚。
    """
    sql = f"SELECT COUNT(*) FROM {table}{where}"
    current = int(_scalar(pg_engine, sql, params) or 0)

    baseline = _last_row_count(rule, session)
    if baseline is None:
        # 第一次跑没有基线：只记录当前值，不判定（与 monitor 的"宁可漏报"一致）
        return CheckOutcome(
            RESULT_OK,
            value=float(current),
            baseline=None,
            change_pct=None,
            message=f"当前行数 {current}（首次巡检，无基线，不判定）",
        )

    if baseline == 0:
        return CheckOutcome(
            RESULT_OK,
            value=float(current),
            baseline=baseline,
            change_pct=None,
            message=f"上期行数为 0，环比无法计算（当前 {current}）",
        )

    pct = (current - baseline) / abs(baseline) * 100
    threshold = float(rule.threshold)
    hit = (
        pct >= threshold
        if rule.direction == "up"
        else (pct <= -threshold if rule.direction == "down" else abs(pct) >= threshold)
    )
    detail = f"行数环比 {pct:+.2f}%（上期 {baseline:.0f} → 本期 {current}）"
    if hit:
        return CheckOutcome(
            RESULT_ANOMALY,
            value=float(current),
            baseline=baseline,
            change_pct=round(pct, 2),
            message=f"{detail}，超过阈值 {threshold:.0f}%",
        )
    return CheckOutcome(
        RESULT_OK,
        value=float(current),
        baseline=baseline,
        change_pct=round(pct, 2),
        message=detail,
    )


def _check_null_rate(rule, *, table, where, params, columns, pg_engine,
                     session=None) -> CheckOutcome:
    """指定字段的空值率。超过 threshold% 即告警。"""
    if not rule.target_field:
        raise QualityCheckError("null_rate 规则必须指定 target_field")
    field = _ident(rule.target_field, "target_field")
    if columns and field not in columns:
        raise QualityCheckError(
            f"字段 {field!r} 不在数据集列里：{', '.join(columns)}"
        )
    sql = (
        f'SELECT COUNT(*) FILTER (WHERE "{field}" IS NULL)::float '
        f"/ NULLIF(COUNT(*), 0) * 100 FROM {table}{where}"
    )
    rate = _scalar(pg_engine, sql, params)
    if rate is None:
        return CheckOutcome(RESULT_OK, value=None, message="表里没有数据，跳过空值率检查")
    rate = float(rate)
    threshold = float(rule.threshold)
    if rate > threshold:
        return CheckOutcome(
            RESULT_ANOMALY,
            value=round(rate, 2),
            message=f"字段 {field} 空值率 {rate:.2f}%，超过阈值 {threshold:.0f}%",
        )
    return CheckOutcome(
        RESULT_OK,
        value=round(rate, 2),
        message=f"字段 {field} 空值率 {rate:.2f}%（阈值 {threshold:.0f}%）",
    )


# ---------------------------------------------------------------- 入口


def check_rule(rule: DataQualityRule, *, session, pg_engine, owner) -> CheckOutcome:
    """执行一条巡检规则，返回结果（不落库 —— 落库由调用方决定，便于测试）。"""
    started = time.perf_counter()
    try:
        from app.models import Dataset

        dataset = session.get(Dataset, rule.dataset_id)
        if dataset is None:
            raise QualityCheckError(f"数据集不存在: {rule.dataset_id}")
        where, params = _scoped_where(rule, session, owner)
        table = _quote_table(dataset.source_table)
        checker = _CHECKERS[rule.rule_type]
        outcome = checker(
            rule,
            table=table,
            where=where,
            params=params,
            columns=list(dataset.columns or []),
            pg_engine=pg_engine,
            session=session,
        )
    except QualityCheckError as exc:
        return CheckOutcome(RESULT_FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 —— 巡检失败不能炸掉调度线程
        return CheckOutcome(RESULT_FAILED, error=f"{type(exc).__name__}: {exc}")
    outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return outcome


def _last_row_count(rule: DataQualityRule, session) -> float | None:
    """上一期巡检记录的行数（取最近一次 value，即当时的 COUNT）。"""
    if session is None:
        return None
    row = (
        session.query(DataQualityResult)
        .filter_by(rule_id=rule.id)
        .order_by(DataQualityResult.id.desc())
        .first()
    )
    if row is None or row.value is None:
        return None
    return float(row.value)


_CHECKERS = {
    RULE_FRESHNESS: _check_freshness,
    RULE_ROW_COUNT: _check_row_count,
    RULE_NULL_RATE: _check_null_rate,
}
