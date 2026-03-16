"""Queue runner CLI commands — batch task scheduling and execution.

Registered onto the main Click group via register_queue_commands().

Usage:
    my queue list tasks.md           # List tasks in a queue file
    my queue run tasks.md            # Execute all tasks sequentially
    my queue run tasks.md --start 5  # Start from task #5
    my queue run tasks.md --dry-run  # Preview without executing
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import click


def register_queue_commands(main: click.Group) -> None:  # noqa: C901
    """Attach queue runner commands to the main Click group."""

    from multi_agent.cli import handle_errors

    @main.group()
    def queue() -> None:
        """批量任务队列管理."""

    # ── queue list ───────────────────────────────────────

    @queue.command("list")
    @click.argument("queue_file", type=click.Path(exists=True))
    @handle_errors
    def queue_list(queue_file: str) -> None:
        """列出队列文件中的所有任务."""
        tasks = extract_tasks_from_md(Path(queue_file))
        if not tasks:
            click.echo("⚠️  未找到任务。请确保文件格式正确 (### N. 标题 + ``` 代码块)。")
            return
        click.echo(f"📋 {Path(queue_file).name}: {len(tasks)} 条任务\n")
        for num, title, _prompt in tasks:
            click.echo(f"  [{num:3d}] {title}")

    # ── queue run ────────────────────────────────────────

    @queue.command("run")
    @click.argument("queue_file", type=click.Path(exists=True))
    @click.option("--start", default=1, type=int, help="从第 N 条开始")
    @click.option("--end", default=999, type=int, help="到第 N 条结束")
    @click.option("--only", default=None, type=str, help="只运行指定编号 (逗号分隔)")
    @click.option("--builder", default="windsurf", help="Builder agent")
    @click.option("--reviewer", default="cursor", help="Reviewer agent")
    @click.option("--timeout", default=3600, type=int, help="每个任务超时秒数")
    @click.option("--dry-run", is_flag=True, default=False, help="只预览不执行")
    @click.option("--pause", default=5, type=int, help="任务间暂停秒数")
    @click.option("--parallel", default=1, type=int, help="并行执行数 (1=顺序, >1=并行)")
    @handle_errors
    def queue_run(
        queue_file: str, start: int, end: int, only: str | None,
        builder: str, reviewer: str, timeout: int, dry_run: bool, pause: int,
        parallel: int,
    ) -> None:
        """执行队列文件中的任务."""
        tasks = extract_tasks_from_md(Path(queue_file))
        if not tasks:
            click.echo("⚠️  未找到任务。")
            return

        # Filter tasks
        if only:
            try:
                only_set = {int(x.strip()) for x in only.split(",") if x.strip()}
            except ValueError:
                click.echo("❌ --only 参数格式错误，请使用逗号分隔的数字，如 --only 1,3,5")
                return
            tasks = [t for t in tasks if t[0] in only_set]
        else:
            tasks = [t for t in tasks if start <= t[0] <= end]

        if not tasks:
            click.echo("⚠️  过滤后无任务可执行。")
            return

        click.echo(f"📋 准备执行 {len(tasks)} 条任务 (builder={builder}, reviewer={reviewer})")

        if dry_run:
            for num, title, prompt in tasks:
                click.echo(f"\n  [{num}] {title}")
                click.echo(f"       prompt: {len(prompt)} 字符")
                click.echo(f"       前 80 字: {prompt[:80]}...")
            click.echo(f"\n共 {len(tasks)} 条任务 (dry-run, 未执行)")
            return

        if parallel > 1:
            click.echo("⚠️  并行模式注意: 当前系统一次只能有一个活跃任务 (.lock 限制)。", err=True)
            click.echo("   并行任务可能因 lock 冲突而失败。建议使用 --parallel 1 (默认)。", err=True)
        results = run_queue(tasks, builder, reviewer, timeout, pause, parallel=parallel)
        _print_summary(results)

        # Save results
        from multi_agent.config import workspace_dir
        report_path = workspace_dir() / "queue-results.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo(f"📄 结果保存到: {report_path}")

    # ── queue status ─────────────────────────────────────

    @queue.command("status")
    @handle_errors
    def queue_status() -> None:
        """查看最近一次队列执行结果."""
        from multi_agent.config import workspace_dir
        report_path = workspace_dir() / "queue-results.json"
        if not report_path.exists():
            click.echo("暂无队列执行记录。运行 `my queue run` 开始。")
            return
        results = json.loads(report_path.read_text(encoding="utf-8"))
        click.echo(f"✅ 通过: {len(results.get('passed', []))}")
        click.echo(f"❌ 失败: {len(results.get('failed', []))}")
        if results.get("failed"):
            click.echo(f"   失败编号: {results['failed']}")
        if results.get("elapsed"):
            click.echo(f"⏱️  耗时: {results['elapsed']}")


# ── Core logic (testable without CLI) ────────────────────


_MAX_QUEUE_FILE_SIZE = 10 * 1024 * 1024  # 10 MB cap


def extract_tasks_from_md(md_path: Path) -> list[tuple[int, str, str]]:
    """Extract (number, title, prompt) from a markdown queue file.

    Expected format:
        ### N. Title
        ```
        prompt content
        ```
    """
    try:
        fsize = md_path.stat().st_size
    except OSError:
        fsize = 0
    if fsize > _MAX_QUEUE_FILE_SIZE:
        raise click.ClickException(
            f"Queue file too large: {fsize} bytes > {_MAX_QUEUE_FILE_SIZE} limit"
        )
    text = md_path.read_text(encoding="utf-8")
    pattern = r"### (\d+)\.\s+(.+?)\n\n?```\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    tasks: list[tuple[int, str, str]] = []
    for num_str, title, prompt in matches:
        tasks.append((int(num_str), title.strip(), prompt.strip()))
    return tasks


def run_single_queue_task(
    num: int, title: str, prompt: str,
    builder: str, reviewer: str, timeout: int,
) -> dict[str, Any]:
    """Execute a single queued task. Returns result dict."""
    task_id = f"task-queue-{num:03d}"
    click.echo(f"\n{'=' * 60}")
    click.echo(f"[{num}] 🚀 {title}")
    click.echo(f"  task_id: {task_id} | builder: {builder} | reviewer: {reviewer}")
    click.echo(f"{'=' * 60}\n")

    start_time = time.time()
    # Sanitize prompt: strip null bytes and limit length for CLI arg safety
    sanitized_prompt = prompt.replace("\x00", "")[:100_000]
    cmd = ["my", "go", sanitized_prompt, "--task-id", task_id, "--builder", builder, "--reviewer", reviewer]

    try:
        result = subprocess.run(cmd, timeout=timeout)
        elapsed = time.time() - start_time
        success = result.returncode == 0
        status = "passed" if success else "failed"
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        click.echo(f"  ⏰ TIMEOUT after {timeout}s")
        with contextlib.suppress(Exception):
            subprocess.run(["my", "cancel"], capture_output=True, timeout=30)
        status = "timeout"
        success = False
    except KeyboardInterrupt:
        click.echo(f"\n  🛑 User interrupted at task #{num}")
        with contextlib.suppress(Exception):
            subprocess.run(["my", "cancel"], capture_output=True, timeout=30)
        raise

    return {
        "num": num,
        "title": title,
        "task_id": task_id,
        "status": status,
        "elapsed_sec": round(elapsed, 1),
    }


def _format_elapsed(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    hours, rem = divmod(int(seconds), 3600)
    mins, secs = divmod(rem, 60)
    return f"{hours}h {mins}m {secs}s"


def _progress_bar(done: int, total: int, width: int = 30) -> str:
    """Render a text progress bar."""
    pct = done / total if total > 0 else 0
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {done}/{total} ({pct:.0%})"


def run_queue(
    tasks: list[tuple[int, str, str]],
    builder: str, reviewer: str, timeout: int, pause: int,
    *, parallel: int = 1,
) -> dict[str, Any]:
    """Run a list of queued tasks. Returns results summary.

    Args:
        parallel: Number of concurrent tasks (1 = sequential, >1 = parallel).
    """
    passed: list[int] = []
    failed: list[int] = []
    details: list[dict[str, Any]] = []
    start_time = time.time()
    total = len(tasks)

    if parallel > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        click.echo(f"🔀 并行模式: {parallel} 个任务同时执行\n")
        futures = {}
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for num, title, prompt in tasks:
                f = pool.submit(run_single_queue_task, num, title, prompt, builder, reviewer, timeout)
                futures[f] = num
            for f in as_completed(futures):
                result = f.result()
                details.append(result)
                if result["status"] == "passed":
                    passed.append(result["num"])
                else:
                    failed.append(result["num"])
                done = len(passed) + len(failed)
                click.echo(f"  {_progress_bar(done, total)}  #{result['num']} {result['status']}")
    else:
        for i, (num, title, prompt) in enumerate(tasks):
            result = run_single_queue_task(num, title, prompt, builder, reviewer, timeout)
            details.append(result)
            if result["status"] == "passed":
                passed.append(num)
            else:
                failed.append(num)
            done = len(passed) + len(failed)
            click.echo(f"  {_progress_bar(done, total)}")
            if i < total - 1 and pause > 0:
                time.sleep(pause)

    details.sort(key=lambda d: d["num"])
    return {
        "passed": passed,
        "failed": failed,
        "details": details,
        "elapsed": _format_elapsed(time.time() - start_time),
        "total": total,
    }


def _print_summary(results: dict[str, Any]) -> None:
    """Print execution summary to terminal."""
    click.echo(f"\n{'=' * 60}")
    click.echo("📊 队列执行完成")
    click.echo(f"  ✅ 通过: {len(results['passed'])}")
    click.echo(f"  ❌ 失败: {len(results['failed'])}")
    if results["failed"]:
        click.echo(f"  失败编号: {results['failed']}")
    click.echo(f"  ⏱️  总耗时: {results['elapsed']}")
    click.echo(f"{'=' * 60}")
