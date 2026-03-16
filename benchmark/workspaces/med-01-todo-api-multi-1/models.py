"""Pydantic models for the Todo API."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TodoCreate(BaseModel):
    """Request model for creating a todo."""
    title: str
    description: str = ""

    @field_validator("title")
    @classmethod
    def title_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must be a non-empty string")
        return v.strip()


class TodoUpdate(BaseModel):
    """Request model for updating a todo (partial update)."""
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (not v or not v.strip()):
            raise ValueError("title must be a non-empty string")
        return v.strip() if v is not None else v


class TodoResponse(BaseModel):
    """Response model representing a todo item."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    description: str = ""
    completed: bool = False
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
