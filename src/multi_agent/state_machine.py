"""State machine validator — enforces transition rules from specs/state-machine.yaml.

Architecture fix (defect A4): Previously the YAML spec was purely documentary
and completely disconnected from runtime code. This module loads the spec and
provides ``validate_transition()`` which can be called at every state change
to ensure the code never performs an illegal transition.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

_log = logging.getLogger(__name__)

_spec: dict[str, Any] | None = None


def _load_spec() -> dict[str, Any]:
    """Load and cache the state-machine spec from specs/state-machine.yaml."""
    global _spec
    if _spec is not None:
        return _spec

    from multi_agent.config import root_dir
    spec_path = root_dir() / "specs" / "state-machine.yaml"
    if not spec_path.exists():
        _log.warning("state-machine.yaml not found at %s — validation disabled", spec_path)
        _spec = {"transitions": {}, "terminal_states": []}
        return _spec

    with spec_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data.get("transitions"), dict):
        _log.warning("state-machine.yaml has no valid 'transitions' key")
        data["transitions"] = {}

    _spec = data
    return _spec


def terminal_states() -> frozenset[str]:
    """Return the set of terminal states from the spec (normalized to uppercase)."""
    spec = _load_spec()
    return frozenset(s.upper().strip() if isinstance(s, str) else s for s in spec.get("terminal_states", []))


def valid_targets(from_state: str) -> frozenset[str]:
    """Return all states reachable from *from_state* per the spec (case-insensitive)."""
    spec = _load_spec()
    transitions = spec.get("transitions", {})
    # Normalize keys for case-insensitive lookup
    normalized = {k.upper().strip(): v for k, v in transitions.items() if isinstance(k, str)}
    targets = normalized.get(from_state.upper().strip(), [])
    return frozenset(t.upper().strip() if isinstance(t, str) else t for t in targets)


class InvalidTransitionError(RuntimeError):
    """Raised when a state transition violates the spec."""

    def __init__(self, from_state: str, to_state: str, allowed: frozenset[str]):
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed
        super().__init__(
            f"illegal state transition {from_state} → {to_state}; "
            f"allowed targets: {sorted(allowed) if allowed else '(none — terminal state)'}"
        )


def validate_transition(from_state: str, to_state: str, *, strict: bool = False) -> bool:
    """Check whether *from_state* → *to_state* is a legal transition.

    Parameters
    ----------
    from_state : str
        Current state (e.g. "RUNNING").
    to_state : str
        Desired next state (e.g. "VERIFYING").
    strict : bool
        If True, raises ``InvalidTransitionError`` on illegal transitions.
        If False (default), logs a warning and returns False.

    Returns
    -------
    bool
        True if the transition is valid or the spec is unavailable.
    """
    from_state = from_state.upper().strip()
    to_state = to_state.upper().strip()

    if from_state == to_state:
        return True  # self-transition always allowed (idempotent)

    # Terminal states must not have outgoing transitions
    if from_state in terminal_states():
        if strict:
            raise InvalidTransitionError(from_state, to_state, frozenset())
        _log.warning(
            "illegal state transition %s → %s (terminal state)",
            from_state, to_state,
        )
        return False

    allowed = valid_targets(from_state)

    # If spec doesn't define transitions for from_state, allow anything
    # (graceful degradation when spec is incomplete).
    if not allowed and from_state not in _load_spec().get("transitions", {}):
        return True

    if to_state in allowed:
        return True

    if strict:
        raise InvalidTransitionError(from_state, to_state, allowed)

    _log.warning(
        "illegal state transition %s → %s (allowed: %s)",
        from_state, to_state, sorted(allowed),
    )
    return False


def reset_cache() -> None:
    """Clear the cached spec — useful for testing."""
    global _spec
    _spec = None
