"""Task notification system — macOS native notifications + webhook.

Sends notifications when tasks reach terminal states (approved, failed, escalated, cancelled).
Triggered via EventHooks integration in graph execution.

Configuration via .ma.yaml:
    notify:
      enabled: true
      macos: true          # macOS native notifications (default: true on macOS)
      sound: true          # play notification sound
      webhook_url: ""      # optional webhook URL for external integrations
      webhook_format: auto # auto | slack | discord | generic
      webhook_retries: 2   # retry failed webhook deliveries (0 = no retry)
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


def _safe_int(value: Any, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── Configuration ─────────────────────────────────────────


@dataclass
class NotifyConfig:
    enabled: bool = True
    macos: bool = True
    sound: bool = True
    webhook_url: str = ""
    webhook_format: str = "auto"  # auto | slack | discord | generic
    webhook_retries: int = 2


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
        webhook_format=str(raw.get("webhook_format", "auto")),
        webhook_retries=max(0, min(_safe_int(raw.get("webhook_retries", 2), 2), 5)),
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


def _detect_webhook_format(url: str) -> str:
    """Auto-detect webhook format from URL."""
    url_lower = url.lower()
    if "hooks.slack.com" in url_lower:
        return "slack"
    if "discord.com/api/webhooks" in url_lower or "discordapp.com/api/webhooks" in url_lower:
        return "discord"
    return "generic"


def _format_slack_payload(
    event: str, task_id: str, status: str, summary: str, retries: int,
) -> dict[str, Any]:
    """Format payload as Slack Block Kit message."""
    emoji_map = {"approved": ":white_check_mark:", "done": ":white_check_mark:",
                 "failed": ":x:", "escalated": ":warning:", "cancelled": ":octagonal_sign:"}
    emoji = emoji_map.get(status, ":clipboard:")
    color = {"approved": "#36a64f", "done": "#36a64f", "failed": "#dc3545",
             "escalated": "#ffc107", "cancelled": "#6c757d"}.get(status, "#0d6efd")
    fields = [{"title": "Task", "value": task_id, "short": True},
              {"title": "Status", "value": f"{emoji} {status.upper()}", "short": True}]
    if retries > 0:
        fields.append({"title": "Retries", "value": str(retries), "short": True})
    attachment: dict[str, Any] = {"color": color, "fields": fields, "fallback": f"MyGO: {task_id} {status}"}
    if summary:
        attachment["text"] = summary[:300]
    return {"attachments": [attachment]}


def _format_discord_payload(
    event: str, task_id: str, status: str, summary: str, retries: int,
) -> dict[str, Any]:
    """Format payload as Discord embed message."""
    color = {"approved": 0x36A64F, "done": 0x36A64F, "failed": 0xDC3545,
             "escalated": 0xFFC107, "cancelled": 0x6C757D}.get(status, 0x0D6EFD)
    fields = [{"name": "Task", "value": task_id, "inline": True},
              {"name": "Status", "value": status.upper(), "inline": True}]
    if retries > 0:
        fields.append({"name": "Retries", "value": str(retries), "inline": True})
    embed: dict[str, Any] = {"title": f"MyGO Task {status.upper()}", "color": color, "fields": fields}
    if summary:
        embed["description"] = summary[:300]
    return {"embeds": [embed]}


def _format_webhook_payload(
    fmt: str, url: str,
    event: str, task_id: str, status: str, summary: str, retries: int,
) -> dict[str, Any]:
    """Format webhook payload based on format type."""
    if fmt == "auto":
        fmt = _detect_webhook_format(url)
    if fmt == "slack":
        return _format_slack_payload(event, task_id, status, summary, retries)
    if fmt == "discord":
        return _format_discord_payload(event, task_id, status, summary, retries)
    # generic — unchanged from original
    return {"event": event, "task_id": task_id, "status": status,
            "summary": summary, "retries": retries}


def _send_webhook(
    url: str, payload: dict[str, Any], *, retries: int = 2,
) -> bool:
    """Send notification via HTTP webhook (non-blocking, with retry).

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
        import time as _time
        import urllib.request

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(1 + retries):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    _log.debug("Webhook response: %d (attempt %d)", resp.status, attempt + 1)
                    return  # success
            except Exception as e:
                last_err = e
                if attempt < retries:
                    _time.sleep(min(2 ** attempt, 8))  # exponential backoff: 1s, 2s, 4s, 8s
        _log.warning("Webhook failed after %d attempts: %s", retries + 1, last_err)

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
        payload = _format_webhook_payload(
            cfg.webhook_format, cfg.webhook_url,
            "task_complete", task_id, status, summary, retries,
        )
        _send_webhook(cfg.webhook_url, payload, retries=cfg.webhook_retries)

    _log.info("Notification sent: %s — %s", title, message)


# ── EventHooks Integration ───────────────────────────────


_notify_registered = False
_notify_lock = __import__("threading").Lock()


def register_notify_hooks() -> None:
    """Register notification hooks with the graph EventHooks system.

    Safe to call multiple times — hooks are registered only once.
    Sends notifications on task terminal states (approved/failed/escalated).
    """
    global _notify_registered
    with _notify_lock:
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


def notify_decompose_complete(
    parent_task_id: str,
    total: int,
    completed: int,
    failed: list[str],
    duration_sec: float = 0,
    *,
    config: NotifyConfig | None = None,
) -> None:
    """Send digest notification for decompose task completion."""
    cfg = config or load_notify_config()
    if not cfg.enabled:
        return

    status = "failed" if failed else "approved"
    emoji_map = {"approved": "\u2705", "failed": "\u274c"}
    emoji = emoji_map.get(status, "\U0001f4cb")
    title = f"MyGO {emoji} Decompose {status.upper()}"
    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    message = f"Task {parent_task_id}: {completed}/{total} sub-tasks"
    if mins > 0:
        message += f" in {mins}m{secs}s"
    if failed:
        message += f" (failed: {', '.join(failed[:3])})"

    if cfg.macos:
        _send_macos_notification(title, message, sound=cfg.sound)

    if cfg.webhook_url:
        payload = _format_webhook_payload(
            cfg.webhook_format, cfg.webhook_url,
            "decompose_complete", parent_task_id, status,
            f"{completed}/{total} sub-tasks completed" + (f", failed: {', '.join(failed)}" if failed else ""),
            0,
        )
        _send_webhook(cfg.webhook_url, payload, retries=cfg.webhook_retries)

    _log.info("Decompose notification: %s", message)
