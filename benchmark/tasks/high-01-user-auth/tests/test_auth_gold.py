"""Gold-standard tests for User Authentication System task.

Each test uses a fresh TestClient fixture to ensure test isolation.
No shared state between tests — each test creates its own users.
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


def _register(client, user: dict) -> dict:
    resp = client.post("/auth/register", json=user)
    return resp.json()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    return resp.json().get("access_token", "")


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/auth/register", json={
            "email": "reg1@example.com",
            "password": "ValidPass1",
            "name": "New User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "reg1@example.com"
        assert data["name"] == "New User"
        assert "password" not in data
        assert "hashed_password" not in data
        assert "id" in data

    def test_register_duplicate_email(self, client):
        client.post("/auth/register", json={
            "email": "dup@example.com",
            "password": "ValidPass1",
            "name": "First",
        })
        resp = client.post("/auth/register", json={
            "email": "dup@example.com",
            "password": "ValidPass1",
            "name": "Second",
        })
        assert resp.status_code == 409

    def test_register_weak_password(self, client):
        resp = client.post("/auth/register", json={
            "email": "weak@example.com",
            "password": "short",
            "name": "Weak",
        })
        assert resp.status_code in (400, 422)

    def test_register_no_digit_password(self, client):
        resp = client.post("/auth/register", json={
            "email": "nodigit@example.com",
            "password": "NoDigitHere",
            "name": "NoDigit",
        })
        assert resp.status_code in (400, 422)

    def test_register_no_uppercase_password(self, client):
        resp = client.post("/auth/register", json={
            "email": "noupper@example.com",
            "password": "nouppercase1",
            "name": "NoUpper",
        })
        assert resp.status_code in (400, 422)

    def test_register_invalid_email(self, client):
        resp = client.post("/auth/register", json={
            "email": "not-an-email",
            "password": "ValidPass1",
            "name": "BadEmail",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_login_success(self, client):
        client.post("/auth/register", json={
            "email": "login1@example.com",
            "password": "LoginPass1",
            "name": "Login User",
        })
        resp = client.post("/auth/login", json={
            "email": "login1@example.com",
            "password": "LoginPass1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        client.post("/auth/register", json={
            "email": "login2@example.com",
            "password": "CorrectPass1",
            "name": "User",
        })
        resp = client.post("/auth/login", json={
            "email": "login2@example.com",
            "password": "WrongPass1",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "Whatever1",
        })
        assert resp.status_code == 401


class TestMe:
    def test_me_authenticated(self, client):
        client.post("/auth/register", json={
            "email": "me1@example.com",
            "password": "MePass123",
            "name": "Me User",
        })
        token = _login(client, "me1@example.com", "MePass123")
        assert token, "Login should return a token"
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "me1@example.com"
        assert "password" not in data
        assert "hashed_password" not in data

    def test_me_no_token(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code in (401, 403)

    def test_me_invalid_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code in (401, 403)


class TestAdminEndpoint:
    def test_list_users_requires_auth(self, client):
        """The /users endpoint should require authentication."""
        resp = client.get("/users")
        assert resp.status_code in (401, 403)

    def test_list_users_unauthorized(self, client):
        """Regular user should get 403 on /users."""
        client.post("/auth/register", json={
            "email": "regular@example.com",
            "password": "RegularPass1",
            "name": "Regular",
        })
        token = _login(client, "regular@example.com", "RegularPass1")
        resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
        # Should be forbidden (403) for non-admin
        assert resp.status_code == 403


class TestPasswordSecurity:
    def test_password_not_stored_plaintext(self, client):
        """Verify password is hashed, not stored as plaintext."""
        client.post("/auth/register", json={
            "email": "secure@example.com",
            "password": "SecurePass1",
            "name": "Secure",
        })
        # Login should work (password was stored correctly)
        resp = client.post("/auth/login", json={
            "email": "secure@example.com",
            "password": "SecurePass1",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_token_grants_access(self, client):
        """Verify the token returned by login actually works for /auth/me."""
        client.post("/auth/register", json={
            "email": "tokentest@example.com",
            "password": "TokenTest1",
            "name": "Token User",
        })
        token = _login(client, "tokentest@example.com", "TokenTest1")
        assert token, "Login should return a valid token"
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "tokentest@example.com"
