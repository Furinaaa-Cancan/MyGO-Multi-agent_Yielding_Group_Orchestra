"""FastAPI application entry point with auth endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models import RegisterRequest, LoginRequest, UserResponse, TokenResponse
from auth import hash_password, verify_password, create_access_token, decode_access_token
from database import get_user_by_email, get_user_by_id, create_user, get_all_users

app = FastAPI(title="User Authentication System")
security = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Dependency: extract and validate the current user from the Bearer token."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(request: RegisterRequest):
    """Register a new user."""
    existing = get_user_by_email(request.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = {
        "id": str(uuid.uuid4()),
        "email": request.email,
        "hashed_password": hash_password(request.password),
        "name": request.name,
        "role": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    create_user(user)

    return UserResponse(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        role=user["role"],
        created_at=user["created_at"],
    )


@app.post("/auth/login", response_model=TokenResponse)
def login(request: LoginRequest):
    """Authenticate a user and return a JWT token."""
    user = get_user_by_email(request.email)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(request.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"], user["role"])
    return TokenResponse(access_token=token)


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: dict = Depends(get_current_user)):
    """Return the currently authenticated user's info."""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        role=current_user["role"],
        created_at=current_user["created_at"],
    )


@app.get("/users", response_model=list[UserResponse])
def list_users(current_user: dict = Depends(get_current_user)):
    """Return all users. Admin only."""
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    all_users = get_all_users()
    return [
        UserResponse(
            id=u["id"],
            email=u["email"],
            name=u["name"],
            role=u["role"],
            created_at=u["created_at"],
        )
        for u in all_users
    ]
