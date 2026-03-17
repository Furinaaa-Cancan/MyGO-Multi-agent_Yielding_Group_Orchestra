"""Unit tests for user read operations."""

import importlib

import pytest

import app


@pytest.fixture(autouse=True)
def reset_store():
    """Reset the in-memory store before each test."""
    importlib.reload(app)
    yield


class TestGetUser:
    def test_get_existing_user(self):
        user = app.create_user("Alice", "alice@example.com")
        result = app.get_user(1)
        assert result == user

    def test_get_nonexistent_user(self):
        assert app.get_user(999) is None

    def test_get_user_returns_same_structure_as_create(self):
        created = app.create_user("Bob", "bob@example.com")
        fetched = app.get_user(created["id"])
        assert fetched is not None
        assert set(fetched.keys()) == set(created.keys())
        assert fetched == created


class TestListUsers:
    def test_list_users_empty(self):
        assert app.list_users() == []

    def test_list_users_returns_all(self):
        u1 = app.create_user("A", "a@example.com")
        u2 = app.create_user("B", "b@example.com")
        users = app.list_users()
        assert u1 in users
        assert u2 in users

    def test_list_users_count_matches(self):
        app.create_user("A", "a@example.com")
        app.create_user("B", "b@example.com")
        app.create_user("C", "c@example.com")
        assert len(app.list_users()) == 3
