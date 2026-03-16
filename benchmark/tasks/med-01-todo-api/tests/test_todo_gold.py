"""Gold-standard tests for Todo REST API task."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


class TestCreateTodo:
    def test_create_success(self):
        resp = client.post("/todos", json={"title": "Buy milk"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Buy milk"
        assert data["completed"] is False
        assert "id" in data
        assert "created_at" in data

    def test_create_with_description(self):
        resp = client.post("/todos", json={"title": "Test", "description": "Details"})
        assert resp.status_code == 201
        assert resp.json()["description"] == "Details"

    def test_create_empty_title_fails(self):
        resp = client.post("/todos", json={"title": ""})
        assert resp.status_code == 422 or resp.status_code == 400

    def test_create_missing_title_fails(self):
        resp = client.post("/todos", json={})
        assert resp.status_code == 422


class TestListTodos:
    def test_list_empty(self):
        # Create fresh — list may have items from other tests
        resp = client.get("/todos")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_filter_completed(self):
        # Create one completed and one not
        r1 = client.post("/todos", json={"title": "Done"}).json()
        client.patch(f"/todos/{r1['id']}", json={"completed": True})
        client.post("/todos", json={"title": "Not done"})

        completed = client.get("/todos", params={"completed": True})
        assert completed.status_code == 200
        for item in completed.json():
            assert item["completed"] is True


class TestGetTodo:
    def test_get_existing(self):
        created = client.post("/todos", json={"title": "Find me"}).json()
        resp = client.get(f"/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Find me"

    def test_get_not_found(self):
        resp = client.get("/todos/nonexistent-id")
        assert resp.status_code == 404


class TestUpdateTodo:
    def test_update_title(self):
        created = client.post("/todos", json={"title": "Old"}).json()
        resp = client.patch(f"/todos/{created['id']}", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    def test_update_completed(self):
        created = client.post("/todos", json={"title": "Toggle"}).json()
        resp = client.patch(f"/todos/{created['id']}", json={"completed": True})
        assert resp.status_code == 200
        assert resp.json()["completed"] is True

    def test_update_not_found(self):
        resp = client.patch("/todos/nonexistent-id", json={"title": "X"})
        assert resp.status_code == 404


class TestDeleteTodo:
    def test_delete_existing(self):
        created = client.post("/todos", json={"title": "Delete me"}).json()
        resp = client.delete(f"/todos/{created['id']}")
        assert resp.status_code == 204

        # Verify deleted
        get_resp = client.get(f"/todos/{created['id']}")
        assert get_resp.status_code == 404

    def test_delete_not_found(self):
        resp = client.delete("/todos/nonexistent-id")
        assert resp.status_code == 404
