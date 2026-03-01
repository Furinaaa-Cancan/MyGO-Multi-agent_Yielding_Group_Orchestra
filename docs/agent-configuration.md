# Agent Configuration Guide

## agents.yaml Format

The agent registry file (`agents/agents.yaml`) defines all available AI coding assistants and their configuration.

```yaml
version: 2
role_strategy: manual    # "manual" (user picks) or "auto" (system picks)

defaults:
  builder: windsurf      # Default builder IDE
  reviewer: cursor       # Default reviewer IDE

agents:
  - id: windsurf
    driver: file
    capabilities: [planning, implementation, testing, review, docs]
    reliability: 0.95
    queue_health: 0.9
    cost: 0.5

  - id: cursor
    driver: file
    capabilities: [planning, implementation, testing, review, docs]

  - id: claude
    driver: cli
    command: "claude -p 'Read {task_file} and complete the task. Save result to {outbox_file}' --allowedTools Read,Edit,Bash,Write"
    capabilities: [planning, implementation, testing, review, docs]
```

## Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | required | Unique agent identifier |
| `driver` | string | `"file"` | `"file"` (IDE) or `"cli"` (auto-spawn) |
| `command` | string | `""` | CLI command template (for `driver: cli`) |
| `capabilities` | list | `[]` | Agent capabilities |
| `reliability` | float | `0.9` | Historical success rate (0-1) |
| `queue_health` | float | `0.9` | Current availability (0-1) |
| `cost` | float | `0.5` | Relative cost (0-1) |

## Driver Modes

### File Driver (IDE agents)

For IDE-based agents (Windsurf, Cursor, Kiro, etc.):

```yaml
- id: windsurf
  driver: file
  capabilities: [implementation, review]
```

The orchestrator writes prompts to `TASK.md`. The user tells the IDE AI to read it. The agent writes results to `outbox/`.

### CLI Driver (automated agents)

For CLI-based agents (Claude Code, Codex, Aider):

```yaml
- id: claude
  driver: cli
  command: "claude -p 'Read {task_file} ...' --allowedTools Read,Edit,Bash,Write"
  capabilities: [implementation, review]
```

The orchestrator spawns the CLI process automatically.

## Command Template Placeholders

| Placeholder | Replaced With |
|-------------|---------------|
| `{task_file}` | Absolute path to `TASK.md` |
| `{outbox_file}` | Absolute path to expected outbox JSON |
| `{inbox_file}` | Absolute path to inbox prompt |
| `{workspace}` | Absolute path to `.multi-agent/` directory |

## IDE-Specific Examples

### Windsurf

```yaml
- id: windsurf
  driver: file
  capabilities: [planning, implementation, testing, docs]
```

Usage: Tell Windsurf AI — `"帮我完成 @.multi-agent/TASK.md 里的任务"`

### Cursor

```yaml
- id: cursor
  driver: file
  capabilities: [planning, implementation, testing, review, docs]
```

Usage: Tell Cursor AI — `"帮我完成 @.multi-agent/TASK.md 里的任务"`

### Claude Code (CLI)

```yaml
- id: claude
  driver: cli
  command: "claude -p 'Read {task_file} and complete the task. Save JSON result to {outbox_file}' --allowedTools Read,Edit,Bash,Write"
  capabilities: [planning, implementation, testing, review, docs]
```

### Codex (CLI)

```yaml
- id: codex
  driver: cli
  command: "codex exec 'Read {task_file} and complete the task. Save JSON result to {outbox_file}' --full-auto"
  capabilities: [planning, implementation, testing, review, docs]
```

## Troubleshooting

### Agent not found

```
Error: No agent configured for builder role
```

Check that your `agents.yaml` has at least one agent with the required capabilities, or specify the agent explicitly with `--builder` / `--reviewer`.

### CLI binary not found

```
Warning: Binary 'claude' not found on PATH, degrading to file mode
```

Ensure the CLI tool is installed and available on your `PATH`.

### Same builder and reviewer

```
Error: Reviewer cannot be the same as builder
```

Cross-model adversarial review requires different agents for builder and reviewer roles. Configure at least 2 agents.

### Health check

Run `ma status` to see agent health and active task information.
