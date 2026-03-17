from fastapi import APIRouter, HTTPException, status
from app.models import UserCreate, UserResponse
import uuid

router = APIRouter(prefix="/users", tags=["users"])

# In-memory store (sufficient for this task scope)
_users: dict[str, dict] = {}


# --- Service-layer functions (callable directly) ---


def get_user(user_id) -> dict | None:
    """Return user dict if found, None otherwise."""
    return _users.get(str(user_id))


def list_users() -> list[dict]:
    """Return list of all user dicts."""
    return list(_users.values())


def delete_user(user_id) -> bool:
    """Delete user by id. Returns True if deleted, False if not found."""
    key = str(user_id)
    if key not in _users:
        return False
    del _users[key]
    return True


# --- HTTP route handlers ---


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
)
def create_user(payload: UserCreate) -> UserResponse:
    for existing in _users.values():
        if existing["email"] == payload.email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )
    user_id = str(uuid.uuid4())
    user = {"id": user_id, "name": payload.name, "email": payload.email}
    _users[user_id] = user
    return UserResponse(**user)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a user by ID",
)
def get_user_endpoint(user_id: str) -> UserResponse:
    user = get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return UserResponse(**user)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="List all users",
)
def list_users_endpoint() -> list[UserResponse]:
    return [UserResponse(**u) for u in list_users()]


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a user by ID",
)
def delete_user_endpoint(user_id: str) -> dict:
    deleted = delete_user(user_id)
    return {"deleted": deleted}
