"""Tests for state_machine.py — transition validation against specs/state-machine.yaml."""

from __future__ import annotations

import pytest

from multi_agent.state_machine import (
    InvalidTransitionError,
    reset_cache,
    terminal_states,
    valid_targets,
    validate_transition,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Ensure each test starts with a fresh spec cache."""
    reset_cache()
    yield
    reset_cache()


class TestTerminalStates:
    def test_returns_frozenset(self):
        ts = terminal_states()
        assert isinstance(ts, frozenset)

    def test_done_is_terminal(self):
        assert "DONE" in terminal_states()

    def test_cancelled_is_terminal(self):
        assert "CANCELLED" in terminal_states()

    def test_running_is_not_terminal(self):
        assert "RUNNING" not in terminal_states()


class TestValidTargets:
    def test_draft_targets(self):
        targets = valid_targets("DRAFT")
        assert "QUEUED" in targets
        assert "CANCELLED" in targets

    def test_running_targets(self):
        targets = valid_targets("RUNNING")
        assert "VERIFYING" in targets
        assert "FAILED" in targets
        assert "RETRY" in targets
        assert "ESCALATED" in targets

    def test_done_has_no_targets(self):
        targets = valid_targets("DONE")
        assert len(targets) == 0

    def test_unknown_state_returns_empty(self):
        targets = valid_targets("NONEXISTENT")
        assert len(targets) == 0


class TestValidateTransition:
    def test_valid_transition_returns_true(self):
        assert validate_transition("DRAFT", "QUEUED") is True

    def test_invalid_transition_returns_false(self):
        assert validate_transition("DRAFT", "RUNNING") is False

    def test_self_transition_always_valid(self):
        assert validate_transition("RUNNING", "RUNNING") is True

    def test_case_insensitive(self):
        assert validate_transition("draft", "queued") is True

    def test_whitespace_stripped(self):
        assert validate_transition("  DRAFT  ", "  QUEUED  ") is True

    def test_strict_raises_on_invalid(self):
        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition("DRAFT", "DONE", strict=True)
        err = exc_info.value
        assert err.from_state == "DRAFT"
        assert err.to_state == "DONE"
        assert isinstance(err.allowed, frozenset)

    def test_strict_does_not_raise_on_valid(self):
        assert validate_transition("DRAFT", "QUEUED", strict=True) is True

    def test_unknown_source_state_allows_anything(self):
        assert validate_transition("NONEXISTENT", "WHATEVER") is True

    def test_terminal_state_with_no_transitions_allows_gracefully(self):
        # DONE is terminal but not in transitions dict → graceful degradation allows
        assert validate_transition("DONE", "RUNNING") is True

    def test_cancelled_state_with_no_transitions_allows_gracefully(self):
        assert validate_transition("CANCELLED", "RUNNING") is True


class TestSpecMissing:
    def test_missing_spec_degrades_gracefully(self, monkeypatch, tmp_path):
        """If state-machine.yaml doesn't exist, all transitions are allowed."""
        monkeypatch.setattr(
            "multi_agent.state_machine._load_spec",
            lambda: {"transitions": {}, "terminal_states": []},
        )
        reset_cache()
        assert validate_transition("ANY", "OTHER") is True


class TestInvalidTransitionError:
    def test_str_representation(self):
        err = InvalidTransitionError("A", "B", frozenset(["C", "D"]))
        assert "A" in str(err)
        assert "B" in str(err)
        assert "illegal" in str(err).lower()

    def test_empty_allowed(self):
        err = InvalidTransitionError("DONE", "X", frozenset())
        assert "terminal" in str(err).lower() or "none" in str(err).lower()


class TestResetCache:
    def test_reset_clears_spec(self):
        terminal_states()  # loads the spec
        reset_cache()
        # After reset, calling again should reload
        ts = terminal_states()
        assert isinstance(ts, frozenset)


class TestFullTransitionChain:
    """Test a complete happy-path transition chain through the state machine."""

    def test_happy_path(self):
        chain = ["DRAFT", "QUEUED", "ASSIGNED", "RUNNING", "VERIFYING",
                 "APPROVED", "MERGED", "DONE"]
        for i in range(len(chain) - 1):
            assert validate_transition(chain[i], chain[i + 1], strict=True) is True

    def test_retry_path(self):
        assert validate_transition("RUNNING", "RETRY", strict=True) is True
        assert validate_transition("RETRY", "QUEUED", strict=True) is True
        assert validate_transition("RETRY", "ASSIGNED", strict=True) is True

    def test_escalation_path(self):
        assert validate_transition("RUNNING", "ESCALATED", strict=True) is True
        assert validate_transition("ESCALATED", "ASSIGNED", strict=True) is True
        assert validate_transition("ESCALATED", "CANCELLED", strict=True) is True
