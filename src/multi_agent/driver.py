"""Agent drivers — spawn CLI agents, GUI automation, or show file-based instructions.

Architecture note (defect B1): All driver-type dispatch is consolidated in
``dispatch_agent()`` to eliminate the 4x duplicated if/else pattern that was
previously scattered across cli.py.  Callers should use ``dispatch_agent()``
instead of manually checking ``get_agent_driver()["driver"]``.

Supported drivers:
- ``file``  — write TASK.md, user manually tells IDE (default)
- ``cli``   — spawn CLI tool automatically (e.g. claude, aider)
- ``gui``   — macOS AppleScript automation for desktop IDE apps (e.g. Codex)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from multi_agent.config import outbox_dir, workspace_dir

logger = logging.getLogger(__name__)


# ── Driver Protocol (OCP compliance) ─────────────────────

@runtime_checkable
class AgentDriverProtocol(Protocol):
    """Abstract interface for agent execution strategies.

    Adding a new driver type (e.g. MCP, HTTP API) only requires implementing
    this protocol — no changes to dispatch_agent() or callers needed.
    """

    def is_available(self) -> bool:
        """Return True if this driver can execute right now."""
        ...

    def execute(self, agent_id: str, role: str, *, timeout_sec: int = 600) -> threading.Thread | None:
        """Execute the agent. Returns a Thread for async drivers, None for sync/manual."""
        ...

    def describe_fallback(self, agent_id: str) -> str:
        """Human-readable instruction when the driver can't auto-execute."""
        ...

# Task 10: concurrency lock — prevents duplicate CLI agent spawns
_cli_lock = threading.Lock()
_active_agents: dict[str, threading.Thread] = {}


def get_agent_driver(agent_id: str) -> dict[str, Any]:
    """Look up driver config for an agent from agents.yaml."""
    from multi_agent.router import load_agents

    for agent in load_agents():
        if agent.id == agent_id:
            return {"driver": agent.driver, "command": agent.command, "app_name": agent.app_name}
    return {"driver": "file", "command": "", "app_name": ""}


def get_latest_log(agent_id: str) -> Path | None:
    """Get the most recent log file for the given agent. Returns None if no logs exist."""
    logs_dir = workspace_dir() / "logs"
    if not logs_dir.exists():
        return None
    logs = sorted(logs_dir.glob(f"{agent_id}-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def can_use_cli(command_template: str) -> bool:
    """Check if the CLI binary in a command template is available on PATH.

    Extracts the first token (the binary name) and checks via shutil.which().
    Returns False if binary not found — caller should degrade to file mode.
    """
    binary = command_template.split()[0] if command_template.strip() else ""
    if not binary:
        return False
    return shutil.which(binary) is not None


def _stream_stdout(proc: subprocess.Popen[str], agent_id: str, role: str) -> str:
    """Read stdout line-by-line, print in real-time, return accumulated text."""
    lines: list[str] = []
    if proc.stdout:
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            logger.debug("[%s/%s stdout] %s", agent_id, role, line)
    return "\n".join(lines)


def classify_stderr(text: str) -> str:
    """Classify stderr content severity: 'error', 'warning', or 'info'."""
    lower = text.lower()
    if any(kw in lower for kw in ("error", "fatal", "traceback", "exception")):
        return "error"
    if any(kw in lower for kw in ("warning", "warn", "deprecat")):
        return "warning"
    return "info"


def _stream_stderr(proc: subprocess.Popen[str], agent_id: str, role: str) -> str:
    """Read stderr line-by-line, log in real-time with severity, write to log file, return accumulated text."""
    import time as _time
    lines: list[str] = []
    # Write stderr to log file
    logs_dir = workspace_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = int(_time.time())
    log_path = logs_dir / f"{agent_id}-{role}-{ts}.log"
    log_file = None
    try:
        log_file = log_path.open("w", encoding="utf-8")
        if proc.stderr:
            for line in proc.stderr:
                line = line.rstrip("\n")
                lines.append(line)
                log_file.write(line + "\n")
                log_file.flush()
                severity = classify_stderr(line)
                if severity == "error":
                    logger.error("[%s/%s stderr] %s", agent_id, role, line)
                elif severity == "warning":
                    logger.warning("[%s/%s stderr] %s", agent_id, role, line)
                else:
                    logger.info("[%s/%s stderr] %s", agent_id, role, line)
    finally:
        if log_file:
            log_file.close()
    return "\n".join(lines)


def spawn_cli_agent(
    agent_id: str,
    role: str,
    command_template: str,
    project_dir: str | None = None,
    timeout_sec: int = 600,
) -> threading.Thread:
    """Spawn a CLI agent in a background thread.

    The CLI agent reads TASK.md and writes its output to outbox/{role}.json.
    The watcher will detect the outbox file and resume the graph.

    Returns the thread (for testing). Caller does NOT need to join it.
    If same agent+role is already running, returns the existing thread.

    Security Note:
        Uses shell=False with shlex.split() for command execution, eliminating
        shell injection risk. The command_template comes from agents.yaml.
        Ensure agents.yaml has proper file permissions (0o644 or stricter).
    """
    # Task 10: concurrency protection — single lock scope to eliminate
    # check-then-act race between duplicate detection and thread registration.
    lock_key = f"{agent_id}:{role}"

    task_file = str(workspace_dir() / "TASK.md")
    outbox_file = str(outbox_dir() / f"{role}.json")

    # D1+C3: Build command list with shell=False to eliminate injection risk.
    # Paths are inserted literally (no quoting needed without shell).
    cmd_str = command_template.format(
        task_file=task_file,
        outbox_file=outbox_file,
    )
    cmd_list = shlex.split(cmd_str)

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                cmd_list,
                shell=False,
                cwd=project_dir or str(Path.cwd()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Drain stdout and stderr in background threads to avoid deadlock
            # when both pipes fill up (Python subprocess docs).
            # Previous design blocked on _stream_stderr, making proc.wait(timeout)
            # unreachable if the process hung producing infinite stderr.
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            def _drain_stdout() -> None:
                if proc.stdout:
                    stdout_lines.append(proc.stdout.read())

            def _drain_stderr() -> None:
                stderr_lines.append(_stream_stderr(proc, agent_id, role))

            stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            # Wait for process with enforced timeout — this is now reachable
            # regardless of how much stderr/stdout the process produces.
            proc.wait(timeout=timeout_sec)

            # Give pipe-drain threads a short grace period to finish reading
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            stdout_text = stdout_lines[0] if stdout_lines else ""
            stderr_text = stderr_lines[0] if stderr_lines else ""

            _ensure_outbox_written(
                outbox_file, stdout_text, stderr_text,
                agent_id, proc.returncode,
            )
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                proc.wait()  # reap zombie
            _write_error(outbox_file, f"{agent_id} CLI timed out after {timeout_sec}s")
        except Exception as e:
            _write_error(outbox_file, f"{agent_id} CLI error: {e}")
        finally:
            with _cli_lock:
                _active_agents.pop(lock_key, None)

    with _cli_lock:
        existing = _active_agents.get(lock_key)
        if existing and existing.is_alive():
            logger.info("CLI agent %s already running as %s, returning existing thread", agent_id, role)
            return existing
        t = threading.Thread(target=_run, daemon=True, name=f"cli-{agent_id}-{role}")
        _active_agents[lock_key] = t
        t.start()
    return t


def _ensure_outbox_written(
    outbox_file: str, stdout_text: str, stderr_text: str,
    agent_id: str, returncode: int | None,
) -> None:
    """Ensure outbox file exists after CLI run; extract from stdout or write error."""
    outbox_path = Path(outbox_file)
    if not outbox_path.exists() and stdout_text.strip():
        _try_extract_json(stdout_text, outbox_path)
    if not outbox_path.exists():
        stderr_hint = stderr_text.strip()[:200]
        if returncode != 0:
            _write_error(outbox_file, f"{agent_id} CLI exited with code {returncode}: {stderr_hint}")
        else:
            _write_error(outbox_file, f"{agent_id} CLI produced no parseable JSON output")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via temp file + os.replace (D3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


def _try_extract_json(text: str, outbox_path: Path) -> None:
    """Try to find and extract a JSON object from CLI output text."""
    # Look for JSON between ```json ... ``` markers
    match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                _atomic_write_json(outbox_path, data)
                return
        except json.JSONDecodeError:
            pass

    # Try parsing the whole output as JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            _atomic_write_json(outbox_path, data)
    except json.JSONDecodeError:
        pass


def _write_error(outbox_file: str, error_msg: str) -> None:
    """Write an error marker to outbox so the graph can detect failure."""
    _atomic_write_json(Path(outbox_file), {"status": "error", "summary": error_msg})


# ── GUI Driver (macOS AppleScript automation) ────────────

def can_use_gui() -> bool:
    """Check if macOS GUI automation is available (osascript exists)."""
    return shutil.which("osascript") is not None


def send_gui_message(app_name: str, message: str) -> bool:
    """Send a message to a macOS desktop IDE app via AppleScript.

    Activates the target app window, pastes the message via clipboard,
    and presses Enter to submit. Requires macOS Accessibility permission.

    Returns True on success, False on failure.
    """
    if not can_use_gui():
        logger.warning("osascript not found — GUI automation unavailable")
        return False

    applescript = f'''
tell application "{app_name}" to activate
delay 1.0
tell application "System Events"
    tell process "{app_name}"
        set frontmost to true
        delay 0.5
        keystroke "v" using command down
        delay 0.3
        keystroke return
    end tell
end tell
'''
    try:
        # Set clipboard content
        clip_proc = subprocess.run(
            ["pbcopy"], input=message, text=True,
            capture_output=True, timeout=5,
        )
        if clip_proc.returncode != 0:
            logger.error("pbcopy failed: %s", clip_proc.stderr)
            return False

        # Execute AppleScript
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error("AppleScript failed: %s", result.stderr.strip())
            return False

        logger.info("GUI message sent to %s", app_name)
        return True
    except subprocess.TimeoutExpired:
        logger.error("GUI automation timed out for %s", app_name)
        return False
    except Exception as e:
        logger.error("GUI automation error for %s: %s", app_name, e)
        return False


def spawn_gui_agent(
    agent_id: str,
    role: str,
    app_name: str,
) -> threading.Thread:
    """Send task prompt to a desktop IDE app via GUI automation.

    The GUI agent reads TASK.md content and sends it as a message.
    The watcher will detect the outbox file written by the IDE.
    Returns the thread (non-blocking).
    """
    message = "帮我完成 @.multi-agent/TASK.md 里的任务"

    def _run() -> None:
        try:
            success = send_gui_message(app_name, message)
            if success:
                logger.info("GUI agent %s (%s) message sent to %s", agent_id, role, app_name)
            else:
                logger.warning("GUI agent %s (%s) failed to send to %s, falling back to manual", agent_id, role, app_name)
        except Exception as e:
            logger.error("GUI agent %s error: %s", agent_id, e)

    t = threading.Thread(target=_run, daemon=True, name=f"gui-{agent_id}-{role}")
    t.start()
    return t


# ── Unified Dispatch (defect B1 fix) ─────────────────────

class DispatchResult:
    """Outcome of dispatch_agent() — replaces scattered if/else in callers."""
    __slots__ = ("message", "mode", "thread")

    def __init__(self, mode: str, thread: threading.Thread | None, message: str):
        self.mode = mode        # "auto" | "manual" | "degraded"
        self.thread = thread    # non-None only for "auto"
        self.message = message  # human-readable status line


def dispatch_agent(
    agent_id: str,
    role: str,
    *,
    timeout_sec: int = 600,
) -> DispatchResult:
    """Resolve driver for *agent_id* and either auto-execute or return
    manual-mode instructions.

    This is the **single call-site** that replaces the 4x duplicated
    if/else driver-check pattern previously in cli.py.

    Returns a ``DispatchResult`` so the caller only needs to display
    ``result.message`` — no driver-type branching required.
    """
    drv = get_agent_driver(agent_id)
    step_label = "Build" if role == "builder" else "Review"

    if drv["driver"] == "cli" and drv["command"]:
        if can_use_cli(drv["command"]):
            thread = spawn_cli_agent(agent_id, role, drv["command"], timeout_sec=timeout_sec)
            return DispatchResult(
                mode="auto",
                thread=thread,
                message=f"🤖 [{step_label}] 自动调用 {agent_id} CLI…",
            )
        # CLI configured but binary not installed → degrade gracefully
        binary = drv["command"].split()[0]
        return DispatchResult(
            mode="degraded",
            thread=None,
            message=(
                f"⚠️  {agent_id} 配置为 CLI 模式但 `{binary}` 未安装，降级为手动模式\n"
                f"📋 [{step_label}] 在 {agent_id} IDE 里对 AI 说:\n"
                f'   "帮我完成 @.multi-agent/TASK.md 里的任务"'
            ),
        )

    if drv["driver"] == "gui" and drv.get("app_name"):
        app_name = drv["app_name"]
        if can_use_gui():
            thread = spawn_gui_agent(agent_id, role, app_name)
            return DispatchResult(
                mode="auto",
                thread=thread,
                message=f"🖥️  [{step_label}] 自动向 {app_name} 发送任务…",
            )
        # macOS GUI not available → degrade gracefully
        return DispatchResult(
            mode="degraded",
            thread=None,
            message=(
                f"⚠️  {agent_id} 配置为 GUI 模式但 osascript 不可用，降级为手动模式\n"
                f"📋 [{step_label}] 在 {agent_id} IDE 里对 AI 说:\n"
                f'   "帮我完成 @.multi-agent/TASK.md 里的任务"'
            ),
        )

    # File-based (manual) driver
    return DispatchResult(
        mode="manual",
        thread=None,
        message=(
            f"📋 [{step_label}] 在 {agent_id} IDE 里对 AI 说:\n"
            f'   "帮我完成 @.multi-agent/TASK.md 里的任务"'
        ),
    )
