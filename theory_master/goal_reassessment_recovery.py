"""Evidence for continuing an exact delivery-only Goal Judge after interruption."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .goal_snapshot import GoalAuthoritySnapshot
from .invocations import LEDGER_SCHEMA, TERMINAL_STATES, stable_hash
from .public_submission import read_bounded_regular_file, strict_json


_SHA256 = re.compile(r"[0-9a-f]{64}")
_CYCLE = re.compile(r"[1-9][0-9]{0,19}")
_INVOCATION = re.compile(r"inv-[A-Za-z0-9_-]{1,128}")
_STAGE = "goal_completion"
_MAX_LEDGER = 16 * 1024 * 1024
_MAX_DOCUMENT = 4 * 1024 * 1024


def goal_reassessment_output_key(revision: str, manifest_sha256: str) -> str:
    return f"goal_completion_{revision}_{manifest_sha256}"


def _ledger_histories(data: bytes) -> dict[str, list[dict]]:
    histories: dict[str, list[dict]] = {}
    if not data or not data.endswith(b"\n"):
        raise ValueError("Goal reassessment requires a complete durable invocation ledger")
    for line in data.splitlines():
        record = strict_json(line)
        if not isinstance(record, dict) or record.get("schema") != LEDGER_SCHEMA:
            raise ValueError("Invalid Goal reassessment invocation record")
        invocation_id = record.get("invocation_id")
        state = record.get("state")
        if (not isinstance(invocation_id, str) or not _INVOCATION.fullmatch(invocation_id)
                or state not in {"prepared", "running"} | TERMINAL_STATES
                or record.get("event") != f"invocation.{state}"):
            raise ValueError("Invalid Goal reassessment invocation identity")
        history = histories.setdefault(invocation_id, [])
        if not history:
            if state != "prepared" or not isinstance(record.get("logical_key"), str):
                raise ValueError("Goal reassessment invocation has no prepared record")
        elif state == "prepared" or any(key in record and record[key] != history[0].get(key)
                                        for key in ("role", "stage", "logical_key", "contract_ref")):
            raise ValueError("Goal reassessment invocation history changed identity")
        history.append(record)
    return histories


def goal_reassessment_invocation_pin(cycle: Path, state: dict, logical_key: str, contract: Any) -> dict | None:
    """Require prior dispatch absence or a verified closure for this exact slot."""
    try:
        data = read_bounded_regular_file(cycle / "invocations.jsonl", _MAX_LEDGER)
        histories = _ledger_histories(data)
        related = {key: rows for key, rows in histories.items()
                   if rows[0]["logical_key"] == logical_key}
        if any(rows[0].get("role") != "judge" or rows[0].get("stage") != _STAGE
               or rows[0].get("kind") != "agent" or rows[0].get("contract_ref") != contract.descriptor()
               or rows[0].get("commit_state") != "not_committed" or rows[0].get("thread_id")
               or rows[0].get("external_job_id") or not _SHA256.fullmatch(str(rows[0].get("input_hash") or ""))
               for rows in related.values()):
            return None
        if state.get("active_invocation_id") and state["active_invocation_id"] not in related:
            return None
        if any(rows[-1]["state"] not in TERMINAL_STATES
               for key, rows in histories.items() if key not in related):
            return None
        pin = {"ledger_sha256": hashlib.sha256(data).hexdigest(), "logical_key": logical_key,
               "contract_ref": contract.descriptor()}
        dispatched = {key: rows for key, rows in related.items() if len(rows) > 1}
        if not dispatched:
            # InvocationPreparation fsyncs a running record before the provider
            # can receive its first Turn. Prepared records alone are safe here.
            return {**pin, "mode": "first_dispatch"}
        if len(dispatched) != 1:
            return None
        invocation_id, rows = next(iter(dispatched.items()))
        states = [row["state"] for row in rows]
        if states not in (["prepared", "running", "completed"],
                          ["prepared", "running", "completed", "committed"]):
            return None
        if not isinstance(rows[1].get("thread_id"), str) or not rows[1]["thread_id"]:
            return None
        completed = rows[2]
        closure_hash = completed.get("closure_hash")
        if not isinstance(closure_hash, str) or not _SHA256.fullmatch(closure_hash):
            return None
        closure_path = cycle / "semantic_closure" / f"{invocation_id}.json"
        if (completed.get("closure_artifact") != str(closure_path)
                or completed.get("commit_state") != "not_committed"
                or (states[-1] == "committed" and (rows[-1].get("commit_state") != "committed"
                    or rows[-1].get("closure_artifact") != str(closure_path)
                    or rows[-1].get("closure_hash") != closure_hash))):
            return None
        closure = strict_json(read_bounded_regular_file(closure_path, _MAX_DOCUMENT))
        if (not isinstance(closure, dict) or closure.get("role") != "judge" or closure.get("stage") != _STAGE
                or stable_hash(closure) != closure_hash or not isinstance(closure.get("closed_output"), dict)):
            return None
        contract.validate(closure["closed_output"], {"mode": _STAGE})
        return {**pin, "mode": "reuse_closure", "invocation_id": invocation_id, "closure_hash": closure_hash}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def goal_reassessment_recovery_pin(spec: Any, plan: dict) -> dict | None:
    """Pin the already persisted snapshot and Judge slot of a verified reassessment."""
    from .contracts import role_contract
    from .goal_materials import material_roots
    from .workflow_invocation import InvocationMixin

    try:
        root = Path(spec.research_root)
        anchor_id, stopped_id = plan["anchor_cycle"], plan["stopped_cycle"]
        revision = plan["delivery_revision"]
        if (not _CYCLE.fullmatch(anchor_id) or not _CYCLE.fullmatch(stopped_id)
                or int(anchor_id) >= int(stopped_id) or not _SHA256.fullmatch(revision)):
            return None
        cycle = root / anchor_id
        state = strict_json(read_bounded_regular_file(cycle / "research_state.json", _MAX_DOCUMENT))
        pending = state.get("goal_delivery_pending") or {}
        if (state.get("cycle_id") != anchor_id or state.get("master_id") != spec.master_id
                or pending.get("cycle_id") != anchor_id or pending.get("closed_status") not in {"over", "completed"}
                or pending.get("delivery_revision") != revision or state.get("goal_submission_gap")):
            return None
        manifest = Path(pending["snapshot_manifest"])
        manifest_sha256 = pending["snapshot_manifest_sha256"]
        if (not _SHA256.fullmatch(manifest_sha256) or manifest.name != "manifest.json"
                or manifest.parent.parent != cycle / "goal_snapshots"
                or re.fullmatch(r"[0-9a-f]{32}", manifest.parent.name) is None):
            return None
        raw_manifest = read_bounded_regular_file(manifest, _MAX_DOCUMENT)
        manifest_doc = strict_json(raw_manifest)
        files = [Path(row["path"]) for row in manifest_doc["files"]]
        sources = [Path(row["path"]) for row in manifest_doc["sources"]]
        allowed = material_roots(spec)
        if (hashlib.sha256(raw_manifest).hexdigest() != manifest_sha256
                or manifest_doc.get("schema") != "theory-master/goal-authority-snapshot/v1"
                or any(path.is_absolute() or ".." in path.parts
                       or (manifest.parent / path).resolve() != manifest.parent / path for path in files)
                or any(not path.is_absolute() or path.resolve() != path
                       or not any(path.is_relative_to(base) for base in allowed) for path in sources)
                or not {Path("access.json"), Path("delivery_revision.json"), Path("science_graph.json"),
                        Path("agent_graph.json"), Path("scientific_model.json"),
                        Path(manifest_doc["plan_path"])}.issubset(files)):
            return None
        snapshot = GoalAuthoritySnapshot.restore(manifest, manifest_sha256)
        if snapshot.access.get("delivery_revision") != revision:
            return None
        contract = role_contract("judge", phase=_STAGE)
        logical_key = InvocationMixin._invocation_logical_key(
            state, "judge", "judge", _STAGE, {"mode": _STAGE},
            goal_reassessment_output_key(revision, manifest_sha256),
        )
        slot = goal_reassessment_invocation_pin(cycle, state, logical_key, contract)
        if slot is not None:
            return {**slot, "anchor_cycle": anchor_id, "stopped_cycle": stopped_id, "delivery_revision": revision,
                    "snapshot_manifest": str(manifest), "snapshot_manifest_sha256": manifest_sha256}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None
    return None
