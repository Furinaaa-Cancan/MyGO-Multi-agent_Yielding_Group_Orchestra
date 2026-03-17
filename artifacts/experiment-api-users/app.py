"""User management API — in-memory CRUD operations."""
from __future__ import annotations

_users: dict[int, dict] = {}
_next_id: int = 1


def create_user(name: str, email: str) -> dict:
    """Create a new user. Email must contain @."""
    global _next_id
    if "@" not in email:
        raise ValueError(f"Invalid email: {email!r} (must contain @)")
    user = {"id": _next_id, "name": name, "email": email}
    _users[_next_id] = user
    _next_id += 1
    return dict(user)


def get_user(user_id: int) -> dict | None:
    """Get user by ID. Returns None if not found."""
    user = _users.get(user_id)
    return dict(user) if user else None


def list_users() -> list[dict]:
    """Return all users."""
    return [dict(u) for u in _users.values()]


def delete_user(user_id: int) -> bool:
    """Delete user by ID. Returns True if deleted, False if not found."""
    if user_id in _users:
        del _users[user_id]
        return True
    return False
