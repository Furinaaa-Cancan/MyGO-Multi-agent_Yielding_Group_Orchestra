"""Tests for Jinja2 prompt renderer."""

from pathlib import Path

import pytest

from multi_agent.contract import load_contract
from multi_agent.prompt import render_builder_prompt, render_reviewer_prompt, _template_dir
from multi_agent.schema import Task


SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _make_task(**overrides) -> Task:
    defaults = {
        "task_id": "task-test-abc",
        "trace_id": "a" * 16,
        "skill_id": "code-implement",
        "done_criteria": ["implement X", "add tests"],
        "input_payload": {"requirement": "Add input validation"},
    }
    defaults.update(overrides)
    return Task(**defaults)


class TestTemplateDir:
    def test_finds_templates(self):
        d = _template_dir()
        assert d.is_dir()
        assert (d / "builder.md.j2").exists()
        assert (d / "reviewer.md.j2").exists()


class TestRenderBuilderPrompt:
    def test_basic_render(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        # Should contain key sections
        assert "Builder" in result
        assert "windsurf" in result
        assert "implement X" in result
        assert "add tests" in result
        assert "code-implement" in result
        # Should contain output JSON template
        assert '"status"' in result
        assert '"summary"' in result

    def test_includes_quality_gates(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "lint" in result
        assert "unit_test" in result

    def test_retry_section_absent_on_first_try(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf", retry_count=0)
        assert "重试" not in result

    def test_retry_section_present_on_retry(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(
            task, contract, agent_id="windsurf",
            retry_count=1, retry_feedback="fix the tests", retry_budget=2,
        )
        assert "重试" in result
        assert "fix the tests" in result
        assert "1" in result  # retry count

    def test_input_payload_rendered(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "requirement" in result
        assert "Add input validation" in result


class TestRenderReviewerPrompt:
    def test_basic_render(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {
            "status": "completed",
            "summary": "Added validation logic",
            "changed_files": ["/src/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
            "risks": [],
            "handoff_notes": "check edge cases",
        }
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "Reviewer" in result
        assert "cursor" in result
        assert "windsurf" in result
        assert "Added validation logic" in result
        assert "/src/main.py" in result
        assert "check edge cases" in result

    def test_includes_decision_template(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert '"decision"' in result
        assert "approve" in result
        assert "reject" in result

    def test_gate_warnings_displayed(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {
            "status": "completed",
            "summary": "done",
            "check_results": {"lint": "pass"},
            "gate_warnings": ["quality gate 'unit_test' not reported"],
        }
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "unit_test" in result


class TestPromptBoundary:
    """Task 40: Prompt template rendering boundary tests."""

    def test_empty_done_criteria(self):
        task = _make_task(done_criteria=[])
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "Builder" in result

    def test_input_payload_none(self):
        task = _make_task(input_payload=None)
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "Builder" in result

    def test_long_requirement(self):
        long_req = "implement X " * 500
        task = _make_task(done_criteria=[long_req])
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "implement X" in result

    def test_special_chars_not_escaped(self):
        task = _make_task(done_criteria=["handle `backticks` and <html> and \"quotes\""])
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "`backticks`" in result

    def test_retry_count_zero_no_retry_section(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf", retry_count=0)
        assert "重试" not in result

    def test_retry_feedback_with_markdown(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(
            task, contract, agent_id="windsurf",
            retry_count=1, retry_feedback="- fix **bold** issue\n- add `test`", retry_budget=3,
        )
        assert "**bold**" in result
        assert "`test`" in result

    def test_reviewer_prompt_includes_builder_id(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "windsurf" in result

    def test_reviewer_prompt_includes_builder_fields(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {
            "status": "completed", "summary": "Added API",
            "changed_files": ["/src/api.py"], "check_results": {"lint": "pass"},
        }
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "Added API" in result
        assert "/src/api.py" in result

    def test_no_unrendered_jinja_tags_builder(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "{{" not in result
        assert "}}" not in result

    def test_no_unrendered_jinja_tags_reviewer(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "{{" not in result
        assert "}}" not in result


class TestStructuredOutput:
    """Task 51: Builder template structured output guidance."""

    def test_builder_has_field_table(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "字段说明" in result
        assert "status" in result
        assert "summary" in result
        assert "changed_files" in result
        assert "check_results" in result

    def test_builder_has_good_example(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "好的输出示例" in result

    def test_builder_has_common_mistakes(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "常见错误" in result


class TestReviewChecklist:
    """Task 52: Reviewer template review checklist."""

    def test_reviewer_has_checklist(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "检查清单" in result

    def test_reviewer_has_decision_criteria(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "approve" in result
        assert "reject" in result
        assert "request_changes" in result

    def test_reviewer_has_feedback_guidelines(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "Feedback" in result

    def test_reviewer_structured_builder_report(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {
            "status": "completed", "summary": "Added API",
            "changed_files": ["/src/api.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        }
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "Added API" in result
        assert "lint" in result
        assert "unit_test" in result


class TestSkillSpecificTemplates:
    """Task 53: Skill-specific template selection."""

    def test_test_and_review_uses_test_template(self):
        task = _make_task()
        contract = load_contract("test-and-review", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "Test Builder" in result or "测试" in result

    def test_code_implement_uses_generic(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "Builder" in result

    def test_unknown_skill_falls_back_to_generic(self):
        from multi_agent.prompt import _resolve_template, _env
        env = _env()
        tmpl = _resolve_template(env, "nonexistent-skill", "builder")
        assert tmpl == "builder.md.j2"

    def test_test_reviewer_template(self):
        task = _make_task()
        contract = load_contract("test-and-review", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "Test Reviewer" in result or "测试审查" in result


class TestPromptTruncation:
    """Task 54: Prompt length control."""

    def test_normal_prompt_not_truncated(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "截断" not in result
        from multi_agent.prompt import MAX_PROMPT_CHARS
        assert len(result) <= MAX_PROMPT_CHARS

    def test_truncation_adds_notice(self):
        from multi_agent.prompt import _truncate_if_needed
        long_text = "x" * 60000
        result = _truncate_if_needed(long_text)
        assert "截断" in result
        from multi_agent.prompt import MAX_PROMPT_CHARS
        assert len(result) > MAX_PROMPT_CHARS  # includes notice

    def test_short_text_unchanged(self):
        from multi_agent.prompt import _truncate_if_needed
        short = "hello world"
        assert _truncate_if_needed(short) == short


class TestPromptVersionTracking:
    """Task 56: Prompt version tracking."""

    def test_builder_prompt_has_version_comment(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(task, contract, agent_id="windsurf")
        assert "<!-- AgentOrchestra v" in result
        assert "prompt: builder" in result

    def test_reviewer_prompt_has_version_comment(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        builder_output = {"status": "completed", "summary": "done", "check_results": {}}
        result = render_reviewer_prompt(
            task, contract, agent_id="cursor",
            builder_output=builder_output, builder_id="windsurf",
        )
        assert "<!-- AgentOrchestra v" in result
        assert "prompt: reviewer" in result

    def test_get_prompt_metadata_format(self):
        from multi_agent.prompt import get_prompt_metadata
        meta = get_prompt_metadata("builder")
        assert meta.startswith("<!--")
        assert meta.endswith("-->")
        assert "AgentOrchestra" in meta
        assert "builder" in meta
        assert "rendered:" in meta


class TestRetryContextEnhancement:
    """Task 57: Builder retry context enhancement."""

    def test_retry_has_strategy_section(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(
            task, contract, agent_id="windsurf",
            retry_count=1, retry_feedback="fix tests", retry_budget=3,
        )
        assert "重试策略" in result
        assert "只修改" in result

    def test_retry_zero_no_retry_content(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(
            task, contract, agent_id="windsurf", retry_count=0,
        )
        assert "重试策略" not in result
        assert "次重试" not in result

    def test_retry_shows_count_and_budget(self):
        task = _make_task()
        contract = load_contract("code-implement", base=SKILLS_DIR)
        result = render_builder_prompt(
            task, contract, agent_id="windsurf",
            retry_count=2, retry_feedback="fix X", retry_budget=3,
        )
        assert "第 2 次重试" in result
        assert "3 次机会" in result
