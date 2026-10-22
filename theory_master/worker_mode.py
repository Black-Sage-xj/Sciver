"""Task-scoped operator intent for the multi-Worker execution mode.

A mode is not itself a running process. OFF hides the secondary console and
stops future allocations without killing any already-running scientific Cycle.
"""
from __future__ import annotations

import fcntl
from pathlib import Path
from typing import Any

from .multi_worker import _read_json, _save_json

SCHEMA = "theory-master/worker-mode/v1"


def mode_path(research_root: Path) -> Path:
    return Path(research_root).resolve() / ".theory-master" / "worker_mode.json"


def read_mode(research_root: Path) -> dict[str, Any]:
    path = mode_path(research_root)
    if path.is_symlink():
        raise ValueError("invalid symlink for Worker mode")
    data = _read_json(path)
    if not data:
        return {"schema": SCHEMA, "enabled": False, "desired_agents": 2, "share_accounts": False, "configured": False}
    if (data.get("schema") != SCHEMA or type(data.get("enabled")) is not bool
            or type(data.get("desired_agents")) is not int
            or not 1 <= data["desired_agents"] <= 1024):
        raise ValueError("invalid task Worker mode registry")
    if type(data.get("share_accounts", False)) is not bool:
        raise ValueError("share_accounts must be boolean")
    return {"schema": SCHEMA, "enabled": data["enabled"],
            "desired_agents": data["desired_agents"],
            "share_accounts": data.get("share_accounts", False),
            "configured": True}


def configure_mode(research_root: Path, *, enabled: bool, desired_agents: int, share_accounts: bool | None = None) -> dict[str, Any]:
    if type(enabled) is not bool or type(desired_agents) is not int or not 1 <= desired_agents <= 1024:
        raise ValueError("Worker count must be 1..1024 and enabled must be bool")
    if share_accounts is not None and type(share_accounts) is not bool:
        raise ValueError("share_accounts must be bool")
    path = mode_path(research_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "worker_mode.lock").open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError("invalid symlink for Worker mode")
        previous = read_mode(research_root)
        _save_json(path, {"schema": SCHEMA, "enabled": enabled,
                          "desired_agents": desired_agents,
                          "share_accounts": previous["share_accounts"] if share_accounts is None else share_accounts})
        return read_mode(research_root)


def request_one_worker(research_root: Path, *, registered_count: int) -> dict[str, Any]:
    """Record one UI add request without touching existing Worker processes.

    A previous numeric parallelism limit is not an explicit UI request.
    """
    if type(registered_count) is not int or registered_count < 0:
        raise ValueError("invalid registered Worker count")
    path = mode_path(research_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "worker_mode.lock").open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        current = read_mode(research_root)
        raw = _read_json(path) if path.is_file() else {}
        requested = raw.get("requested_agents")
        if type(requested) is not int or not 1 <= requested <= 1024:
            requested = max(1, registered_count)
        target = max(registered_count, requested) + 1
        if target > 1024:
            raise ValueError("Worker request limit reached (1024)")
        _save_json(path, {"schema": SCHEMA, "enabled": True,
                          "desired_agents": target, "requested_agents": target,
                          "share_accounts": current["share_accounts"]})
        return {**read_mode(research_root), "requested_agents": target,
                "previously_enabled": current["enabled"]}


def reduce_one_worker_request(research_root: Path, *, registered_count: int,
                              require_pending: bool = True) -> dict[str, Any]:
    """Reduce the desired future concurrency without touching running Cycles.

    For explicit pending cancellation, reject a count already claimed by a
    Worker. For an archived finished Worker, caller has already hidden its
    card, so one capacity slot may be released without affecting live owners.
    """
    if type(registered_count) is not int or registered_count < 0:
        raise ValueError("invalid registered Worker count")
    path = mode_path(research_root)
    with (path.parent / "worker_mode.lock").open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        current = read_mode(research_root)
        raw = _read_json(path) if path.is_file() else {}
        requested = raw.get("requested_agents")
        if type(requested) is not int or not 1 <= requested <= 1024:
            if require_pending:
                raise ValueError("no explicit pending Agent request to cancel")
            requested = current["desired_agents"]
        if require_pending and requested <= registered_count:
            raise ValueError("Agent slot already assigned; refresh the page")
        target = max(1, registered_count, requested - 1)
        if target == requested and require_pending:
            raise ValueError("no Agent capacity to reduce")
        _save_json(path, {"schema": SCHEMA, "enabled": current["enabled"],
                          "desired_agents": target, "requested_agents": target,
                          "share_accounts": current["share_accounts"]})
        return {"requested_agents": target, "desired_agents": target,
                "running_cycles_unaffected": True}
