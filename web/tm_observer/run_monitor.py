"""Bounded read-only Web TUI projection; task paths come from Observer discovery.

No TheoryMaster control imports, process launches or canvas-md file access.
"""
from __future__ import annotations

import json
import re

CYCLE_NAME = re.compile(r"(?:cycle-\d+|[1-9]\d*)\Z")
from pathlib import Path
from typing import Any

REDACT = re.compile(r'(?i)(bearer\s+)[a-z0-9._~+/=-]+|((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[\"\']?[^\s\"\']+')
TABS = ("activity", "investigation", "judge", "usage", "events", "logs", "thread", "research", "artifacts")


def _safe_cycle(run: dict[str, Any]) -> Path | None:
    try:
        root = Path(str(run.get("research_root"))).resolve(strict=True)
        cycle = Path(str(run.get("cycle_dir"))).resolve(strict=True)
        if cycle.parent == root and cycle.is_dir() and CYCLE_NAME.fullmatch(cycle.name):
            return cycle
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def read_json(run: dict[str, Any], name: str, *, max_bytes: int = 4_000_000) -> dict[str, Any]:
    if name not in {"run_state.json", "research_state.json"}:
        return {}
    cycle = _safe_cycle(run)
    if cycle is None:
        return {}
    path = cycle / name
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return {}


def tail_lines(run: dict[str, Any], name: str, *, limit: int = 100) -> list[str]:
    if name not in {"orchestrator.log", "runtime_events.jsonl"}:
        return []
    cycle = _safe_cycle(run)
    if cycle is None:
        return []
    path = cycle / name
    try:
        if path.is_symlink() or not path.is_file():
            return []
        # Read only the last 256 KiB; large historical transcripts are never
        # loaded into the HTTP server's memory.
        with path.open("rb") as handle:
            handle.seek(0, 2)
            length = handle.tell()
            handle.seek(max(0, length - 262144))
            chunk = handle.read(262144)
        result = chunk.decode("utf-8", errors="replace").splitlines()
        if length > 262144:
            result = result[1:]
        return [redact(line[:2000]) for line in result[-max(1, min(200, limit)):]]
    except (OSError, ValueError):
        return []


def redact(text: str) -> str:
    return REDACT.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", text)


def data(run: dict[str, Any]) -> dict[str, Any]:
    observed = read_json(run, "run_state.json")
    scientific = read_json(run, "research_state.json")
    # Lifecycle identity is scientific; run_state is the richer monitoring view.
    # Neither overrides a stopped task by assuming existence of a live Runner.
    snapshot = dict(observed)
    for key in ("status", "resumable", "execution_active", "phase", "attention"):
        if key in scientific:
            snapshot[key] = scientific[key]
    # Only the actual long-running Runner may stamp this capability;
    # the short-lived operator worker must not infer it from installed code.
    cap = scientific.get("sdk_runtime_capabilities")
    if isinstance(cap, dict):
        snapshot["sdk_runtime_capabilities"] = {
            "abandon_cycle_v1": cap.get("abandon_cycle_v1") is True,
            "hot_role_route_v1": cap.get("hot_role_route_v1") is True,
        }
    snapshot.setdefault("cycle_id", run.get("cycle"))
    snapshot.setdefault("master_id", run.get("run_id"))
    snapshot.setdefault("status", run.get("raw_status") or run.get("state"))
    snapshot.setdefault("current_agent", run.get("active_agent"))
    return snapshot


def short_json(value: Any, limit: int = 12000) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (ValueError, TypeError, OverflowError):
        raw = str(value)
    if len(raw) > limit:
        raw = raw[:limit] + "\n… 其余内容请到任务工作区查看"
    return redact(raw)
