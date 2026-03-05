"""Admin / info CLI commands — extracted from cli.py (A2 refactor).

These commands handle introspection, maintenance, and diagnostics:
  history, init, render, cache-stats, schema, cleanup, doctor,
  agents, list-skills, export, replay, version, trace.

Registered onto the main Click group via register_admin_commands().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from pydantic import BaseModel


def register_admin_commands(main: click.Group) -> None:  # noqa: C901
    """Attach all admin/info commands to the main Click group."""

    # Import shared utilities from cli.py — safe because cli.py has already
    # defined these by the time register_admin_commands() is called.
    from multi_agent.cli import (
        _validate_skill_id,
        _validate_task_id,
        handle_errors,
    )
    from multi_agent.workspace import (
        ensure_workspace,
    )

    # ── history ─────────────────────────────────────────

    @main.command()
    @handle_errors
    @click.option("--limit", default=20, type=int, help="Max number of tasks to show")
    @click.option("--status", "filter_status", default=None, help="Filter by status (active/approved/failed/cancelled)")
    def history(limit: int, filter_status: str | None) -> None:
        """查看历史任务记录."""
        import yaml

        from multi_agent.config import tasks_dir

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

    # ── init ────────────────────────────────────────────

    @main.command()
    @handle_errors
    @click.option("--force", is_flag=True, default=False, help="Overwrite existing files")
    def init(force: bool) -> None:
        """初始化 MyGO 项目."""
        from pathlib import Path

        import yaml

        cwd = Path.cwd()

        # Check if already initialized
        skills = cwd / "skills"
        agents_dir = cwd / "agents"
        if skills.exists() and agents_dir.exists() and not force:
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
        agents_dir.mkdir(parents=True, exist_ok=True)
        agents_file = agents_dir / "agents.yaml"
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
        click.echo("  ✅ .multi-agent/")

        click.echo("\n🎉 初始化完成！下一步:")
        click.echo('  ma go "实现用户登录功能"')

    # ── render ──────────────────────────────────────────

    @main.command()
    @click.argument("requirement")
    @click.option("--skill", default="code-implement", help="Skill to use")
    @click.option("--role", default="builder", type=click.Choice(["builder", "reviewer"]),
                  help="Which role's prompt to render")
    @click.option("--builder-output", "builder_output_file", default=None,
                  type=click.Path(exists=True), help="Builder output JSON (required for reviewer role)")
    @handle_errors
    def render(requirement: str, skill: str, role: str, builder_output_file: str | None) -> None:
        """预览 prompt（不执行任何操作）."""
        _validate_skill_id(skill)
        import json
        from pathlib import Path

        from multi_agent.contract import load_contract
        from multi_agent.prompt import render_builder_prompt, render_reviewer_prompt
        from multi_agent.schema import Task

        try:
            contract = load_contract(skill)
        except FileNotFoundError:
            click.echo(f"❌ Skill '{skill}' not found", err=True)
            raise SystemExit(1) from None

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
            with Path(builder_output_file).open(encoding="utf-8") as f:
                builder_output = json.load(f)
            result = render_reviewer_prompt(
                task, contract, agent_id="preview",
                builder_output=builder_output, builder_id="preview-builder",
            )

        click.echo(result)

    # ── cache-stats ─────────────────────────────────────

    @main.command("cache-stats")
    @handle_errors
    def cache_stats() -> None:
        """显示 LRU 缓存命中率."""
        from multi_agent.config import root_dir
        info = root_dir.cache_info()
        click.echo(f"root_dir cache: hits={info.hits}, misses={info.misses}, "
                   f"size={info.currsize}/{info.maxsize}")

    # ── schema ──────────────────────────────────────────

    @main.command()
    @click.argument("model", default="all",
                    type=click.Choice(["all", "Task", "BuilderOutput", "ReviewerOutput",
                                       "SubTask", "DecomposeResult"], case_sensitive=False))
    @handle_errors
    def schema(model: str) -> None:
        """导出 Pydantic 模型的 JSON Schema."""
        import json as _json

        from multi_agent.schema import (
            BuilderOutput,
            DecomposeResult,
            ReviewerOutput,
            SubTask,
            Task,
        )
        models: dict[str, type[BaseModel]] = {
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

    # ── cleanup ─────────────────────────────────────────

    @main.command()
    @click.option("--days", default=7, type=int, help="Max age in days")
    @handle_errors
    def cleanup(days: int) -> None:
        """清理旧的 workspace 文件."""
        from multi_agent.workspace import cleanup_old_files
        deleted = cleanup_old_files(max_age_days=days)
        click.echo(f"已清理 {deleted} 个文件 (>{days} 天)")

    # ── doctor ──────────────────────────────────────────

    @main.command()
    @handle_errors
    @click.option("--fix", is_flag=True, default=False, help="Attempt to auto-fix common state inconsistencies")
    def doctor(fix: bool) -> None:
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

        if fix:
            from multi_agent.cli import _auto_fix_runtime_consistency
            fix_actions = _auto_fix_runtime_consistency()
            if fix_actions:
                click.echo("🛠️  自动修复动作:")
                for action in fix_actions:
                    click.echo(f"   - {action}")
            else:
                click.echo("🛠️  未发现可自动修复的问题")

    # ── agents ──────────────────────────────────────────

    @main.command()
    @handle_errors
    def agents() -> None:
        """显示所有 agent 状态."""
        from multi_agent.router import check_agent_health, load_agents
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

    # ── list-skills ─────────────────────────────────────

    @main.command("list-skills")
    @handle_errors
    def list_skills() -> None:
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

    # ── export ──────────────────────────────────────────

    @main.command()
    @click.argument("task_id")
    @click.option("--format", "fmt", default="json",
                  type=click.Choice(["json", "markdown"]), help="Export format")
    @handle_errors
    def export(task_id: str, fmt: str) -> None:
        """导出任务执行结果."""
        _validate_task_id(task_id)
        import json as _json

        from multi_agent.config import history_dir, tasks_dir
        history_file = history_dir() / f"{task_id}.json"
        task_file = tasks_dir() / f"{task_id}.yaml"

        result: dict[str, Any] = {"task_id": task_id}
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

    # ── replay ──────────────────────────────────────────

    @main.command()
    @click.argument("task_id")
    @click.option("--from-step", "from_step", default=0, type=int, help="Start from step N")
    @handle_errors
    def replay(task_id: str, from_step: int) -> None:
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
            click.echo(f"  [{i}] {role}: {action}")
            if "summary" in entry:
                click.echo(f"       {entry['summary'][:100]}")

        click.echo(f"\n✅ Replay complete ({len(conversation)} total, shown from step {from_step})")

    # ── version ─────────────────────────────────────────

    @main.command()
    @handle_errors
    def version() -> None:
        """显示版本信息."""
        import sys
        from pathlib import Path

        from multi_agent import __version__
        click.echo(f"MyGO v{__version__}")
        click.echo(f"Python {sys.version}")
        click.echo(f"Install: {Path(__file__).parent}")

    # ── trace ───────────────────────────────────────────

    @main.command("trace")
    @click.option("--task-id", required=True, help="Task ID")
    @click.option("--format", "fmt", default="tree", type=click.Choice(["tree", "mermaid"]), help="Trace 输出格式")
    @handle_errors
    def trace_cmd(task_id: str, fmt: str) -> None:
        """输出会话事件轨迹（tree 或 mermaid）."""
        from multi_agent.session import session_trace

        _validate_task_id(task_id)
        click.echo(session_trace(task_id, fmt))
