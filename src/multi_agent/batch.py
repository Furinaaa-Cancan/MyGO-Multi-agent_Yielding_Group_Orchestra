"""Batch Mode — run multiple tasks from a YAML manifest.

Usage::

    my batch tasks.yaml
    my batch tasks.yaml --dry-run
    my batch tasks.yaml --parallel 2

Manifest format (tasks.yaml)::

    tasks:
      - requirement: "Add user login endpoint"
        skill: code-implement
        builder: windsurf
        reviewer: cursor
      - requirement: "Write unit tests for auth"
        template: test
      - requirement: "Fix CORS bug"
        retry_budget: 3
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

_MAX_BATCH_FILE_SIZE = 256 * 1024  # 256 KB
_MAX_TASKS_PER_BATCH = 50


class BatchValidationError(Exception):
    """Raised when batch manifest has structural errors."""


def load_batch_manifest(path: Path) -> list[dict[str, Any]]:
    """Load and validate a batch manifest YAML file.

    Returns list of task dicts, each with at least 'requirement' or 'template'.

    Raises:
        BatchValidationError: If manifest is invalid.
        FileNotFoundError: If file doesn't exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Batch file not found: {path}")

    try:
        fsize = path.stat().st_size
    except OSError as e:
        raise BatchValidationError(f"Cannot stat {path}: {e}") from e

    if fsize > _MAX_BATCH_FILE_SIZE:
        raise BatchValidationError(
            f"Batch file too large: {fsize} bytes > {_MAX_BATCH_FILE_SIZE}"
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BatchValidationError(f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise BatchValidationError("Batch manifest must be a YAML mapping with 'tasks' key")

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise BatchValidationError("'tasks' must be a non-empty list")

    if len(tasks) > _MAX_TASKS_PER_BATCH:
        raise BatchValidationError(
            f"Too many tasks: {len(tasks)} > {_MAX_TASKS_PER_BATCH}"
        )

    validated: list[dict[str, Any]] = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise BatchValidationError(f"Task #{i+1} must be a mapping")
        if not task.get("requirement") and not task.get("template"):
            raise BatchValidationError(
                f"Task #{i+1} must have 'requirement' or 'template'"
            )
        # Cap requirement length
        if task.get("requirement"):
            task["requirement"] = str(task["requirement"])[:2000]
        validated.append(task)

    return validated


def format_batch_summary(results: list[dict[str, Any]]) -> str:
    """Format batch execution results as a summary string."""
    total = len(results)
    ok = sum(1 for r in results if r.get("status") == "completed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")

    lines = [
        f"{'='*40}",
        f"  Batch 结果: {ok}/{total} 完成",
    ]
    if failed:
        lines.append(f"  ❌ {failed} 失败")
    if skipped:
        lines.append(f"  ⏭️  {skipped} 跳过")

    elapsed = sum(r.get("elapsed", 0) for r in results)
    lines.append(f"  ⏱️  总耗时: {elapsed:.1f}s")
    lines.append(f"{'='*40}")

    for i, r in enumerate(results):
        status_icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(
            r.get("status", ""), "❓"
        )
        req = r.get("requirement", r.get("template", "?"))[:60]
        lines.append(f"  {i+1}. {status_icon} {req}")
        if r.get("error"):
            lines.append(f"     ↳ {r['error'][:100]}")

    return "\n".join(lines)
