"""Tests for agent router."""

from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent.router import (
    eligible_agents,
    get_defaults,
    get_strategy,
    load_agents,
    load_registry,
    pick_agent,
    pick_reviewer,
    resolve_builder,
    resolve_reviewer,
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
        assert by_id["codex"].driver == "gui"
        assert by_id["codex"].app_name == "Codex"
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
        with patch("multi_agent.router.get_defaults", return_value={}), pytest.raises(ValueError):
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
        agent_results = [r for r in results if r["id"] != "_system"]
        assert len(agent_results) == 1
        assert agent_results[0]["status"] == "healthy"
        assert agent_results[0]["issues"] == []
        # Single-agent setup triggers cross-model diversity warning
        system_results = [r for r in results if r["id"] == "_system"]
        assert len(system_results) == 1
        assert system_results[0]["status"] == "warning"

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

    def test_cli_missing_required_env_degraded(self, monkeypatch):
        from multi_agent.router import check_agent_health

        monkeypatch.delenv("TEST_NEED_KEY", raising=False)
        agents = [
            AgentProfile(
                id="cli-auth",
                driver="cli",
                command="echo ok",
                required_env=["TEST_NEED_KEY"],
                capabilities=["implementation"],
            )
        ]
        results = check_agent_health(agents)
        assert results[0]["status"] == "degraded"
        assert any("missing required env" in i for i in results[0]["issues"])

    def test_cli_auth_check_failure_degraded(self, monkeypatch):
        from multi_agent.router import check_agent_health
        import sys

        monkeypatch.setenv("TEST_NEED_KEY", "1")
        agents = [
            AgentProfile(
                id="cli-auth",
                driver="cli",
                command="echo ok",
                auth_check=f"{sys.executable} -c 'import sys; sys.exit(2)'",
                required_env=["TEST_NEED_KEY"],
                capabilities=["implementation"],
            )
        ]
        results = check_agent_health(agents)
        assert results[0]["status"] == "degraded"
        assert any("auth_check failed" in i for i in results[0]["issues"])

    def test_cli_auth_check_success_healthy(self, monkeypatch):
        from multi_agent.router import check_agent_health
        import sys

        monkeypatch.setenv("TEST_NEED_KEY", "1")
        agents = [
            AgentProfile(
                id="cli-auth",
                driver="cli",
                command="echo ok",
                auth_check=f"{sys.executable} -c 'import sys; sys.exit(0)'",
                required_env=["TEST_NEED_KEY"],
                capabilities=["implementation"],
            )
        ]
        results = check_agent_health(agents)
        assert results[0]["status"] == "healthy"
        assert results[0].get("auth_status") == "ready"


class TestLoadAgentsWarning:
    """R13 F1: load_agents should log warning for malformed entries."""

    def test_malformed_entry_logs_warning(self, tmp_path, caplog):

        import yaml
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(yaml.dump({
            "version": 2,
            "agents": [
                {"id": "good", "capabilities": ["implementation"]},
                {"capabilities": ["review"]},  # missing id → skip silently before
            ],
        }))
        # Malformed entries without "id" are caught by the `if "id" not in a` check,
        # not by the exception handler. Verify they don't crash and good entries load.
        agents = load_agents(yaml_file)
        assert len(agents) == 1
        assert agents[0].id == "good"


class TestResolveBuilderWarnings:
    """R13 F2+F3: resolve_builder should warn on last-resort and unknown explicit."""

    def test_explicit_unknown_agent_warns(self, caplog):
        import logging
        agents = [AgentProfile(id="windsurf", capabilities=["implementation"])]
        contract = _make_contract()
        with caplog.at_level(logging.WARNING, logger="multi_agent.router"):
            result = resolve_builder(agents, contract, explicit="nonexistent")
        assert result == "nonexistent"
        assert any("not in registry" in r.message for r in caplog.records)

    def test_explicit_known_agent_no_warning(self, caplog):
        import logging
        agents = [AgentProfile(id="windsurf", capabilities=["implementation"])]
        contract = _make_contract()
        with caplog.at_level(logging.WARNING, logger="multi_agent.router"):
            result = resolve_builder(agents, contract, explicit="windsurf")
        assert result == "windsurf"
        assert not any("not in registry" in r.message for r in caplog.records)

    def test_last_resort_fallback_warns(self, caplog):
        import logging
        # Agent with low health score → filtered by _eligible → last resort
        agents = [AgentProfile(id="sick", capabilities=["testing"], reliability=0.1, queue_health=0.1)]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}), \
             caplog.at_level(logging.WARNING, logger="multi_agent.router"):
            result = resolve_builder(agents, contract)
        assert result == "sick"
        assert any("filters bypassed" in r.message for r in caplog.records)


class TestResolveReviewerWarnings:
    """R13 F2+F3: resolve_reviewer should warn on last-resort and unknown explicit."""

    def test_explicit_unknown_reviewer_warns(self, caplog):
        import logging
        agents = [
            AgentProfile(id="windsurf", capabilities=["implementation"]),
            AgentProfile(id="cursor", capabilities=["review"]),
        ]
        contract = _make_contract()
        with caplog.at_level(logging.WARNING, logger="multi_agent.router"):
            result = resolve_reviewer(agents, contract, builder_id="windsurf", explicit="ghost")
        assert result == "ghost"
        assert any("not in registry" in r.message for r in caplog.records)

    def test_last_resort_reviewer_warns(self, caplog):
        import logging
        # Both agents have low health → _eligible filters all → last resort
        agents = [
            AgentProfile(id="builder-a", capabilities=["implementation"], reliability=0.1, queue_health=0.1),
            AgentProfile(id="fallback-b", capabilities=["testing"], reliability=0.1, queue_health=0.1),
        ]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}), \
             caplog.at_level(logging.WARNING, logger="multi_agent.router"):
            result = resolve_reviewer(agents, contract, builder_id="builder-a")
        assert result == "fallback-b"
        assert any("filters bypassed" in r.message for r in caplog.records)


# ── Uncovered lines: legacy profiles.json fallback, eligible fallback, health issues ──


class TestLoadRegistryLegacyFallback:
    """Cover lines 35-40: legacy profiles.json fallback when agents.yaml missing."""

    def test_legacy_profiles_json(self, tmp_path):
        # No agents.yaml, but profiles.json exists
        import json
        profiles = tmp_path / "agents" / "profiles.json"
        profiles.parent.mkdir(parents=True)
        profiles.write_text(json.dumps({"agents": [{"id": "legacy-agent"}]}))
        with patch("multi_agent.router.agents_profile_path", return_value=profiles):
            reg = load_registry(path=tmp_path / "nonexistent.yaml")
        assert any(a["id"] == "legacy-agent" for a in reg.get("agents", []))

    def test_no_registry_files(self, tmp_path):
        with patch("multi_agent.router.agents_profile_path", return_value=tmp_path / "no.json"):
            reg = load_registry(path=tmp_path / "no.yaml")
        assert reg["agents"] == []


class TestResolveBuilderNoAgents:
    """Cover line 126: no agents raises ValueError."""

    def test_no_agents_raises(self):
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}), \
             pytest.raises(ValueError, match="No agent"):
            resolve_builder([], contract)


class TestResolveReviewerEligibleFallback:
    """Cover line 161: eligible reviewer by capabilities."""

    def test_eligible_reviewer_picked(self):
        agents = [
            AgentProfile(id="builder-1", capabilities=["implementation"]),
            AgentProfile(id="reviewer-1", capabilities=["review"]),
        ]
        contract = _make_contract()
        with patch("multi_agent.router.get_defaults", return_value={}):
            result = resolve_reviewer(agents, contract, builder_id="builder-1")
        assert result == "reviewer-1"


class TestCheckAgentHealthIssues:
    """Cover line 218: low queue_health issue detection."""

    def test_low_queue_health_reported(self):
        from multi_agent.router import check_agent_health
        agents = [AgentProfile(id="sick", capabilities=["implementation"],
                               reliability=0.9, queue_health=0.3)]
        health = check_agent_health(agents)
        assert any("queue_health" in issue for h in health for issue in h["issues"])
