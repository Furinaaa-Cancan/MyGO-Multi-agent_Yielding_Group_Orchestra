"""File watcher — monitors outbox/ for new agent outputs and auto-resumes the graph."""

from __future__ import annotations

import json
import time
from pathlib import Path

from multi_agent.config import outbox_dir


MAX_OUTBOX_SIZE = 10 * 1024 * 1024  # 10 MB


class OutboxPoller:
    """Simple polling-based watcher for outbox/ directory.

    Uses polling instead of OS-level watchers for maximum FS compatibility.
    Falls back gracefully — user can always use ``ma done`` manually.
    """

    def __init__(self, poll_interval: float = 2.0, *,
                 min_interval: float = 0.5, max_interval: float = 5.0):
        self.poll_interval = poll_interval
        self.min_interval = min_interval
        self.max_interval = max_interval
        self._current_interval = poll_interval
        self._idle_count = 0
        self._known: dict[str, float] = {}

    def _scan(self) -> dict[str, Path]:
        """Scan outbox/ for .json files, return {role: path}.

        Role-based: detects builder.json and reviewer.json.
        """
        d = outbox_dir()
        if not d.exists():
            return {}
        return {
            p.stem: p
            for p in d.glob("*.json")
        }

    @staticmethod
    def _wait_stable(path: Path, settle_time: float = 0.5, max_wait: float = 3.0) -> bool:
        """Wait until file size stabilizes. Returns True if stable, False if still changing or missing."""
        if not path.exists():
            return False
        try:
            prev_size = path.stat().st_size
        except OSError:
            return False
        elapsed = 0.0
        while elapsed < max_wait:
            time.sleep(settle_time)
            elapsed += settle_time
            try:
                cur_size = path.stat().st_size
            except OSError:
                return False
            if cur_size == prev_size:
                return True
            prev_size = cur_size
        return False  # still changing after max_wait

    def check_once(self) -> list[tuple[str, dict]]:
        """Check for new or updated outbox files. Returns [(role, data), ...]."""
        results: list[tuple[str, dict]] = []
        for role, path in self._scan().items():
            try:
                stat = path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue  # File deleted between _scan and stat

            # Skip oversized files
            if size > MAX_OUTBOX_SIZE:
                import warnings
                warnings.warn(f"Outbox file {path} exceeds {MAX_OUTBOX_SIZE} bytes, skipping.")
                continue

            if role not in self._known or self._known[role] < mtime:
                # Wait for file to stabilize before reading
                if not self._wait_stable(path):
                    continue  # File still changing, retry next poll
                try:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._known[role] = mtime
                        results.append((role, data))
                except (json.JSONDecodeError, OSError):
                    pass  # Partial write — retry on next poll
        return results

    def watch(self, callback, *, stop_after: int | None = None):
        """Poll loop. Calls ``callback(role, data)`` for each new outbox file.

        Uses adaptive polling: shortens interval after activity, lengthens after idle.

        Args:
            callback: function(role: str, data: dict) -> None
            stop_after: stop after N detections (None = run forever)
        """
        count = 0
        while True:
            results = self.check_once()
            if results:
                self._idle_count = 0
                self._current_interval = self.min_interval
            else:
                self._idle_count += 1
                if self._idle_count >= 10:
                    self._current_interval = min(
                        self._current_interval * 1.5, self.max_interval
                    )
            for role, data in results:
                callback(role, data)
                count += 1
                if stop_after and count >= stop_after:
                    return
            time.sleep(self._current_interval)
