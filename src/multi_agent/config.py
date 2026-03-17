"""Unified configuration — resolve all paths relative to project root."""

from __future__ import annotations

import os
import re as _re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _find_root() -> Path:
    """Walk up from CWD (or env override) looking for the project marker."""
    override = os.environ.get("MA_ROOT")
    if override:
        p = Path(override).resolve()
        if not p.exists():
            raise FileNotFoundError(
                f"MA_ROOT={override} does not exist (resolved to {p}). "
                f"Check the path or unset MA_ROOT."
            )
        if not (p / "skills").is_dir() or not (p / "agents").is_dir():
            import warnings
            warnings.warn(
                f"MA_ROOT={p} does not contain 'skills/' and 'agents/' directories. "
                f"Some operations may fail.",
                stacklevel=2,
            )
        return p

    cur = Path.cwd()
    scanned: list[str] = []
    for parent in [cur, *cur.parents]:
        scanned.append(str(parent))
        if (parent / "skills").is_dir() and (parent / "agents").is_dir():
            return parent

    import warnings
    scanned_display = ", ".join(scanned[:5])
    warnings.warn(
        f"Could not find MyGO project root (no 'skills/' + 'agents/' found). "
        f"Scanned: {scanned_display}. "
        f"Falling back to CWD: {cur}. "
        f"Run 'my init' to initialize a project or set MA_ROOT env var.",
        stacklevel=2,
    )
    return cur


@lru_cache(maxsize=1)
def root_dir() -> Path:
    return _find_root()


# Alias for semantic clarity in experiment/bridge code
project_root = root_dir


def workspace_dir() -> Path:
    return root_dir() / ".multi-agent"


def skills_dir() -> Path:
    return root_dir() / "skills"


def agents_profile_path() -> Path:
    return root_dir() / "agents" / "profiles.json"


def store_db_path() -> Path:
    return workspace_dir() / "store.db"


def inbox_dir() -> Path:
    return workspace_dir() / "inbox"


def outbox_dir() -> Path:
    return workspace_dir() / "outbox"


_SAFE_SUBTASK_ID_RE = _re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _validate_subtask_id(subtask_id: str) -> None:
    """Validate subtask_id to prevent path traversal in subtask workspace paths."""
    if not _SAFE_SUBTASK_ID_RE.match(subtask_id) or ".." in subtask_id:
        raise ValueError(
            f"invalid subtask_id: {subtask_id!r} — "
            "must match [a-zA-Z0-9][a-zA-Z0-9._-]{0,127}"
        )


def subtask_workspace(subtask_id: str) -> Path:
    """Return isolated workspace dir for a parallel sub-task."""
    _validate_subtask_id(subtask_id)
    return workspace_dir() / "subtasks" / subtask_id


def subtask_task_file(subtask_id: str) -> Path:
    """Return TASK.md path for a parallel sub-task."""
    _validate_subtask_id(subtask_id)
    return subtask_workspace(subtask_id) / "TASK.md"


def subtask_outbox_dir(subtask_id: str) -> Path:
    """Return outbox dir for a parallel sub-task."""
    _validate_subtask_id(subtask_id)
    return subtask_workspace(subtask_id) / "outbox"


# ── Agent Persona Names (MyGO!!!!! band members) ─────────

_DEFAULT_AGENT_NAMES: list[str] = [
    "高松燈",      # Vo.  たかまつ ともり
    "千早愛音",    # Gt.  ちはや あのん
    "長崎そよ",    # Ba.  ながさき そよ
    "要楽奈",      # Gt.  かなめ らあな
    "椎名立希花",  # Dr.  しいな たき
]

_custom_names: list[str] | None = None


def set_agent_names(names: list[str]) -> None:
    """Override agent persona names (user customization)."""
    global _custom_names
    _custom_names = list(names)


def get_agent_name(index: int) -> str:
    """Get persona name for the *index*-th parallel agent (0-based, wraps)."""
    names = _custom_names or _DEFAULT_AGENT_NAMES
    return names[index % len(names)]


def get_all_agent_names() -> list[str]:
    """Return current agent persona name list."""
    return list(_custom_names or _DEFAULT_AGENT_NAMES)


def load_agent_names_from_config() -> None:
    """Load custom agent names from .ma.yaml if present."""
    try:
        proj = load_project_config()
        custom = proj.get("agent_names")
        if isinstance(custom, list) and all(isinstance(n, str) for n in custom):
            set_agent_names(custom)
    except Exception:
        pass


def tasks_dir() -> Path:
    return workspace_dir() / "tasks"


def history_dir() -> Path:
    return workspace_dir() / "history"


def dashboard_path() -> Path:
    return workspace_dir() / "dashboard.md"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


VALID_CONFIG_KEYS = {
    "default_skill", "default_builder", "default_reviewer",
    "retry_budget", "timeout_sec", "watch_interval",
    "verbose", "auto_watch", "lang",
    "git", "auto_test", "agent_names", "dashboard", "finops", "memory", "notify", "profiles", "hooks",
}


def _validate_numeric_field(
    data: dict[str, Any], key: str, types: tuple[type, ...],
    *, min_val: float | None = None, max_val: float | None = None,
    type_msg: str = "must be a number",
    range_msg: str | None = None,
) -> str | None:
    """Validate a numeric config field. Returns warning string or None."""
    if key not in data:
        return None
    val = data[key]
    if not isinstance(val, types):
        return f"{key} {type_msg}"
    num: float = float(val)  # type: ignore[arg-type]  # isinstance-guarded above
    if max_val is not None and min_val is not None and not (min_val <= num <= max_val):
        return range_msg or f"{key}={val} out of range ({int(min_val)}-{int(max_val)})"
    if min_val is not None and max_val is None and num < min_val:
        return range_msg or f"{key} must be >= {min_val}, got {val}"
    return None


def validate_config(data: dict[str, Any]) -> list[str]:
    """Validate .ma.yaml config structure. Returns list of warnings."""
    warnings_list: list[str] = []
    unknown = set(data.keys()) - VALID_CONFIG_KEYS
    if unknown:
        warnings_list.append(f"Unknown config keys: {', '.join(sorted(unknown))}")

    for w in (
        _validate_numeric_field(data, "retry_budget", (int,), min_val=0, max_val=20, type_msg="must be an integer"),
        _validate_numeric_field(data, "timeout_sec", (int, float), min_val=0.001,
                               range_msg=f"timeout_sec must be positive, got {data.get('timeout_sec')}"),
        _validate_numeric_field(data, "watch_interval", (int, float), min_val=0.1,
                               range_msg=f"watch_interval must be >= 0.1s, got {data.get('watch_interval')}"),
    ):
        if w:
            warnings_list.append(w)
    return warnings_list


def load_project_config() -> dict[str, Any]:
    """Load optional .ma.yaml project-level config from project root.

    Returns empty dict if file doesn't exist or is malformed.
    CLI flags > .ma.yaml > hardcoded defaults.
    """
    path = root_dir() / ".ma.yaml"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            import warnings
            warnings.warn(".ma.yaml is not a valid mapping, ignoring.", stacklevel=2)
            return {}
        return data
    except Exception as e:
        import warnings
        warnings.warn(f".ma.yaml parse error: {e}. Using defaults.", stacklevel=2)
        return {}


# ── Unified Configuration Aggregator (defect C3 fix) ─────

_DEFAULTS = {
    "skill": "code-implement",
    "retry_budget": 2,
    "timeout_sec": 1800,
    "watch_interval": 2.0,
    "workflow_mode": "strict",
    "builder": "",
    "reviewer": "",
    "lang": "zh",
}


class ProjectSettings:
    """Aggregated project configuration with clear precedence.

    Precedence (highest wins):
        1. CLI flags (``overrides`` dict)
        2. ``.ma.yaml`` project config
        3. ``config/workmode.yaml`` mode defaults
        4. Hardcoded ``_DEFAULTS``

    Architecture fix (defect C3): Previously each module independently loaded
    its own subset of config from different sources, leading to inconsistent
    defaults and duplicated loading logic across cli.py, session.py, graph.py.
    """

    def __init__(self, *, overrides: dict[str, Any] | None = None, mode: str | None = None):
        # Layer 4: hardcoded defaults
        self._merged: dict[str, Any] = dict(_DEFAULTS)

        # Layer 3: workmode.yaml mode defaults
        mode = mode or self._merged.get("workflow_mode", "strict")
        self._apply_workmode(mode)

        # Layer 2: .ma.yaml
        proj = load_project_config()
        for k, v in proj.items():
            if v is not None and v != "":
                self._merged[k] = v

        # Layer 1: CLI overrides (highest precedence)
        if overrides:
            for k, v in overrides.items():
                if v is not None and v != "":
                    self._merged[k] = v

    def _apply_workmode(self, mode: str) -> None:
        """Load workmode.yaml and apply mode-specific defaults."""
        wm_path = root_dir() / "config" / "workmode.yaml"
        if not wm_path.exists():
            return
        try:
            wm = yaml.safe_load(wm_path.read_text(encoding="utf-8")) or {}
            mode_cfg = (wm.get("modes") or {}).get(mode) or {}
            if not isinstance(mode_cfg, dict):
                return
            roles = mode_cfg.get("roles") or {}
            if isinstance(roles, dict):
                for k in ("builder", "reviewer"):
                    if roles.get(k):
                        self._merged[k] = roles[k]
            if "review_policy" in mode_cfg:
                self._merged["review_policy"] = mode_cfg["review_policy"]
        except Exception as exc:
            import warnings
            warnings.warn(f"Failed to load workmode.yaml: {exc}", stacklevel=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self._merged.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._merged[key]

    def __contains__(self, key: str) -> bool:
        return key in self._merged

    def as_dict(self) -> dict[str, Any]:
        """Return a copy of the merged configuration."""
        return dict(self._merged)
