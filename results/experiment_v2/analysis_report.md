# Experiment Analysis Report

Generated from 94 runs across 4 conditions and 9 tasks.

## Summary Results

| Condition | N | Resolve% | TestPass% | Avg Duration | Avg Retry |
|-----------|---|----------|-----------|-------------|-----------|
| C1: Single | 27 | 100% | 100.0% | 186s | 0.0 |
| C2: Multi | 27 | 100% | 100.0% | 177s | 0.0 |
| C3: FixedDecomp | 27 | 93% | 99.5% | 396s | 0.1 |
| C6: Adaptive+Bridge | 13 | 77% | 81.8% | 922s | 0.3 |

## Stratified Results by Task Complexity

### Simple Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 9 | 100% | 265s |
| C2: Multi | 9 | 100% | 227s |
| C3: FixedDecomp | 9 | 100% | 279s |
| C6: Adaptive+Bridge | 3 | 100% | 391s |

### Medium Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 3 | 100% | 161s |
| C2: Multi | 3 | 100% | 153s |
| C3: FixedDecomp | 3 | 100% | 213s |
| C6: Adaptive+Bridge | 3 | 67% | 1284s |

### Complex Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 15 | 100% | 144s |
| C2: Multi | 15 | 100% | 151s |
| C3: FixedDecomp | 15 | 87% | 502s |
| C6: Adaptive+Bridge | 7 | 71% | 995s |

## Per-Task Results

| Task | Complexity | C1: Single | C2: Multi | C3: FixedDecomp | C6: Adaptive+Bridge |
|------|-----------|---|---|---|---|
| task-api-orders | complex | 3/3 | 3/3 | 3/3 | 2/3 |
| task-api-products | medium | 3/3 | 3/3 | 3/3 | 2/3 |
| task-api-users | complex | 3/3 | 3/3 | 3/3 | 8/8 |
| task-auth-jwt | complex | 3/3 | 3/3 | 3/3 | 11/11 |
| task-auth-oauth | complex | 3/3 | 3/3 | 3/3 | 7/11 |
| task-auth-session | complex | 3/3 | 3/3 | 1/3 | 14/14 |
| task-bugfix-01 | simple | 3/3 | 3/3 | 3/3 | 3/3 |
| task-bugfix-02 | simple | 3/3 | 3/3 | 3/3 | 3/3 |
| task-bugfix-03 | simple | 3/3 | 3/3 | 3/3 | 3/3 |

## Pairwise Comparisons

| Comparison | Mean A | Mean B | Cliff's δ | Effect | U | p-value | Sig |
|-----------|--------|--------|-----------|--------|---|---------|-----|
| C6 vs C3 (primary claim) | 0.77 | 0.93 | -0.157 | small | 148.0 | 0.1736 | ns |
| C6 vs C2 (no degradation) | 0.77 | 1.00 | -0.231 | small | 135.0 | 0.0114 | ** |
| C5 vs C3 (adaptive ablation) | - | - | - | - | - | - | insufficient data |
| C4 vs C3 (bridge ablation) | - | - | - | - | - | - | insufficient data |
| C2 vs C1 (multi-agent value) | 1.00 | 1.00 | +0.000 | negligible | 364.5 | 1.0000 | ns |
| C6 vs C1 (full system vs baseline) | 0.77 | 1.00 | -0.231 | small | 135.0 | 0.0114 | ** |

Significance: *** p<0.0083 (Holm-Bonferroni), ** p<0.05, ns = not significant