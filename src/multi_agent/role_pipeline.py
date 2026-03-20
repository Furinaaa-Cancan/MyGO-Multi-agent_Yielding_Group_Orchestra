"""Role Pipeline — configurable multi-role workflow sequences.

Generalizes the fixed plan->build->review->decide pipeline into configurable
role sequences that can be adapted per-task based on complexity.

Inspired by:
- MetaGPT (ICLR 2024): multi-role SOP pipeline (PM->Architect->Engineer->QA)
- MASAI (arXiv 2024): modular sub-agents with per-task strategy tuning
- CodeR (arXiv 2024): pre-defined task graphs with plan selection (A/B/C)

Novel contribution: dynamic pipeline selection per sub-task in a black-box
IDE agent orchestration framework. Each sub-task gets an optimized role
sequence based on its classification (bugfix, feature, refactor, test).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence

_log = logging.getLogger(__name__)


# ── Role Kinds ────────────────────────────────────────────


class RoleKind(StrEnum):
    """Enumeration of roles that can appear in a pipeline.

    Each role maps to a logical step in the software engineering workflow.
    The ordering in the enum reflects typical execution order, but pipelines
    can reorder or omit roles as needed.
    """

    PLAN = "plan"
    """Decompose the task into actionable steps and acceptance criteria."""

    ARCHITECT = "architect"
    """Design high-level structure: API boundaries, file layout, patterns."""

    BUILD = "build"
    """Implement the code changes (delegated to an IDE agent)."""

    VERIFY = "verify"
    """Run automated checks: tests, linting, type-checking."""

    REVIEW = "review"
    """Cross-model adversarial code review (delegated to a second agent)."""

    DECIDE = "decide"
    """Final gate: approve, request changes, or escalate."""


# ── Role Specification ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Specification for a single role within a pipeline.

    Attributes:
        kind: Which logical role this step represents.
        agent_capability: Capability string used for agent routing
            (e.g. ``"implementation"``, ``"review"``, ``"architecture"``).
        template_name: Name of the prompt template to use for this step.
        timeout_sec: Maximum wall-clock seconds allowed for this step.
        is_interrupt: If ``True``, the orchestrator waits for an external
            agent to complete (file-based handoff). If ``False``, the step
            runs inline.
        skip_on_retry: If ``True``, this step is skipped when the pipeline
            is re-executed after a ``request_changes`` decision. Useful for
            planning steps that don't need to re-run.
    """

    kind: RoleKind
    agent_capability: str = ""
    template_name: str = ""
    timeout_sec: int = 1800
    is_interrupt: bool = False
    skip_on_retry: bool = False

    def __post_init__(self) -> None:
        # Auto-derive capability and template from kind if not provided
        _defaults: dict[RoleKind, tuple[str, str]] = {
            RoleKind.PLAN: ("planning", "plan"),
            RoleKind.ARCHITECT: ("architecture", "architect"),
            RoleKind.BUILD: ("implementation", "build"),
            RoleKind.VERIFY: ("verification", "verify"),
            RoleKind.REVIEW: ("review", "review"),
            RoleKind.DECIDE: ("decision", "decide"),
        }
        cap, tmpl = _defaults.get(self.kind, ("", ""))
        if not self.agent_capability:
            object.__setattr__(self, "agent_capability", cap)
        if not self.template_name:
            object.__setattr__(self, "template_name", tmpl)


# ── Pipeline Configuration ───────────────────────────────


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """A named, ordered sequence of roles that defines a workflow pipeline.

    Attributes:
        name: Short identifier for this pipeline (e.g. ``"standard"``).
        roles: Ordered list of role specifications to execute.
        description: Human-readable description of when to use this pipeline.
        suitable_for: List of task type strings this pipeline is designed for.
    """

    name: str
    roles: tuple[RoleSpec, ...] = ()
    description: str = ""
    suitable_for: list[str] = field(default_factory=list)

    @property
    def role_kinds(self) -> list[RoleKind]:
        """Return the ordered list of role kinds in this pipeline."""
        return [r.kind for r in self.roles]

    @property
    def total_timeout_sec(self) -> int:
        """Sum of all role timeouts — upper bound on pipeline duration."""
        return sum(r.timeout_sec for r in self.roles)

    def steps_for_retry(self) -> tuple[RoleSpec, ...]:
        """Return the subset of roles that execute on retry iterations.

        Roles with ``skip_on_retry=True`` (typically PLAN) are excluded.
        """
        return tuple(r for r in self.roles if not r.skip_on_retry)

    def has_role(self, kind: RoleKind) -> bool:
        """Check whether a given role kind appears in this pipeline."""
        return any(r.kind == kind for r in self.roles)

    def get_role(self, kind: RoleKind) -> RoleSpec | None:
        """Return the first RoleSpec matching ``kind``, or ``None``."""
        for r in self.roles:
            if r.kind == kind:
                return r
        return None


# ── Predefined Pipelines ─────────────────────────────────

MINIMAL = PipelineConfig(
    name="minimal",
    roles=(
        RoleSpec(kind=RoleKind.PLAN, timeout_sec=300, skip_on_retry=True),
        RoleSpec(kind=RoleKind.BUILD, timeout_sec=1800, is_interrupt=True),
        RoleSpec(kind=RoleKind.DECIDE, timeout_sec=120),
    ),
    description=(
        "Lightweight pipeline for trivial bugfixes and test-only changes. "
        "Skips review to minimize latency and token cost."
    ),
    suitable_for=["bugfix", "test", "docs"],
)

STANDARD = PipelineConfig(
    name="standard",
    roles=(
        RoleSpec(kind=RoleKind.PLAN, timeout_sec=300, skip_on_retry=True),
        RoleSpec(kind=RoleKind.BUILD, timeout_sec=1800, is_interrupt=True),
        RoleSpec(kind=RoleKind.REVIEW, timeout_sec=900, is_interrupt=True),
        RoleSpec(kind=RoleKind.DECIDE, timeout_sec=120),
    ),
    description=(
        "Default pipeline matching the current 4-node workflow. "
        "Includes cross-model adversarial review for quality assurance."
    ),
    suitable_for=["simple_feature", "enhancement"],
)

VERIFIED = PipelineConfig(
    name="verified",
    roles=(
        RoleSpec(kind=RoleKind.PLAN, timeout_sec=300, skip_on_retry=True),
        RoleSpec(kind=RoleKind.BUILD, timeout_sec=1800, is_interrupt=True),
        RoleSpec(kind=RoleKind.VERIFY, timeout_sec=600),
        RoleSpec(kind=RoleKind.REVIEW, timeout_sec=900, is_interrupt=True),
        RoleSpec(kind=RoleKind.DECIDE, timeout_sec=120),
    ),
    description=(
        "Adds automated verification (tests, lint) between build and review. "
        "Catches mechanical errors before consuming reviewer tokens."
    ),
    suitable_for=["feature", "refactor"],
)

FULL = PipelineConfig(
    name="full",
    roles=(
        RoleSpec(kind=RoleKind.PLAN, timeout_sec=300, skip_on_retry=True),
        RoleSpec(kind=RoleKind.ARCHITECT, timeout_sec=600, skip_on_retry=True),
        RoleSpec(kind=RoleKind.BUILD, timeout_sec=2400, is_interrupt=True),
        RoleSpec(kind=RoleKind.VERIFY, timeout_sec=600),
        RoleSpec(kind=RoleKind.REVIEW, timeout_sec=900, is_interrupt=True),
        RoleSpec(kind=RoleKind.DECIDE, timeout_sec=120),
    ),
    description=(
        "Full pipeline for complex features. Architect step designs the "
        "high-level structure before implementation begins, reducing "
        "rework cycles (MetaGPT finding: architecture-first reduces "
        "token usage by ~30%%)."
    ),
    suitable_for=["complex_feature", "large_refactor"],
)

# ── Pipeline Registry ────────────────────────────────────

_REGISTRY: dict[str, PipelineConfig] = {
    p.name: p for p in [MINIMAL, STANDARD, VERIFIED, FULL]
}

# Task type -> pipeline name mapping
# Aligned with SubTask.skill_id and adaptive_decompose classifications
_TASK_TYPE_MAP: dict[str, str] = {
    "bugfix": "minimal",
    "test": "minimal",
    "docs": "minimal",
    "simple_feature": "standard",
    "enhancement": "standard",
    "feature": "verified",
    "refactor": "verified",
    "complex_feature": "full",
    "large_refactor": "full",
}

# Complexity level overrides: higher complexity can bump a pipeline up
_COMPLEXITY_UPGRADE: dict[str, str] = {
    "minimal": "standard",
    "standard": "verified",
    "verified": "full",
    "full": "full",  # already maximal
}


def get_pipeline(name: str) -> PipelineConfig:
    """Look up a pipeline configuration by name.

    Args:
        name: Pipeline name (e.g. ``"standard"``, ``"verified"``).

    Returns:
        The matching ``PipelineConfig``.

    Raises:
        KeyError: If no pipeline with the given name is registered.
    """
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(
            f"Unknown pipeline '{name}'. Available: {available}"
        )
    return _REGISTRY[name]


def list_pipelines() -> list[PipelineConfig]:
    """Return all registered pipeline configurations.

    Ordered by increasing complexity (number of roles).
    """
    return sorted(_REGISTRY.values(), key=lambda p: len(p.roles))


def register_pipeline(pipeline: PipelineConfig) -> None:
    """Register a custom pipeline configuration.

    Overwrites any existing pipeline with the same name.

    Args:
        pipeline: The pipeline configuration to register.
    """
    _REGISTRY[pipeline.name] = pipeline
    _log.info("Registered pipeline '%s' with %d roles", pipeline.name, len(pipeline.roles))


def select_pipeline(
    task_type: str,
    complexity_level: str = "medium",
) -> PipelineConfig:
    """Select the optimal pipeline for a given task type and complexity.

    The selection logic:
    1. Map ``task_type`` to a base pipeline via ``_TASK_TYPE_MAP``.
    2. If ``complexity_level`` is ``"complex"`` or ``"high"``, upgrade the
       pipeline one tier (e.g. standard -> verified).
    3. If ``complexity_level`` is ``"simple"`` or ``"low"``, keep or
       downgrade the pipeline.

    Args:
        task_type: Classification of the task (e.g. ``"bugfix"``,
            ``"feature"``, ``"complex_feature"``).
        complexity_level: Estimated complexity: ``"simple"``, ``"medium"``,
            ``"complex"``, or ``"high"``.

    Returns:
        The selected ``PipelineConfig``.

    Examples:
        >>> select_pipeline("bugfix").name
        'minimal'
        >>> select_pipeline("feature", "complex").name
        'full'
        >>> select_pipeline("simple_feature", "simple").name
        'standard'
    """
    # Step 1: base pipeline from task type
    base_name = _TASK_TYPE_MAP.get(task_type, "standard")

    # Step 2: adjust for complexity
    if complexity_level in ("complex", "high"):
        pipeline_name = _COMPLEXITY_UPGRADE.get(base_name, base_name)
        _log.debug(
            "Complexity '%s' upgraded pipeline: %s -> %s",
            complexity_level, base_name, pipeline_name,
        )
    elif complexity_level in ("simple", "low") and base_name != "minimal":
        # Don't downgrade below minimal; just keep the base
        pipeline_name = base_name
        _log.debug(
            "Complexity '%s' keeps pipeline at '%s'",
            complexity_level, pipeline_name,
        )
    else:
        pipeline_name = base_name

    return _REGISTRY[pipeline_name]


def pipeline_summary(pipeline: PipelineConfig) -> str:
    """Return a concise human-readable summary of a pipeline.

    Useful for logging and CLI display.

    Example output::

        standard: plan -> build -> review -> decide (4 roles, 3120s max)
    """
    arrow_chain = " -> ".join(r.kind.value for r in pipeline.roles)
    return (
        f"{pipeline.name}: {arrow_chain} "
        f"({len(pipeline.roles)} roles, {pipeline.total_timeout_sec}s max)"
    )
