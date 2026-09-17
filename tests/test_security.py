from app.core.security import hash_password, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("secret")
    assert encoded != "secret"
    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)
