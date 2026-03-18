# Experiment Analysis Report

Generated from 15 runs across 4 conditions and 9 tasks.

## Summary Results

| Condition | N | Resolve% | TestPass% | Avg Duration | Avg Retry |
|-----------|---|----------|-----------|-------------|-----------|
| C1: Single | 4 | 100% | 100.0% | 99s | 0.0 |
| C2: Multi | 9 | 100% | 100.0% | 207s | 0.0 |
| C3: FixedDecomp | 1 | 100% | 100.0% | 459s | 0.0 |
| C6: Adaptive+Bridge | 1 | 100% | 100.0% | 398s | 0.0 |

## Stratified Results by Task Complexity

### Simple Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 1 | 100% | 1s |
| C2: Multi | 3 | 100% | 299s |

### Medium Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C2: Multi | 1 | 100% | 148s |

### Complex Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 3 | 100% | 132s |
| C2: Multi | 5 | 100% | 163s |
| C3: FixedDecomp | 1 | 100% | 459s |
| C6: Adaptive+Bridge | 1 | 100% | 398s |

## Per-Task Results

| Task | Complexity | C1: Single | C2: Multi | C3: FixedDecomp | C6: Adaptive+Bridge |
|------|-----------|---|---|---|---|
| task-api-orders | complex | - | 8/8 | - | - |
| task-api-products | medium | - | 8/8 | - | - |
| task-api-users | complex | - | 8/8 | - | - |
| task-auth-jwt | complex | 11/11 | 11/11 | - | 11/11 |
| task-auth-oauth | complex | 11/11 | 11/11 | 11/11 | - |
| task-auth-session | complex | 14/14 | 14/14 | - | - |
| task-bugfix-01 | simple | - | 3/3 | - | - |
| task-bugfix-02 | simple | 3/3 | 3/3 | - | - |
| task-bugfix-03 | simple | - | 3/3 | - | - |

## Pairwise Comparisons

| Comparison | Mean A | Mean B | Cliff's δ | Effect | U | p-value | Sig |
|-----------|--------|--------|-----------|--------|---|---------|-----|
| C6 vs C3 (primary claim) | - | - | - | - | - | - | insufficient data |
| C6 vs C2 (no degradation) | - | - | - | - | - | - | insufficient data |
| C5 vs C3 (adaptive ablation) | - | - | - | - | - | - | insufficient data |
| C4 vs C3 (bridge ablation) | - | - | - | - | - | - | insufficient data |
| C2 vs C1 (multi-agent value) | 1.00 | 1.00 | +0.000 | negligible | 18.0 | 1.0000 | ns |
| C6 vs C1 (full system vs baseline) | - | - | - | - | - | - | insufficient data |

Significance: *** p<0.0083 (Holm-Bonferroni), ** p<0.05, ns = not significant