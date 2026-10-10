"""P0 权限口径回归测试。

对应 4 处修复：
1. MCP 通道用 resolve_grant（管理员无显式授权也能用）
2. MCP 数据集列表 = 管理员全量 ∪ 用户直授 ∪ 角色授权
3. 报表 owner 用 resolve_grant（管理员 owner 不再必然失败）
4. schema 样例数据走权限裁剪器（行级规则 + 字段白名单）
"""
import uuid

import pytest
import sqlalchemy as sa

from app.access.grants import query_visible_datasets
from app.api.deps import AnyPrincipal
from app.assistant.schema_context import build_schema_context
from app.mcp_tools import mcp_describe_dataset, mcp_list_datasets
from app.models import AuditLog, Dataset, DatasetGrant, Role, User

TABLE = "ods.rg_t"


@pytest.fixture
def rg_env(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {TABLE} "
                "(id integer PRIMARY KEY, region text, secret text)"
            )
        )
        conn.execute(sa.text(f"DELETE FROM {TABLE}"))
        conn.execute(
            sa.text(f"INSERT INTO {TABLE} VALUES (1,'华东','v1'),(2,'华北','v2'),(3,'华东','v3')")
        )

    with pg_session_factory() as s:
        role = Role(name=f"rg_r_{suffix}")
        ds = Dataset(
            name=f"rg_d_{suffix}",
            source_table=TABLE,
            pk_columns=["id"],
            columns=["id", "region", "secret"],
        )
        admin = User(username=f"rg_adm_{suffix}", password_hash="x", is_admin=True)
        direct = User(username=f"rg_dir_{suffix}", password_hash="x")
        roled = User(username=f"rg_rol_{suffix}", password_hash="x")
        none = User(username=f"rg_non_{suffix}", password_hash="x")
        s.add_all([role, ds, admin, direct, roled, none])
        s.flush()
        roled.role_id = role.id
        s.add(
            DatasetGrant(
                role_id=role.id,
                dataset_id=ds.id,
                row_rules=[{"field": "region", "op": "eq", "value": "华东"}],
                field_whitelist=["id", "region"],
            )
        )
        # 用户直授：无角色，仅靠 user_id 授权（旧 MCP 实现会漏掉）
        s.add(
            DatasetGrant(
                user_id=direct.id, dataset_id=ds.id, row_rules=[], field_whitelist=None
            )
        )
        s.commit()
        ids = {
            "role": role.id,
            "ds": ds.id,
            "ds_name": ds.name,
            "admin": admin.id,
            "direct": direct.id,
            "roled": roled.id,
            "none": none.id,
        }

    yield ids, pg_session_factory, pg_engine

    with pg_session_factory() as s:
        user_ids = [ids["admin"], ids["direct"], ids["roled"], ids["none"]]
        # 先清审计日志：mcp_* 工具会写入，否则删 users 会撞外键
        s.query(AuditLog).filter(AuditLog.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        s.query(DatasetGrant).filter_by(dataset_id=ids["ds"]).delete()
        s.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        s.query(Role).filter_by(id=ids["role"]).delete()
        s.query(Dataset).filter_by(id=ids["ds"]).delete()
        s.commit()


def _user(sf, user_id):
    with sf() as s:
        return s.get(User, user_id)


def _visible(sf, user_id):
    with sf() as s:
        return [d.name for d in query_visible_datasets(s, _user(sf, user_id)).all()]


def test_visible_datasets_admin_sees_all(rg_env):
    ids, sf, _ = rg_env
    assert ids["ds_name"] in _visible(sf, ids["admin"])


def test_visible_datasets_includes_direct_grant(rg_env):
    """用户直授（无角色）必须可见 —— 旧 MCP 实现按 role join，会漏掉。"""
    ids, sf, _ = rg_env
    assert _visible(sf, ids["direct"]) == [ids["ds_name"]]


def test_visible_datasets_includes_role_grant(rg_env):
    ids, sf, _ = rg_env
    assert _visible(sf, ids["roled"]) == [ids["ds_name"]]


def test_visible_datasets_empty_without_grant(rg_env):
    ids, sf, _ = rg_env
    assert _visible(sf, ids["none"]) == []


def test_visible_datasets_survives_user_without_id(rg_env):
    """无 id / 无角色的匿名主体不能报错，也不能看到任何数据集。"""

    class Anonymous:
        id = None
        role_id = None
        is_admin = False

    _, sf, _ = rg_env
    with sf() as s:
        assert query_visible_datasets(s, Anonymous()).all() == []


def test_mcp_list_includes_direct_grant(rg_env):
    """修复 2：MCP list 不再只按 role 过滤。"""
    ids, sf, _ = rg_env
    listed = [d["name"] for d in mcp_list_datasets(AnyPrincipal(user=_user(sf, ids["direct"])))]
    assert ids["ds_name"] in listed


def test_mcp_list_visible_for_admin(rg_env):
    """修复 2：管理员不再拿到空列表。"""
    ids, sf, _ = rg_env
    listed = [d["name"] for d in mcp_list_datasets(AnyPrincipal(user=_user(sf, ids["admin"])))]
    assert ids["ds_name"] in listed


def test_mcp_describe_allows_admin_without_grant(rg_env):
    """修复 1：管理员无显式授权时，MCP 通道应放行而不是 403。"""
    ids, sf, _ = rg_env
    desc = mcp_describe_dataset(AnyPrincipal(user=_user(sf, ids["admin"])), ids["ds_name"])
    assert desc["name"] == ids["ds_name"]
    assert set(desc["columns"]) == {"id", "region", "secret"}  # 管理员无字段裁剪
    assert desc["row_rules"] == []


def test_mcp_describe_still_denies_ungranted(rg_env):
    """非管理员无授权时仍需拒绝。"""
    ids, sf, _ = rg_env
    with pytest.raises(ValueError):
        mcp_describe_dataset(AnyPrincipal(user=_user(sf, ids["none"])), ids["ds_name"])


def test_sample_rows_respect_row_rules_and_whitelist(rg_env):
    """修复 4：样例行必须走裁剪器 —— 只出现授权行、且不含白名单外字段。"""
    ids, sf, engine = rg_env
    ctx = build_schema_context(sf, engine, _user(sf, ids["roled"]))
    table = next(t for t in ctx["tables"] if t["name"] == ids["ds_name"])
    assert table["columns"] == ["id", "region"]
    assert len(table["samples"]) == 2  # 只剩华东两行
    assert all(row[1] == "华东" for row in table["samples"])
    assert "华北" not in ctx["prompt"]
    assert "v2" not in ctx["prompt"]  # 越权行的真实值不得进入 prompt
