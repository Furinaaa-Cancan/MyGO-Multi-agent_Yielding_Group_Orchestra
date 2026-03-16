"""FastAPI application entry point."""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models import RegisterRequest, LoginRequest, UserResponse, TokenResponse
from auth import hash_password, verify_password, create_access_token, decode_access_token
from database import get_user_by_email, get_user_by_id, create_user, get_all_users

app = FastAPI(title="User Authentication System")
security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user = get_user_by_id(payload["sub"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


@app.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest):
    existing = get_user_by_email(request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

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
    user = get_user_by_email(request.email)
    if user is None or not verify_password(request.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(user["id"], user["role"])
    return TokenResponse(access_token=access_token)


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        role=current_user["role"],
        created_at=current_user["created_at"],
    )


@app.get("/users", response_model=list[UserResponse])
def list_users(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    users = get_all_users()
    return [
        UserResponse(
            id=u["id"],
            email=u["email"],
            name=u["name"],
            role=u["role"],
            created_at=u["created_at"],
        )
        for u in users
    ]
