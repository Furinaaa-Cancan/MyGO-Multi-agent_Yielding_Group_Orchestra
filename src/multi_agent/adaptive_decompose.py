"""Adaptive Decomposition — automatically select decomposition strategy.

Replaces the binary --decompose/--no-decompose decision with a data-driven
complexity estimator that selects among three strategies:

- NO_DECOMPOSE: task fits in single build-review cycle
- SHALLOW_DECOMPOSE: 2-3 sub-tasks with flat dependencies
- DEEP_DECOMPOSE: 4-6 sub-tasks with DAG deps + context bridge

The complexity classifier uses quantitative features extracted from the
requirement text and (optionally) codebase analysis. Thresholds are
calibrated from pilot study data.

Reference: experiment-protocol-v2.md §7.1
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ── Enums & Data Models ─────────────────────────────────


class ComplexityLevel(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class StrategyKind(StrEnum):
    NO_DECOMPOSE = "no_decompose"
    SHALLOW_DECOMPOSE = "shallow_decompose"
    DEEP_DECOMPOSE = "deep_decompose"


@dataclass
class ComplexityFeatures:
    """Quantitative feature vector for complexity estimation."""
    token_count: int = 0
    sentence_count: int = 0
    verb_count: int = 0          # action verbs (implement, create, add...)
    conjunction_count: int = 0   # "and", "also", "additionally"...
    domain_signal_count: int = 0 # auth, database, API...
    file_scope_estimate: int = 0 # estimated files to change (from codebase analysis)
    cross_module_refs: int = 0   # references to multiple modules/packages
    constraint_count: int = 0    # security/perf/compat constraints
    api_endpoint_count: int = 0  # number of API endpoints mentioned
    function_sig_count: int = 0  # function signatures/definitions mentioned
    is_bugfix: bool = False      # whether requirement describes a fix/patch

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_count": self.token_count,
            "sentence_count": self.sentence_count,
            "verb_count": self.verb_count,
            "conjunction_count": self.conjunction_count,
            "domain_signal_count": self.domain_signal_count,
            "file_scope_estimate": self.file_scope_estimate,
            "cross_module_refs": self.cross_module_refs,
            "constraint_count": self.constraint_count,
            "api_endpoint_count": self.api_endpoint_count,
            "function_sig_count": self.function_sig_count,
            "is_bugfix": self.is_bugfix,
        }

    @property
    def complexity_score(self) -> float:
        """Weighted complexity score. Higher = more complex.

        Key discriminators (from 9-task pilot calibration):
        - function_sig_count: strongest signal (bugfix=0, API=3-4, auth=5+)
        - is_bugfix: strong negative signal (bugfix tasks are simple)
        - domain_signal_count: correlates with integration complexity
        - verb_count: penalized to avoid over-counting in verbose requirements
        """
        bugfix_discount = -6.0 if self.is_bugfix else 0.0
        return (
            self.token_count * 0.002
            + self.verb_count * 0.5
            + self.conjunction_count * 0.3
            + self.domain_signal_count * 1.2
            + self.file_scope_estimate * 2.0
            + self.cross_module_refs * 2.5
            + self.constraint_count * 1.0
            + self.api_endpoint_count * 1.0
            + self.function_sig_count * 2.0
            + bugfix_discount
        )


@dataclass
class DecomposeStrategy:
    """Selected decomposition strategy with parameters."""
    kind: StrategyKind
    level: ComplexityLevel
    max_subtasks: int = 6
    enable_bridge: bool = False
    confidence: float = 0.0
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "level": self.level.value,
            "max_subtasks": self.max_subtasks,
            "enable_bridge": self.enable_bridge,
            "confidence": round(self.confidence, 3),
            "features": self.features,
        }


# ── Complexity Thresholds ────────────────────────────────
# Default values; overridden by calibration data from pilot study.

_DEFAULT_THRESHOLDS = {
    "simple_max": 4.0,    # score <= this → SIMPLE
    "complex_min": 10.0,  # score >= this → COMPLEX
    # Between simple_max and complex_min → MEDIUM
}

_thresholds: dict[str, float] = dict(_DEFAULT_THRESHOLDS)


def load_thresholds(path: Path | None = None) -> None:
    """Load calibrated thresholds from pilot study results.

    File format: JSON with keys "simple_max", "complex_min".
    """
    global _thresholds
    if path is None:
        # Default location
        path = Path(__file__).resolve().parents[2] / "config" / "complexity_thresholds.json"
    if not path.exists():
        _log.debug("No threshold calibration file at %s, using defaults", path)
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("simple_max", "complex_min"):
            if key in data:
                _thresholds[key] = float(data[key])
        _log.info("Loaded calibrated thresholds: %s", _thresholds)
    except Exception as e:
        _log.warning("Failed to load thresholds from %s: %s", path, e)


# ── Feature Extraction ──────────────────────────────────


# --- Keyword lists ---

_ACTION_VERBS_ZH = [
    "实现", "创建", "添加", "修改", "删除", "优化", "重构", "集成",
    "部署", "配置", "设计", "开发", "编写", "生成", "验证", "测试",
    "迁移", "升级", "扩展",
]
_ACTION_VERBS_EN = [
    "implement", "create", "add", "modify", "delete", "optimize",
    "refactor", "integrate", "deploy", "configure", "design", "develop",
    "write", "generate", "validate", "test", "migrate", "upgrade",
    "extend", "build", "fix", "update", "remove", "replace",
]
_ACTION_VERBS = _ACTION_VERBS_ZH + _ACTION_VERBS_EN

_CONJUNCTIONS_ZH = ["和", "且", "以及", "并且", "同时", "还需要", "另外", "此外"]
_CONJUNCTIONS_EN = [" and ", " also ", " additionally ", " furthermore ", " moreover "]
_CONJUNCTIONS = _CONJUNCTIONS_ZH + _CONJUNCTIONS_EN

_DOMAIN_SIGNALS = [
    "数据库", "database", "认证", "auth", "API", "微服务", "microservice",
    "分布式", "distributed", "缓存", "cache", "队列", "queue",
    "websocket", "graphql", "oauth", "jwt", "rbac", "session",
    "订单", "order", "支付", "payment", "回调", "callback",
    "审计", "audit", "库存", "inventory", "中间件", "middleware",
    "加密", "encrypt", "权限", "permission", "日志", "logging",
    "监控", "monitor", "通知", "notification",
]

_CONSTRAINT_SIGNALS = [
    "安全", "security", "性能", "performance", "兼容", "compat",
    "幂等", "idempotent", "事务", "transaction", "并发", "concurrent",
    "原子", "atomic", "一致性", "consistency", "可靠", "reliable",
    "timeout", "超时", "限流", "rate limit",
]

_API_PATTERNS = re.compile(
    r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/|"
    r"(endpoint|route|path|接口|端点)",
    re.IGNORECASE,
)

_MODULE_PATTERNS = re.compile(
    r"(module|模块|package|包|service|服务|layer|层|component|组件)",
    re.IGNORECASE,
)

# Function signature patterns: "def foo(", "- func_name(", "函数名(参数)"
_FUNC_SIG_PATTERN = re.compile(
    r"(?:def\s+\w+\s*\(|"                       # Python def
    r"-\s+\w+\s*\([^)]*\)\s*(?:->|→|—)|"        # Markdown list: - func(args) ->
    r"\w+\((?:[^)]{0,80})\)\s*(?:->|→|—|:))",    # func(args) -> or func(args):
)

# Bugfix signals
_BUGFIX_SIGNALS = [
    "修复", "fix", "bug", "patch", "hotfix", "repair",
    "问题", "issue", "error", "错误", "缺陷", "defect",
]


def estimate_complexity_features(requirement: str) -> ComplexityFeatures:
    """Extract quantitative complexity features from a requirement string.

    Pure text analysis — no LLM calls, deterministic.
    """
    if not requirement or not requirement.strip():
        return ComplexityFeatures()

    text = requirement.strip()
    text_lower = text.lower()

    # Token count (rough: split by whitespace + CJK character count)
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(text.split())
    token_count = words + cjk_chars

    # Sentence count
    sentence_count = max(1, len(re.split(r"[。.!?！？\n]+", text)) - 1)

    # Action verbs
    verb_count = sum(1 for v in _ACTION_VERBS if v.lower() in text_lower)

    # Conjunctions
    conj_count = sum(text_lower.count(c.lower()) for c in _CONJUNCTIONS)

    # Domain signals
    domain_count = sum(1 for s in _DOMAIN_SIGNALS if s.lower() in text_lower)

    # Constraint signals
    constraint_count = sum(1 for s in _CONSTRAINT_SIGNALS if s.lower() in text_lower)

    # API endpoint mentions
    api_count = len(_API_PATTERNS.findall(text))

    # Cross-module references
    cross_module = len(_MODULE_PATTERNS.findall(text))

    # Function signatures — strongest discriminator between task types
    func_sigs = len(_FUNC_SIG_PATTERN.findall(text))

    # Bugfix detection
    is_bugfix = any(s.lower() in text_lower for s in _BUGFIX_SIGNALS)

    return ComplexityFeatures(
        token_count=token_count,
        sentence_count=sentence_count,
        verb_count=verb_count,
        conjunction_count=conj_count,
        domain_signal_count=domain_count,
        cross_module_refs=cross_module,
        constraint_count=constraint_count,
        api_endpoint_count=api_count,
        function_sig_count=func_sigs,
        is_bugfix=is_bugfix,
    )


def estimate_file_scope(
    requirement: str,
    codebase_root: Path | None = None,
) -> int:
    """Estimate the number of files that need modification.

    If codebase_root is provided, does a lightweight grep for
    referenced symbols. Otherwise falls back to text heuristics.
    """
    if codebase_root is None:
        # Heuristic: count file-like references
        file_refs = len(re.findall(r"\b\w+\.(py|js|ts|go|java|rs)\b", requirement))
        path_refs = len(re.findall(r"(src/|app/|lib/|tests?/|pkg/)", requirement))
        return max(file_refs, path_refs, 1)

    # Simple codebase scan: look for mentioned function/class names
    # Extract potential identifiers from requirement
    identifiers = set(re.findall(r"\b([A-Z][a-zA-Z]+|[a-z_][a-z_0-9]{3,})\b", requirement))
    if not identifiers:
        return 1

    matched_files: set[str] = set()
    src_dir = codebase_root / "src"
    search_dir = src_dir if src_dir.exists() else codebase_root

    try:
        for py_file in search_dir.rglob("*.py"):
            if ".venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                for ident in identifiers:
                    if ident in content:
                        matched_files.add(str(py_file))
                        break
            except OSError:
                continue
    except OSError:
        pass

    return max(len(matched_files), 1)


# ── Classification ───────────────────────────────────────


def classify_complexity(features: ComplexityFeatures) -> ComplexityLevel:
    """Classify task complexity based on feature vector.

    Uses calibrated thresholds (loaded from pilot data or defaults).
    """
    score = features.complexity_score

    if score <= _thresholds["simple_max"]:
        return ComplexityLevel.SIMPLE
    if score >= _thresholds["complex_min"]:
        return ComplexityLevel.COMPLEX
    return ComplexityLevel.MEDIUM


def _compute_confidence(features: ComplexityFeatures, level: ComplexityLevel) -> float:
    """Compute classification confidence (0.0–1.0).

    Higher when the score is far from decision boundaries.
    """
    score = features.complexity_score
    simple_max = _thresholds["simple_max"]
    complex_min = _thresholds["complex_min"]
    midpoint = (simple_max + complex_min) / 2
    half_range = (complex_min - simple_max) / 2

    if half_range <= 0:
        return 0.5

    if level == ComplexityLevel.SIMPLE:
        # Distance below simple_max, normalized
        distance = max(0, simple_max - score)
        return min(1.0, 0.5 + distance / (2 * half_range))
    elif level == ComplexityLevel.COMPLEX:
        distance = max(0, score - complex_min)
        return min(1.0, 0.5 + distance / (2 * half_range))
    else:
        # MEDIUM: confidence is lower near boundaries
        distance_from_boundary = min(abs(score - simple_max), abs(score - complex_min))
        return min(1.0, 0.3 + distance_from_boundary / half_range * 0.7)


# ── Strategy Selection ───────────────────────────────────


def select_strategy(
    requirement: str,
    *,
    codebase_root: Path | None = None,
    enable_bridge: bool = False,
    force_level: ComplexityLevel | None = None,
) -> DecomposeStrategy:
    """Select decomposition strategy based on requirement analysis.

    Args:
        requirement: The task requirement text.
        codebase_root: Optional project root for file scope estimation.
        enable_bridge: Whether context bridge is enabled (experiment flag).
        force_level: Override complexity classification (for ablation).

    Returns:
        DecomposeStrategy with kind, parameters, and confidence.
    """
    features = estimate_complexity_features(requirement)

    # Optional: enrich with file scope estimate
    if codebase_root:
        features.file_scope_estimate = estimate_file_scope(requirement, codebase_root)

    level = force_level or classify_complexity(features)
    confidence = _compute_confidence(features, level)

    if level == ComplexityLevel.SIMPLE:
        strategy = DecomposeStrategy(
            kind=StrategyKind.NO_DECOMPOSE,
            level=level,
            max_subtasks=1,
            enable_bridge=False,
            confidence=confidence,
            features=features.to_dict(),
        )
    elif level == ComplexityLevel.MEDIUM:
        strategy = DecomposeStrategy(
            kind=StrategyKind.SHALLOW_DECOMPOSE,
            level=level,
            max_subtasks=3,
            enable_bridge=enable_bridge,
            confidence=confidence,
            features=features.to_dict(),
        )
    else:  # COMPLEX
        strategy = DecomposeStrategy(
            kind=StrategyKind.DEEP_DECOMPOSE,
            level=level,
            max_subtasks=6,
            enable_bridge=enable_bridge,
            confidence=confidence,
            features=features.to_dict(),
        )

    _log.info(
        "Adaptive strategy: %s (level=%s, score=%.1f, confidence=%.2f)",
        strategy.kind, level, features.complexity_score, confidence,
    )
    return strategy


# ── Calibration ──────────────────────────────────────────


def calibrate_thresholds(
    labeled_data: list[dict[str, Any]],
    output_path: Path | None = None,
) -> dict[str, float]:
    """Calibrate complexity thresholds from labeled pilot data.

    labeled_data: list of {"requirement": str, "oracle_level": "simple"|"medium"|"complex"}

    Finds thresholds that maximize classification accuracy via grid search.
    """
    if not labeled_data:
        return dict(_DEFAULT_THRESHOLDS)

    # Compute scores for all samples
    scored: list[tuple[float, str]] = []
    for item in labeled_data:
        features = estimate_complexity_features(item["requirement"])
        scored.append((features.complexity_score, item["oracle_level"]))

    scored.sort(key=lambda x: x[0])
    scores = [s for s, _ in scored]

    # Grid search over threshold pairs
    best_acc = 0.0
    best_thresholds = dict(_DEFAULT_THRESHOLDS)

    min_score = min(scores)
    max_score = max(scores)
    step = max(0.5, (max_score - min_score) / 50)

    simple_max = min_score
    while simple_max <= max_score:
        complex_min = simple_max + step
        while complex_min <= max_score + step:
            correct = 0
            for score, label in scored:
                if score <= simple_max:
                    predicted = "simple"
                elif score >= complex_min:
                    predicted = "complex"
                else:
                    predicted = "medium"
                if predicted == label:
                    correct += 1

            acc = correct / len(scored)
            if acc > best_acc:
                best_acc = acc
                best_thresholds = {
                    "simple_max": round(simple_max, 2),
                    "complex_min": round(complex_min, 2),
                }

            complex_min += step
        simple_max += step

    _log.info(
        "Calibrated thresholds: simple_max=%.2f, complex_min=%.2f (accuracy=%.1f%%)",
        best_thresholds["simple_max"], best_thresholds["complex_min"], best_acc * 100,
    )

    # Persist
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(best_thresholds, indent=2), encoding="utf-8"
        )

    # Apply globally
    global _thresholds
    _thresholds = best_thresholds

    return best_thresholds
