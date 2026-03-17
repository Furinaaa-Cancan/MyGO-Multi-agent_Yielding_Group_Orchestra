"""Ground truth tests for task-api-users: user management API."""
import sys
from pathlib import Path
import pytest

# Add artifacts to path so we can import the module
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-api-users"))


def test_create_user_returns_dict():
    from app import create_user
    result = create_user("Alice", "alice@example.com")
    assert isinstance(result, dict)
    assert "id" in result
    assert result["name"] == "Alice"
    assert result["email"] == "alice@example.com"


def test_create_user_autoincrement_id():
    from app import create_user, list_users
    # Clear state by reimporting if possible
    u1 = create_user("Bob", "bob@example.com")
    u2 = create_user("Carol", "carol@example.com")
    assert u2["id"] > u1["id"]


def test_create_user_invalid_email_raises():
    from app import create_user
    with pytest.raises(ValueError):
        create_user("Dave", "no-at-sign")


def test_get_user_exists():
    from app import create_user, get_user
    user = create_user("Eve", "eve@example.com")
    found = get_user(user["id"])
    assert found is not None
    assert found["name"] == "Eve"


def test_get_user_not_exists():
    from app import get_user
    result = get_user(99999)
    assert result is None


def test_list_users_returns_list():
    from app import list_users
    result = list_users()
    assert isinstance(result, list)


def test_delete_user():
    from app import create_user, delete_user, get_user
    user = create_user("Frank", "frank@example.com")
    assert delete_user(user["id"]) is True
    assert get_user(user["id"]) is None


def test_delete_nonexistent_user():
    from app import delete_user
    assert delete_user(99999) is False
