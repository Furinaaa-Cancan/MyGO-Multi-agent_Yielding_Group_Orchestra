"""Gold-standard tests for Todo REST API task.

Each test class uses a fresh TestClient to avoid shared state.
Tests are self-contained: they create their own data and verify it,
never depending on state from other tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create a fresh TestClient per test to ensure isolation."""
    from app import app
    return TestClient(app)


class TestCreateTodo:
    def test_create_success(self, client):
        resp = client.post("/todos", json={"title": "Buy milk"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Buy milk"
        assert data["completed"] is False
        assert "id" in data
        assert "created_at" in data

    def test_create_with_description(self, client):
        resp = client.post("/todos", json={"title": "Test", "description": "Details"})
        assert resp.status_code == 201
        assert resp.json()["description"] == "Details"

    def test_create_empty_title_fails(self, client):
        resp = client.post("/todos", json={"title": ""})
        assert resp.status_code in (400, 422)

    def test_create_missing_title_fails(self, client):
        resp = client.post("/todos", json={})
        assert resp.status_code == 422

    def test_create_and_retrieve(self, client):
        """Verify created todo is actually persisted and retrievable."""
        resp = client.post("/todos", json={"title": "Persist me"})
        assert resp.status_code == 201
        todo_id = resp.json()["id"]

        get_resp = client.get(f"/todos/{todo_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "Persist me"


class TestListTodos:
    def test_list_returns_array(self, client):
        resp = client.get("/todos")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_includes_created(self, client):
        """Verify created todo appears in the list."""
        client.post("/todos", json={"title": "Listed item"})
        resp = client.get("/todos")
        assert resp.status_code == 200
        titles = [t["title"] for t in resp.json()]
        assert "Listed item" in titles

    def test_list_filter_completed(self, client):
        # Create one completed and one not
        r1 = client.post("/todos", json={"title": "Done"}).json()
        client.patch(f"/todos/{r1['id']}", json={"completed": True})
        client.post("/todos", json={"title": "Not done"})

        completed = client.get("/todos", params={"completed": True})
        assert completed.status_code == 200
        for item in completed.json():
            assert item["completed"] is True


class TestGetTodo:
    def test_get_existing(self, client):
        created = client.post("/todos", json={"title": "Find me"}).json()
        resp = client.get(f"/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Find me"

    def test_get_not_found(self, client):
        resp = client.get("/todos/nonexistent-id-99999")
        assert resp.status_code == 404


class TestUpdateTodo:
    def test_update_title(self, client):
        created = client.post("/todos", json={"title": "Old"}).json()
        resp = client.patch(f"/todos/{created['id']}", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    def test_update_completed(self, client):
        created = client.post("/todos", json={"title": "Toggle"}).json()
        resp = client.patch(f"/todos/{created['id']}", json={"completed": True})
        assert resp.status_code == 200
        assert resp.json()["completed"] is True

    def test_update_not_found(self, client):
        resp = client.patch("/todos/nonexistent-id-99999", json={"title": "X"})
        assert resp.status_code == 404


class TestDeleteTodo:
    def test_delete_existing(self, client):
        created = client.post("/todos", json={"title": "Delete me"}).json()
        resp = client.delete(f"/todos/{created['id']}")
        assert resp.status_code == 204

        # Verify deleted
        get_resp = client.get(f"/todos/{created['id']}")
        assert get_resp.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/todos/nonexistent-id-99999")
        assert resp.status_code == 404
