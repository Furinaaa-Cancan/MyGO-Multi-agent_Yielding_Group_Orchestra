"""Unified configuration — resolve all paths relative to project root."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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
        f"Could not find AgentOrchestra project root (no 'skills/' + 'agents/' found). "
        f"Scanned: {scanned_display}. "
        f"Falling back to CWD: {cur}. "
        f"Run 'ma init' to initialize a project or set MA_ROOT env var.",
        stacklevel=2,
    )
    return cur


@lru_cache(maxsize=1)
def root_dir() -> Path:
    return _find_root()


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


def tasks_dir() -> Path:
    return workspace_dir() / "tasks"


def history_dir() -> Path:
    return workspace_dir() / "history"


def dashboard_path() -> Path:
    return workspace_dir() / "dashboard.md"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


VALID_CONFIG_KEYS = {
    "default_skill", "default_builder", "default_reviewer",
    "retry_budget", "timeout_sec", "watch_interval",
    "verbose", "auto_watch", "lang",
}


def validate_config(data: dict) -> list[str]:
    """Validate .ma.yaml config structure. Returns list of warnings."""
    warnings_list: list[str] = []
    unknown = set(data.keys()) - VALID_CONFIG_KEYS
    if unknown:
        warnings_list.append(f"Unknown config keys: {', '.join(sorted(unknown))}")
    if "retry_budget" in data:
        if not isinstance(data["retry_budget"], int):
            warnings_list.append("retry_budget must be an integer")
        elif not (0 <= data["retry_budget"] <= 20):
            warnings_list.append(f"retry_budget={data['retry_budget']} out of range (0-20)")
    if "timeout_sec" in data:
        if not isinstance(data["timeout_sec"], (int, float)):
            warnings_list.append("timeout_sec must be a number")
        elif data["timeout_sec"] <= 0:
            warnings_list.append(f"timeout_sec must be positive, got {data['timeout_sec']}")
    if "watch_interval" in data:
        if not isinstance(data["watch_interval"], (int, float)):
            warnings_list.append("watch_interval must be a number")
        elif data["watch_interval"] < 0.1:
            warnings_list.append(f"watch_interval must be >= 0.1s, got {data['watch_interval']}")
    return warnings_list


def load_project_config() -> dict:
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

    def __init__(self, *, overrides: dict | None = None, mode: str | None = None):
        # Layer 4: hardcoded defaults
        self._merged: dict = dict(_DEFAULTS)

        # Layer 3: workmode.yaml mode defaults
        mode = mode or self._merged.get("workflow_mode", "strict")
        wm_path = root_dir() / "config" / "workmode.yaml"
        if wm_path.exists():
            try:
                wm = yaml.safe_load(wm_path.read_text(encoding="utf-8")) or {}
                mode_cfg = (wm.get("modes") or {}).get(mode) or {}
                if isinstance(mode_cfg, dict):
                    roles = mode_cfg.get("roles") or {}
                    if isinstance(roles, dict):
                        for k in ("builder", "reviewer"):
                            if roles.get(k):
                                self._merged[k] = roles[k]
                    if "review_policy" in mode_cfg:
                        self._merged["review_policy"] = mode_cfg["review_policy"]
            except Exception:
                pass

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

    def get(self, key: str, default=None):
        return self._merged.get(key, default)

    def __getitem__(self, key: str):
        return self._merged[key]

    def __contains__(self, key: str) -> bool:
        return key in self._merged

    def as_dict(self) -> dict:
        """Return a copy of the merged configuration."""
        return dict(self._merged)
