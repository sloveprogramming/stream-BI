"""动态 few-shot 的检索、权限过滤与录入测试。"""
import uuid

import pytest

from app.assistant import fewshot
from app.models import Dataset, DatasetGrant, Role, User, VerifiedQa


# ---------------------------------------------------------------- 纯函数


def test_similarity_identical_questions_is_one():
    assert fewshot.similarity("上周华东销售额", "上周华东销售额") == 1.0


def test_similarity_prefers_closer_question():
    base = "上周华东区的销售额是多少"
    close = fewshot.similarity(base, "上周华东销售额多少")
    far = fewshot.similarity(base, "各仓库存件数")
    assert close > far


def test_bigrams_strip_punctuation_and_case():
    assert fewshot.bigrams("A，B。") == fewshot.bigrams("ab")


def test_similarity_of_unrelated_is_low():
    assert fewshot.similarity("销售额", "库存周转") < 0.2


def test_render_empty_examples_returns_blank():
    assert fewshot.render([]) == ""


def test_render_lists_questions_and_sql():
    text = fewshot.render([{"question": "上周销售额", "sql": "SELECT 1"}])
    assert "上周销售额" in text
    assert "SELECT 1" in text


# ---------------------------------------------------------------- 数据库


@pytest.fixture
def fs_env(pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        role = Role(name=f"fs_r_{suffix}")
        allowed = Dataset(
            name=f"fs_a_{suffix}", source_table="ods.fs_a", pk_columns=["id"], columns=["id"]
        )
        hidden = Dataset(
            name=f"fs_b_{suffix}", source_table="ods.fs_b", pk_columns=["id"], columns=["id"]
        )
        user = User(username=f"fs_u_{suffix}", password_hash="x")
        admin = User(username=f"fs_adm_{suffix}", password_hash="x", is_admin=True)
        s.add_all([role, allowed, hidden, user, admin])
        s.flush()
        user.role_id = role.id
        s.add(
            DatasetGrant(
                role_id=role.id, dataset_id=allowed.id, row_rules=[], field_whitelist=None
            )
        )
        s.commit()
        ids = {
            "role": role.id,
            "allowed": allowed.name,
            "hidden": hidden.name,
            "user": user.id,
            "admin": admin.id,
            "dataset_ids": [allowed.id, hidden.id],
        }

    yield ids, pg_session_factory

    with pg_session_factory() as s:
        s.query(VerifiedQa).filter(VerifiedQa.question.in_([f"fs_q_{suffix}_a", f"fs_q_{suffix}_b"])).delete(
            synchronize_session=False
        )
        s.query(DatasetGrant).filter_by(role_id=ids["role"]).delete()
        s.query(User).filter(User.id.in_([ids["user"], ids["admin"]])).delete(
            synchronize_session=False
        )
        s.query(Role).filter_by(id=ids["role"]).delete()
        s.query(Dataset).filter(Dataset.id.in_(ids["dataset_ids"])).delete(
            synchronize_session=False
        )
        s.commit()


def _user(sf, user_id):
    with sf() as s:
        return s.get(User, user_id)


def test_remember_creates_then_overwrites(fs_env):
    ids, sf = fs_env
    created = fewshot.remember(sf, "fs_q_remember", "SELECT 1", dataset_keys=["x"])
    assert created is True
    created_again = fewshot.remember(sf, "fs_q_remember", "SELECT 2", dataset_keys=["x", "y"])
    assert created_again is False
    with sf() as s:
        row = s.query(VerifiedQa).filter_by(question="fs_q_remember").one()
        assert row.sql == "SELECT 2"
        assert row.dataset_keys == ["x", "y"]
        s.query(VerifiedQa).filter_by(question="fs_q_remember").delete()
        s.commit()


def test_remember_rejects_blank_input(fs_env):
    _, sf = fs_env
    with pytest.raises(ValueError):
        fewshot.remember(sf, "  ", "SELECT 1")


def test_retrieve_hides_examples_from_invisible_datasets(fs_env):
    """引用了用户看不到的数据集的样例，绝不能出现在他的 prompt 里。"""
    ids, sf = fs_env
    fewshot.remember(sf, "fs_q_visible", "SELECT 1", dataset_keys=[ids["allowed"]])
    fewshot.remember(sf, "fs_q_hidden", "SELECT 2", dataset_keys=[ids["hidden"]])

    got = fewshot.retrieve(sf, "fs_q_visible", _user(sf, ids["user"]), limit=10)
    questions = [item["question"] for item in got]
    assert "fs_q_visible" in questions
    assert "fs_q_hidden" not in questions

    # 管理员可见全部数据集，两条都能拿到
    admin_questions = [
        item["question"] for item in fewshot.retrieve(sf, "fs_q", _user(sf, ids["admin"]), 10)
    ]
    assert "fs_q_hidden" in admin_questions


def test_retrieve_ranks_by_similarity_and_bumps_hits(fs_env):
    ids, sf = fs_env
    fewshot.remember(sf, "华东区销售额", "SELECT 1", dataset_keys=[ids["allowed"]])
    fewshot.remember(sf, "库存周转天数", "SELECT 2", dataset_keys=[ids["allowed"]])

    got = fewshot.retrieve(sf, "华东区销售额是多少", _user(sf, ids["user"]), limit=5)
    assert got[0]["question"] == "华东区销售额"
    with sf() as s:
        row = s.query(VerifiedQa).filter_by(question="华东区销售额").one()
        assert row.hits >= 1
