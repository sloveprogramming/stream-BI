"""数据质量巡检：三类规则的判定、失败隔离、安全（标识符白名单）。

重点验证：

1. **新鲜度**：超过阈值命中，未超过不命中，空表跳过；
2. **行数环比**：首次无基线不判定（宁可漏报），有基线才算涨跌幅；
3. **空值率**：超过阈值命中；
4. **配置错误不炸链路**：字段不存在等落成 failed，而不是抛出去；
5. **安全**：字段名/表名必须过 `check_ident`，拼不出注入。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import (
    RESULT_ANOMALY,
    RESULT_FAILED,
    RESULT_OK,
    Dataset,
    DatasetGrant,
    DataQualityResult,
    DataQualityRule,
    User,
)
from app.quality import (
    QualityCheckError,
    _quote_table,
    check_rule,
)
from app.quality_runner import run_rule

TABLE = "ods.dq_src"


@pytest.fixture
def dq_env(pg_session_factory, pg_engine):
    """一张可写的源表 + 一个数据集 + 一个 owner 用户。"""
    suffix = uuid.uuid4().hex[:8]
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            f"CREATE TABLE IF NOT EXISTS {TABLE} "
            "(id integer PRIMARY KEY, region text, amount numeric, modified timestamp)"
        ))
        conn.execute(sa.text(f"DELETE FROM {TABLE}"))
        conn.execute(sa.text(
            f"INSERT INTO {TABLE} VALUES "
            "(1,'华东',10,now() - interval '1 hour'),"
            "(2,'华东',20,now() - interval '1 hour'),"
            "(3,'华北',NULL,now() - interval '1 hour')"
        ))

    with pg_session_factory() as s:
        owner = User(username=f"dqu_{suffix}", password_hash="x")
        ds = Dataset(name=f"dq_{suffix}", source_table=TABLE,
                     pk_columns=["id"], columns=["id", "region", "amount", "modified"])
        s.add_all([owner, ds])
        s.flush()
        # 巡检跑在 owner 身份下，所以 owner 必须有授权（否则规则一律 failed）
        s.add(DatasetGrant(user_id=owner.id, dataset_id=ds.id, row_rules=[]))
        s.commit()
        ids = {"owner": owner.id, "ds": ds.id, "suffix": suffix}

    yield ids, pg_session_factory, pg_engine

    with pg_session_factory() as s:
        rule_ids = [
            r[0] for r in s.query(DataQualityRule.id).filter_by(dataset_id=ids["ds"]).all()
        ]
        # 顺序：结果 → 规则 → 审计（审计引用 owner 用户）→ 授权 → 数据集 → 用户
        if rule_ids:
            s.query(DataQualityResult).filter(
                DataQualityResult.rule_id.in_(rule_ids)
            ).delete(synchronize_session=False)
            s.query(DataQualityRule).filter(
                DataQualityRule.id.in_(rule_ids)
            ).delete(synchronize_session=False)
        from app.models import AuditLog

        s.query(AuditLog).filter_by(user_id=ids["owner"]).delete(
            synchronize_session=False
        )
        s.query(DatasetGrant).filter_by(dataset_id=ids["ds"]).delete(
            synchronize_session=False
        )
        s.query(Dataset).filter_by(id=ids["ds"]).delete(synchronize_session=False)
        s.query(User).filter_by(id=ids["owner"]).delete(synchronize_session=False)
        s.commit()


def _rule(ids, **kw) -> DataQualityRule:
    """造一条规则对象（不落库 —— 判定逻辑不需要 id）。"""
    base = dict(
        id=999999,
        tenant_id=1,
        name=f"r_{uuid.uuid4().hex[:6]}",
        dataset_id=ids["ds"],
        owner_user_id=ids["owner"],
        rule_type="freshness",
        threshold=24.0,
        target_field=None,
        timestamp_field=None,
        direction="both",
        cron="0 7 * * *",
        enabled=True,
        last_status="",
    )
    base.update(kw)
    return DataQualityRule(**base)


# ---------------------------------------------------------------- 安全


def test_quote_table_rejects_injection():
    """表名两段都要过标识符白名单 —— 拼不出注入。"""
    with pytest.raises(QualityCheckError):
        _quote_table("ods.t; DROP TABLE users")
    with pytest.raises(QualityCheckError):
        _quote_table("ods.a b")
    with pytest.raises(QualityCheckError):
        _quote_table("noschema")


def test_quote_table_quotes_both_parts():
    assert _quote_table("ods.sales_order") == '"ods"."sales_order"'


# ---------------------------------------------------------------- 新鲜度


def test_freshness_ok_when_recent(dq_env):
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="freshness", timestamp_field="modified", threshold=24.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_OK
    assert out.value is not None and out.value < 2


def test_freshness_anomaly_when_stale(dq_env):
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        # 阈值设成 0.1 小时（6 分钟），数据是 1 小时前的 → 命中
        out = check_rule(
            _rule(ids, rule_type="freshness", timestamp_field="modified", threshold=0.1),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_ANOMALY
    assert out.value > 0.1
    assert "超过阈值" in out.message


def test_freshness_uses_fallback_field(dq_env):
    """没指定 timestamp_field 时，从常见更新时间列里挑 modified。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="freshness", threshold=24.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status in (RESULT_OK, RESULT_ANOMALY)
    assert out.error is None


def test_freshness_fails_when_no_timestamp_field(dq_env):
    """数据集里没有可识别的更新时间列 → failed（并说明怎么办）。"""
    ids, sf, engine = dq_env
    with sf() as s:
        ds = s.get(Dataset, ids["ds"])
        ds.columns = ["id", "region", "amount"]  # 去掉 modified
        s.commit()
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="freshness", threshold=24.0),
            session=s, pg_engine=engine, owner=owner,
        )
        ds.columns = ["id", "region", "amount", "modified"]
        s.commit()
    assert out.status == RESULT_FAILED
    assert "timestamp_field" in (out.error or "")


# ---------------------------------------------------------------- 空值率


def test_null_rate_ok_below_threshold(dq_env):
    """amount 有 1/3 为 NULL（33%），阈值 50% → OK。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="null_rate", target_field="amount", threshold=50.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_OK
    assert 30 < out.value < 36


def test_null_rate_anomaly_above_threshold(dq_env):
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="null_rate", target_field="amount", threshold=10.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_ANOMALY
    assert "空值率" in out.message


def test_null_rate_requires_target_field(dq_env):
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="null_rate", target_field=None, threshold=10.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_FAILED
    assert "target_field" in (out.error or "")


def test_null_rate_rejects_unknown_field(dq_env):
    """字段不在数据集列里 → failed（配置错误要明确报出来）。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="null_rate", target_field="nope", threshold=10.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_FAILED
    assert "nope" in (out.error or "")


def test_null_rate_rejects_injection_in_field(dq_env):
    """字段名带引号/分号 → failed，不会拼进 SQL。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="null_rate", target_field='amount" ; DROP TABLE x --',
                  threshold=10.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_FAILED


# ---------------------------------------------------------------- 行数环比


def test_row_count_first_run_has_no_baseline(dq_env):
    """首次巡检没有基线 → 只记录当前值，不判定（宁可漏报）。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        out = check_rule(
            _rule(ids, rule_type="row_count", threshold=20.0),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_OK
    assert out.value == 3          # 当前绝对行数
    assert out.baseline is None
    assert "首次巡检" in out.message


def test_row_count_detects_drop(dq_env):
    """已有基线（上期 10 行）→ 本期 3 行，跌幅 70% 命中。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        rule = _rule(ids, rule_type="row_count", threshold=20.0)
        s.add(User(username=f"tmp_{uuid.uuid4().hex[:6]}", password_hash="x"))
        # 先落一条历史结果当基线
        rule_row = DataQualityRule(
            tenant_id=1, name=rule.name, dataset_id=ids["ds"],
            owner_user_id=ids["owner"], rule_type="row_count", threshold=20.0,
        )
        s.add(rule_row)
        s.commit()
        s.add(DataQualityResult(rule_id=rule_row.id, status=RESULT_OK, value=10.0))
        s.commit()
        rule.id = rule_row.id
        out = check_rule(rule, session=s, pg_engine=engine, owner=owner)
    assert out.status == RESULT_ANOMALY
    assert out.baseline == 10.0
    assert out.change_pct is not None and out.change_pct < -60


def test_row_count_direction_up_only_ignores_drop(dq_env):
    """direction=up 时，只关注上涨，跌了不报。"""
    ids, sf, engine = dq_env
    with sf() as s:
        owner = s.get(User, ids["owner"])
        rule_row = DataQualityRule(
            tenant_id=1, name=f"up_{uuid.uuid4().hex[:6]}", dataset_id=ids["ds"],
            owner_user_id=ids["owner"], rule_type="row_count",
            threshold=20.0, direction="up",
        )
        s.add(rule_row)
        s.commit()
        s.add(DataQualityResult(rule_id=rule_row.id, status=RESULT_OK, value=10.0))
        s.commit()
        out = check_rule(
            _rule(ids, rule_type="row_count", threshold=20.0, direction="up",
                  id=rule_row.id),
            session=s, pg_engine=engine, owner=owner,
        )
    assert out.status == RESULT_OK


# ---------------------------------------------------------------- 落库与审计


def test_run_rule_persists_result_and_updates_status(dq_env):
    """跑一条规则要落结果、回填 last_status、异常时记审计。"""
    ids, sf, engine = dq_env
    with sf() as s:
        rule = DataQualityRule(
            tenant_id=1, name=f"run_{uuid.uuid4().hex[:6]}", dataset_id=ids["ds"],
            owner_user_id=ids["owner"], rule_type="null_rate",
            target_field="amount", threshold=10.0,
        )
        s.add(rule)
        s.commit()
        rid = rule.id

    result = run_rule(rid, session_factory=sf, pg_engine=engine)
    assert result.status == RESULT_ANOMALY
    assert result.value is not None

    with sf() as s:
        fresh = s.get(DataQualityRule, rid)
        assert fresh.last_status == RESULT_ANOMALY
        assert fresh.last_run_at is not None
        # 异常要留下审计
        from app.models import AuditLog

        logs = s.query(AuditLog).filter_by(action="quality.anomaly").all()
        assert any((log.detail or {}).get("rule_id") == rid for log in logs)


def test_run_rule_bad_config_does_not_raise(dq_env):
    """配置错误落成 failed，不抛异常 —— 调度线程不能被一条坏规则炸掉。"""
    ids, sf, engine = dq_env
    with sf() as s:
        rule = DataQualityRule(
            tenant_id=1, name=f"bad_{uuid.uuid4().hex[:6]}", dataset_id=ids["ds"],
            owner_user_id=ids["owner"], rule_type="null_rate",
            target_field="does_not_exist", threshold=10.0,
        )
        s.add(rule)
        s.commit()
        rid = rule.id

    result = run_rule(rid, session_factory=sf, pg_engine=engine)
    assert result.status == RESULT_FAILED
    assert result.error


def test_run_rule_missing_rule_raises(dq_env):
    """规则不存在属于调用方错误，要抛出来（不是数据问题）。"""
    ids, sf, engine = dq_env
    with pytest.raises(QualityCheckError):
        run_rule(999999999, session_factory=sf, pg_engine=engine)
