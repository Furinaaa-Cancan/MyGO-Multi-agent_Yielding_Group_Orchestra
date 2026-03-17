"""Unit tests for POST /users endpoint."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.routers import users as users_router


@pytest.fixture(autouse=True)
def clear_store():
    """Reset in-memory store before each test."""
    users_router._users.clear()
    yield
    users_router._users.clear()


client = TestClient(app)


def test_create_user_success():
    resp = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["email"] == "alice@example.com"
    assert "id" in data


def test_create_user_returns_unique_ids():
    r1 = client.post("/users", json={"name": "Bob", "email": "bob@example.com"})
    r2 = client.post("/users", json={"name": "Carol", "email": "carol@example.com"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


def test_create_user_duplicate_email_returns_409():
    client.post("/users", json={"name": "Dave", "email": "dave@example.com"})
    resp = client.post("/users", json={"name": "Dave2", "email": "dave@example.com"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_create_user_missing_name_returns_422():
    resp = client.post("/users", json={"email": "noname@example.com"})
    assert resp.status_code == 422


def test_create_user_invalid_email_returns_422():
    resp = client.post("/users", json={"name": "Eve", "email": "not-an-email"})
    assert resp.status_code == 422


def test_create_user_empty_name_returns_422():
    resp = client.post("/users", json={"name": "", "email": "empty@example.com"})
    assert resp.status_code == 422


def test_create_user_response_schema():
    resp = client.post("/users", json={"name": "Frank", "email": "frank@example.com"})
    assert resp.status_code == 201
    data = resp.json()
    assert set(data.keys()) == {"id", "name", "email"}


# --- GET /users/{user_id} tests ---


def test_get_user_existing():
    create_resp = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    user_id = create_resp.json()["id"]
    resp = client.get(f"/users/{user_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == user_id
    assert data["name"] == "Alice"
    assert data["email"] == "alice@example.com"


def test_get_user_not_found():
    resp = client.get("/users/999")
    assert resp.status_code == 404


def test_get_user_matches_create_response():
    create_resp = client.post("/users", json={"name": "Bob", "email": "bob@example.com"})
    created = create_resp.json()
    get_resp = client.get(f"/users/{created['id']}")
    assert get_resp.json() == created


# --- GET /users tests ---


def test_list_users_empty():
    resp = client.get("/users")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_users_returns_all():
    client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    client.post("/users", json={"name": "Bob", "email": "bob@example.com"})
    resp = client.get("/users")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_list_users_count_matches_created():
    for i in range(5):
        client.post("/users", json={"name": f"User{i}", "email": f"user{i}@example.com"})
    resp = client.get("/users")
    assert len(resp.json()) == 5


# --- DELETE /users/{user_id} HTTP endpoint tests ---


def test_delete_user_endpoint_existing_returns_true():
    create_resp = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    user_id = create_resp.json()["id"]
    resp = client.delete(f"/users/{user_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_delete_user_endpoint_nonexistent_returns_false():
    resp = client.delete("/users/999")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is False


# --- delete_user() function-level tests (done criteria) ---


def test_delete_user_returns_true_when_exists():
    """delete_user(id) returns True when user exists and removes from storage."""
    create_resp = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    user_id = create_resp.json()["id"]
    result = users_router.delete_user(user_id)
    assert result is True


def test_delete_user_returns_false_for_nonexistent():
    """delete_user(999) returns False."""
    result = users_router.delete_user(999)
    assert result is False


def test_delete_user_get_user_returns_none():
    """After deletion, get_user(deleted_id) returns None."""
    create_resp = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    user_id = create_resp.json()["id"]
    users_router.delete_user(user_id)
    assert users_router.get_user(user_id) is None


def test_delete_user_not_in_list_users():
    """After deletion, get_user and list_users no longer contain the user."""
    r1 = client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    client.post("/users", json={"name": "Bob", "email": "bob@example.com"})
    user_id = r1.json()["id"]
    users_router.delete_user(user_id)
    assert users_router.get_user(user_id) is None
    remaining = users_router.list_users()
    assert all(u["id"] != user_id for u in remaining)


def test_delete_user_list_length_decreases():
    """After deletion, list_users() length decreases by 1."""
    ids = []
    for i in range(3):
        r = client.post("/users", json={"name": f"User{i}", "email": f"u{i}@example.com"})
        ids.append(r.json()["id"])
    assert len(users_router.list_users()) == 3
    users_router.delete_user(ids[0])
    assert len(users_router.list_users()) == 2


def test_delete_nonexistent_does_not_affect_others():
    """Deleting nonexistent user returns False and doesn't affect other data."""
    client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    client.post("/users", json={"name": "Bob", "email": "bob@example.com"})
    before = users_router.list_users()
    result = users_router.delete_user(999)
    assert result is False
    after = users_router.list_users()
    assert len(before) == len(after)
    assert before == after
