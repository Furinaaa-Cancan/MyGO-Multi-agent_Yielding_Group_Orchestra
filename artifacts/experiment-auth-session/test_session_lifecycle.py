import time
from unittest.mock import patch

import session_auth


def _reset():
    session_auth._users.clear()
    session_auth._sessions.clear()
    session_auth._next_id = 1


def _setup_alice():
    _reset()
    session_auth.register("alice", "password123", "user")


def test_login_success():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    assert "session_id" in result
    assert "user_id" in result
    assert "expires_at" in result
    assert result["user_id"] == 1


def test_login_wrong_password():
    _setup_alice()
    try:
        session_auth.login("alice", "wrong")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_login_nonexistent_user():
    _reset()
    try:
        session_auth.login("nobody", "password123")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_get_session_valid():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    session = session_auth.get_session(result["session_id"])
    assert session is not None
    assert session["user_id"] == 1
    assert session["username"] == "alice"
    assert session["role"] == "user"
    assert "expires_at" in session


def test_get_session_nonexistent():
    _reset()
    assert session_auth.get_session("nonexistent") is None


def test_get_session_expired():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    sid = result["session_id"]
    # Simulate expiration
    session_auth._sessions[sid]["expires_at"] = time.time() - 1
    assert session_auth.get_session(sid) is None


def test_logout_valid():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    assert session_auth.logout(result["session_id"]) is True


def test_logout_nonexistent():
    _reset()
    assert session_auth.logout("nonexistent") is False


def test_logout_double():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    sid = result["session_id"]
    assert session_auth.logout(sid) is True
    assert session_auth.logout(sid) is False


def test_require_role_admin_any_role():
    _reset()
    session_auth.register("admin_user", "password123", "admin")
    result = session_auth.login("admin_user", "password123")
    sid = result["session_id"]
    assert session_auth.require_role(sid, "moderator") is True
    assert session_auth.require_role(sid, "admin") is True
    assert session_auth.require_role(sid, "user") is True


def test_require_role_user_exact_match():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    sid = result["session_id"]
    assert session_auth.require_role(sid, "user") is True


def test_require_role_user_insufficient():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    sid = result["session_id"]
    try:
        session_auth.require_role(sid, "admin")
        assert False, "Should have raised PermissionError"
    except PermissionError:
        pass


def test_require_role_invalid_session():
    _reset()
    try:
        session_auth.require_role("invalid_session", "user")
        assert False, "Should have raised PermissionError"
    except PermissionError:
        pass


def test_require_role_expired_session():
    _setup_alice()
    result = session_auth.login("alice", "password123")
    sid = result["session_id"]
    session_auth._sessions[sid]["expires_at"] = time.time() - 1
    try:
        session_auth.require_role(sid, "user")
        assert False, "Should have raised PermissionError"
    except PermissionError:
        pass


def test_multiple_sessions_same_user():
    _setup_alice()
    s1 = session_auth.login("alice", "password123")
    s2 = session_auth.login("alice", "password123")
    s3 = session_auth.login("alice", "password123")
    sessions = session_auth.list_active_sessions("alice")
    assert len(sessions) == 3
    sids = {s["session_id"] for s in sessions}
    assert s1["session_id"] in sids
    assert s2["session_id"] in sids
    assert s3["session_id"] in sids
