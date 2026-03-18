# Experiment Analysis Report

Generated from 45 runs across 4 conditions and 9 tasks.

## Summary Results

| Condition | N | Resolve% | TestPass% | Avg Duration | Avg Retry |
|-----------|---|----------|-----------|-------------|-----------|
| C1: Single | 18 | 100% | 100.0% | 147s | 0.0 |
| C2: Multi | 9 | 100% | 100.0% | 207s | 0.0 |
| C3: FixedDecomp | 9 | 89% | 99.2% | 331s | 0.0 |
| C6: Adaptive+Bridge | 9 | 89% | 96.0% | 530s | 0.4 |

## Stratified Results by Task Complexity

### Simple Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 3 | 100% | 167s |
| C2: Multi | 3 | 100% | 299s |
| C3: FixedDecomp | 3 | 100% | 282s |
| C6: Adaptive+Bridge | 3 | 100% | 391s |

### Medium Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 3 | 100% | 161s |
| C2: Multi | 1 | 100% | 148s |
| C3: FixedDecomp | 1 | 100% | 221s |
| C6: Adaptive+Bridge | 1 | 100% | 240s |

### Complex Tasks

| Condition | N | Resolve% | Avg Duration |
|-----------|---|----------|-------------|
| C1: Single | 12 | 100% | 138s |
| C2: Multi | 5 | 100% | 163s |
| C3: FixedDecomp | 5 | 80% | 382s |
| C6: Adaptive+Bridge | 5 | 80% | 671s |

## Per-Task Results

| Task | Complexity | C1: Single | C2: Multi | C3: FixedDecomp | C6: Adaptive+Bridge |
|------|-----------|---|---|---|---|
| task-api-orders | complex | 3/3 | 8/8 | 8/8 | 8/8 |
| task-api-products | medium | 3/3 | 8/8 | 8/8 | 8/8 |
| task-api-users | complex | 3/3 | 8/8 | 8/8 | 8/8 |
| task-auth-jwt | complex | 3/3 | 11/11 | 11/11 | 11/11 |
| task-auth-oauth | complex | 2/2 | 11/11 | 11/11 | 7/11 |
| task-auth-session | complex | 14/14 | 14/14 | 13/14 | 14/14 |
| task-bugfix-01 | simple | 3/3 | 3/3 | 3/3 | 3/3 |
| task-bugfix-02 | simple | 3/3 | 3/3 | 3/3 | 3/3 |
| task-bugfix-03 | simple | 3/3 | 3/3 | 3/3 | 3/3 |

## Pairwise Comparisons

| Comparison | Mean A | Mean B | Cliff's δ | Effect | U | p-value | Sig |
|-----------|--------|--------|-----------|--------|---|---------|-----|
| C6 vs C3 (primary claim) | 0.89 | 0.89 | +0.000 | negligible | 40.5 | 1.0000 | ns |
| C6 vs C2 (no degradation) | 0.89 | 1.00 | -0.111 | negligible | 36.0 | 0.3741 | ns |
| C5 vs C3 (adaptive ablation) | - | - | - | - | - | - | insufficient data |
| C4 vs C3 (bridge ablation) | - | - | - | - | - | - | insufficient data |
| C2 vs C1 (multi-agent value) | 1.00 | 1.00 | +0.000 | negligible | 81.0 | 1.0000 | ns |
| C6 vs C1 (full system vs baseline) | 0.89 | 1.00 | -0.111 | negligible | 72.0 | 0.1817 | ns |

Significance: *** p<0.0083 (Holm-Bonferroni), ** p<0.05, ns = not significant