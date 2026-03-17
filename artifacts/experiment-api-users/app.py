"""User storage layer with in-memory store and create_user function."""

_users: dict[int, dict] = {}
_next_id: int = 1


def create_user(name: str, email: str) -> dict:
    """Create a new user with auto-incrementing ID.

    Args:
        name: The user's name.
        email: The user's email (must contain '@').

    Returns:
        A dict with id, name, and email fields.

    Raises:
        ValueError: If email does not contain '@'.
    """
    global _next_id

    if "@" not in email:
        raise ValueError("email must contain @")

    user = {"id": _next_id, "name": name, "email": email}
    _users[_next_id] = user
    _next_id += 1
    return user


def get_user(user_id: int) -> dict | None:
    """Return the user dict for the given user_id, or None if not found."""
    return _users.get(user_id)


def list_users() -> list[dict]:
    """Return a list of all users."""
    return list(_users.values())


def delete_user(user_id: int) -> bool:
    """Delete user by id. Returns True if deleted, False if not found."""
    if user_id in _users:
        del _users[user_id]
        return True
    return False
