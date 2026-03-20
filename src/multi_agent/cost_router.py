"""Cost-Aware Router — FinOps-integrated agent selection.

Extends the base router with cost-complexity aware scoring for intelligent
agent selection. Simple tasks route to cheaper agents; complex tasks route
to more capable (expensive) ones.

Inspired by:
- FinOps principles: cost as a first-class design concern
- MASAI (arXiv 2024): per-sub-agent strategy tuning
- AgentCoder (arXiv 2024): 56.9K tokens vs MetaGPT's 138.2K — efficiency matters

Novel contribution: coupling complexity estimation with agent cost tiers
for budget-aware routing in multi-agent SE systems. No prior framework
integrates FinOps directly into agent selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Sequence

from multi_agent.finops import aggregate_usage, check_budget
from multi_agent.router import load_agents, _eligible
from multi_agent.schema import AgentProfile, SkillContract

_log = logging.getLogger(__name__)


# ── Cost Tiers ────────────────────────────────────────────


class CostTier(StrEnum):
    """Agent cost classification tiers.

    Tiers map to rough cost brackets:
    - ECONOMY: low-cost models (e.g. GPT-4.1-mini, Claude Haiku, Codex mini)
    - STANDARD: mid-range models (e.g. GPT-4o, Claude Sonnet)
    - PREMIUM: top-tier models (e.g. o3, Claude Opus, GPT-4.1)
    """

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


# ── Cost tier boundaries for agent.cost field ─────────────
# agent.cost is a 0.0-1.0 float in AgentProfile
_TIER_THRESHOLDS: dict[CostTier, tuple[float, float]] = {
    CostTier.ECONOMY: (0.0, 0.33),
    CostTier.STANDARD: (0.33, 0.67),
    CostTier.PREMIUM: (0.67, 1.01),
}


def agent_cost_tier(agent: AgentProfile) -> CostTier:
    """Classify an agent into a cost tier based on its ``cost`` field.

    Args:
        agent: The agent profile to classify.

    Returns:
        The cost tier for this agent.
    """
    for tier, (low, high) in _TIER_THRESHOLDS.items():
        if low <= agent.cost < high:
            return tier
    return CostTier.STANDARD  # fallback


# ── Scoring ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CostAwareScore:
    """Composite score for cost-aware agent selection.

    Attributes:
        agent_id: Identifier of the scored agent.
        base_score: Product of reliability and queue_health (0.0-1.0).
        cost_factor: Multiplier based on tier match (1.0 = perfect match).
        complexity_factor: Multiplier reflecting task complexity alignment.
        budget_factor: Multiplier based on remaining budget health.
        final_score: Composite score = base * cost_factor * budget_factor.
        reasoning: Human-readable explanation of the score components.
    """

    agent_id: str
    base_score: float
    cost_factor: float
    complexity_factor: float
    budget_factor: float
    final_score: float
    reasoning: str


def complexity_to_cost_tier(complexity_level: str) -> CostTier:
    """Map a complexity level string to the preferred cost tier.

    The mapping reflects the principle that simple tasks should use
    cheaper agents (saving budget), while complex tasks benefit from
    more capable (expensive) agents.

    Args:
        complexity_level: One of ``"simple"``, ``"low"``, ``"medium"``,
            ``"complex"``, ``"high"``.

    Returns:
        The recommended ``CostTier`` for this complexity level.

    Examples:
        >>> complexity_to_cost_tier("simple")
        <CostTier.ECONOMY: 'economy'>
        >>> complexity_to_cost_tier("complex")
        <CostTier.PREMIUM: 'premium'>
    """
    _map: dict[str, CostTier] = {
        "simple": CostTier.ECONOMY,
        "low": CostTier.ECONOMY,
        "medium": CostTier.STANDARD,
        "complex": CostTier.PREMIUM,
        "high": CostTier.PREMIUM,
    }
    return _map.get(complexity_level, CostTier.STANDARD)


def _tier_distance(tier_a: CostTier, tier_b: CostTier) -> int:
    """Compute the ordinal distance between two cost tiers.

    Returns 0 if tiers match, 1 if adjacent, 2 if extreme ends.
    """
    order = [CostTier.ECONOMY, CostTier.STANDARD, CostTier.PREMIUM]
    return abs(order.index(tier_a) - order.index(tier_b))


def _compute_budget_factor(
    budget_remaining: float | None,
    total_budget: float | None,
) -> float:
    """Compute a budget health multiplier in [0.3, 1.0].

    When budget is healthy (>50% remaining), returns 1.0.
    As budget depletes below 50%, the factor decreases linearly
    to a floor of 0.3 (never fully zeroes out, to avoid deadlocks).

    Args:
        budget_remaining: Remaining budget in USD (None = unlimited).
        total_budget: Total budget in USD (None = unlimited).

    Returns:
        Budget factor multiplier.
    """
    if budget_remaining is None or total_budget is None or total_budget <= 0:
        return 1.0

    ratio = budget_remaining / total_budget
    if ratio >= 0.5:
        return 1.0

    # Linear decay from 1.0 at 50% to 0.3 at 0%
    # factor = 0.3 + 1.4 * ratio  (at ratio=0 -> 0.3, at ratio=0.5 -> 1.0)
    return max(0.3, 0.3 + 1.4 * ratio)


def score_agent(
    agent: AgentProfile,
    task_complexity: str = "medium",
    budget_remaining: float | None = None,
    total_budget: float | None = None,
) -> CostAwareScore:
    """Compute a cost-aware composite score for an agent.

    The score combines three factors:

    1. **Base score**: ``reliability * queue_health`` — how likely the agent
       is to produce a correct result in a timely manner.

    2. **Cost factor**: How well the agent's cost tier matches the task
       complexity tier. Perfect match = 1.0, one tier off = 0.7,
       two tiers off = 0.4.

    3. **Budget factor**: Scales down scores when budget is depleted,
       favoring cheaper agents as money runs out.

    Args:
        agent: The agent profile to score.
        task_complexity: Complexity level of the task.
        budget_remaining: Remaining budget in USD (``None`` = unlimited).
        total_budget: Total budget in USD (``None`` = unlimited).

    Returns:
        A ``CostAwareScore`` with all factor breakdowns.
    """
    # Factor 1: base reliability * health
    base = agent.reliability * agent.queue_health

    # Factor 2: cost tier alignment
    agent_tier = agent_cost_tier(agent)
    target_tier = complexity_to_cost_tier(task_complexity)
    dist = _tier_distance(agent_tier, target_tier)
    cost_factor_map = {0: 1.0, 1: 0.7, 2: 0.4}
    cost_factor = cost_factor_map.get(dist, 0.4)

    # Factor 3: budget health
    budget_factor = _compute_budget_factor(budget_remaining, total_budget)

    # Composite
    final = base * cost_factor * budget_factor

    # Build reasoning string
    parts = [
        f"base={base:.2f} (rel={agent.reliability:.2f} * health={agent.queue_health:.2f})",
        f"cost_factor={cost_factor:.1f} (agent_tier={agent_tier.value}, target={target_tier.value}, dist={dist})",
        f"budget_factor={budget_factor:.2f}",
        f"final={final:.3f}",
    ]
    reasoning = "; ".join(parts)

    return CostAwareScore(
        agent_id=agent.id,
        base_score=round(base, 4),
        cost_factor=cost_factor,
        complexity_factor=cost_factor,  # same as cost_factor in this model
        budget_factor=round(budget_factor, 4),
        final_score=round(final, 4),
        reasoning=reasoning,
    )


# ── Agent Selection ──────────────────────────────────────


def select_agent_cost_aware(
    agents: list[AgentProfile],
    contract: SkillContract,
    role: str = "builder",
    complexity_level: str = "medium",
    budget_info: dict[str, Any] | None = None,
) -> AgentProfile:
    """Select the best agent considering cost, complexity, and budget.

    This is the primary entry point for cost-aware routing. It extends
    the base router's capability-based filtering with composite scoring.

    Args:
        agents: Available agent profiles.
        contract: The skill contract constraining agent compatibility.
        role: Role being filled (``"builder"``, ``"reviewer"``, etc.).
        complexity_level: Task complexity for tier matching.
        budget_info: Optional dict with ``"remaining"`` and ``"total"``
            keys (USD floats). If ``None``, budget is ignored.

    Returns:
        The highest-scoring eligible ``AgentProfile``.

    Raises:
        ValueError: If no eligible agents are found.
    """
    # Map role to required capabilities
    role_caps: dict[str, list[str]] = {
        "builder": ["implementation"],
        "reviewer": ["review"],
        "architect": ["architecture"],
        "verifier": ["verification"],
        "planner": ["planning"],
    }
    caps = role_caps.get(role, [])

    # Use base router filtering for compatibility
    eligible = _eligible(agents, contract, caps)
    if not eligible:
        # Fallback: try without capability filter
        eligible = _eligible(agents, contract, [])

    if not eligible:
        raise ValueError(
            f"No eligible agent for role={role}, skill={contract.id}, "
            f"complexity={complexity_level}"
        )

    # Extract budget info
    remaining = None
    total = None
    if budget_info:
        remaining = budget_info.get("remaining")
        total = budget_info.get("total")

    # Score all eligible agents
    scored: list[tuple[CostAwareScore, AgentProfile]] = []
    for agent in eligible:
        s = score_agent(agent, complexity_level, remaining, total)
        scored.append((s, agent))
        _log.debug("Cost-aware score for '%s': %s", agent.id, s.reasoning)

    # Sort by final score descending
    scored.sort(key=lambda pair: pair[0].final_score, reverse=True)

    winner_score, winner = scored[0]
    _log.info(
        "Cost-aware routing: selected '%s' (score=%.3f) for role=%s, "
        "complexity=%s [%d candidates]",
        winner.id,
        winner_score.final_score,
        role,
        complexity_level,
        len(scored),
    )

    return winner


# ── Cost Estimation ──────────────────────────────────────


def estimate_task_cost(
    pipeline_name: str,
    agents: list[AgentProfile] | None = None,
    complexity_level: str = "medium",
) -> float:
    """Estimate total USD cost for executing a task through a pipeline.

    Uses rough per-role token estimates combined with agent cost tiers
    to project total spend. This is a planning heuristic, not an exact
    prediction.

    Per-role token estimates (input + output):
    - PLAN: ~2K tokens
    - ARCHITECT: ~4K tokens
    - BUILD: ~15K tokens (dominant cost)
    - VERIFY: ~1K tokens (mostly tool output)
    - REVIEW: ~8K tokens
    - DECIDE: ~500 tokens

    Args:
        pipeline_name: Name of the pipeline to estimate for.
        agents: Agent list (uses registry if ``None``).
        complexity_level: Affects token multiplier.

    Returns:
        Estimated cost in USD.
    """
    from multi_agent.finops import estimate_cost as _estimate_cost
    from multi_agent.role_pipeline import get_pipeline, RoleKind

    pipeline = get_pipeline(pipeline_name)
    if agents is None:
        agents = load_agents()

    # Base token estimates per role kind
    _base_tokens: dict[RoleKind, tuple[int, int]] = {
        RoleKind.PLAN: (1500, 500),
        RoleKind.ARCHITECT: (2500, 1500),
        RoleKind.BUILD: (8000, 7000),
        RoleKind.VERIFY: (800, 200),
        RoleKind.REVIEW: (5000, 3000),
        RoleKind.DECIDE: (400, 100),
    }

    # Complexity multiplier
    _complexity_mult: dict[str, float] = {
        "simple": 0.5,
        "low": 0.6,
        "medium": 1.0,
        "complex": 1.8,
        "high": 2.5,
    }
    mult = _complexity_mult.get(complexity_level, 1.0)

    total_cost = 0.0
    for role_spec in pipeline.roles:
        inp, out = _base_tokens.get(role_spec.kind, (1000, 500))
        inp = int(inp * mult)
        out = int(out * mult)
        # Use default model pricing
        role_cost = _estimate_cost(inp, out)
        total_cost += role_cost

    return round(total_cost, 4)


def check_budget_before_dispatch(
    task_id: str,
    estimated_cost: float,
    max_budget: float | None = None,
) -> tuple[bool, str]:
    """Pre-dispatch budget check: can we afford to run this task?

    Queries the FinOps module for current spend, adds the estimated cost,
    and checks whether the total would exceed the budget.

    Args:
        task_id: Identifier for the task being dispatched (for logging).
        estimated_cost: Projected cost of this task in USD.
        max_budget: Budget cap in USD. If ``None``, reads from project config.

    Returns:
        A tuple of ``(allowed, reason)``:
        - ``(True, "ok")`` if within budget.
        - ``(False, reason_string)`` if over budget.
    """
    budget_status = check_budget(max_cost=max_budget)

    current_cost = budget_status.get("total_cost", 0.0)
    budget_limit = budget_status.get("budget_usd")

    if budget_limit is None:
        # No budget configured — always allow
        _log.debug(
            "No budget limit configured; allowing task '%s' (est. $%.4f)",
            task_id, estimated_cost,
        )
        return True, "ok"

    projected = current_cost + estimated_cost
    if projected > budget_limit:
        reason = (
            f"Budget exceeded: current=${current_cost:.4f} + "
            f"estimated=${estimated_cost:.4f} = ${projected:.4f} > "
            f"limit=${budget_limit:.4f}"
        )
        _log.warning(
            "Budget check FAILED for task '%s': %s", task_id, reason,
        )
        return False, reason

    # Check if we're in the warning zone (>80% spent)
    if budget_limit > 0 and projected / budget_limit > 0.8:
        _log.warning(
            "Budget warning for task '%s': projected spend $%.4f is >80%% "
            "of limit $%.4f",
            task_id, projected, budget_limit,
        )

    _log.debug(
        "Budget check OK for task '%s': $%.4f + $%.4f = $%.4f < $%.4f",
        task_id, current_cost, estimated_cost, projected, budget_limit,
    )
    return True, "ok"


# ── Reporting Helpers ────────────────────────────────────


def rank_agents(
    agents: list[AgentProfile] | None = None,
    contract: SkillContract | None = None,
    complexity_level: str = "medium",
    budget_info: dict[str, Any] | None = None,
) -> list[CostAwareScore]:
    """Score and rank all agents for diagnostic/reporting purposes.

    Unlike ``select_agent_cost_aware``, this returns scores for ALL agents
    (not just the winner), useful for CLI diagnostics and dashboards.

    Args:
        agents: Agent list (uses registry if ``None``).
        contract: Optional skill contract for filtering.
        complexity_level: Task complexity for scoring.
        budget_info: Optional budget info dict.

    Returns:
        List of ``CostAwareScore`` objects sorted by descending final_score.
    """
    if agents is None:
        agents = load_agents()

    remaining = budget_info.get("remaining") if budget_info else None
    total = budget_info.get("total") if budget_info else None

    scores = [
        score_agent(agent, complexity_level, remaining, total)
        for agent in agents
    ]
    scores.sort(key=lambda s: s.final_score, reverse=True)
    return scores


def format_ranking(scores: list[CostAwareScore]) -> str:
    """Format a ranking table for CLI display.

    Args:
        scores: Scored agents (pre-sorted by ``rank_agents``).

    Returns:
        Multi-line string table.
    """
    lines = [
        f"{'Rank':<5} {'Agent':<20} {'Final':<8} {'Base':<8} "
        f"{'Cost':<8} {'Budget':<8}",
        "-" * 60,
    ]
    for i, s in enumerate(scores, 1):
        lines.append(
            f"{i:<5} {s.agent_id:<20} {s.final_score:<8.3f} "
            f"{s.base_score:<8.3f} {s.cost_factor:<8.1f} "
            f"{s.budget_factor:<8.2f}"
        )
    return "\n".join(lines)
