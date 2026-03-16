from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime, timezone
import uuid


class TodoCreate(BaseModel):
    title: str
    description: str = ""

    @validator("title")
    def title_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("title must be a non-empty string")
        return v


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None

    @validator("title")
    def title_must_not_be_empty(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValueError("title must be a non-empty string")
        return v


class TodoResponse(BaseModel):
    id: str
    title: str
    description: str
    completed: bool
    created_at: str
