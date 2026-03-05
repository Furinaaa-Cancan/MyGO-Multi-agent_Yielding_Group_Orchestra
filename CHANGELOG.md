# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
