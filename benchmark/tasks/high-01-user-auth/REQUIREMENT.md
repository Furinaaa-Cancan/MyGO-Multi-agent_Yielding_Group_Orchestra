# Task: User Authentication System

Implement a complete user authentication system with registration, login,
and protected endpoints.

## Files to Create

- `app.py` — FastAPI application entry point
- `auth.py` — Authentication logic (password hashing, JWT)
- `models.py` — Pydantic models for request/response
- `database.py` — In-memory user storage
- `test_auth.py` — Test suite

## Data Model

```python
class User:
    id: str              # UUID
    email: str           # unique, validated format
    hashed_password: str # bcrypt or similar hash
    name: str
    role: str            # "user" or "admin", default "user"
    created_at: str      # ISO 8601
```

## Endpoints

### 1. `POST /auth/register`
- Body: `{"email": "...", "password": "...", "name": "..."}`
- Password requirements: min 8 chars, at least 1 digit, 1 uppercase
- Returns: 201 + user info (WITHOUT password)
- Returns: 409 if email already registered
- Returns: 422 if validation fails

### 2. `POST /auth/login`
- Body: `{"email": "...", "password": "..."}`
- Returns: 200 + `{"access_token": "...", "token_type": "bearer"}`
- Returns: 401 if credentials invalid

### 3. `GET /auth/me`
- Requires: `Authorization: Bearer <token>` header
- Returns: 200 + current user info
- Returns: 401 if no/invalid token

### 4. `GET /users` (admin only)
- Requires: Bearer token + admin role
- Returns: 200 + list of all users (without passwords)
- Returns: 403 if not admin

## Requirements
- Use bcrypt or passlib for password hashing
- Use PyJWT or python-jose for JWT tokens
- JWT should include user_id and role
- Token expiration: 30 minutes
- In-memory storage (dict)
- Proper error responses with detail messages
