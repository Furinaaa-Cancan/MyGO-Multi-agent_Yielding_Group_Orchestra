"""Test suite for the user authentication system."""

import pytest
from fastapi.testclient import TestClient

from app import app
from database import clear_db, create_user, get_user_by_email
from auth import hash_password


@pytest.fixture(autouse=True)
def clean_db():
    clear_db()
    yield
    clear_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def registered_user(client):
    response = client.post("/auth/register", json={
        "email": "test@example.com",
        "password": "Password1",
        "name": "Test User",
    })
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def auth_token(client, registered_user):
    response = client.post("/auth/login", json={
        "email": "test@example.com",
        "password": "Password1",
    })
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture
def admin_user(client):
    """Create an admin user directly in the database."""
    import uuid
    from datetime import datetime, timezone
    user = {
        "id": str(uuid.uuid4()),
        "email": "admin@example.com",
        "hashed_password": hash_password("AdminPass1"),
        "name": "Admin User",
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    create_user(user)
    response = client.post("/auth/login", json={
        "email": "admin@example.com",
        "password": "AdminPass1",
    })
    assert response.status_code == 200
    return response.json()["access_token"]


# --- Registration Tests ---

class TestRegister:
    def test_register_success(self, client):
        response = client.post("/auth/register", json={
            "email": "new@example.com",
            "password": "ValidPass1",
            "name": "New User",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "new@example.com"
        assert data["name"] == "New User"
        assert data["role"] == "user"
        assert "id" in data
        assert "created_at" in data
        assert "password" not in data
        assert "hashed_password" not in data

    def test_register_duplicate_email(self, client, registered_user):
        response = client.post("/auth/register", json={
            "email": "test@example.com",
            "password": "Password1",
            "name": "Another User",
        })
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"].lower()

    def test_register_invalid_email(self, client):
        response = client.post("/auth/register", json={
            "email": "notanemail",
            "password": "Password1",
            "name": "Bad Email",
        })
        assert response.status_code == 422

    def test_register_short_password(self, client):
        response = client.post("/auth/register", json={
            "email": "short@example.com",
            "password": "Short1",
            "name": "Short Pass",
        })
        assert response.status_code == 422

    def test_register_no_uppercase(self, client):
        response = client.post("/auth/register", json={
            "email": "noup@example.com",
            "password": "nouppercase1",
            "name": "No Upper",
        })
        assert response.status_code == 422

    def test_register_no_digit(self, client):
        response = client.post("/auth/register", json={
            "email": "nodigit@example.com",
            "password": "NoDigitHere",
            "name": "No Digit",
        })
        assert response.status_code == 422


# --- Login Tests ---

class TestLogin:
    def test_login_success(self, client, registered_user):
        response = client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "Password1",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client, registered_user):
        response = client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "WrongPass1",
        })
        assert response.status_code == 401

    def test_login_nonexistent_user(self, client):
        response = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "Password1",
        })
        assert response.status_code == 401


# --- /auth/me Tests ---

class TestMe:
    def test_get_me_success(self, client, auth_token):
        response = client.get("/auth/me", headers={
            "Authorization": f"Bearer {auth_token}",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"
        assert "hashed_password" not in data

    def test_get_me_no_token(self, client):
        response = client.get("/auth/me")
        assert response.status_code in (401, 403)

    def test_get_me_invalid_token(self, client):
        response = client.get("/auth/me", headers={
            "Authorization": "Bearer invalidtoken123",
        })
        assert response.status_code in (401, 403)


# --- /users Tests ---

class TestUsers:
    def test_list_users_as_admin(self, client, registered_user, admin_user):
        response = client.get("/users", headers={
            "Authorization": f"Bearer {admin_user}",
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        for user in data:
            assert "hashed_password" not in user

    def test_list_users_as_regular_user(self, client, auth_token):
        response = client.get("/users", headers={
            "Authorization": f"Bearer {auth_token}",
        })
        assert response.status_code == 403

    def test_list_users_no_token(self, client):
        response = client.get("/users")
        assert response.status_code in (401, 403)
