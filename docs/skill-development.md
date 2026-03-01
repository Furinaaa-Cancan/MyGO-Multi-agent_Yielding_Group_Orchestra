# Skill Development Guide

## What is a Skill?

A skill defines a repeatable task type that AgentOrchestra can execute. Each skill has a `contract.yaml` that specifies inputs, outputs, quality gates, and retry policies.

## Creating a New Skill

### 1. Create the Directory

```bash
mkdir -p skills/my-new-skill
```

### 2. Write contract.yaml

```yaml
id: my-new-skill
version: "1.0.0"
description: "Description of what this skill does"

triggers:
  - manual
  - decompose

inputs:
  - name: requirement
    schema: string
    required: true

outputs:
  - name: result
    schema: object
    required: true

preconditions:
  - "RUNNING"          # Task must be in RUNNING state

postconditions:
  - "files_changed"    # At least one file was modified

quality_gates:
  - lint               # Run linter
  - unit_test          # Run unit tests

timeouts:
  run_sec: 1800        # 30 minutes for builder
  verify_sec: 600      # 10 minutes for reviewer

retry:
  max_attempts: 2      # Maximum retry count
  backoff: linear      # none | linear | exponential | fixed

fallback:
  on_failure: retry    # What to do on failure

compatibility:
  supported_agents:    # Leave empty for all agents
    - windsurf
    - cursor
    - claude
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique skill identifier (lowercase, hyphens) |
| `version` | string | Yes | Semantic version |
| `description` | string | No | Human-readable description |
| `triggers` | list | No | How this skill can be triggered |
| `inputs` | list | No | Expected input parameters |
| `outputs` | list | No | Expected output fields |
| `preconditions` | list | No | Conditions that must be true before execution |
| `postconditions` | list | No | Conditions that must be true after execution |
| `quality_gates` | list | No | Checks to run (lint, unit_test, etc.) |
| `timeouts` | object | No | Time limits for build and review phases |
| `retry` | object | No | Retry policy configuration |
| `fallback` | object | No | Failure handling strategy |
| `compatibility.supported_agents` | list | No | Restrict to specific agents |

### 3. Create Custom Templates (Optional)

To customize prompts for your skill, create Jinja2 templates:

```
src/multi_agent/templates/
├── {skill-id}-builder.md.j2    # Builder prompt for this skill
└── {skill-id}-reviewer.md.j2   # Reviewer prompt for this skill
```

If no skill-specific templates exist, the generic `builder.md.j2` and `reviewer.md.j2` are used.

## Quality Gates

Quality gates are checks that the builder's output must pass:

| Gate | Description |
|------|-------------|
| `lint` | Code passes linter (flake8, ruff, etc.) |
| `unit_test` | Unit tests pass |
| `integration_test` | Integration tests pass |
| `contract_test` | Contract tests pass |
| `security_scan` | No security vulnerabilities |
| `artifact_checksum` | Artifact integrity verified |

## Preconditions & Postconditions

- **Preconditions**: Checked before the build phase. If any fail, the task fails immediately without consuming retry budget.
- **Postconditions**: Checked after the build phase. Currently validated by the reviewer.

## Example: code-review Skill

```yaml
id: code-review
version: "1.0.0"
description: "Review existing code for quality issues"

quality_gates:
  - lint

timeouts:
  run_sec: 900
  verify_sec: 300

retry:
  max_attempts: 1
  backoff: none
```

## Using Your Skill

```bash
ma go "Review the authentication module" --skill my-new-skill
```
