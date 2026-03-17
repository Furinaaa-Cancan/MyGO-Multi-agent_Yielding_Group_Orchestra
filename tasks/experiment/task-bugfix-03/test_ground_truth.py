"""Ground truth tests for task-bugfix-03: emit_event empty type validation."""
import pytest
from unittest.mock import patch


def test_empty_event_type_raises(tmp_path):
    """emit_event('') must raise ValueError."""
    with patch("multi_agent.trace._audit_log_path", return_value=tmp_path / "audit.ndjson"):
        from multi_agent.trace import emit_event
        with pytest.raises(ValueError, match="event_type"):
            emit_event("", task_id="test-1")


def test_whitespace_event_type_raises(tmp_path):
    """emit_event('  ') must raise ValueError."""
    with patch("multi_agent.trace._audit_log_path", return_value=tmp_path / "audit.ndjson"):
        from multi_agent.trace import emit_event
        with pytest.raises(ValueError, match="event_type"):
            emit_event("   ", task_id="test-1")


def test_valid_event_type_works(tmp_path):
    """Normal event_type should work without error."""
    log_file = tmp_path / "audit.ndjson"
    with patch("multi_agent.trace._audit_log_path", return_value=log_file):
        from multi_agent.trace import emit_event
        # Should not raise
        emit_event("task_started", task_id="test-1")
