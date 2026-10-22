"""Task-level Codex quota auto-adjust setting and controller state."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .runtime import atomic, now, write_json

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}
_KEY = "THEORY_MASTER_QUOTA_AUTO_ADJUST"
_POLL_KEY = "THEORY_MASTER_QUOTA_POLL_SECONDS"
_SCHEMA = "theory-master/quota-auto-adjust/v1"


def _task_env_path(research_root: Path) -> Path:
    return Path(research_root) / ".theory-master" / "env"


def _task_env_value(research_root: Path, key: str) -> str | None:
    path = _task_env_path(research_root)
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("\"").strip("'")
    return None


def quota_auto_adjust_enabled(research_root: Path) -> bool:
    raw = _task_env_value(Path(research_root), _KEY)
    if raw is None:
        raw = os.environ.get(_KEY)
    if raw is None:
        return False
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return False


def write_quota_auto_adjust_setting(research_root: Path, enabled: bool) -> Path:
    path = _task_env_path(Path(research_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"{_KEY}={'1' if enabled else '0'}"
    output: list[str] = []
    replaced = False
    for raw in lines:
        name, sep, _value = raw.partition("=")
        if sep and name.strip() == _KEY:
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(raw)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(replacement)
    atomic(path, "\n".join(output).rstrip() + "\n")
    return path


def controller_state_path(research_root: Path) -> Path:
    return Path(research_root) / ".theory-master" / "quota-auto-adjust.json"


def read_controller_state(research_root: Path) -> dict[str, Any]:
    path = controller_state_path(Path(research_root))
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_controller_state(research_root: Path, **updates: Any) -> dict[str, Any]:
    root = Path(research_root)
    payload = read_controller_state(root)
    if payload.get("schema") != _SCHEMA:
        payload = {"schema": _SCHEMA}
    payload.update(updates)
    payload["enabled"] = quota_auto_adjust_enabled(root)
    payload["updated_at"] = now()
    path = controller_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    return payload


# This controller is deterministic: limits come from Codex rateLimits/read only.
PROACTIVE_SWITCH_THRESHOLD_PERCENT = 30.0


def _window_remaining_percent(window: Any) -> float | None:
    import math

    if not isinstance(window, dict):
        return None
    value = window.get("remaining_percent")
    if value is None:
        used = window.get("used_percent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            return None
        value = 100.0 - used
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _remaining_windows(snapshot: Any) -> tuple[list[float], bool]:
    """Return measured limiting windows and whether any reported window is unknown."""
    if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
        return [], True
    known: list[float] = []
    unknown = False
    for name in ("primary", "secondary"):
        raw = snapshot.get(name)
        if raw is None:
            continue  # An absent window is not an active restriction.
        remaining = _window_remaining_percent(raw)
        if remaining is None:
            unknown = True
        else:
            known.append(remaining)
    return known, unknown


def proactive_switch_target(snapshots: dict[str, dict], current: str | None) -> str | None:
    """Switch at a Cycle boundary when current limiting quota is <30% and lower.

    Compare the minimum remaining percentage of each account's 5-hour/week
    windows. Unknown window data fail closed; equality never switches.
    """
    if current not in {"1", "2"}:
        return None
    from .codex_accounts import quota_available

    present = snapshots.get(current) or {}
    measured, unknown = _remaining_windows(present)
    if quota_available(present) is not True or unknown or not measured:
        return None
    current_remaining = min(measured)
    if current_remaining >= PROACTIVE_SWITCH_THRESHOLD_PERCENT:
        return None
    other = "2" if current == "1" else "1"
    alternative = snapshots.get(other) or {}
    candidate_windows, candidate_unknown = _remaining_windows(alternative)
    if (quota_available(alternative) is not True or candidate_unknown
            or not candidate_windows):
        return None
    return other if min(candidate_windows) > current_remaining else None


def quota_poll_seconds() -> float:
    raw = (os.environ.get(_POLL_KEY) or "60").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 60.0
    return max(5.0, min(value, 300.0))
