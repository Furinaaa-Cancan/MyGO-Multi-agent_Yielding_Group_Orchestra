"""Tests for agent router."""

import pytest
from pathlib import Path
from unittest.mock import patch

from multi_agent.router import (
    load_agents, eligible_agents, pick_agent, pick_reviewer,
    load_registry, get_defaults, get_strategy,
    resolve_builder, resolve_reviewer,
)
from multi_agent.schema import AgentProfile, SkillContract


PROFILES_PATH = Path(__file__).parent.parent / "agents" / "profiles.json"
AGENTS_YAML_PATH = Path(__file__).parent.parent / "agents" / "agents.yaml"


def _make_contract(**kwargs) -> SkillContract:
    defaults = {"id": "test-skill", "version": "1.0.0"}
    defaults.update(kwargs)
    return SkillContract(**defaults)


def _make_agents() -> list[AgentProfile]:
    return [
        AgentProfile(id="windsurf", capabilities=["implementation", "testing"]),
        AgentProfile(id="cursor", capabilities=["implementation", "review"]),
        AgentProfile(id="kiro", capabilities=["implementation", "review"]),
    ]


class TestRegistry:
    def test_load_yaml(self):
        reg = load_registry(AGENTS_YAML_PATH)
        assert reg["version"] == 2
        ids = {a["id"] for a in reg["agents"]}
        assert "windsurf" in ids
        assert "cursor" in ids

    def test_fallback_to_json(self):
        reg = load_registry(PROFILES_PATH)
        assert reg["version"] == 1
        assert reg["role_strategy"] == "auto"

    def test_get_defaults(self):
        defaults = get_defaults(AGENTS_YAML_PATH)
        assert "builder" in defaults
        assert "reviewer" in defaults

    def test_get_strategy(self):
        strategy = get_strategy(AGENTS_YAML_PATH)
        assert strategy == "manual"


class TestResolveBuilder:
    def test_explicit(self):
        agents = _make_agents()
        contract = _make_contract()
        result = resolve_builder(agents, contract, explicit="kiro")
        assert result == "kiro"

    def test_fallback_auto(self):
        agents = _make_agents()
        contract = _make_contract()
        result = resolve_builder(agents, contract)
        assert result in {"windsurf", "cursor", "kiro"}


class TestResolveReviewer:
    def test_explicit(self):
        agents = _make_agents()
        contract = _make_contract()
        result = resolve_reviewer(agents, contract, builder_id="windsurf", explicit="cursor")
        assert result == "cursor"

    def test_explicit_same_as_builder_raises(self):
        agents = _make_agents()
        contract = _make_contract()
        with pytest.raises(ValueError, match="cannot be the same"):
            resolve_reviewer(agents, contract, builder_id="cursor", explicit="cursor")

    def test_auto_differs_from_builder(self):
        agents = _make_agents()
        contract = _make_contract()
        result = resolve_reviewer(agents, contract, builder_id="windsurf")
        assert result != "windsurf"


class TestLoadAgents:
    def test_load_json(self):
        agents = load_agents(PROFILES_PATH)
        assert len(agents) == 3
        ids = {a.id for a in agents}
        assert ids == {"codex", "windsurf", "antigravity"}

    def test_load_yaml(self):
        agents = load_agents(AGENTS_YAML_PATH)
        ids = {a.id for a in agents}
        assert "windsurf" in ids
        assert "cursor" in ids

    def test_load_yaml_parses_driver_fields(self):
        agents = load_agents(AGENTS_YAML_PATH)
        by_id = {a.id: a for a in agents}
        # CLI agents should have driver="cli" and a command
        assert by_id["claude"].driver == "cli"
        assert "claude" in by_id["claude"].command
        assert by_id["codex"].driver == "cli"
        # IDE agents should have driver="file"
        assert by_id["windsurf"].driver == "file"
        assert by_id["windsurf"].command == ""


class TestEligible:
    def test_all_eligible(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["codex", "windsurf", "antigravity"])
        result = eligible_agents(agents, contract, ["implementation"])
        # antigravity has implementation capability
        ids = {a.id for a in result}
        assert "windsurf" in ids
        assert "codex" in ids

    def test_filter_by_supported(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["windsurf"])
        result = eligible_agents(agents, contract, ["implementation"])
        assert len(result) == 1
        assert result[0].id == "windsurf"

    def test_filter_by_capability(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=[])
        result = eligible_agents(agents, contract, ["security"])
        # Only antigravity has security
        assert len(result) == 1
        assert result[0].id == "antigravity"


class TestPickAgent:
    def test_pick_builder(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["codex", "windsurf", "antigravity"])
        agent = pick_agent(agents, contract, ["implementation"], role="builder")
        assert agent.id in {"codex", "windsurf", "antigravity"}

    def test_pick_with_exclude(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["codex", "windsurf", "antigravity"])
        agent = pick_agent(
            agents, contract, ["implementation"], role="builder", exclude=["windsurf"]
        )
        assert agent.id != "windsurf"

    def test_no_eligible(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["nonexistent"])
        with pytest.raises(ValueError, match="No eligible agent"):
            pick_agent(agents, contract, ["implementation"])


class TestPickReviewer:
    def test_reviewer_differs_from_builder(self):
        agents = load_agents(PROFILES_PATH)
        contract = _make_contract(supported_agents=["codex", "windsurf", "antigravity"])
        reviewer = pick_reviewer(agents, contract, builder_id="windsurf")
        assert reviewer.id != "windsurf"
        assert "review" in reviewer.capabilities


class TestRouterEdgeCases:
    """Task 41: Router edge case tests."""

    def test_single_agent_builder_ok(self):
        agents = [AgentProfile(id="solo", capabilities=["implementation"])]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}):
            result = resolve_builder(agents, contract)
        assert result == "solo"

    def test_single_agent_reviewer_raises(self):
        agents = [AgentProfile(id="solo", capabilities=["implementation", "review"])]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}):
            with pytest.raises(ValueError):
                resolve_reviewer(agents, contract, builder_id="solo")

    def test_three_agents_sorted_by_reliability(self):
        agents = [
            AgentProfile(id="low", capabilities=["implementation"], reliability=0.5),
            AgentProfile(id="high", capabilities=["implementation"], reliability=0.99),
            AgentProfile(id="mid", capabilities=["implementation"], reliability=0.75),
        ]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}):
            result = resolve_builder(agents, contract)
        assert result == "high"  # highest reliability

    def test_supported_agents_constraint(self):
        agents = _make_agents()
        contract = _make_contract(supported_agents=["kiro"])
        result = eligible_agents(agents, contract, ["implementation"])
        assert len(result) == 1
        assert result[0].id == "kiro"

    def test_all_excluded_raises(self):
        agents = [
            AgentProfile(id="a", capabilities=["implementation"]),
            AgentProfile(id="b", capabilities=["implementation"]),
        ]
        contract = _make_contract()
        with pytest.raises(ValueError):
            pick_agent(agents, contract, ["implementation"], exclude=["a", "b"])

    def test_capability_filter(self):
        agents = [
            AgentProfile(id="no-review", capabilities=["implementation"]),
            AgentProfile(id="has-review", capabilities=["implementation", "review"]),
        ]
        contract = _make_contract()
        result = eligible_agents(agents, contract, ["review"])
        assert len(result) == 1
        assert result[0].id == "has-review"

    def test_v1_json_format_compat(self):
        reg = load_registry(PROFILES_PATH)
        assert reg["version"] == 1
        agents = load_agents(PROFILES_PATH)
        assert len(agents) > 0


class TestAgentHealthCheck:
    """Task 72: Agent health check tests."""

    def test_healthy_agent(self):
        from multi_agent.router import check_agent_health
        agents = [AgentProfile(id="ws", capabilities=["implementation"], reliability=0.9)]
        results = check_agent_health(agents)
        assert len(results) == 1
        assert results[0]["status"] == "healthy"
        assert results[0]["issues"] == []

    def test_low_reliability_degraded(self):
        from multi_agent.router import check_agent_health
        agents = [AgentProfile(id="bad", capabilities=["impl"], reliability=0.3)]
        results = check_agent_health(agents)
        assert results[0]["status"] == "degraded"
        assert any("reliability" in i for i in results[0]["issues"])

    def test_cli_no_command_degraded(self):
        from multi_agent.router import check_agent_health
        agents = [AgentProfile(id="cli-agent", driver="cli", command="", capabilities=["impl"])]
        results = check_agent_health(agents)
        assert results[0]["status"] == "degraded"
        assert any("no command" in i for i in results[0]["issues"])

    def test_no_capabilities_degraded(self):
        from multi_agent.router import check_agent_health
        agents = [AgentProfile(id="empty", capabilities=[])]
        results = check_agent_health(agents)
        assert any("no capabilities" in i for i in results[0]["issues"])
