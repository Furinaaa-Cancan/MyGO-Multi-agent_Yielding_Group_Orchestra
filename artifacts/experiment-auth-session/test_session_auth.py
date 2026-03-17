"""Tests for session authentication module."""

import time

import pytest

from session_auth import (
    _sessions,
    _usernames,
    _users,
    get_session,
    list_active_sessions,
    login,
    logout,
    register,
    require_role,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """Reset module state before each test."""
    import session_auth

    _users.clear()
    _usernames.clear()
    _sessions.clear()
    session_auth._next_user_id = 1


class TestRegister:
    def test_register_success(self):
        result = register("alice", "password123")
        assert result["username"] == "alice"
        assert result["role"] == "user"
        assert "user_id" in result

    def test_register_with_role(self):
        result = register("bob", "password123", role="admin")
        assert result["role"] == "admin"

    def test_register_moderator(self):
        result = register("mod", "password123", role="moderator")
        assert result["role"] == "moderator"

    def test_register_duplicate_username(self):
        register("alice", "password123")
        with pytest.raises(ValueError, match="already exists"):
            register("alice", "password456")

    def test_register_short_password(self):
        with pytest.raises(ValueError, match="at least 8"):
            register("alice", "short")

    def test_register_invalid_role(self):
        with pytest.raises(ValueError, match="Invalid role"):
            register("alice", "password123", role="superuser")

    def test_register_increments_user_id(self):
        r1 = register("alice", "password123")
        r2 = register("bob", "password456")
        assert r2["user_id"] == r1["user_id"] + 1


class TestLogin:
    def test_login_success(self):
        register("alice", "password123")
        result = login("alice", "password123")
        assert "session_id" in result
        assert "user_id" in result
        assert "expires_at" in result
        assert result["expires_at"] > int(time.time())

    def test_login_wrong_password(self):
        register("alice", "password123")
        with pytest.raises(ValueError, match="Invalid"):
            login("alice", "wrongpassword")

    def test_login_nonexistent_user(self):
        with pytest.raises(ValueError, match="Invalid"):
            login("nobody", "password123")

    def test_login_session_id_length(self):
        register("alice", "password123")
        result = login("alice", "password123")
        assert len(result["session_id"]) == 64  # 32 bytes hex

    def test_login_multiple_sessions(self):
        register("alice", "password123")
        s1 = login("alice", "password123")
        s2 = login("alice", "password123")
        assert s1["session_id"] != s2["session_id"]


class TestGetSession:
    def test_get_session_success(self):
        reg = register("alice", "password123")
        sess = login("alice", "password123")
        result = get_session(sess["session_id"])
        assert result is not None
        assert result["user_id"] == reg["user_id"]
        assert result["username"] == "alice"
        assert result["role"] == "user"
        assert "expires_at" in result

    def test_get_session_not_found(self):
        assert get_session("nonexistent") is None


class TestLogout:
    def test_logout_success(self):
        register("alice", "password123")
        sess = login("alice", "password123")
        assert logout(sess["session_id"]) is True
        assert get_session(sess["session_id"]) is None

    def test_logout_nonexistent(self):
        assert logout("nonexistent") is False

    def test_logout_idempotent(self):
        register("alice", "password123")
        sess = login("alice", "password123")
        assert logout(sess["session_id"]) is True
        assert logout(sess["session_id"]) is False


class TestListActiveSessions:
    def test_list_active_sessions(self):
        reg = register("alice", "password123")
        login("alice", "password123")
        login("alice", "password123")
        sessions = list_active_sessions(reg["user_id"])
        assert len(sessions) == 2

    def test_list_no_sessions(self):
        reg = register("alice", "password123")
        assert list_active_sessions(reg["user_id"]) == []

    def test_list_excludes_other_users(self):
        r1 = register("alice", "password123")
        register("bob", "password456")
        login("alice", "password123")
        login("bob", "password456")
        sessions = list_active_sessions(r1["user_id"])
        assert len(sessions) == 1
        assert sessions[0]["username"] == "alice"


class TestRequireRole:
    def test_require_role_exact_match(self):
        register("alice", "password123", role="moderator")
        sess = login("alice", "password123")
        assert require_role(sess["session_id"], "moderator") is True

    def test_require_role_admin_has_all(self):
        register("admin", "password123", role="admin")
        sess = login("admin", "password123")
        assert require_role(sess["session_id"], "user") is True
        assert require_role(sess["session_id"], "moderator") is True
        assert require_role(sess["session_id"], "admin") is True

    def test_require_role_insufficient(self):
        register("alice", "password123", role="user")
        sess = login("alice", "password123")
        with pytest.raises(PermissionError, match="Insufficient"):
            require_role(sess["session_id"], "admin")

    def test_require_role_invalid_session(self):
        with pytest.raises(PermissionError, match="Invalid"):
            require_role("nonexistent", "user")
