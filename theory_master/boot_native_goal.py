"""Authenticate the exact unfinished Investigator Goal a cold boot may reattach."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from .contract_stages import role_contract
from .invocation_execution import NATIVE_INVESTIGATOR_STAGES
from .invocations import TERMINAL_STATES, stable_hash
from .native_goal import FROZEN_CONTEXT_SHA_MARKER, execution_limit_setup_binding
from .native_goal_recovery import NativeGoalRecoveryConflict, find_native_goal_origin
from .public_submission import read_bounded_regular_file, strict_json
from .scientific_objective import frozen_science_digest, locked_science_view


_INVOCATION_ID = re.compile(r"inv-[A-Za-z0-9_-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTITY_KEYS = ("thread_id", "created_at", "objective_sha256", "token_budget")
_USAGE_KEYS = ("tokens_used", "time_used_seconds")


def _hash_string(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _durable_identity(identity: object, thread_id: str) -> bool:
    return (isinstance(identity, dict) and identity.get("thread_id") == thread_id
            and type(identity.get("created_at")) is int and identity["created_at"] > 0
            and isinstance(identity.get("status"), str) and bool(identity["status"])
            and _hash_string(identity.get("objective_sha256"))
            and "token_budget" in identity
            and (identity["token_budget"] is None or (type(identity["token_budget"]) is int
                                                       and identity["token_budget"] > 0))
            and all(type(identity.get(key)) is int and identity[key] >= 0 for key in _USAGE_KEYS))


def _matching_starts(cycle: Path, invocation_ids: set[str], stage: str, thread_id: str,
                     descriptor: dict) -> bool:
    data = read_bounded_regular_file(cycle / "events.jsonl", 16 * 1024 * 1024)
    seen: set[str] = set()
    for line in data.splitlines():
        if not line.strip():
            continue
        event = strict_json(line)
        if event.get("type") != "role.started":
            continue
        payload = event.get("payload") or {}
        invocation_id = payload.get("invocation_id")
        if invocation_id not in invocation_ids:
            continue
        if (invocation_id in seen or payload.get("role") != "investigator"
                or payload.get("stage") != stage or payload.get("thread_id") != thread_id
                or payload.get("contract_id") != descriptor["contract_id"]
                or payload.get("contract_version") != descriptor["contract_version"]
                or payload.get("contract_schema_hash") != descriptor["schema_hash"]):
            return False
        seen.add(invocation_id)
    return seen == invocation_ids


def native_goal_boot_pin(cycle: Path, state: dict, invocations: dict[str, dict]) -> dict | None:
    """Bind prior-boot metadata to local SDK authority without contacting Codex."""
    invocation_id = state.get("active_invocation_id")
    if not isinstance(invocation_id, str) or _INVOCATION_ID.fullmatch(invocation_id) is None:
        return None
    try:
        row = invocations.get(invocation_id) or {}
        stage, logical_key, thread_id = (row.get(key) for key in ("stage", "logical_key", "thread_id"))
        lock = state.get("hypothesis_lock")
        identity = row.get("native_goal_identity")
        if (state.get("active_role") != "investigator" or not isinstance(lock, dict)
                or lock.get("status") != "locked" or row.get("kind") != "agent"
                or row.get("role") != "investigator" or stage not in NATIVE_INVESTIGATOR_STAGES
                or row.get("state") != "running" or row.get("commit_state") != "not_committed"
                or row.get("closure_hash") or not _hash_string(logical_key)
                or not isinstance(thread_id, str) or not thread_id
                or (state.get("threads") or {}).get("investigator") != thread_id
                or not _durable_identity(identity, thread_id)
                or next(reversed(invocations), None) != invocation_id):
            return None
        if any(record.get("state") not in TERMINAL_STATES or record["state"] == "completed"
               or (record["state"] == "committed" and (record.get("commit_state") != "committed"
                   or record.get("logical_key") == logical_key))
               for key, record in invocations.items() if key != invocation_id):
            return None
        contract = role_contract("investigator", phase=stage)
        descriptor = contract.descriptor()
        if row.get("contract_ref") != descriptor:
            return None
        session = SimpleNamespace(
            state={**state, "recovery": {**(state.get("recovery") or {}), "action": "resume"}},
            owner=SimpleNamespace(cycle_dir=cycle),
            invocation_ledger=SimpleNamespace(snapshots=lambda: list(invocations.values())),
            invocation_id=None, stage=stage, logical_key=logical_key, identifier=thread_id, contract=contract,
        )
        origin = find_native_goal_origin(session)
        if origin is None:
            return None
        persistent = origin.get("persistent_goal")
        if (not _durable_identity(persistent, thread_id)
                or any(identity[key] != persistent[key] for key in _IDENTITY_KEYS)
                or any(identity[key] > persistent[key] for key in _USAGE_KEYS)):
            return None
        original_id = origin["invocation_id"]
        if (invocation_id != original_id and not row.get("native_goal_origin")) or not _matching_starts(
                cycle, {invocation_id, original_id}, stage, thread_id, descriptor):
            return None
        setup_source = origin["setup_context"].lstrip()
        _, end = json.JSONDecoder().raw_decode(setup_source)
        setup = strict_json(setup_source[:end])
        frozen_sha256 = frozen_science_digest(locked_science_view(lock))
        # Inline Goals carry the scientific text themselves. Setup only
        # repeats it for the digest-reference form.
        setup_science = setup.get("frozen_objective")
        handoff = Path(origin["handoff_path"])
        if handoff.exists() and not handoff.is_file():
            return None
        binding = execution_limit_setup_binding(origin["setup_context"], handoff)
        if (binding is None or frozen_sha256 != origin["frozen_objective_sha256"]
                or (setup_science is None and FROZEN_CONTEXT_SHA_MARKER in origin["objective"])
                or (setup_science is not None and frozen_sha256 != frozen_science_digest(setup_science))):
            return None
        for inv in {invocation_id, original_id}:
            for directory in ("native_deliveries", "semantic_closure"):
                artifact = cycle / directory / f"{inv}.json"
                if artifact.parent.is_symlink() or artifact.is_symlink() or artifact.exists():
                    return None
        from .retained_goal_quota import boot_goal_account_binding

        account_binding = boot_goal_account_binding(cycle, state, origin)
        if not isinstance(account_binding, dict) or not all(
                isinstance(account_binding.get(key), str) and account_binding[key] for key in ("provider", "model")):
            return None
    except (AttributeError, IndexError, KeyError, NativeGoalRecoveryConflict, OSError, TypeError, ValueError):
        return None
    return {"invocation_id": invocation_id, "origin_invocation_id": original_id, "stage": stage,
            "logical_key": logical_key, "thread_id": thread_id, "contract_ref": descriptor,
            "origin_sha256": stable_hash(origin), "account_binding": account_binding}


def require_boot_native_origin(pin: dict, origin: dict | None, *, role: str, stage: str,
                               logical_key: str, thread_id: str, provider: str, model: str) -> None:
    """Recheck the actual resumed slot before sending any Goal operation."""
    account = pin.get("account_binding") or {}
    if (not origin or role != "investigator" or pin.get("stage") != stage
            or pin.get("logical_key") != logical_key or pin.get("thread_id") != thread_id
            or account.get("provider") != provider or account.get("model") != model
            or origin.get("invocation_id") != pin.get("origin_invocation_id")
            or origin.get("contract_ref") != pin.get("contract_ref")
            or origin.get("stage") != stage or origin.get("logical_key") != logical_key
            or origin.get("thread_id") != thread_id or stable_hash(origin) != pin.get("origin_sha256")):
        raise NativeGoalRecoveryConflict("cold boot could not establish the original Investigator Goal")


def pending_boot_native_goal(backend: object) -> dict | None:
    """Expose the boot pin only until its original delivery is recorded."""
    pin = getattr(backend, "boot_native_goal", None)
    return pin if isinstance(pin, dict) and not getattr(backend, "retained_goal_delivered", False) else None
