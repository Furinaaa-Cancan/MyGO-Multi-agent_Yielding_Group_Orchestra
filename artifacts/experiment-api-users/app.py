"""User management module with in-memory storage."""

_users: dict[int, dict] = {}
_next_id: int = 1


def create_user(name: str, email: str) -> dict:
    """Create a new user. Raises ValueError if email lacks '@'."""
    global _next_id
    if "@" not in email:
        raise ValueError("email must contain @")
    user = {"id": _next_id, "name": name, "email": email}
    _users[_next_id] = user
    _next_id += 1
    return dict(user)


def get_user(user_id: int) -> dict | None:
    """Return user by id, or None if not found."""
    user = _users.get(user_id)
    return dict(user) if user is not None else None


def list_users() -> list[dict]:
    """Return all users."""
    return [dict(u) for u in _users.values()]


def delete_user(user_id: int) -> bool:
    """Delete user by id. Returns True if deleted, False if not found."""
    if user_id in _users:
        del _users[user_id]
        return True
    return False
