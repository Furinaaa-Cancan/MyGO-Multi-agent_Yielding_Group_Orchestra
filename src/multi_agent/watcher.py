"""File watcher — monitors outbox/ for new agent outputs and auto-resumes the graph.

OpenClaw-inspired improvement: supports HTTP notify mode alongside file polling.
CLI agents can POST to ``/notify`` after writing the outbox file, triggering
immediate resume instead of waiting for the next poll cycle.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from multi_agent.config import outbox_dir

_log = logging.getLogger(__name__)

MAX_OUTBOX_SIZE = 10 * 1024 * 1024  # 10 MB
DEFAULT_NOTIFY_PORT = 18790  # OpenClaw uses 18789; we use 18790


class _NotifyHandler(BaseHTTPRequestHandler):
    """HTTP handler for ``POST /notify`` — triggers immediate outbox check."""

    # Set by NotifyServer before serving
    _notify_event: threading.Event | None = None

    def do_POST(self) -> None:  # noqa: N802 — HTTP method naming convention
        if self.path.rstrip("/") == "/notify":
            if self._notify_event:
                self._notify_event.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging — use Python logger instead."""
        _log.debug("NotifyServer: %s", format % args)


class NotifyServer:
    """Lightweight HTTP server that CLI agents POST to after writing outbox.

    Binds to ``127.0.0.1:<port>`` (localhost only — no external exposure).
    Thread-safe: runs in a daemon thread alongside OutboxPoller.
    """

    def __init__(self, port: int = DEFAULT_NOTIFY_PORT) -> None:
        self.port = port
        self.event = threading.Event()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start the notify server in a background thread. Returns True on success."""
        handler_class = type(
            "_BoundHandler",
            (_NotifyHandler,),
            {"_notify_event": self.event},
        )
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), handler_class)
            self._server.timeout = 1.0
            self._thread = threading.Thread(
                target=self._serve_forever,
                daemon=True,
                name="notify-server",
            )
            self._thread.start()
            _log.info("NotifyServer started on 127.0.0.1:%d", self.port)
            return True
        except OSError as e:
            _log.warning("NotifyServer failed to bind port %d: %s (falling back to polling)", self.port, e)
            return False

    def _serve_forever(self) -> None:
        if self._server:
            self._server.serve_forever()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def wait(self, timeout: float) -> bool:
        """Wait for a notify event. Returns True if notified, False on timeout.

        Clears the event before waiting to avoid missing notifications that
        arrive between clear() and wait() (classic lost-wakeup race).
        """
        self.event.clear()
        return self.event.wait(timeout=timeout)


def notify_watcher(port: int = DEFAULT_NOTIFY_PORT) -> bool:
    """Send a POST /notify to the local watcher. Used by CLI drivers after writing outbox.

    Returns True if notify was acknowledged, False on error (caller should not
    retry — the watcher will poll anyway).
    """
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/notify",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


class OutboxPoller:
    """Polling + event-driven watcher for outbox/ directory.

    Supports two modes (can run simultaneously):
    1. **Polling** (default): scan outbox/ every N seconds
    2. **Event-driven** (OpenClaw-inspired): CLI agents POST to /notify
       after writing outbox, triggering immediate check

    Uses polling as fallback even when notify is enabled.
    Falls back gracefully — user can always use ``my done`` manually.
    """

    def __init__(self, poll_interval: float = 2.0, *,
                 min_interval: float = 0.5, max_interval: float = 5.0,
                 watch_dir: Path | None = None,
                 notify_port: int = DEFAULT_NOTIFY_PORT,
                 enable_notify: bool = False):
        self.poll_interval = poll_interval
        self.min_interval = min_interval
        self.max_interval = max_interval
        self._watch_dir = watch_dir
        self._current_interval = poll_interval
        self._idle_count = 0
        self._known: dict[str, float] = {}
        self._content_hashes: dict[str, str] = {}  # F2: content-hash dedup
        self._warned_oversized: set[str] = set()
        self._notify_server: NotifyServer | None = None
        self._notify_port = notify_port
        self._enable_notify = enable_notify

    def _scan(self) -> dict[str, Path]:
        """Scan outbox/ for .json files, return {role: path}.

        Role-based: detects builder.json and reviewer.json.
        """
        d = self._watch_dir or outbox_dir()
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

    def check_once(self) -> list[tuple[str, dict[str, Any]]]:
        """Check for new or updated outbox files. Returns [(role, data), ...]."""
        results: list[tuple[str, dict[str, Any]]] = []
        for role, path in self._scan().items():
            try:
                stat = path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue  # File deleted between _scan and stat

            # Skip oversized files (warn only once per role to avoid log flood)
            if size > MAX_OUTBOX_SIZE:
                if role not in self._warned_oversized:
                    import warnings
                    warnings.warn(f"Outbox file {path} exceeds {MAX_OUTBOX_SIZE} bytes, skipping.", stacklevel=2)
                    self._warned_oversized.add(role)
                continue
            else:
                self._warned_oversized.discard(role)

            if role not in self._known or self._known[role] < mtime:
                # Wait for file to stabilize before reading
                if not self._wait_stable(path):
                    continue  # File still changing, retry next poll
                try:
                    raw = path.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        # F2: Content-hash dedup (AutoGen idempotency pattern).
                        # Prevents duplicate submissions from mtime jitter
                        # on NFS/external drives or rapid file touch events.
                        import hashlib
                        content_hash = hashlib.sha256(raw.encode()).hexdigest()
                        if self._content_hashes.get(role) == content_hash:
                            self._known[role] = mtime  # update mtime but skip
                            continue
                        self._known[role] = mtime
                        self._content_hashes[role] = content_hash
                        results.append((role, data))
                except (json.JSONDecodeError, OSError, ValueError):
                    pass  # Partial write — retry on next poll
        return results

    def watch(self, callback: Any, *, stop_after: int | None = None) -> None:
        """Poll loop with optional event-driven notify.

        Calls ``callback(role, data)`` for each new outbox file.
        Uses adaptive polling: shortens interval after activity, lengthens after idle.
        When notify is enabled, CLI agents can POST to /notify to trigger
        immediate check (OpenClaw Gateway-inspired event model).

        Args:
            callback: function(role: str, data: dict) -> None
            stop_after: stop after N detections (None = run forever)
        """
        # Start notify server if enabled
        if self._enable_notify and not self._notify_server:
            ns = NotifyServer(self._notify_port)
            if ns.start():
                self._notify_server = ns

        count = 0
        try:
            while True:
                if stop_after is not None and count >= stop_after:
                    return
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
                    if stop_after is not None and count >= stop_after:
                        return

                # Wait: use notify event with poll interval as timeout
                # This means we wake up EITHER on notify OR after interval
                if self._notify_server:
                    self._notify_server.wait(self._current_interval)
                else:
                    time.sleep(self._current_interval)
        finally:
            if self._notify_server:
                self._notify_server.stop()
                self._notify_server = None
