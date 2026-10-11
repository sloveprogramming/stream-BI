"""运行时配置解析：**DB 默认优先，env 兜底**。

这个模块保证两件事：平台里点「设为默认」立即生效（不用重启）；
什么都不配时行为与引入之前完全一致（回退环境变量）。
"""
import uuid
from types import SimpleNamespace

import pytest

import app.ai.runtime as runtime
from app.ai.embedding import resolve_status
from app.models import ModelProvider
from app.security.secrets import encrypt_secret


def _env_settings(**overrides):
    base = {
        "llm_base_url": "https://env.example/v1",
        "llm_api_key": "env-key",
        "llm_model": "env-model",
        "llm_planner_model": "env-planner",
        "embedding_timeout_seconds": 5,
        "database_url": "postgresql+psycopg://unused/unused",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def providers(pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    ids: list[int] = []

    def _add(**kwargs) -> int:
        row = ModelProvider(name=kwargs.pop("name", f"prov_{suffix}_{len(ids)}"), **kwargs)
        with pg_session_factory() as session:
            session.add(row)
            session.commit()
            ids.append(row.id)
        return row.id

    yield _add
    with pg_session_factory() as session:
        session.query(ModelProvider).filter(ModelProvider.id.in_(ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.fixture
def env_settings(monkeypatch):
    def _apply(**overrides):
        monkeypatch.setattr(runtime, "get_settings", lambda: _env_settings(**overrides))

    return _apply


# ---------------------------------------------------------------- LLM


def test_falls_back_to_env_when_nothing_registered(env_settings, pg_session_factory):
    env_settings()
    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.source == "env"
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.api_key == "env-key"
    assert cfg.model == "env-model"
    assert cfg.planner_model == "env-planner"


def test_db_default_wins_over_env(env_settings, providers, pg_session_factory):
    """平台里点「设为默认」后，下一个请求就该走它 —— 这是本功能的存在意义。"""
    env_settings()
    providers(
        name="deepseek-main",
        kind="llm",
        provider_kind="api",
        base_url="https://api.deepseek.com/v1",
        encrypted_api_key=encrypt_secret("db-plain-key"),
        model="deepseek-chat",
        planner_model="deepseek-reasoner",
        enabled=True,
        is_default=True,
    )
    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.source == "db:deepseek-main"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.api_key == "db-plain-key"  # 解密后的明文只给调用方，不给接口
    assert cfg.model == "deepseek-chat"
    assert cfg.planner_model == "deepseek-reasoner"


def test_deleted_default_falls_back_to_env(env_settings, providers, pg_session_factory):
    """删掉默认配置是日常操作 —— 之后必须回退 env，而不是报"找不到配置"。"""
    env_settings()
    provider_id = providers(
        kind="llm",
        provider_kind="api",
        base_url="https://db.example/v1",
        encrypted_api_key=encrypt_secret("k"),
        model="db-model",
        enabled=True,
        is_default=True,
    )
    assert runtime.resolve_llm(session=pg_session_factory()).source.startswith("db:")

    with pg_session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        session.delete(row)
        session.commit()

    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.source == "env"


def test_disabled_default_is_ignored(env_settings, providers, pg_session_factory):
    """停用的默认配置不该生效 —— "默认"必须配合"启用"才成立。"""
    env_settings()
    providers(
        kind="llm",
        provider_kind="api",
        base_url="https://db.example/v1",
        encrypted_api_key=encrypt_secret("k"),
        model="db-model",
        enabled=False,  # 停用
        is_default=True,
    )
    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.source == "env"


def test_local_source_has_empty_key(env_settings, providers, pg_session_factory):
    """本地服务不填 key 是合法的 —— 解密层不该因此报错。"""
    env_settings()
    providers(
        name="ollama-local",
        kind="llm",
        provider_kind="local",
        base_url="http://localhost:11434/v1",
        encrypted_api_key="",
        model="qwen2.5:7b",
        enabled=True,
        is_default=True,
    )
    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.source == "db:ollama-local"
    assert cfg.api_key == ""
    assert cfg.base_url == "http://localhost:11434/v1"


def test_planner_model_falls_back_to_model(env_settings, providers, pg_session_factory):
    env_settings(llm_planner_model="")
    providers(
        kind="llm",
        provider_kind="api",
        base_url="https://db.example/v1",
        encrypted_api_key=encrypt_secret("k"),
        model="single-model",
        planner_model="",  # 没单独配规划模型
        enabled=True,
        is_default=True,
    )
    cfg = runtime.resolve_llm(session=pg_session_factory())
    assert cfg.planner_model == "single-model"


# ---------------------------------------------------------------- embedding


def test_embedding_api_runtime_from_db(env_settings, providers, pg_session_factory):
    env_settings()
    providers(
        name="siliconflow-embed",
        kind="embedding",
        provider_kind="api",
        base_url="https://api.siliconflow.cn/v1",
        encrypted_api_key=encrypt_secret("emb-key"),
        model="BAAI/bge-m3",
        enabled=True,
        is_default=True,
    )
    rt = runtime.resolve_embedding_api(session=pg_session_factory())
    assert rt is not None
    assert rt.model == "BAAI/bge-m3"
    assert rt.api_key == "emb-key"


def test_embedding_local_row_is_not_served_as_api(env_settings, providers, pg_session_factory):
    """local 来源不走 HTTP 客户端 —— resolve_embedding_api 返回 None，由探测逻辑接管。"""
    env_settings()
    providers(
        kind="embedding",
        provider_kind="local",
        base_url="",
        encrypted_api_key="",
        model="BAAI/bge-m3",
        enabled=True,
        is_default=True,
    )
    assert runtime.resolve_embedding_api(session=pg_session_factory()) is None


def test_resolve_status_prefers_db_api_row(monkeypatch, providers):
    """探测结果必须反映 DB 配置：configured="db"、provider="api"。"""
    providers(
        kind="embedding",
        provider_kind="api",
        base_url="https://api.siliconflow.cn/v1",
        encrypted_api_key=encrypt_secret("emb-key"),
        model="BAAI/bge-m3",
        enabled=True,
        is_default=True,
    )
    status = resolve_status()
    assert status.provider == "api"
    assert status.configured == "db"
    assert status.usable is True
    assert status.model == "BAAI/bge-m3"


def test_resolve_status_db_local_without_dependency(monkeypatch, providers):
    """DB 指定本地模型但本机缺依赖 → 按既有语义中止，**不回退 env 的 API 配置**。"""
    providers(
        kind="embedding",
        provider_kind="local",
        base_url="",
        encrypted_api_key="",
        model="BAAI/bge-m3",
        enabled=True,
        is_default=True,
    )
    monkeypatch.setattr("app.ai.embedding.local_available", lambda: (False, "未安装 FlagEmbedding"))
    status = resolve_status()
    assert status.provider == "none"
    assert status.usable is False
    assert "FlagEmbedding" in status.reason
