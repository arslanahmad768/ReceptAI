from app.core.security import (
    create_access_token, create_refresh_token, verify_token,
    hash_password, verify_password
)

def test_password_round_trip():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed) is True
    assert verify_password("wrong", hashed) is False

def test_access_token_round_trip():
    token = create_access_token({"sub": "user-123"})
    payload = verify_token(token)
    assert payload["sub"] == "user-123"

def test_tampered_token_is_rejected():
    import pytest
    token = create_access_token({"sub": "user-123"})
    with pytest.raises(ValueError):
        verify_token(token + "tampered")

def test_refresh_token_has_type_field():
    token = create_refresh_token({"sub": "user-123"})
    payload = verify_token(token)
    assert payload.get("type") == "refresh"
