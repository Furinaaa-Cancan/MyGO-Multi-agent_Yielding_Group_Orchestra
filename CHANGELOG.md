# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.15.0] - 2026-03-09

### Added
- **Config Profiles** — named presets for common task configurations
  - New `profiles.py` module: load/validate/list profiles from `.ma.yaml`
  - `my go --profile fast` — apply profile defaults (builder, reviewer, timeout, etc.)
  - `my profiles` — list all available profiles
  - Profile fields: retry_budget, timeout, builder, reviewer, skill, mode, decompose, visible
  - Unknown fields filtered out; CLI flags always override profile values
  - `profiles` added to `VALID_CONFIG_KEYS`
- **Memory Auto-Prune** — TTL expiry and entry cap for semantic memory
  - `prune()` function: remove entries older than N days (default 180) + cap total entries
  - `my memory prune [days]` — CLI action for manual pruning
  - Two-phase: TTL expiry first, then cap most-recent
- **12 new tests** — config profiles (7) + memory auto-prune (5)

## [0.14.0] - 2026-03-09

### Added
- **Batch Mode** — run multiple tasks from a YAML manifest
  - New `batch.py` module: manifest loading, validation, summary formatting
  - `my batch tasks.yaml` — sequential task execution from manifest
  - `my batch tasks.yaml --dry-run` — validate without executing
  - `--builder` / `--reviewer` overrides for all tasks
  - Supports `requirement` and `template` per task, max 50 tasks per batch
  - Safety: file size cap (256KB), requirement length cap (2000 chars)
- **Memory Export/Import** — share team knowledge across projects
  - `my memory export [file.json]` — export all entries with metadata
  - `my memory import file.json` — import with dedup by content hash
  - Versioned export format (`version: 1`) for forward compatibility
  - Import validation: file size cap (10MB), content length cap, category normalization
- **15 new tests** — batch mode (9) + memory export/import (6)

## [0.13.0] - 2026-03-09

### Added
- **OpenAI Embeddings Backend** — optional upgrade path for semantic memory search
  - `semantic_memory.py`: dual-backend search — TF-IDF (default) or OpenAI embeddings
  - Config via `.ma.yaml`: `memory.backend: openai`, `memory.openai_model: text-embedding-3-small`
  - Requires `OPENAI_API_KEY` env var; auto-falls back to TF-IDF on failure
  - Embeddings cache at `.multi-agent/memory/embeddings_cache.json` — avoids re-embedding unchanged entries
  - Dense cosine similarity via `_cosine_sim_vectors()` for OpenAI vectors
  - `_get_backend()` auto-detects: needs both config + API key to activate
- `config.py`: added `memory` and `notify` to `VALID_CONFIG_KEYS`
- **10 new tests** — backend selection, cache roundtrip, cosine similarity, fallback, mocked search

## [0.12.0] - 2026-03-09

### Added
- **Webhook Notification Enhancement** — Slack/Discord native formatting + retry logic
  - `notify.py`: auto-detect webhook format from URL (Slack/Discord/Generic)
  - `_format_slack_payload()`: Slack attachment with color-coded status, fields, fallback text
  - `_format_discord_payload()`: Discord embed with color, fields, description
  - Exponential backoff retry (1s→2s→4s→8s) for failed webhook deliveries
  - Config: `webhook_format: auto|slack|discord|generic`, `webhook_retries: 0-5`
  - `notify_decompose_complete()`: digest notification for decompose task completion
  - Wired into `cli_decompose.py` `_finalize_decompose()`
- **Enhanced `my doctor` Command** — 5-check comprehensive system validation
  - [1/5] Workspace health (files, sizes, orphan locks)
  - [2/5] Config validation (.ma.yaml keys + skill contract parsing)
  - [3/5] Agent availability (CLI binary existence via `shutil.which`)
  - [4/5] Semantic memory integrity (entry count, empty file detection)
  - [5/5] Webhook connectivity (URL scheme validation, format/retry config)
  - Summary with pass/fail count and actionable issue list
- **12 new tests** — webhook formatters (9) + doctor command (3)

## [0.11.0] - 2026-03-09

### Added
- **Smart Retry with Memory** — retry prompts now inject relevant semantic memory context
  - `graph.py` `plan_node`: on `retry_count > 0`, queries `semantic_memory.get_context()` with requirement + feedback
  - Injects up to 1500 chars of past decisions, conventions, and bugfix patterns into builder prompt
  - Wrapped in `contextlib.suppress(Exception)` — never blocks the retry pipeline
- **MCP Server Write Tools** — full bidirectional control from any MCP client
  - `submit_review(decision, feedback, summary)` — approve/reject/request_changes via outbox
  - `memory_search(query, top_k)` — search semantic memory from IDE
  - `memory_store(content, category, tags)` — store knowledge entries
  - `memory_list(category, limit)` — list entries with stats
  - `finops_summary()` — aggregated token usage and cost
  - All tools have input validation and length caps
- **12 new tests** — smart retry memory injection + MCP write tools (9 skip when fastmcp unavailable)

## [0.10.0] - 2026-03-09

### Added
- **Semantic Memory** — cross-task knowledge persistence with TF-IDF retrieval
  - New module `semantic_memory.py`: store/search/delete/clear/stats operations
  - TF-IDF cosine similarity engine (zero external dependencies, <50ms for 5000 entries)
  - Categories: architecture, convention, pattern, bugfix, preference, context
  - Auto-capture from review summaries in `graph.py` (extracts decisions/patterns)
  - `cli_admin.py`: `my memory search|add|list|stats|delete|clear` CLI commands
  - `app.js`: `/api/memory` + `/api/memory/search` endpoints
  - `index.html`: Memory panel with search input and category stats
  - `get_context()` helper for injecting relevant memories into LLM prompts
  - Content-hash deduplication, fcntl file locking, 5000 entry cap
- **Dashboard Bidirectional Control** — approve/reject/cancel from web UI
  - `app.js`: `POST /api/actions/cancel` — cancels task, releases lock, clears runtime
  - `app.js`: `POST /api/actions/review` — writes reviewer.json to outbox (watcher picks up)
  - `index.html`: Action bar with Approve/Reject/Cancel buttons (auto-shows when task active)
  - Reject requires feedback text; approve optional; cancel clears all runtime files
- **Python FastAPI server alignment** — full parity with Node.js backend
  - `server.py`: Auth middleware with `hmac.compare_digest` timing-safe comparison
  - `server.py`: `/api/auth/check`, `/api/auth/login` endpoints
  - `server.py`: `/api/finops` aggregated token usage endpoint
  - `server.py`: `/api/memory`, `/api/memory/search` endpoints
  - `server.py`: `/api/actions/cancel`, `/api/actions/review` endpoints
  - `server.py`: CORS middleware with Authorization header support
- **40 new tests** — semantic memory (store/search/delete/TF-IDF/auto-capture), dashboard actions, server parity, CLI memory commands
- **Documentation** — updated `COMMON_ERRORS.md` (7 new entries E19-E25), `SKILLS_CHEATSHEET.md` (4 new sections)

### Fixed
- `cli_admin.py`: memory command used wrong decorator (`admin_group` → `main`)

## [0.9.2] - 2026-03-09

### Added
- **Web Dashboard Authentication** — optional token-based auth for API/SSE
  - `app.js`: Bearer token middleware on all `/api/*` routes (env `MYGO_AUTH_TOKEN` or `--token` CLI arg)
  - `app.js`: `/api/auth/check` + `/api/auth/login` endpoints for frontend handshake
  - `app.js`: SSE accepts token via query param (`?token=xxx`) since EventSource can't set headers
  - `index.html`: Login overlay UI with token input, `localStorage` persistence, `authFetch()` wrapper
  - `cli.py`: `my dashboard --token auto` generates random token; `--token <value>` for explicit token
  - Token resolution chain: CLI flag → `MYGO_AUTH_TOKEN` env var → `.ma.yaml` `dashboard.token` → disabled
  - CORS `Access-Control-Allow-Headers` updated to include `Authorization`
- **Cross-platform `--visible` terminal support** — Windows & Linux
  - `driver.py`: `_detect_terminal_emulator()` — platform-aware detection
  - macOS: Terminal.app via AppleScript (existing, refactored)
  - Windows: Windows Terminal (`wt.exe`) or `cmd.exe` with `.bat` wrapper calling Git Bash/WSL
  - Linux: gnome-terminal, konsole, xfce4-terminal, xterm (first available)
  - Graceful fallback to headless CLI if no terminal emulator found
- **FinOps — Token usage tracking & cost reporting**
  - New module `finops.py`: persistent JSONL logging, cost estimation, aggregation, budget alerts
  - `graph.py`: build + review nodes persist token usage to `logs/token-usage.jsonl`
  - `cli_admin.py`: `my finops` command — human-readable report or `--json` output
  - `app.js`: `/api/finops` endpoint aggregating token usage for Dashboard
  - `index.html`: FinOps cost panel (total tokens, cost, per-node breakdown)
  - Default pricing for GPT-4o/4.1/o3/o4-mini, Claude Sonnet/Haiku, Codex
  - `.ma.yaml` `finops.budget_usd` / `finops.budget_tokens` budget alert config
  - `config.py`: `dashboard` and `finops` added to `VALID_CONFIG_KEYS`
- **24 new tests** — FinOps module, cross-platform terminal detection, dashboard auth CLI option

## [0.9.1] - 2026-03-09

### Security
- **Enterprise-grade security hardening** — full-codebase audit (31 files, 22 fixes)
  - `app.js`: `yaml.load()` → `JSON_SCHEMA` (prevents `!!js/function` code execution)
  - `app.js`: CORS middleware restricts origins to localhost only (CSRF prevention)
  - `app.js`: SSE connection limit (max 10), YAML file size cap (5MB), JSON body limit (1MB)
  - `app.js`: task_id regex tightened to match Python side, absolute path leakage removed
  - `index.html`: XSS prevention — HTML-escape before `innerHTML` rendering
  - `git_ops.py`: `--` separator in `git add`/`git checkout -b` (flag injection prevention)
  - `git_ops.py`: `branch_prefix` regex validation with safe fallback
  - `session.py`: 10MB file size cap on `session_push` reads
  - `workspace.py`: symlink protection in `_find_oversized_files` and `cleanup_old_files`
  - `cli_decompose.py`: 5MB cap on decompose file reads
  - `decompose.py`: 5MB cap on decompose outbox reads
  - `meta_graph.py`: 10MB checkpoint size cap + atomic write via `tempfile`+`os.replace`
  - `config.py`: `subtask_id` path traversal validation on all subtask workspace functions
  - `graph_infra.py`: `task_id` path sanitization in `log_timing`
- **23 new security regression tests** covering all fixes
- **Full audit report**: `docs/SECURITY_AUDIT.md`

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
