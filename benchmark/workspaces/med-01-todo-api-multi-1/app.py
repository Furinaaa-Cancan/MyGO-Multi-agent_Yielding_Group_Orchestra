"""FastAPI Todo REST API application."""

from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response

from models import TodoCreate, TodoResponse, TodoUpdate

app = FastAPI(title="Todo API")

# In-memory storage
todos: dict[str, TodoResponse] = {}


@app.post("/todos", status_code=201, response_model=TodoResponse)
def create_todo(todo: TodoCreate) -> TodoResponse:
    """Create a new todo item."""
    new_todo = TodoResponse(
        title=todo.title,
        description=todo.description,
    )
    todos[new_todo.id] = new_todo
    return new_todo


@app.get("/todos", response_model=list[TodoResponse])
def list_todos(completed: Optional[bool] = Query(None)) -> list[TodoResponse]:
    """List all todos, optionally filtered by completed status."""
    if completed is not None:
        return [t for t in todos.values() if t.completed == completed]
    return list(todos.values())


@app.get("/todos/{todo_id}", response_model=TodoResponse)
def get_todo(todo_id: str) -> TodoResponse:
    """Get a single todo by ID."""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todos[todo_id]


@app.patch("/todos/{todo_id}", response_model=TodoResponse)
def update_todo(todo_id: str, update: TodoUpdate) -> TodoResponse:
    """Update a todo item (partial update)."""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")

    existing = todos[todo_id]
    update_data = update.model_dump(exclude_unset=True)

    updated = existing.model_copy(update=update_data)
    todos[todo_id] = updated
    return updated


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: str):
    """Delete a todo item."""
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    del todos[todo_id]
    return Response(status_code=204)
