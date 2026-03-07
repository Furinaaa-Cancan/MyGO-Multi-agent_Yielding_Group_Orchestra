#!/usr/bin/env python3
"""SQLite-based file lock manager for multi-agent collaboration."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time
from typing import Any


def normalize_path(path: str, *, cwd: pathlib.Path | None = None) -> str:
    base = cwd or pathlib.Path.cwd()
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute():
        p = base / p
    real = os.path.realpath(os.path.abspath(str(p)))
    return os.path.normcase(real)


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS locks (
            file_path TEXT PRIMARY KEY,
            owner_task TEXT NOT NULL,
            lock_version INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            renewed_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def cleanup_expired(conn: sqlite3.Connection, now_ts: int) -> int:
    cur = conn.execute("DELETE FROM locks WHERE expires_at <= ?", (now_ts,))
    return cur.rowcount


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "file_path": row["file_path"],
        "owner_task": row["owner_task"],
        "lock_version": row["lock_version"],
        "created_at": row["created_at"],
        "renewed_at": row["renewed_at"],
        "expires_at": row["expires_at"],
    }


def _candidate_cwds(db_path: pathlib.Path) -> list[pathlib.Path]:
    base = db_path.resolve().parent
    out = [base.parent, base, pathlib.Path.cwd()]
    dedup: list[pathlib.Path] = []
    seen: set[str] = set()
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(p)
    return dedup


def _canonical_candidates(raw_path: str, *, candidate_cwds: list[pathlib.Path]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        if value in seen:
            return
        seen.add(value)
        ordered.append(value)

    try:
        p = pathlib.Path(raw_path).expanduser()
        if p.is_absolute():
            _add(normalize_path(raw_path))
            return ordered
        for cwd in candidate_cwds:
            _add(normalize_path(raw_path, cwd=cwd))
    except Exception:
        return []
    return ordered


def find_lock_by_canonical(
    conn: sqlite3.Connection,
    canonical_path: str,
    *,
    candidate_cwds: list[pathlib.Path] | None = None,
) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM locks WHERE file_path = ?", (canonical_path,)).fetchone()
    if row is not None:
        return row

    # Backward compatibility: rows written before canonicalization.
    cwds = candidate_cwds or [pathlib.Path.cwd()]
    rows = conn.execute("SELECT * FROM locks").fetchall()
    for candidate in rows:
        raw_path = str(candidate["file_path"])
        normalized = _canonical_candidates(raw_path, candidate_cwds=cwds)
        if canonical_path in normalized:
            return candidate
    return None


def command_acquire(args: argparse.Namespace) -> int:
    now_ts = int(time.time())
    expires_at = now_ts + args.ttl_sec
    canonical = normalize_path(args.file_path)
    db_path = pathlib.Path(args.db)
    cwds = _candidate_cwds(db_path)

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cleanup_expired(conn, now_ts)
        row = find_lock_by_canonical(conn, canonical, candidate_cwds=cwds)

        if row is None:
            conn.execute(
                """
                INSERT INTO locks(file_path, owner_task, lock_version, created_at, renewed_at, expires_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (canonical, args.task_id, now_ts, now_ts, expires_at),
            )
            conn.commit()
            print(json.dumps({"status": "acquired", "lock_version": 1, "file_path": canonical}, ensure_ascii=True))
            return 0

        row_key = row["file_path"]
        if row["owner_task"] == args.task_id:
            next_version = int(row["lock_version"]) + 1
            conn.execute(
                """
                UPDATE locks
                SET lock_version = ?, renewed_at = ?, expires_at = ?, file_path = ?
                WHERE file_path = ? AND owner_task = ?
                """,
                (next_version, now_ts, expires_at, canonical, row_key, args.task_id),
            )
            conn.commit()
            print(
                json.dumps(
                    {"status": "renewed_by_owner", "lock_version": next_version, "file_path": canonical},
                    ensure_ascii=True,
                )
            )
            return 0

        conn.rollback()
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "holder": row["owner_task"],
                    "expires_at": row["expires_at"],
                    "file_path": row["file_path"],
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1


def command_renew(args: argparse.Namespace) -> int:
    now_ts = int(time.time())
    expires_at = now_ts + args.ttl_sec
    canonical = normalize_path(args.file_path)
    db_path = pathlib.Path(args.db)
    cwds = _candidate_cwds(db_path)

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cleanup_expired(conn, now_ts)
        row = find_lock_by_canonical(conn, canonical, candidate_cwds=cwds)

        if row is None:
            conn.rollback()
            print("ERROR: lock not found", file=sys.stderr)
            return 1
        if row["owner_task"] != args.task_id:
            conn.rollback()
            print("ERROR: lock is owned by another task", file=sys.stderr)
            return 1

        row_key = row["file_path"]
        next_version = int(row["lock_version"]) + 1
        conn.execute(
            """
            UPDATE locks
            SET lock_version = ?, renewed_at = ?, expires_at = ?, file_path = ?
            WHERE file_path = ? AND owner_task = ?
            """,
            (next_version, now_ts, expires_at, canonical, row_key, args.task_id),
        )
        conn.commit()

    print(json.dumps({"status": "renewed", "lock_version": next_version, "file_path": canonical}, ensure_ascii=True))
    return 0


def command_release(args: argparse.Namespace) -> int:
    now_ts = int(time.time())
    canonical = normalize_path(args.file_path)
    db_path = pathlib.Path(args.db)
    cwds = _candidate_cwds(db_path)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cleanup_expired(conn, now_ts)
        row = find_lock_by_canonical(conn, canonical, candidate_cwds=cwds)
        if row is None:
            conn.rollback()
            print(
                "ERROR: lock not found (already released, expired, or using a different DB path)",
                file=sys.stderr,
            )
            return 1
        if row["owner_task"] != args.task_id:
            conn.rollback()
            print(
                f"ERROR: lock is owned by '{row['owner_task']}', not '{args.task_id}'",
                file=sys.stderr,
            )
            return 1

        cur = conn.execute("DELETE FROM locks WHERE file_path = ? AND owner_task = ?", (row["file_path"], args.task_id))
        conn.commit()
        if cur.rowcount == 0:
            print("ERROR: lock delete failed unexpectedly", file=sys.stderr)
            return 1

    print(json.dumps({"status": "released", "file_path": canonical}, ensure_ascii=True))
    return 0


def command_list(args: argparse.Namespace) -> int:
    now_ts = int(time.time())
    with connect(pathlib.Path(args.db)) as conn:
        cleanup_expired(conn, now_ts)
        rows = conn.execute("SELECT * FROM locks ORDER BY file_path ASC").fetchall()

    payload = [row_to_dict(row) for row in rows]
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    now_ts = int(time.time())
    issues: list[dict[str, Any]] = []
    fixed: list[dict[str, Any]] = []
    db_path = pathlib.Path(args.db)
    cwds = _candidate_cwds(db_path)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cleanup_expired(conn, now_ts)
        rows = conn.execute("SELECT * FROM locks ORDER BY file_path ASC").fetchall()
        for row in rows:
            file_path = str(row["file_path"])
            candidates = _canonical_candidates(file_path, candidate_cwds=cwds)
            raw_path_obj = pathlib.Path(file_path).expanduser()
            if not candidates:
                canonical = file_path
            else:
                existing = [c for c in candidates if pathlib.Path(c).exists()]
                canonical = existing[0] if existing else candidates[0]
            row_issue: dict[str, Any] | None = None

            # Relative paths without an existing target are ambiguous across roots.
            # Keep as-is unless we can resolve to an existing canonical location.
            if (not raw_path_obj.is_absolute()) and candidates and not any(pathlib.Path(c).exists() for c in candidates):
                ambiguous = {
                    "type": "ambiguous_relative_path",
                    "file_path": file_path,
                    "owner_task": row["owner_task"],
                    "severity": "warning",
                    "fixable": False,
                    "note": "Relative lock path has no existing target; skip auto-fix to avoid wrong rewrite.",
                }
                issues.append(ambiguous)
                continue

            if canonical != file_path:
                row_issue = {
                    "type": "non_canonical_path",
                    "file_path": file_path,
                    "canonical_path": canonical,
                    "owner_task": row["owner_task"],
                }
                if args.fix:
                    conn.execute(
                        "UPDATE locks SET file_path = ? WHERE file_path = ?",
                        (canonical, file_path),
                    )
                    fixed.append(row_issue)

            if not pathlib.Path(canonical).exists():
                orphan = {
                    "type": "missing_file",
                    "file_path": canonical,
                    "owner_task": row["owner_task"],
                    "severity": "warning",
                    "fixable": False,
                    "note": "Path does not currently exist; lock may still be valid for planned file creation.",
                }
                issues.append(orphan)
            elif row_issue and not args.fix:
                issues.append(row_issue)

        conn.commit()

    out = {
        "status": "ok" if not issues else "issues_found",
        "issues": issues,
        "fixed": fixed,
    }
    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0 if not issues else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite lock manager for strict multi-agent editing")
    parser.add_argument("--db", default="runtime/locks.db", help="Path to sqlite DB")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("acquire", help="Acquire or renew lock for owner task")
    p.add_argument("--task-id", required=True, help="Task ID holding the lock")
    p.add_argument("--file-path", required=True, help="File path to lock")
    p.add_argument("--ttl-sec", type=int, default=1800, help="Lock TTL seconds")
    p.set_defaults(func=command_acquire)

    p = sub.add_parser("renew", help="Renew lock held by same task")
    p.add_argument("--task-id", required=True, help="Task ID holding the lock")
    p.add_argument("--file-path", required=True, help="File path to lock")
    p.add_argument("--ttl-sec", type=int, default=1800, help="Lock TTL seconds")
    p.set_defaults(func=command_renew)

    p = sub.add_parser("release", help="Release lock held by task")
    p.add_argument("--task-id", required=True, help="Task ID holding the lock")
    p.add_argument("--file-path", required=True, help="File path to unlock")
    p.set_defaults(func=command_release)

    p = sub.add_parser("list", help="List active locks")
    p.set_defaults(func=command_list)

    p = sub.add_parser("doctor", help="Check lock consistency and optionally fix")
    p.add_argument("--fix", action="store_true", help="Attempt to auto-fix lock issues")
    p.set_defaults(func=command_doctor)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "ttl_sec") and args.ttl_sec <= 0:
        print("ERROR: ttl-sec must be > 0", file=sys.stderr)
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
