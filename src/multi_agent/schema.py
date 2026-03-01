"""Pydantic models for Task, SkillContract, and AgentOutput."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_TRACE_RE = re.compile(r"^[a-f0-9-]{16,64}$")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    id: str = Field(..., pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    driver: str = "file"  # "file" (IDE, manual) or "cli" (auto-spawn)
    command: str = ""      # CLI command template (for driver="cli")
    capabilities: list[str] = Field(default_factory=list)
    reliability: float = 0.9
    queue_health: float = 0.9
    cost: float = 0.5
