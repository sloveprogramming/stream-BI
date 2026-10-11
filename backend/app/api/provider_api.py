"""模型提供方的管理接口（仅管理员）。

key 的三条铁律：

1. 落库前 Fernet 加密（`app/security/secrets.py`）；
2. **任何响应只回掩码**，永不回明文 —— 前端"编辑"时不带 `api_key` 键表示不修改，
   带空串表示清空（本地服务不需要 key）；
3. 测试连接用解密后的真实值，但响应里同样没有 key。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.ai.embedding import local_available
from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import ModelProvider
from app.models.provider import KINDS, KIND_EMBEDDING, KIND_LLM, SOURCES
from app.security.secrets import (
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)

router = APIRouter(prefix="/admin/providers", tags=["model-providers"])

_TEST_TIMEOUT = 15


def _require_admin(principal: AnyPrincipal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


# ---------------------------------------------------------------- 出参与校验


def _out(row: ModelProvider) -> dict:
    try:
        masked = mask_secret(decrypt_secret(row.encrypted_api_key))
    except SecretDecryptionError:
        # 主密钥换过：不能装作没事，也不能泄内容 —— 明确告诉人要重填
        masked = "（解密失败，请重新填写）"
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "provider_kind": row.provider_kind,
        "base_url": row.base_url,
        "api_key_masked": masked,
        "model": row.model,
        "planner_model": row.planner_model,
        "enabled": row.enabled,
        "is_default": row.is_default,
    }


def _fields_from(body: dict) -> dict:
    """校验并规范化表单字段。缺什么报什么，别等调用模型才炸。"""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 必填")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="name 过长（最多 64 字符）")

    kind = (body.get("kind") or "").strip()
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 llm 或 embedding")

    provider_kind = (body.get("provider_kind") or "api").strip()
    if provider_kind not in SOURCES:
        raise HTTPException(status_code=400, detail="provider_kind 必须是 api 或 local")

    base_url = (body.get("base_url") or "").strip()
    if provider_kind == "api" and not base_url:
        raise HTTPException(status_code=400, detail="API 来源必须填写 base_url")

    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model 必填")

    planner_model = (body.get("planner_model") or "").strip() if kind == KIND_LLM else ""
    return {
        "name": name,
        "kind": kind,
        "provider_kind": provider_kind,
        "base_url": base_url,
        "model": model,
        "planner_model": planner_model,
    }


# ---------------------------------------------------------------- 连通性


def _client(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key or "not-needed", base_url=base_url, timeout=_TEST_TIMEOUT)


def _explain(exc: Exception) -> str:
    """把 openai SDK 的异常翻译成能定位问题的人话。"""
    from openai import (
        APIConnectionError,
        AuthenticationError,
        InternalServerError,
        NotFoundError,
    )

    if isinstance(exc, AuthenticationError):
        return "鉴权失败：API key 不对或已失效"
    if isinstance(exc, APIConnectionError):
        return "连不上服务：base_url 或网络有问题"
    if isinstance(exc, NotFoundError):
        return "接口不存在（404）：请检查 base_url 路径（通常以 /v1 结尾）"
    if isinstance(exc, InternalServerError):
        # 走代理的环境里，"连不上目标"会以 502/500 的形式出现
        return "服务返回 5xx：地址可能不对、服务没起，或中间有代理拦截"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _run_test(kind: str, provider_kind: str, base_url: str, api_key: str, model: str) -> dict:
    """连通性测试。LLM 调模型清单接口（免费）；embedding 发一次真实向量化。"""
    if provider_kind == "local" and kind == KIND_EMBEDDING:
        ok, why = local_available()
        return {
            "ok": ok,
            "error": None if ok else f"本机缺少本地模型依赖：{why}",
        }
    if not base_url:
        return {"ok": False, "error": "base_url 未填写"}

    try:
        client = _client(base_url, api_key)
        if kind == KIND_LLM:
            data = client.models.list()
            return {"ok": True, "models": [m.id for m in data][:30]}
        resp = client.embeddings.create(model=model, input=["ping"])
        return {"ok": True, "dim": len(resp.data[0].embedding)}
    except Exception as exc:  # noqa: BLE001 —— 测试接口要把失败变成人话，而不是 500
        return {"ok": False, "error": _explain(exc)}


def _run_models(base_url: str, api_key: str) -> dict:
    try:
        data = _client(base_url, api_key).models.list()
        return {"ok": True, "models": [m.id for m in data]}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": _explain(exc),
            "hint": "该服务可能不提供模型清单接口，可直接手填模型名",
        }


def _saved_params(row: ModelProvider) -> tuple[str, str, str, str]:
    """（kind, provider_kind, base_url, 解密后的 key）。解密失败按无 key 处理并提示。"""
    try:
        api_key = decrypt_secret(row.encrypted_api_key)
    except SecretDecryptionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return row.kind, row.provider_kind, row.base_url, api_key


# ---------------------------------------------------------------- CRUD


@router.get("")
def list_providers(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        rows = session.query(ModelProvider).order_by(ModelProvider.kind, ModelProvider.id).all()
        return [_out(r) for r in rows]


@router.post("", status_code=201)
def create_provider(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    fields = _fields_from(body)
    with ctx.session_factory() as session:
        if session.query(ModelProvider).filter_by(name=fields["name"]).first():
            raise HTTPException(status_code=409, detail="同名配置已存在")
        row = ModelProvider(**fields, encrypted_api_key=encrypt_secret(body.get("api_key") or ""))
        if fields["kind"] == KIND_LLM and not session.query(ModelProvider).filter_by(kind=KIND_LLM).first():
            row.is_default = True  # 该用途的第一条顺手设为默认
        if fields["kind"] == KIND_EMBEDDING and not session.query(ModelProvider).filter_by(
            kind=KIND_EMBEDDING
        ).first():
            row.is_default = True
        session.add(row)
        session.commit()
        return _out(row)


@router.put("/{provider_id}")
def update_provider(
    provider_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    fields = _fields_from(body)
    with ctx.session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        clash = (
            session.query(ModelProvider)
            .filter(ModelProvider.name == fields["name"], ModelProvider.id != row.id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=409, detail="同名配置已存在")

        for key, value in fields.items():
            setattr(row, key, value)
        # 启停是高频小操作，不参与必填校验
        if "enabled" in body:
            row.enabled = bool(body["enabled"])
        # key 的约定：键不存在 = 不修改（前端编辑时没填新 key 就不带这个键）；
        # 键存在且为空串 = 清空（本地服务不需要 key）
        if "api_key" in body:
            row.encrypted_api_key = encrypt_secret(body.get("api_key") or "")
        session.commit()
        return _out(row)


@router.delete("/{provider_id}")
def delete_provider(
    provider_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        session.delete(row)
        session.commit()
    # 删除默认配置后运行时自然回退环境变量（resolve_* 每次现查），无需额外动作
    return {"deleted": provider_id}


@router.post("/{provider_id}/default")
def set_default(
    provider_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """设为默认。同用途只留一个默认：先把同 kind 的其他行清掉。"""
    _require_admin(principal)
    with ctx.session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        session.query(ModelProvider).filter(
            ModelProvider.kind == row.kind, ModelProvider.id != row.id
        ).update({"is_default": False}, synchronize_session=False)
        row.is_default = True
        row.enabled = True  # 设默认时顺手启用：默认项停用是自相矛盾的状态
        session.commit()
        return _out(row)


# ---------------------------------------------------------------- 测试与模型清单


@router.post("/test")
def test_params(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """保存前测试：用表单里的临时参数（key 明文进、不落库、响应不回显）。"""
    _require_admin(principal)
    kind = (body.get("kind") or "").strip()
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 llm 或 embedding")
    result = _run_test(
        kind,
        (body.get("provider_kind") or "api").strip(),
        (body.get("base_url") or "").strip(),
        body.get("api_key") or "",
        (body.get("model") or "").strip(),
    )
    return result


@router.post("/{provider_id}/test")
def test_saved(
    provider_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """测试已保存的配置（用解密后的真实值）。"""
    _require_admin(principal)
    with ctx.session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        kind, provider_kind, base_url, api_key = _saved_params(row)
        model = row.model
    result = _run_test(kind, provider_kind, base_url, api_key, model)
    result.pop("models", None)  # 测试接口不需要模型清单，响应尽量小
    return result


@router.post("/models")
def list_models_from_params(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    return _run_models(
        (body.get("base_url") or "").strip(), body.get("api_key") or ""
    )


@router.post("/{provider_id}/models")
def list_models_saved(
    provider_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        _kind, _pk, base_url, api_key = _saved_params(row)
    return _run_models(base_url, api_key)
