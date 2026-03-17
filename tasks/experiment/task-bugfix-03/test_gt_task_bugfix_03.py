"""Ground truth tests for task-bugfix-03: append_trace_event empty type validation."""
import pytest
from unittest.mock import patch
from pathlib import Path


def test_empty_event_type_raises(tmp_path):
    """append_trace_event with event_type='' must raise ValueError."""
    history = tmp_path / "history"
    history.mkdir()

    with patch("multi_agent.trace.history_dir", return_value=history):
        from multi_agent.trace import append_trace_event
        with pytest.raises(ValueError, match="event_type"):
            append_trace_event(
                task_id="task-test-1",
                event_type="",
                actor="test",
                role="builder",
                state="RUNNING",
            )


def test_whitespace_event_type_raises(tmp_path):
    """append_trace_event with event_type='  ' must raise ValueError."""
    history = tmp_path / "history"
    history.mkdir()

    with patch("multi_agent.trace.history_dir", return_value=history):
        from multi_agent.trace import append_trace_event
        with pytest.raises(ValueError, match="event_type"):
            append_trace_event(
                task_id="task-test-2",
                event_type="   ",
                actor="test",
                role="reviewer",
                state="VERIFYING",
            )


def test_valid_event_type_works(tmp_path):
    """Normal event_type should work without error."""
    history = tmp_path / "history"
    history.mkdir()

    with patch("multi_agent.trace.history_dir", return_value=history):
        from multi_agent.trace import append_trace_event
        event = append_trace_event(
            task_id="task-test-3",
            event_type="task_started",
            actor="orchestrator",
            role="orchestrator",
            state="RUNNING",
        )
        assert event["event_type"] == "task_started"
