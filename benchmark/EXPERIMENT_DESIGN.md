# MyGO Benchmark: Multi-Agent vs Single-Agent Experimental Design

## 1. Research Question

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

### 4.1 Correctness (Binary)
- `test_pass`: Do all provided tests pass? (0/1)
- `lint_pass`: Does the code pass linting? (0/1)
- `builds`: Does the code build/import without errors? (0/1)

### 4.2 Quality (Continuous, 0-100)
- `quality_score`: Composite weighted score (see Section 7)

### 4.3 Efficiency
- `wall_clock_sec`: Total execution time
- `total_tokens`: Token consumption
- `cost_usd`: API cost
- `retry_count`: Number of build-review cycles needed

### 4.4 Code Metrics
- `lines_changed`: Lines added + deleted
- `files_changed`: Number of files modified
- `complexity_delta`: Cyclomatic complexity change (radon)

## 5. Task Suite Design

### 5.1 Principles
- Each task has a **gold-standard test suite** written independently
- Tests are NOT shown to the agents — they serve as ground truth evaluation
- Tasks span 3 complexity levels x 3+ types = 9+ unique tasks minimum
- Each task is run N >= 3 times per condition (for variance estimation)

### 5.2 Task Complexity Rubric

| Level | Criteria | Example |
|-------|----------|---------|
| Low | Single file, < 50 LOC, no dependencies | Add a utility function |
| Medium | 2-3 files, 50-200 LOC, 1 external dep | CRUD endpoint + tests |
| High | 4+ files, 200+ LOC, multiple deps, design decisions | Auth system with RBAC |

### 5.3 Task Suite (9 tasks)

```
benchmark/tasks/
  low-01-fizzbuzz/        # Pure function, trivial
  low-02-string-utils/    # String manipulation utilities
  low-03-config-parser/   # Simple config file parser
  med-01-todo-api/        # REST API for todo items
  med-02-csv-processor/   # CSV read/transform/write pipeline
  med-03-cache-decorator/ # LRU cache with TTL decorator
  high-01-user-auth/      # Registration + login + JWT
  high-02-event-system/   # Pub/sub event bus with filtering
  high-03-data-pipeline/  # ETL pipeline with validation
```

Each task directory contains:
```
task-name/
  REQUIREMENT.md     # What the agent sees
  tests/             # Gold-standard tests (hidden from agent)
    test_*.py
  expected/          # Optional: reference implementation
  metadata.yaml      # complexity, type, estimated_loc, tags
```

## 6. Experimental Protocol

### 6.1 Within-Subject Design
Same task runs under both conditions (single + multi), eliminating
task-specific variance. Order is counterbalanced.

### 6.2 Procedure Per Trial
```
1. Clean workspace (git stash / fresh directory)
2. Place REQUIREMENT.md
3. Run agent(s) with timeout
4. Collect output files
5. Run evaluation suite against output
6. Record all metrics to benchmark DB
```

### 6.3 Controls
- Same base model for all agents when possible
- Same system prompt / skill contract
- Same timeout budget
- Same retry budget
- Fresh workspace per trial (no cross-contamination)

## 7. Evaluation Scoring

### 7.1 Automated Quality Score (0-100)

```
quality_score = (
    30 * test_pass_rate      +   # % of gold tests passing
    20 * lint_clean           +   # 1 if no lint errors, else 0
    15 * builds_clean         +   # 1 if imports/runs without error
    15 * structure_score      +   # File organization, naming conventions
    10 * completeness_score   +   # Required features implemented
    10 * security_score           # No obvious vulnerabilities
)
```

### 7.2 Metric Definitions

| Metric | How Computed | Tool |
|--------|-------------|------|
| test_pass_rate | pytest exit code + pass/total ratio | pytest --tb=no -q |
| lint_clean | ruff check exit code | ruff check |
| builds_clean | python -c "import module" | Python import |
| structure_score | File count, naming, __init__.py | Custom checker |
| completeness_score | Keyword/pattern matching vs requirements | Custom checker |
| security_score | Basic SAST patterns | ruff + bandit rules |

### 7.3 Statistical Analysis Plan
- **Primary test**: Mann-Whitney U (non-parametric, small N)
- **Effect size**: Cliff's delta (ordinal effect size)
- **Confidence**: Bootstrap 95% CI for median differences
- **Multiple comparisons**: Bonferroni correction across DVs
- **Visualization**: Box plots per condition, scatter by complexity

## 8. Threats to Validity

### Internal
- **Agent non-determinism**: LLM outputs vary per run → mitigate with N>=3 replications
- **Order effects**: Counterbalanced task ordering
- **Evaluation bias**: All evaluation is automated, no human judgment

### External
- **Task representativeness**: Limited to Python web/utility tasks
- **Agent coverage**: Results specific to tested agent combinations
- **Scale**: Small project scope, may not generalize to large codebases

### Construct
- **Quality definition**: Automated metrics proxy real quality
- **Complexity rating**: Subjective human rating → mitigate with rubric

## 9. Execution Checklist

```
[ ] Define all 9 task requirements
[ ] Write gold-standard test suites
[ ] Implement automated evaluator
[ ] Configure agent credentials
[ ] Run pilot (1 task, 1 rep per condition)
[ ] Run full experiment (9 tasks x 2 conditions x 3 reps = 54 trials)
[ ] Export data: my bench export v_trial_summary -o results.csv
[ ] Statistical analysis in notebook
[ ] Write results section
```
