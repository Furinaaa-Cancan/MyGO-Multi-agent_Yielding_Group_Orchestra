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
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from multi_agent.config import outbox_dir, subtask_outbox_dir, subtask_task_file, workspace_dir

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
            return {
                "driver": agent.driver,
                "command": agent.command,
                "app_name": agent.app_name,
                "auth_check": agent.auth_check,
                "login_hint": agent.login_hint,
                "required_env": list(agent.required_env),
            }
    return {
        "driver": "file",
        "command": "",
        "app_name": "",
        "auth_check": "",
        "login_hint": "",
        "required_env": [],
    }


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


# ── Intelligent Stderr Analysis ──────────────────────────

_KNOWN_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern_substring, short_id, user_message)
    (
        "UTF-8 encoding error",
        "utf8_path",
        "Codex CLI 的 WebSocket 因非 ASCII 路径编码失败，已降级到 HTTPS。\n"
        "   💡 建议: 将项目移到纯英文路径（如 /Users/you/snake-test）可避免此问题。",
    ),
    (
        "Reconnecting...",
        "ws_reconnect",
        "Codex CLI WebSocket 连接不稳定，正在重连。通常会自动恢复。",
    ),
    (
        "Falling back from WebSockets to HTTPS",
        "ws_fallback",
        "Codex CLI 已从 WebSocket 降级到 HTTPS 传输。功能正常但速度略慢。",
    ),
    (
        "invalid_token",
        "auth_token",
        "MCP 认证令牌无效。这通常是 Codex 内部 MCP 插件问题，不影响核心功能。",
    ),
    (
        "sysmond service not found",
        "sysmon",
        "macOS sysmon 服务未找到（沙盒限制）。不影响功能。",
    ),
    (
        "rate limit",
        "rate_limit",
        "API 速率限制。Codex 会自动重试，但任务可能变慢。\n"
        "   💡 建议: 减少并行任务数或等待一段时间再试。",
    ),
]


def analyze_agent_stderr(stderr_text: str, agent_id: str) -> list[str]:
    """Analyze CLI agent stderr for known error patterns.

    Returns a list of user-friendly diagnostic messages. Empty list = no issues.
    Deduplicates: each pattern reported at most once even if it appears many times.
    """
    if not stderr_text:
        return []

    diagnostics: list[str] = []
    seen: set[str] = set()

    for pattern, short_id, message in _KNOWN_PATTERNS:
        if short_id in seen:
            continue
        count = stderr_text.count(pattern)
        if count > 0:
            seen.add(short_id)
            prefix = f"[{agent_id}] " if agent_id else ""
            if count > 1:
                diagnostics.append(f"{prefix}⚠️  {message} (出现 {count} 次)")
            else:
                diagnostics.append(f"{prefix}⚠️  {message}")

    return diagnostics


def _log_stderr_diagnostics(stderr_text: str, agent_id: str) -> None:
    """Run intelligent stderr analysis and log any diagnostics."""
    for diag in analyze_agent_stderr(stderr_text, agent_id):
        logger.warning(diag)


def spawn_cli_agent(
    agent_id: str,
    role: str,
    command_template: str,
    project_dir: str | None = None,
    timeout_sec: int = 600,
    subtask_id: str | None = None,
) -> threading.Thread:
    """Spawn a CLI agent in a background thread.

    The CLI agent reads TASK.md and writes its output to outbox/{role}.json.
    The watcher will detect the outbox file and resume the graph.

    Returns the thread (for testing). Caller does NOT need to join it.
    If same agent+role is already running, returns the existing thread.

    When *subtask_id* is provided, the agent uses an isolated workspace
    under ``.multi-agent/subtasks/<subtask_id>/`` so multiple CLI agents
    can run in parallel without file conflicts.

    Security Note:
        Uses shell=False with shlex.split() for command execution, eliminating
        shell injection risk. The command_template comes from agents.yaml.
        Ensure agents.yaml has proper file permissions (0o644 or stricter).
    """
    # Task 10: concurrency protection — single lock scope to eliminate
    # check-then-act race between duplicate detection and thread registration.
    lock_key = f"{agent_id}:{role}" if not subtask_id else f"{agent_id}:{role}:{subtask_id}"

    if subtask_id:
        task_file = str(subtask_task_file(subtask_id))
        outbox_file = str(subtask_outbox_dir(subtask_id) / f"{role}.json")
    else:
        task_file = str(workspace_dir() / "TASK.md")
        outbox_file = str(outbox_dir() / f"{role}.json")

    # D1+C3: Build command list with shell=False to eliminate injection risk.
    # Paths are inserted literally (no quoting needed without shell).
    resolved_project_root = project_dir or str(Path.cwd())
    cmd_str = command_template.format(
        task_file=task_file,
        outbox_file=outbox_file,
        project_root=resolved_project_root,
    )
    cmd_list = shlex.split(cmd_str)

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                cmd_list,
                shell=False,
                cwd=resolved_project_root,
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
            _log_stderr_diagnostics(stderr_text, agent_id)
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


# ── Visible Terminal Management ───────────────────────────
# Terminal pool — persistent windows reused across subtasks/groups.
# Each slot gets ONE terminal window. The wrapper script loops,
# watching for trigger files written by dispatch_agent.

_terminal_counter: int = 0
_terminal_counter_lock = threading.Lock()
_open_terminals: dict[str, str] = {}  # key → wrapper_path


def _terminal_key(agent_id: str, role: str, subtask_id: str | None, terminal_slot: int | None = None) -> str:
    if terminal_slot is not None:
        return f"slot:{terminal_slot}"
    return subtask_id or f"{agent_id}:{role}"


def _trigger_dir(subtask_id: str | None, terminal_slot: int | None = None) -> Path:
    """Dir where trigger/done files live for a visible terminal."""
    if terminal_slot is not None:
        d = workspace_dir() / "terminals" / f"slot-{terminal_slot}"
        d.mkdir(parents=True, exist_ok=True)
        return d
    if subtask_id:
        return subtask_outbox_dir(subtask_id).parent
    return workspace_dir()


# ── Cross-platform Terminal Opener ────────────────────────

def _detect_terminal_emulator() -> tuple[str, list[str]] | None:
    """Detect available terminal emulator on the current platform.

    Returns (name, command_prefix) or None if no terminal found.
    The command_prefix is a list that, when combined with the script path,
    opens a new terminal window running the script.
    """
    plat = sys.platform

    if plat == "darwin":
        if shutil.which("osascript"):
            return ("Terminal.app", ["osascript"])
        return None

    if plat == "win32":
        # Windows Terminal (modern)
        if shutil.which("wt.exe"):
            return ("Windows Terminal", ["wt.exe", "new-tab", "--title"])
        # Classic cmd
        if shutil.which("cmd.exe"):
            return ("cmd.exe", ["cmd.exe", "/c", "start"])
        return None

    # Linux / FreeBSD / other Unix
    for name, cmd_prefix in [
        ("gnome-terminal", ["gnome-terminal", "--title"]),
        ("konsole",        ["konsole", "-p", "tabtitle="]),
        ("xfce4-terminal", ["xfce4-terminal", "--title"]),
        ("xterm",          ["xterm", "-T"]),
    ]:
        if shutil.which(name):
            return (name, cmd_prefix)
    return None


def _open_terminal_window(script_path: str, label: str) -> bool:
    """Open a new terminal window running the given script. Cross-platform.

    Returns True on success, False on failure.
    Supports: macOS (Terminal.app), Windows (wt.exe/cmd.exe),
    Linux (gnome-terminal/konsole/xfce4-terminal/xterm).
    """
    detected = _detect_terminal_emulator()
    if not detected:
        logger.warning("No terminal emulator detected on %s", sys.platform)
        return False

    name, _ = detected

    try:
        if sys.platform == "darwin":
            # macOS: use AppleScript to open Terminal.app
            safe_path = script_path.replace("\\", "\\\\").replace('"', '\\"')
            applescript = f'''
tell application "Terminal"
    activate
    do script "{safe_path}"
end tell
'''
            subprocess.run(["osascript", "-e", applescript], capture_output=True, timeout=10)
            return True

        if sys.platform == "win32":
            # Windows: create a .bat wrapper that calls bash/WSL
            bat_path = script_path + ".bat"
            # Sanitize label for .bat title command (strip shell metacharacters)
            import re
            safe_label = re.sub(r'[&|<>^()!%"]', "", label)[:60] or "MyGO"
            bash_bin = shutil.which("bash")
            if bash_bin:
                # Git Bash or WSL available — run bash script through it
                Path(bat_path).write_text(
                    f'@echo off\ntitle {safe_label}\n"{bash_bin}" "{script_path}"\n',
                    encoding="utf-8",
                )
            else:
                # No bash — unlikely but write a stub
                Path(bat_path).write_text(
                    f'@echo off\ntitle {safe_label}\necho No bash found. Install Git for Windows or WSL.\npause\n',
                    encoding="utf-8",
                )
            if name == "Windows Terminal":
                subprocess.run(
                    ["wt.exe", "new-tab", "--title", safe_label, "cmd", "/c", bat_path],
                    capture_output=True, timeout=10,
                )
            else:
                # start requires first quoted arg as window title
                subprocess.run(
                    ["cmd.exe", "/c", "start", f'"{safe_label}"', bat_path],
                    capture_output=True, timeout=10,
                )
            return True

        # Linux: use detected terminal emulator
        if name == "gnome-terminal":
            subprocess.Popen(
                ["gnome-terminal", "--title", label, "--", "bash", script_path],
                start_new_session=True,
            )
        elif name == "konsole":
            subprocess.Popen(
                ["konsole", "-p", f"tabtitle={label}", "-e", "bash", script_path],
                start_new_session=True,
            )
        elif name == "xfce4-terminal":
            subprocess.Popen(
                ["xfce4-terminal", "--title", label, "-e", f"bash {script_path}"],
                start_new_session=True,
            )
        elif name == "xterm":
            subprocess.Popen(
                ["xterm", "-T", label, "-e", "bash", script_path],
                start_new_session=True,
            )
        else:
            return False
        return True

    except Exception as e:
        logger.error("Failed to open %s terminal: %s", name, e)
        return False


def dispatch_visible(
    agent_id: str,
    role: str,
    command_template: str,
    project_dir: str | None = None,
    timeout_sec: int = 600,
    subtask_id: str | None = None,
    terminal_slot: int | None = None,
) -> None:
    """Dispatch a CLI agent visibly. Opens a terminal only on first call per slot.

    When *terminal_slot* is provided, the terminal is keyed by slot number
    and reused across different subtasks/groups — no new windows.
    Subsequent calls for the same slot write a trigger file that the
    already-open terminal picks up and executes.
    """
    from multi_agent.config import get_agent_name

    key = _terminal_key(agent_id, role, subtask_id, terminal_slot)
    tdir = _trigger_dir(subtask_id, terminal_slot)
    tdir.mkdir(parents=True, exist_ok=True)

    # Build the actual CLI command
    if subtask_id:
        task_file = str(subtask_task_file(subtask_id))
        outbox_file = str(subtask_outbox_dir(subtask_id) / f"{role}.json")
    else:
        task_file = str(workspace_dir() / "TASK.md")
        outbox_file = str(outbox_dir() / f"{role}.json")
    resolved_project_root = project_dir or str(Path.cwd())
    cmd_str = command_template.format(
        task_file=task_file,
        outbox_file=outbox_file,
        project_root=resolved_project_root,
    )

    # Remove stale .done file from previous close — prevents new wrapper from
    # exiting immediately when a slot is reused after close_all_visible_terminals.
    done_file = tdir / ".done"
    with contextlib.suppress(OSError):
        done_file.unlink(missing_ok=True)

    # Write trigger file atomically (tempfile + rename) — wrapper might read
    # partial content if we use plain write_text due to a race condition.
    trigger = tdir / ".trigger"
    fd_trig, tmp_trig = tempfile.mkstemp(dir=str(tdir), suffix=".trigger.tmp")
    try:
        with os.fdopen(fd_trig, "w", encoding="utf-8") as f_trig:
            f_trig.write(f"{outbox_file}\n{cmd_str}")
        Path(tmp_trig).replace(trigger)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_trig).unlink()
        raise

    # If terminal already open for this key, just return — wrapper will see trigger
    if key in _open_terminals:
        logger.info("Trigger written for existing terminal %s", key)
        return

    # First time: create wrapper script + open terminal
    cwd = project_dir or str(Path.cwd())
    with _terminal_counter_lock:
        global _terminal_counter
        persona = get_agent_name(_terminal_counter)
        _terminal_counter += 1
    label = f"{persona} · {subtask_id}" if subtask_id else f"{persona} · {agent_id}/{role}"

    fd, wrapper_path = tempfile.mkstemp(
        suffix=".sh", prefix=f"mygo-{key.replace(':', '-')}-",
        dir=tempfile.gettempdir(),
    )
    trigger_path = shlex.quote(str(trigger))
    done_path = shlex.quote(str(tdir / ".done"))
    fence = "```"

    lines = [
        "#!/bin/bash",
        f"printf '\\033]0;\\xf0\\x9f\\xa4\\x96 {label}\\007'",
        'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        f'echo "🤖 MyGO Agent: {label}"',
        f'echo "📂 {cwd}"',
        'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        f"cd {shlex.quote(cwd)}",
        "",
        "# Main loop — wait for trigger, run command, repeat",
        f"TRIGGER={trigger_path}",
        f"DONE={done_path}",
        "",
        "while true; do",
        '    # Wait for trigger file or done signal',
        '    echo "⏸️  等待下一个任务分配…"',
        '    while [ ! -f "$TRIGGER" ] && [ ! -f "$DONE" ]; do sleep 0.5; done',
        '    [ -f "$DONE" ] && break',
        "",
        '    # Read trigger: line1=outbox path, line2+=command',
        '    OUTBOX=$(head -1 "$TRIGGER")',
        '    CMD=$(tail -n +2 "$TRIGGER")',
        '    rm -f "$TRIGGER"',
        '    mkdir -p "$(dirname "$OUTBOX")"',
        '    rm -f "$OUTBOX"',
        '    echo ""',
        '    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        '    echo "🚀 执行: $CMD"',
        '    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        "",
        "    TMPLOG=$(mktemp /tmp/mygo-output-XXXXXX)",
        "    # Write CMD to a temp script instead of eval to avoid shell injection",
        "    CMDSCRIPT=$(mktemp /tmp/mygo-cmd-XXXXXX)",
        '    echo "$CMD" > "$CMDSCRIPT"',
        '    chmod +x "$CMDSCRIPT"',
        '    bash "$CMDSCRIPT" 2>&1 | tee "$TMPLOG"',
        "    EXIT_CODE=${PIPESTATUS[0]}",
        '    rm -f "$CMDSCRIPT"',
        "",
        '    # If codex wrote the outbox file directly, we are done',
        '    if [ -f "$OUTBOX" ]; then',
        '        rm -f "$TMPLOG"',
        "    else",
        '        # Extract JSON from stdout or write a completion stub',
        '        export OUTBOX TMPLOG EXIT_CODE',
        "        python3 << 'PYEOF'",
        "import json, re, sys, os, subprocess",
        "outbox = os.environ.get('OUTBOX', '')",
        "tmplog = os.environ.get('TMPLOG', '')",
        "exit_code = int(os.environ.get('EXIT_CODE', '1'))",
        "if outbox: os.makedirs(os.path.dirname(outbox), exist_ok=True)",
        "text = ''",
        "try: text = open(tmplog).read()",
        "except (OSError, IOError): pass",
        f"m = re.search(r'{fence}json\\s*\\n(.*?)\\n\\s*{fence}', text, re.DOTALL)",
        "if m:",
        "    try:",
        "        d = json.loads(m.group(1))",
        "        if isinstance(d, dict):",
        "            json.dump(d, open(outbox,'w'), ensure_ascii=False, indent=2); sys.exit(0)",
        "    except (json.JSONDecodeError, ValueError, KeyError): pass",
        "for line in reversed(text.splitlines()):",
        "    line = line.strip()",
        "    if line.startswith('{'):",
        "        try:",
        "            d = json.loads(line)",
        "            if isinstance(d, dict):",
        "                json.dump(d, open(outbox,'w'), ensure_ascii=False, indent=2); sys.exit(0)",
        "        except (json.JSONDecodeError, ValueError): pass",
        "# No JSON found — build a completion stub from changed files",
        "changed = []",
        "try:",
        "    r = subprocess.run(['git','diff','--name-only','HEAD'], capture_output=True, text=True, timeout=5)",
        "    if r.returncode == 0: changed = [f for f in r.stdout.strip().splitlines() if f]",
        "except Exception: pass",
        "if not changed:",
        "    try:",
        "        r = subprocess.run(['git','ls-files','--others','--exclude-standard'], capture_output=True, text=True, timeout=5)",
        "        if r.returncode == 0: changed = [f for f in r.stdout.strip().splitlines() if f]",
        "    except Exception: pass",
        "status = 'completed' if (exit_code == 0 or changed) else 'error'",
        "summary = f'codex exited {exit_code}, {len(changed)} changed files' if not changed else ', '.join(changed[:10])",
        "json.dump({'status': status, 'summary': summary, 'changed_files': changed},",
        "          open(outbox,'w'), ensure_ascii=False, indent=2)",
        "PYEOF",
        '        rm -f "$TMPLOG"',
        "    fi",
        "",
        '    if [ -f "$OUTBOX" ]; then',
        '        echo "✅ 完成！等待主进程调度下一步…"',
        "    else",
        '        echo "❌ 未能提取 JSON"',
        "    fi",
        "done",
        "",
        'echo ""',
        'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        f'echo "🏁 {label} 任务完成，终端关闭"',
        'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"',
        'rm -f "$DONE"',
        "sleep 2",
        "exit 0",
    ]
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    Path(wrapper_path).chmod(0o755)

    ok = _open_terminal_window(wrapper_path, label)
    if ok:
        _open_terminals[key] = wrapper_path
        logger.info("Opened terminal for %s", key)
    else:
        logger.error("Failed to open terminal for %s, falling back to headless CLI", key)
        spawn_cli_agent(agent_id, role, command_template, project_dir, timeout_sec, subtask_id)


def close_visible_terminal(subtask_id: str | None = None, agent_id: str = "", role: str = "", terminal_slot: int | None = None) -> None:
    """Signal a visible terminal to exit by writing a .done file."""
    key = _terminal_key(agent_id, role, subtask_id, terminal_slot)
    tdir = _trigger_dir(subtask_id, terminal_slot)
    done = tdir / ".done"
    done.write_text("done", encoding="utf-8")
    _open_terminals.pop(key, None)


def close_all_visible_terminals() -> None:
    """Close all open visible terminals (called at end of decompose run)."""
    for key in list(_open_terminals.keys()):
        with contextlib.suppress(OSError):
            if key.startswith("slot:"):
                slot = int(key.split(":")[1])
                tdir = _trigger_dir(None, terminal_slot=slot)
            elif ":" in key:
                # Legacy non-slot key format "agent:role" maps to workspace root.
                # Treating it as subtask_id would fail validation because ':' is invalid.
                tdir = _trigger_dir(None)
            else:
                # Legacy key is a subtask_id. Subtask workspace may already be
                # cleaned up — suppress errors.
                tdir = _trigger_dir(key)
            done = tdir / ".done"
            done.parent.mkdir(parents=True, exist_ok=True)
            done.write_text("done", encoding="utf-8")
    _open_terminals.clear()


def _ensure_outbox_written(
    outbox_file: str, stdout_text: str, stderr_text: str,
    agent_id: str, returncode: int | None,
) -> None:
    """Ensure outbox file exists after CLI run; extract from stdout or write error.

    After ensuring the outbox file exists, sends a POST /notify to the local
    watcher (OpenClaw-inspired event-driven notification) for immediate resume
    instead of waiting for the next poll cycle.
    """
    outbox_path = Path(outbox_file)
    if not outbox_path.exists() and stdout_text.strip():
        _try_extract_json(stdout_text, outbox_path)
    if not outbox_path.exists():
        stderr_hint = stderr_text.strip()[:200]
        if returncode != 0:
            _write_error(outbox_file, f"{agent_id} CLI exited with code {returncode}: {stderr_hint}")
        else:
            _write_error(outbox_file, f"{agent_id} CLI produced no parseable JSON output")

    # Notify watcher for immediate resume (event-driven, OpenClaw pattern)
    if outbox_path.exists():
        try:
            from multi_agent.watcher import notify_watcher
            notify_watcher()
        except Exception:
            pass  # Non-critical — watcher will poll anyway


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


def _extract_fenced_json(text: str) -> list[dict[str, Any]]:
    """Extract JSON dicts from fenced code blocks (```json ... ```)."""
    results: list[dict[str, Any]] = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                results.append(data)
        except json.JSONDecodeError:
            continue
    return results


def _extract_bare_json(text: str) -> list[dict[str, Any]]:
    """Extract bare JSON objects { ... } embedded in text."""
    results: list[dict[str, Any]] = []
    for match in re.finditer(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and len(data) >= 2:
                results.append(data)
        except json.JSONDecodeError:
            continue
    return results


def _try_extract_json(text: str, outbox_path: Path) -> None:
    """Try to find and extract a JSON object from CLI output text.

    Handles multiple output patterns from Claude CLI, Codex, and other agents:
    1. Fenced code blocks: ```json ... ``` or ``` ... ```
    2. Bare JSON objects in text
    3. Pure JSON output
    Picks the best candidate (prefers one with 'status' field).
    """
    candidates = _extract_fenced_json(text)
    if not candidates:
        candidates = _extract_bare_json(text)
    if not candidates:
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict):
                candidates.append(data)
        except json.JSONDecodeError:
            pass

    if not candidates:
        return

    best = next((c for c in candidates if "status" in c), None)
    if not best:
        best = max(candidates, key=lambda c: len(c))
    _atomic_write_json(outbox_path, best)


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

    # Escape AppleScript strings to prevent injection via app_name.
    # Must escape: backslash, double-quote, newline, and dollar sign
    # (AppleScript variable interpolation in some contexts).
    safe_app = (app_name
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "")
                .replace("\r", "")
                .replace("$", "")
                .replace("`", ""))
    applescript = f'''
tell application "{safe_app}" to activate
delay 1.0
tell application "System Events"
    tell process "{safe_app}"
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
    *,
    subtask_id: str | None = None,
) -> threading.Thread:
    """Send task prompt to a desktop IDE app via GUI automation.

    The GUI agent reads TASK.md content and sends it as a message.
    The watcher will detect the outbox file written by the IDE.
    Returns the thread (non-blocking).
    """
    # Keep message SHORT — IDE chat input can't handle large paste.
    # Use @-relative file reference; works because my go runs from the target project.
    if subtask_id:
        task_rel = f".multi-agent/subtasks/{subtask_id}/TASK.md"
        outbox_rel = f".multi-agent/subtasks/{subtask_id}/outbox/{role}.json"
    else:
        task_rel = ".multi-agent/TASK.md"
        outbox_rel = f".multi-agent/outbox/{role}.json"
    message = (
        f"帮我完成 @{task_rel} 里的任务，"
        f"完成后将 JSON 输出保存到 @{outbox_rel}"
    )

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
    subtask_id: str | None = None,
    visible: bool = False,
    project_dir: str | None = None,
    terminal_slot: int | None = None,
) -> DispatchResult:
    """Resolve driver for *agent_id* and either auto-execute or return
    manual-mode instructions.

    This is the **single call-site** that replaces the 4x duplicated
    if/else driver-check pattern previously in cli.py.

    When *subtask_id* is provided, CLI agents use an isolated workspace
    under ``.multi-agent/subtasks/<subtask_id>/`` for parallel execution.

    When *visible* is True, CLI agents open in new Terminal.app windows
    so the user can watch them work in real-time.

    Returns a ``DispatchResult`` so the caller only needs to display
    ``result.message`` — no driver-type branching required.
    """
    drv = get_agent_driver(agent_id)
    step_label = "Build" if role == "builder" else "Review"
    if subtask_id:
        task_rel = f".multi-agent/subtasks/{subtask_id}/TASK.md"
        outbox_rel = f".multi-agent/subtasks/{subtask_id}/outbox/{role}.json"
    else:
        task_rel = ".multi-agent/TASK.md"
        outbox_rel = f".multi-agent/outbox/{role}.json"
    manual_instruction = (
        f'   "帮我完成 @{task_rel} 里的任务，完成后将 JSON 输出保存到 @{outbox_rel}"'
    )

    if drv["driver"] == "cli" and drv["command"]:
        if can_use_cli(drv["command"]):
            if visible:
                dispatch_visible(agent_id, role, drv["command"], timeout_sec=timeout_sec, subtask_id=subtask_id, project_dir=project_dir, terminal_slot=terminal_slot)
                return DispatchResult(
                    mode="auto",
                    thread=None,
                    message=f"🖥️  [{step_label}] {agent_id} CLI 已触发",
                )
            thread = spawn_cli_agent(agent_id, role, drv["command"], timeout_sec=timeout_sec, subtask_id=subtask_id, project_dir=project_dir)
            return DispatchResult(
                mode="auto",
                thread=thread,
                message=f"🤖 [{step_label}] 自动调用 {agent_id} CLI…",
            )
        # CLI configured but binary not installed → degrade gracefully
        binary = drv["command"].split()[0]
        login_hint = str(drv.get("login_hint", "")).strip()
        hint_line = f"\n💡 登录提示: {login_hint}" if login_hint else ""
        return DispatchResult(
            mode="degraded",
            thread=None,
            message=(
                f"⚠️  {agent_id} 配置为 CLI 模式但 `{binary}` 未安装，降级为手动模式\n"
                f"📋 [{step_label}] 在 {agent_id} IDE 里对 AI 说:\n"
                f"{manual_instruction}\n"
                f"🔎 可先运行: my auth doctor --agent {agent_id}{hint_line}"
            ),
        )

    if drv["driver"] == "gui" and drv.get("app_name"):
        app_name = drv["app_name"]
        if can_use_gui():
            thread = spawn_gui_agent(agent_id, role, app_name, subtask_id=subtask_id)
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
                f"{manual_instruction}"
            ),
        )

    # File-based (manual) driver
    return DispatchResult(
        mode="manual",
        thread=None,
        message=(
            f"📋 [{step_label}] 在 {agent_id} IDE 里对 AI 说:\n"
            f"{manual_instruction}"
        ),
    )
