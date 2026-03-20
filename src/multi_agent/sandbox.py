"""Sandbox — lightweight execution isolation for verification commands.

Provides subprocess-based isolation with resource limits for running
tests and linters safely. Inspired by OpenHands (arXiv 2024) Docker
sandboxing but adapted for local IDE-first workflows.

The sandbox strips sensitive environment variables, enforces timeouts,
and truncates output to prevent memory issues from verbose test suites.

Design decisions:
- Subprocess backend is the default for zero-setup local usage.
- Docker backend is stubbed for future container-based isolation.
- Output truncation is applied symmetrically to stdout and stderr
  to keep verification results manageable in LLM context windows.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


# ── Constants ────────────────────────────────────────────

DEFAULT_OUTPUT_MAX_CHARS = 4000

# Environment variables stripped from subprocess env to avoid leaking
# secrets into test/lint processes.
_SENSITIVE_ENV_PREFIXES = (
    "AWS_SECRET",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SECRET_",
    "TOKEN_",
    "PASSWORD",
    "PRIVATE_KEY",
)


# ── Data Classes ─────────────────────────────────────────

@dataclass(frozen=True)
class SandboxResult:
    """Outcome of a sandboxed command execution.

    Attributes:
        stdout: Captured standard output (truncated to max_output_chars).
        stderr: Captured standard error (truncated to max_output_chars).
        returncode: Process exit code, or -1 if timed out.
        timed_out: True if the process exceeded the timeout.
        duration_sec: Wall-clock execution time in seconds.
    """

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration_sec: float


# ── Helpers ──────────────────────────────────────────────

def _sanitize_env(
    base_env: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a sanitized environment dict, stripping sensitive variables.

    Args:
        base_env: Starting environment. Defaults to ``os.environ``.
        extra_env: Additional variables to merge (after sanitization).

    Returns:
        A new dict safe for subprocess use.
    """
    env = dict(base_env or os.environ)
    # Strip sensitive keys
    keys_to_remove = [
        k for k in env
        if any(k.upper().startswith(p) for p in _SENSITIVE_ENV_PREFIXES)
    ]
    for k in keys_to_remove:
        del env[k]
    # Merge extras (these are intentional, so not filtered)
    if extra_env:
        env.update(extra_env)
    return env


def _truncate(text: str, max_chars: int) -> str:
    """Truncate *text* to *max_chars*, appending a notice if truncated."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [truncated {len(text) - max_chars} chars] ...\n\n"
        + text[-half:]
    )


# ── Sandbox Runner ───────────────────────────────────────

class SandboxRunner:
    """Execute commands in an isolated subprocess with resource limits.

    Args:
        max_output_chars: Maximum characters to keep from stdout/stderr.
            Output exceeding this limit is truncated symmetrically
            (keeping head and tail).
        default_timeout_sec: Default timeout if not specified per-call.

    Example::

        runner = SandboxRunner()
        result = runner.run("pytest -x --tb=short", cwd="/path/to/repo")
        if result.timed_out:
            print("Tests timed out")
        elif result.returncode == 0:
            print("All tests passed")
    """

    def __init__(
        self,
        max_output_chars: int = DEFAULT_OUTPUT_MAX_CHARS,
        default_timeout_sec: float = 120.0,
    ) -> None:
        self.max_output_chars = max_output_chars
        self.default_timeout_sec = default_timeout_sec

    def run(
        self,
        command: str | list[str],
        cwd: str | None = None,
        timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
        restrict_network: bool = False,
    ) -> SandboxResult:
        """Run *command* in a subprocess sandbox.

        Args:
            command: Shell command string or arg list.
            cwd: Working directory for the process.
            timeout_sec: Per-call timeout override.
            env: Extra environment variables to inject.
            restrict_network: If True, hint that network should be
                restricted. Currently a no-op for subprocess backend;
                Docker backend will implement this.

        Returns:
            A :class:`SandboxResult` with captured output.
        """
        timeout = timeout_sec if timeout_sec is not None else self.default_timeout_sec
        safe_env = _sanitize_env(extra_env=env)

        # Parse command
        if isinstance(command, str):
            cmd_args = shlex.split(command)
        else:
            cmd_args = list(command)

        t0 = time.monotonic()
        timed_out = False

        try:
            proc = subprocess.run(
                cmd_args,
                cwd=cwd,
                env=safe_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = time.monotonic() - t0
            stdout = _truncate(proc.stdout or "", self.max_output_chars)
            stderr = _truncate(proc.stderr or "", self.max_output_chars)
            return SandboxResult(
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
                timed_out=False,
                duration_sec=round(duration, 2),
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - t0
            timed_out = True
            stdout = _truncate(
                (exc.stdout or b"").decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                self.max_output_chars,
            )
            stderr = _truncate(
                (exc.stderr or b"").decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
                self.max_output_chars,
            )
            return SandboxResult(
                stdout=stdout,
                stderr=stderr,
                returncode=-1,
                timed_out=True,
                duration_sec=round(duration, 2),
            )
        except FileNotFoundError:
            duration = time.monotonic() - t0
            return SandboxResult(
                stdout="",
                stderr=f"Command not found: {cmd_args[0]!r}",
                returncode=127,
                timed_out=False,
                duration_sec=round(duration, 2),
            )
        except OSError as exc:
            duration = time.monotonic() - t0
            return SandboxResult(
                stdout="",
                stderr=f"OS error running command: {exc}",
                returncode=126,
                timed_out=False,
                duration_sec=round(duration, 2),
            )


# ── Docker Backend Stub ──────────────────────────────────

class DockerSandboxRunner:
    """Placeholder for future Docker-based sandbox.

    Will provide stronger isolation via containerised execution with
    network restrictions, filesystem snapshots, and memory limits.

    Not yet implemented — instantiating raises ``NotImplementedError``.
    """

    def __init__(self, image: str = "python:3.12-slim", **kwargs: Any) -> None:
        raise NotImplementedError(
            "DockerSandboxRunner is not yet implemented. "
            "Use SandboxRunner (subprocess backend) for now."
        )
