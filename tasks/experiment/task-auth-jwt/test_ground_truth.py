"""Ground truth tests for task-auth-jwt: JWT authentication module."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-auth-jwt"))


def test_register_returns_user():
    from auth import register
    result = register("alice_jwt", "password123")
    assert "user_id" in result
    assert result["username"] == "alice_jwt"


def test_register_duplicate_raises():
    from auth import register
    register("bob_jwt", "password123")
    with pytest.raises(ValueError):
        register("bob_jwt", "password456")


def test_register_short_password_raises():
    from auth import register
    with pytest.raises(ValueError):
        register("short_pwd", "1234567")  # 7 chars


def test_login_returns_token():
    from auth import register, login
    register("carol_jwt", "password123")
    result = login("carol_jwt", "password123")
    assert "token" in result
    assert isinstance(result["token"], str)
    assert len(result["token"]) > 0


def test_login_wrong_password_raises():
    from auth import register, login
    register("dave_jwt", "password123")
    with pytest.raises(ValueError, match="[Ii]nvalid"):
        login("dave_jwt", "wrongpassword")


def test_login_nonexistent_user_raises():
    from auth import login
    with pytest.raises(ValueError, match="[Ii]nvalid"):
        login("nonexistent_user_xyz", "password123")


def test_verify_token_valid():
    from auth import register, login, verify_token
    register("eve_jwt", "password123")
    token_data = login("eve_jwt", "password123")
    result = verify_token(token_data["token"])
    assert "user_id" in result
    assert result["username"] == "eve_jwt"


def test_verify_token_invalid():
    from auth import verify_token
    with pytest.raises(ValueError, match="[Ii]nvalid"):
        verify_token("totally.invalid.token")


def test_change_password_success():
    from auth import register, change_password, login
    user = register("frank_jwt", "password123")
    result = change_password(user["user_id"], "password123", "newpassword123")
    assert result is True
    # Should be able to login with new password
    login("frank_jwt", "newpassword123")


def test_change_password_wrong_old():
    from auth import register, change_password
    user = register("grace_jwt", "password123")
    with pytest.raises(ValueError):
        change_password(user["user_id"], "wrongold", "newpassword123")


def test_password_not_stored_plaintext():
    """Verify passwords are hashed, not stored as plain text."""
    from auth import register
    import auth
    register("hash_check_user", "mysecretpassword")
    # Check internal storage - password should not appear in plain text
    # This is a best-effort check; the exact storage mechanism may vary
    storage_str = str(vars(auth))
    assert "mysecretpassword" not in storage_str, "Password appears to be stored in plain text"
