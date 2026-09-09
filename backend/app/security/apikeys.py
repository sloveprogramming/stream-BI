import hashlib
import secrets

_PREFIX = "bk_"


def generate_key() -> tuple[str, str]:
    return _PREFIX + secrets.token_hex(6), secrets.token_urlsafe(24)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
