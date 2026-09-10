import sqlalchemy as sa

from app.assistant.schema_context import build_schema_context
from app.models import Dataset, DatasetGrant, Role, User


def test_build_schema_only_authorized(pg_session_factory, pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS ods.sc_a "
                "(id integer PRIMARY KEY, region text)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO ods.sc_a VALUES (1, '华东') ON CONFLICT (id) DO NOTHING"
            )
        )

    with pg_session_factory() as s:
        # 防御：清理上次运行残留（即使上次失败、用户已不存在，也按名称兜底清理）
        old_u = s.query(User).filter_by(username="sc_u").first()
        stale_role_ids = s.query(Role.id).filter_by(name="sc_r")
        stale_ds_ids = s.query(Dataset.id).filter(Dataset.name.in_(["sc_a", "sc_b"]))
        s.query(DatasetGrant).filter(
            DatasetGrant.role_id.in_(stale_role_ids)
        ).delete(synchronize_session=False)
        s.query(DatasetGrant).filter(
            DatasetGrant.dataset_id.in_(stale_ds_ids)
        ).delete(synchronize_session=False)
        s.query(Dataset).filter(Dataset.name.in_(["sc_a", "sc_b"])).delete(
            synchronize_session=False
        )
        s.query(Role).filter_by(name="sc_r").delete()
        if old_u is not None:
            s.query(User).filter_by(username="sc_u").delete()
        s.commit()

        u = User(username="sc_u", password_hash="x")
        r = Role(name="sc_r")
        d1 = Dataset(
            name="sc_a",
            source_table="ods.sc_a",
            pk_columns=["id"],
            columns=["id", "region"],
        )
        d2 = Dataset(
            name="sc_b",
            source_table="ods.sc_b",
            pk_columns=["id"],
            columns=["id", "secret"],
        )
        s.add_all([u, r, d1, d2])
        s.flush()
        u.role_id = r.id
        s.add(
            DatasetGrant(
                role_id=r.id,
                dataset_id=d1.id,
                row_rules=[],
                field_whitelist=["id", "region"],
            )
        )
        s.commit()

        ctx = build_schema_context(pg_session_factory, pg_engine, u)
        names = [t["name"] for t in ctx["tables"]]
        assert "sc_a" in names
        assert "sc_b" not in names
        assert "region" in ctx["prompt"]
        assert ctx["tables"][0]["samples"] == [["1", "华东"]]

        # 清理
        s.query(DatasetGrant).filter_by(role_id=r.id).delete()
        s.query(User).filter_by(id=u.id).delete()
        s.query(Role).filter_by(id=r.id).delete()
        s.query(Dataset).filter(Dataset.id.in_([d1.id, d2.id])).delete(
            synchronize_session=False
        )
        s.commit()
