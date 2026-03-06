"""CLI entry point — my go / my done / my status / my cancel / my watch."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import re
import signal
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from multi_agent._utils import (
    SAFE_TASK_ID_RE as _SAFE_TASK_ID_RE,
)
from multi_agent._utils import (
    is_terminal_final_status as _is_terminal_final_status,
)
from multi_agent.workspace import (
    acquire_lock,
    clear_runtime,
    ensure_workspace,
    read_lock,
    read_outbox,
    release_lock,
    save_task_yaml,
    validate_outbox_data,
)

_F = TypeVar("_F", bound=Callable[..., Any])

log = logging.getLogger(__name__)


def handle_errors(f: _F) -> _F:
    """Unified exception handler for CLI commands.

    - Shows user-friendly error messages by default.
    - Shows full traceback when --verbose is set.
    - Does not mutate lock state implicitly on errors.
    - Logs error to .multi-agent/logs/ directory.
    """
    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return f(*args, **kwargs)
        except SystemExit:
            raise
        except KeyboardInterrupt:
            click.echo("\n⏹️  操作已取消")
            raise SystemExit(0) from None
        except click.exceptions.Exit:
            raise
        except Exception as e:
            ctx = click.get_current_context(silent=True)
            verbose = (ctx and ctx.find_root().params.get("verbose")) if ctx else False

            click.echo(f"❌ 错误: {e}", err=True)

            if verbose:
                click.echo(traceback.format_exc(), err=True)

            # Log error to file
            _log_error_to_file(f.__name__, e)

            raise SystemExit(1) from None
    return wrapper  # type: ignore[return-value]  # functools.wraps preserves signature


def _log_error_to_file(command: str, error: Exception) -> None:
    """Write error details to .multi-agent/logs/."""
    try:
        from datetime import UTC, datetime

        from multi_agent.config import workspace_dir
        logs_dir = workspace_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        log_file = logs_dir / f"error-{ts}.log"
        log_file.write_text(
            f"command: {command}\nerror: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass



def _make_config(task_id: str) -> dict[str, Any]:
    from multi_agent.orchestrator import make_config
    return make_config(task_id)


_SAFE_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validate_task_id(task_id: str) -> str:
    """Validate task_id to prevent path traversal attacks.

    Delegates to ``_utils.validate_task_id`` and re-raises as
    ``click.BadParameter`` for CLI context.
    """
    from multi_agent._utils import validate_task_id as _validate_core
    try:
        _validate_core(task_id)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--task-id") from exc
    return task_id


def _validate_skill_id(skill_id: str) -> str:
    """Validate skill_id to prevent path traversal via --skill."""
    if not _SAFE_SKILL_ID_RE.match(skill_id):
        raise click.BadParameter(
            f"Invalid skill_id: {skill_id!r}. "
            f"Must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}.",
            param_hint="--skill",
        )
    return skill_id


def _generate_task_id(requirement: str) -> str:
    content = f"{requirement}-{time.time()}"
    h = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"task-{h}"


# _is_terminal_final_status, _positive_int, _count_nonempty_entries
# imported from multi_agent._utils


from multi_agent.cli_watch import (  # noqa: E402 — re-export for mock compatibility
    _normalize_resume_output,
    _run_watch_loop,
    _show_waiting,
)


def _is_task_terminal_or_missing(app: Any, task_id: str) -> bool:
    """Return True if a locked task is already terminal or has no graph state."""
    try:
        snapshot = app.get_state(_make_config(task_id))
    except Exception:
        return False

    if not snapshot:
        # No graph state but lock exists -> stale lock.
        return True

    vals = snapshot.values or {}
    final = vals.get("final_status")
    if _is_terminal_final_status(final):
        return True

    return not snapshot.next


def _mark_task_inactive(task_id: str, *, status: str, reason: str) -> bool:
    """Update task YAML status so it is no longer treated as active."""
    import yaml

    from multi_agent.config import tasks_dir

    path = tasks_dir() / f"{task_id}.yaml"
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return False
        data["task_id"] = task_id
        data["status"] = status
        data["reason"] = reason
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return True
    except Exception:
        return False


def _sigterm_handler(signum: int, frame: Any) -> None:
    """Graceful SIGTERM handler — release lock and clean runtime."""
    try:
        if read_lock():
            release_lock()
        clear_runtime()
    except Exception:
        pass
    click.echo("\n⏹️  收到终止信号，已清理资源", err=True)
    raise SystemExit(128 + signum)


@click.group()
@click.option("--verbose", is_flag=True, default=False, help="Show full traceback on errors")
def main(verbose: bool) -> None:
    """my — MyGO (Multi-agent Yielding Group Orchestra). 你的 AI 乐队，一条命令开演."""
    signal.signal(signal.SIGTERM, _sigterm_handler)


@main.group()
def session() -> None:
    """IDE-first 会话命令族（LangGraph 单入口）."""


@session.command("start")
@click.option("--task", "task_file", required=True, type=click.Path(exists=True), help="Task JSON 路径")
@click.option("--mode", default="strict", help="Workmode profile 名称")
@click.option("--config", "config_path", default="config/workmode.yaml", help="Workmode 配置路径")
@click.option("--reset", is_flag=True, default=False, help="重置同 task_id 的历史 checkpoint 后再启动")
@handle_errors
def session_start(task_file: str, mode: str, config_path: str, reset: bool) -> None:
    """启动 IDE 会话并生成各 agent 的提示词文件."""
    from multi_agent.session import start_session

    payload = start_session(task_file, mode=mode, config_path=config_path, reset=reset)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@session.command("status")
@click.option("--task-id", required=True, help="Task ID")
@handle_errors
def session_status_cmd(task_id: str) -> None:
    """查看会话状态（owner、角色、状态、提示词路径）."""
    from multi_agent.session import session_status

    _validate_task_id(task_id)
    payload = session_status(task_id)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@session.command("pull")
@click.option("--task-id", required=True, help="Task ID")
@click.option("--agent", required=True, help="Agent ID")
@click.option("--out", default=None, type=click.Path(), help="提示词输出文件路径（默认 prompts/current-<agent>.txt）")
@click.option("--json-meta", "json_meta", is_flag=True, default=False, help="输出元信息 JSON 而不是提示词正文")
@handle_errors
def session_pull_cmd(task_id: str, agent: str, out: str | None, json_meta: bool) -> None:
    """拉取某个 agent 当前提示词（纯 IDE 文本，无终端命令）."""
    from multi_agent.session import session_pull

    _validate_task_id(task_id)
    payload = session_pull(task_id, agent, out=out)
    if json_meta:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    prompt_text = Path(payload["prompt_path"]).read_text(encoding="utf-8")
    click.echo(prompt_text.rstrip("\n"))


@session.command("push")
@click.option("--task-id", required=True, help="Task ID")
@click.option("--agent", required=True, help="Agent ID")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True), help="agent 输出文件（JSON 或包含 JSON 代码块）")
@handle_errors
def session_push_cmd(task_id: str, agent: str, file_path: str) -> None:
    """提交 agent 输出并自动推进到下一角色或终态."""
    from multi_agent.session import session_push

    _validate_task_id(task_id)
    payload = session_push(task_id, agent, file_path)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _apply_project_defaults(
    proj: dict[str, Any],
    builder: str, reviewer: str, timeout: int, retry_budget: int,
    mode: str, mode_config_path: str,
) -> tuple[str, str, int, int, str, str]:
    """Apply .ma.yaml project config defaults (CLI flags override)."""
    if proj:
        from multi_agent.config import validate_config
        config_warnings = validate_config(proj)
        for cw in config_warnings:
            click.echo(f"⚠️  .ma.yaml: {cw}", err=True)
    if not builder and proj.get("default_builder"):
        builder = proj["default_builder"]
    if not reviewer and proj.get("default_reviewer"):
        reviewer = proj["default_reviewer"]
    if timeout == 1800 and proj.get("default_timeout"):
        timeout = proj["default_timeout"]
    if retry_budget == 2 and proj.get("default_retry_budget"):
        retry_budget = proj["default_retry_budget"]
    if mode == "strict" and isinstance(proj.get("default_workflow_mode"), str):
        mode = str(proj["default_workflow_mode"]).strip() or mode
    if mode_config_path == "config/workmode.yaml" and isinstance(proj.get("workmode_config"), str):
        mode_config_path = str(proj["workmode_config"]).strip() or mode_config_path
    return builder, reviewer, timeout, retry_budget, mode, mode_config_path


def _ensure_no_active_task(app: Any) -> None:
    """Enforce single active task — exit if one is already running."""
    locked = read_lock()
    active_task = _detect_active_task(app)
    if locked:
        if _is_task_terminal_or_missing(app, locked):
            release_lock()
            clear_runtime()
            click.echo(f"🧹 检测到陈旧锁 '{locked}'，已自动清理。")
        else:
            click.echo(f"❌ 任务 '{locked}' 正在进行中。", err=True)
            click.echo("   先完成或取消当前任务:", err=True)
            click.echo("   • my cancel   — 取消当前任务", err=True)
            click.echo("   • my done     — 手动提交结果", err=True)
            click.echo("   • my status   — 查看任务状态", err=True)
            sys.exit(1)
    if active_task:
        if _is_task_terminal_or_missing(app, active_task):
            _mark_task_inactive(
                active_task,
                status="failed",
                reason="go auto-cleared stale active marker (terminal graph state)",
            )
            if read_lock() == active_task:
                release_lock()
            clear_runtime()
            click.echo(f"🧹 检测到陈旧 active 标记 '{active_task}'，已自动清理。")
        else:
            with contextlib.suppress(RuntimeError):
                acquire_lock(active_task)
            click.echo(f"❌ 检测到活跃任务标记 '{active_task}'，请先恢复或取消该任务。", err=True)
            click.echo(f"   • my watch --task-id {active_task}   — 恢复自动推进", err=True)
            click.echo(f"   • my cancel --task-id {active_task}  — 取消并清理", err=True)
            click.echo("   • my doctor --fix                    — 自动修复常见状态不一致", err=True)
            sys.exit(1)


@main.command()
@click.argument("requirement")
@click.option("--skill", default="code-implement", help="Skill ID to use")
@click.option("--task-id", default=None, help="Override task ID")
@click.option("--builder", default="", help="IDE for builder role (e.g. windsurf, cursor, kiro)")
@click.option("--reviewer", default="", help="IDE for reviewer role (e.g. cursor, codex, kiro)")
@click.option("--retry-budget", default=2, type=int, help="Max retries")
@click.option("--timeout", default=1800, type=int, help="Timeout in seconds")
@click.option("--no-watch", is_flag=True, default=False, help="Don't auto-watch (exit after start)")
@click.option("--decompose", is_flag=True, default=False, help="Decompose complex requirement into sub-tasks first")
@click.option("--auto-confirm", is_flag=True, default=False, help="Skip decompose confirmation (for automated runs)")
@click.option("--decompose-file", default=None, type=click.Path(exists=True), help="Read decompose result from file instead of agent")
@click.option("--no-cache", is_flag=True, default=False, help="Skip decompose result cache (force fresh decomposition)")
@click.option("--visible", is_flag=True, default=False, help="Open CLI agents in separate Terminal windows (macOS)")
@click.option("--mode", default="strict", help="Workmode profile 名称")
@click.option("--config", "mode_config_path", default="config/workmode.yaml", help="Workmode 配置路径")
@handle_errors
def go(requirement: str, skill: str, task_id: str | None, builder: str, reviewer: str, retry_budget: int, timeout: int, no_watch: bool, decompose: bool, auto_confirm: bool, decompose_file: str | None, no_cache: bool, visible: bool, mode: str, mode_config_path: str) -> None:
    """Start a new task and watch for IDE output.

    Starts the task, then auto-watches outbox/ for agent output.
    When the IDE AI saves its result, the orchestrator auto-advances.

    Usage:
      1. Run: my go "your requirement"
      2. Open .multi-agent/TASK.md in your IDE
      3. Watch the terminal — it handles the rest

    Examples:
      my go "实现 POST /users endpoint"
      my go "Add auth middleware" --builder windsurf --reviewer cursor
      my go "Fix login bug" --no-watch
      my go "实现完整用户认证模块" --decompose
    """
    from multi_agent.config import load_project_config
    from multi_agent.graph import compile_graph

    ensure_workspace()

    # Load custom agent persona names from .ma.yaml (if any)
    from multi_agent.config import load_agent_names_from_config
    load_agent_names_from_config()

    if task_id:
        _validate_task_id(task_id)
    _validate_skill_id(skill)
    if builder and reviewer and builder == reviewer:
        from multi_agent.driver import get_agent_driver
        drv = get_agent_driver(builder)
        if drv["driver"] == "file":
            raise click.BadParameter(
                f"file driver 不支持同一 agent 做 builder 和 reviewer (got '{builder}')",
                param_hint="--reviewer",
            )
        click.echo(f"⚠️  builder 和 reviewer 都是 '{builder}'，将使用同一 agent 的不同实例", err=True)

    # Task 6: Apply project config defaults (CLI flags override)
    proj = load_project_config()
    builder, reviewer, timeout, retry_budget, mode, mode_config_path = _apply_project_defaults(
        proj, builder, reviewer, timeout, retry_budget, mode, mode_config_path,
    )

    from multi_agent.session import _resolve_review_policy
    review_policy = _resolve_review_policy(mode, mode_config_path)

    # Task 16: Suggest decompose for complex requirements
    if not decompose:
        from multi_agent.decompose import estimate_complexity
        complexity = estimate_complexity(requirement)
        if complexity == "complex":
            click.echo("⚠️  需求较复杂，建议使用 --decompose 模式", err=True)

    # Enforce single active task — prevent data conflicts
    app = compile_graph()
    _ensure_no_active_task(app)

    task_id = task_id or _generate_task_id(requirement)

    # Clear ALL shared runtime files to prevent stale data leaking
    clear_runtime()

    # Acquire lock — marks this task as the sole active task
    acquire_lock(task_id)

    try:
        if decompose or decompose_file:
            from multi_agent.cli_decompose import _run_decomposed
            _run_decomposed(app, task_id, requirement, skill, builder, reviewer,
                            retry_budget, timeout, no_watch, mode, review_policy,
                            auto_confirm=auto_confirm, decompose_file=decompose_file,
                            no_cache=no_cache, visible=visible)
            return

        _run_single_task(app, task_id, requirement, skill, builder, reviewer,
                         retry_budget, timeout, no_watch, mode, review_policy, visible=visible)
    except (SystemExit, KeyboardInterrupt):
        raise  # don't release lock on intentional exit or Ctrl-C (task still active)
    except Exception:
        # Release lock on unexpected errors to prevent permanent lock leak
        release_lock()
        raise


def _run_single_task(app: Any, task_id: str, requirement: str, skill: str, builder: str, reviewer: str,
                     retry_budget: int, timeout: int, no_watch: bool, workflow_mode: str, review_policy: Any, visible: bool = False) -> None:
    """Run a single monolithic build-review cycle (original behavior)."""
    from multi_agent.orchestrator import TaskStartError, start_task

    # Resolve orchestrator for state persistence (used by session_push/status)
    from multi_agent.router import get_defaults as _get_defaults
    _defaults = _get_defaults()
    _orchestrator = str(_defaults.get("orchestrator", "")).strip() or "codex"

    initial_state = {
        "task_id": task_id,
        "requirement": requirement,
        "skill_id": skill,
        "done_criteria": [requirement],
        "workflow_mode": workflow_mode,
        "review_policy": review_policy,
        "timeout_sec": timeout,
        "retry_budget": retry_budget,
        "retry_count": 0,
        "input_payload": {"requirement": requirement},
        "builder_explicit": builder,
        "reviewer_explicit": reviewer,
        "orchestrator_id": _orchestrator,
        "conversation": [],
    }

    click.echo(f"🚀 Task: {task_id}")
    click.echo(f"   {requirement}")
    click.echo()

    # Delegate to orchestrator for graph invocation
    try:
        start_task(app, task_id, initial_state)
    except TaskStartError as e:
        release_lock()
        cause = e.cause
        if isinstance(cause, FileNotFoundError):
            click.echo(f"❌ {cause}", err=True)
            click.echo("   确认你在 MyGO 项目根目录运行, 且 skills/ 和 agents/ 存在。", err=True)
            click.echo("   或设置 MA_ROOT 环境变量指向项目根目录。", err=True)
        elif isinstance(cause, ValueError):
            click.echo(f"❌ {cause}", err=True)
            click.echo("   检查 agents/agents.yaml 配置是否正确。", err=True)
        else:
            click.echo(f"❌ Task failed to start: {cause}", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(cause)})
        sys.exit(1)

    save_task_yaml(task_id, {"task_id": task_id, "skill": skill, "status": "active"})

    config = _make_config(task_id)

    # Show what to do
    _show_waiting(app, config, visible=visible)

    if no_watch:
        click.echo("\n📌 Run `my done` after the IDE finishes, or `my watch` to auto-detect.")
        return

    # Auto-watch mode (default) — poll outbox and auto-submit
    _run_watch_loop(app, config, task_id, visible=visible)


def _resolve_done_task(app: Any, task_id: str | None) -> tuple[str, Any, Any]:
    """Resolve task ID, config, and snapshot for `done` command. Exits on error."""
    if task_id:
        _validate_task_id(task_id)
    else:
        task_id = _detect_active_task(app)
        if not task_id:
            click.echo("❌ No active task found. Specify --task-id.", err=True)
            sys.exit(1)

    config = _make_config(task_id)
    snapshot = app.get_state(config)

    if not snapshot or not snapshot.next:
        click.echo("❌ No pending interrupt for this task.", err=True)
        sys.exit(1)

    return task_id, config, snapshot


def _read_done_output(role: str, file_path: str | None) -> dict[str, Any]:
    """Read output from --file, role-based outbox, or stdin. Exits on error."""
    output_data = None
    if file_path:
        try:
            fsize = Path(file_path).stat().st_size
        except OSError:
            fsize = 0
        if fsize > 10 * 1024 * 1024:
            click.echo(f"❌ File too large ({fsize // 1024 // 1024} MB > 10 MB limit): {file_path}", err=True)
            sys.exit(1)
        try:
            with Path(file_path).open(encoding="utf-8") as f:
                output_data = json.load(f)
        except json.JSONDecodeError as e:
            click.echo(f"❌ Invalid JSON in {file_path}: {e}", err=True)
            sys.exit(1)
    else:
        output_data = read_outbox(role)

    if output_data is None:
        click.echo(f"📝 No output in outbox/{role}.json. Paste JSON (Ctrl-D to end):")
        raw = sys.stdin.read().strip()
        if raw:
            try:
                output_data = json.loads(raw)
            except json.JSONDecodeError as e:
                click.echo(f"❌ Invalid JSON: {e}", err=True)
                sys.exit(1)

    if output_data is None:
        click.echo(f"❌ No output found. Save to .multi-agent/outbox/{role}.json or use --file.", err=True)
        sys.exit(1)

    return output_data


@main.command()
@handle_errors
@click.option("--task-id", default=None, help="Task ID (auto-detect if only one active)")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="Read output from file")
def done(task_id: str | None, file_path: str | None) -> None:
    """手动提交 IDE 输出并推进任务.

    自动从 .multi-agent/outbox/ 读取当前角色的 JSON 输出,
    也可用 --file 指定文件, 或从 stdin 粘贴.
    """
    from multi_agent.graph import compile_graph

    app = compile_graph()
    task_id, config, snapshot = _resolve_done_task(app, task_id)

    # Determine current role and agent from interrupt metadata
    role = "builder"
    agent_id = "?"
    if snapshot.tasks and snapshot.tasks[0].interrupts:
        info = snapshot.tasks[0].interrupts[0].value
        role = info.get("role", "builder")
        agent_id = info.get("agent", "?")

    # Read, normalize, and validate output
    output_data = _read_done_output(role, file_path)
    vals = snapshot.values or {}
    try:
        output_data = _normalize_resume_output(role, output_data, vals)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    validation_errors = validate_outbox_data(role, output_data)
    if validation_errors:
        click.echo("⚠️  Output validation warnings:", err=True)
        for ve in validation_errors:
            click.echo(f"   - {ve}", err=True)

    click.echo(f"📤 Submitting {role} output for task {task_id} (IDE: {agent_id})")

    from multi_agent.orchestrator import resume_task
    try:
        status = resume_task(app, task_id, output_data)
    except Exception as e:
        release_lock()
        clear_runtime()
        click.echo(f"❌ Graph error during resume: {e}", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        sys.exit(1)

    # Mark task completed if graph finished
    if status.is_terminal:
        final = status.final_status or ""
        if final:
            save_task_yaml(task_id, {"task_id": task_id, "status": final})
        release_lock()
        clear_runtime()

    _show_waiting(app, config)


@main.command()
@handle_errors
@click.option("--task-id", default=None, help="Task ID")
def status(task_id: str | None) -> None:
    """Show current task status."""
    from multi_agent.graph import compile_graph

    app = compile_graph()

    if task_id:
        _validate_task_id(task_id)
    else:
        task_id = _detect_active_task(app)
        if not task_id:
            click.echo("No active tasks.")
            return

    config = _make_config(task_id)
    snapshot = app.get_state(config)

    if not snapshot:
        click.echo(f"No state found for task {task_id}")
        return

    vals = snapshot.values
    current_role = vals.get("current_role", "?")
    locked = read_lock()

    click.echo(f"📊 Task: {task_id}")
    click.echo(f"   Step:     {current_role}")
    click.echo(f"   Builder:  {vals.get('builder_id', '?')}")
    click.echo(f"   Reviewer: {vals.get('reviewer_id', '?')}")
    click.echo(f"   Retry:    {vals.get('retry_count', 0)}/{vals.get('retry_budget', 2)}")
    click.echo(f"   Lock:     {'🔒 ' + locked if locked else '🔓 none'}")

    if vals.get("error"):
        click.echo(f"   ❌ Error: {vals['error']}")
    final_status = vals.get("final_status")
    if final_status:
        click.echo(f"   🏁 Final: {final_status}")
        if _is_terminal_final_status(final_status):
            click.echo("   ✅ Graph complete")
            return

    if snapshot.next:
        agent = vals.get("builder_id" if current_role == "builder" else "reviewer_id", "?")
        from multi_agent.driver import get_agent_driver
        drv = get_agent_driver(agent)
        mode = "🤖 auto" if drv["driver"] == "cli" else "📋 manual"
        click.echo(f"   ⏸️  Waiting: {current_role} ({agent}) [{mode}]")
        if drv["driver"] != "cli":
            click.echo(f'   📋 在 {agent} IDE 里说: "帮我完成 @.multi-agent/TASK.md 里的任务"')
    else:
        click.echo("   ✅ Graph complete")


@main.command()
@handle_errors
@click.option("--task-id", default=None)
@click.option("--reason", default="user cancelled")
def cancel(task_id: str | None, reason: str) -> None:
    """Cancel the current task."""
    from multi_agent.graph import compile_graph

    app = compile_graph()

    if task_id:
        _validate_task_id(task_id)
    else:
        task_id = _detect_active_task(app)
        if not task_id:
            # Fallback: check for orphaned lock (e.g. after kill -9)
            task_id = read_lock()
            if not task_id:
                click.echo("No active task to cancel.")
                return
            _validate_task_id(task_id)
            click.echo(f"⚠️  发现孤立锁 (task: {task_id}), 正在清理…")

    # Mark task YAML as cancelled so auto-detect skips it
    save_task_yaml(task_id, {"task_id": task_id, "status": "cancelled", "reason": reason})

    # Release lock + clean shared files
    release_lock()
    clear_runtime()

    click.echo(f"🛑 Task {task_id} cancelled: {reason}")


@main.command()
@handle_errors
@click.option("--task-id", default=None)
@click.option("--interval", default=2.0, type=float, help="Poll interval in seconds")
def watch(task_id: str | None, interval: float) -> None:
    """自动检测 IDE 输出并推进任务.

    恢复之前中断的自动检测.
    适用于 `my go --no-watch` 启动的任务.
    """
    from multi_agent.graph import compile_graph

    app = compile_graph()

    if task_id:
        _validate_task_id(task_id)
    else:
        task_id = _detect_active_task(app)
        if not task_id:
            click.echo("❌ No active task to watch.", err=True)
            sys.exit(1)

    # Validate lock consistency — prevent watching wrong task
    locked = read_lock()
    if locked and locked != task_id:
        click.echo(f"❌ 锁文件指向 '{locked}', 但你要 watch '{task_id}'。", err=True)
        click.echo("   同时只能有一个活跃任务。", err=True)
        sys.exit(1)
    if not locked:
        acquire_lock(task_id)

    config = _make_config(task_id)
    snapshot = app.get_state(config)
    if not snapshot or not snapshot.next:
        vals = snapshot.values if snapshot else {}
        final = vals.get("final_status", "done")
        release_lock()
        clear_runtime()
        click.echo(f"✅ Task {task_id} already finished — {final}")
        return
    _show_waiting(app, config)
    _run_watch_loop(app, config, task_id, interval=interval)


def _detect_active_task(app: Any = None) -> str | None:
    """Detect the active task from task YAML markers in workspace."""
    from multi_agent.config import tasks_dir
    td = tasks_dir()
    if not td.exists():
        return None
    yamls = sorted(td.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    for yf in yamls:
        try:
            import yaml
            data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
            if data.get("status") == "active":
                tid = yf.stem
                if not _SAFE_TASK_ID_RE.match(tid):
                    continue  # skip malicious filenames
                return tid
        except Exception:
            continue
    return None


def _auto_fix_runtime_consistency() -> list[str]:
    """Best-effort lock/task marker reconciliation for smoother recovery."""
    actions: list[str] = []
    active_task = _detect_active_task()
    locked_task = read_lock()
    app = None
    if active_task or locked_task:
        from multi_agent.graph import compile_graph
        app = compile_graph()

    if active_task and not locked_task:
        if app and _is_task_terminal_or_missing(app, active_task):
            _mark_task_inactive(
                active_task,
                status="failed",
                reason="doctor auto-fixed stale active marker (terminal graph state)",
            )
            actions.append(f"清理陈旧 active 标记: {active_task}")
            return actions
        try:
            acquire_lock(active_task)
            actions.append(f"恢复锁: {active_task}")
        except Exception as exc:  # pragma: no cover - defensive
            actions.append(f"恢复锁失败: {active_task} ({exc})")
        return actions

    if locked_task and not active_task:
        if app and not _is_task_terminal_or_missing(app, locked_task):
            actions.append(f"保留锁: {locked_task}（任务仍在进行）")
            return actions
        release_lock()
        actions.append(f"释放孤立锁: {locked_task}")
        return actions

    if locked_task and active_task and locked_task != active_task:
        release_lock()
        try:
            acquire_lock(active_task)
            actions.append(f"重对齐锁: {locked_task} -> {active_task}")
        except Exception as exc:  # pragma: no cover - defensive
            actions.append(f"重对齐失败: {locked_task} -> {active_task} ({exc})")
        return actions

    return actions


# ── Admin commands (extracted to cli_admin.py) ──────────
from multi_agent.cli_admin import register_admin_commands  # noqa: E402

register_admin_commands(main)

# ── Queue runner commands (cli_queue.py) ─────────────────
from multi_agent.cli_queue import register_queue_commands  # noqa: E402

register_queue_commands(main)


if __name__ == "__main__":
    main()
