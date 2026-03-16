"""Test suite for the user authentication system."""

import pytest
from fastapi.testclient import TestClient

from app import app
from database import clear_db


@pytest.fixture(autouse=True)
def clean_db():
    """Clear the in-memory database before each test."""
    clear_db()
    yield
    clear_db()


client = TestClient(app)


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_success(self):
        response = client.post("/auth/register", json={
            "email": "alice@example.com",
            "password": "Secret1234",
            "name": "Alice",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "alice@example.com"
        assert data["name"] == "Alice"
        assert data["role"] == "user"
        assert "id" in data
        assert "created_at" in data
        # Password must NOT appear in response
        assert "password" not in data
        assert "hashed_password" not in data

    def test_register_duplicate_email(self):
        client.post("/auth/register", json={
            "email": "alice@example.com",
            "password": "Secret1234",
            "name": "Alice",
        })
        response = client.post("/auth/register", json={
            "email": "alice@example.com",
            "password": "Secret1234",
            "name": "Alice Again",
        })
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"].lower()

    def test_register_password_too_short(self):
        response = client.post("/auth/register", json={
            "email": "bob@example.com",
            "password": "Ab1",
            "name": "Bob",
        })
        assert response.status_code == 422

    def test_register_password_no_uppercase(self):
        response = client.post("/auth/register", json={
            "email": "bob@example.com",
            "password": "alllower1",
            "name": "Bob",
        })
        assert response.status_code == 422

    def test_register_password_no_digit(self):
        response = client.post("/auth/register", json={
            "email": "bob@example.com",
            "password": "NoDigitHere",
            "name": "Bob",
        })
        assert response.status_code == 422

    def test_register_invalid_email(self):
        response = client.post("/auth/register", json={
            "email": "not-an-email",
            "password": "Secret1234",
            "name": "Invalid",
        })
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self):
        client.post("/auth/register", json={
            "email": "alice@example.com",
            "password": "Secret1234",
            "name": "Alice",
        })
        response = client.post("/auth/login", json={
            "email": "alice@example.com",
            "password": "Secret1234",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self):
        client.post("/auth/register", json={
            "email": "alice@example.com",
            "password": "Secret1234",
            "name": "Alice",
        })
        response = client.post("/auth/login", json={
            "email": "alice@example.com",
            "password": "WrongPass1",
        })
        assert response.status_code == 401

    def test_login_nonexistent_user(self):
        response = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "Secret1234",
        })
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# /auth/me tests
# ---------------------------------------------------------------------------

class TestMe:
    def _get_token(self, email="alice@example.com", password="Secret1234", name="Alice"):
        client.post("/auth/register", json={
            "email": email, "password": password, "name": name,
        })
        resp = client.post("/auth/login", json={
            "email": email, "password": password,
        })
        return resp.json()["access_token"]

    def test_me_success(self):
        token = self._get_token()
        response = client.get("/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "alice@example.com"
        assert data["name"] == "Alice"

    def test_me_no_token(self):
        response = client.get("/auth/me")
        assert response.status_code == 401

    def test_me_invalid_token(self):
        response = client.get("/auth/me", headers={
            "Authorization": "Bearer invalid.token.here",
        })
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# /users (admin) tests
# ---------------------------------------------------------------------------

class TestAdminUsers:
    def _register_and_login(self, email, password, name):
        client.post("/auth/register", json={
            "email": email, "password": password, "name": name,
        })
        resp = client.post("/auth/login", json={
            "email": email, "password": password,
        })
        return resp.json()["access_token"]

    def test_users_forbidden_for_regular_user(self):
        token = self._register_and_login(
            "user@example.com", "Secret1234", "Regular"
        )
        response = client.get("/users", headers={
            "Authorization": f"Bearer {token}",
        })
        assert response.status_code == 403

    def test_users_success_for_admin(self):
        # Register a user, then manually promote to admin for testing
        from database import get_user_by_email
        client.post("/auth/register", json={
            "email": "admin@example.com",
            "password": "Admin1234",
            "name": "Admin",
        })
        admin_user = get_user_by_email("admin@example.com")
        admin_user["role"] = "admin"

        resp = client.post("/auth/login", json={
            "email": "admin@example.com",
            "password": "Admin1234",
        })
        token = resp.json()["access_token"]

        # Register another user so the list isn't trivial
        client.post("/auth/register", json={
            "email": "user@example.com",
            "password": "Secret1234",
            "name": "Regular",
        })

        response = client.get("/users", headers={
            "Authorization": f"Bearer {token}",
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Ensure no passwords leak
        for u in data:
            assert "hashed_password" not in u
            assert "password" not in u

    def test_users_no_token(self):
        response = client.get("/users")
        assert response.status_code == 401
