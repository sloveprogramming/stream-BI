"""可逆加密：给「必须落库、但绝不能明文」的值用 —— 目前是模型提供方的 API key。

主密钥从 `jwt_secret` 派生（SHA-256 → Fernet key），与 JWT 同一安全边界：
拿到数据库的人仍需要服务器上的 `jwt_secret` 才能解。
所以 key 不再明文躺在 `.env`（会被备份/同步/截图），也不明文躺在数据库里。

⚠️ 更换 `jwt_secret` 会让旧密文解不开 —— 这是密钥管理的本意，不是 bug；
`decrypt_secret` 在这种情况下抛 `SecretDecryptionError`，
调用方应转成「主密钥已更换，请重新填写」这类可操作的提示，而不是甩堆栈。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings

_MASK_TAIL = 4


class SecretDecryptionError(Exception):
    """密文解不开（通常是主密钥更换过或密文损坏）。"""


def _fernet() -> Fernet:
    digest = hashlib.sha256((get_settings().jwt_secret or "").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    """明文 → 密文。空串原样返回（本地服务不填 key 是合法的）。"""
    value = (plain or "").strip()
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """密文 → 明文。空串原样返回。解不开抛 `SecretDecryptionError`。"""
    token = (token or "").strip()
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptionError(
            "密文解不开：主密钥（jwt_secret）可能已更换，或密文已损坏。"
            "请在模型配置里重新填写 API key。"
        ) from exc


def mask_secret(plain: str) -> str:
    """给接口展示用的掩码：只露末 4 位，够人辨认是哪把 key，不够人用。"""
    value = (plain or "").strip()
    if not value:
        return ""
    if len(value) <= _MASK_TAIL:
        return "•" * len(value)
    return f"{value[:3]}…{value[-_MASK_TAIL:]}"


__all__ = ["SecretDecryptionError", "decrypt_secret", "encrypt_secret", "mask_secret"]
