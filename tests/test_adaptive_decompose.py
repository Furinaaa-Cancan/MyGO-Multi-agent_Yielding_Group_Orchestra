"""Tests for adaptive_decompose module — complexity estimation & strategy selection."""

import json
from pathlib import Path

import pytest

from multi_agent.adaptive_decompose import (
    ComplexityFeatures,
    ComplexityLevel,
    DecomposeStrategy,
    StrategyKind,
    calibrate_thresholds,
    classify_complexity,
    estimate_complexity_features,
    estimate_file_scope,
    select_strategy,
)


class TestEstimateComplexityFeatures:
    def test_empty_requirement(self):
        features = estimate_complexity_features("")
        assert features.complexity_score == 0

    def test_simple_requirement(self):
        features = estimate_complexity_features("Fix the typo in README")
        assert features.verb_count >= 1  # "fix"
        assert features.domain_signal_count == 0

    def test_complex_requirement_en(self):
        req = (
            "Implement user authentication with JWT tokens and OAuth2 support. "
            "Add session management with Redis cache. "
            "Configure RBAC permissions and audit logging. "
            "Integrate with the existing database middleware."
        )
        features = estimate_complexity_features(req)
        assert features.verb_count >= 3
        assert features.domain_signal_count >= 4
        assert features.conjunction_count >= 2
        assert features.complexity_score > 10

    def test_complex_requirement_zh(self):
        req = "实现用户认证模块，集成JWT和OAuth2，添加数据库缓存支持，配置RBAC权限系统"
        features = estimate_complexity_features(req)
        assert features.verb_count >= 3
        assert features.domain_signal_count >= 3

    def test_medium_requirement(self):
        req = "Create a REST API endpoint for user CRUD operations with validation"
        features = estimate_complexity_features(req)
        assert features.verb_count >= 1
        assert features.domain_signal_count >= 1

    def test_api_endpoint_detection(self):
        req = "Add POST /users and GET /users/{id} endpoints"
        features = estimate_complexity_features(req)
        assert features.api_endpoint_count >= 2

    def test_constraint_detection(self):
        req = "Implement idempotent transaction processing with timeout handling"
        features = estimate_complexity_features(req)
        assert features.constraint_count >= 2


class TestClassifyComplexity:
    def test_simple(self):
        features = ComplexityFeatures(token_count=10, verb_count=1)
        assert classify_complexity(features) == ComplexityLevel.SIMPLE

    def test_complex(self):
        features = ComplexityFeatures(
            token_count=200, verb_count=5, domain_signal_count=4,
            cross_module_refs=3,
        )
        assert classify_complexity(features) == ComplexityLevel.COMPLEX

    def test_medium(self):
        features = ComplexityFeatures(
            token_count=50, verb_count=3, domain_signal_count=2,
            function_sig_count=2,
        )
        assert classify_complexity(features) == ComplexityLevel.MEDIUM

    def test_complexity_score_ordering(self):
        simple = ComplexityFeatures(token_count=10, verb_count=1, is_bugfix=True)
        medium = ComplexityFeatures(token_count=50, verb_count=3, domain_signal_count=2, function_sig_count=3)
        complex_ = ComplexityFeatures(
            token_count=200, verb_count=5, domain_signal_count=4, function_sig_count=6,
        )
        assert simple.complexity_score < medium.complexity_score < complex_.complexity_score


class TestSelectStrategy:
    def test_simple_gets_no_decompose(self):
        strategy = select_strategy("Fix typo in README")
        assert strategy.kind == StrategyKind.NO_DECOMPOSE
        assert strategy.level == ComplexityLevel.SIMPLE

    def test_complex_gets_deep_decompose(self):
        req = (
            "Implement user authentication with JWT and OAuth2. "
            "Add session management with database cache. "
            "Configure RBAC permissions and audit logging middleware."
        )
        strategy = select_strategy(req)
        assert strategy.kind == StrategyKind.DEEP_DECOMPOSE
        assert strategy.level == ComplexityLevel.COMPLEX
        assert strategy.max_subtasks == 6

    def test_bridge_flag_propagated(self):
        req = (
            "Implement authentication with JWT and OAuth2 and session management "
            "and database integration and RBAC"
        )
        strategy = select_strategy(req, enable_bridge=True)
        assert strategy.enable_bridge is True

    def test_bridge_not_set_for_simple(self):
        strategy = select_strategy("Fix typo", enable_bridge=True)
        assert strategy.enable_bridge is False  # simple tasks never use bridge

    def test_force_level(self):
        strategy = select_strategy(
            "Fix typo", force_level=ComplexityLevel.COMPLEX
        )
        assert strategy.level == ComplexityLevel.COMPLEX
        assert strategy.kind == StrategyKind.DEEP_DECOMPOSE

    def test_confidence_range(self):
        strategy = select_strategy("Fix typo in README")
        assert 0.0 <= strategy.confidence <= 1.0

    def test_features_included(self):
        strategy = select_strategy("Implement user authentication with JWT")
        assert "verb_count" in strategy.features
        assert "domain_signal_count" in strategy.features

    def test_to_dict(self):
        strategy = select_strategy("Fix typo")
        d = strategy.to_dict()
        assert "kind" in d
        assert "level" in d
        assert "confidence" in d


class TestEstimateFileScope:
    def test_heuristic_with_file_refs(self):
        count = estimate_file_scope("Modify auth.py and models.py")
        assert count >= 2

    def test_heuristic_with_path_refs(self):
        count = estimate_file_scope("Update src/ and tests/ directories")
        assert count >= 2

    def test_minimum_is_one(self):
        count = estimate_file_scope("Do something")
        assert count >= 1

    def test_with_codebase(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text("def authorize(): pass")
        count = estimate_file_scope("authorize function", codebase_root=tmp_path)
        assert count >= 1


class TestCalibrateThresholds:
    def test_basic_calibration(self, tmp_path):
        labeled = [
            {"requirement": "Fix typo", "oracle_level": "simple"},
            {"requirement": "Fix bug in parse function", "oracle_level": "simple"},
            {"requirement": "Create API endpoint for users with validation",
             "oracle_level": "medium"},
            {"requirement": "Add REST API for product CRUD operations",
             "oracle_level": "medium"},
            {"requirement": (
                "Implement authentication with JWT and OAuth2 and session "
                "management with database and cache and RBAC"
             ), "oracle_level": "complex"},
            {"requirement": (
                "Build distributed microservice with API gateway, "
                "authentication, database migration, and monitoring"
             ), "oracle_level": "complex"},
        ]
        output = tmp_path / "thresholds.json"
        thresholds = calibrate_thresholds(labeled, output)
        assert "simple_max" in thresholds
        assert "complex_min" in thresholds
        assert thresholds["simple_max"] < thresholds["complex_min"]
        assert output.exists()

    def test_empty_data(self):
        thresholds = calibrate_thresholds([])
        assert "simple_max" in thresholds  # returns defaults
