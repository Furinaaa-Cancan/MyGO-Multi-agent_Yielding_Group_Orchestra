"""Ground truth tests for task-auth-session: session-based authentication."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-auth-session"))


def test_register_user():
    from session_auth import register
    user = register("alice_sess", "password123", role="user")
    assert user["username"] == "alice_sess"
    assert user["role"] == "user"
    assert "user_id" in user


def test_register_duplicate_raises():
    from session_auth import register
    register("bob_sess", "password123")
    with pytest.raises(ValueError):
        register("bob_sess", "password456")


def test_register_short_password_raises():
    from session_auth import register
    with pytest.raises(ValueError):
        register("short_sess", "1234567")


def test_register_invalid_role_raises():
    from session_auth import register
    with pytest.raises(ValueError):
        register("role_test", "password123", role="superuser")


def test_login_returns_session():
    from session_auth import register, login
    register("carol_sess", "password123")
    session = login("carol_sess", "password123")
    assert "session_id" in session
    assert "user_id" in session
    assert "expires_at" in session


def test_login_wrong_password_raises():
    from session_auth import register, login
    register("dave_sess", "password123")
    with pytest.raises(ValueError):
        login("dave_sess", "wrongpassword")


def test_get_session():
    from session_auth import register, login, get_session
    register("eve_sess", "password123", role="admin")
    session = login("eve_sess", "password123")
    info = get_session(session["session_id"])
    assert info is not None
    assert info["username"] == "eve_sess"
    assert info["role"] == "admin"


def test_get_session_nonexistent():
    from session_auth import get_session
    assert get_session("nonexistent-session-id") is None


def test_logout():
    from session_auth import register, login, logout, get_session
    register("frank_sess", "password123")
    session = login("frank_sess", "password123")
    assert logout(session["session_id"]) is True
    assert get_session(session["session_id"]) is None


def test_logout_nonexistent():
    from session_auth import logout
    assert logout("nonexistent-session") is False


def test_list_active_sessions():
    from session_auth import register, login, list_active_sessions
    user = register("grace_sess", "password123")
    login("grace_sess", "password123")
    login("grace_sess", "password123")
    sessions = list_active_sessions(user["user_id"])
    assert len(sessions) >= 2


def test_require_role_admin_has_all():
    from session_auth import register, login, require_role
    register("admin_sess", "password123", role="admin")
    session = login("admin_sess", "password123")
    assert require_role(session["session_id"], "user") is True
    assert require_role(session["session_id"], "moderator") is True
    assert require_role(session["session_id"], "admin") is True


def test_require_role_insufficient():
    from session_auth import register, login, require_role
    register("user_sess", "password123", role="user")
    session = login("user_sess", "password123")
    with pytest.raises(PermissionError):
        require_role(session["session_id"], "admin")


def test_require_role_invalid_session():
    from session_auth import require_role
    with pytest.raises(PermissionError):
        require_role("invalid-session-id", "user")
