"""Decomposed task execution — extracted from cli.py (A2b refactor).

Contains _run_decomposed(): the sub-task build-review pipeline
invoked by `my go --decompose`.  Supports both sequential and parallel
execution of independent sub-tasks via topo_sort_grouped().

All CLI helpers (_make_config, _show_waiting, _run_watch_loop, _run_single_task)
are imported lazily from cli.py to break the circular-import chain.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import click


def _read_decompose_file(decompose_file: str) -> Any:
    """Read decompose result from a JSON/YAML file. Exits on error."""
    import json as _json

    from multi_agent.schema import DecomposeResult
    from multi_agent.workspace import release_lock
    try:
        raw = Path(decompose_file).read_text(encoding="utf-8")
        if decompose_file.endswith((".yaml", ".yml")):
            import yaml as _yaml
            data = _yaml.safe_load(raw)
        else:
            data = _json.loads(raw)
        click.echo(f"📂 从文件加载分解结果: {decompose_file}")
        return DecomposeResult(**data)
    except Exception as e:
        click.echo(f"❌ 无法读取分解文件: {e}", err=True)
        release_lock()
        sys.exit(1)


def _wait_for_decompose_agent(
    requirement: str, builder: str, timeout: int,
) -> Any | None:
    """Write prompt and wait for agent to produce decompose result."""
    from multi_agent.decompose import read_decompose_result, write_decompose_prompt
    from multi_agent.workspace import clear_runtime, release_lock

    write_decompose_prompt(requirement)
    click.echo("📋 分解任务中… 在 IDE 里对 AI 说:")
    click.echo('   "帮我完成 @.multi-agent/TASK.md 里的任务"')

    from multi_agent.driver import can_use_cli, get_agent_driver, spawn_cli_agent
    from multi_agent.router import load_agents
    agents = load_agents()
    decompose_agent = builder if builder else (agents[0].id if agents else "?")
    drv = get_agent_driver(decompose_agent)
    if drv["driver"] == "cli" and drv["command"] and can_use_cli(drv["command"]):
        click.echo(f"🤖 自动调用 {decompose_agent} CLI 进行任务分解…")
        spawn_cli_agent(decompose_agent, "decompose", drv["command"], timeout_sec=timeout)

    click.echo("👁️  等待任务分解结果… (Ctrl-C 停止)")
    deadline = time.time() + timeout
    try:
        while True:
            result = read_decompose_result()
            if result:
                return result
            if time.time() > deadline:
                click.echo(f"❌ 任务分解超时 ({timeout}s)。", err=True)
                release_lock()
                clear_runtime()
                sys.exit(1)
            time.sleep(2)
    except KeyboardInterrupt:
        click.echo("\n⏹️  Decomposition stopped.")
        release_lock()
        clear_runtime()
        return None


def _obtain_decompose_result(
    requirement: str,
    skill: str,
    builder: str,
    timeout: int,
    *,
    decompose_file: str | None = None,
    no_cache: bool = False,
) -> Any:
    """Phase 1: Obtain decompose result from cache, file, or by waiting for agent."""
    # Check cache first
    result = None
    if not decompose_file and not no_cache:
        from multi_agent.decompose import get_cached_decompose
        result = get_cached_decompose(requirement, skill_id=skill)
        if result:
            click.echo("💾 使用缓存的分解结果 (原始需求相同)")

    if result is None and decompose_file:
        result = _read_decompose_file(decompose_file)

    if result is None:
        result = _wait_for_decompose_agent(requirement, builder, timeout)

    # Cache the result for future re-use
    if result and not decompose_file and not no_cache:
        from multi_agent.decompose import cache_decompose
        with contextlib.suppress(Exception):
            cache_decompose(requirement, result, skill_id=skill)
    return result


def _collect_sub_result(
    app: Any, config: dict[str, Any], st: Any, sub_start: float,
) -> dict[str, Any]:
    """Collect result from a completed sub-task snapshot."""
    snapshot = app.get_state(config)
    vals = snapshot.values if snapshot else {}
    builder_out = vals.get("builder_output", {})
    if not isinstance(builder_out, dict):
        builder_out = {}
    reviewer_out = vals.get("reviewer_output", {})
    if not isinstance(reviewer_out, dict):
        reviewer_out = {}
    return {
        "sub_id": st.id,
        "status": vals.get("final_status", "unknown"),
        "summary": builder_out.get("summary", ""),
        "changed_files": builder_out.get("changed_files", []),
        "retry_count": vals.get("retry_count", 0),
        "duration_sec": round(time.time() - sub_start, 1),
        "estimated_minutes": getattr(st, 'estimated_minutes', 0),
        "reviewer_feedback": reviewer_out.get("feedback", ""),
    }


class _DecomposeExecContext:
    """Encapsulates per-sub-task execution logic to reduce _run_decomposed complexity."""

    def __init__(self, *, app: Any, parent_task_id: str,
                 builder: str, reviewer: str, timeout: int, retry_budget: int,
                 workflow_mode: str, review_policy: Any,
                 no_watch: bool, auto_confirm: bool,
                 make_config: Any, build_state: Any, start_task: Any,
                 start_error: type[BaseException], show_waiting: Any, watch_loop: Any,
                 save_yaml: Any, save_ckpt: Any, clear_rt: Any,
                 visible: bool = False) -> None:
        self.app = app
        self.parent_task_id = parent_task_id
        self.builder = builder
        self.reviewer = reviewer
        self.timeout = timeout
        self.retry_budget = retry_budget
        self.workflow_mode = workflow_mode
        self.review_policy = review_policy
        self.no_watch = no_watch
        self.auto_confirm = auto_confirm
        self.make_config = make_config
        self.build_state = build_state
        self.start_task = start_task
        self.start_error = start_error
        self.show_waiting = show_waiting
        self.watch_loop = watch_loop
        self.save_yaml = save_yaml
        self.save_ckpt = save_ckpt
        self.clear_rt = clear_rt
        self.visible = visible

    def run_one(
        self, i: int, total: int, st: Any,
        prior_results: list[dict[str, Any]],
        completed_ids: set[str], failed_ids: set[str],
        sorted_tasks: Any,
    ) -> str | None:
        """Execute one sub-task. Returns 'return', 'break', or None (continue)."""
        if st.id in completed_ids:
            click.echo(f"\n[{i}/{total}] ⏩ {st.id} 已完成 (checkpoint)")
            return None
        done_count = len([r for r in prior_results if r["status"] in ("approved", "completed", "skipped")])
        pct = int(done_count / total * 100)

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
            return None

        click.echo(f"\n{'='*60}")
        click.echo(f"  [{i}/{total}] 📦 {st.id} ({pct}% 完成)")
        click.echo(f"  {st.description}")
        click.echo(f"{'='*60}")
        sub_start = time.time()
        self.clear_rt()

        sub_state = self.build_state(
            sub_task=st, parent_task_id=self.parent_task_id,
            builder=self.builder, reviewer=self.reviewer,
            timeout=self.timeout, retry_budget=self.retry_budget,
            prior_results=prior_results,
            workflow_mode=self.workflow_mode, review_policy=self.review_policy,
        )
        sub_task_id = sub_state["task_id"]
        sub_config = self.make_config(sub_task_id)

        try:
            self.start_task(self.app, sub_task_id, sub_state)
        except self.start_error as e:
            cause = getattr(e, "cause", e)
            click.echo(f"❌ Sub-task {st.id} failed to start: {cause}", err=True)
            prior_results.append({
                "sub_id": st.id, "status": "failed",
                "summary": str(e), "changed_files": [], "retry_count": 0,
                "duration_sec": round(time.time() - sub_start, 1),
                "estimated_minutes": getattr(st, 'estimated_minutes', 0),
            })
            failed_ids.add(st.id)
            return None

        self.show_waiting(self.app, sub_config)
        if self.no_watch:
            click.echo(f"📌 Sub-task {st.id}: 等待手动 my done")
            click.echo("⚠️  --no-watch 模式下 --decompose 只执行第一步分解。")
            click.echo("   后续请逐个手动执行各子任务。")
            self.save_yaml(self.parent_task_id, {
                "task_id": self.parent_task_id, "status": "decomposed",
                "sub_tasks": [s.model_dump() for s in sorted_tasks],
            })
            return "return"

        self.watch_loop(self.app, sub_config, sub_task_id, manage_lock=False)

        result = _collect_sub_result(self.app, sub_config, st, sub_start)
        sub_status = result["status"]
        prior_results.append(result)
        completed_ids.add(st.id)
        self.save_ckpt(self.parent_task_id, prior_results, list(completed_ids))

        done_count2 = len([r for r in prior_results if r["status"] in ("approved", "completed", "skipped")])
        pct2 = int(done_count2 / total * 100)
        if sub_status in ("approved", "completed"):
            click.echo(f"[{i}/{total}] ✅ {st.id} 完成 ({pct2}%)")
            return None

        return self._handle_failure(
            st, sub_start, prior_results, completed_ids, failed_ids,
        )

    def _setup_subtask_workspace(self, st: Any, subtask_id: str) -> None:
        """Copy global TASK.md to subtask workspace for isolated parallel execution."""
        from multi_agent.config import subtask_outbox_dir, subtask_task_file, workspace_dir

        # Ensure subtask dirs exist
        subtask_outbox_dir(subtask_id).mkdir(parents=True, exist_ok=True)

        # Copy TASK.md from global workspace to subtask workspace
        global_task = workspace_dir() / "TASK.md"
        sub_task = subtask_task_file(subtask_id)
        if global_task.exists():
            shutil.copy2(str(global_task), str(sub_task))

    def _run_one_parallel(
        self, i: int, total: int, st: Any, subtask_id: str,
        sub_config: dict[str, Any], sub_task_id: str, sub_start: float,
    ) -> dict[str, Any]:
        """Execute one sub-task's agent dispatch + watch loop (thread-safe).

        Called from run_group_parallel() in a worker thread.
        Returns the sub-task result dict.
        """
        # Dispatch agent with subtask isolation
        self.show_waiting(self.app, sub_config, subtask_id=subtask_id, visible=self.visible)

        if self.no_watch:
            return {
                "sub_id": st.id, "status": "pending",
                "summary": "no-watch mode", "changed_files": [],
                "retry_count": 0, "duration_sec": 0,
                "estimated_minutes": getattr(st, 'estimated_minutes', 0),
            }

        # Watch subtask-specific outbox
        self.watch_loop(self.app, sub_config, sub_task_id, manage_lock=False, subtask_id=subtask_id, visible=self.visible)
        return _collect_sub_result(self.app, sub_config, st, sub_start)

    def _prepare_group(
        self, group: list[Any],
        prior_results: list[dict[str, Any]],
        completed_ids: set[str], failed_ids: set[str],
    ) -> list[tuple[Any, str, dict[str, Any], str, float]]:
        """Phase A of parallel execution: sequentially start each subtask and copy TASK.md."""
        prepared: list[tuple[Any, str, dict[str, Any], str, float]] = []
        for st in group:
            if st.id in completed_ids:
                click.echo(f"  ⏩ {st.id} 已完成 (checkpoint)")
                continue
            skipped_deps = [d for d in st.deps if d in failed_ids]
            if skipped_deps:
                click.echo(f"  ⏭️ {st.id} 跳过 (依赖 {', '.join(skipped_deps)} 失败)")
                prior_results.append({
                    "sub_id": st.id, "status": "skipped",
                    "summary": f"Skipped: dependency {', '.join(skipped_deps)} failed",
                    "changed_files": [], "retry_count": 0, "duration_sec": 0,
                    "estimated_minutes": getattr(st, 'estimated_minutes', 0),
                })
                failed_ids.add(st.id)
                continue

            sub_start = time.time()
            self.clear_rt()
            sub_state = self.build_state(
                sub_task=st, parent_task_id=self.parent_task_id,
                builder=self.builder, reviewer=self.reviewer,
                timeout=self.timeout, retry_budget=self.retry_budget,
                prior_results=prior_results,
                workflow_mode=self.workflow_mode, review_policy=self.review_policy,
            )
            sub_task_id = sub_state["task_id"]
            sub_config = self.make_config(sub_task_id)
            subtask_id = st.id

            try:
                self.start_task(self.app, sub_task_id, sub_state)
            except self.start_error as e:
                cause = getattr(e, "cause", e)
                click.echo(f"  ❌ {st.id} 启动失败: {cause}", err=True)
                prior_results.append({
                    "sub_id": st.id, "status": "failed",
                    "summary": str(e), "changed_files": [], "retry_count": 0,
                    "duration_sec": round(time.time() - sub_start, 1),
                    "estimated_minutes": getattr(st, 'estimated_minutes', 0),
                })
                failed_ids.add(st.id)
                continue

            self._setup_subtask_workspace(st, subtask_id)
            click.echo(f"  📦 {st.id}: 已准备")
            prepared.append((st, subtask_id, sub_config, sub_task_id, sub_start))
        return prepared

    def run_group_parallel(
        self, group: list[Any], start_idx: int, total: int,
        prior_results: list[dict[str, Any]],
        completed_ids: set[str], failed_ids: set[str],
    ) -> None:
        """Run a group of independent sub-tasks in parallel.

        1. Sequentially: start each task's graph (writes TASK.md) → copy to subtask workspace
        2. In parallel: dispatch agents + watch subtask outboxes
        3. Collect all results
        """
        done_count = len([r for r in prior_results if r["status"] in ("approved", "completed", "skipped")])
        pct = int(done_count / total * 100)
        group_ids = ", ".join(st.id for st in group)
        click.echo(f"\n{'='*60}")
        click.echo(f"  🔀 并行执行 ({len(group)} 个子任务): {group_ids} ({pct}%)")
        click.echo(f"{'='*60}")

        prepared = self._prepare_group(group, prior_results, completed_ids, failed_ids)
        if not prepared:
            return

        # Phase B: Dispatch agents and watch in parallel
        click.echo(f"\n  🚀 并行启动 {len(prepared)} 个 Agent…")
        results_lock = threading.Lock()

        def _worker(st: Any, subtask_id: str, sub_config: dict[str, Any],
                     sub_task_id: str, sub_start: float) -> tuple[Any, dict[str, Any]]:
            result = self._run_one_parallel(
                start_idx, total, st, subtask_id, sub_config, sub_task_id, sub_start,
            )
            return st, result

        with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
            futures = {
                pool.submit(_worker, st, sid, cfg, tid, t0): st.id
                for st, sid, cfg, tid, t0 in prepared
            }
            for future in as_completed(futures):
                st_id = futures[future]
                try:
                    st, result = future.result()
                    sub_status = result["status"]
                    with results_lock:
                        prior_results.append(result)
                        if sub_status in ("approved", "completed"):
                            completed_ids.add(st.id)
                            click.echo(f"  ✅ {st.id} 完成")
                        else:
                            failed_ids.add(st.id)
                            click.echo(f"  ❌ {st.id} 失败 ({sub_status})")
                except Exception as exc:
                    click.echo(f"  ❌ {st_id} 异常: {exc}", err=True)
                    with results_lock:
                        failed_ids.add(st_id)
                        prior_results.append({
                            "sub_id": st_id, "status": "failed",
                            "summary": str(exc), "changed_files": [],
                            "retry_count": 0, "duration_sec": 0,
                        })

        # Save checkpoint after group completes
        self.save_ckpt(self.parent_task_id, prior_results, list(completed_ids))

        # Close visible terminals before cleaning up workspaces
        if self.visible:
            from multi_agent.driver import close_visible_terminal
            for _st, subtask_id, _, _, _ in prepared:
                with contextlib.suppress(Exception):
                    close_visible_terminal(subtask_id=subtask_id)
            import time as _time
            _time.sleep(1)  # give wrapper scripts time to see .done and exit

        # Cleanup subtask workspaces
        from multi_agent.config import subtask_workspace
        for _st, subtask_id, _, _, _ in prepared:
            with contextlib.suppress(OSError):
                shutil.rmtree(str(subtask_workspace(subtask_id)), ignore_errors=True)

    def _handle_failure(
        self, st: Any, sub_start: float,
        prior_results: list[dict[str, Any]],
        completed_ids: set[str], failed_ids: set[str],
    ) -> str | None:
        """Handle a failed sub-task: prompt user for retry/skip/abort."""
        sub_status = prior_results[-1]["status"]
        if not self.auto_confirm:
            click.echo(f"\n❌ Sub-task {st.id} 失败 (状态: {sub_status})")
            choice = click.prompt(
                "选择操作", type=click.Choice(["skip", "retry", "abort"]),
                default="skip",
            )
            if choice == "retry":
                click.echo(f"🔄 重试 Sub-task {st.id}…")
                self.clear_rt()
                prior_results.pop()
                retry_result = _retry_sub_task(
                    self.app, st, self.parent_task_id,
                    self.builder, self.reviewer,
                    self.timeout, self.retry_budget, prior_results,
                    self.workflow_mode, self.review_policy, sub_start,
                    self.make_config, self.build_state, self.start_task,
                    self.start_error, self.show_waiting, self.watch_loop,
                )
                prior_results.append(retry_result)
                if retry_result["status"] not in ("approved", "completed"):
                    failed_ids.add(st.id)
                else:
                    completed_ids.add(st.id)
                    self.save_ckpt(self.parent_task_id, prior_results, list(completed_ids))
                return None
            elif choice == "abort":
                click.echo("⏹️  终止 decompose 流程，保存已完成结果。")
                failed_ids.add(st.id)
                return "break"
        failed_ids.add(st.id)
        return None


def _finalize_decompose(
    parent_task_id: str,
    prior_results: list[dict[str, Any]],
    decompose_start: float,
    aggregate_fn: Any,
    save_yaml_fn: Any,
    clear_ckpt_fn: Any,
    release_lock_fn: Any,
    clear_runtime_fn: Any,
) -> None:
    """Phase 4: Aggregate results, write report, and clean up."""
    from multi_agent.config import workspace_dir
    from multi_agent.meta_graph import generate_aggregate_report

    click.echo(f"\n{'='*60}")
    click.echo("  📊 汇总结果")
    click.echo(f"{'='*60}")

    agg = aggregate_fn(parent_task_id, prior_results)

    click.echo(f"  总子任务: {agg['total_sub_tasks']}")
    click.echo(f"  完成: {agg['completed']}")
    click.echo(f"  总重试: {agg['total_retries']}")
    if agg["failed"]:
        click.echo(f"  ❌ 失败: {', '.join(agg['failed'])}")
    else:
        click.echo("  ✅ 全部通过")
    click.echo(f"  修改文件: {', '.join(agg['all_changed_files']) or '无'}")

    report_text = generate_aggregate_report(agg)
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

    save_yaml_fn(parent_task_id, {
        "task_id": parent_task_id, "status": agg["final_status"],
        "sub_results": prior_results,
    })
    clear_ckpt_fn(parent_task_id)
    release_lock_fn()
    clear_runtime_fn()


def _retry_sub_task(
    app: Any, st: Any, parent_task_id: str,
    builder: str, reviewer: str, timeout: int, retry_budget: int,
    prior_results: list[dict[str, Any]],
    workflow_mode: str, review_policy: Any, sub_start: float,
    make_config_fn: Any, build_state_fn: Any, start_fn: Any,
    start_error_cls: type[BaseException], show_waiting_fn: Any, watch_loop_fn: Any,
) -> dict[str, Any]:
    """Retry a failed sub-task once and return the collected result."""
    sub_state = build_state_fn(
        sub_task=st, parent_task_id=parent_task_id,
        builder=builder, reviewer=reviewer,
        timeout=timeout, retry_budget=retry_budget,
        prior_results=prior_results,
        workflow_mode=workflow_mode, review_policy=review_policy,
    )
    sub_config = make_config_fn(sub_state["task_id"])
    try:
        start_fn(app, sub_state["task_id"], sub_state)
    except start_error_cls as e:
        cause = getattr(e, "cause", e)
        return {
            "sub_id": st.id, "status": "failed",
            "summary": f"Retry start failed: {cause}",
            "changed_files": [], "retry_count": 0,
            "duration_sec": round(time.time() - sub_start, 1),
            "estimated_minutes": getattr(st, 'estimated_minutes', 0),
        }
    show_waiting_fn(app, sub_config)
    watch_loop_fn(app, sub_config, sub_state["task_id"], manage_lock=False)
    return _collect_sub_result(app, sub_config, st, sub_start)


def _validate_and_sort(
    decompose_result: Any, release_lock_fn: Any, clear_runtime_fn: Any,
) -> Any | None:
    """Validate and topologically sort sub-tasks. Returns sorted list or None if empty."""
    from multi_agent.decompose import topo_sort, validate_decompose_result

    validation_errors = validate_decompose_result(decompose_result)
    if validation_errors:
        click.echo("⚠️  分解结果存在问题:", err=True)
        for ve in validation_errors:
            click.echo(f"   - {ve}", err=True)

    try:
        sorted_tasks = topo_sort(decompose_result.sub_tasks)
    except ValueError as e:
        click.echo(f"❌ 分解结果无效: {e}", err=True)
        release_lock_fn()
        clear_runtime_fn()
        sys.exit(1)

    return sorted_tasks if sorted_tasks else None


def _display_sub_tasks(decompose_result: Any, sorted_tasks: Any) -> None:
    """Display decomposed sub-tasks with parallel group info."""
    from multi_agent.decompose import topo_sort_grouped

    click.echo(f"\n✅ 分解完成: {len(sorted_tasks)} 个子任务")
    if decompose_result.reasoning:
        click.echo(f"   理由: {decompose_result.reasoning}")

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


def _load_decompose_checkpoint(
    parent_task_id: str,
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Load checkpoint for crash recovery. Returns (prior_results, completed_ids, failed_ids)."""
    from multi_agent.meta_graph import load_checkpoint

    ckpt = load_checkpoint(parent_task_id)
    prior_results: list[dict[str, Any]] = ckpt["prior_results"] if ckpt else []
    completed_ids: set[str] = set(ckpt["completed_ids"]) if ckpt else set()
    failed_ids: set[str] = set()
    if ckpt:
        click.echo(f"💾 恢复 checkpoint: {len(completed_ids)} 个子任务已完成")
        for pr in prior_results:
            if pr.get("status") not in ("approved", "completed", "skipped"):
                failed_ids.add(pr["sub_id"])
    return prior_results, completed_ids, failed_ids


def _run_decomposed(
    app: Any,
    parent_task_id: str,
    requirement: str,
    skill: str,
    builder: str,
    reviewer: str,
    retry_budget: int,
    timeout: int,
    no_watch: bool,
    workflow_mode: str,
    review_policy: Any,
    *,
    auto_confirm: bool = False,
    decompose_file: str | None = None,
    no_cache: bool = False,
    visible: bool = False,
) -> None:
    """Decompose → grouped parallel/sequential sub-task build-review cycles → aggregate."""
    from multi_agent.cli import (  # type: ignore[attr-defined]
        _make_config,
        _run_single_task,
        _run_watch_loop,
        _show_waiting,
    )
    from multi_agent.decompose import topo_sort_grouped
    from multi_agent.meta_graph import aggregate_results, build_sub_task_state
    from multi_agent.orchestrator import TaskStartError, start_task
    from multi_agent.workspace import clear_runtime, release_lock, save_task_yaml

    click.echo(f"🧩 Task Decomposition: {parent_task_id}")
    click.echo(f"   {requirement}")
    click.echo()

    save_task_yaml(parent_task_id, {
        "task_id": parent_task_id, "status": "active", "mode": "decompose",
    })

    decompose_result = _obtain_decompose_result(
        requirement, skill, builder, timeout,
        decompose_file=decompose_file, no_cache=no_cache,
    )
    if decompose_result is None:
        return

    # Phase 2: Validate, sort, display, confirm
    sorted_tasks = _validate_and_sort(decompose_result, release_lock, clear_runtime)
    if sorted_tasks is None:
        click.echo("⚠️  分解结果为空，降级为单任务模式")
        _run_single_task(app, parent_task_id, requirement, skill, builder, reviewer,
                         retry_budget, timeout, no_watch, workflow_mode, review_policy)
        return

    _display_sub_tasks(decompose_result, sorted_tasks)

    if not auto_confirm and not click.confirm("确认执行这些子任务？", default=True):
        click.echo("⏹️  已取消。可修改 .multi-agent/outbox/decompose.json 后重新运行。")
        release_lock()
        return

    # Phase 3: Load checkpoint for crash recovery
    from multi_agent.meta_graph import clear_checkpoint, save_checkpoint
    prior_results, completed_ids, failed_ids = _load_decompose_checkpoint(parent_task_id)

    total = len(sorted_tasks)
    decompose_start = time.time()

    _exec_ctx = _DecomposeExecContext(
        app=app, parent_task_id=parent_task_id,
        builder=builder, reviewer=reviewer,
        timeout=timeout, retry_budget=retry_budget,
        workflow_mode=workflow_mode, review_policy=review_policy,
        no_watch=no_watch, auto_confirm=auto_confirm,
        make_config=_make_config, build_state=build_sub_task_state,
        start_task=start_task, start_error=TaskStartError,
        show_waiting=_show_waiting, watch_loop=_run_watch_loop,
        save_yaml=save_task_yaml, save_ckpt=save_checkpoint,
        clear_rt=clear_runtime,
        visible=visible,
    )

    # Phase 3b: Execute sub-tasks using parallel groups
    # Groups from topo_sort_grouped: tasks in the same group have no
    # inter-dependencies and can run in parallel.
    try:
        groups = topo_sort_grouped(decompose_result.sub_tasks)
    except ValueError:
        # Fallback to sequential if grouping fails (circular deps handled earlier)
        groups = [[st] for st in sorted_tasks]

    task_idx = 1
    abort = False
    for _group_idx, group in enumerate(groups, 1):
        if abort:
            break

        if len(group) == 1:
            # Single task in group — run sequentially (original path)
            st = group[0]
            action = _exec_ctx.run_one(
                task_idx, total, st, prior_results, completed_ids, failed_ids, sorted_tasks,
            )
            task_idx += 1
            if action == "return":
                return
            if action == "break":
                abort = True
        else:
            # Multiple independent tasks — run in parallel
            _exec_ctx.run_group_parallel(
                group, task_idx, total, prior_results, completed_ids, failed_ids,
            )
            task_idx += len(group)

    # Phase 4: Aggregate & report
    _finalize_decompose(
        parent_task_id, prior_results, decompose_start,
        aggregate_results, save_task_yaml, clear_checkpoint,
        release_lock, clear_runtime,
    )
