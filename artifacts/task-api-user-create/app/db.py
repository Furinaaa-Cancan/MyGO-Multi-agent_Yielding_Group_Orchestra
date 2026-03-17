"""SQLite access and migration bootstrap for the auth data layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock

DB_PATH = Path(__file__).resolve().parent / "auth.db"
MIGRATION_DIR = Path(__file__).resolve().parent / "migrations"

_db_init_lock = Lock()
_db_initialized = False


def _migration_files() -> list[Path]:
    return sorted(MIGRATION_DIR.glob("*.sql"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Apply SQL migrations once per process."""
    global _db_initialized
    if _db_initialized:
        return

    with _db_init_lock:
        if _db_initialized:
            return

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with get_connection() as conn:
            for migration in _migration_files():
                conn.executescript(migration.read_text(encoding="utf-8"))
            conn.commit()

        _db_initialized = True


def reset_db_for_tests() -> None:
    """Reset database file for isolated test execution."""
    global _db_initialized
    if DB_PATH.exists():
        DB_PATH.unlink()
    _db_initialized = False
