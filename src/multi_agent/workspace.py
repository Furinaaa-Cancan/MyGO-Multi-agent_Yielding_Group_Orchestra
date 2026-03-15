"""Workspace manager — manages .multi-agent/ directory (inbox/outbox/dashboard)."""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from multi_agent._utils import validate_agent_id as _validate_agent_id
from multi_agent._utils import validate_task_id as _validate_task_id
from multi_agent.config import (
    history_dir,
    inbox_dir,
    outbox_dir,
    tasks_dir,
    workspace_dir,
)

_F = TypeVar("_F", bound=Callable[..., Any])

_log = logging.getLogger(__name__)


def _fsync_file(fd: int) -> None:
    """Flush file data to disk before atomic rename (crash safety)."""
    os.fsync(fd)


FILE_OP_RETRIES = 3
FILE_OP_DELAY = 0.1


def retry_file_op(retries: int = FILE_OP_RETRIES, delay: float = FILE_OP_DELAY) -> Callable[[_F], _F]:
    """Retry decorator for file operations that may fail due to transient OS errors.

    Uses exponential backoff with jitter to avoid thundering-herd on shared
    filesystems (literature: production AI error handling best practice).
    """
    import random
    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err = None
            for attempt in range(retries):
                try:
                    return fn(*args, **kwargs)
                except OSError as e:
                    last_err = e
                    if attempt < retries - 1:
                        _log.warning(
                            "%s failed (attempt %d/%d): %s",
                            fn.__name__, attempt + 1, retries, e,
                        )
                        backoff = delay * (2 ** attempt) + random.uniform(0, delay)
                        time.sleep(backoff)
            raise last_err  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]  # functools.wraps preserves signature
    return decorator


def ensure_workspace() -> Path:
    """Create .multi-agent/ and all subdirectories if they don't exist."""
    ws = workspace_dir()
    for d in [ws, inbox_dir(), outbox_dir(), tasks_dir(), history_dir()]:
        d.mkdir(parents=True, exist_ok=True)
    # Task 14: check disk space and warn if low
    try:
        ok, avail = check_disk_space()
        if not ok:
            import warnings
            warnings.warn(f"磁盘空间不足: 仅剩 {avail} MB，建议至少 100 MB", stacklevel=2)
    except Exception:
        pass
    return ws


@retry_file_op()
def write_inbox(agent_id: str, content: str) -> Path:
    """Write a prompt file to inbox/{agent_id}.md."""
    _validate_agent_id(agent_id)
    ensure_workspace()
    path = inbox_dir() / f"{agent_id}.md"
    path.write_text(content, encoding="utf-8")
    return path


def validate_outbox_data(role: str, data: dict[str, Any]) -> list[str]:
    """Validate outbox data for a given role. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    if role == "builder":
        if "status" not in data:
            errors.append("missing 'status' field")
        if "summary" not in data:
            errors.append("missing 'summary' field")
    elif role == "reviewer":
        if "decision" not in data:
            errors.append("missing 'decision' field")
    return errors


def read_outbox(agent_id: str, *, validate: bool = False) -> dict[str, Any] | None:
    """Read and parse outbox/{agent_id}.json. Returns None if not found or corrupt.

    When validate=True, checks that the data has required fields for the role.
    Tries UTF-8 first, then UTF-8-BOM, then latin-1 as encoding fallbacks.
    """
    _validate_agent_id(agent_id)
    path = outbox_dir() / f"{agent_id}.json"
    if not path.exists():
        return None

    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            data = json.loads(text)
            if not isinstance(data, dict):
                _log.warning("Outbox %s is not a JSON object (got %s), ignoring.", path, type(data).__name__)
                return None
            if validate:
                errors = validate_outbox_data(agent_id, data)
                if errors:
                    _log.warning("Outbox %s validation failed: %s", agent_id, "; ".join(errors))
                    return None
            return data
        except (UnicodeDecodeError, OSError):
            continue
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("Outbox %s JSON parse error (enc=%s): %s", path, enc, exc)
            continue

    _log.warning("Outbox %s unreadable after trying all encodings.", path)
    return None


@retry_file_op()
def write_outbox(agent_id: str, data: dict[str, Any]) -> Path:
    """Write agent output to outbox/{agent_id}.json.

    D2: Uses atomic write (temp file + os.replace) to prevent the watcher
    from reading partial JSON during write (TOCTOU race condition).
    """
    _validate_agent_id(agent_id)
    ensure_workspace()
    path = outbox_dir() / f"{agent_id}.json"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{agent_id}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)  # atomic on POSIX
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return path


def clear_outbox(agent_id: str) -> None:
    """Remove outbox file for an agent (before a new cycle)."""
    _validate_agent_id(agent_id)
    path = outbox_dir() / f"{agent_id}.json"
    path.unlink(missing_ok=True)


def clear_inbox(agent_id: str) -> None:
    """Remove inbox file for an agent."""
    _validate_agent_id(agent_id)
    path = inbox_dir() / f"{agent_id}.md"
    path.unlink(missing_ok=True)


@retry_file_op()
def save_task_yaml(task_id: str, data: dict[str, Any]) -> Path:
    """Save task state to tasks/{task_id}.yaml (atomic write)."""
    import yaml

    _validate_task_id(task_id)
    ensure_workspace()
    path = tasks_dir() / f"{task_id}.yaml"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{task_id}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)  # atomic on POSIX
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return path


def update_task_yaml(task_id: str, updates: dict[str, Any]) -> Path:
    """Merge-update task YAML while preserving existing metadata fields.

    Uses atomic read-merge-write (tempfile + fsync + os.replace) to prevent
    partial writes from corrupting the YAML on crash.
    """
    import yaml

    _validate_task_id(task_id)
    ensure_workspace()
    path = tasks_dir() / f"{task_id}.yaml"
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

    merged = dict(existing)
    merged.update(updates)
    merged["task_id"] = task_id

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{task_id}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)  # atomic on POSIX
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return path


# ── Task Lock ─────────────────────────────────────────────

def _lock_path() -> Path:
    return workspace_dir() / ".lock"


def read_lock() -> str | None:
    """Read the active task_id from lock file. Returns None if no lock."""
    p = _lock_path()
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    except FileNotFoundError:
        return None


def acquire_lock(task_id: str) -> None:
    """Write lock file with the given task_id.

    Uses atomic O_CREAT|O_EXCL to prevent race conditions in multi-process scenarios.
    Raises RuntimeError if lock is already held by another task.
    """
    import os
    _validate_task_id(task_id)
    ensure_workspace()
    lock_path = _lock_path()

    # Atomic lock acquisition: O_EXCL ensures file creation fails if it exists.
    # Self-heal path: if existing lock is empty/corrupted, attempt removal once.
    # The retry loop + O_EXCL keeps the TOCTOU window minimal (another process
    # could still win between unlink and the second O_EXCL open, but that is
    # the correct outcome — the other process legitimately acquired the lock).
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, task_id.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        except FileExistsError:
            existing = read_lock()
            if existing is None and attempt == 0:
                # Empty/corrupted lock — safe to reclaim.
                with contextlib.suppress(FileNotFoundError):
                    lock_path.unlink()
                continue
            raise RuntimeError(
                f"Lock already held by task '{existing}'. "
                f"Run 'my cancel' to release it or wait for completion."
            ) from None


def release_lock() -> None:
    """Remove lock file."""
    _lock_path().unlink(missing_ok=True)


def clear_runtime() -> None:
    """Remove all shared runtime files (inbox, outbox, TASK.md, dashboard).

    Called at task start to ensure clean state, and at task end to prevent
    stale files from leaking into the next task.
    """
    for role in ("builder", "reviewer", "decompose"):
        clear_inbox(role)
        clear_outbox(role)
    for name in ("TASK.md", "dashboard.md"):
        p = workspace_dir() / name
        if p.exists():
            p.unlink()


MAX_FILE_SIZE_MB = 50


def check_workspace_health() -> list[str]:
    """Check workspace health. Returns list of issues (empty = healthy)."""
    issues: list[str] = []
    ws = workspace_dir()

    # Check required directories
    for d_fn in (inbox_dir, outbox_dir, tasks_dir, history_dir):
        d = d_fn()
        if not d.exists():
            issues.append(f"Missing directory: {d.relative_to(ws)}")

    # Check store.db writable
    from multi_agent.config import store_db_path
    db = store_db_path()
    if db.exists():
        try:
            with db.open("a"):
                pass
        except OSError:
            issues.append(f"store.db is not writable: {db}")

    # Check orphan lock
    lock_content = read_lock()
    if lock_content:
        task_file = tasks_dir() / f"{lock_content}.yaml"
        if not task_file.exists():
            issues.append(f"Orphan lock: task '{lock_content}' has no YAML file")

    # Check oversized files
    issues.extend(_find_oversized_files(ws))

    return issues


def _find_oversized_files(ws: Path) -> list[str]:
    """Scan workspace for files exceeding MAX_FILE_SIZE_MB."""
    found: list[str] = []
    if not ws.exists():
        return found
    for f in ws.rglob("*"):
        if not f.is_file() or f.is_symlink():
            continue
        # Prevent symlink escape: resolved path must stay within workspace
        try:
            if not str(f.resolve()).startswith(str(ws.resolve())):
                found.append(f"Symlink escape detected: {f.relative_to(ws)}")
                continue
            size_mb = f.stat().st_size / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                found.append(f"Oversized file ({size_mb:.1f}MB): {f.relative_to(ws)}")
        except OSError:
            pass
    return found


def get_workspace_stats() -> dict[str, Any]:
    """Get workspace size statistics."""
    ws = workspace_dir()
    if not ws.exists():
        return {"total_size_mb": 0, "file_count": 0, "largest_file": "", "oldest_file": ""}

    total_size = 0
    file_count = 0
    largest_size = 0
    largest_name = ""
    oldest_time = float("inf")
    oldest_name = ""

    for f in ws.rglob("*"):
        if not f.is_file():
            continue
        try:
            st = f.stat()
            total_size += st.st_size
            file_count += 1
            if st.st_size > largest_size:
                largest_size = st.st_size
                largest_name = str(f.relative_to(ws))
            if st.st_mtime < oldest_time:
                oldest_time = st.st_mtime
                oldest_name = str(f.relative_to(ws))
        except OSError:
            pass

    return {
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "file_count": file_count,
        "largest_file": largest_name,
        "oldest_file": oldest_name,
    }


def check_disk_space(min_mb: int = 100) -> tuple[bool, int]:
    """Check available disk space. Returns (is_sufficient, available_mb)."""
    usage = shutil.disk_usage(workspace_dir().parent)
    available_mb = usage.free // (1024 * 1024)
    return (available_mb >= min_mb, available_mb)


def cleanup_old_files(max_age_days: int = 7) -> int:
    """Remove old files from tasks/, history/, and logs/. Returns count of deleted files."""
    ws = workspace_dir()
    active_task = read_lock()
    deleted = 0
    cutoff = time.time() - (max_age_days * 86400)

    for subdir in ("tasks", "history", "logs", "snapshots", "cache"):
        d = ws / subdir
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.is_symlink():
                continue
            # Prevent symlink escape: resolved path must stay within workspace
            try:
                if not str(f.resolve()).startswith(str(ws.resolve())):
                    continue
            except OSError:
                continue
            # Don't delete active task files
            if active_task and active_task in f.name:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
    return deleted


@retry_file_op()
def archive_conversation(task_id: str, conversation: list[dict[str, Any]]) -> Path:
    """Archive conversation history to history/{task_id}.json (atomic write)."""
    _validate_task_id(task_id)
    ensure_workspace()
    path = history_dir() / f"{task_id}.json"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{task_id}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(conversation, f, ensure_ascii=False, indent=2)
            f.write("\n")
        Path(tmp).replace(path)  # atomic on POSIX
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return path
