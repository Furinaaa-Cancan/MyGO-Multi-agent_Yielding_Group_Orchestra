"""Agent drivers — spawn CLI agents or show file-based instructions."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path

from multi_agent.config import workspace_dir, outbox_dir

logger = logging.getLogger(__name__)

# Task 10: concurrency lock — prevents duplicate CLI agent spawns
_cli_lock = threading.Lock()
_active_agents: dict[str, threading.Thread] = {}


def get_agent_driver(agent_id: str) -> dict:
    """Look up driver config for an agent from agents.yaml."""
    from multi_agent.router import load_agents

    for agent in load_agents():
        if agent.id == agent_id:
            return {"driver": agent.driver, "command": agent.command}
    return {"driver": "file", "command": ""}


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


def _stream_stdout(proc: subprocess.Popen, agent_id: str, role: str) -> str:
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


def _stream_stderr(proc: subprocess.Popen, agent_id: str, role: str) -> str:
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
    """
    # Task 10: concurrency protection
    lock_key = f"{agent_id}:{role}"
    with _cli_lock:
        existing = _active_agents.get(lock_key)
        if existing and existing.is_alive():
            logger.info("CLI agent %s already running as %s, returning existing thread", agent_id, role)
            return existing

    task_file = str(workspace_dir() / "TASK.md")
    outbox_file = str(outbox_dir() / f"{role}.json")

    cmd = command_template.format(
        task_file=task_file,
        outbox_file=outbox_file,
    )

    def _run():
        try:
            # Task 9: stream stderr in real-time instead of capture_output
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=project_dir or str(Path.cwd()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Read stdout in a thread to avoid deadlock when both
            # stdout and stderr pipes fill up (Python subprocess docs).
            stdout_lines: list[str] = []
            def _drain_stdout():
                if proc.stdout:
                    stdout_lines.append(proc.stdout.read())
            stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
            stdout_thread.start()
            stderr_text = _stream_stderr(proc, agent_id, role)
            stdout_thread.join(timeout=timeout_sec)
            stdout_text = stdout_lines[0] if stdout_lines else ""
            proc.wait(timeout=timeout_sec)

            # If the CLI tool didn't write the outbox file itself,
            # try to extract JSON from stdout and write it
            outbox_path = Path(outbox_file)
            if not outbox_path.exists() and stdout_text.strip():
                _try_extract_json(stdout_text, outbox_path)
            # If outbox still missing after extraction attempt → report error
            if not outbox_path.exists():
                stderr_hint = stderr_text.strip()[:200]
                if proc.returncode != 0:
                    _write_error(outbox_file, f"{agent_id} CLI exited with code {proc.returncode}: {stderr_hint}")
                else:
                    _write_error(outbox_file, f"{agent_id} CLI produced no parseable JSON output")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()  # reap zombie
            _write_error(outbox_file, f"{agent_id} CLI timed out after {timeout_sec}s")
        except Exception as e:
            _write_error(outbox_file, f"{agent_id} CLI error: {e}")
        finally:
            with _cli_lock:
                _active_agents.pop(lock_key, None)

    t = threading.Thread(target=_run, daemon=True, name=f"cli-{agent_id}-{role}")
    with _cli_lock:
        _active_agents[lock_key] = t
    t.start()
    return t


def _try_extract_json(text: str, outbox_path: Path) -> None:
    """Try to find and extract a JSON object from CLI output text."""
    # Look for JSON between ```json ... ``` markers
    match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                outbox_path.parent.mkdir(parents=True, exist_ok=True)
                with outbox_path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                return
        except json.JSONDecodeError:
            pass

    # Try parsing the whole output as JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            outbox_path.parent.mkdir(parents=True, exist_ok=True)
            with outbox_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
    except json.JSONDecodeError:
        pass


def _write_error(outbox_file: str, error_msg: str) -> None:
    """Write an error marker to outbox so the graph can detect failure."""
    path = Path(outbox_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"status": "error", "summary": error_msg}, f, indent=2)
        f.write("\n")
