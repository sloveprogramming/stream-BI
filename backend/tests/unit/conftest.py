"""flow 测试的公共夹具。

造一个最小可用环境：一个源数据集 + owner/他人/管理员三个用户 + 授权，
用完把它自己造出来的东西（表、流程、授权、生成文件）全部清干净 ——
本地库是共用的，不清理会污染后续测试。
"""
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models import (
    AuditLog,
    DataFlow,
    DataFlowVersion,
    Dataset,
    DatasetGrant,
    FlowRun,
    User,
)

SOURCE_TABLE = "ods.flow_src"
GENERATED_DIR = Path(__file__).resolve().parents[2] / "dbt" / "models" / "generated"


@pytest.fixture
def flow_env(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]

    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {SOURCE_TABLE} "
                "(order_id integer PRIMARY KEY, warehouse text, amount numeric)"
            )
        )
        conn.execute(sa.text(f"DELETE FROM {SOURCE_TABLE}"))
        conn.execute(
            sa.text(f"INSERT INTO {SOURCE_TABLE} VALUES (1,'德清',10),(2,'安徽',20)")
        )

    with pg_session_factory() as s:
        owner = User(username=f"flow_own_{suffix}", password_hash="x")
        other = User(username=f"flow_oth_{suffix}", password_hash="x")
        admin = User(username=f"flow_adm_{suffix}", password_hash="x", is_admin=True)
        ds = Dataset(
            name=f"flow_src_{suffix}",
            source_table=SOURCE_TABLE,
            pk_columns=["order_id"],
            columns=["order_id", "warehouse", "amount"],
        )
        s.add_all([owner, other, admin, ds])
        s.flush()
        # owner 只有德清仓的行级权限 —— 关键：产物必须继承这条规则
        s.add(
            DatasetGrant(
                user_id=owner.id,
                dataset_id=ds.id,
                row_rules=[{"field": "warehouse", "op": "eq", "value": "德清"}],
                field_whitelist=None,
            )
        )
        s.add(
            DatasetGrant(user_id=admin.id, dataset_id=ds.id, row_rules=[], field_whitelist=None)
        )
        s.commit()
        ids = {
            "suffix": suffix,
            "owner": owner.id,
            "other": other.id,
            "admin": admin.id,
            "ds": ds.id,
            "ds_name": ds.name,
        }

    yield ids, pg_session_factory, pg_engine

    # ---- 清理 ----
    with pg_engine.begin() as conn:
        for schema in ("dwd", "dws", "ads"):
            rows = conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name LIKE 'flowt\\_%'"
                ),
                {"s": schema},
            ).fetchall()
            for (table,) in rows:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}"."{table}"'))

    with pg_session_factory() as s:
        flows = s.query(DataFlow).filter(DataFlow.name.like(f"flowt_{suffix}%")).all()
        flow_ids = [f.id for f in flows]
        dataset_ids = [ids["ds"]] + [f.output_dataset_id for f in flows if f.output_dataset_id]
        user_ids = [ids["owner"], ids["other"], ids["admin"]]

        s.query(FlowRun).filter(FlowRun.flow_id.in_(flow_ids)).delete(
            synchronize_session=False
        )
        s.query(DataFlowVersion).filter(DataFlowVersion.flow_id.in_(flow_ids)).delete(
            synchronize_session=False
        )
        # 清 flow_runs.trigger_user_id 的外键引用后再删用户
        s.query(AuditLog).filter(AuditLog.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        s.query(DataFlow).filter(DataFlow.id.in_(flow_ids)).delete(synchronize_session=False)
        s.query(DatasetGrant).filter(DatasetGrant.dataset_id.in_(dataset_ids)).delete(
            synchronize_session=False
        )
        s.query(Dataset).filter(Dataset.id.in_(dataset_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        s.commit()

    for path in GENERATED_DIR.glob(f"flowt_{suffix}*"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 清理失败不能掩盖测试结果；残留文件是 .gitignore 掉的，无害
            pass


@pytest.fixture
def load_user(pg_session_factory):
    def _load(user_id):
        with pg_session_factory() as s:
            return s.get(User, user_id)

    return _load
