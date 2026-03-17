"""Ground truth tests for task-auth-oauth: OAuth2 authorization code flow."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-auth-oauth"))


def test_register_client():
    from oauth import register_client
    client = register_client("TestApp", "http://localhost/callback")
    assert "client_id" in client
    assert "client_secret" in client
    assert client["redirect_uri"] == "http://localhost/callback"
    assert len(client["client_id"]) >= 16
    assert len(client["client_secret"]) >= 16


def test_authorize_returns_code():
    from oauth import register_client, authorize
    client = register_client("AuthApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-1", scope="read")
    assert isinstance(code, str)
    assert len(code) > 0


def test_authorize_invalid_client_raises():
    from oauth import authorize
    with pytest.raises(ValueError):
        authorize("invalid-client-id", "user-1")


def test_authorize_invalid_scope_raises():
    from oauth import register_client, authorize
    client = register_client("ScopeApp", "http://localhost/cb")
    with pytest.raises(ValueError):
        authorize(client["client_id"], "user-1", scope="delete")


def test_exchange_token_success():
    from oauth import register_client, authorize, exchange_token
    client = register_client("ExchangeApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-2", scope="write")
    token_data = exchange_token(client["client_id"], client["client_secret"], code)
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"
    assert token_data["scope"] == "write"


def test_exchange_token_code_reuse_raises():
    from oauth import register_client, authorize, exchange_token
    client = register_client("ReuseApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-3")
    exchange_token(client["client_id"], client["client_secret"], code)
    with pytest.raises(ValueError):
        exchange_token(client["client_id"], client["client_secret"], code)


def test_exchange_token_wrong_secret_raises():
    from oauth import register_client, authorize, exchange_token
    client = register_client("SecretApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-4")
    with pytest.raises(ValueError):
        exchange_token(client["client_id"], "wrong-secret", code)


def test_validate_token():
    from oauth import register_client, authorize, exchange_token, validate_token
    client = register_client("ValidateApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-5", scope="admin")
    token_data = exchange_token(client["client_id"], client["client_secret"], code)
    info = validate_token(token_data["access_token"])
    assert info["user_id"] == "user-5"
    assert info["scope"] == "admin"


def test_validate_invalid_token_raises():
    from oauth import validate_token
    with pytest.raises(ValueError):
        validate_token("invalid-token-xyz")


def test_revoke_token():
    from oauth import register_client, authorize, exchange_token, revoke_token, validate_token
    client = register_client("RevokeApp", "http://localhost/cb")
    code = authorize(client["client_id"], "user-6")
    token_data = exchange_token(client["client_id"], client["client_secret"], code)
    assert revoke_token(token_data["access_token"]) is True
    with pytest.raises(ValueError):
        validate_token(token_data["access_token"])


def test_revoke_nonexistent_token():
    from oauth import revoke_token
    assert revoke_token("nonexistent-token") is False
