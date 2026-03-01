# API Reference

## config

### `root_dir() → Path`
Returns the project root directory (cached). Uses `MA_ROOT` env var or walks up from CWD.

### `workspace_dir() → Path`
Returns `.multi-agent/` directory path.

### `skills_dir() → Path`
Returns `skills/` directory path.

### `inbox_dir() → Path` / `outbox_dir() → Path` / `tasks_dir() → Path` / `history_dir() → Path`
Returns respective subdirectory paths under `.multi-agent/`.

### `load_yaml(path: Path) → dict`
Load a YAML file and return its contents as a dict.

### `load_project_config() → dict`
Load `.ma.yaml` project config. Returns empty dict if missing or malformed.

### `validate_config(data: dict) → list[str]`
Validate config structure. Returns list of warnings (empty = valid).

---

## schema

### `Task`
Pydantic model for a task. `extra="forbid"` — rejects unknown fields.

| Field | Type | Default |
|-------|------|---------|
| `task_id` | str | required |
| `trace_id` | str | required |
| `skill_id` | str | required |
| `state` | TaskState | DRAFT |
| `priority` | Priority | NORMAL |
| `retry_budget` | int | 2 |
| `timeout_sec` | int | 1800 |
| `done_criteria` | list[str] | [] |

### `BuilderOutput`
Builder's output. `extra="allow"` — preserves unknown fields from IDE output.

### `ReviewerOutput`
Reviewer's output. `extra="allow"`.

### `SubTask`
Decomposed sub-task. `extra="ignore"` — silently drops unknown fields.

### `DecomposeResult`
Decomposition result containing `sub_tasks: list[SubTask]`. `extra="ignore"`.

### `AgentProfile`
Agent configuration model with `id`, `driver`, `command`, `capabilities`, `reliability`, `cost`.

### `SkillContract`
Skill contract model. Use `SkillContract.from_yaml(data)` to parse from YAML dict.

---

## graph

### `plan_node(state) → dict`
Load contract, resolve builder/reviewer, render prompt, write TASK.md.

### `build_node(state) → dict`
Interrupt for builder output, validate, prepare reviewer prompt.

### `review_node(state) → dict`
Interrupt for reviewer output, record decision.

### `decide_node(state) → dict`
Route: approve → END, reject → retry or escalate.

### `build_graph() → StateGraph`
Construct the 4-node LangGraph workflow.

### `trim_conversation(conversation: list[dict]) → list[dict]`
Trim conversation to MAX_CONVERSATION_SIZE, preserving head and tail.

### `save_state_snapshot(task_id, node_name, state) → None`
Save state snapshot for debugging. Auto-cleans old snapshots (keeps 10).

### `EventHooks`
Registry for lifecycle callbacks: `on_node_enter()`, `on_node_exit()`, `on_error()`.

---

## router

### `load_agents(path=None) → list[AgentProfile]`
Load agent profiles from `agents.yaml` or `profiles.json`.

### `resolve_builder(agents, contract, explicit=None) → str`
Resolve builder agent ID. Priority: explicit → defaults → auto-pick.

### `resolve_reviewer(agents, contract, builder_id, explicit=None) → str`
Resolve reviewer agent ID (must differ from builder).

### `check_agent_health(agents) → list[dict]`
Check health of all agents. Returns `[{id, status, issues}]`.

---

## driver

### `spawn_cli_agent(agent_id, role, command_template, ...) → Thread`
Spawn a CLI agent subprocess in a background thread.

### `can_use_cli(command_template) → bool`
Check if the CLI binary exists on PATH.

### `classify_stderr(text) → str`
Classify stderr severity: `"error"`, `"warning"`, or `"info"`.

---

## workspace

### `ensure_workspace() → Path`
Create `.multi-agent/` and all subdirectories.

### `write_inbox(agent_id, content) → Path`
Write prompt to `inbox/{agent_id}.md`. Retries on OS errors.

### `read_outbox(agent_id, validate=False) → dict | None`
Read and parse `outbox/{agent_id}.json` with encoding fallback.

### `write_outbox(agent_id, data) → Path`
Write agent output to outbox. Retries on OS errors.

### `acquire_lock(task_id) → None` / `release_lock() → None` / `read_lock() → str | None`
Lock file management for active task.

### `clear_runtime() → None`
Remove all shared runtime files (inbox, outbox, TASK.md, dashboard).

### `check_workspace_health() → list[str]`
Check workspace health. Returns issues list (empty = healthy).

### `check_disk_space(min_mb=100) → tuple[bool, int]`
Check available disk space.

### `archive_conversation(task_id, conversation) → Path`
Archive conversation to `history/{task_id}.json`. Retries on OS errors.

### `retry_file_op(retries=3, delay=0.1)`
Decorator for retrying file operations on transient OS errors.

---

## decompose

### `write_decompose_prompt(requirement, lang="zh", project_context="") → Path`
Write decomposition prompt to TASK.md.

### `read_decompose_result() → DecomposeResult | None`
Read result from `outbox/decompose.json`.

### `topo_sort(sub_tasks) → list[SubTask]`
Topologically sort sub-tasks by dependencies.

### `topo_sort_grouped(sub_tasks) → list[list[SubTask]]`
Sort into parallel execution groups.

### `validate_decompose_result(result) → list[str]`
Validate for duplicates, empty descriptions, invalid deps.

### `diff_decompose_results(old, new) → list[str]`
Compare two decompose results, return diff descriptions.

### `estimate_complexity(requirement) → str`
Estimate complexity: `"simple"`, `"medium"`, or `"complex"`.

### `cache_decompose(requirement, result) → Path` / `get_cached_decompose(requirement) → DecomposeResult | None`
Cache management for decompose results.

---

## prompt

### `render_builder_prompt(requirement, contract, ...) → str`
Render builder prompt using Jinja2 templates (skill-specific with fallback).

### `render_reviewer_prompt(requirement, contract, builder_output, ...) → str`
Render reviewer prompt.

### `get_prompt_metadata() → dict`
Get prompt version and timestamp metadata.

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `ma go "req"` | Start task + auto-watch |
| `ma done` | Manually submit output |
| `ma status` | Show task state |
| `ma cancel` | Cancel active task |
| `ma watch` | Resume outbox watching |
| `ma render "req"` | Preview prompt (dry-run) |
| `ma init` | Initialize project structure |
| `ma history` | Show task history |
