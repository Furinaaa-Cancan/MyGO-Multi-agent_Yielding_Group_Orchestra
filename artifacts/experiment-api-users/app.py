"""User storage layer with in-memory store and create_user function."""

_store: dict[int, dict] = {}
_next_id: int = 1


def create_user(name: str, email: str) -> dict:
    """Create a new user with auto-incrementing ID.

    Args:
        name: User's name.
        email: User's email (must contain '@').

    Returns:
        Dict with id, name, and email.

    Raises:
        ValueError: If email does not contain '@'.
    """
    global _next_id

    if "@" not in email:
        raise ValueError(f"Invalid email: {email!r} must contain '@'")

    user = {"id": _next_id, "name": name, "email": email}
    _store[_next_id] = user
    _next_id += 1
    return user


def get_user(user_id: int) -> dict | None:
    """Get a user by ID.

    Returns:
        Dict with user data if found, None otherwise.
    """
    return _store.get(user_id)


def list_users() -> list[dict]:
    """Return all users as a list of dicts."""
    return list(_store.values())


def delete_user(user_id: int) -> bool:
    """Delete a user by ID.

    Returns:
        True if the user was deleted, False if not found.
    """
    if user_id in _store:
        del _store[user_id]
        return True
    return False
