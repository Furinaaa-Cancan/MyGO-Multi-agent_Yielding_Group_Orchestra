"""Pydantic models for Task, SkillContract, and AgentOutput."""

from __future__ import annotations

import re
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from multi_agent._utils import SAFE_TASK_ID_RE as _ID_RE
from multi_agent._utils import now_utc as _now_utc

# ── Enums ─────────────────────────────────────────────────

class TaskState(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    APPROVED = "APPROVED"
    MERGED = "MERGED"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRY = "RETRY"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class CheckKind(str, Enum):
    LINT = "lint"
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    CONTRACT_TEST = "contract_test"
    SECURITY_SCAN = "security_scan"
    ARTIFACT_CHECKSUM = "artifact_checksum"


class ErrorCode(str, Enum):
    """Unified error codes for graph workflow failures.

    Aligned with SHIELDA (2025) exception taxonomy categories:
    Task Flow, Interface, Tool, Planning, Goal.
    """
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REQUEST_CHANGES_CAP = "REQUEST_CHANGES_CAP"
    TIMEOUT = "TIMEOUT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    BUILDER_ERROR = "BUILDER_ERROR"
    REVIEWER_ERROR = "REVIEWER_ERROR"
    CIRCULAR_DEPENDENCY = "CIRCULAR_DEPENDENCY"
    NO_AGENT = "NO_AGENT"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class BackoffStrategy(str, Enum):
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    FIXED = "fixed"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


# ── Helpers ───────────────────────────────────────────────

_TRACE_RE = re.compile(r"^[a-f0-9-]{16,64}$")


# _now_utc imported from _utils


# ── Task ──────────────────────────────────────────────────

class TaskError(BaseModel):
    code: str
    message: str


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    skill_id: str
    skill_version: str = "1.0.0"
    producer: str = "orchestrator"
    consumer: str = "pending"
    idempotency_key: str = ""
    input_digest: str = ""
    artifact_uri: str = ""
    expected_checks: list[CheckKind] = Field(default_factory=list)
    timeout_sec: int = 1800
    retry_budget: int = 2
    priority: Priority = Priority.NORMAL
    required_capabilities: list[str] = Field(default_factory=list)
    state: TaskState = TaskState.DRAFT
    deps: list[str] = Field(default_factory=list)
    done_criteria: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_utc)
    updated_at: str = Field(default_factory=_now_utc)
    owner: str | None = None
    input_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    error: TaskError | None = None
    parent_task_id: str | None = None

    @field_validator("task_id")
    @classmethod
    def _valid_task_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"task_id must match {_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("trace_id")
    @classmethod
    def _valid_trace_id(cls, v: str) -> str:
        if not _TRACE_RE.match(v):
            raise ValueError(f"trace_id must match {_TRACE_RE.pattern}, got {v!r}")
        return v


# ── Skill Contract ────────────────────────────────────────

class SkillInput(BaseModel):
    name: str
    schema_: str = Field(alias="schema")
    required: bool = True


class SkillOutput(BaseModel):
    name: str
    schema_: str = Field(alias="schema")
    required: bool = True


class RetryPolicy(BaseModel):
    max_attempts: int = 2
    backoff: BackoffStrategy = BackoffStrategy.LINEAR


class Timeouts(BaseModel):
    run_sec: int = 1800
    verify_sec: int = 600


class FallbackPolicy(BaseModel):
    on_failure: str = "retry"


class HandoffSpec(BaseModel):
    artifact_path: str = ""
    required_fields: list[str] = Field(default_factory=list)


class SkillContract(BaseModel):
    id: str
    version: str = "1.0.0"
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    inputs: list[SkillInput] = Field(default_factory=list)
    outputs: list[SkillOutput] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    quality_gates: list[str] = Field(default_factory=list)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    fallback: FallbackPolicy = Field(default_factory=FallbackPolicy)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    handoff: HandoffSpec = Field(default_factory=HandoffSpec)
    supported_agents: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, data: dict) -> "SkillContract":
        data = {**data}  # shallow copy to avoid mutating caller's dict
        compat = data.get("compatibility", {})
        if isinstance(compat, dict):
            compat = {**compat}  # copy before pop
            agents = compat.pop("supported_agents", [])
            data["compatibility"] = compat
        else:
            agents = []
        return cls(supported_agents=agents, **data)


# ── Agent Output ──────────────────────────────────────────

class BuilderOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str  # "completed" | "blocked"
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    check_results: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    handoff_notes: str = ""


class ReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: ReviewDecision = ReviewDecision.REJECT
    summary: str = ""
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""


# ── Sub-Task (for task decomposition) ─────────────────────

class SubTask(BaseModel):
    """A single decomposed sub-task from a larger requirement."""
    model_config = ConfigDict(extra="ignore")
    id: str                   # e.g. "auth-login"
    description: str          # what to implement
    done_criteria: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)  # IDs of sub-tasks this depends on
    skill_id: str = "code-implement"
    priority: Priority = Priority.NORMAL
    estimated_minutes: int = 30
    acceptance_criteria: list[str] = Field(default_factory=list)
    parent_task_id: str | None = None


class DecomposeResult(BaseModel):
    """Output of task decomposition — a list of sub-tasks."""
    model_config = ConfigDict(extra="ignore")
    sub_tasks: list[SubTask]
    reasoning: str = ""  # why this decomposition was chosen
    total_estimated_minutes: int = 0
    version: str = "1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_utc)


# ── Agent Profile ─────────────────────────────────────────

class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    driver: str = "file"  # "file" (IDE, manual) or "cli" (auto-spawn)
    command: str = ""      # CLI command template (for driver="cli")
    capabilities: list[str] = Field(default_factory=list)
    reliability: float = 0.9
    queue_health: float = 0.9
    cost: float = 0.5


# ── Conversation Events ──────────────────────────────────
#
# Type-safe event definitions for the conversation log.
# Previously all events were untyped dicts with ad-hoc keys,
# making it impossible to detect typos or missing fields at
# validation time (architecture review defect B3).


class ConversationAction(str, Enum):
    """All valid orchestrator/agent actions in the conversation log."""
    ASSIGNED = "assigned"
    APPROVED = "approved"
    RETRY = "retry"
    REQUEST_CHANGES = "request_changes"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    TOTAL_TIMEOUT = "total_timeout"
    INTERNAL_ERROR = "internal_error"
    RUBBER_STAMP_WARNING = "rubber_stamp_warning"
    TERMINAL_PASSTHROUGH = "terminal_passthrough"
    PRECONDITION_FAILED = "precondition_failed"


class ConversationEvent(BaseModel):
    """A single entry in the workflow conversation log.

    Replaces the untyped ``dict`` pattern used throughout graph.py.
    Constructed via the ``make_event`` factory for backward compatibility
    with existing code that expects plain dicts.
    """
    model_config = ConfigDict(extra="allow")

    role: str
    t: float = Field(default_factory=time.time)
    # Fields that appear depending on event type:
    action: str | None = None
    decision: str | None = None
    output: str | None = None
    details: Any = None
    agent: str | None = None
    feedback: str | None = None
    node: str | None = None
    elapsed: int | None = None
    reviewer_id: str | None = None


def make_event(role: str, *, action: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Create a conversation event dict with timestamp and validation.

    Returns a plain dict for backward compatibility with LangGraph state
    (which uses ``list[dict]``), while providing construction-time validation.
    """
    evt = ConversationEvent(role=role, action=action, **kwargs)
    # Export as dict, dropping None values for compact storage
    return {k: v for k, v in evt.model_dump().items() if v is not None}
