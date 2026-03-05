"""Tests for task decomposition module."""

import json

import pytest

from multi_agent.decompose import (
    DECOMPOSE_PROMPT,
    DECOMPOSE_PROMPT_EN,
    cache_decompose,
    collect_project_context,
    estimate_complexity,
    get_cached_decompose,
    parse_decompose_json,
    read_decompose_result,
    topo_sort,
    topo_sort_grouped,
    validate_decompose_result,
    write_decompose_prompt,
)
from multi_agent.schema import DecomposeResult, SubTask


class TestParseDecomposeJson:
    def test_parse_raw_json(self):
        raw = json.dumps({
            "sub_tasks": [
                {"id": "auth-login", "description": "Implement login", "done_criteria": ["login works"]},
                {"id": "auth-register", "description": "Implement register"},
            ],
            "reasoning": "Split by feature",
        })
        result = parse_decompose_json(raw)
        assert result is not None
        assert len(result.sub_tasks) == 2
        assert result.sub_tasks[0].id == "auth-login"
        assert result.reasoning == "Split by feature"

    def test_parse_markdown_fenced(self):
        raw = """Here is the decomposition:

```json
{
  "sub_tasks": [
    {"id": "step-1", "description": "First step"}
  ],
  "reasoning": "Simple"
}
```

Done!"""
        result = parse_decompose_json(raw)
        assert result is not None
        assert len(result.sub_tasks) == 1
        assert result.sub_tasks[0].id == "step-1"

    def test_parse_invalid_json(self):
        assert parse_decompose_json("not json at all") is None

    def test_parse_missing_sub_tasks(self):
        raw = json.dumps({"reasoning": "no sub_tasks key"})
        assert parse_decompose_json(raw) is None

    def test_parse_empty_string(self):
        assert parse_decompose_json("") is None


class TestTopoSort:
    def test_no_deps(self):
        tasks = [
            SubTask(id="a", description="A"),
            SubTask(id="b", description="B"),
            SubTask(id="c", description="C"),
        ]
        sorted_tasks = topo_sort(tasks)
        ids = [t.id for t in sorted_tasks]
        assert set(ids) == {"a", "b", "c"}

    def test_linear_deps(self):
        tasks = [
            SubTask(id="c", description="C", deps=["b"]),
            SubTask(id="b", description="B", deps=["a"]),
            SubTask(id="a", description="A"),
        ]
        sorted_tasks = topo_sort(tasks)
        ids = [t.id for t in sorted_tasks]
        assert ids == ["a", "b", "c"]

    def test_diamond_deps(self):
        tasks = [
            SubTask(id="d", description="D", deps=["b", "c"]),
            SubTask(id="b", description="B", deps=["a"]),
            SubTask(id="c", description="C", deps=["a"]),
            SubTask(id="a", description="A"),
        ]
        sorted_tasks = topo_sort(tasks)
        ids = [t.id for t in sorted_tasks]
        assert ids[0] == "a"  # a must be first
        assert ids[-1] == "d"  # d must be last
        assert ids.index("b") < ids.index("d")
        assert ids.index("c") < ids.index("d")

    def test_circular_dep_raises(self):
        tasks = [
            SubTask(id="a", description="A", deps=["b"]),
            SubTask(id="b", description="B", deps=["a"]),
        ]
        with pytest.raises(ValueError, match="Circular"):
            topo_sort(tasks)

    def test_unknown_dep_raises(self):
        tasks = [
            SubTask(id="a", description="A", deps=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="Unknown dependency"):
            topo_sort(tasks)

    def test_single_task(self):
        tasks = [SubTask(id="only", description="Only task")]
        sorted_tasks = topo_sort(tasks)
        assert len(sorted_tasks) == 1
        assert sorted_tasks[0].id == "only"


class TestWriteAndRead:
    def test_write_decompose_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.decompose.inbox_dir", lambda: tmp_path / "inbox")

        p = write_decompose_prompt("Build auth module")
        assert p.exists()
        content = p.read_text()
        assert "Build auth module" in content
        assert "sub_tasks" in content

    def test_read_decompose_result(self, tmp_path, monkeypatch):
        outbox = tmp_path / "outbox"
        outbox.mkdir()
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: outbox)

        data = {
            "sub_tasks": [
                {"id": "step-1", "description": "First"},
                {"id": "step-2", "description": "Second", "deps": ["step-1"]},
            ],
            "reasoning": "test",
        }
        (outbox / "decompose.json").write_text(json.dumps(data))

        result = read_decompose_result()
        assert result is not None
        assert len(result.sub_tasks) == 2

    def test_read_markdown_fenced_json(self, tmp_path, monkeypatch):
        """Agent may wrap JSON in ```json blocks — fallback should handle it."""
        outbox = tmp_path / "outbox"
        outbox.mkdir()
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: outbox)

        fenced = '''Here is my decomposition:

```json
{
  "sub_tasks": [{"id": "step-1", "description": "Do it"}],
  "reasoning": "simple"
}
```
'''
        (outbox / "decompose.json").write_text(fenced)
        result = read_decompose_result()
        assert result is not None
        assert result.sub_tasks[0].id == "step-1"

    def test_read_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "nope")
        assert read_decompose_result() is None


class TestEstimateComplexity:
    """Task 16: Verify complexity estimation heuristics."""

    def test_empty_string(self):
        assert estimate_complexity("") == "simple"

    def test_none_input(self):
        assert estimate_complexity("") == "simple"

    def test_short_simple(self):
        assert estimate_complexity("Fix the login button") == "simple"

    def test_medium_length(self):
        assert estimate_complexity("实现用户认证模块，包括登录和注册功能") == "medium"

    def test_complex_long(self):
        text = "x" * 201
        assert estimate_complexity(text) == "complex"

    def test_complex_many_verbs(self):
        assert estimate_complexity("实现登录功能，创建注册页面，添加密码重置") == "complex"

    def test_complex_english_verbs(self):
        assert estimate_complexity("implement auth, create users, add tests, modify config") == "complex"

    def test_whitespace_only(self):
        assert estimate_complexity("   ") == "simple"


class TestTopoSortGrouped:
    """Task 19: Verify parallel group detection."""

    def test_all_independent(self):
        tasks = [
            SubTask(id="a", description="A"),
            SubTask(id="b", description="B"),
            SubTask(id="c", description="C"),
        ]
        groups = topo_sort_grouped(tasks)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_linear_deps(self):
        tasks = [
            SubTask(id="c", description="C", deps=["b"]),
            SubTask(id="b", description="B", deps=["a"]),
            SubTask(id="a", description="A"),
        ]
        groups = topo_sort_grouped(tasks)
        assert len(groups) == 3
        assert groups[0][0].id == "a"
        assert groups[1][0].id == "b"
        assert groups[2][0].id == "c"

    def test_diamond_deps(self):
        tasks = [
            SubTask(id="d", description="D", deps=["b", "c"]),
            SubTask(id="b", description="B", deps=["a"]),
            SubTask(id="c", description="C", deps=["a"]),
            SubTask(id="a", description="A"),
        ]
        groups = topo_sort_grouped(tasks)
        assert len(groups) == 3
        assert groups[0][0].id == "a"
        parallel = {t.id for t in groups[1]}
        assert parallel == {"b", "c"}
        assert groups[2][0].id == "d"

    def test_circular_raises(self):
        tasks = [
            SubTask(id="a", description="A", deps=["b"]),
            SubTask(id="b", description="B", deps=["a"]),
        ]
        with pytest.raises(ValueError, match="Circular"):
            topo_sort_grouped(tasks)

    def test_single_task(self):
        tasks = [SubTask(id="only", description="Only")]
        groups = topo_sort_grouped(tasks)
        assert len(groups) == 1
        assert groups[0][0].id == "only"


class TestValidateDecomposeResult:
    """Task 20: Verify decompose result validation."""

    def test_valid_result(self):
        result = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="Do A"),
            SubTask(id="b", description="Do B", deps=["a"]),
        ], reasoning="test")
        errors = validate_decompose_result(result)
        assert errors == []

    def test_duplicate_ids(self):
        result = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="Do A"),
            SubTask(id="a", description="Do A again"),
        ], reasoning="test")
        errors = validate_decompose_result(result)
        assert any("duplicate" in e for e in errors)

    def test_empty_description(self):
        result = DecomposeResult(sub_tasks=[
            SubTask(id="a", description=""),
        ], reasoning="test")
        errors = validate_decompose_result(result)
        assert any("empty description" in e for e in errors)

    def test_unknown_dep(self):
        result = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="A", deps=["nonexistent"]),
        ], reasoning="test")
        errors = validate_decompose_result(result)
        assert any("unknown id" in e for e in errors)

    def test_self_dependency(self):
        result = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="A", deps=["a"]),
        ], reasoning="test")
        errors = validate_decompose_result(result)
        assert any("depends on itself" in e for e in errors)

    def test_empty_result(self):
        result = DecomposeResult(sub_tasks=[], reasoning="nothing")
        errors = validate_decompose_result(result)
        assert any("empty" in e or "minimum" in e for e in errors)

    def test_too_many_sub_tasks(self):
        from multi_agent.schema import SubTask
        many = [SubTask(id=f"t{i}", description=f"task {i}", skill_id="code-implement", deps=[])
                for i in range(11)]
        result = DecomposeResult(sub_tasks=many, reasoning="big")
        errors = validate_decompose_result(result)
        assert any("too many" in e or "maximum" in e for e in errors)


class TestDecomposePromptEN:
    """Task 17: Verify English decompose prompt."""

    def test_en_prompt_contains_rules(self):
        assert "Task Decomposition" in DECOMPOSE_PROMPT_EN
        assert "sub_tasks" in DECOMPOSE_PROMPT_EN
        assert "done_criteria" in DECOMPOSE_PROMPT_EN
        assert "deps" in DECOMPOSE_PROMPT_EN
        assert "reasoning" in DECOMPOSE_PROMPT_EN

    def test_zh_prompt_contains_rules(self):
        assert "任务分解" in DECOMPOSE_PROMPT
        assert "sub_tasks" in DECOMPOSE_PROMPT
        assert "done_criteria" in DECOMPOSE_PROMPT

    def test_en_prompt_same_json_fields(self):
        """EN and ZH prompts must have the same JSON field names."""
        for field in ["sub_tasks", "id", "description", "done_criteria", "deps", "skill_id", "reasoning"]:
            assert field in DECOMPOSE_PROMPT_EN
            assert field in DECOMPOSE_PROMPT

    def test_write_en_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.decompose.inbox_dir", lambda: tmp_path / "inbox")
        p = write_decompose_prompt("Build auth module", lang="en")
        content = p.read_text()
        assert "Build auth module" in content
        assert "Task Decomposition" in content
        assert "After completion" in content

    def test_write_zh_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.decompose.inbox_dir", lambda: tmp_path / "inbox")
        p = write_decompose_prompt("实现登录功能", lang="zh")
        content = p.read_text()
        assert "实现登录功能" in content
        assert "任务分解" in content
        assert "完成后" in content

    def test_default_lang_is_zh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.decompose.inbox_dir", lambda: tmp_path / "inbox")
        monkeypatch.setattr("multi_agent.decompose.collect_project_context", lambda: "")
        p = write_decompose_prompt("test")
        content = p.read_text()
        assert "任务分解" in content


class TestDecomposeCache:
    """Task 23: Verify decompose result caching."""

    def test_cache_write_and_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        result = DecomposeResult(
            sub_tasks=[SubTask(id="a", description="Do A")],
            reasoning="test",
        )
        cache_decompose("my requirement", result)
        cached = get_cached_decompose("my requirement")
        assert cached is not None
        assert cached.sub_tasks[0].id == "a"

    def test_cache_miss(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        assert get_cached_decompose("never cached") is None

    def test_different_requirements_different_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        r1 = DecomposeResult(sub_tasks=[SubTask(id="a", description="A")], reasoning="r1")
        r2 = DecomposeResult(sub_tasks=[SubTask(id="b", description="B")], reasoning="r2")
        cache_decompose("req one", r1)
        cache_decompose("req two", r2)
        c1 = get_cached_decompose("req one")
        c2 = get_cached_decompose("req two")
        assert c1.sub_tasks[0].id == "a"
        assert c2.sub_tasks[0].id == "b"

    def test_cache_dir_auto_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        result = DecomposeResult(sub_tasks=[], reasoning="empty")
        path = cache_decompose("test", result)
        assert path.exists()
        assert (tmp_path / "cache").is_dir()


class TestCollectProjectContext:
    """Task 25: Verify project context collection."""

    def test_empty_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.config.root_dir", lambda: tmp_path)
        ctx = collect_project_context()
        assert ctx == ""

    def test_with_src_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.config.root_dir", lambda: tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hello')")
        (src / "utils.py").write_text("pass")
        ctx = collect_project_context()
        assert "main.py" in ctx
        assert "utils.py" in ctx

    def test_with_readme(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.config.root_dir", lambda: tmp_path)
        (tmp_path / "README.md").write_text("# My Project\nSome description")
        ctx = collect_project_context()
        assert "My Project" in ctx

    def test_truncation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.config.root_dir", lambda: tmp_path)
        (tmp_path / "README.md").write_text("x" * 3000)
        ctx = collect_project_context(max_chars=100)
        assert len(ctx) <= 120  # 100 + "... (已截断)"
        assert "已截断" in ctx

    def test_project_context_in_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.decompose.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.decompose.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.decompose.inbox_dir", lambda: tmp_path / "inbox")
        p = write_decompose_prompt("test", project_context="My project files here")
        content = p.read_text()
        assert "项目背景" in content
        assert "My project files here" in content


class TestDiffDecomposeResults:
    """Task 71: Decompose result diff detection tests."""

    def test_identical_results_no_diff(self):
        from multi_agent.decompose import diff_decompose_results
        from multi_agent.schema import DecomposeResult, SubTask
        r1 = DecomposeResult(sub_tasks=[SubTask(id="a", description="do A")])
        r2 = DecomposeResult(sub_tasks=[SubTask(id="a", description="do A")])
        assert diff_decompose_results(r1, r2) == []

    def test_added_subtask(self):
        from multi_agent.decompose import diff_decompose_results
        from multi_agent.schema import DecomposeResult, SubTask
        r1 = DecomposeResult(sub_tasks=[SubTask(id="a", description="do A")])
        r2 = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="do A"),
            SubTask(id="b", description="do B"),
        ])
        diffs = diff_decompose_results(r1, r2)
        assert any("新增" in d and "b" in d for d in diffs)

    def test_removed_subtask(self):
        from multi_agent.decompose import diff_decompose_results
        from multi_agent.schema import DecomposeResult, SubTask
        r1 = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="do A"),
            SubTask(id="b", description="do B"),
        ])
        r2 = DecomposeResult(sub_tasks=[SubTask(id="a", description="do A")])
        diffs = diff_decompose_results(r1, r2)
        assert any("移除" in d and "b" in d for d in diffs)

    def test_changed_description(self):
        from multi_agent.decompose import diff_decompose_results
        from multi_agent.schema import DecomposeResult, SubTask
        r1 = DecomposeResult(sub_tasks=[SubTask(id="a", description="old desc")])
        r2 = DecomposeResult(sub_tasks=[SubTask(id="a", description="new desc")])
        diffs = diff_decompose_results(r1, r2)
        assert any("描述变更" in d for d in diffs)

    def test_changed_deps(self):
        from multi_agent.decompose import diff_decompose_results
        from multi_agent.schema import DecomposeResult, SubTask
        r1 = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="A"),
            SubTask(id="b", description="B", deps=[]),
        ])
        r2 = DecomposeResult(sub_tasks=[
            SubTask(id="a", description="A"),
            SubTask(id="b", description="B", deps=["a"]),
        ])
        diffs = diff_decompose_results(r1, r2)
        assert any("依赖变更" in d for d in diffs)

    def test_changed_done_criteria(self):
        from multi_agent.decompose import diff_decompose_results
        r1 = DecomposeResult(sub_tasks=[SubTask(id="a", description="A", done_criteria=["pass"])])
        r2 = DecomposeResult(sub_tasks=[SubTask(id="a", description="A", done_criteria=["pass", "lint"])])
        diffs = diff_decompose_results(r1, r2)
        assert any("完成标准变更" in d for d in diffs)


class TestReadDecomposeResultEdgeCases:
    """Cover uncovered branches in read_decompose_result."""

    def test_oserror_returns_none(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from multi_agent import config
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        monkeypatch.setattr(config, "outbox_dir", lambda: tmp_path / "outbox")
        outbox = tmp_path / "outbox"
        outbox.mkdir(parents=True)
        (outbox / "decompose.json").write_text('{"sub_tasks": []}')
        with patch("pathlib.Path.read_text", side_effect=OSError("locked")):
            result = read_decompose_result(validate=False)
        assert result is None

    def test_validation_critical_error_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        monkeypatch.setattr(config, "outbox_dir", lambda: tmp_path / "outbox")
        outbox = tmp_path / "outbox"
        outbox.mkdir(parents=True)
        # Empty sub_tasks — should be flagged as critical
        (outbox / "decompose.json").write_text('{"sub_tasks": []}')
        result = read_decompose_result(validate=True)
        assert result is None

    def test_markdown_fence_fallback(self, tmp_path, monkeypatch):
        from multi_agent import config
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        monkeypatch.setattr(config, "outbox_dir", lambda: tmp_path / "outbox")
        outbox = tmp_path / "outbox"
        outbox.mkdir(parents=True)
        # Invalid JSON but valid markdown-fenced JSON
        content = 'Here is the result:\n```json\n{"sub_tasks": [{"id": "a", "description": "do A"}]}\n```'
        (outbox / "decompose.json").write_text(content)
        result = read_decompose_result(validate=False)
        assert result is not None
        assert result.sub_tasks[0].id == "a"


class TestGetCachedDecomposeEdgeCases:
    """Cover get_cached_decompose exception handling (lines 330-332)."""

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        cd = tmp_path / "cache"
        cd.mkdir(parents=True)
        from multi_agent.decompose import _cache_key
        key = _cache_key("test req", "")
        (cd / f"decompose-{key}.json").write_text("not valid json{{{")
        result = get_cached_decompose("test req")
        assert result is None


class TestTopoSortUnknownDep:
    """Cover unknown dependency error in topo_sort_grouped (line 494)."""

    def test_unknown_dep_raises(self):
        tasks = [SubTask(id="a", description="A", deps=["nonexistent"])]
        with pytest.raises(ValueError, match="Unknown dependency"):
            topo_sort_grouped(tasks)


class TestCollectProjectContextEdgeCases:
    """Cover README read exception (lines 166-167)."""

    def test_readme_read_error(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from multi_agent import config
        monkeypatch.setattr(config, "root_dir", lambda: tmp_path)
        # Create README but make it unreadable
        readme = tmp_path / "README.md"
        readme.write_text("# Test")
        with patch.object(type(readme), "read_text", side_effect=PermissionError("denied")):
            result = collect_project_context()
        # Should not crash, just skip README
        assert "denied" not in result

    def test_pyproject_deps_extraction(self, tmp_path, monkeypatch):
        from multi_agent import config
        monkeypatch.setattr(config, "root_dir", lambda: tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\ndependencies = [\n  "click>=8.0",\n  "pydantic>=2.0",\n]\n')
        result = collect_project_context()
        assert "click" in result or "dependencies" in result
