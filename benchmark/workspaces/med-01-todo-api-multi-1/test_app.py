"""Tests for the Todo API."""

import pytest
from fastapi.testclient import TestClient

from app import app, todos


@pytest.fixture(autouse=True)
def clear_todos():
    """Clear the in-memory store before each test."""
    todos.clear()
    yield
    todos.clear()


@pytest.fixture
def client():
    return TestClient(app)


# --- POST /todos ---

def test_create_todo(client):
    response = client.post("/todos", json={"title": "Buy groceries"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Buy groceries"
    assert data["description"] == ""
    assert data["completed"] is False
    assert "id" in data
    assert "created_at" in data


def test_create_todo_with_description(client):
    response = client.post(
        "/todos", json={"title": "Buy groceries", "description": "Milk and eggs"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Milk and eggs"


def test_create_todo_empty_title(client):
    response = client.post("/todos", json={"title": ""})
    assert response.status_code == 422


def test_create_todo_whitespace_title(client):
    response = client.post("/todos", json={"title": "   "})
    assert response.status_code == 422


def test_create_todo_missing_title(client):
    response = client.post("/todos", json={"description": "no title"})
    assert response.status_code == 422


def test_create_todo_no_body(client):
    response = client.post("/todos")
    assert response.status_code == 422


def test_create_todo_title_stripped(client):
    response = client.post("/todos", json={"title": "  Buy milk  "})
    assert response.status_code == 201
    assert response.json()["title"] == "Buy milk"


# --- GET /todos ---

def test_list_todos_empty(client):
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == []


def test_list_todos(client):
    client.post("/todos", json={"title": "First"})
    client.post("/todos", json={"title": "Second"})
    response = client.get("/todos")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_todos_filter_completed(client):
    client.post("/todos", json={"title": "Todo 1"})
    resp2 = client.post("/todos", json={"title": "Todo 2"})
    todo2_id = resp2.json()["id"]
    client.patch(f"/todos/{todo2_id}", json={"completed": True})

    # Filter completed=true
    response = client.get("/todos", params={"completed": True})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["completed"] is True

    # Filter completed=false
    response = client.get("/todos", params={"completed": False})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["completed"] is False


# --- GET /todos/{id} ---

def test_get_todo(client):
    resp = client.post("/todos", json={"title": "My todo"})
    todo_id = resp.json()["id"]
    response = client.get(f"/todos/{todo_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "My todo"
    assert data["id"] == todo_id
    assert data["description"] == ""
    assert data["completed"] is False
    assert "created_at" in data


def test_get_todo_not_found(client):
    response = client.get("/todos/nonexistent-id")
    assert response.status_code == 404


# --- PATCH /todos/{id} ---

def test_update_todo_title(client):
    resp = client.post("/todos", json={"title": "Old title"})
    todo_id = resp.json()["id"]
    response = client.patch(f"/todos/{todo_id}", json={"title": "New title"})
    assert response.status_code == 200
    assert response.json()["title"] == "New title"


def test_update_todo_completed(client):
    resp = client.post("/todos", json={"title": "My task"})
    todo_id = resp.json()["id"]
    response = client.patch(f"/todos/{todo_id}", json={"completed": True})
    assert response.status_code == 200
    assert response.json()["completed"] is True


def test_update_todo_description(client):
    resp = client.post("/todos", json={"title": "Task"})
    todo_id = resp.json()["id"]
    response = client.patch(
        f"/todos/{todo_id}", json={"description": "Updated desc"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated desc"


def test_update_todo_not_found(client):
    response = client.patch("/todos/nonexistent-id", json={"title": "X"})
    assert response.status_code == 404


def test_update_todo_empty_title(client):
    resp = client.post("/todos", json={"title": "Valid"})
    todo_id = resp.json()["id"]
    response = client.patch(f"/todos/{todo_id}", json={"title": ""})
    assert response.status_code == 422


def test_update_todo_whitespace_title(client):
    resp = client.post("/todos", json={"title": "Valid"})
    todo_id = resp.json()["id"]
    response = client.patch(f"/todos/{todo_id}", json={"title": "   "})
    assert response.status_code == 422


def test_update_multiple_fields(client):
    resp = client.post("/todos", json={"title": "Original", "description": "Desc"})
    todo_id = resp.json()["id"]
    response = client.patch(
        f"/todos/{todo_id}",
        json={"title": "Updated", "completed": True, "description": "New desc"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated"
    assert data["completed"] is True
    assert data["description"] == "New desc"


# --- DELETE /todos/{id} ---

def test_delete_todo(client):
    resp = client.post("/todos", json={"title": "To delete"})
    todo_id = resp.json()["id"]
    response = client.delete(f"/todos/{todo_id}")
    assert response.status_code == 204
    assert response.content == b""

    # Verify it's gone
    response = client.get(f"/todos/{todo_id}")
    assert response.status_code == 404


def test_delete_todo_not_found(client):
    response = client.delete("/todos/nonexistent-id")
    assert response.status_code == 404
