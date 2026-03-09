"""Git integration — auto-commit, branch management, and test runner.

Provides git operations triggered by EventHooks after build/review/approve
steps. All operations are opt-in via .ma.yaml ``git:`` configuration block.

Safety: never force-pushes, never commits on detached HEAD, checks for
.git existence before any operation.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent.config import load_project_config, root_dir

_log = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────


@dataclass(frozen=True)
class GitConfig:
    """Parsed git integration settings from .ma.yaml."""

    auto_commit: bool = False
    auto_branch: bool = False
    branch_prefix: str = "task/"
    commit_on: tuple[str, ...] = ("build", "approve")
    auto_tag: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitConfig:
        commit_on = data.get("commit_on", ["build", "approve"])
        if isinstance(commit_on, str):
            commit_on = [commit_on]
        prefix = str(data.get("branch_prefix", "task/"))
        # Validate branch_prefix: must be safe for git branch names
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9/_.-]{0,30}$', prefix):
            _log.warning("Invalid branch_prefix '%s', falling back to 'task/'", prefix)
            prefix = "task/"
        return cls(
            auto_commit=bool(data.get("auto_commit", False)),
            auto_branch=bool(data.get("auto_branch", False)),
            branch_prefix=prefix,
            commit_on=tuple(commit_on),
            auto_tag=bool(data.get("auto_tag", False)),
        )


@dataclass(frozen=True)
class AutoTestConfig:
    """Parsed auto_test settings from .ma.yaml."""

    enabled: bool = False
    command: str = "pytest tests/ -q --tb=short"
    inject_evidence: bool = True
    fail_action: str = "warn"  # "warn" | "block"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutoTestConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            command=str(data.get("command", "pytest tests/ -q --tb=short")),
            inject_evidence=bool(data.get("inject_evidence", True)),
            fail_action=str(data.get("fail_action", "warn")),
        )


def load_git_config() -> GitConfig:
    """Load git config from .ma.yaml, returning defaults if absent."""
    proj = load_project_config()
    git_section = proj.get("git")
    if isinstance(git_section, dict):
        return GitConfig.from_dict(git_section)
    return GitConfig()


def load_auto_test_config() -> AutoTestConfig:
    """Load auto_test config from .ma.yaml, returning defaults if absent."""
    proj = load_project_config()
    test_section = proj.get("auto_test")
    if isinstance(test_section, dict):
        return AutoTestConfig.from_dict(test_section)
    return AutoTestConfig()


# ── Git Primitives ────────────────────────────────────────


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command safely (shell=False). Raises on failure."""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd or root_dir()),
        capture_output=True,
        text=True,
        timeout=30,
    )


def has_git() -> bool:
    """Check if git is available and project is a git repo."""
    if not shutil.which("git"):
        return False
    git_dir = root_dir() / ".git"
    return git_dir.exists() or git_dir.is_file()  # .git can be a file for worktrees


def is_clean() -> bool:
    """Check if working tree is clean (no uncommitted changes)."""
    result = _git("status", "--porcelain")
    return result.returncode == 0 and not result.stdout.rstrip()


def current_branch() -> str | None:
    """Return current branch name, or None if detached HEAD."""
    result = _git("rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return None if branch == "HEAD" else branch


def has_changes() -> bool:
    """Check if there are staged or unstaged changes."""
    result = _git("status", "--porcelain")
    return result.returncode == 0 and bool(result.stdout.rstrip())


def changed_files() -> list[str]:
    """Return list of changed files (staged + unstaged + untracked)."""
    result = _git("status", "--porcelain")
    if result.returncode != 0:
        return []
    files: list[str] = []
    for line in result.stdout.splitlines():
        # git status --porcelain: XY filename (2 status chars + space + path)
        if len(line) >= 4:
            files.append(line[3:].strip())
    return files


# ── Git Operations ────────────────────────────────────────


def create_branch(task_id: str, prefix: str = "task/") -> str:
    """Create and checkout a new branch for a task. Returns branch name."""
    branch_name = f"{prefix}{task_id}"
    existing = current_branch()
    if existing == branch_name:
        _log.info("Already on branch %s", branch_name)
        return branch_name

    # Use -- to prevent branch_name from being interpreted as a git flag
    result = _git("checkout", "-b", branch_name, "--")
    if result.returncode != 0:
        # Branch might already exist — try switching
        result = _git("checkout", branch_name, "--")
        if result.returncode != 0:
            _log.error("Failed to create/switch to branch %s: %s", branch_name, result.stderr)
            raise RuntimeError(f"git branch failed: {result.stderr.strip()}")

    _log.info("Switched to branch %s", branch_name)
    return branch_name


def auto_commit(
    message: str,
    *,
    task_id: str = "",
    changed: list[str] | None = None,
) -> str | None:
    """Stage all changes and commit. Returns commit SHA or None if nothing to commit."""
    if not has_git():
        _log.warning("No git repo found, skipping auto-commit")
        return None

    if not has_changes():
        _log.info("No changes to commit for task %s", task_id)
        return None

    # Stage specific files if provided, otherwise stage all
    # Use -- separator to prevent filenames starting with - from being
    # interpreted as git flags (defense against malicious builder output)
    if changed:
        for f in changed:
            _git("add", "--", f)
    else:
        _git("add", "-A")

    result = _git("commit", "-m", message)
    if result.returncode != 0:
        _log.error("git commit failed: %s", result.stderr)
        return None

    # Get the commit SHA
    sha_result = _git("rev-parse", "--short", "HEAD")
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"
    _log.info("Committed %s: %s", sha, message)
    return sha


def create_tag(tag_name: str, message: str = "") -> bool:
    """Create an annotated git tag. Returns True on success."""
    args = ["tag"]
    if message:
        args.extend(["-a", tag_name, "-m", message])
    else:
        args.append(tag_name)

    result = _git(*args)
    if result.returncode != 0:
        _log.error("git tag failed: %s", result.stderr)
        return False
    _log.info("Created tag %s", tag_name)
    return True


# ── Auto-Test Runner ──────────────────────────────────────


@dataclass
class AutoTestResult:
    """Result of an automated test run."""

    passed: bool = False
    exit_code: int = 1
    stdout: str = ""
    stderr: str = ""
    summary: str = ""
    test_count: int = 0
    fail_count: int = 0

    @property
    def as_evidence(self) -> list[str]:
        """Format test result as evidence entries for reviewer."""
        entries: list[str] = []
        if self.passed:
            entries.append(f"Auto-test PASSED: {self.summary}")
        else:
            entries.append(f"Auto-test FAILED (exit {self.exit_code}): {self.summary}")
        # Include last few lines of output as detail
        output_lines = self.stdout.strip().splitlines()
        if output_lines:
            tail = output_lines[-min(5, len(output_lines)):]
            entries.append("Test output: " + " | ".join(tail))
        return entries


def run_tests(config: AutoTestConfig | None = None) -> AutoTestResult:
    """Run project tests using configured command. Returns AutoTestResult."""
    if config is None:
        config = load_auto_test_config()

    if not config.enabled:
        return AutoTestResult(passed=True, exit_code=0, summary="auto-test disabled")

    cmd = shlex.split(config.command)
    _log.info("Running auto-test: %s", config.command)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(root_dir()),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return AutoTestResult(
            passed=False,
            exit_code=-1,
            summary="Test timed out (300s limit)",
        )
    except FileNotFoundError:
        return AutoTestResult(
            passed=False,
            exit_code=-1,
            summary=f"Test command not found: {cmd[0]}",
        )

    # Parse pytest-style summary from last line
    summary = ""
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line:
            summary = line
            break

    test_count = 0
    fail_count = 0
    # Try to extract counts from pytest output like "27 passed" or "3 failed"
    passed_match = re.search(r"(\d+)\s+passed", summary)
    failed_match = re.search(r"(\d+)\s+failed", summary)
    if passed_match:
        test_count += int(passed_match.group(1))
    if failed_match:
        fail_count = int(failed_match.group(1))
        test_count += fail_count

    return AutoTestResult(
        passed=result.returncode == 0,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        summary=summary or f"exit code {result.returncode}",
        test_count=test_count,
        fail_count=fail_count,
    )


# ── Hook Handlers ─────────────────────────────────────────


def _make_on_build_submit(cfg: GitConfig) -> Callable[..., None]:
    """Create build-submit hook with cached config."""
    def _on_build_submit(state: Any, result: dict[str, Any] | None = None) -> None:
        """Hook: auto-commit after builder submits."""
        if not cfg.auto_commit or "build" not in cfg.commit_on:
            return

        task_id = state.get("task_id", "unknown") if isinstance(state, dict) else "unknown"
        builder_id = state.get("builder_id", "?") if isinstance(state, dict) else "?"
        summary = ""
        files: list[str] = []
        if isinstance(result, dict):
            builder_out = result.get("builder_output")
            if isinstance(builder_out, dict):
                summary = builder_out.get("summary", "")
                files = builder_out.get("changed_files", [])

        msg = f"build({builder_id}): {summary}" if summary else f"build({builder_id}): task {task_id}"
        auto_commit(msg, task_id=task_id, changed=files or None)
    return _on_build_submit


def _make_on_decide_approve(cfg: GitConfig) -> Callable[..., None]:
    """Create decide-approve hook with cached config."""
    def _on_decide_approve(state: Any, result: dict[str, Any] | None = None) -> None:
        """Hook: auto-commit + tag after task approved.

        IMPORTANT: This fires on ALL decide exits. Must check final_status
        to avoid committing/tagging on reject or request_changes.
        """
        # Only act on actual approvals
        if not isinstance(result, dict) or result.get("final_status") != "approved":
            return

        task_id = state.get("task_id", "unknown") if isinstance(state, dict) else "unknown"

        if cfg.auto_commit and "approve" in cfg.commit_on:
            auto_commit(f"approved: task {task_id}", task_id=task_id)

        if cfg.auto_tag:
            tag = f"task/{task_id}"
            create_tag(tag, f"Task {task_id} approved")
    return _on_decide_approve


def _make_on_plan_start(cfg: GitConfig) -> Callable[..., None]:
    """Create plan-start hook with cached config."""
    def _on_plan_start(state: Any) -> None:
        """Hook: create task branch at plan start (if auto_branch enabled)."""
        if not cfg.auto_branch or not has_git():
            return

        task_id = state.get("task_id", "") if isinstance(state, dict) else ""
        if not task_id:
            return

        try:
            create_branch(task_id, prefix=cfg.branch_prefix)
        except RuntimeError as e:
            _log.warning("Auto-branch creation failed: %s", e)
    return _on_plan_start


# ── Registration ──────────────────────────────────────────


_hooks_registered = False


def reset_git_hooks() -> None:
    """Reset hook registration state. Used for testing."""
    global _hooks_registered
    _hooks_registered = False


def register_git_hooks() -> None:
    """Register git integration hooks with the graph EventHooks system.

    Safe to call multiple times — hooks are registered only once.
    """
    global _hooks_registered
    if _hooks_registered:
        return
    _hooks_registered = True

    cfg = load_git_config()
    if not cfg.auto_commit and not cfg.auto_branch and not cfg.auto_tag:
        _log.debug("Git integration disabled (no git options in .ma.yaml)")
        return

    _register_hooks_for_config(cfg)


def register_git_hooks_override() -> None:
    """Force-register git hooks with auto_commit=True (for --git-commit CLI flag).

    Overrides .ma.yaml settings. Safe to call multiple times.
    """
    global _hooks_registered
    if _hooks_registered:
        return
    _hooks_registered = True

    cfg = load_git_config()
    # Override: force auto_commit on build+approve
    cfg = GitConfig(
        auto_commit=True,
        auto_branch=cfg.auto_branch,
        branch_prefix=cfg.branch_prefix,
        commit_on=cfg.commit_on if cfg.commit_on else ("build", "approve"),
        auto_tag=cfg.auto_tag,
    )
    _register_hooks_for_config(cfg)


def _register_hooks_for_config(cfg: GitConfig) -> None:
    """Internal: register hooks based on a GitConfig."""
    if not has_git():
        _log.warning("Git hooks requested but no git repo found — skipping")
        return

    from multi_agent.graph_infra import graph_hooks

    if cfg.auto_branch:
        graph_hooks.on_node_enter("plan", _make_on_plan_start(cfg))
        _log.info("Registered git hook: auto-branch on plan start")

    if cfg.auto_commit:
        if "build" in cfg.commit_on:
            graph_hooks.on_node_exit("build", _make_on_build_submit(cfg))
            _log.info("Registered git hook: auto-commit on build submit")
        if "approve" in cfg.commit_on:
            graph_hooks.on_node_exit("decide", _make_on_decide_approve(cfg))
            _log.info("Registered git hook: auto-commit on approve")
