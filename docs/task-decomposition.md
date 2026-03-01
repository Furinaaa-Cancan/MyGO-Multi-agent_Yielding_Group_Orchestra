# Task Decomposition Guide

## Overview

Task decomposition breaks complex requirements into smaller, independent sub-tasks. Each sub-task goes through its own build-review cycle.

## When to Use

Use `--decompose` when:
- The requirement involves multiple independent features
- Estimated implementation time exceeds 30 minutes
- The requirement touches multiple modules or files
- You want isolated build-review cycles per feature

Skip decomposition when:
- The requirement is a single, focused change
- It's a bug fix or small refactor
- The scope is already narrow

## Usage

```bash
ma go "Build a user management system with login, registration, and profile editing" --decompose
```

## How It Works

1. **Decompose phase**: The orchestrator sends a decomposition prompt to an IDE/CLI agent
2. **Agent output**: The agent analyzes the requirement and produces a JSON with sub-tasks
3. **Validation**: Sub-tasks are validated for structure, dependencies, and duplicates
4. **Topological sort**: Sub-tasks are ordered by dependencies into parallel groups
5. **Execution**: Each sub-task runs through the standard plan→build→review→decide cycle

## Output Format

The decompose agent must output JSON to `.multi-agent/outbox/decompose.json`:

```json
{
  "sub_tasks": [
    {
      "id": "auth-login",
      "description": "Implement user login with email/password",
      "done_criteria": [
        "POST /login endpoint works",
        "Returns JWT token on success",
        "Returns 401 on invalid credentials"
      ],
      "deps": [],
      "skill_id": "code-implement"
    },
    {
      "id": "auth-register",
      "description": "Implement user registration",
      "done_criteria": [
        "POST /register endpoint works",
        "Validates email format",
        "Hashes password before storage"
      ],
      "deps": [],
      "skill_id": "code-implement"
    },
    {
      "id": "user-profile",
      "description": "Implement profile viewing and editing",
      "done_criteria": [
        "GET /profile returns user data",
        "PUT /profile updates user data",
        "Requires authentication"
      ],
      "deps": ["auth-login"],
      "skill_id": "code-implement"
    }
  ],
  "reasoning": "Login and registration are independent. Profile depends on login for auth."
}
```

## Dependencies

Sub-tasks can declare dependencies via the `deps` field:

```json
{
  "id": "user-profile",
  "deps": ["auth-login"]
}
```

This means `user-profile` won't start until `auth-login` is approved.

### Parallel Groups

Sub-tasks with no inter-dependencies run in parallel groups:

```
Group 1: [auth-login, auth-register]     ← no deps, run together
Group 2: [user-profile]                   ← depends on auth-login
```

### Circular Dependencies

Circular dependencies are detected and rejected:

```
Error: Circular dependency detected involving {'a', 'b'}
```

## Caching

Decomposition results are cached based on the requirement text hash:

- Same requirement → reuses cached result (instant)
- Modified requirement → fresh decomposition
- Use `diff_decompose_results()` to compare old vs new

## Sub-Task Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique sub-task ID (lowercase, hyphens) |
| `description` | string | Yes | What to implement |
| `done_criteria` | list | No | Completion standards |
| `deps` | list | No | IDs of sub-tasks this depends on |
| `skill_id` | string | No | Skill to use (default: `code-implement`) |
| `priority` | string | No | `low`, `normal`, `high` |
| `estimated_minutes` | int | No | Time estimate (default: 30) |

## Failure Handling

- If a sub-task fails and exhausts its retry budget → it is marked `escalated`
- Other sub-tasks that don't depend on the failed one continue normally
- Sub-tasks that depend on a failed task are skipped
- The overall task reports partial completion with details on which sub-tasks succeeded/failed
