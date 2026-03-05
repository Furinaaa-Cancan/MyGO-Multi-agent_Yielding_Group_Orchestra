# Contributing to MyGO

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/Furinaaa-Cancan/MyGO.git
cd MyGO
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Code Style

- Follow existing code style in the project
- Use type hints for all function signatures
- Keep functions focused and well-documented
- Imports at the top of files, grouped: stdlib → third-party → local

## Testing

- All new features must include tests
- Run the full test suite before submitting:

```bash
pytest tests/ -v
```

- Test coverage should not decrease from the current level
- Use `pytest` fixtures and `tmp_path` for filesystem tests
- Mock external dependencies (config, file I/O) appropriately

## Commit Convention

Use prefixed commit messages:

| Prefix | Usage |
|--------|-------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `test:` | Adding or updating tests |
| `refactor:` | Code change that neither fixes a bug nor adds a feature |
| `chore:` | Build process or auxiliary tool changes |

Example: `feat: add retry mechanism to workspace file operations`

## Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes with tests
4. Run `pytest tests/ -v` — all tests must pass
5. Commit with conventional prefix
6. Push and open a Pull Request
7. Describe what changed and why in the PR description

## Project Structure

```
src/multi_agent/
├── cli.py          # CLI entry point (Click)
├── config.py       # Path resolution and configuration
├── schema.py       # Pydantic models (Task, SkillContract, etc.)
├── graph.py        # LangGraph 4-node workflow
├── router.py       # Agent role assignment
├── driver.py       # Agent drivers (CLI/file)
├── workspace.py    # Workspace file management
├── watcher.py      # Outbox polling
├── prompt.py       # Jinja2 prompt rendering
├── decompose.py    # Task decomposition
├── dashboard.py    # Progress dashboard generation
├── contract.py     # Skill contract loading
└── templates/      # Jinja2 prompt templates
```

## Adding a New Skill

See [docs/skill-development.md](docs/skill-development.md) for the full guide.

## Adding a New Agent

See [docs/agent-configuration.md](docs/agent-configuration.md) for the full guide.
