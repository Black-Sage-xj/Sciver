"""Persist a returned Native Goal delivery before semantic registration starts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .invocations import stable_hash
from .runtime import now, write_json


def record_native_delivery(session: Any) -> Path:
    """Record observed SDK metadata, never infer Goal completion from text."""
    invocation = session.invocation_id
    if not isinstance(invocation, str) or not invocation or Path(invocation).name != invocation:
        raise ValueError("Native delivery journal requires a local invocation identity")
    directory = session.owner.cycle_dir / "native_deliveries"
    path = directory / f"{invocation}.json"
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Native delivery journal is unsafe")
    raw = session.raw
    if not isinstance(raw, str):
        raise ValueError("Native delivery journal requires returned text")
    handoff = Path(session.handoff_path)
    if any(part.is_symlink() for part in (handoff, *handoff.parents)):
        raise ValueError("Native delivery handoff is unsafe")
    observed = dict(getattr(session.owner.backend, "last_native_goal", {}) or {})
    payload = {
        "schema": "theory-master/native-delivery/v1",
        "invocation_id": session.invocation_id,
        "thread_id": session.identifier,
        "logical_key": session.logical_key,
        "role": session.role,
        "stage": session.stage,
        "contract_ref": session.contract.descriptor(),
        "authority": "sdk_return",
        "native_goal": observed,
        "raw": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "raw_findings_sha256": stable_hash(json.loads(raw)),
        "handoff_path": str(handoff),
        "handoff_sha256": hashlib.sha256(handoff.read_bytes()).hexdigest()
        if handoff.is_file() else None,
        "objective_sha256": hashlib.sha256(session.native_objective.encode()).hexdigest(),
        "frozen_objective_sha256": (session.limit_binding or {}).get("frozen_objective_sha256"),
    }
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if {key: value for key, value in saved.items() if key != "at"} != payload:
            raise ValueError("Native delivery journal conflicts with existing delivery")
    else:
        write_json(path, {**payload, "at": now()})
    session.state["native_goal"] = {
        **observed,
        "invocation_id": session.invocation_id,
        "delivery_receipt": str(path),
    }
    session.owner._persist(session.state)
    return path
