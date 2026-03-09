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
        click.echo('  my go "实现用户登录功能"')

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
        """检查 workspace 健康状态 + 配置验证 + agent 可用性."""
        import contextlib

        from multi_agent.workspace import check_workspace_health, get_workspace_stats

        total_checks = 0
        total_ok = 0
        all_issues: list[str] = []

        # ── 1. Workspace Health ──
        click.echo("🔍 [1/5] Workspace 检查…")
        issues = check_workspace_health()
        stats = get_workspace_stats()
        click.echo(f"   📊 {stats['file_count']} 文件, {stats['total_size_mb']} MB")
        if stats["largest_file"]:
            click.echo(f"   最大文件: {stats['largest_file']}")
        total_checks += 1
        if not issues:
            click.echo("   ✅ Workspace 正常")
            total_ok += 1
        else:
            click.echo(f"   ⚠️  {len(issues)} 个问题")
            all_issues.extend(issues)

        # ── 2. Config Validation ──
        click.echo("🔍 [2/5] 配置文件验证…")
        total_checks += 1
        config_ok = True
        try:
            from multi_agent.config import load_project_config, VALID_CONFIG_KEYS
            proj = load_project_config()
            if not proj:
                click.echo("   ⚠️  未找到 .ma.yaml 配置文件")
                config_ok = False
            else:
                unknown_keys = set(proj.keys()) - set(VALID_CONFIG_KEYS)
                if unknown_keys:
                    click.echo(f"   ⚠️  未知配置键: {', '.join(sorted(unknown_keys))}")
                    all_issues.append(f"Unknown config keys: {', '.join(sorted(unknown_keys))}")
                    config_ok = False
                # Validate skill contracts
                skill_count = 0
                skill_errors = 0
                with contextlib.suppress(Exception):
                    from multi_agent.config import root_dir
                    skills_dir = root_dir() / "skills"
                    if skills_dir.exists():
                        for sd in skills_dir.iterdir():
                            if sd.is_dir() and (sd / "contract.yaml").exists():
                                skill_count += 1
                                try:
                                    from multi_agent.contract import load_contract
                                    load_contract(sd.name)
                                except Exception as e:
                                    skill_errors += 1
                                    all_issues.append(f"Skill '{sd.name}' contract error: {e}")
                    click.echo(f"   📋 {skill_count} skills, {skill_errors} errors")
                if config_ok and skill_errors == 0:
                    click.echo("   ✅ 配置正常")
                    total_ok += 1
        except Exception as e:
            click.echo(f"   ❌ 配置加载失败: {e}")
            all_issues.append(f"Config load failed: {e}")

        # ── 3. Agent Availability ──
        click.echo("🔍 [3/5] Agent 可用性…")
        total_checks += 1
        agents_ok = True
        try:
            from multi_agent.router import load_agents
            agents = load_agents()
            if not agents:
                click.echo("   ⚠️  未配置任何 agent")
                all_issues.append("No agents configured")
                agents_ok = False
            else:
                for ag in agents:
                    driver_str = getattr(ag, "driver", "file")
                    avail = "✅"
                    if driver_str == "cli":
                        cmd = getattr(ag, "command", None)
                        if cmd:
                            import shutil
                            prog = cmd.split()[0] if isinstance(cmd, str) else cmd[0]
                            if not shutil.which(prog):
                                avail = "⚠️  CLI 不可用"
                                all_issues.append(f"Agent '{ag.id}' CLI not found: {prog}")
                                agents_ok = False
                    click.echo(f"   {avail} {ag.id} (driver={driver_str})")
            if agents_ok:
                total_ok += 1
        except Exception as e:
            click.echo(f"   ❌ Agent 加载失败: {e}")
            all_issues.append(f"Agent load failed: {e}")

        # ── 4. Memory Integrity ──
        click.echo("🔍 [4/5] 语义记忆完整性…")
        total_checks += 1
        try:
            from multi_agent.semantic_memory import stats as mem_stats
            ms = mem_stats()
            total_mem = ms.get("total_entries", 0)
            click.echo(f"   📝 {total_mem} entries, {ms.get('by_category', {})}")
            if ms.get("file_exists") and total_mem == 0:
                click.echo("   ⚠️  记忆文件存在但为空")
                all_issues.append("Memory file exists but is empty")
            else:
                click.echo("   ✅ 记忆正常")
                total_ok += 1
        except Exception as e:
            click.echo(f"   ❌ 记忆检查失败: {e}")
            all_issues.append(f"Memory check failed: {e}")

        # ── 5. Webhook Connectivity ──
        click.echo("🔍 [5/5] Webhook 连通性…")
        total_checks += 1
        try:
            from multi_agent.notify import load_notify_config
            ncfg = load_notify_config()
            if not ncfg.webhook_url:
                click.echo("   ⏭️  未配置 webhook (跳过)")
                total_ok += 1
            else:
                click.echo(f"   🔗 {ncfg.webhook_url[:60]}…")
                click.echo(f"   格式: {ncfg.webhook_format}, 重试: {ncfg.webhook_retries}")
                # Validate URL
                from urllib.parse import urlparse
                parsed = urlparse(ncfg.webhook_url)
                if parsed.scheme not in ("http", "https"):
                    click.echo("   ❌ URL scheme 必须是 http 或 https")
                    all_issues.append(f"Webhook URL invalid scheme: {parsed.scheme}")
                else:
                    click.echo("   ✅ Webhook 配置正常 (连通性需 --fix 测试)")
                    total_ok += 1
        except Exception as e:
            click.echo(f"   ❌ Webhook 检查失败: {e}")
            all_issues.append(f"Webhook check failed: {e}")

        # ── Summary ──
        click.echo(f"\n{'='*40}")
        click.echo(f"  结果: {total_ok}/{total_checks} 通过")
        if all_issues:
            click.echo(f"  ⚠️  {len(all_issues)} 个问题:")
            for issue in all_issues:
                click.echo(f"     - {issue}")
        else:
            click.echo("  ✅ 全部正常!")
        click.echo()

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
                fsize = history_file.stat().st_size
                if fsize > 10 * 1024 * 1024:  # 10 MB cap
                    result["conversation"] = [{"_error": f"file too large ({fsize} bytes)"}]
                else:
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

        fsize = history_file.stat().st_size
        if fsize > 10 * 1024 * 1024:  # 10 MB cap
            click.echo(f"❌ 历史文件过大: {fsize} bytes > 10 MB", err=True)
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

    # ── stats ────────────────────────────────────────────

    def _collect_task_stats(cutoff: float) -> tuple[int, dict[str, int]]:
        """Scan task YAMLs and return (total, status_counts)."""
        import yaml as _yaml

        from multi_agent.config import tasks_dir
        td = tasks_dir()
        if not td.exists():
            return 0, {}
        status_counts: dict[str, int] = {}
        total = 0
        for f in td.glob("*.yaml"):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                data = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            total += 1
            s = data.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
        return total, status_counts

    def _collect_timing_stats(cutoff: float) -> dict[str, list[int]]:
        """Parse timing JSONL logs and return {node: [duration_ms...]}."""
        import json as _json

        from multi_agent.config import workspace_dir
        logs_dir = workspace_dir() / "logs"
        node_stats: dict[str, list[int]] = {}
        if not logs_dir.exists():
            return node_stats
        for tf in logs_dir.glob("timing-*.jsonl"):
            try:
                if tf.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            try:
                for line in tf.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = _json.loads(line)
                        node_stats.setdefault(entry.get("node", "?"), []).append(entry.get("duration_ms", 0))
                    except _json.JSONDecodeError:
                        continue
            except Exception:
                continue
        return node_stats

    def _fmt_ms(ms: float) -> str:
        return f"{ms/1000:.1f}s" if ms > 1000 else f"{int(ms)}ms"

    @main.command()
    @handle_errors
    @click.option("--days", default=30, type=int, help="统计最近 N 天的任务")
    def stats(days: int) -> None:
        """显示任务执行统计报告."""
        import time as _time

        cutoff = _time.time() - days * 86400
        total, status_counts = _collect_task_stats(cutoff)
        if total == 0:
            click.echo("暂无任务数据")
            return

        approved = status_counts.get("approved", 0) + status_counts.get("done", 0)
        failed = status_counts.get("failed", 0)
        cancelled = status_counts.get("cancelled", 0)
        escalated = status_counts.get("escalated", 0)
        active = status_counts.get("active", 0)
        success_rate = (approved / total * 100) if total > 0 else 0

        click.echo(f"\n📊 MyGO 任务统计 (最近 {days} 天)\n")
        click.echo(f"  总任务数: {total}")
        click.echo(f"  ✅ 通过: {approved}  ❌ 失败: {failed}  ⚠️ 升级: {escalated}")
        click.echo(f"  🛑 取消: {cancelled}  🔵 进行中: {active}")
        click.echo(f"  📈 成功率: {success_rate:.1f}%")
        other = total - approved - failed - cancelled - escalated - active
        if other > 0:
            click.echo(f"  ⚪ 其他: {other}")

        node_stats = _collect_timing_stats(cutoff)
        if node_stats:
            click.echo("\n⏱️  节点耗时统计:\n")
            click.echo(f"  {'节点':<12} {'次数':>6} {'平均':>8} {'最大':>8} {'总计':>10}")
            click.echo(f"  {'─'*12} {'─'*6} {'─'*8} {'─'*8} {'─'*10}")
            for node in ["plan", "build", "review", "decide"]:
                vals = node_stats.get(node, [])
                if not vals:
                    continue
                count = len(vals)
                avg_ms = sum(vals) / count
                click.echo(f"  {node:<12} {count:>6} {_fmt_ms(avg_ms):>8} {_fmt_ms(max(vals)):>8} {_fmt_ms(sum(vals)):>10}")
        click.echo()

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

    # ── finops ──────────────────────────────────────────

    @main.command("finops")
    @click.option("--task-id", default=None, help="Filter by task ID")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    @handle_errors
    def finops_cmd(task_id: str | None, as_json: bool) -> None:
        """Token 用量与成本报告（FinOps）."""
        import json as _json

        from multi_agent.finops import aggregate_usage, check_budget, format_report

        if task_id:
            _validate_task_id(task_id)

        agg = aggregate_usage(task_id=task_id)

        if as_json:
            click.echo(_json.dumps(agg, ensure_ascii=False, indent=2))
            return

        click.echo(format_report(agg))

        # Budget check
        budget = check_budget()
        if budget["over_budget"]:
            for w in budget["warnings"]:
                click.echo(f"  ⚠️  {w}", err=True)

    # ── memory ───────────────────────────────────────────
    @main.command("memory")
    @click.argument("action", type=click.Choice(["search", "add", "list", "stats", "delete", "clear"]))
    @click.argument("text", required=False, default="")
    @click.option("--category", "-c", default="general", help="Memory category")
    @click.option("--top-k", "-k", default=5, type=int, help="Max results for search")
    @click.option("--tags", "-t", default="", help="Comma-separated tags")
    @click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
    @handle_errors
    def memory_cmd(action: str, text: str, category: str, top_k: int, tags: str, as_json: bool) -> None:
        """Semantic memory — store and retrieve cross-task knowledge.

        Actions:
          search  Search memories by natural language query
          add     Store a new memory entry
          list    List stored memories
          stats   Show memory statistics
          delete  Delete a memory entry by ID
          clear   Clear all memories (or by category)

        Examples:
          my memory search "authentication pattern"
          my memory add "Use JWT tokens for API auth" -c architecture -t auth,jwt
          my memory list -c convention
          my memory stats
          my memory delete abc123def456
          my memory clear -c bugfix
        """
        from multi_agent.semantic_memory import (
            clear as mem_clear,
            delete as mem_delete,
            list_entries,
            search as mem_search,
            stats as mem_stats,
            store as mem_store,
        )

        ensure_workspace()

        if action == "search":
            if not text:
                click.echo("Usage: my memory search \"query text\"", err=True)
                return
            results = mem_search(text, top_k=top_k, category=category if category != "general" else None)
            if as_json:
                click.echo(json.dumps(results, indent=2, ensure_ascii=False))
                return
            if not results:
                click.echo("  No matching memories found.")
                return
            click.echo(f"  Found {len(results)} result(s):\n")
            for r in results:
                e = r["entry"]
                score = r["score"]
                cat = e.get("category", "general")
                tags_str = ", ".join(e.get("tags", []))
                click.echo(f"  [{cat}] (score: {score:.2f}) {e['content']}")
                if tags_str:
                    click.echo(f"         tags: {tags_str}")
                click.echo()

        elif action == "add":
            if not text:
                click.echo("Usage: my memory add \"memory content\" [-c category] [-t tag1,tag2]", err=True)
                return
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            result = mem_store(text, category=category, tags=tag_list, source="cli")
            if as_json:
                click.echo(json.dumps(result, indent=2))
            else:
                status = result.get("status", "error")
                if status == "stored":
                    click.echo(f"  ✅ Stored (id: {result['entry_id']}, total: {result['count']})")
                elif status == "duplicate":
                    click.echo(f"  ⚠️  Duplicate (id: {result['entry_id']})")
                else:
                    click.echo(f"  ❌ {result.get('reason', 'unknown error')}", err=True)

        elif action == "list":
            cat_filter = category if category != "general" else None
            entries = list_entries(category=cat_filter)
            if as_json:
                click.echo(json.dumps(entries, indent=2, ensure_ascii=False))
                return
            if not entries:
                click.echo("  No memories stored.")
                return
            click.echo(f"  {len(entries)} memorie(s):\n")
            for e in entries:
                cat = e.get("category", "general")
                entry_id = e.get("id", "?")[:8]
                click.echo(f"  {entry_id}  [{cat}] {e.get('content', '')[:80]}")

        elif action == "stats":
            s = mem_stats()
            if as_json:
                click.echo(json.dumps(s, indent=2))
                return
            click.echo(f"  Total entries: {s['total_entries']}")
            for cat, count in sorted(s.get("by_category", {}).items()):
                click.echo(f"    {cat}: {count}")

        elif action == "delete":
            if not text:
                click.echo("Usage: my memory delete <entry_id>", err=True)
                return
            result = mem_delete(text)
            if result["status"] == "deleted":
                click.echo(f"  ✅ Deleted (remaining: {result['remaining']})")
            else:
                click.echo(f"  ❌ Not found: {text}", err=True)

        elif action == "clear":
            cat_filter = category if category != "general" else None
            result = mem_clear(category=cat_filter)
            click.echo(f"  🗑️  Cleared {result.get('removed', 0)} entries")

        elif action == "export":
            from multi_agent.semantic_memory import export_entries
            out_path = text or "memory_export.json"
            count = export_entries(out_path)
            click.echo(f"  📤 Exported {count} entries → {out_path}")

        elif action == "import":
            if not text:
                click.echo("Usage: my memory import <file.json>", err=True)
                return
            from multi_agent.semantic_memory import import_entries
            result = import_entries(text)
            click.echo(f"  📥 Imported {result['imported']} entries ({result['skipped']} duplicates)")

    # ── batch ──────────────────────────────────────────

    @main.command("batch")
    @click.argument("manifest", type=click.Path(exists=True))
    @click.option("--dry-run", is_flag=True, default=False, help="Validate manifest without executing")
    @click.option("--builder", default="", help="Override builder for all tasks")
    @click.option("--reviewer", default="", help="Override reviewer for all tasks")
    @handle_errors
    def batch_cmd(manifest: str, dry_run: bool, builder: str, reviewer: str) -> None:
        """从 YAML 文件批量运行任务.

        Manifest 格式:
          tasks:
            - requirement: "Add login endpoint"
              skill: code-implement
            - requirement: "Write tests"
              template: test
        """
        import time as _time

        from multi_agent.batch import (
            BatchValidationError,
            format_batch_summary,
            load_batch_manifest,
        )

        from pathlib import Path
        path = Path(manifest)
        try:
            tasks = load_batch_manifest(path)
        except (BatchValidationError, FileNotFoundError) as e:
            click.echo(f"❌ {e}", err=True)
            sys.exit(1)

        click.echo(f"📋 Batch: {len(tasks)} 个任务")
        for i, t in enumerate(tasks):
            req = t.get("requirement", t.get("template", "?"))[:60]
            click.echo(f"   {i+1}. {req}")
        click.echo()

        if dry_run:
            click.echo("✅ Dry-run 验证通过，未执行任何任务。")
            return

        from multi_agent.graph import compile_graph

        results: list[dict[str, Any]] = []

        for i, task_def in enumerate(tasks):
            req = task_def.get("requirement", "")
            tmpl_id = task_def.get("template")
            skill = task_def.get("skill", "code-implement")
            task_builder = builder or task_def.get("builder", "")
            task_reviewer = reviewer or task_def.get("reviewer", "")
            retry_budget = task_def.get("retry_budget", 2)
            timeout = task_def.get("timeout", 1800)

            click.echo(f"\n{'─'*40}")
            click.echo(f"🚀 [{i+1}/{len(tasks)}] {req or tmpl_id}")

            t0 = _time.time()
            try:
                # Resolve template if specified
                if tmpl_id:
                    from multi_agent.task_templates import load_template, resolve_variables
                    tmpl = load_template(tmpl_id)
                    tmpl = resolve_variables(tmpl)
                    req = req or tmpl.requirement
                    if skill == "code-implement" and tmpl.skill:
                        skill = tmpl.skill

                if not req:
                    results.append({"requirement": tmpl_id, "status": "skipped",
                                    "error": "No requirement", "elapsed": 0})
                    continue

                from multi_agent.cli import _generate_task_id, _run_single_task
                from multi_agent.session import _resolve_review_policy

                app = compile_graph()
                task_id = _generate_task_id(req)
                review_policy = _resolve_review_policy("strict", "config/workmode.yaml")

                _run_single_task(app, task_id, req, skill, task_builder, task_reviewer,
                                 retry_budget, timeout, False, "strict", review_policy)

                elapsed = _time.time() - t0
                results.append({"requirement": req, "status": "completed",
                                "task_id": task_id, "elapsed": elapsed})
                click.echo(f"   ✅ 完成 ({elapsed:.1f}s)")

            except Exception as e:
                elapsed = _time.time() - t0
                results.append({"requirement": req or str(tmpl_id), "status": "failed",
                                "error": str(e)[:200], "elapsed": elapsed})
                click.echo(f"   ❌ 失败: {e}")
                continue

        click.echo(f"\n{format_batch_summary(results)}")
