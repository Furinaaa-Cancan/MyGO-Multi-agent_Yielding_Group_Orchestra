# Security & Performance Audit — 2026-03-05

## Summary

| Category | Status | Details |
|----------|--------|---------|
| Path traversal | **Fixed** | task_id, agent_id, skill_id all validated |
| Command injection | **Mitigated** | shell=True with defense-in-depth |
| SQL injection | **N/A** | No raw SQL; LangGraph uses parameterized queries |
| Concurrency | **OK** | Lock-based, connection-pooled, thread-safe |
| Resource leaks | **Low risk** | SQLite connections atexit-cleaned |
| State machine | **Known gap** | Terminal states not enforced at runtime |

## Findings & Actions

### 1. Path Traversal — agent_id (FIXED)

**Risk**: HIGH → **Resolved**

`agent_id` was used unsanitized in file paths (`inbox/{agent_id}.md`,
`outbox/{agent_id}.json`). A crafted `--builder ../../../etc` flag
could write files outside the workspace.

**Fix**: Added `validate_agent_id()` in `_utils.py` with regex
`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`. Applied to all 6 workspace
functions: `write_inbox`, `read_outbox`, `write_outbox`, `clear_outbox`,
`clear_inbox`, plus defense-in-depth in `trace.py` and `memory.py`.

### 2. Path Traversal — task_id (Already Protected)

**Risk**: LOW (was already mitigated)

`task_id` validated at CLI entry (`cli.py:_validate_task_id`), session
entry (`session.py:_validate_task_id`), and now also in `trace.py` and
`memory.py` (defense-in-depth). Graph snapshot saving uses regex
sanitization (`graph.py:save_state_snapshot`).

### 3. Command Injection — shell=True (Mitigated)

**Risk**: MEDIUM → **Accepted with mitigations**

`driver.py:spawn_cli_agent` uses `shell=True` for CLI agent execution.

Mitigations in place:
- Command template comes from `agents.yaml` (trusted config, not user input)
- Shell metacharacter detection warns on dangerous chars (`;|&$\`<>`)
- Path arguments shell-quoted via `shlex.quote()`
- Security note in docstring recommends `agents.yaml` file permission `0o644`

**Recommendation**: Consider switching to `shell=False` with `shlex.split()`
in a future release for additional hardening.

### 4. SQLite Resource Leaks (Low Risk)

**Risk**: LOW

Test warnings show `ResourceWarning: unclosed database`. This is because
`compile_graph()` creates pooled connections that are cleaned up via
`atexit.register(conn.close)` but not during test teardown.

`reset_graph()` properly closes all pooled connections and is called
in test fixtures. The warnings come from tests that don't call
`reset_graph()` in their teardown.

**No production impact** — connections are properly closed on process exit.

### 5. State Machine Terminal State Gap (Known)

**Risk**: LOW

`validate_transition("DONE", "RUNNING")` returns `True` because `DONE`
is not listed in the `transitions` dict (only in `terminal_states`).
The code treats undefined source states as "allow anything" for graceful
degradation.

**Impact**: Minimal — the graph nodes enforce terminal states independently.
The state machine validator is advisory, not authoritative.

**Recommendation**: Consider adding explicit empty transition entries for
terminal states in `specs/state-machine.yaml` to enable strict enforcement.

### 6. Atomic File Writes (OK)

All JSON writes to outbox/inbox use atomic temp-file + `Path.replace()`
pattern with cleanup on failure. This prevents TOCTOU race conditions
with the file watcher.

### 7. Concurrency Safety (OK)

- Lock file mechanism prevents concurrent task execution
- `_cli_lock` mutex prevents duplicate CLI agent spawning
- `_conn_lock` protects SQLite connection pool
- Connection pool uses `check_same_thread=False` for multi-threaded access

### 8. Input Validation Coverage

| Input | Validated | Pattern |
|-------|-----------|---------|
| task_id | ✅ | `[a-z0-9][a-z0-9-]{2,63}` |
| agent_id | ✅ | `[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}` |
| skill_id | ✅ | `[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}` |
| file_path (CLI) | ✅ | `click.Path(exists=True)` |
| JSON payloads | ✅ | Pydantic + schema validation |
| command_template | ⚠️ | Metachar warning, not blocked |

## Test Coverage

22 dedicated security tests in `tests/test_security.py` covering:
- task_id validation (traversal, special chars, boundary lengths)
- agent_id validation (traversal, shell metachars, boundaries)
- Workspace function rejection of malicious agent_ids
