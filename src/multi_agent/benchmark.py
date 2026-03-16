"""Benchmark data storage for multi-agent vs single-agent experiments.

Research-grade SQLite-backed storage designed for top-tier venue publications.
Supports experiment → trial → agent_run hierarchy with full provenance tracking.

Schema design principles:
  - Normalized relational model (3NF) for analytical queries
  - Full provenance chain: experiment → trial → agent_run → quality_gate
  - Timestamps in ISO-8601 UTC; durations in seconds (float)
  - Cost in USD (float); tokens as integers
  - Every table has created_at for audit trail
  - Views for common cross-group comparisons (pandas/R friendly)
"""

from __future__ import annotations

import json
import logging
import platform
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────

_SCHEMA_VERSION = 1

_DDL = """\
-- ── Meta ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── Experiments ──────────────────────────────────────────
-- One row per experimental setup (e.g. "multi-agent CRUD vs single-agent CRUD")
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    hypothesis      TEXT,            -- H0/H1 statement
    description     TEXT,
    independent_var TEXT,            -- e.g. "agent_mode (single|multi)"
    dependent_vars  TEXT,            -- JSON array: ["completion_time","quality_score"]
    config_snapshot TEXT,            -- JSON: frozen agents.yaml + workmode at experiment time
    git_commit      TEXT,            -- repo HEAD at experiment creation
    python_version  TEXT,
    platform_info   TEXT,            -- OS/arch
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- ── Trials ───────────────────────────────────────────────
-- One row per task execution (single run of a requirement)
CREATE TABLE IF NOT EXISTS trials (
    trial_id        TEXT PRIMARY KEY,
    experiment_id   TEXT NOT NULL REFERENCES experiments(experiment_id),
    task_id         TEXT,            -- maps to orchestrator task_id
    requirement     TEXT NOT NULL,   -- the task description
    complexity      TEXT,            -- low/medium/high/extreme (manual or auto-tagged)
    complexity_score REAL,           -- numeric 0-10 for regression analysis
    agent_mode      TEXT NOT NULL CHECK(agent_mode IN ('single','multi')),
    builder_agent   TEXT,            -- agent id used as builder
    reviewer_agent  TEXT,            -- agent id used as reviewer (NULL for single-agent)
    workflow_mode   TEXT,            -- strict/normal
    decomposed      INTEGER DEFAULT 0, -- 1 if task was decomposed
    sub_task_count  INTEGER DEFAULT 1,
    -- Outcome
    status          TEXT,            -- approved/failed/timeout/cancelled
    retry_count     INTEGER DEFAULT 0,
    review_rounds   INTEGER DEFAULT 0, -- how many build→review cycles
    -- Timing (seconds)
    wall_clock_sec  REAL,            -- total elapsed time
    build_time_sec  REAL,            -- time spent in build phase
    review_time_sec REAL,            -- time spent in review phase
    idle_time_sec   REAL,            -- time waiting (handoff gaps)
    -- Aggregated cost
    total_cost_usd  REAL DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    -- Metadata
    tags            TEXT,            -- JSON array for filtering: ["crud","auth"]
    notes           TEXT,            -- researcher annotations
    raw_snapshot    TEXT,            -- JSON: full task snapshot at completion
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

-- ── Agent Runs ───────────────────────────────────────────
-- One row per individual agent invocation within a trial
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,
    trial_id        TEXT NOT NULL REFERENCES trials(trial_id),
    agent_id        TEXT NOT NULL,    -- e.g. "claude", "cursor", "mock"
    role            TEXT NOT NULL CHECK(role IN ('builder','reviewer','decomposer','orchestrator')),
    invocation_seq  INTEGER NOT NULL, -- 1st, 2nd, 3rd invocation within trial
    -- Timing
    started_at      TEXT,
    finished_at     TEXT,
    duration_sec    REAL,
    -- Token usage
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    -- Output metrics
    tool_call_count INTEGER DEFAULT 0,
    files_changed   INTEGER DEFAULT 0,
    lines_added     INTEGER DEFAULT 0,
    lines_deleted   INTEGER DEFAULT 0,
    -- Result
    status          TEXT,            -- completed/blocked/error/timeout
    output_summary  TEXT,
    error_message   TEXT,
    raw_output      TEXT,            -- JSON: full agent output payload
    created_at      TEXT NOT NULL
);

-- ── Quality Gates ────────────────────────────────────────
-- One row per quality check result (lint, test, etc.)
CREATE TABLE IF NOT EXISTS quality_gates (
    gate_id     TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES agent_runs(run_id),
    trial_id    TEXT NOT NULL REFERENCES trials(trial_id),
    check_name  TEXT NOT NULL,      -- lint/unit_test/contract_test/security_scan/artifact_checksum
    passed      INTEGER NOT NULL,   -- 0 or 1
    details     TEXT,               -- JSON: error messages, coverage %, etc.
    created_at  TEXT NOT NULL
);

-- ── Review Decisions ─────────────────────────────────────
-- One row per review round decision
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id     TEXT PRIMARY KEY,
    trial_id        TEXT NOT NULL REFERENCES trials(trial_id),
    run_id          TEXT NOT NULL REFERENCES agent_runs(run_id),
    round_num       INTEGER NOT NULL, -- which review round (1-indexed)
    decision        TEXT NOT NULL,     -- approve/reject/request_changes
    reasoning       TEXT,
    evidence_count  INTEGER DEFAULT 0,
    issues_count    INTEGER DEFAULT 0,
    risks_count     INTEGER DEFAULT 0,
    is_rubber_stamp INTEGER DEFAULT 0, -- 1 if flagged as shallow review
    raw_review      TEXT,              -- JSON: full reviewer output
    created_at      TEXT NOT NULL
);

-- ── Indices ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_trials_experiment
    ON trials(experiment_id);
CREATE INDEX IF NOT EXISTS idx_trials_agent_mode
    ON trials(agent_mode);
CREATE INDEX IF NOT EXISTS idx_trials_status
    ON trials(status);
CREATE INDEX IF NOT EXISTS idx_trials_builder
    ON trials(builder_agent);
CREATE INDEX IF NOT EXISTS idx_agent_runs_trial
    ON agent_runs(trial_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent
    ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_role
    ON agent_runs(role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_trial_seq
    ON agent_runs(trial_id, invocation_seq);
CREATE INDEX IF NOT EXISTS idx_quality_gates_trial
    ON quality_gates(trial_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_run
    ON quality_gates(run_id);
CREATE INDEX IF NOT EXISTS idx_review_decisions_trial
    ON review_decisions(trial_id);

-- ── Views for Analysis ───────────────────────────────────

-- V1: Trial summary with timing breakdown (main comparison table)
CREATE VIEW IF NOT EXISTS v_trial_summary AS
SELECT
    t.trial_id,
    e.name AS experiment_name,
    t.requirement,
    t.agent_mode,
    t.builder_agent,
    t.reviewer_agent,
    t.complexity,
    t.complexity_score,
    t.status,
    t.retry_count,
    t.review_rounds,
    t.wall_clock_sec,
    t.build_time_sec,
    t.review_time_sec,
    t.idle_time_sec,
    t.total_cost_usd,
    t.total_input_tokens,
    t.total_output_tokens,
    t.sub_task_count,
    t.decomposed,
    t.created_at,
    t.completed_at
FROM trials t
JOIN experiments e ON e.experiment_id = t.experiment_id;

-- V2: Agent performance across all trials
CREATE VIEW IF NOT EXISTS v_agent_performance AS
SELECT
    ar.agent_id,
    ar.role,
    COUNT(*)                       AS total_runs,
    SUM(CASE WHEN ar.status = 'completed' THEN 1 ELSE 0 END) AS successful_runs,
    ROUND(AVG(ar.duration_sec), 2) AS avg_duration_sec,
    ROUND(AVG(ar.input_tokens), 0) AS avg_input_tokens,
    ROUND(AVG(ar.output_tokens),0) AS avg_output_tokens,
    ROUND(SUM(ar.cost_usd), 4)     AS total_cost_usd,
    ROUND(AVG(ar.files_changed),1)  AS avg_files_changed,
    ROUND(AVG(ar.lines_added), 1)   AS avg_lines_added
FROM agent_runs ar
GROUP BY ar.agent_id, ar.role;

-- V3: Mode comparison (the key table for multi vs single)
CREATE VIEW IF NOT EXISTS v_mode_comparison AS
SELECT
    t.agent_mode,
    COUNT(*)                                AS trial_count,
    SUM(CASE WHEN t.status = 'approved' THEN 1 ELSE 0 END) AS success_count,
    ROUND(100.0 * SUM(CASE WHEN t.status = 'approved' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
    ROUND(AVG(t.wall_clock_sec), 2)         AS avg_wall_clock_sec,
    ROUND(AVG(t.build_time_sec), 2)         AS avg_build_time_sec,
    ROUND(AVG(t.review_time_sec), 2)        AS avg_review_time_sec,
    ROUND(AVG(t.total_cost_usd), 4)         AS avg_cost_usd,
    ROUND(AVG(t.total_input_tokens), 0)     AS avg_input_tokens,
    ROUND(AVG(t.total_output_tokens), 0)    AS avg_output_tokens,
    ROUND(AVG(t.retry_count), 2)            AS avg_retries,
    ROUND(AVG(t.review_rounds), 2)          AS avg_review_rounds
FROM trials t
GROUP BY t.agent_mode;

-- V4: Quality gate pass rates per agent
CREATE VIEW IF NOT EXISTS v_quality_pass_rates AS
SELECT
    ar.agent_id,
    qg.check_name,
    COUNT(*)                    AS total_checks,
    SUM(qg.passed)              AS passed_count,
    ROUND(100.0 * SUM(qg.passed) / NULLIF(COUNT(*), 0), 1) AS pass_rate_pct
FROM quality_gates qg
JOIN agent_runs ar ON ar.run_id = qg.run_id
GROUP BY ar.agent_id, qg.check_name;

-- V5: Review quality analysis
CREATE VIEW IF NOT EXISTS v_review_quality AS
SELECT
    t.agent_mode,
    ar.agent_id AS reviewer_agent,
    COUNT(*)                    AS total_reviews,
    SUM(CASE WHEN rd.decision = 'approve' THEN 1 ELSE 0 END)          AS approvals,
    SUM(CASE WHEN rd.decision = 'reject' THEN 1 ELSE 0 END)           AS rejections,
    SUM(CASE WHEN rd.decision = 'request_changes' THEN 1 ELSE 0 END)  AS change_requests,
    SUM(rd.is_rubber_stamp)     AS rubber_stamps,
    ROUND(AVG(rd.evidence_count), 1) AS avg_evidence_items,
    ROUND(AVG(rd.issues_count), 1)   AS avg_issues_found
FROM review_decisions rd
JOIN agent_runs ar ON ar.run_id = rd.run_id
JOIN trials t ON t.trial_id = rd.trial_id
GROUP BY t.agent_mode, ar.agent_id;
"""


# ── Helpers ───────────────────────────────────────────────

def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _uid() -> str:
    return uuid4().hex[:12]


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def _platform_info() -> str:
    return f"{platform.system()} {platform.machine()} {platform.release()}"


# ── Database ──────────────────────────────────────────────

def default_db_path() -> Path:
    from multi_agent.config import root_dir
    return root_dir() / "benchmark" / "benchmark.db"


def init_db(db_path: Path | None = None) -> Path:
    """Initialize the benchmark database. Returns the path."""
    db_path = db_path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES (?, ?)",
            ("created_at", _now()),
        )
        conn.commit()
    finally:
        conn.close()

    log.info("Benchmark DB initialized: %s", db_path)
    return db_path


@contextmanager
def _connect(db_path: Path | None = None):
    db_path = db_path or default_db_path()
    if not db_path.exists():
        init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Experiment CRUD ───────────────────────────────────────

def create_experiment(
    *,
    name: str,
    hypothesis: str | None = None,
    description: str | None = None,
    independent_var: str = "agent_mode (single|multi)",
    dependent_vars: list[str] | None = None,
    config_snapshot: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    """Create a new experiment. Returns experiment_id."""
    eid = f"exp-{_uid()}"
    now = _now()
    dep_vars = dependent_vars or [
        "wall_clock_sec", "build_time_sec", "review_time_sec",
        "total_cost_usd", "success_rate", "retry_count",
    ]
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO experiments
            (experiment_id, name, hypothesis, description,
             independent_var, dependent_vars, config_snapshot,
             git_commit, python_version, platform_info,
             created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                eid, name, hypothesis, description,
                independent_var, json.dumps(dep_vars, ensure_ascii=False),
                json.dumps(config_snapshot, ensure_ascii=False) if config_snapshot else None,
                _git_head(),
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                _platform_info(),
                now, now,
            ),
        )
    return eid


def list_experiments(db_path: Path | None = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM experiments ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Trial CRUD ────────────────────────────────────────────

def create_trial(
    *,
    experiment_id: str,
    requirement: str,
    agent_mode: str,
    builder_agent: str | None = None,
    reviewer_agent: str | None = None,
    task_id: str | None = None,
    complexity: str | None = None,
    complexity_score: float | None = None,
    workflow_mode: str | None = None,
    decomposed: bool = False,
    sub_task_count: int = 1,
    tags: list[str] | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Create a new trial. Returns trial_id."""
    if agent_mode not in ("single", "multi"):
        raise ValueError(f"agent_mode must be 'single' or 'multi', got {agent_mode!r}")
    tid = f"trial-{_uid()}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trials
            (trial_id, experiment_id, task_id, requirement,
             complexity, complexity_score, agent_mode,
             builder_agent, reviewer_agent, workflow_mode,
             decomposed, sub_task_count, tags, notes, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid, experiment_id, task_id, requirement,
                complexity, complexity_score, agent_mode,
                builder_agent, reviewer_agent, workflow_mode,
                1 if decomposed else 0, sub_task_count,
                json.dumps(tags, ensure_ascii=False) if tags else None,
                notes, _now(),
            ),
        )
    return tid


def complete_trial(
    trial_id: str,
    *,
    status: str,
    wall_clock_sec: float | None = None,
    build_time_sec: float | None = None,
    review_time_sec: float | None = None,
    idle_time_sec: float | None = None,
    total_cost_usd: float = 0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    retry_count: int = 0,
    review_rounds: int = 0,
    raw_snapshot: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    """Mark a trial as completed with outcome data."""
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE trials SET
                status=?, wall_clock_sec=?, build_time_sec=?,
                review_time_sec=?, idle_time_sec=?,
                total_cost_usd=?, total_input_tokens=?, total_output_tokens=?,
                retry_count=?, review_rounds=?,
                raw_snapshot=?, completed_at=?
            WHERE trial_id=?""",
            (
                status, wall_clock_sec, build_time_sec,
                review_time_sec, idle_time_sec,
                total_cost_usd, total_input_tokens, total_output_tokens,
                retry_count, review_rounds,
                json.dumps(raw_snapshot, ensure_ascii=False) if raw_snapshot else None,
                _now(), trial_id,
            ),
        )


# ── Agent Run CRUD ────────────────────────────────────────

def record_agent_run(
    *,
    trial_id: str,
    agent_id: str,
    role: str,
    invocation_seq: int,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_sec: float | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0,
    tool_call_count: int = 0,
    files_changed: int = 0,
    lines_added: int = 0,
    lines_deleted: int = 0,
    status: str | None = None,
    output_summary: str | None = None,
    error_message: str | None = None,
    raw_output: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    """Record a single agent invocation. Returns run_id."""
    _valid_roles = ("builder", "reviewer", "decomposer", "orchestrator")
    if role not in _valid_roles:
        raise ValueError(f"role must be one of {_valid_roles}, got {role!r}")
    rid = f"run-{_uid()}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO agent_runs
            (run_id, trial_id, agent_id, role, invocation_seq,
             started_at, finished_at, duration_sec,
             input_tokens, output_tokens, cost_usd,
             tool_call_count, files_changed, lines_added, lines_deleted,
             status, output_summary, error_message, raw_output, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid, trial_id, agent_id, role, invocation_seq,
                started_at, finished_at, duration_sec,
                input_tokens, output_tokens, cost_usd,
                tool_call_count, files_changed, lines_added, lines_deleted,
                status, output_summary, error_message,
                json.dumps(raw_output, ensure_ascii=False) if raw_output else None,
                _now(),
            ),
        )
    return rid


# ── Quality Gate CRUD ─────────────────────────────────────

def record_quality_gate(
    *,
    run_id: str,
    trial_id: str,
    check_name: str,
    passed: bool,
    details: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    """Record a quality gate check result. Returns gate_id."""
    gid = f"qg-{_uid()}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO quality_gates
            (gate_id, run_id, trial_id, check_name, passed, details, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (
                gid, run_id, trial_id, check_name,
                1 if passed else 0,
                json.dumps(details, ensure_ascii=False) if details else None,
                _now(),
            ),
        )
    return gid


# ── Review Decision CRUD ─────────────────────────────────

def record_review_decision(
    *,
    trial_id: str,
    run_id: str,
    round_num: int,
    decision: str,
    reasoning: str | None = None,
    evidence_count: int = 0,
    issues_count: int = 0,
    risks_count: int = 0,
    is_rubber_stamp: bool = False,
    raw_review: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    """Record a review decision. Returns decision_id."""
    did = f"rd-{_uid()}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO review_decisions
            (decision_id, trial_id, run_id, round_num, decision,
             reasoning, evidence_count, issues_count, risks_count,
             is_rubber_stamp, raw_review, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                did, trial_id, run_id, round_num, decision,
                reasoning, evidence_count, issues_count, risks_count,
                1 if is_rubber_stamp else 0,
                json.dumps(raw_review, ensure_ascii=False) if raw_review else None,
                _now(),
            ),
        )
    return did


# ── Ingest from orchestrator snapshot ─────────────────────

def ingest_trial_from_snapshot(
    experiment_id: str,
    snapshot: dict[str, Any],
    *,
    agent_mode: str = "multi",
    complexity: str | None = None,
    complexity_score: float | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Ingest a completed task snapshot into the benchmark DB.

    Reads the orchestrator's JSON snapshot format and populates
    trials, agent_runs, quality_gates, and review_decisions.
    Returns the trial_id.
    """
    task_id = snapshot.get("task_id", "")
    requirement = snapshot.get("requirement", "")
    builder_id = snapshot.get("builder_id")
    reviewer_id = snapshot.get("reviewer_id")
    workflow_mode = snapshot.get("workflow_mode")
    retry_count = snapshot.get("retry_count", 0)

    # Timing
    started = snapshot.get("task_started_at") or snapshot.get("started_at")
    build_started = snapshot.get("build_started_at")
    review_started = snapshot.get("review_started_at")

    # Determine if single agent (reviewer_id is None or same as builder with no review)
    has_reviewer = reviewer_id is not None and snapshot.get("reviewer_output") is not None
    effective_mode = agent_mode

    # Calculate durations from timestamps
    conversation = snapshot.get("conversation", [])
    last_t = conversation[-1].get("t") if conversation else None
    wall_clock = (last_t - started) if (started and last_t) else None
    build_end = review_started or last_t
    build_time = (build_end - build_started) if (build_started and build_end) else None
    review_time = None
    if review_started and last_t:
        review_time = last_t - review_started

    # Determine status
    last_action = conversation[-1].get("action", "") if conversation else ""
    last_decision = conversation[-1].get("decision", "") if conversation else ""
    status = "approved" if (last_action == "approved" or last_decision == "approve") else "failed"

    # Create trial
    trial_id = create_trial(
        experiment_id=experiment_id,
        requirement=requirement,
        agent_mode=effective_mode,
        builder_agent=builder_id,
        reviewer_agent=reviewer_id if has_reviewer else None,
        task_id=task_id,
        complexity=complexity,
        complexity_score=complexity_score,
        workflow_mode=workflow_mode,
        tags=tags,
        notes=notes,
        db_path=db_path,
    )

    review_rounds = 1 if has_reviewer else 0

    complete_trial(
        trial_id,
        status=status,
        wall_clock_sec=wall_clock,
        build_time_sec=build_time,
        review_time_sec=review_time,
        retry_count=retry_count,
        review_rounds=review_rounds,
        raw_snapshot=snapshot,
        db_path=db_path,
    )

    # Record builder run
    builder_output = snapshot.get("builder_output", {})
    build_run_id = record_agent_run(
        trial_id=trial_id,
        agent_id=builder_id or "unknown",
        role="builder",
        invocation_seq=1,
        started_at=datetime.fromtimestamp(started, tz=UTC).isoformat() if started else None,
        finished_at=datetime.fromtimestamp(review_started or last_t, tz=UTC).isoformat() if (review_started or last_t) else None,
        duration_sec=build_time,
        files_changed=len(builder_output.get("changed_files", [])),
        status=builder_output.get("status", "unknown"),
        output_summary=builder_output.get("summary"),
        raw_output=builder_output if builder_output else None,
        db_path=db_path,
    )

    # Record quality gates from builder check_results
    check_results = builder_output.get("check_results", {})
    for check_name, result in check_results.items():
        passed = result in ("pass", "passed", True, 1)
        record_quality_gate(
            run_id=build_run_id,
            trial_id=trial_id,
            check_name=check_name,
            passed=passed,
            db_path=db_path,
        )

    # Record reviewer run + decision
    reviewer_output = snapshot.get("reviewer_output")
    if has_reviewer and reviewer_output:
        review_run_id = record_agent_run(
            trial_id=trial_id,
            agent_id=reviewer_id or "unknown",
            role="reviewer",
            invocation_seq=2,
            started_at=datetime.fromtimestamp(review_started, tz=UTC).isoformat() if review_started else None,
            finished_at=datetime.fromtimestamp(last_t, tz=UTC).isoformat() if last_t else None,
            duration_sec=review_time,
            status="completed",
            output_summary=reviewer_output.get("summary"),
            raw_output=reviewer_output,
            db_path=db_path,
        )

        record_review_decision(
            trial_id=trial_id,
            run_id=review_run_id,
            round_num=1,
            decision=reviewer_output.get("decision", "unknown"),
            reasoning=reviewer_output.get("reasoning"),
            evidence_count=len(reviewer_output.get("evidence", [])),
            issues_count=len(reviewer_output.get("issues", [])),
            risks_count=len(reviewer_output.get("risks", [])),
            raw_review=reviewer_output,
            db_path=db_path,
        )

    return trial_id


# ── Export ────────────────────────────────────────────────

def export_csv(view_name: str, output_path: Path, db_path: Path | None = None) -> Path:
    """Export a view or table to CSV for analysis in pandas/R."""
    import csv

    valid_names = {
        "v_trial_summary", "v_agent_performance", "v_mode_comparison",
        "v_quality_pass_rates", "v_review_quality",
        "experiments", "trials", "agent_runs", "quality_gates", "review_decisions",
    }
    if view_name not in valid_names:
        raise ValueError(f"Unknown view/table: {view_name}. Valid: {sorted(valid_names)}")

    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM {view_name}").fetchall()  # noqa: S608
        if not rows:
            output_path.write_text("")
            return output_path

        keys = rows[0].keys()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

    return output_path


def query(sql: str, params: tuple = (), db_path: Path | None = None) -> list[dict[str, Any]]:
    """Run an arbitrary read-only SQL query. For advanced analysis."""
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        raise ValueError("Only SELECT/WITH queries are allowed")
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── FinOps backfill ───────────────────────────────────────

def backfill_finops(trial_id: str, task_id: str, db_path: Path | None = None) -> bool:
    """Backfill token/cost data from FinOps JSONL logs into a trial's agent_runs.

    Reads .multi-agent/logs/token-usage.jsonl, matches entries by task_id,
    and updates agent_runs + trial aggregate totals.
    Returns True if any data was backfilled.
    """
    try:
        from multi_agent.finops import load_usage_log
    except ImportError:
        return False

    entries = [e for e in load_usage_log() if e.get("task_id") == task_id]
    if not entries:
        return False

    # Aggregate per agent
    agent_totals: dict[str, dict[str, int | float]] = {}
    for e in entries:
        aid = e.get("agent_id", "unknown")
        if aid not in agent_totals:
            agent_totals[aid] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        agent_totals[aid]["input_tokens"] += e.get("input_tokens", 0)
        agent_totals[aid]["output_tokens"] += e.get("output_tokens", 0)
        agent_totals[aid]["cost_usd"] += e.get("cost", 0.0)

    total_input = sum(v["input_tokens"] for v in agent_totals.values())
    total_output = sum(v["output_tokens"] for v in agent_totals.values())
    total_cost = sum(v["cost_usd"] for v in agent_totals.values())

    with _connect(db_path) as conn:
        # Update per-agent runs
        for aid, totals in agent_totals.items():
            conn.execute(
                """UPDATE agent_runs SET
                    input_tokens=?, output_tokens=?, cost_usd=?
                WHERE trial_id=? AND agent_id=?""",
                (totals["input_tokens"], totals["output_tokens"],
                 round(totals["cost_usd"], 6), trial_id, aid),
            )
        # Update trial aggregates
        conn.execute(
            """UPDATE trials SET
                total_input_tokens=?, total_output_tokens=?, total_cost_usd=?
            WHERE trial_id=?""",
            (total_input, total_output, round(total_cost, 6), trial_id),
        )

    return True
