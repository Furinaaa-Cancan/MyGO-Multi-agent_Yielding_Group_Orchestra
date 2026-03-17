"""Unit tests for delete_user and related functions."""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import app as app_mod


def _reset_store():
    """Reset the in-memory store between tests."""
    app_mod._store.clear()
    app_mod._next_id = 1


def test_delete_existing_user_returns_true():
    _reset_store()
    user = app_mod.create_user("Alice", "alice@example.com")
    assert app_mod.delete_user(user["id"]) is True


def test_delete_nonexistent_user_returns_false():
    _reset_store()
    assert app_mod.delete_user(999) is False


def test_deleted_user_not_in_get_user():
    _reset_store()
    user = app_mod.create_user("Bob", "bob@example.com")
    app_mod.delete_user(user["id"])
    assert app_mod.get_user(user["id"]) is None


def test_deleted_user_not_in_list_users():
    _reset_store()
    user = app_mod.create_user("Carol", "carol@example.com")
    app_mod.create_user("Dave", "dave@example.com")
    initial_count = len(app_mod.list_users())
    app_mod.delete_user(user["id"])
    assert len(app_mod.list_users()) == initial_count - 1
    assert all(u["id"] != user["id"] for u in app_mod.list_users())


def test_delete_does_not_affect_other_users():
    _reset_store()
    user1 = app_mod.create_user("Eve", "eve@example.com")
    user2 = app_mod.create_user("Frank", "frank@example.com")
    app_mod.delete_user(user1["id"])
    assert app_mod.get_user(user2["id"]) is not None
    assert len(app_mod.list_users()) == 1
