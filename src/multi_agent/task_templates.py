"""Task template system — predefined task configs for one-command launch.

Templates are YAML files stored in ``task-templates/`` at the project root.
Each template defines a reusable task configuration (requirement, skill,
builder/reviewer preferences, flags, etc.) that can be launched via::

    my go --template auth
    my go --template crud --var model=User --var table=users

Template resolution order:
    1. ``task-templates/`` in project root (built-in / project-level)
    2. Additional directories specified in ``.ma.yaml`` under ``template_dirs``
"""

from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any

import yaml

from multi_agent.config import load_project_config, root_dir

# ── Constants ────────────────────────────────────────────

_SAFE_TEMPLATE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

_REQUIRED_FIELDS = frozenset({"id", "name", "requirement"})

_KNOWN_FIELDS = frozenset({
    "id", "name", "description", "requirement",
    "skill", "builder", "reviewer",
    "retry_budget", "timeout", "mode",
    "decompose", "tags", "variables",
})

_MAX_TEMPLATE_FILE_SIZE = 64 * 1024  # 64 KB cap per template file

_SAFE_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


# ── Exceptions ───────────────────────────────────────────

class TemplateNotFoundError(Exception):
    """Raised when a template ID cannot be resolved."""


class TemplateValidationError(Exception):
    """Raised when a template YAML has structural errors."""


# ── Template Data Class ──────────────────────────────────

class TaskTemplate:
    """Parsed and validated task template."""

    __slots__ = (
        "id", "name", "description", "requirement",
        "skill", "builder", "reviewer",
        "retry_budget", "timeout", "mode",
        "decompose", "tags", "variables", "source_path",
    )

    def __init__(self, data: dict[str, Any], source_path: Path | None = None):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.description: str = data.get("description", "")
        self.requirement: str = data["requirement"]
        self.skill: str = data.get("skill", "code-implement")
        self.builder: str = data.get("builder", "")
        self.reviewer: str = data.get("reviewer", "")
        self.retry_budget: int = data.get("retry_budget", 2)
        self.timeout: int = data.get("timeout", 1800)
        self.mode: str = data.get("mode", "strict")
        self.decompose: bool = data.get("decompose", False)
        self.tags: list[str] = data.get("tags", [])
        self.variables: dict[str, str] = data.get("variables", {})
        self.source_path: Path | None = source_path

    def as_dict(self) -> dict[str, Any]:
        """Return template fields as a plain dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "requirement": self.requirement,
            "skill": self.skill,
            "builder": self.builder,
            "reviewer": self.reviewer,
            "retry_budget": self.retry_budget,
            "timeout": self.timeout,
            "mode": self.mode,
            "decompose": self.decompose,
            "tags": self.tags,
            "variables": self.variables,
        }


# ── Validation ───────────────────────────────────────────

def _validate_template_data(data: dict[str, Any], path: Path | None = None) -> list[str]:
    """Validate raw template dict. Returns list of error messages."""
    errors: list[str] = []
    ctx = f" ({path})" if path else ""

    if not isinstance(data, dict):
        return [f"Template{ctx} is not a YAML mapping"]

    for field in _REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field '{field}'{ctx}")

    tid = data.get("id", "")
    if tid and not _SAFE_TEMPLATE_ID_RE.match(str(tid)):
        errors.append(
            f"Invalid template id '{tid}'{ctx}. "
            f"Must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}."
        )

    if "requirement" in data and not isinstance(data["requirement"], str):
        errors.append(f"'requirement' must be a string{ctx}")

    if "tags" in data and not isinstance(data["tags"], list):
        errors.append(f"'tags' must be a list{ctx}")

    if "variables" in data and not isinstance(data["variables"], dict):
        errors.append(f"'variables' must be a mapping{ctx}")

    # Validate skill format if present
    skill = data.get("skill", "")
    if skill and not _SAFE_SKILL_ID_RE.match(str(skill)):
        errors.append(f"Invalid skill '{skill}'{ctx}")

    # Warn about unknown fields
    unknown = set(data.keys()) - _KNOWN_FIELDS
    if unknown:
        errors.append(f"Unknown fields: {', '.join(sorted(unknown))}{ctx}")

    if "retry_budget" in data:
        rb = data["retry_budget"]
        if not isinstance(rb, int) or rb < 0 or rb > 20:
            errors.append(f"'retry_budget' must be int 0-20{ctx}, got {rb}")

    if "timeout" in data:
        to = data["timeout"]
        if not isinstance(to, int) or to < 1:
            errors.append(f"'timeout' must be positive int{ctx}, got {to}")

    return errors


# ── Loading ──────────────────────────────────────────────

def _template_dirs() -> list[Path]:
    """Return ordered list of directories to search for templates."""
    dirs: list[Path] = []

    # Primary: task-templates/ in project root
    primary = root_dir() / "task-templates"
    if primary.is_dir():
        dirs.append(primary)

    # Additional dirs from .ma.yaml (must stay within project root)
    proj = load_project_config()
    extra = proj.get("template_dirs")
    project_root = root_dir().resolve()
    if isinstance(extra, list):
        for d in extra:
            p = Path(d) if Path(d).is_absolute() else root_dir() / d
            resolved = p.resolve()
            # Prevent path traversal outside project root
            try:
                resolved.relative_to(project_root)
            except ValueError:
                continue
            if resolved.is_dir():
                dirs.append(resolved)

    return dirs


def load_template(template_id: str) -> TaskTemplate:
    """Load and validate a single template by ID.

    Raises:
        TemplateNotFoundError: If no template matches the ID.
        TemplateValidationError: If the template YAML is invalid.
    """
    if not _SAFE_TEMPLATE_ID_RE.match(template_id):
        raise TemplateNotFoundError(
            f"Invalid template ID: {template_id!r}. "
            f"Must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}."
        )

    for tdir in _template_dirs():
        for ext in (".yaml", ".yml"):
            path = tdir / f"{template_id}{ext}"
            if path.is_file():
                return _load_template_file(path)

    available = list_templates()
    avail_str = ", ".join(t.id for t in available) if available else "(none)"
    raise TemplateNotFoundError(
        f"Template '{template_id}' not found. Available: {avail_str}"
    )


def _load_template_file(path: Path) -> TaskTemplate:
    """Parse and validate a single template YAML file."""
    try:
        fsize = path.stat().st_size
    except OSError as e:
        raise TemplateValidationError(f"Cannot stat {path}: {e}") from e
    if fsize > _MAX_TEMPLATE_FILE_SIZE:
        raise TemplateValidationError(
            f"Template file too large: {path} ({fsize} bytes > {_MAX_TEMPLATE_FILE_SIZE})"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplateValidationError(f"YAML parse error in {path}: {e}") from e

    if not isinstance(data, dict):
        raise TemplateValidationError(f"Template {path} is not a YAML mapping")

    errors = _validate_template_data(data, path)
    if errors:
        raise TemplateValidationError(
            "Template validation failed:\n  " + "\n  ".join(errors)
        )

    return TaskTemplate(data, source_path=path)


def list_templates() -> list[TaskTemplate]:
    """Discover and load all valid templates from all template directories."""
    templates: list[TaskTemplate] = []
    seen_ids: set[str] = set()

    for tdir in _template_dirs():
        for path in sorted(tdir.glob("*.yaml")) + sorted(tdir.glob("*.yml")):
            try:
                tmpl = _load_template_file(path)
            except (TemplateValidationError, yaml.YAMLError, OSError):
                continue
            if tmpl.id not in seen_ids:
                seen_ids.add(tmpl.id)
                templates.append(tmpl)

    return templates


# ── Variable Substitution ────────────────────────────────

def resolve_variables(
    template: TaskTemplate,
    overrides: dict[str, str] | None = None,
) -> TaskTemplate:
    """Substitute ``${var}`` placeholders in the requirement string.

    Variables are resolved in this order:
        1. ``overrides`` from ``--var key=value`` CLI args (highest precedence)
        2. ``variables`` defined in the template YAML (defaults)

    Unresolved placeholders are left as-is (no error).
    """
    merged_vars: dict[str, str] = dict(template.variables)
    if overrides:
        merged_vars.update(overrides)

    if not merged_vars:
        return template

    # Use safe_substitute to leave unresolved ${var} intact
    tpl = string.Template(template.requirement)
    resolved_req = tpl.safe_substitute(merged_vars)

    # Build a new template with the resolved requirement
    data = template.as_dict()
    data["requirement"] = resolved_req
    return TaskTemplate(data, source_path=template.source_path)


def parse_var_args(var_list: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Parse ``--var key=value`` CLI arguments into a dict.

    Raises ValueError on malformed entries.
    """
    result: dict[str, str] = {}
    for item in var_list:
        if "=" not in item:
            raise ValueError(
                f"Invalid --var format: {item!r}. Expected key=value."
            )
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in --var: {item!r}")
        result[key] = value
    return result
