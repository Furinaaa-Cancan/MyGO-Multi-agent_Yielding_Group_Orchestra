# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] - 2026-03-08

### Added
- **MCP Server** (`mcp_server.py`) — expose MyGO as MCP service for AI tool integration
  - 6 tools: task_status, task_list, task_detail, dashboard, task_cancel, project_info
  - 2 resources: mygo://dashboard, mygo://status
  - 3 rounds of security audit: path traversal prevention, file size caps, input sanitization
- **Notification system** (`notify.py`) — macOS native notifications + webhook on task completion
  - Triggers on approved/failed/escalated/cancelled via EventHooks
  - Configurable via `.ma.yaml` notify: section
- **Multi-project support** — Dashboard project switcher with backend registry
  - /api/projects, /api/projects/add, /api/projects/switch endpoints
  - Frontend project selector dropdown in header
- **Task statistics** (`my stats`) — execution statistics report with node timing analysis
- **Task template system** (`task_templates.py`) — predefined task configs for one-command launch
  - 6 built-in templates: auth, crud, bugfix, refactor, test, api-endpoint
  - `my go --template auth` / `my go --template crud --var model=User`
  - `my template list` / `my template show <id>` CLI commands
  - `${var}` placeholder substitution with `--var key=value` overrides
  - Security: path traversal prevention, file size cap, skill validation
- **Intelligent stderr analysis** — detect known Codex CLI error patterns (UTF-8, WebSocket, rate limit)
- **Enhanced JSON extraction** — 3-strategy cascade for CLI agent output parsing
  - Fenced code blocks, bare JSON objects, pure JSON, best-candidate selection

### Changed
- Claude CLI command template includes explicit JSON schema example
- Builder prompt template strengthens changed_files requirement
- Reviewer prompt softens empty changeset handling for CLI agents

### Fixed
- MCP Server: 12 security/functionality issues across 3 audit rounds
- Codex CLI compatibility: reviewer no longer auto-rejects missing changed_files

## [0.8.1] - 2026-03-07

### Added
- **Node.js/Express backend** (`web/app.js`) — primary dashboard server with SSE via chokidar file watching
- **Chinese/English i18n** — full language toggle (50+ translation keys) with localStorage persistence
- **Premium UI redesign** — warm gold/zinc theme, Hero section, workflow pipeline visualization, status banner, multi-column footer
- **Markdown table rendering** — dashboard.md tables now render as styled HTML tables
- **System architecture doc** (`docs/SYSTEM_ARCHITECTURE.md`) — complete pipeline learning guide
- **Skills cheatsheet** (`docs/SKILLS_CHEATSHEET.md`) — 8 categories of reusable development patterns
- **Common errors guide** (`docs/COMMON_ERRORS.md`) — 18 documented errors with solutions + 4 design decisions
- **Audit reports** (`docs/REVIEW_REPORT_v0.8.1*.md`) — 7-round code review findings

### Fixed
- **C1**: SSE trace event detection used magic number approximation → byte-offset seek (no duplicates/misses)
- **H1**: Git hooks re-read YAML config on every call → closure factory caches config at registration
- **M1**: `decide_node` dashboard showed trimmed conversation → uses original pre-trim conversation
- **M2**: Node.js server had no graceful shutdown → SIGTERM/SIGINT handlers added
- **M4**: Python `server.py` called `stat()` twice per task file → cached mtime
- **L1**: `_is_cancelled` function-level `import yaml` → moved to module top
- **L2**: i18n `data-i18n` attribute conflicted with dynamic event counter → removed, refresh in JS
- **Trace matching**: Both `app.js` and `server.py` now match `.events.jsonl` file naming pattern

### Changed
- CLI `my dashboard` prefers Node.js backend, falls back to Python/uvicorn if Node.js unavailable
- Git hooks use `_make_on_build_submit(cfg)` / `_make_on_decide_approve(cfg)` factory pattern
- `_decide_reject_retry` accepts `original_convo` parameter for dashboard consistency
- `pyproject.toml` version synced to 0.8.1

## [0.8.0] - 2026-03-07

### Added
- **Git integration** (`git_ops.py`) — auto-commit, auto-branch, auto-tag triggered by EventHooks
  - `auto_commit()` — stage and commit changes after build/approve steps
  - `create_branch()` — create task-specific feature branches at plan start
  - `create_tag()` — annotated tags on task approval
  - `register_git_hooks()` / `register_git_hooks_override()` — hook lifecycle management
- **Auto-test runner** — run project tests automatically, inject results as reviewer evidence
  - `run_tests()` returns `AutoTestResult` with parsed pass/fail counts and evidence formatting
  - Configurable via `.ma.yaml` `auto_test:` block (command, inject_evidence, fail_action)
- **`--git-commit` CLI flag** — one-shot auto-commit without `.ma.yaml` configuration
- `.ma.yaml` now supports `git:` and `auto_test:` configuration sections
- 41 new tests in `test_git_ops.py` covering config, primitives, operations, hooks, and registration

### Changed
- `VALID_CONFIG_KEYS` extended with `git`, `auto_test`, `agent_names`
- `my go` command registers git hooks at startup (no-op if git section absent)

## [0.7.1] - 2026-03-07

### Changed
- `scripts/smoke_mvp.sh` now runs session-mode smoke in an isolated `MA_ROOT` workspace, preventing interference from existing active tasks and stale locks.
- Session smoke flow is simplified to LangGraph SSOT validation (legacy transition replay removed from smoke path).

## [0.7.0] - 2025-06-17

### Added
- **Parallel sub-task execution** — independent sub-tasks in `--decompose` mode now run concurrently via `ThreadPoolExecutor`
- **`--visible` flag** — open each CLI agent in a separate Terminal.app window for real-time visibility (macOS)
- **MyGO!!!!! persona names** — terminal windows named after band members (燈/愛音/そよ/楽奈/立希花), customizable via `.ma.yaml` `agent_names`
- `spawn_cli_in_terminal()` — AppleScript-based visible terminal spawning with wrapper script
- `subtask_workspace()`, `subtask_task_file()`, `subtask_outbox_dir()` utilities in `config.py`
- `get_agent_name()`, `set_agent_names()`, `load_agent_names_from_config()` persona API
- `OutboxPoller` accepts custom `watch_dir` for subtask-specific polling

### Changed
- `_run_decomposed()` uses `topo_sort_grouped()` to execute groups: single-task groups run sequentially, multi-task groups run in parallel
- `spawn_cli_agent()` and `dispatch_agent()` accept optional `subtask_id` and `visible` parameters
- `_show_waiting()`, `_run_watch_loop()`, `_process_outbox()` pass `subtask_id` and `visible` through
- Same agent allowed for builder and reviewer when using CLI/GUI drivers (relaxed restriction in `cli.py` and `router.py`)

### Fixed
- `test_lock_released_on_unexpected_error` — patch workspace module's local `workspace_dir` reference

## [0.6.1] - 2025-06-16

### Added
- **GUI driver** (`driver: gui`) — macOS AppleScript automation for desktop IDE apps (Codex)
- `send_gui_message()`, `spawn_gui_agent()`, `can_use_gui()` in driver.py
- `app_name` field in `AgentProfile` schema for GUI driver config
- `format_duration()` utility function in `_utils.py`
- `scripts/gui_send.sh` standalone AppleScript wrapper

### Changed
- `dispatch_agent()` now handles `gui` driver type with graceful degradation
- `agents.yaml` Codex entry updated from `cli` to `gui` driver
- README updated with GUI driver documentation and 3-driver comparison table

## [0.6.0] - 2025-06-15

### Added
- Task decomposition (`--decompose`) with topological sorting and parallel groups
- Decompose result caching and diff detection
- Project context collection for decompose prompts
- Complexity estimation heuristic
- Skill-specific Jinja2 prompt templates (test-builder, test-reviewer, decompose-builder)
- Prompt length control with truncation and warnings
- Prompt version tracking metadata
- Retry context enhancement in builder prompts
- Example JSON output files in `docs/examples/`
- `my render` command for prompt dry-run preview
- `my init` command for project initialization
- `my history` command for task history
- Unified CLI error handling decorator (`@handle_errors`)
- SIGTERM signal handler for graceful shutdown
- File operation retry decorator (`@retry_file_op`)
- Graph node exception fallback (plan, build, review, decide)
- State snapshot saving for debugging
- Conversation size limit with trimming
- Config validation (`validate_config`)
- Root dir diagnostic improvements
- Pydantic model strict modes (forbid/allow/ignore)
- Outbox file size limit in watcher
- Outbox encoding detection fallback (UTF-8 → BOM → latin-1)
- Workspace health check (`check_workspace_health`)
- Agent health check (`check_agent_health`)
- stderr severity classification for CLI drivers
- 470 tests covering all modules

### Changed
- README updated with decompose flow diagram and file interaction diagram
- CLI reference tables updated with new commands

## [0.5.0] - 2025-05-01

### Added
- Event hooks system for graph node lifecycle
- Quality gate validation in build node
- Cancellation detection via `.cancel` marker
- Dashboard generation with conversation timeline
- Outbox validation for builder and reviewer
- Watcher `stop_after` parameter
- Disk space checking
- Conversation archiving to history

### Changed
- Graph nodes use `interrupt()` from LangGraph for human-in-the-loop

## [0.4.0] - 2025-04-01

### Added
- CLI agent driver with subprocess spawning
- Concurrency lock for CLI agents
- `can_use_cli` binary detection
- Stderr streaming with real-time logging
- Agent profile model with reliability and cost scoring
- Router role assignment (manual and auto strategies)

### Changed
- Workspace structure standardized with inbox/outbox/tasks/history

## [0.3.0] - 2025-03-01

### Added
- Skill contract system with YAML-based definitions
- Precondition and postcondition validation
- Retry policy with backoff strategies
- Timeout configuration per skill
- `SkillContract.from_yaml()` factory method
- Contract loading from skills directory

## [0.2.0] - 2025-02-01

### Added
- Pydantic schema models: Task, BuilderOutput, ReviewerOutput
- Task state machine (DRAFT → QUEUED → RUNNING → APPROVED/FAILED)
- Priority enum (LOW, NORMAL, HIGH)
- Task ID and trace ID validation
- agents.yaml v2 format with role_strategy
- `load_registry` with JSON fallback

## [0.1.0] - 2025-01-15

### Added
- Initial project structure
- LangGraph 4-node graph: plan → build → review → decide
- File-based workspace communication
- Basic CLI: `my go`, `my done`, `my status`, `my cancel`, `my watch`
- TASK.md self-contained prompt pattern
- Outbox auto-detection via polling watcher
