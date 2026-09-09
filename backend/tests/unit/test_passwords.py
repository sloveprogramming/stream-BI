from app.security.passwords import hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert "s3cret!" not in h
    assert verify_password("s3cret!", h) is True
    assert verify_password("wrong", h) is False


def test_unique_salt():
    assert hash_password("same") != hash_password("same")
