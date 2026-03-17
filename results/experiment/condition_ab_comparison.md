# Experiment Results: Condition A vs Condition B

> Date: 2026-03-17 | Model: Claude Opus 4.6 | Run: 1

## Summary

| Metric | Condition A (Single) | Condition B (Multi+Decompose) |
|--------|---------------------|-------------------------------|
| Tasks Attempted | 9 | 6 (medium + complex only) |
| Ground Truth Pass Rate | **70/70 (100%)** | **56/60 (93.3%)** |
| Retries | 0 | 0 |
| Reviewer Caught Bugs | N/A | 0 (2 bugs slipped through) |

## Per-Task Results

### Medium Tasks

| Task | Condition A | Condition B | Sub-tasks | Duration |
|------|------------|-------------|-----------|----------|
| api-users | 8/8 (100%) | 8/8 (100%) | 3 | 5m35s |
| api-products | 8/8 (100%) | 8/8 (100%) | 2 | 3m40s |
| api-orders | 8/8 (100%) | 8/8 (100%) | 3 | 6m24s |

### Complex Tasks

| Task | Condition A | Condition B | Sub-tasks | Duration | Bug |
|------|------------|-------------|-----------|----------|-----|
| auth-jwt | 11/11 (100%) | 11/11 (100%) | 3 | 6m32s | - |
| auth-oauth | 11/11 (100%) | **7/11 (63.6%)** | 3 | 6m02s | scope default missing |
| auth-session | 14/14 (100%) | **13/14 (92.9%)** | 3 | 6m30s | list_active_sessions broken |

## Key Findings

1. **Single agent outperformed multi-agent on this run**: 100% vs 93.3%
   - Caveat: Single agent (Condition A) was executed by Claude Code with full
     context window and direct file editing. Multi-agent ran through the MyGO
     orchestration pipeline with file-system communication.

2. **Task decomposition adds overhead without clear quality benefit**:
   - Medium tasks averaged 5.2 min via multi-agent vs near-instant for single
   - Decomposition was auto-triggered even for simple CRUD tasks

3. **Reviewer failed to catch 2 specification conformance bugs**:
   - auth-oauth: `authorize(scope: str)` should have `scope: str = "read"`
   - auth-session: `list_active_sessions()` returns empty list
   - Both are subtle API contract violations, not runtime errors

4. **Multi-agent produced additional artifacts**: Each task generated its own
   test files (test_app.py, test_oauth.py, etc.) — a positive side effect

## Threats to This Comparison

- **Not a fair comparison**: Condition A was manually executed by Claude Code
  (same model, full context), while Condition B ran through the orchestration
  pipeline with file-system I/O and context truncation per sub-task.
- **N=1**: Single run per condition. LLM output is stochastic.
- **Same model**: Both builder and reviewer are the same Claude model,
  which limits the diversity of review perspectives.
