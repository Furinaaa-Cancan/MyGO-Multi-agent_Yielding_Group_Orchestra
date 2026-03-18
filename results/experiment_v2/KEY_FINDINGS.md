# Key Findings — Pilot Study (in progress)

> Status: C1 27/27 done, C2 27/27 done, C3 running (10/27), C6 9/9 (rep 1 only)
> Last updated: 2026-03-18

## Finding 1: Multi-agent review adds no quality benefit on these tasks

C1 (Single) and C2 (Multi) both achieve **100% resolve rate** across all 27 runs
(9 tasks × 3 reps). The reviewer never caught a builder error because the builder
produces correct code given the full requirement context.

**Implication**: On tasks within the LLM's capability frontier, adding a reviewer
is pure overhead. The reviewer's value emerges only when the builder makes mistakes.

## Finding 2: Task decomposition degrades quality on complex tasks

C3 (FixedDecomp) shows **80% resolve rate on complex tasks** vs 100% for C1/C2.
The failure (auth-session 13/14) is caused by cross-boundary information loss:
sub-task 2 doesn't have full visibility into sub-task 1's implementation details.

**Implication**: Decomposition should only be used when the task genuinely
exceeds a single context window. For tasks within context, it adds risk.

## Finding 3: Context Bridge fixes specific failures but doesn't eliminate them

C6 (Adaptive+Bridge) fixed C3's auth-session failure (14/14 vs 13/14) by
injecting interface contracts. However, it introduced a *new* failure on
auth-oauth (7/11). This suggests:

1. Bridge addresses **structural** information loss (function signatures, defaults)
2. But doesn't address **semantic** information loss (intent, context, rationale)
3. LLM non-determinism is a major confound — same condition can pass or fail
   depending on the specific generation

## Finding 4: Decomposition adds significant duration overhead

| Condition | Complex Avg Duration | Overhead vs C1 |
|-----------|---------------------|----------------|
| C1 Single | 144s | — |
| C2 Multi | 151s | +5% |
| C3 FixedDecomp | 382s | **+165%** |
| C6 Adaptive+Bridge | 671s | **+366%** |

Decomposition roughly triples execution time on complex tasks.

## Finding 5: Simple tasks — all conditions equal

All conditions achieve 100% on simple tasks (bugfix). The adaptive classifier
correctly routes these to NO_DECOMPOSE in C6, avoiding unnecessary overhead.

## Open Questions (need more reps)

1. Is C3's 80% complex resolve rate statistically different from C1's 100%?
   (Need C3 27 runs for power)
2. Does C6 bridge consistently fix C3 failures, or is it random?
   (Need C6 27 runs to compare failure patterns)
3. Is there a task-specific pattern to failures, or is it purely stochastic?
