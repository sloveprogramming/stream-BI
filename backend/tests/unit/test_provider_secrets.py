"""API key 的加密与脱敏。

核心断言：**数据库里读不到明文，接口能拿到的只有掩码** ——
这是本功能相对「key 写在 .env 里」的全部安全增量，一条都不能松。
"""
import base64

import pytest

import app.security.secrets as secrets_mod
from app.security.secrets import (
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)


def test_roundtrip_preserves_the_value():
    plain = "sk-abc123XYZ生产key"
    token = encrypt_secret(plain)
    assert token != plain
    assert decrypt_secret(token) == plain


def test_ciphertext_is_not_the_plaintext_and_is_ascii():
    token = encrypt_secret("sk-secret-value")
    assert "secret" not in token
    # Fernet 输出是 urlsafe base64 —— 可以放心放 Text 列
    base64.urlsafe_b64decode(token)


def test_empty_secret_stays_empty():
    """本地服务（Ollama 等）不填 key 是合法的，不该产生"空密文"。"""
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) == ""  # type: ignore[arg-type]
    assert decrypt_secret("") == ""


def test_mask_shows_only_the_tail():
    assert mask_secret("sk-live-abcdef1234") == "sk-…1234"
    assert mask_secret("abcd") == "••••"
    assert mask_secret("") == ""


def test_mask_only_shows_head3_and_tail4():
    """掩码设计就是露前 3 + 后 4（够辨认、不够使用）；中间部分不能出现。"""
    value = "super-long-secret-key-value"
    masked = mask_secret(value)
    assert masked == value[:3] + "…" + value[-4:]
    assert value[3:-4] not in masked


def test_decrypt_fails_after_master_key_rotation():
    """换 jwt_secret 后旧密文解不开 —— 报可操作的原因，不是甩堆栈。"""
    token = encrypt_secret("sk-old")

    class _Rotated:
        jwt_secret = "rotated-master-key"

    original = secrets_mod.get_settings
    secrets_mod.get_settings = lambda: _Rotated()  # type: ignore[assignment]
    try:
        with pytest.raises(SecretDecryptionError, match="重新填写"):
            decrypt_secret(token)
    finally:
        secrets_mod.get_settings = original  # type: ignore[assignment]
