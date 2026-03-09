"""Plugin/Hook System — register custom event hooks for task lifecycle.

Hooks are Python callables registered to lifecycle events. They run
synchronously in registration order after each event fires.

Supported events::

    on_task_start     — task begins (task_id, requirement)
    on_build_complete — builder finishes (task_id, output)
    on_review_complete — reviewer finishes (task_id, output)
    on_task_complete  — task approved (task_id, elapsed)
    on_task_failed    — task failed (task_id, error)
    on_retry          — retry triggered (task_id, attempt)

Usage (programmatic)::

    from multi_agent.hooks import register_hook, emit

    @register_hook("on_task_complete")
    def notify_slack(event):
        requests.post(SLACK_URL, json={"text": f"Task {event['task_id']} done!"})

    emit("on_task_complete", {"task_id": "t-123", "elapsed": 42.5})

Usage (.ma.yaml)::

    hooks:
      on_task_complete:
        - module: my_hooks
          function: notify_slack
      on_task_failed:
        - module: my_hooks
          function: alert_pagerduty
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)

# ── Types ────────────────────────────────────────────────

HookFn = Callable[[dict[str, Any]], None]

VALID_EVENTS = frozenset({
    "on_task_start",
    "on_build_complete",
    "on_review_complete",
    "on_task_complete",
    "on_task_failed",
    "on_retry",
})

# ── Registry ─────────────────────────────────────────────

_registry: dict[str, list[HookFn]] = {event: [] for event in VALID_EVENTS}
_loaded_from_config = False


def register_hook(event: str, fn: HookFn | None = None) -> Any:
    """Register a hook function for an event.

    Can be used as a decorator or called directly::

        @register_hook("on_task_complete")
        def my_hook(event): ...

        register_hook("on_task_complete", my_hook)
    """
    if event not in VALID_EVENTS:
        raise ValueError(f"Unknown event: {event!r}. Valid: {sorted(VALID_EVENTS)}")

    if fn is not None:
        _registry[event].append(fn)
        return fn

    # Decorator mode
    def decorator(func: HookFn) -> HookFn:
        _registry[event].append(func)
        return func
    return decorator


def unregister_hook(event: str, fn: HookFn) -> bool:
    """Remove a hook function. Returns True if found and removed."""
    if event not in VALID_EVENTS:
        return False
    try:
        _registry[event].remove(fn)
        return True
    except ValueError:
        return False


def clear_hooks(event: str | None = None) -> None:
    """Clear all hooks, or hooks for a specific event."""
    if event:
        if event in _registry:
            _registry[event] = []
    else:
        for e in _registry:
            _registry[e] = []


def list_hooks() -> dict[str, int]:
    """Return count of registered hooks per event."""
    return {event: len(fns) for event, fns in _registry.items()}


# ── Emit ─────────────────────────────────────────────────


def emit(event: str, data: dict[str, Any] | None = None) -> int:
    """Fire an event — call all registered hooks.

    Args:
        event: Event name (must be in VALID_EVENTS).
        data: Event payload dict passed to each hook.

    Returns:
        Number of hooks that executed successfully.
    """
    if event not in VALID_EVENTS:
        _log.warning("Unknown hook event: %s", event)
        return 0

    hooks = _registry.get(event, [])
    if not hooks:
        return 0

    payload = dict(data or {})
    payload["event"] = event
    executed = 0

    for fn in hooks:
        try:
            fn(payload)
            executed += 1
        except Exception as exc:
            _log.warning("Hook %s for %s failed: %s", fn.__name__, event, exc)

    return executed


# ── Config Loading ───────────────────────────────────────

_MAX_HOOKS_PER_EVENT = 10


def load_hooks_from_config() -> int:
    """Load hooks from .ma.yaml 'hooks' section.

    Format::

        hooks:
          on_task_complete:
            - module: my_hooks
              function: notify_slack

    Returns:
        Number of hooks loaded.
    """
    global _loaded_from_config
    if _loaded_from_config:
        return 0

    try:
        from multi_agent.config import load_project_config
        cfg = load_project_config()
    except Exception:
        return 0

    hooks_cfg = cfg.get("hooks")
    if not isinstance(hooks_cfg, dict):
        _loaded_from_config = True
        return 0

    loaded = 0
    for event_name, hook_list in hooks_cfg.items():
        if event_name not in VALID_EVENTS:
            _log.warning("Unknown hook event in config: %s", event_name)
            continue
        if not isinstance(hook_list, list):
            continue
        for hook_def in hook_list[:_MAX_HOOKS_PER_EVENT]:
            if not isinstance(hook_def, dict):
                continue
            module_name = hook_def.get("module")
            func_name = hook_def.get("function")
            if not module_name or not func_name:
                continue
            try:
                mod = importlib.import_module(module_name)
                fn = getattr(mod, func_name)
                if callable(fn):
                    register_hook(event_name, fn)
                    loaded += 1
                    _log.info("Loaded hook %s.%s for %s", module_name, func_name, event_name)
            except (ImportError, AttributeError) as exc:
                _log.warning("Failed to load hook %s.%s: %s", module_name, func_name, exc)

    _loaded_from_config = True
    return loaded
