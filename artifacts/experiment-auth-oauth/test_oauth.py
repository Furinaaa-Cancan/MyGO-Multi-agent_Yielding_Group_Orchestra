"""Tests for OAuth authorization code flow."""

import time
from unittest.mock import patch

import pytest

from oauth import authorize, auth_codes, clients, exchange_token, register_client, revoke_token, tokens, validate_token


@pytest.fixture(autouse=True)
def clear_storage():
    """Clear module-level storage between tests."""
    clients.clear()
    auth_codes.clear()
    tokens.clear()


@pytest.fixture
def registered_client():
    """Register and return a test client."""
    return register_client("test_app", "https://example.com/callback")


class TestAuthorize:
    def test_returns_code_string(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        assert isinstance(code, str)
        assert len(code) > 0

    def test_stores_code_with_expiry(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        assert code in auth_codes
        data = auth_codes[code]
        assert data["client_id"] == registered_client["client_id"]
        assert data["user_id"] == "user1"
        assert data["scope"] == "read"
        assert data["used"] is False
        assert data["expires_at"] > time.time()
        assert data["expires_at"] <= time.time() + 300

    def test_validates_scope_read(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        assert code in auth_codes

    def test_validates_scope_write(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "write")
        assert code in auth_codes

    def test_validates_scope_admin(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "admin")
        assert code in auth_codes

    def test_raises_for_unknown_client_id(self):
        with pytest.raises(ValueError, match="Unknown client_id"):
            authorize("nonexistent", "user1", "read")

    def test_raises_for_invalid_scope(self, registered_client):
        with pytest.raises(ValueError, match="Invalid scope"):
            authorize(registered_client["client_id"], "user1", "delete")

    def test_raises_for_empty_scope(self, registered_client):
        with pytest.raises(ValueError, match="Invalid scope"):
            authorize(registered_client["client_id"], "user1", "")


class TestExchangeToken:
    def test_successful_exchange(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        assert "access_token" in result
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 3600
        assert result["scope"] == "read"

    def test_returns_correct_scope(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "admin")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        assert result["scope"] == "admin"

    def test_code_single_use(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        with pytest.raises(ValueError, match="already been used"):
            exchange_token(
                registered_client["client_id"],
                registered_client["client_secret"],
                code,
            )

    def test_raises_for_invalid_code(self, registered_client):
        with pytest.raises(ValueError, match="Invalid authorization code"):
            exchange_token(
                registered_client["client_id"],
                registered_client["client_secret"],
                "bogus_code",
            )

    def test_raises_for_wrong_client_secret(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        with pytest.raises(ValueError, match="Invalid client_secret"):
            exchange_token(
                registered_client["client_id"],
                "wrong_secret",
                code,
            )

    def test_raises_for_expired_code(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        auth_codes[code]["expires_at"] = time.time() - 1
        with pytest.raises(ValueError, match="expired"):
            exchange_token(
                registered_client["client_id"],
                registered_client["client_secret"],
                code,
            )

    def test_raises_for_wrong_client_id(self, registered_client):
        other = register_client("other_app", "https://other.com/callback")
        code = authorize(registered_client["client_id"], "user1", "read")
        with pytest.raises(ValueError, match="not issued to this client"):
            exchange_token(
                other["client_id"],
                other["client_secret"],
                code,
            )


class TestValidateToken:
    def test_returns_user_id_scope_client_id(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        info = validate_token(result["access_token"])
        assert info["user_id"] == "user1"
        assert info["scope"] == "read"
        assert info["client_id"] == registered_client["client_id"]

    def test_raises_for_invalid_token(self):
        with pytest.raises(ValueError, match="Invalid or revoked"):
            validate_token("nonexistent_token")

    def test_raises_for_revoked_token(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "write")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        revoke_token(result["access_token"])
        with pytest.raises(ValueError, match="Invalid or revoked"):
            validate_token(result["access_token"])


class TestRevokeToken:
    def test_returns_true_on_success(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        assert revoke_token(result["access_token"]) is True

    def test_returns_false_for_nonexistent_token(self):
        assert revoke_token("does_not_exist") is False

    def test_token_removed_from_storage(self, registered_client):
        code = authorize(registered_client["client_id"], "user1", "read")
        result = exchange_token(
            registered_client["client_id"],
            registered_client["client_secret"],
            code,
        )
        access_token = result["access_token"]
        assert access_token in tokens
        revoke_token(access_token)
        assert access_token not in tokens


class TestFullFlow:
    def test_register_authorize_exchange_validate_revoke(self):
        """Full lifecycle: register -> authorize -> exchange -> validate -> revoke."""
        cred = register_client("lifecycle_app", "https://lifecycle.com/cb")
        code = authorize(cred["client_id"], "alice", "admin")
        tok = exchange_token(cred["client_id"], cred["client_secret"], code)

        info = validate_token(tok["access_token"])
        assert info["user_id"] == "alice"
        assert info["scope"] == "admin"
        assert info["client_id"] == cred["client_id"]

        assert revoke_token(tok["access_token"]) is True

        with pytest.raises(ValueError):
            validate_token(tok["access_token"])

        assert revoke_token(tok["access_token"]) is False
