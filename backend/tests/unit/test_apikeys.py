from app.security.apikeys import generate_key, hash_secret


def test_generate_key_shape():
    key_id, secret = generate_key()
    assert key_id.startswith("bk_") and len(key_id) == 15
    assert len(secret) >= 32
    assert generate_key()[0] != key_id


def test_hash_secret_deterministic():
    assert hash_secret("abc") == hash_secret("abc")
    assert len(hash_secret("abc")) == 64
