from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run(script: Path, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def test_lockctl_release_with_relative_vs_absolute_path(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    file_path = tmp_path / "specs" / "task.schema.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("{}", encoding="utf-8")

    # Acquire using relative path.
    rel = str(file_path.relative_to(tmp_path))
    res = _run(
        script,
        ["--db", str(db), "acquire", "--task-id", "task-a", "--file-path", rel, "--ttl-sec", "1800"],
        cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["status"] == "acquired"

    # Release using absolute path should still work.
    res = _run(
        script,
        ["--db", str(db), "release", "--task-id", "task-a", "--file-path", str(file_path)],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["status"] == "released"


def test_lockctl_doctor_detects_missing_file(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    missing = tmp_path / "missing" / "gone.py"

    res = _run(
        script,
        ["--db", str(db), "acquire", "--task-id", "task-a", "--file-path", str(missing), "--ttl-sec", "1800"],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr

    res = _run(script, ["--db", str(db), "doctor"], cwd=repo_root)
    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert payload["status"] == "issues_found"
    assert any(item["type"] == "missing_file" for item in payload["issues"])


def test_lockctl_doctor_fix_keeps_missing_file_lock(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    missing = tmp_path / "missing" / "planned.py"

    res = _run(
        script,
        ["--db", str(db), "acquire", "--task-id", "task-a", "--file-path", str(missing), "--ttl-sec", "1800"],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr

    res = _run(script, ["--db", str(db), "doctor", "--fix"], cwd=repo_root)
    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert any(item["type"] == "missing_file" for item in payload["issues"])
    assert all(item.get("type") != "missing_file" for item in payload["fixed"])

    res = _run(script, ["--db", str(db), "list"], cwd=repo_root)
    assert res.returncode == 0, res.stderr
    listed = json.loads(res.stdout)
    assert len(listed) == 1
    assert listed[0]["owner_task"] == "task-a"


def test_lockctl_release_matches_legacy_relative_path_row(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    file_path = tmp_path / "specs" / "task.schema.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("{}", encoding="utf-8")

    res = _run(
        script,
        ["--db", str(db), "acquire", "--task-id", "task-a", "--file-path", str(file_path), "--ttl-sec", "1800"],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE locks SET file_path = ? WHERE owner_task = ?", ("specs/task.schema.json", "task-a"))
        conn.commit()

    res = _run(
        script,
        ["--db", str(db), "release", "--task-id", "task-a", "--file-path", str(file_path)],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["status"] == "released"


def test_lockctl_doctor_fix_uses_db_context_for_legacy_relative_path(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    file_path = tmp_path / "specs" / "task.schema.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("{}", encoding="utf-8")

    res = _run(
        script,
        ["--db", str(db), "acquire", "--task-id", "task-a", "--file-path", str(file_path), "--ttl-sec", "1800"],
        cwd=repo_root,
    )
    assert res.returncode == 0, res.stderr

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE locks SET file_path = ? WHERE owner_task = ?", ("specs/task.schema.json", "task-a"))
        conn.commit()

    res = _run(script, ["--db", str(db), "doctor", "--fix"], cwd=repo_root)
    assert res.returncode == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    assert any(item["type"] == "non_canonical_path" for item in payload["fixed"])

    res = _run(script, ["--db", str(db), "list"], cwd=repo_root)
    assert res.returncode == 0, res.stderr
    listed = json.loads(res.stdout)
    assert len(listed) == 1
    assert listed[0]["file_path"] == str(file_path)


def test_lockctl_doctor_fix_skips_ambiguous_missing_relative_path(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "lockctl.py"
    db = tmp_path / "locks.db"
    missing_rel = "specs/not-created-yet.py"

    with sqlite3.connect(db) as conn:
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
        conn.execute(
            "INSERT INTO locks(file_path, owner_task, lock_version, created_at, renewed_at, expires_at) VALUES (?, ?, 1, 1, 1, 9999999999)",
            (missing_rel, "task-a"),
        )
        conn.commit()

    res = _run(script, ["--db", str(db), "doctor", "--fix"], cwd=repo_root)
    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert any(item["type"] == "ambiguous_relative_path" for item in payload["issues"])
    assert payload["fixed"] == []

    res = _run(script, ["--db", str(db), "list"], cwd=repo_root)
    assert res.returncode == 0, res.stderr
    listed = json.loads(res.stdout)
    assert listed[0]["file_path"] == missing_rel
