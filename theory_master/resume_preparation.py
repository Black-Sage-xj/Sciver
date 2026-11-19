"""Shared resume metadata and consumption of an explicitly superseded control.

Worker control callers hold scheduler.lock while capturing and consuming the
old request. The child never removes a request: a later Pause must remain live.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import now, write_json


@dataclass(frozen=True)
class ResumeControl:
    path: Path
    sha256: str
    value: dict[str, Any]


def snapshot_resume_control(cycle: Path) -> ResumeControl | None:
    """Capture only a regular, bounded, known operational control request."""
    path = cycle / "recovery_control.json"
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Unsafe recovery control for Resume")
    if not path.exists():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Resume control must be a regular file")
        data = handle.read(65_537)
    if len(data) > 65_536:
        raise ValueError("Resume control exceeds its size bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid recovery control for Resume")
    if value.get("action") == "abandon_cycle":
        raise ValueError("Cycle abandonment is pending; Resume is forbidden")
    if value.get("action") not in {"pause", "interrupt", "kill"}:
        raise ValueError("Unknown recovery control cannot be superseded by Resume")
    return ResumeControl(path, hashlib.sha256(data).hexdigest(), value)


def consume_resume_control(
    cycle: Path, expected: ResumeControl | None, *, authorization: dict[str, Any]
) -> dict[str, Any] | None:
    """Consume the captured old request under the same Worker action lock.

    Changed or newly arrived requests fail closed. Once the child is launched,
    this owner is never called, so a new operator Pause cannot be swept away.
    """
    current = snapshot_resume_control(cycle)
    if current != expected:
        raise ValueError("Recovery control changed during Resume authorization")
    if current is None:
        return None
    directory = cycle / "resume_controls"
    if directory.is_symlink():
        raise ValueError("Unsafe Resume control archive")
    directory.mkdir(exist_ok=True)
    receipt = {
        "schema": "theory-master/resume-control/v1",
        "at": now(),
        "source_path": str(current.path),
        "source_sha256": current.sha256,
        "superseded_control": current.value,
        "authorization": authorization,
    }
    path = directory / (uuid.uuid4().hex + ".json")
    write_json(path, receipt)
    current.path.unlink()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def restore_operator_guidance(state: dict[str, Any], cycle: Path) -> dict[str, Any]:
    from .attention_guidance import load_attention_guidance

    guidance = load_attention_guidance(cycle)
    if guidance is not None and str(guidance.get("status") or "pending") != "consumed":
        state["operator_guidance"] = guidance
        state["operator_guidance_pending"] = True
        state.setdefault("recovery", {})["attention_guidance"] = {
            "source": guidance.get("source"),
            "path": guidance.get("path"),
            "attention_code": guidance.get("attention_code"),
        }
    return state


def prepare_resume_recovery(state: dict[str, Any], cycle: Path) -> dict[str, Any]:
    """Mark explicit Resume while retaining original error/source/Goal metadata."""
    recovery = dict(state.get("recovery") or {})
    recovery.update(action="resume", resumed_at=now(), allow_thread_fallback=True)
    state["recovery"] = recovery
    return restore_operator_guidance(state, cycle)
