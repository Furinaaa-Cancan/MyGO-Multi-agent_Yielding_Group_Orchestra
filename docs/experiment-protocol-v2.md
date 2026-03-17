# Experiment Protocol v2: Adaptive Decomposition with Context Bridging

> Version: 2.0 | Date: 2026-03-17 | Status: Pre-registration Draft

## 1. Research Questions

**RQ1 (Adaptive Decomposition):** Does an adaptive decomposition strategy—which
selects among no-decompose, shallow-decompose, and deep-decompose based on
automated complexity estimation—achieve higher functional correctness than
fixed-strategy approaches across tasks of varying complexity?

**RQ2 (Context Bridge Protocol):** Does explicit interface contract extraction
and injection at sub-task boundaries reduce cross-boundary information-loss
failures compared to natural-language-only context passing?

**RQ3 (Interaction Effect):** Is there a significant interaction between
decomposition strategy (adaptive vs. fixed) and context bridging (on vs. off)
on functional correctness for complex tasks?

**RQ4 (Cost-Effectiveness):** What is the Pareto frontier of functional
correctness vs. cost (tokens + wall-clock time) across conditions, and does
the adaptive+bridge condition dominate the fixed-decompose condition?

## 2. Hypotheses

| ID   | Hypothesis | Null | Statistical Test |
|------|-----------|------|------------------|
| H1a  | Adaptive decomposition achieves higher resolve rate than fixed-decompose on complex tasks | No difference | McNemar's test, α=0.05 |
| H1b  | Adaptive decomposition achieves ≥ resolve rate of no-decompose across all complexity levels | Adaptive is worse | One-sided Wilcoxon, α=0.05 |
| H2a  | Context bridge reduces cross-boundary failures by ≥50% vs. no-bridge | No reduction | Fisher's exact test |
| H2b  | Context bridge cost overhead ≤15% vs. no-bridge | Overhead >15% | One-sided paired t-test |
| H3   | Interaction term (strategy × bridge) is significant for complex tasks | No interaction | Two-way ANOVA, α=0.05 |
| H4   | Adaptive+bridge achieves higher correctness/dollar than fixed-decompose | No difference | Bootstrap CI comparison |

## 3. Experimental Conditions

Six conditions forming a 2×2 factorial (C3–C6) plus two baselines (C1–C2):

| ID | Label | Builder | Reviewer | Decompose | Bridge | Purpose |
|----|-------|---------|----------|-----------|--------|---------|
| C1 | Single | Claude CLI | None | None | N/A | Baseline: single-agent |
| C2 | Multi | Claude CLI | Claude CLI | None | N/A | Baseline: multi-agent, no decompose |
| C3 | FixedDecomp | Claude CLI | Claude CLI | Always | Off | Current system (known to degrade) |
| C4 | FixedDecomp+Bridge | Claude CLI | Claude CLI | Always | On | Ablation: bridge only |
| C5 | Adaptive | Claude CLI | Claude CLI | Adaptive | Off | Ablation: adaptive only |
| C6 | Adaptive+Bridge | Claude CLI | Claude CLI | Adaptive | On | **Full system** (expected best) |

All multi-agent conditions use `--mode strict` review policy.

## 4. Task Set

### 4.1 Primary: SWE-bench Verified Subset (30 tasks)

Stratified random sample from SWE-bench Verified (500 instances):

- **Simple** (10): single-file, <100 lines gold diff, no cross-module imports
- **Medium** (10): 2–3 files, 100–300 lines diff, limited cross-module
- **Complex** (10): ≥4 files OR >300 lines diff OR cross-module API changes

Complexity score: `C = files_changed × 2 + lines_changed / 100 + cross_module_imports`

Selection: Python repos with pytest suites, exclude external service deps.

### 4.2 Secondary: Custom Controlled Tasks (9 tasks, retained)

| Complexity | Tasks |
|-----------|-------|
| Simple (bugfix) | task-bugfix-01, task-bugfix-02, task-bugfix-03 |
| Medium (API) | task-api-users, task-api-products, task-api-orders |
| Complex (auth) | task-auth-jwt, task-auth-oauth, task-auth-session |

### 4.3 Sample Size

- **Repetitions**: N=5 per (condition, task) pair
- **Total runs**: 39 tasks × 6 conditions × 5 reps = **1,170 runs**
- **Pilot**: 9 custom tasks × 6 conditions × 3 reps = **162 runs**

### 4.4 Power Analysis

McNemar's test: 10 complex tasks × 5 reps = 50 paired observations.
Expected effect: 15% improvement (85%→100%). At α=0.05, power >0.80.

## 5. Metrics

### 5.1 Primary

| Metric | Definition | Source |
|--------|-----------|--------|
| `resolve_rate` | All GT tests pass (binary) | pytest exit code |
| `test_pass_rate` | Passed / total test cases | pytest output parsing |

### 5.2 Secondary

| Metric | Definition | Source |
|--------|-----------|--------|
| `total_tokens` | Input + output tokens | finops.py JSONL |
| `cost_usd` | Estimated cost | finops.py pricing |
| `wall_clock_sec` | End-to-end duration | timer |
| `retry_count` | Build-review cycles | graph stats |
| `lint_violations` | Ruff violations | ruff check |
| `type_errors` | Mypy errors | mypy |
| `decompose_decision` | Strategy selected | adaptive module log |
| `sub_task_count` | Sub-tasks generated | DecomposeResult |
| `bridge_violations` | Contract conformance failures | bridge checker |
| `cross_boundary_failures` | Interface mismatch test failures | manual classification |

### 5.3 Derived

| Metric | Formula |
|--------|---------|
| `correctness_per_dollar` | resolve_rate / cost_usd |
| `correctness_per_minute` | resolve_rate / (wall_clock_sec / 60) |
| `decompose_accuracy` | adaptive decision vs oracle label agreement |
| `bridge_rescue_rate` | prevented_failures / total_cross_boundary_failures |

## 6. Statistical Analysis Plan

### 6.1 Primary Comparisons

| Comparison | Test | Justification |
|-----------|------|---------------|
| C6 vs C3 | McNemar's (paired binary) | Primary claim |
| C6 vs C2 | McNemar's | No degradation |
| C5 vs C3 | McNemar's | Ablation: adaptive |
| C4 vs C3 | Fisher's exact | Ablation: bridge |
| C3,C4,C5,C6 interaction | Two-way ANOVA | Factorial analysis |
| All 6 conditions | Friedman test | Omnibus |

### 6.2 Stratified Analysis

All tests run separately for Simple, Medium, Complex strata.

### 6.3 Multiple Comparison Correction

Holm-Bonferroni on 6 primary comparisons.

### 6.4 Effect Sizes

- Binary outcomes: Odds Ratio + 95% CI
- Continuous: Cliff's delta
- Categorical: Cramér's V

## 7. Controls

- **Semantic memory**: cleared before every run (`_clear_semantic_memory()`)
- **Git state**: each run starts on clean artifact state
- **LLM version**: model ID recorded per run; all runs within 48h window
- **Temperature**: 0 (deterministic when API supports)
- **Execution path**: all conditions go through orchestration pipeline (C1 uses reviewer=none)
- **Condition ordering**: Latin Square across tasks

## 8. Threats to Validity

### Internal
- Order effects → Latin Square ordering
- Memory leakage → cleared per run
- LLM drift → 48h window + model pinning
- Complexity classifier accuracy → validated against SWE-bench gold labels

### External
- Single LLM (Claude) → acknowledged limitation
- Python-only → SWE-bench constraint
- Task scope → SWE-bench provides external validity

### Construct
- resolve_rate as quality proxy → supplemented with lint/type metrics
- Complexity classification validity → inter-rater agreement if manual
- Cost completeness → finops captures all API calls

### Statistical Conclusion
- Power → N=5 reps × 10 complex tasks ≥ 0.80 power
- Multiple comparisons → Holm-Bonferroni
- Non-normality → non-parametric tests

## 9. Reproducibility

- All task definitions, GT tests, and analysis scripts in version control
- Random seed fixed: `seed=42` for task sampling
- Result JSON includes SHA-256 checksum
- Experiment runner CLI is deterministic and fully documented

## 10. References

1. Jimenez et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024.
2. Bhatia et al. "MASAI: Modular Architecture for Software-engineering AI Agents." arXiv 2024.
3. Xia et al. "Agentless: Demystifying LLM-based Software Engineering Agents." FSE 2025.
4. Islam et al. "MapCoder: Multi-Agent Code Generation." ACL 2024.
5. Yang et al. "SWE-agent: Agent-Computer Interfaces." NeurIPS 2024.
6. Jimenez et al. "GAIA: A Benchmark for General AI Assistants." ICLR 2024.
7. Cohen. "Statistical Power Analysis for the Behavioral Sciences." 1988.
8. Hu et al. "ADAS: Automated Design of Agentic Systems." ICLR 2025.
9. Zhang et al. "AFlow: Automating Agentic Workflow Generation." ICLR 2025 (Oral).
10. Li et al. "Lessons Learned: A Multi-Agent Framework for Code LLMs." NeurIPS 2025.
