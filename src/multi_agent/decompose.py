"""Task decomposition — break complex requirements into sub-tasks.

Uses the first available builder agent (via IDE or CLI) to decompose
a complex requirement into independent sub-tasks, each with its own
build-review cycle.

The decomposition result is a DecomposeResult containing SubTask objects
with dependency ordering.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import hashlib

from multi_agent.config import workspace_dir, outbox_dir, inbox_dir
from multi_agent.schema import DecomposeResult, SubTask


DECOMPOSE_PROMPT = """\
# 🧩 任务分解

## 你的身份
- **角色**: Task Decomposer (任务分解器)
- **目标**: 把一个复杂需求拆分成多个独立的、可逐个实现的子任务

## 原始需求
{requirement}

## 规则
1. 每个子任务必须是**独立可实现**的（一次 build-review 能完成）
2. 子任务之间可以有依赖关系（用 deps 字段表示）
3. 每个子任务需要明确的 done_criteria（完成标准）
4. 子任务数量控制在 2-6 个（太少没意义，太多增加开销）
5. 如果需求本身就很简单（单个功能），输出 1 个子任务即可
6. 子任务 ID 使用小写字母和连字符，如 "auth-login"

## 产出要求
输出以下 JSON:

```json
{{
  "sub_tasks": [
    {{
      "id": "subtask-id",
      "description": "要实现什么",
      "done_criteria": ["标准1", "标准2"],
      "acceptance_criteria": ["验收标准1"],
      "deps": [],
      "skill_id": "code-implement",
      "priority": "normal",
      "estimated_minutes": 30
    }}
  ],
  "total_estimated_minutes": 60,
  "reasoning": "为什么这样拆分"
}}
```

字段说明:
- priority: 优先级 ("low" / "normal" / "high")
- estimated_minutes: 预估完成时间（分钟）
- acceptance_criteria: 验收标准列表（比 done_criteria 更具体）
- total_estimated_minutes: 所有子任务预估总时间
"""


DECOMPOSE_PROMPT_EN = """\
# 🧩 Task Decomposition

## Your Role
- **Role**: Task Decomposer
- **Goal**: Break a complex requirement into multiple independent sub-tasks that can be implemented one by one

## Original Requirement
{requirement}

## Rules
1. Each sub-task must be **independently implementable** (completable in a single build-review cycle)
2. Sub-tasks may have dependency relationships (expressed via the deps field)
3. Each sub-task needs clear done_criteria (completion standards)
4. Keep the number of sub-tasks between 2-6 (too few is pointless, too many adds overhead)
5. If the requirement is simple enough (single feature), output just 1 sub-task
6. Sub-task IDs should use lowercase letters and hyphens, e.g. "auth-login"

## Output Format
Output the following JSON:

```json
{{
  "sub_tasks": [
    {{
      "id": "subtask-id",
      "description": "what to implement",
      "done_criteria": ["criterion 1", "criterion 2"],
      "acceptance_criteria": ["acceptance criterion 1"],
      "deps": [],
      "skill_id": "code-implement",
      "priority": "normal",
      "estimated_minutes": 30
    }}
  ],
  "total_estimated_minutes": 60,
  "reasoning": "why this decomposition"
}}
```

Field notes:
- priority: "low" / "normal" / "high"
- estimated_minutes: estimated completion time in minutes
- acceptance_criteria: specific acceptance criteria (more detailed than done_criteria)
- total_estimated_minutes: sum of all sub-task estimated times
"""


def collect_project_context(max_chars: int = 2000) -> str:
    """Collect project context for decomposition prompts.

    Scans src/ for Python files, reads README.md header, and pyproject.toml deps.
    Returns empty string if nothing found. Total size capped at max_chars.
    """
    from multi_agent.config import root_dir

    parts: list[str] = []
    root = root_dir()

    # 1. Python files in src/
    src = root / "src"
    if src.exists():
        py_files = sorted(src.rglob("*.py"))[:20]
        if py_files:
            rel_paths = [str(f.relative_to(root)) for f in py_files]
            parts.append("项目文件:\n" + "\n".join(f"  - {p}" for p in rel_paths))

    # 2. README.md header
    readme = root / "README.md"
    if readme.exists():
        try:
            lines = readme.read_text(encoding="utf-8").splitlines()[:50]
            parts.append("README.md (前50行):\n" + "\n".join(lines))
        except Exception:
            pass

    # 3. pyproject.toml dependencies
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            # Extract dependencies section
            in_deps = False
            dep_lines = []
            for line in text.splitlines():
                if "dependencies" in line.lower():
                    in_deps = True
                    dep_lines.append(line)
                    continue
                if in_deps:
                    if line.strip().startswith("]"):
                        dep_lines.append(line)
                        break
                    dep_lines.append(line)
            if dep_lines:
                parts.append("依赖:\n" + "\n".join(dep_lines))
        except Exception:
            pass

    if not parts:
        return ""

    context = "\n\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n... (已截断)"
    return context


def write_decompose_prompt(requirement: str, *, lang: str = "zh", project_context: str = "") -> Path:
    """Write decomposition prompt to TASK.md for IDE/CLI agent.

    Args:
        requirement: The requirement text to decompose.
        lang: Language for the prompt - "zh" (Chinese) or "en" (English).
        project_context: Optional project context string. If empty, auto-collected.
    """
    template = DECOMPOSE_PROMPT_EN if lang == "en" else DECOMPOSE_PROMPT
    prompt = template.format(requirement=requirement)

    # Inject project context
    if not project_context:
        try:
            project_context = collect_project_context()
        except Exception:
            project_context = ""
    if project_context:
        section_title = "## Project Background" if lang == "en" else "## 项目背景"
        prompt = prompt + f"\n{section_title}\n{project_context}\n"

    outbox_rel = ".multi-agent/outbox/decompose.json"
    outbox_abs = str(outbox_dir() / "decompose.json")

    if lang == "en":
        footer_lines = [
            "> **After completion, save the JSON result to the following path:**",
            f"> `{outbox_rel}`",
            f"> Absolute path: `{outbox_abs}`",
        ]
    else:
        footer_lines = [
            "> **完成后，把上面要求的 JSON 结果保存到以下路径:**",
            f"> `{outbox_rel}`",
            f"> 绝对路径: `{outbox_abs}`",
        ]

    lines = [prompt, "", "---", ""] + footer_lines + [""]

    p = workspace_dir() / "TASK.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")

    # Also write to inbox for consistency
    inbox_p = inbox_dir() / "decompose.md"
    inbox_p.parent.mkdir(parents=True, exist_ok=True)
    inbox_p.write_text(prompt, encoding="utf-8")

    return p


def read_decompose_result(*, validate: bool = True) -> DecomposeResult | None:
    """Read decomposition result from outbox/decompose.json.

    Args:
        validate: If True, runs validate_decompose_result and returns None
                  if there are critical errors (empty sub_tasks, duplicate IDs).
    """
    outbox_file = outbox_dir() / "decompose.json"
    if not outbox_file.exists():
        return None

    try:
        text = outbox_file.read_text(encoding="utf-8")
    except OSError:
        return None

    result = None

    # Primary: standard JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "sub_tasks" in data:
            result = DecomposeResult(**data)
    except (json.JSONDecodeError, Exception):
        pass

    # Fallback: agent may have wrapped JSON in markdown fences
    if result is None:
        result = parse_decompose_json(text)

    if result is not None and validate:
        errors = validate_decompose_result(result)
        if errors:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("Decompose result validation errors: %s", errors)
            # Critical: empty sub_tasks or duplicate IDs (cause key collisions downstream)
            critical = [e for e in errors if ("empty" in e.lower() and "sub_tasks" in e.lower()) or "duplicate" in e.lower()]
            if critical:
                return None

    return result


def parse_decompose_json(text: str) -> DecomposeResult | None:
    """Parse decomposition result from raw text (handles markdown fences)."""
    # Try extracting from ```json ... ```
    match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "sub_tasks" in data:
                return DecomposeResult(**data)
        except (json.JSONDecodeError, Exception):
            pass

    # Try parsing whole text as JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and "sub_tasks" in data:
            return DecomposeResult(**data)
    except (json.JSONDecodeError, Exception):
        pass

    return None


def _cache_dir() -> Path:
    """Return the decompose cache directory."""
    return workspace_dir() / "cache"


def _cache_key(requirement: str) -> str:
    """Generate a cache key from the requirement text."""
    return hashlib.sha256(requirement.strip().encode()).hexdigest()[:16]


def get_cached_decompose(requirement: str) -> DecomposeResult | None:
    """Look up a cached decompose result for the given requirement."""
    cd = _cache_dir()
    key = _cache_key(requirement)
    cache_file = cd / f"decompose-{key}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "sub_tasks" in data:
            return DecomposeResult(**data)
    except Exception:
        pass
    return None


def cache_decompose(requirement: str, result: DecomposeResult) -> Path:
    """Cache a decompose result for future reuse."""
    cd = _cache_dir()
    cd.mkdir(parents=True, exist_ok=True)
    key = _cache_key(requirement)
    cache_file = cd / f"decompose-{key}.json"
    cache_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return cache_file


def diff_decompose_results(old: DecomposeResult, new: DecomposeResult) -> list[str]:
    """Compare two decompose results and return a list of diff descriptions.

    Returns empty list if identical.
    """
    diffs: list[str] = []
    old_ids = {st.id for st in old.sub_tasks}
    new_ids = {st.id for st in new.sub_tasks}

    added = new_ids - old_ids
    removed = old_ids - new_ids
    common = old_ids & new_ids

    for sid in sorted(added):
        diffs.append(f"+ 新增子任务: {sid}")
    for sid in sorted(removed):
        diffs.append(f"- 移除子任务: {sid}")

    old_map = {st.id: st for st in old.sub_tasks}
    new_map = {st.id: st for st in new.sub_tasks}
    for sid in sorted(common):
        o, n = old_map[sid], new_map[sid]
        if o.description != n.description:
            diffs.append(f"~ {sid}: 描述变更")
        if set(o.deps) != set(n.deps):
            diffs.append(f"~ {sid}: 依赖变更 {o.deps} → {n.deps}")
        if o.done_criteria != n.done_criteria:
            diffs.append(f"~ {sid}: 完成标准变更")

    return diffs


def estimate_complexity(requirement: str) -> str:
    """Estimate task complexity based on simple heuristics.

    Returns "simple", "medium", or "complex".
    """
    if not requirement or not requirement.strip():
        return "simple"

    text = requirement.strip()
    length = len(text)

    # Count conjunction words (Chinese and English)
    conjunctions = ["和", "且", "以及", "并且", "同时", "还需要", "另外",
                    " and ", " also ", " additionally "]
    conj_count = sum(text.count(c) for c in conjunctions)

    # Count action verbs (Chinese and English)
    action_verbs = ["实现", "创建", "添加", "修改", "删除", "优化", "重构", "集成", "部署", "配置",
                    "implement", "create", "add", "modify", "delete", "optimize", "refactor",
                    "integrate", "deploy", "configure", "build", "design"]
    verb_count = sum(1 for v in action_verbs if v in text.lower())

    # Structural complexity signals (literature: MASAI structural analysis)
    complex_signals = [
        "数据库", "database", "认证", "auth", "API", "微服务", "microservice",
        "分布式", "distributed", "缓存", "cache", "队列", "queue",
        "websocket", "GraphQL", "OAuth", "JWT", "RBAC",
    ]
    struct_count = sum(1 for s in complex_signals if s.lower() in text.lower())

    if length > 200 or verb_count >= 3 or struct_count >= 2:
        return "complex"
    if length < 50 and conj_count == 0 and struct_count == 0:
        return "simple"
    return "medium"


def validate_decompose_result(result: DecomposeResult) -> list[str]:
    """Validate a decompose result for semantic correctness.

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    # Check sub_tasks count (1-10)
    if len(result.sub_tasks) < 1:
        errors.append("sub_tasks is empty (minimum 1)")
    if len(result.sub_tasks) > 10:
        errors.append(f"too many sub_tasks: {len(result.sub_tasks)} (maximum 10)")

    ids = [st.id for st in result.sub_tasks]

    # Check for duplicate IDs
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            errors.append(f"duplicate sub_task id: '{sid}'")
        seen.add(sid)

    for st in result.sub_tasks:
        # Check empty description
        if not st.description or not st.description.strip():
            errors.append(f"sub_task '{st.id}' has empty description")
        # Check deps reference valid IDs
        for dep in st.deps:
            if dep not in seen:
                errors.append(f"sub_task '{st.id}' depends on unknown id '{dep}'")
        # Check self-dependency
        if st.id in st.deps:
            errors.append(f"sub_task '{st.id}' depends on itself")

    # Check circular dependencies (DFS cycle detection)
    adj: dict[str, list[str]] = {st.id: list(st.deps) for st in result.sub_tasks}
    visited: set[str] = set()
    in_stack: set[str] = set()

    def _has_cycle(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        for dep in adj.get(node, []):
            if dep in in_stack:
                errors.append(f"circular dependency detected involving '{node}' → '{dep}'")
                return True
            if dep not in visited and _has_cycle(dep):
                return True
        in_stack.discard(node)
        return False

    for sid in adj:
        if sid not in visited:
            _has_cycle(sid)

    return errors


def topo_sort_grouped(sub_tasks: list[SubTask]) -> list[list[SubTask]]:
    """Topologically sort sub-tasks into parallel execution groups.

    Returns list of groups. Tasks within the same group have no
    inter-dependencies and can run in parallel.
    Raises ValueError if circular dependency detected.
    """
    by_id = {st.id: st for st in sub_tasks}
    in_degree: dict[str, int] = {st.id: 0 for st in sub_tasks}
    dependents: dict[str, list[str]] = {st.id: [] for st in sub_tasks}

    for st in sub_tasks:
        for dep in st.deps:
            if dep not in by_id:
                raise ValueError(f"Unknown dependency '{dep}'")
            in_degree[st.id] += 1
            dependents[dep].append(st.id)

    groups: list[list[SubTask]] = []
    remaining = set(by_id.keys())

    while remaining:
        # Find all tasks with in_degree == 0
        ready = [tid for tid in remaining if in_degree[tid] == 0]
        if not ready:
            raise ValueError(f"Circular dependency detected involving {remaining}")
        group = [by_id[tid] for tid in sorted(ready)]
        groups.append(group)
        for tid in ready:
            remaining.discard(tid)
            for dep_id in dependents[tid]:
                in_degree[dep_id] -= 1

    return groups


def topo_sort(sub_tasks: list[SubTask]) -> list[SubTask]:
    """Topologically sort sub-tasks by dependencies.

    Returns sub-tasks in execution order: tasks with no deps first,
    then tasks whose deps are satisfied, etc.
    Raises ValueError if circular dependency detected.
    """
    by_id = {st.id: st for st in sub_tasks}
    visited: set[str] = set()
    result: list[SubTask] = []
    visiting: set[str] = set()

    def visit(task_id: str):
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError(f"Circular dependency detected involving '{task_id}'")
        visiting.add(task_id)

        task = by_id.get(task_id)
        if task is None:
            raise ValueError(f"Unknown dependency '{task_id}'")

        for dep in task.deps:
            visit(dep)

        visiting.discard(task_id)
        visited.add(task_id)
        result.append(task)

    for st in sub_tasks:
        visit(st.id)

    return result
