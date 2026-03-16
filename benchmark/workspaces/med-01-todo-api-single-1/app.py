from fastapi import FastAPI, HTTPException, Query
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from models import TodoCreate, TodoUpdate, TodoResponse

app = FastAPI()

# In-memory storage
todos: dict[str, dict] = {}


@app.post("/todos", response_model=TodoResponse, status_code=201)
def create_todo(todo: TodoCreate):
    todo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    todo_data = {
        "id": todo_id,
        "title": todo.title,
        "description": todo.description,
        "completed": False,
        "created_at": now,
    }
    todos[todo_id] = todo_data
    return todo_data


@app.get("/todos", response_model=List[TodoResponse])
def list_todos(completed: Optional[bool] = Query(None)):
    result = list(todos.values())
    if completed is not None:
        result = [t for t in result if t["completed"] == completed]
    return result


@app.get("/todos/{todo_id}", response_model=TodoResponse)
def get_todo(todo_id: str):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todos[todo_id]


@app.patch("/todos/{todo_id}", response_model=TodoResponse)
def update_todo(todo_id: str, update: TodoUpdate):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo_data = todos[todo_id]
    update_dict = update.dict(exclude_unset=True)
    for key, value in update_dict.items():
        todo_data[key] = value
    return todo_data


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: str):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    del todos[todo_id]
    return None
