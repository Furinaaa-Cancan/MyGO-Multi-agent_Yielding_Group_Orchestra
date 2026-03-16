# Task: Todo REST API

Implement a Todo list REST API using FastAPI.

## Files to Create

- `app.py` — FastAPI application with the following endpoints
- `models.py` — Pydantic models
- `test_app.py` — Tests

## Data Model

```python
class Todo:
    id: str          # UUID, auto-generated
    title: str       # required, non-empty
    description: str # optional, default ""
    completed: bool  # default False
    created_at: str  # ISO 8601 timestamp, auto-generated
```

## Endpoints

1. `POST /todos` — Create a new todo
   - Body: `{"title": "...", "description": "..."}`
   - Returns: 201 + created todo object
   - Validates: title must be non-empty string

2. `GET /todos` — List all todos
   - Query params: `completed` (optional bool filter)
   - Returns: 200 + list of todos

3. `GET /todos/{id}` — Get single todo
   - Returns: 200 + todo object
   - Returns: 404 if not found

4. `PATCH /todos/{id}` — Update a todo
   - Body: partial update (any combination of title, description, completed)
   - Returns: 200 + updated todo
   - Returns: 404 if not found

5. `DELETE /todos/{id}` — Delete a todo
   - Returns: 204 (no content)
   - Returns: 404 if not found

## Requirements
- Use in-memory storage (dict)
- All responses use JSON
- Proper HTTP status codes
- Input validation with clear error messages
