"""CLI entry point — ma go / ma done / ma status / ma cancel / ma watch."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import signal
import sys
import time
import traceback
from pathlib import Path

import click

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

log = logging.getLogger(__name__)


def handle_errors(f):
    """Unified exception handler for CLI commands.

    - Shows user-friendly error messages by default.
    - Shows full traceback when --verbose is set.
    - Releases lock and cleans runtime on severe errors.
    - Logs error to .multi-agent/logs/ directory.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except SystemExit:
            raise
        except KeyboardInterrupt:
            click.echo("\n⏹️  操作已取消")
            try:
                if read_lock():
                    release_lock()
            except Exception:
                pass
            raise SystemExit(0)
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

            # Release lock on severe errors
            try:
                if read_lock():
                    release_lock()
                    click.echo("🔓 已自动释放锁", err=True)
            except Exception:
                pass

            raise SystemExit(1)
    return wrapper


def _log_error_to_file(command: str, error: Exception):
    """Write error details to .multi-agent/logs/."""
    try:
        from multi_agent.config import workspace_dir
        from datetime import datetime
        logs_dir = workspace_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = logs_dir / f"error-{ts}.log"
        log_file.write_text(
            f"command: {command}\nerror: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _thread_id(task_id: str) -> str:
    return task_id


def _make_config(task_id: str) -> dict:
    return {"configurable": {"thread_id": _thread_id(task_id)}}


_SAFE_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SAFE_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validate_task_id(task_id: str) -> str:
    """Validate task_id to prevent path traversal attacks.

    Rejects IDs containing '/', '..', '~', or other unsafe characters.
    Raises click.BadParameter if invalid.
    """
    if not _SAFE_TASK_ID_RE.match(task_id):
        raise click.BadParameter(
            f"Invalid task_id: {task_id!r}. "
            f"Must match [a-z0-9][a-z0-9-]{{2,63}}.",
            param_hint="--task-id",
        )
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


def _sigterm_handler(signum, frame):
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
def main(verbose: bool):
    """ma — Multi-Agent 协作 CLI. 一条命令协调多个 IDE AI."""
    signal.signal(signal.SIGTERM, _sigterm_handler)


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
@handle_errors
def go(requirement: str, skill: str, task_id: str | None, builder: str, reviewer: str, retry_budget: int, timeout: int, no_watch: bool, decompose: bool, auto_confirm: bool, decompose_file: str | None, no_cache: bool):
    """Start a new task and watch for IDE output.

    Starts the task, then auto-watches outbox/ for agent output.
    When the IDE AI saves its result, the orchestrator auto-advances.

    Usage:
      1. Run: ma go "your requirement"
      2. Open .multi-agent/TASK.md in your IDE
      3. Watch the terminal — it handles the rest

    Examples:
      ma go "实现 POST /users endpoint"
      ma go "Add auth middleware" --builder windsurf --reviewer cursor
      ma go "Fix login bug" --no-watch
      ma go "实现完整用户认证模块" --decompose
    """
    from multi_agent.graph import compile_graph
    from multi_agent.config import load_project_config

    ensure_workspace()

    if task_id:
        _validate_task_id(task_id)
    _validate_skill_id(skill)

    # Task 6: Apply project config defaults (CLI flags override)
    proj = load_project_config()
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

    # Task 16: Suggest decompose for complex requirements
    if not decompose:
        from multi_agent.decompose import estimate_complexity
        complexity = estimate_complexity(requirement)
        if complexity == "complex":
            click.echo("⚠️  需求较复杂，建议使用 --decompose 模式", err=True)

    # Enforce single active task — prevent data conflicts
    app = compile_graph()
    locked = read_lock()
    if locked:
        click.echo(f"❌ 任务 '{locked}' 正在进行中。", err=True)
        click.echo(f"   先完成或取消当前任务:", err=True)
        click.echo(f"   • ma cancel   — 取消当前任务", err=True)
        click.echo(f"   • ma done     — 手动提交结果", err=True)
        click.echo(f"   • ma status   — 查看任务状态", err=True)
        sys.exit(1)

    task_id = task_id or _generate_task_id(requirement)

    # Clear ALL shared runtime files to prevent stale data leaking
    clear_runtime()

    # Acquire lock — marks this task as the sole active task
    acquire_lock(task_id)

    if decompose or decompose_file:
        _run_decomposed(app, task_id, requirement, skill, builder, reviewer,
                        retry_budget, timeout, no_watch,
                        auto_confirm=auto_confirm, decompose_file=decompose_file,
                        no_cache=no_cache)
        return

    _run_single_task(app, task_id, requirement, skill, builder, reviewer,
                     retry_budget, timeout, no_watch)


def _run_single_task(app, task_id, requirement, skill, builder, reviewer,
                     retry_budget, timeout, no_watch):
    """Run a single monolithic build-review cycle (original behavior)."""
    initial_state = {
        "task_id": task_id,
        "requirement": requirement,
        "skill_id": skill,
        "done_criteria": [requirement],
        "timeout_sec": timeout,
        "retry_budget": retry_budget,
        "retry_count": 0,
        "input_payload": {"requirement": requirement},
        "builder_explicit": builder,
        "reviewer_explicit": reviewer,
        "conversation": [],
    }

    click.echo(f"🚀 Task: {task_id}")
    click.echo(f"   {requirement}")
    click.echo()

    config = _make_config(task_id)

    # Run until first interrupt (plan → build interrupt)
    from langgraph.errors import GraphInterrupt
    try:
        app.invoke(initial_state, config)
    except GraphInterrupt:
        pass
    except FileNotFoundError as e:
        release_lock()
        click.echo(f"❌ {e}", err=True)
        click.echo(f"   确认你在 AgentOrchestra 项目根目录运行, 且 skills/ 和 agents/ 存在。", err=True)
        click.echo(f"   或设置 MA_ROOT 环境变量指向项目根目录。", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        sys.exit(1)
    except ValueError as e:
        release_lock()
        click.echo(f"❌ {e}", err=True)
        click.echo(f"   检查 agents/agents.yaml 配置是否正确。", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        sys.exit(1)
    except Exception as e:
        release_lock()
        click.echo(f"❌ Task failed to start: {e}", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        sys.exit(1)

    save_task_yaml(task_id, {"task_id": task_id, "skill": skill, "status": "active"})

    # Show what to do
    _show_waiting(app, config)

    if no_watch:
        click.echo("\n📌 Run `ma done` after the IDE finishes, or `ma watch` to auto-detect.")
        return

    # Auto-watch mode (default) — poll outbox and auto-submit
    _run_watch_loop(app, config, task_id)


def _run_decomposed(app, parent_task_id, requirement, skill, builder, reviewer,
                    retry_budget, timeout, no_watch, *,
                    auto_confirm: bool = False, decompose_file: str | None = None,
                    no_cache: bool = False):
    """Decompose → sequential sub-task build-review cycles → aggregate."""
    from multi_agent.decompose import write_decompose_prompt, read_decompose_result, topo_sort, topo_sort_grouped
    from multi_agent.meta_graph import build_sub_task_state, aggregate_results
    from langgraph.errors import GraphInterrupt

    click.echo(f"🧩 Task Decomposition: {parent_task_id}")
    click.echo(f"   {requirement}")
    click.echo()

    save_task_yaml(parent_task_id, {
        "task_id": parent_task_id, "status": "active", "mode": "decompose",
    })

    # Task 23: Check decompose cache first
    decompose_result = None
    if not decompose_file and not no_cache:
        from multi_agent.decompose import get_cached_decompose
        decompose_result = get_cached_decompose(requirement)
        if decompose_result:
            click.echo(f"💾 使用缓存的分解结果 (原始需求相同)")

    # Task 29: Read decompose result from file if provided (JSON or YAML)
    if decompose_result is None and decompose_file:
        import json as _json
        from multi_agent.schema import DecomposeResult
        try:
            raw = Path(decompose_file).read_text(encoding="utf-8")
            if decompose_file.endswith((".yaml", ".yml")):
                import yaml as _yaml
                data = _yaml.safe_load(raw)
            else:
                data = _json.loads(raw)
            decompose_result = DecomposeResult(**data)
            click.echo(f"📂 从文件加载分解结果: {decompose_file}")
        except Exception as e:
            click.echo(f"❌ 无法读取分解文件: {e}", err=True)
            release_lock()
            sys.exit(1)

    if decompose_result is None:
        # Phase 1: Write decompose prompt → wait for agent to decompose
        write_decompose_prompt(requirement)
        click.echo(f"📋 分解任务中… 在 IDE 里对 AI 说:")
        click.echo(f'   "帮我完成 @.multi-agent/TASK.md 里的任务"')

        # Check if builder has CLI driver → auto-spawn for decomposition
        from multi_agent.driver import get_agent_driver, spawn_cli_agent, can_use_cli
        from multi_agent.router import load_agents
        agents = load_agents()
        decompose_agent = builder if builder else (agents[0].id if agents else "?")
        drv = get_agent_driver(decompose_agent)
        if drv["driver"] == "cli" and drv["command"] and can_use_cli(drv["command"]):
            click.echo(f"🤖 自动调用 {decompose_agent} CLI 进行任务分解…")
            spawn_cli_agent(decompose_agent, "decompose", drv["command"], timeout_sec=timeout)

        click.echo(f"👁️  等待任务分解结果… (Ctrl-C 停止)")

        # Poll for decompose.json (with timeout)
        deadline = time.time() + timeout
        try:
            while decompose_result is None:
                decompose_result = read_decompose_result()
                if decompose_result:
                    break
                if time.time() > deadline:
                    click.echo(f"❌ 任务分解超时 ({timeout}s)。", err=True)
                    release_lock()
                    clear_runtime()
                    sys.exit(1)
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo(f"\n⏹️  Decomposition stopped.")
            release_lock()
            clear_runtime()
            return

    # Task 23: Cache the decompose result for future re-use
    if not decompose_file and not no_cache:
        from multi_agent.decompose import cache_decompose
        try:
            cache_decompose(requirement, decompose_result)
        except Exception:
            pass

    # Task 20: Validate decompose result structure
    from multi_agent.decompose import validate_decompose_result
    validation_errors = validate_decompose_result(decompose_result)
    if validation_errors:
        click.echo(f"⚠️  分解结果存在问题:", err=True)
        for ve in validation_errors:
            click.echo(f"   - {ve}", err=True)

    # Phase 2: Sort sub-tasks by dependencies
    try:
        sorted_tasks = topo_sort(decompose_result.sub_tasks)
    except ValueError as e:
        click.echo(f"❌ 分解结果无效: {e}", err=True)
        release_lock()
        clear_runtime()
        sys.exit(1)

    if not sorted_tasks:
        click.echo(f"⚠️  分解结果为空，降级为单任务模式")
        _run_single_task(app, parent_task_id, requirement, skill, builder, reviewer,
                         retry_budget, timeout, no_watch)
        return

    click.echo(f"\n✅ 分解完成: {len(sorted_tasks)} 个子任务")
    if decompose_result.reasoning:
        click.echo(f"   理由: {decompose_result.reasoning}")

    # Task 19: Show parallel group info
    try:
        groups = topo_sort_grouped(decompose_result.sub_tasks)
        for gi, group in enumerate(groups, 1):
            ids = ", ".join(st.id for st in group)
            if len(group) > 1:
                click.echo(f"   组 {gi} (可并行): {ids}")
            else:
                click.echo(f"   组 {gi}: {ids}")
    except ValueError:
        for i, st in enumerate(sorted_tasks, 1):
            deps_str = f" (依赖: {', '.join(st.deps)})" if st.deps else ""
            click.echo(f"   {i}. {st.id}: {st.description}{deps_str}")
    click.echo()

    # Task 28: Confirmation step before execution
    if not auto_confirm:
        if not click.confirm("确认执行这些子任务？", default=True):
            click.echo("⏹️  已取消。可修改 .multi-agent/outbox/decompose.json 后重新运行。")
            release_lock()
            return

    # Phase 3: Execute each sub-task sequentially
    prior_results: list[dict] = []
    failed_ids: set[str] = set()  # track failed sub-task IDs for dep skipping

    total = len(sorted_tasks)
    decompose_start = time.time()

    for i, st in enumerate(sorted_tasks, 1):
        done_count = len([r for r in prior_results if r["status"] in ("approved", "completed", "skipped")])
        pct = int(done_count / total * 100)

        # Skip sub-tasks whose dependencies failed
        skipped_deps = [d for d in st.deps if d in failed_ids]
        if skipped_deps:
            click.echo(f"\n[{i}/{total}] ⏭️ {st.id} 跳过 ({pct}%)")
            prior_results.append({
                "sub_id": st.id, "status": "skipped",
                "summary": f"Skipped: dependency {', '.join(skipped_deps)} failed",
                "changed_files": [], "retry_count": 0, "duration_sec": 0,
                "estimated_minutes": getattr(st, 'estimated_minutes', 0),
            })
            failed_ids.add(st.id)
            continue

        click.echo(f"\n{'='*60}")
        click.echo(f"  [{i}/{total}] 📦 {st.id} ({pct}% 完成)")
        click.echo(f"  {st.description}")
        click.echo(f"{'='*60}")
        sub_start = time.time()

        # Clear runtime for this sub-task
        clear_runtime()

        sub_state = build_sub_task_state(
            sub_task=st,
            parent_task_id=parent_task_id,
            builder=builder,
            reviewer=reviewer,
            timeout=timeout,
            retry_budget=retry_budget,
            prior_results=prior_results,
        )
        sub_task_id = sub_state["task_id"]
        sub_config = _make_config(sub_task_id)

        # Run sub-task graph
        try:
            app.invoke(sub_state, sub_config)
        except GraphInterrupt:
            pass
        except Exception as e:
            click.echo(f"❌ Sub-task {st.id} failed to start: {e}", err=True)
            prior_results.append({
                "sub_id": st.id, "status": "failed",
                "summary": str(e), "changed_files": [], "retry_count": 0,
                "duration_sec": round(time.time() - sub_start, 1),
                "estimated_minutes": getattr(st, 'estimated_minutes', 0),
            })
            failed_ids.add(st.id)
            continue

        # Show waiting + watch loop for this sub-task
        _show_waiting(app, sub_config)

        if no_watch:
            click.echo(f"📌 Sub-task {st.id}: 等待手动 ma done")
            click.echo(f"⚠️  --no-watch 模式下 --decompose 只执行第一步分解。")
            click.echo(f"   后续请逐个手动执行各子任务。")
            save_task_yaml(parent_task_id, {
                "task_id": parent_task_id, "status": "decomposed",
                "sub_tasks": [s.model_dump() for s in sorted_tasks],
            })
            return

        # manage_lock=False: don't release parent lock between sub-tasks
        _run_watch_loop(app, sub_config, sub_task_id, manage_lock=False)

        # Collect result
        snapshot = app.get_state(sub_config)
        vals = snapshot.values if snapshot else {}
        builder_out = vals.get("builder_output", {})
        if not isinstance(builder_out, dict):
            builder_out = {}

        sub_status = vals.get("final_status", "unknown")
        sub_dur = round(time.time() - sub_start, 1)
        reviewer_out = vals.get("reviewer_output", {})
        if not isinstance(reviewer_out, dict):
            reviewer_out = {}
        prior_results.append({
            "sub_id": st.id,
            "status": sub_status,
            "summary": builder_out.get("summary", ""),
            "changed_files": builder_out.get("changed_files", []),
            "retry_count": vals.get("retry_count", 0),
            "duration_sec": sub_dur,
            "estimated_minutes": getattr(st, 'estimated_minutes', 0),
            "reviewer_feedback": reviewer_out.get("feedback", ""),
        })
        done_count2 = len([r for r in prior_results if r["status"] in ("approved", "completed", "skipped")])
        pct2 = int(done_count2 / total * 100)
        if sub_status in ("approved", "completed"):
            click.echo(f"[{i}/{total}] ✅ {st.id} 完成 ({pct2}%)")
        if sub_status not in ("approved", "completed"):
            # Task 21: User choice on failure (skip for auto CLI mode)
            if not auto_confirm:
                click.echo(f"\n❌ Sub-task {st.id} 失败 (状态: {sub_status})")
                choice = click.prompt(
                    "选择操作", type=click.Choice(["skip", "retry", "abort"]),
                    default="skip",
                )
                if choice == "retry":
                    click.echo(f"🔄 重试 Sub-task {st.id}…")
                    clear_runtime()
                    prior_results.pop()  # remove the failed result
                    sub_state2 = build_sub_task_state(
                        sub_task=st,
                        parent_task_id=parent_task_id,
                        builder=builder, reviewer=reviewer,
                        timeout=timeout, retry_budget=retry_budget,
                        prior_results=prior_results,
                    )
                    sub_config2 = _make_config(sub_state2["task_id"])
                    try:
                        app.invoke(sub_state2, sub_config2)
                    except GraphInterrupt:
                        pass
                    _show_waiting(app, sub_config2)
                    _run_watch_loop(app, sub_config2, sub_state2["task_id"], manage_lock=False)
                    snap2 = app.get_state(sub_config2)
                    v2 = snap2.values if snap2 else {}
                    bo2 = v2.get("builder_output", {})
                    if not isinstance(bo2, dict):
                        bo2 = {}
                    s2 = v2.get("final_status", "unknown")
                    ro2 = v2.get("reviewer_output", {})
                    if not isinstance(ro2, dict):
                        ro2 = {}
                    prior_results.append({
                        "sub_id": st.id, "status": s2,
                        "summary": bo2.get("summary", ""),
                        "changed_files": bo2.get("changed_files", []),
                        "retry_count": v2.get("retry_count", 0),
                        "duration_sec": round(time.time() - sub_start, 1),
                        "estimated_minutes": getattr(st, 'estimated_minutes', 0),
                        "reviewer_feedback": ro2.get("feedback", ""),
                    })
                    if s2 not in ("approved", "completed"):
                        failed_ids.add(st.id)
                    continue
                elif choice == "abort":
                    click.echo("⏹️  终止 decompose 流程，保存已完成结果。")
                    failed_ids.add(st.id)
                    break
            failed_ids.add(st.id)

    # Phase 4: Aggregate
    click.echo(f"\n{'='*60}")
    click.echo(f"  📊 汇总结果")
    click.echo(f"{'='*60}")

    agg = aggregate_results(parent_task_id, prior_results)

    click.echo(f"  总子任务: {agg['total_sub_tasks']}")
    click.echo(f"  完成: {agg['completed']}")
    click.echo(f"  总重试: {agg['total_retries']}")
    if agg["failed"]:
        click.echo(f"  ❌ 失败: {', '.join(agg['failed'])}")
    else:
        click.echo(f"  ✅ 全部通过")
    click.echo(f"  修改文件: {', '.join(agg['all_changed_files']) or '无'}")

    # Task 26: Write Markdown report
    from multi_agent.meta_graph import generate_aggregate_report
    report_text = generate_aggregate_report(agg)
    from multi_agent.config import workspace_dir
    report_path = workspace_dir() / f"report-{parent_task_id}.md"
    report_path.write_text(report_text, encoding="utf-8")
    click.echo(f"  📄 报告: {report_path}")
    total_elapsed = round(time.time() - decompose_start)
    if total_elapsed >= 60:
        mins, secs = divmod(total_elapsed, 60)
        click.echo(f"  ⏱️ 总耗时: {mins} 分 {secs} 秒")
    else:
        click.echo(f"  ⏱️ 总耗时: {total_elapsed} 秒")
    click.echo()

    save_task_yaml(parent_task_id, {
        "task_id": parent_task_id, "status": agg["final_status"],
        "sub_results": prior_results,
    })
    release_lock()
    clear_runtime()


@main.command()
@handle_errors
@click.option("--task-id", default=None, help="Task ID (auto-detect if only one active)")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="Read output from file")
def done(task_id: str | None, file_path: str | None):
    """手动提交 IDE 输出并推进任务.

    自动从 .multi-agent/outbox/ 读取当前角色的 JSON 输出,
    也可用 --file 指定文件, 或从 stdin 粘贴.
    """
    from multi_agent.graph import compile_graph

    app = compile_graph()

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

    # Determine current role and agent from interrupt metadata
    role = "builder"
    agent_id = "?"
    if snapshot.tasks and snapshot.tasks[0].interrupts:
        info = snapshot.tasks[0].interrupts[0].value
        role = info.get("role", "builder")
        agent_id = info.get("agent", "?")

    # Read output: --file > role-based outbox > stdin
    output_data = None

    if file_path:
        # Guard against oversized files (10 MB limit, same as watcher)
        try:
            fsize = Path(file_path).stat().st_size
        except OSError:
            fsize = 0
        if fsize > 10 * 1024 * 1024:
            click.echo(f"❌ File too large ({fsize // 1024 // 1024} MB > 10 MB limit): {file_path}", err=True)
            sys.exit(1)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                output_data = json.load(f)
        except json.JSONDecodeError as e:
            click.echo(f"❌ Invalid JSON in {file_path}: {e}", err=True)
            sys.exit(1)
    else:
        # Role-based outbox: outbox/builder.json or outbox/reviewer.json
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

    # Validate output before submitting to graph
    validation_errors = validate_outbox_data(role, output_data)
    if validation_errors:
        click.echo(f"⚠️  Output validation warnings:", err=True)
        for ve in validation_errors:
            click.echo(f"   - {ve}", err=True)

    click.echo(f"📤 Submitting {role} output for task {task_id} (IDE: {agent_id})")

    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt
    try:
        result = app.invoke(Command(resume=output_data), config)
    except GraphInterrupt:
        pass  # Normal — graph paused at next interrupt()
    except Exception as e:
        release_lock()
        clear_runtime()
        click.echo(f"❌ Graph error during resume: {e}", err=True)
        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        sys.exit(1)

    # Mark task completed if graph finished
    snapshot = app.get_state(config)
    if snapshot and not snapshot.next:
        vals = snapshot.values or {}
        final = vals.get("final_status", "")
        if final:
            save_task_yaml(task_id, {"task_id": task_id, "status": final})
        release_lock()
        clear_runtime()

    _show_waiting(app, config)


@main.command()
@handle_errors
@click.option("--task-id", default=None, help="Task ID")
def status(task_id: str | None):
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
    if vals.get("final_status"):
        click.echo(f"   🏁 Final: {vals['final_status']}")

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
def cancel(task_id: str | None, reason: str):
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
def watch(task_id: str | None, interval: float):
    """自动检测 IDE 输出并推进任务.

    恢复之前中断的自动检测.
    适用于 `ma go --no-watch` 启动的任务.
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
        click.echo(f"   同时只能有一个活跃任务。", err=True)
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


def _show_waiting(app, config):
    """Show current waiting state — auto-spawn CLI agents or show manual instructions."""
    snapshot = app.get_state(config)
    if not snapshot or not snapshot.next:
        vals = snapshot.values if snapshot else {}
        final = vals.get("final_status", "")
        error = vals.get("error", "")
        if final in ("approved", ""):
            click.echo(f"✅ Task finished. Status: {final or 'done'}")
        else:
            click.echo(f"❌ Task finished. Status: {final}{' — ' + error if error else ''}")
        return

    role = "builder"
    agent = "?"
    if snapshot.tasks and snapshot.tasks[0].interrupts:
        info = snapshot.tasks[0].interrupts[0].value
        role = info.get("role", "builder")
        agent = info.get("agent", "?")

    step_label = "Build" if role == "builder" else "Review"

    # Check if agent has CLI driver → auto-spawn (with graceful degradation)
    from multi_agent.driver import get_agent_driver, spawn_cli_agent, can_use_cli
    drv = get_agent_driver(agent)
    if drv["driver"] == "cli" and drv["command"]:
        if can_use_cli(drv["command"]):
            vals = snapshot.values or {}
            timeout = vals.get("timeout_sec", 600)
            click.echo(f"🤖 [{step_label}] 自动调用 {agent} CLI…")
            spawn_cli_agent(agent, role, drv["command"], timeout_sec=timeout)
        else:
            binary = drv["command"].split()[0]
            click.echo(f"⚠️  {agent} 配置为 CLI 模式但 `{binary}` 未安装，降级为手动模式")
            click.echo(f"📋 [{step_label}] 在 {agent} IDE 里对 AI 说:")
            click.echo(f'   "帮我完成 @.multi-agent/TASK.md 里的任务"')
    else:
        click.echo(f"📋 [{step_label}] 在 {agent} IDE 里对 AI 说:")
        click.echo(f'   "帮我完成 @.multi-agent/TASK.md 里的任务"')
    click.echo()


def _run_watch_loop(app, config, task_id: str, interval: float = 2.0, manage_lock: bool = True):
    """Shared watch loop — polls outbox/ and auto-submits output."""
    from multi_agent.watcher import OutboxPoller
    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt

    poller = OutboxPoller(poll_interval=interval)
    start_time = time.time()

    click.echo(f"👁️  等待 IDE 完成任务… (Ctrl-C 停止)")
    click.echo()

    try:
        while True:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)

            snapshot = app.get_state(config)
            if not snapshot or not snapshot.next:
                vals = snapshot.values if snapshot else {}
                final = vals.get("final_status", "")
                if final:
                    save_task_yaml(task_id, {"task_id": task_id, "status": final})
                if manage_lock:
                    release_lock()
                    clear_runtime()
                if final in ("approved", ""):
                    summary = vals.get("builder_output", {}).get("summary", "") if isinstance(vals.get("builder_output"), dict) else ""
                    retries = vals.get("retry_count", 0)
                    click.echo(f"[{mins:02d}:{secs:02d}] ✅ Task finished. Status: {final or 'done'}")
                    if summary:
                        click.echo(f"             {summary}")
                    if retries:
                        click.echo(f"             (经过 {retries} 次重试)")
                else:
                    error = vals.get("error", "")
                    click.echo(f"[{mins:02d}:{secs:02d}] ❌ Task finished. Status: {final}{' — ' + error if error else ''}")
                return

            # Determine which role we're waiting for
            role = "builder"
            agent = "?"
            if snapshot.tasks and snapshot.tasks[0].interrupts:
                info = snapshot.tasks[0].interrupts[0].value
                role = info.get("role", "builder")
                agent = info.get("agent", "?")

            for detected_role, data in poller.check_once():
                if detected_role == role:
                    step_label = "Build" if role == "builder" else "Review"
                    click.echo(f"[{mins:02d}:{secs:02d}] 📥 {step_label} 完成 ({agent})")
                    # Validate output before submitting
                    v_errors = validate_outbox_data(role, data)
                    if v_errors:
                        click.echo(f"[{mins:02d}:{secs:02d}] ⚠️  Output warnings:", err=True)
                        for ve in v_errors:
                            click.echo(f"             - {ve}", err=True)
                    try:
                        app.invoke(Command(resume=data), config)
                    except GraphInterrupt:
                        pass
                    except Exception as e:
                        if manage_lock:
                            release_lock()
                            clear_runtime()
                        click.echo(f"[{mins:02d}:{secs:02d}] ❌ Error: {e}", err=True)
                        save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
                        return

                    # Show next waiting state or completion
                    next_snap = app.get_state(config)
                    if next_snap and next_snap.next and next_snap.tasks and next_snap.tasks[0].interrupts:
                        next_info = next_snap.tasks[0].interrupts[0].value
                        next_role = next_info.get("role", "?")
                        next_agent = next_info.get("agent", "?")
                        next_label = "Build" if next_role == "builder" else "Review"
                        # Show retry feedback if this is a retry
                        next_vals = next_snap.values or {}
                        retry_n = next_vals.get("retry_count", 0)
                        if retry_n > 0 and next_role == "builder":
                            reviewer_out = next_vals.get("reviewer_output", {})
                            feedback = reviewer_out.get("feedback", "")
                            budget = next_vals.get("retry_budget", 2)
                            click.echo(f"[{mins:02d}:{secs:02d}] 🔄 Reviewer 要求修改 ({retry_n}/{budget}):")
                            if feedback:
                                click.echo(f"             {feedback}")
                        # Auto-spawn CLI agent or show manual instructions
                        from multi_agent.driver import get_agent_driver, spawn_cli_agent, can_use_cli
                        drv = get_agent_driver(next_agent)
                        if drv["driver"] == "cli" and drv["command"] and can_use_cli(drv["command"]):
                            t_sec = next_vals.get("timeout_sec", 600)
                            click.echo(f"[{mins:02d}:{secs:02d}] 🤖 自动调用 {next_agent} CLI…")
                            spawn_cli_agent(next_agent, next_role, drv["command"], timeout_sec=t_sec)
                        else:
                            if drv["driver"] == "cli" and drv["command"] and not can_use_cli(drv["command"]):
                                binary = drv["command"].split()[0]
                                click.echo(f"[{mins:02d}:{secs:02d}] ⚠️  `{binary}` 未安装，降级手动模式")
                            click.echo(f"[{mins:02d}:{secs:02d}] 📋 在 {next_agent} IDE 里对 AI 说:")
                            click.echo(f'             "帮我完成 @.multi-agent/TASK.md 里的任务"')
                    break

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo(f"\n⏹️  Watch stopped. Task still active — resume with: ma watch")


@main.command()
@handle_errors
@click.option("--limit", default=20, type=int, help="Max number of tasks to show")
@click.option("--status", "filter_status", default=None, help="Filter by status (active/approved/failed/cancelled)")
def history(limit: int, filter_status: str | None):
    """查看历史任务记录."""
    from multi_agent.config import tasks_dir
    import yaml

    td = tasks_dir()
    if not td.exists() or not list(td.glob("*.yaml")):
        click.echo("暂无历史任务记录")
        return

    yamls = sorted(td.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    shown = 0
    for yf in yamls:
        if shown >= limit:
            break
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        task_id = data.get("task_id", yf.stem)
        task_status = data.get("status", "unknown")
        if filter_status and task_status != filter_status:
            continue
        mtime = yf.stat().st_mtime
        from datetime import datetime
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        emoji = {"approved": "✅", "failed": "❌", "cancelled": "🛑", "active": "🔵"}.get(task_status, "⚪")
        click.echo(f"  {emoji} {task_id}  [{task_status}]  {ts}")
        shown += 1

    if shown == 0:
        if filter_status:
            click.echo(f"暂无 status={filter_status} 的任务记录")
        else:
            click.echo("暂无历史任务记录")


@main.command()
@handle_errors
@click.option("--force", is_flag=True, default=False, help="Overwrite existing files")
def init(force: bool):
    """初始化 AgentOrchestra 项目."""
    from pathlib import Path
    import yaml

    cwd = Path.cwd()

    # Check if already initialized
    skills = cwd / "skills"
    agents = cwd / "agents"
    if skills.exists() and agents.exists() and not force:
        click.echo("⚠️  项目已初始化。使用 --force 覆盖。")
        return

    # Create skill contract
    skill_dir = skills / "code-implement"
    skill_dir.mkdir(parents=True, exist_ok=True)
    contract_file = skill_dir / "contract.yaml"
    if not contract_file.exists() or force:
        contract_data = {
            "id": "code-implement",
            "version": "1.0.0",
            "description": "实现代码功能",
            "quality_gates": ["lint", "unit_test"],
            "preconditions": [],
            "postconditions": [],
            "timeouts": {"run_sec": 1800, "verify_sec": 600},
            "retry": {"max_attempts": 2, "backoff": "linear"},
        }
        with contract_file.open("w", encoding="utf-8") as f:
            yaml.dump(contract_data, f, default_flow_style=False, allow_unicode=True)
        click.echo(f"  ✅ {contract_file.relative_to(cwd)}")

    # Create agents.yaml
    agents.mkdir(parents=True, exist_ok=True)
    agents_file = agents / "agents.yaml"
    if not agents_file.exists() or force:
        agents_data = {
            "version": 2,
            "role_strategy": "manual",
            "defaults": {"builder": "windsurf", "reviewer": "cursor"},
            "agents": [
                {"id": "windsurf", "driver": "file", "capabilities": ["implementation", "review"]},
                {"id": "cursor", "driver": "file", "capabilities": ["implementation", "review"]},
            ],
        }
        with agents_file.open("w", encoding="utf-8") as f:
            yaml.dump(agents_data, f, default_flow_style=False, allow_unicode=True)
        click.echo(f"  ✅ {agents_file.relative_to(cwd)}")

    # Create workspace
    ensure_workspace()
    click.echo(f"  ✅ .multi-agent/")

    click.echo("\n🎉 初始化完成！下一步:")
    click.echo('  ma go "实现用户登录功能"')


@main.command()
@click.argument("requirement")
@click.option("--skill", default="code-implement", help="Skill to use")
@click.option("--role", default="builder", type=click.Choice(["builder", "reviewer"]),
              help="Which role's prompt to render")
@click.option("--builder-output", "builder_output_file", default=None,
              type=click.Path(exists=True), help="Builder output JSON (required for reviewer role)")
@handle_errors
def render(requirement: str, skill: str, role: str, builder_output_file: str | None):
    """预览 prompt（不执行任何操作）."""
    _validate_skill_id(skill)
    import json
    from multi_agent.contract import load_contract
    from multi_agent.prompt import render_builder_prompt, render_reviewer_prompt
    from multi_agent.schema import Task

    try:
        contract = load_contract(skill)
    except FileNotFoundError:
        click.echo(f"❌ Skill '{skill}' not found", err=True)
        raise SystemExit(1)

    task = Task(
        task_id="render-preview",
        trace_id="0" * 16,
        skill_id=skill,
        done_criteria=[f"完成: {requirement}"],
        input_payload={"requirement": requirement},
    )

    if role == "builder":
        result = render_builder_prompt(task, contract, agent_id="preview")
    else:
        if not builder_output_file:
            click.echo("❌ --builder-output is required for reviewer role", err=True)
            raise SystemExit(1)
        with open(builder_output_file, "r", encoding="utf-8") as f:
            builder_output = json.load(f)
        result = render_reviewer_prompt(
            task, contract, agent_id="preview",
            builder_output=builder_output, builder_id="preview-builder",
        )

    click.echo(result)


@main.command("cache-stats")
@handle_errors
def cache_stats():
    """显示 LRU 缓存命中率."""
    from multi_agent.config import root_dir
    info = root_dir.cache_info()
    click.echo(f"root_dir cache: hits={info.hits}, misses={info.misses}, "
               f"size={info.currsize}/{info.maxsize}")


@main.command()
@click.argument("model", default="all",
                type=click.Choice(["all", "Task", "BuilderOutput", "ReviewerOutput",
                                   "SubTask", "DecomposeResult"], case_sensitive=False))
@handle_errors
def schema(model: str):
    """导出 Pydantic 模型的 JSON Schema."""
    import json as _json
    from multi_agent.schema import (
        Task, BuilderOutput, ReviewerOutput, SubTask, DecomposeResult,
    )
    models = {
        "Task": Task, "BuilderOutput": BuilderOutput,
        "ReviewerOutput": ReviewerOutput, "SubTask": SubTask,
        "DecomposeResult": DecomposeResult,
    }
    if model == "all":
        for name, cls in models.items():
            click.echo(f"--- {name} ---")
            click.echo(_json.dumps(cls.model_json_schema(), indent=2))
            click.echo()
    else:
        cls = models[model]
        click.echo(_json.dumps(cls.model_json_schema(), indent=2))


@main.command()
@click.option("--days", default=7, type=int, help="Max age in days")
@handle_errors
def cleanup(days: int):
    """清理旧的 workspace 文件."""
    from multi_agent.workspace import cleanup_old_files
    deleted = cleanup_old_files(max_age_days=days)
    click.echo(f"已清理 {deleted} 个文件 (>{days} 天)")


@main.command()
@handle_errors
def doctor():
    """检查 workspace 健康状态."""
    from multi_agent.workspace import check_workspace_health, get_workspace_stats
    issues = check_workspace_health()
    stats = get_workspace_stats()

    click.echo(f"📊 Workspace: {stats['file_count']} 文件, {stats['total_size_mb']} MB")
    if stats["largest_file"]:
        click.echo(f"   最大文件: {stats['largest_file']}")

    if not issues:
        click.echo("✅ 健康状态: 正常")
    else:
        click.echo(f"⚠️  发现 {len(issues)} 个问题:")
        for issue in issues:
            click.echo(f"   - {issue}")


@main.command()
@handle_errors
def agents():
    """显示所有 agent 状态."""
    from multi_agent.router import load_agents, check_agent_health
    agent_list = load_agents()
    if not agent_list:
        click.echo("暂无配置的 agent")
        return
    health = check_agent_health(agent_list)
    for h in health:
        status_icon = "✅" if h["status"] == "healthy" else "⚠️"
        click.echo(f"  {status_icon} {h['id']} — {h['status']}")
        for issue in h["issues"]:
            click.echo(f"      {issue}")


@main.command("list-skills")
@handle_errors
def list_skills():
    """列出所有可用 skill."""
    from multi_agent.config import skills_dir
    sd = skills_dir()
    if not sd.exists():
        click.echo("暂无 skill 目录")
        return
    found = 0
    for skill_dir in sorted(sd.iterdir()):
        contract_path = skill_dir / "contract.yaml"
        if not contract_path.exists():
            continue
        try:
            import yaml
            data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
            sid = data.get("id", skill_dir.name)
            ver = data.get("version", "?")
            desc = data.get("description", "")
            gates = ", ".join(data.get("quality_gates", []))
            click.echo(f"  {sid} (v{ver}) — {desc}")
            if gates:
                click.echo(f"    quality_gates: {gates}")
            found += 1
        except Exception:
            continue
    if found == 0:
        click.echo("暂无可用 skill")


@main.command()
@click.argument("task_id")
@click.option("--format", "fmt", default="json",
              type=click.Choice(["json", "markdown"]), help="Export format")
@handle_errors
def export(task_id: str, fmt: str):
    """导出任务执行结果."""
    _validate_task_id(task_id)
    import json as _json
    from multi_agent.config import history_dir, tasks_dir
    history_file = history_dir() / f"{task_id}.json"
    task_file = tasks_dir() / f"{task_id}.yaml"

    result = {"task_id": task_id}
    if task_file.exists():
        import yaml
        try:
            result["config"] = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        except Exception:
            result["config"] = {"_error": "corrupted YAML"}
    if history_file.exists():
        try:
            result["conversation"] = _json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            result["conversation"] = [{"_error": "corrupted JSON"}]
    else:
        click.echo(f"⚠️  未找到历史记录: {task_id}", err=True)
        result["conversation"] = []

    if fmt == "json":
        click.echo(_json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo(f"# Task: {task_id}\n")
        if "config" in result:
            click.echo(f"**Skill**: {result['config'].get('skill_id', '?')}")
            click.echo(f"**Status**: {result['config'].get('status', '?')}\n")
        click.echo("## Conversation\n")
        for entry in result.get("conversation", []):
            role = entry.get("role", "?")
            action = entry.get("action", "?")
            click.echo(f"- **{role}**: {action}")


@main.command()
@click.argument("task_id")
@click.option("--from-step", "from_step", default=0, type=int, help="Start from step N")
@handle_errors
def replay(task_id: str, from_step: int):
    """重放任务历史."""
    _validate_task_id(task_id)
    import json as _json
    from multi_agent.config import history_dir
    history_file = history_dir() / f"{task_id}.json"
    if not history_file.exists():
        click.echo(f"❌ 未找到历史记录: {task_id}", err=True)
        raise SystemExit(1)

    conversation = _json.loads(history_file.read_text(encoding="utf-8"))
    click.echo(f"📼 Replay: {task_id} ({len(conversation)} steps)\n")

    for i, entry in enumerate(conversation):
        if i < from_step:
            continue
        role = entry.get("role", "?")
        action = entry.get("action", "?")
        t = entry.get("t", "")
        click.echo(f"  [{i}] {role}: {action}")
        if "summary" in entry:
            click.echo(f"       {entry['summary'][:100]}")

    click.echo(f"\n✅ Replay complete ({len(conversation)} total, shown from step {from_step})")


@main.command()
@handle_errors
def version():
    """显示版本信息."""
    import sys
    from pathlib import Path
    from multi_agent import __version__
    click.echo(f"AgentOrchestra v{__version__}")
    click.echo(f"Python {sys.version}")
    click.echo(f"Install: {Path(__file__).parent}")


def _detect_active_task(app=None) -> str | None:
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


if __name__ == "__main__":
    main()
