"""Task notification system — macOS native notifications + optional webhook.

Sends notifications when tasks reach terminal states (approved, failed, escalated, cancelled).
Triggered via EventHooks integration in graph execution.

Configuration via .ma.yaml:
    notify:
      enabled: true
      macos: true          # macOS native notifications (default: true on macOS)
      sound: true          # play notification sound
      webhook_url: ""      # optional webhook URL for external integrations
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────


@dataclass
class NotifyConfig:
    enabled: bool = True
    macos: bool = True
    sound: bool = True
    webhook_url: str = ""


def load_notify_config() -> NotifyConfig:
    """Load notification config from .ma.yaml notify: section."""
    from multi_agent.config import load_project_config

    proj = load_project_config()
    raw = proj.get("notify")
    if not isinstance(raw, dict):
        return NotifyConfig()
    return NotifyConfig(
        enabled=bool(raw.get("enabled", True)),
        macos=bool(raw.get("macos", True)),
        sound=bool(raw.get("sound", True)),
        webhook_url=str(raw.get("webhook_url", "")),
    )


# ── Notification Senders ─────────────────────────────────


def _send_macos_notification(title: str, message: str, *, sound: bool = True) -> bool:
    """Send a macOS native notification via osascript.

    Returns True if notification was sent successfully.
    """
    if platform.system() != "Darwin":
        return False

    sound_clause = 'sound name "Glass"' if sound else ""
    script = (
        f'display notification "{_escape_applescript(message)}" '
        f'with title "{_escape_applescript(title)}" '
        f"{sound_clause}"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            _log.debug("macOS notification failed: %s", result.stderr.strip())
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _log.debug("macOS notification error: %s", e)
        return False


def _escape_applescript(text: str) -> str:
    """Escape text for AppleScript string literal.

    Handles backslash, double-quote, and control characters (newline, tab)
    that could break or inject into AppleScript strings.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("\n", " ")
    text = text.replace("\r", " ")
    text = text.replace("\t", " ")
    return text


_ALLOWED_WEBHOOK_SCHEMES = frozenset({"http", "https"})


def _send_webhook(url: str, payload: dict[str, Any]) -> bool:
    """Send notification via HTTP webhook (non-blocking).

    Returns True if request was initiated (not necessarily delivered).
    Only http:// and https:// URLs are allowed (SSRF prevention).
    """
    if not url:
        return False

    # Validate URL scheme to prevent SSRF (file://, ftp://, etc.)
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        _log.warning("Invalid webhook URL: %s", url)
        return False
    if parsed.scheme not in _ALLOWED_WEBHOOK_SCHEMES:
        _log.warning("Webhook URL scheme '%s' not allowed (only http/https): %s", parsed.scheme, url)
        return False

    def _post() -> None:
        try:
            import urllib.request

            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                _log.debug("Webhook response: %d", resp.status)
        except Exception as e:
            _log.warning("Webhook notification failed: %s", e)

    threading.Thread(target=_post, daemon=True).start()
    return True


# ── High-level API ───────────────────────────────────────


def notify_task_complete(
    task_id: str,
    status: str,
    *,
    summary: str = "",
    retries: int = 0,
    config: NotifyConfig | None = None,
) -> None:
    """Send notification for task completion.

    Args:
        task_id: Task identifier
        status: Final status (approved, failed, escalated, cancelled)
        summary: Brief description of what happened
        retries: Number of retries used
        config: Notification config (loaded from .ma.yaml if None)
    """
    cfg = config or load_notify_config()
    if not cfg.enabled:
        return

    # Build notification content
    emoji_map = {
        "approved": "✅",
        "done": "✅",
        "failed": "❌",
        "escalated": "⚠️",
        "cancelled": "🛑",
    }
    emoji = emoji_map.get(status, "📋")
    title = f"MyGO {emoji} {status.upper()}"
    message = f"Task {task_id}"
    if summary:
        message += f": {summary[:100]}"
    if retries > 0:
        message += f" ({retries} retries)"

    # macOS notification
    if cfg.macos:
        _send_macos_notification(title, message, sound=cfg.sound)

    # Webhook notification
    if cfg.webhook_url:
        _send_webhook(cfg.webhook_url, {
            "event": "task_complete",
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "retries": retries,
        })

    _log.info("Notification sent: %s — %s", title, message)


# ── EventHooks Integration ───────────────────────────────


_notify_registered = False


def register_notify_hooks() -> None:
    """Register notification hooks with the graph EventHooks system.

    Safe to call multiple times — hooks are registered only once.
    Sends notifications on task terminal states (approved/failed/escalated).
    """
    global _notify_registered
    if _notify_registered:
        return
    _notify_registered = True

    cfg = load_notify_config()
    if not cfg.enabled:
        _log.debug("Notifications disabled")
        return

    from multi_agent.graph_infra import graph_hooks

    def _on_decide_exit(state: Any, result: dict[str, Any] | None = None) -> None:
        """Notify on terminal decide outcomes."""
        if not isinstance(result, dict):
            return
        final = result.get("final_status")
        if not final or final not in ("approved", "failed", "escalated", "cancelled"):
            return

        task_id = state.get("task_id", "?") if isinstance(state, dict) else "?"
        retries = state.get("retry_count", 0) if isinstance(state, dict) else 0
        summary = ""
        if isinstance(state, dict):
            bo = state.get("builder_output")
            if isinstance(bo, dict):
                summary = bo.get("summary", "")

        notify_task_complete(
            task_id, final,
            summary=summary,
            retries=retries,
            config=cfg,
        )

    graph_hooks.on_node_exit("decide", _on_decide_exit)
    _log.info("Registered notification hooks (macOS=%s, webhook=%s)",
              cfg.macos, bool(cfg.webhook_url))


def reset_notify_hooks() -> None:
    """Reset hook registration state. Used for testing."""
    global _notify_registered
    _notify_registered = False
