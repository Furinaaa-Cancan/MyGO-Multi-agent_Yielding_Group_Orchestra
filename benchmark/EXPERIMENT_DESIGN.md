# MyGO Benchmark: Multi-Agent vs Single-Agent Experimental Design

## 1. Research Questions

**RQ1**: Does multi-agent (builder + reviewer) mode produce higher quality code
than single-agent mode on the same tasks?

**RQ2**: What is the cost-quality tradeoff between multi-agent and single-agent
modes?

**RQ3**: At what task complexity level does multi-agent mode show the most
significant advantage?

## 2. Hypotheses

- **H1**: Multi-agent mode achieves higher pass rates on automated quality gates
  (lint, unit test, contract test) compared to single-agent mode.
- **H2**: Multi-agent mode costs more tokens/USD per task but produces fewer
  regressions requiring manual intervention.
- **H3**: The quality advantage of multi-agent mode increases with task
  complexity.

## 3. Independent Variables (IV)

| Variable | Levels | Description |
|----------|--------|-------------|
| `agent_mode` | single, multi | Primary IV |
| `task_complexity` | low, medium, high | Task difficulty rating |
| `task_type` | api, crud, bugfix, refactor, test | Template category |
| `agent_pair` | claude+claude, cursor+windsurf, etc. | Agent combination |

## 4. Dependent Variables (DV)

### 4.1 Primary DVs (pre-specified for multiple comparison correction)
- `quality_score`: Composite weighted score (see Section 7)
- `test_pass_rate`: Fraction of gold-standard tests passing
- `wall_clock_sec`: Total execution time

### 4.2 Secondary DVs
- `lint_pass`: Does the code pass linting? (0/1)
- `builds`: Does the code build/import without errors? (0/1)
- `total_tokens`: Token consumption
- `cost_usd`: API cost
- `retry_count`: Number of build-review cycles needed

### 4.3 Code Metrics
- `lines_changed`: Lines added + deleted
- `files_changed`: Number of files modified
- `avg_complexity`: Mean cyclomatic complexity (radon)
- `coverage_pct`: Gold test coverage percentage (pytest-cov)

## 5. Task Suite Design

### 5.1 Principles
- Each task has a **gold-standard test suite** written independently
- Tests are NOT shown to the agents — they serve as ground truth evaluation
- Gold tests use **pytest fixtures** for isolation (no shared state between tests)
- Tasks span 3 complexity levels x 3 types = 9 unique tasks
- Each task is run N >= 3 times per condition (for variance estimation)

### 5.2 Task Complexity Rubric

| Dimension | Low (1-3) | Medium (4-6) | High (7-10) |
|-----------|-----------|-------------|-------------|
| **File count** | 1 file | 2-3 files | 4+ files |
| **LOC expected** | < 50 | 50-200 | 200+ |
| **External deps** | 0 | 1 | 2+ |
| **Design decisions** | None (spec is complete) | Minor (choose data structure) | Major (architecture, patterns) |
| **Error handling** | None | Input validation | Auth, security, concurrency |

**Scoring**: Each dimension rated 0-2, summed and normalized to 1-10 scale.
Ratings validated by at least 2 independent reviewers; disagreements resolved
by discussion. Target inter-rater reliability: Cohen's κ ≥ 0.7.

### 5.3 Task Suite (9 tasks)

| ID | Task | Complexity | Score | Gold Tests | Key Challenge |
|----|------|-----------|-------|------------|---------------|
| low-01-fizzbuzz | Pure functions | low | 2.0 | 14 | Basic logic |
| low-02-string-utils | String manipulation | low | 2.0 | 35 | Edge cases |
| low-03-config-parser | Config file parser | low | 3.0 | 39 | Parsing, types |
| med-01-todo-api | REST API CRUD | medium | 5.0 | 15 | FastAPI, validation |
| med-02-csv-processor | CSV pipeline | medium | 5.0 | 28 | Data transforms |
| med-03-cache-decorator | TTL cache + memoize | medium | 6.0 | 21 | Decorators, threading |
| high-01-user-auth | JWT auth + RBAC | high | 8.0 | 17 | Security, hashing |
| high-02-event-system | Pub/sub event bus | high | 7.5 | 24 | Threading, priority |
| high-03-data-pipeline | Validation pipeline | high | 8.5 | 27 | Composition, types |

Each task directory contains:
```
task-name/
  REQUIREMENT.md     # What the agent sees
  tests/             # Gold-standard tests (hidden from agent)
    test_*.py
  metadata.yaml      # complexity, type, estimated_loc, tags
```

## 6. Experimental Protocol

### 6.1 Within-Subject Design
Same task runs under both conditions (single + multi), eliminating
task-specific variance. Order is counterbalanced using a balanced Latin square.

### 6.2 Procedure Per Trial
```
1. Clean workspace (fresh directory, no prior state)
2. Place REQUIREMENT.md
3. Run agent(s) with fixed timeout
4. Collect output files
5. Run evaluation suite against output (gold tests + lint + build + security)
6. Record all metrics to benchmark SQLite DB
```

### 6.3 Controlled Variables
| Variable | Control Method |
|----------|---------------|
| Base model | Same model (e.g., Claude Opus 4.6) for builder and reviewer |
| System prompt | Identical prompt template per role |
| Timeout budget | Same timeout per trial; applies to total wall-clock, not per-agent |
| Token budget | Tracked but not capped; reported as DV |
| Workspace state | Fresh directory per trial (git-clean isolation) |
| Randomization | Task order randomized per replication; LLM seed not fixed (non-determinism is measured) |

### 6.4 Confounds and Mitigations
| Confound | Risk | Mitigation |
|----------|------|------------|
| Same-model co-linearity | Builder and reviewer share training data | Document as limitation; future work: cross-model pairs |
| LLM non-determinism | Output varies per run | N ≥ 3 replications; report variance |
| Evaluator coverage | Gold tests may miss bugs | Gold tests independently written; test count ≥ 14 per task |
| Token budget asymmetry | Multi-mode uses 2x tokens (builder + reviewer) | Report tokens as DV, not control |

## 7. Evaluation Scoring

### 7.1 Automated Quality Score (0-100)

```
quality_score = (
    30 * test_pass_rate      +   # % of gold tests passing (0.0-1.0)
    20 * lint_clean           +   # 1.0 if no lint errors, else 0.0
    15 * builds_clean         +   # 1.0 if all files parse without SyntaxError
    15 * structure_score      +   # File organization quality (0.0-1.0)
    10 * completeness_score   +   # Required features present (0.0-1.0)
    10 * security_score       +   # No obvious vulnerabilities (0.0-1.0)
) + complexity_bonus            # Up to 5 points for low cyclomatic complexity
```

### 7.2 Metric Definitions

| Metric | How Computed | Range | Tool |
|--------|-------------|-------|------|
| test_pass_rate | Gold tests passed / total | 0.0-1.0 | pytest (hidden tests, PYTHONPATH=workspace) |
| lint_clean | Binary: ruff check --select=E,W,F passes | 0 or 1 | ruff |
| builds_clean | Binary: ast.parse() succeeds for all .py files | 0 or 1 | Python ast module |
| structure_score | 5 sub-checks (has .py, has tests, file count, length, naming) | 0.0-1.0 | Custom checker |
| completeness_score | Regex pattern matching vs metadata.required_patterns | 0.0-1.0 | Custom checker |
| security_score | 4 sub-checks (no hardcoded secrets, no eval, no SQL injection, no shell injection) | 0.0-1.0 | Custom regex scanner |
| avg_complexity | Mean cyclomatic complexity across all functions | 0+ | radon cc -s -a -j |
| coverage_pct | Line coverage of agent code by gold tests | 0-100% | pytest-cov |

### 7.3 Complexity Bonus
Linear scale: `bonus = 5.0 - (avg_complexity - 1.0)` for complexity ∈ [1, 5].
Rationale: reward clean, simple code without over-penalizing necessary complexity.

### 7.4 Statistical Analysis Plan

**Primary analysis** (3 pre-specified DVs → Bonferroni α = 0.05/3 ≈ 0.017):
- **Test**: Wilcoxon signed-rank test (paired, non-parametric)
- **Effect size**: Cliff's delta (ordinal effect size)
  - |δ| < 0.147: negligible, < 0.33: small, < 0.474: medium, ≥ 0.474: large
- **Confidence**: Bootstrap 95% CI for mean difference (10,000 replicates)

**Secondary analysis**:
- Mann-Whitney U for unpaired comparisons
- Interaction analysis: agent_mode × task_complexity (separate per-level tests)
- Bonferroni correction across secondary DVs

**Visualization**:
- Box plots per condition (single vs multi)
- Scatter plots: quality_score vs complexity_score, colored by mode
- Paired difference plots per task

**Power analysis** (post-hoc):
- With N=9 paired observations, Wilcoxon signed-rank can detect large effects
  (|δ| ≥ 0.474) at α=0.05 with ~80% power.
- For medium effects, N ≥ 15 tasks recommended.
- Document as limitation if underpowered.

## 8. Threats to Validity

### Internal
- **Agent non-determinism**: LLM outputs vary per run → mitigate with N≥3 replications
- **Order effects**: Counterbalanced task ordering via Latin square
- **Evaluation bias**: All evaluation is automated, no human judgment
- **Evaluator validity**: Gold tests independently written, fixture-isolated, ≥14 tests/task
- **Interaction effects**: agent_mode × task_type not fully factorial → report per-task

### External
- **Task representativeness**: Limited to Python web/utility tasks
- **Agent coverage**: Results specific to tested agent combinations
- **Scale**: Small project scope (< 500 LOC), may not generalize to large codebases
- **Model generalization**: Results may not transfer across LLM versions/providers

### Construct
- **Quality definition**: Automated metrics proxy real quality → validate with manual review on sample
- **Complexity rating**: Subjective human rating → mitigate with rubric + inter-rater reliability
- **Binary lint metric**: Single lint error costs 20 points → known limitation, incentivizes clean code
- **Completeness via regex**: Pattern matching may false-positive on comments → check code, not strings

## 9. Reproducibility

- All trial data stored in SQLite DB with full provenance chain
- Git commit hash recorded per experiment
- Python version and platform info captured
- Gold test suites version-controlled alongside task definitions
- Export: `my bench export v_trial_summary -o results.csv`

## 10. Execution Checklist

```
[x] Define all 9 task requirements
[x] Write gold-standard test suites (with fixture isolation)
[x] Implement automated evaluator (with radon + pytest-cov)
[x] Configure agent credentials
[x] Run pilot (3 tasks, 1 rep per condition)
[x] Run expanded experiment (9 tasks × 2 conditions = 18 trials)
[ ] Run full experiment (9 tasks x 2 conditions x 3 reps = 54 trials)
[ ] Export data: my bench export v_trial_summary -o results.csv
[ ] Statistical analysis in notebook
[ ] Write results section
```
