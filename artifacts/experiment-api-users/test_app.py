"""Unit tests for user management module."""

import pytest
from app import create_user, get_user, list_users, delete_user, _users, _next_id
import app


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module state before each test."""
    app._users.clear()
    app._next_id = 1
    yield


def test_create_user_returns_dict():
    user = create_user("Alice", "alice@example.com")
    assert user == {"id": 1, "name": "Alice", "email": "alice@example.com"}


def test_create_user_auto_increments_id():
    u1 = create_user("A", "a@x.com")
    u2 = create_user("B", "b@x.com")
    assert u1["id"] == 1
    assert u2["id"] == 2


def test_create_user_invalid_email():
    with pytest.raises(ValueError):
        create_user("Bad", "no-at-sign")


def test_get_user_found():
    create_user("Alice", "alice@example.com")
    assert get_user(1) == {"id": 1, "name": "Alice", "email": "alice@example.com"}


def test_get_user_not_found():
    assert get_user(999) is None


def test_list_users_empty():
    assert list_users() == []


def test_list_users_multiple():
    create_user("A", "a@x.com")
    create_user("B", "b@x.com")
    users = list_users()
    assert len(users) == 2


def test_delete_user_success():
    create_user("A", "a@x.com")
    assert delete_user(1) is True
    assert get_user(1) is None


def test_delete_user_not_found():
    assert delete_user(999) is False
