"""Jinja2 prompt renderer — generates inbox prompts from templates."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

from functools import lru_cache

from jinja2 import Environment, FileSystemLoader, select_autoescape

from multi_agent.config import root_dir
from multi_agent.schema import SkillContract, Task

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 50000


def _template_dir() -> Path:
    """Resolve templates/ directory — inside the package (works after pip install)."""
    # Primary: templates bundled inside the package
    d = Path(__file__).parent / "templates"
    if d.is_dir():
        return d
    # Fallback: project root (dev mode / editable install)
    d = root_dir() / "templates"
    if d.is_dir():
        return d
    raise FileNotFoundError("Cannot find templates/ directory")


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_template_dir())),
        autoescape=select_autoescape([]),
        keep_trailing_newline=True,
    )


def _resolve_template(env: Environment, skill_id: str, role: str) -> str:
    """Resolve skill-specific template with fallback to generic.

    Looks for e.g. test-builder.md.j2 first, falls back to builder.md.j2.
    """
    skill_prefix = skill_id.replace("-", "-")  # keep as-is
    # Map skill_id to template prefix: "test-and-review" -> "test"
    prefix_map = {
        "test-and-review": "test",
        "task-decompose": "decompose",
    }
    prefix = prefix_map.get(skill_id, skill_id)
    specific = f"{prefix}-{role}.md.j2"
    try:
        env.get_template(specific)
        return specific
    except Exception:
        return f"{role}.md.j2"


def render_builder_prompt(
    task: Task,
    contract: SkillContract,
    agent_id: str,
    retry_count: int = 0,
    retry_feedback: str = "",
    retry_budget: int = 2,
    previous_summary: str = "",
) -> str:
    """Render the builder prompt from skill-specific or generic template."""
    env = _env()
    tmpl_name = _resolve_template(env, contract.id, "builder")
    tmpl = env.get_template(tmpl_name)
    result = tmpl.render(
        task=task,
        contract=contract,
        agent_id=agent_id,
        retry_count=retry_count,
        retry_feedback=retry_feedback,
        retry_budget=retry_budget,
        previous_summary=previous_summary,
    )
    result += "\n" + get_prompt_metadata("builder")
    return _truncate_if_needed(result)


def render_reviewer_prompt(
    task: Task,
    contract: SkillContract,
    agent_id: str,
    builder_output: dict,
    builder_id: str,
) -> str:
    """Render the reviewer prompt from skill-specific or generic template."""
    env = _env()
    tmpl_name = _resolve_template(env, contract.id, "reviewer")
    tmpl = env.get_template(tmpl_name)
    result = tmpl.render(
        task=task,
        contract=contract,
        agent_id=agent_id,
        builder_output=builder_output,
        builder_id=builder_id,
    )
    result += "\n" + get_prompt_metadata("reviewer")
    return _truncate_if_needed(result)


def get_prompt_metadata(role: str) -> str:
    """Generate HTML comment with version and timestamp for prompt tracking."""
    from datetime import datetime, timezone
    from multi_agent import __version__
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<!-- AgentOrchestra v{__version__} | prompt: {role} | rendered: {ts} -->"


def _truncate_if_needed(text: str) -> str:
    """Truncate prompt if it exceeds MAX_PROMPT_CHARS, preserving core sections."""
    if len(text) <= MAX_PROMPT_CHARS:
        return text
    original_len = len(text)
    warnings.warn(
        f"Prompt truncated from {original_len} to {MAX_PROMPT_CHARS} chars",
        stacklevel=2,
    )
    log.warning("Prompt truncated from %d to %d chars", original_len, MAX_PROMPT_CHARS)
    truncated = text[:MAX_PROMPT_CHARS]
    truncated += "\n\n(内容已截断，完整内容见 .multi-agent/inbox/ 下对应文件)"
    return truncated
