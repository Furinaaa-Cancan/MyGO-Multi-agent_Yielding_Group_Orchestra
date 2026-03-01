"""Tests for the dashboard generator."""

import pytest

from multi_agent.dashboard import generate_dashboard, write_dashboard


class TestGenerateDashboard:
    def test_basic_content(self):
        content = generate_dashboard(
            task_id="task-abc123",
            done_criteria=["implement X", "add tests"],
            current_agent="windsurf",
            current_role="builder",
            conversation=[],
        )
        assert "task-abc123" in content
        assert "implement X" in content
        assert "add tests" in content

    def test_references_task_md_not_inbox(self):
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="windsurf",
            current_role="builder",
            conversation=[],
        )
        assert "TASK.md" in content
        assert "inbox" not in content.lower()

    def test_error_display(self):
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="windsurf",
            current_role="builder",
            conversation=[],
            error="something broke",
        )
        assert "something broke" in content
        assert "❌" in content

    def test_status_msg(self):
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="cursor",
            current_role="reviewer",
            conversation=[],
            status_msg="🟡 等待审查",
        )
        assert "等待审查" in content

    def test_conversation_history(self):
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="windsurf",
            current_role="builder",
            conversation=[
                {"role": "orchestrator", "action": "assigned"},
                {"role": "builder", "output": "done"},
            ],
        )
        assert "orchestrator" in content
        assert "assigned" in content

    def test_conversation_uses_event_timestamp(self):
        """Conversation entries with 't' field should use event time, not render time."""
        import time
        # Use a fixed timestamp: 2024-01-01 12:30:45 UTC
        fixed_t = 1704112245.0
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="windsurf",
            current_role="builder",
            conversation=[
                {"role": "orchestrator", "action": "assigned", "t": fixed_t},
            ],
        )
        # Should contain the formatted event timestamp, not current time
        assert "12:30:45" in content

    def test_fallback_role_display(self):
        """When no status_msg or error, should show role-based display."""
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="windsurf",
            current_role="builder",
            conversation=[],
        )
        assert "windsurf" in content
        assert "builder" in content


class TestWriteDashboard:
    def test_writes_to_disk(self, tmp_path):
        p = tmp_path / "dashboard.md"
        result = write_dashboard(
            task_id="task-abc",
            done_criteria=["test"],
            current_agent="windsurf",
            current_role="builder",
            conversation=[],
            path=p,
        )
        assert result == p
        assert p.exists()
        assert "task-abc" in p.read_text()

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "sub" / "dir" / "dashboard.md"
        write_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="w",
            current_role="builder",
            conversation=[],
            path=p,
        )
        assert p.exists()

    def test_custom_path(self, tmp_path):
        p = tmp_path / "custom.md"
        write_dashboard(
            task_id="task-abc",
            done_criteria=[],
            current_agent="w",
            current_role="builder",
            conversation=[],
            path=p,
        )
        assert p.exists()


class TestDashboardBoundary:
    """Task 42: Dashboard rendering boundary tests."""

    def test_empty_conversation_header_only(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder", conversation=[],
        )
        assert "task-abc" in content

    def test_multiple_conversation_entries(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder",
            conversation=[
                {"role": "orchestrator", "action": "assigned", "t": 1000000},
                {"role": "builder", "output": "done", "t": 1000010},
                {"role": "reviewer", "decision": "approve", "t": 1000020},
            ],
        )
        assert "orchestrator" in content
        assert "builder" in content
        assert "reviewer" in content

    def test_done_criteria_with_special_chars(self):
        content = generate_dashboard(
            task_id="task-abc",
            done_criteria=["use `backtick` | pipe | **bold**"],
            current_agent="w", current_role="builder", conversation=[],
        )
        assert "`backtick`" in content

    def test_status_msg_empty_uses_default(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder", conversation=[],
        )
        assert "w" in content
        assert "builder" in content

    def test_timeout_remaining_not_crash(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder", conversation=[],
            status_msg="🔵 等待 builder (超时还剩 300s)",
        )
        assert "300s" in content

    def test_error_status_shows_red(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder", conversation=[],
            error="TIMEOUT exceeded",
        )
        assert "❌" in content
        assert "TIMEOUT exceeded" in content

    def test_empty_done_criteria_no_crash(self):
        content = generate_dashboard(
            task_id="task-abc", done_criteria=[], current_agent="w",
            current_role="builder", conversation=[],
        )
        assert "task-abc" in content

    def test_many_done_criteria(self):
        criteria = [f"criterion_{i}" for i in range(20)]
        content = generate_dashboard(
            task_id="task-abc", done_criteria=criteria, current_agent="w",
            current_role="builder", conversation=[],
        )
        assert "criterion_0" in content
        assert "criterion_19" in content
