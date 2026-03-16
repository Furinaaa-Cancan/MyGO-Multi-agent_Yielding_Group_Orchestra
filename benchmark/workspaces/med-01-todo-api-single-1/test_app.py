import pytest
from fastapi.testclient import TestClient
from app import app, todos


@pytest.fixture(autouse=True)
def clear_todos():
    todos.clear()
    yield
    todos.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_create_todo(client):
    response = client.post("/todos", json={"title": "Test todo", "description": "A test"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test todo"
    assert data["description"] == "A test"
    assert data["completed"] is False
    assert "id" in data
    assert "created_at" in data


def test_create_todo_minimal(client):
    response = client.post("/todos", json={"title": "Just a title"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Just a title"
    assert data["description"] == ""


def test_create_todo_empty_title(client):
    response = client.post("/todos", json={"title": ""})
    assert response.status_code == 422


def test_create_todo_missing_title(client):
    response = client.post("/todos", json={"description": "no title"})
    assert response.status_code == 422


def test_list_todos_empty(client):
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == []


def test_list_todos(client):
    client.post("/todos", json={"title": "Todo 1"})
    client.post("/todos", json={"title": "Todo 2"})
    response = client.get("/todos")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_todos_filter_completed(client):
    client.post("/todos", json={"title": "Todo 1"})
    resp = client.post("/todos", json={"title": "Todo 2"})
    todo_id = resp.json()["id"]
    client.patch(f"/todos/{todo_id}", json={"completed": True})

    response = client.get("/todos", params={"completed": True})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["completed"] is True

    response = client.get("/todos", params={"completed": False})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["completed"] is False


def test_get_todo(client):
    resp = client.post("/todos", json={"title": "My todo"})
    todo_id = resp.json()["id"]
    response = client.get(f"/todos/{todo_id}")
    assert response.status_code == 200
    assert response.json()["title"] == "My todo"


def test_get_todo_not_found(client):
    response = client.get("/todos/nonexistent-id")
    assert response.status_code == 404


def test_update_todo(client):
    resp = client.post("/todos", json={"title": "Original"})
    todo_id = resp.json()["id"]

    response = client.patch(f"/todos/{todo_id}", json={"title": "Updated", "completed": True})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated"
    assert data["completed"] is True


def test_update_todo_partial(client):
    resp = client.post("/todos", json={"title": "Original", "description": "Desc"})
    todo_id = resp.json()["id"]

    response = client.patch(f"/todos/{todo_id}", json={"description": "New desc"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Original"
    assert data["description"] == "New desc"


def test_update_todo_not_found(client):
    response = client.patch("/todos/nonexistent-id", json={"title": "Nope"})
    assert response.status_code == 404


def test_delete_todo(client):
    resp = client.post("/todos", json={"title": "To delete"})
    todo_id = resp.json()["id"]

    response = client.delete(f"/todos/{todo_id}")
    assert response.status_code == 204

    response = client.get(f"/todos/{todo_id}")
    assert response.status_code == 404


def test_delete_todo_not_found(client):
    response = client.delete("/todos/nonexistent-id")
    assert response.status_code == 404
