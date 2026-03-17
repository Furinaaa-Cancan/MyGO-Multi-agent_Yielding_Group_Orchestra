# Experiment Results: Condition A vs B vs C

> Date: 2026-03-17 | Model: Claude Opus 4.6 | N=1 per condition

## Conditions

| Condition | Builder | Reviewer | Mode | Decompose |
|-----------|---------|----------|------|-----------|
| **A: Single** | Claude Code (direct) | None | — | No |
| **B: Multi** | Claude CLI | Claude CLI | strict | **No** |
| **C: Multi+Decompose** | Claude CLI | Claude CLI | strict | **Yes** (auto) |

## Summary

| Metric | A (Single) | B (Multi) | C (Multi+Decompose) |
|--------|-----------|-----------|---------------------|
| Tasks | 6 | 6 | 6 |
| GT Pass Rate | **60/60 (100%)** | **60/60 (100%)** | **56/60 (93.3%)** |
| Retries | 0 | 0 | 0 |
| Avg Duration | instant | **2m24s** | **5m37s** |
| Bugs Missed by Reviewer | N/A | 0 | 2 |

## Per-Task Breakdown

### Medium Tasks

| Task | A (Single) | B (Multi) | B Duration | C (Decompose) | C Duration | C Sub-tasks |
|------|-----------|-----------|------------|---------------|------------|-------------|
| api-users | 8/8 | 8/8 | 1m53s | 8/8 | 5m35s | 3 |
| api-products | 8/8 | 8/8 | 4m25s | 8/8 | 3m40s | 2 |
| api-orders | 8/8 | 8/8 | 2m15s | 8/8 | 6m24s | 3 |

### Complex Tasks

| Task | A (Single) | B (Multi) | B Duration | C (Decompose) | C Duration | C Bug |
|------|-----------|-----------|------------|---------------|------------|-------|
| auth-jwt | 11/11 | 11/11 | 2m47s | 11/11 | 6m32s | — |
| auth-oauth | 11/11 | **11/11** | 2m27s | **7/11** | 6m02s | scope default missing |
| auth-session | 14/14 | **14/14** | 2m37s | **13/14** | 6m30s | list_active_sessions broken |

## Key Findings

### 1. Task decomposition HURT quality on complex tasks

Condition C (decompose) was the only condition with failures: 93.3% vs 100% for both A and B.

**Root cause**: When a complex requirement is split into sub-tasks, each sub-task's builder only sees a fragment of the specification. The 2 bugs were both **cross-concern spec conformance issues**:
- `authorize(scope: str)` — the default value `= "read"` was in the parent requirement but lost when decomposed into the "auth-code-flow" sub-task
- `list_active_sessions()` — the session storage structure in sub-task 1 didn't align with the query pattern needed in sub-task 2

**This validates a known concern from Agentless (Xia et al., FSE 2025)**: simpler approaches can outperform more complex multi-step agent architectures.

### 2. Multi-agent review (no decompose) matches single-agent quality

Conditions A and B both achieved 100%. The reviewer in Condition B approved all outputs correctly — there were no false negatives to catch because the builder produced correct code when given the full requirement context.

### 3. Decomposition adds significant time overhead

| Complexity | B avg duration | C avg duration | Overhead |
|-----------|---------------|---------------|----------|
| Medium | 2m51s | 5m13s | +83% |
| Complex | 2m37s | 6m21s | +143% |

The decomposition step itself + multiple build-review cycles doubled or tripled wall-clock time.

### 4. Single build-review cycle handles these tasks well

All 6 tasks fit within a single builder's context window. Decomposition is designed for tasks that exceed context limits — for tasks that don't, it adds overhead and introduces information loss at sub-task boundaries.

## When Decomposition Should Help

Based on these results, decomposition is likely beneficial when:
- The requirement exceeds ~4000 tokens (forces context overflow in single pass)
- The task involves genuinely independent modules with no shared API contracts
- The builder consistently fails on first attempt (retry needed)

It is likely harmful when:
- The requirement has tight cross-cutting API contracts (default values, shared state)
- The task fits in a single builder context window
- Sub-tasks have implicit dependencies not captured in the `deps` field

## Threats to Validity

| Threat | Severity | Mitigation |
|--------|----------|------------|
| N=1 per condition | High | Need n>=3 repetitions for statistical power |
| Same model for builder+reviewer | Medium | Same-model review limits perspective diversity |
| Task set is small (6 tasks) | Medium | Results may not generalize to larger/harder tasks |
| Condition A executed differently | Medium | A was direct Claude Code; B/C used orchestration pipeline |
| LLM version may drift | Low | All runs completed within same day |
