"""Bind a resumed Investigator to its existing SDK Goal and delivery origin.

No scientific text or delivery is synthesized here. Historical Setup metadata
is read from the SDK audit window and the server's Goal must still match it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ORIGIN_KEY = "sdk_native_goal_origin"
ORIGIN_SCHEMA = "theory-master/native-goal-origin/v1"


class NativeGoalRecoveryConflict(RuntimeError):
    """An existing operation cannot safely be identified or reattached."""


def ensure_no_foreign_goal_owner(origin: dict, *, current_server_pid: int | None = None,
                                 proc_root: Path = Path("/proc")) -> None:
    """Refuse another local app-server owning this workspace/rollout.

    The SDK's router/start locks are process-local. This check does not kill or
    modify an old server, nor treats other Tasks sharing CODEX_HOME as owners.
    """
    workspace = Path(origin["workspace_path"])
    thread_id = origin["thread_id"]
    for proc in proc_root.iterdir():
        if not proc.name.isdigit() or int(proc.name) == current_server_pid:
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / "cmdline").read_bytes().split(b"\0")
            if b"app-server" not in command:
                continue
            owns_workspace = Path(os.readlink(proc / "cwd")) == workspace
            owns_rollout = False
            for descriptor in (proc / "fd").iterdir():
                try:
                    target = os.readlink(descriptor).removesuffix(" (deleted)")
                    if Path(target).name.endswith(thread_id + ".jsonl"):
                        owns_rollout = True
                        break
                except FileNotFoundError:
                    continue
            if owns_workspace or owns_rollout:
                raise NativeGoalRecoveryConflict(
                    f"another local app-server may own the original native Goal: pid={proc.name}"
                )
        except (FileNotFoundError, ProcessLookupError):
            continue


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _rows(path: Path) -> list[dict]:
    from .native_goal import _safe_workspace_bytes
    data = _safe_workspace_bytes(Path("/"), Path(*path.absolute().parts[1:]), max_bytes=16 * 1024 * 1024)
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise NativeGoalRecoveryConflict("native origin audit contains a non-object")
    return rows


def find_native_goal_origin(session: Any) -> dict | None:
    """Find only an unfinished invocation in this exact frozen workflow slot."""
    if (session.state.get("recovery") or {}).get("action") != "resume":
        return None
    from .native_goal import execution_limit_setup_binding, verify_native_goal_context_reference, FROZEN_CONTEXT_SHA_MARKER
    from .scientific_objective import serialize_frozen_science, frozen_science_digest
    cycle = session.owner.cycle_dir
    snapshots = session.invocation_ledger.snapshots()
    candidates = [record for record in snapshots
                  if record.get("invocation_id") != session.invocation_id
                  and record.get("role") == "investigator" and record.get("stage") == session.stage
                  and record.get("logical_key") == session.logical_key
                  and record.get("commit_state") == "not_committed"
                  and record.get("state") in {"running", "interrupted", "superseded"}
                  and not record.get("closure_hash")]
    if not candidates:
        return None
    if any(record.get("thread_id") != session.identifier
           or record.get("contract_ref") != session.contract.descriptor() for record in candidates):
        raise NativeGoalRecoveryConflict("unfinished native Goal changed thread or contract")
    recorded_identities = [record.get("native_goal_identity") for record in candidates]
    # A monitor can itself be interrupted. Its durable origin always points
    # back to the original authored Setup, rather than growing a chain.
    inherited = [record.get("native_goal_origin") for record in candidates if record.get("native_goal_origin")]
    if inherited:
        if any(_without_identity(value) != _without_identity(inherited[0]) for value in inherited):
            raise NativeGoalRecoveryConflict("conflicting native Goal origins")
        # Rebuild from the original immutable audit as well: an interrupted
        # monitor's metadata is a reference, never a replacement authority.
        candidates = [record for record in candidates if not record.get("native_goal_origin")]
    if len(candidates) != 1:
        raise NativeGoalRecoveryConflict("ambiguous unfinished native Goal invocation")
    record = candidates[0]
    origin_id = str(record["invocation_id"])
    events = _rows(cycle / "events.jsonl")
    starts = [row for row in events if row.get("type") == "role.started"
              and (row.get("payload") or {}).get("invocation_id") == origin_id]
    if len(starts) != 1 or starts[0]["payload"].get("thread_id") != session.identifier:
        raise NativeGoalRecoveryConflict("native origin has no unique thread-bound start")
    start = starts[0]["at"]
    later = [row["at"] for row in events if row.get("type") == "role.started" and row.get("at", "") > start]
    end = min(later) if later else None
    inputs = [row for row in _rows(cycle / "runtime_events.jsonl")
              if row.get("event") == "AGENT_INPUT" and row.get("role") == "investigator"
              and row.get("stage") == session.stage and str(row.get("ts") or "") >= start
              and (end is None or str(row.get("ts") or "") < end)]
    setups = [row for row in inputs if row.get("input_kind") == "native_goal_setup_turn"]
    objectives = [row for row in inputs if row.get("input_kind") == "native_goal_objective"]
    if len(setups) != 1 or len(objectives) != 1:
        raise NativeGoalRecoveryConflict("native origin Setup or objective is ambiguous")
    for row in [*setups, *objectives]:
        if row.get("thread_id", session.identifier) != session.identifier or row.get("invocation_id", origin_id) != origin_id:
            raise NativeGoalRecoveryConflict("native origin input belongs to another invocation")
    setup_turn, objective = setups[0]["content"], objectives[0]["content"]
    context = setup_turn.partition("\n")[2]
    verify_native_goal_context_reference(objective, context)
    science = session.state["hypothesis_lock"]
    if FROZEN_CONTEXT_SHA_MARKER not in objective and serialize_frozen_science(science) not in objective:
        raise NativeGoalRecoveryConflict("native origin changed the frozen objective")
    handoff = cycle / "workspace" / ".tm-handoff" / f"{origin_id}.json"
    binding = execution_limit_setup_binding(context, handoff)
    frozen_hash = frozen_science_digest(science)
    if binding is not None:
        if binding["invocation_id"] != origin_id or binding["frozen_objective_sha256"] != frozen_hash:
            raise NativeGoalRecoveryConflict("native origin delivery binding changed")
    elif str(handoff) not in context:
        raise NativeGoalRecoveryConflict("historical Setup does not name its origin handoff")
    result = {"schema": ORIGIN_SCHEMA, "invocation_id": origin_id, "thread_id": session.identifier,
              "logical_key": session.logical_key, "stage": session.stage,
              "contract_ref": session.contract.descriptor(), "handoff_path": str(handoff),
              "workspace_path": str(cycle / "workspace"), "objective": objective,
              "objective_sha256": _hash(objective.strip()), "setup_context": context,
              "setup_sha256": _hash(context), "frozen_objective_sha256": frozen_hash}
    if inherited and any(_without_identity(value) != result for value in inherited):
        raise NativeGoalRecoveryConflict("inherited native origin differs from its original audit")
    identities = [*recorded_identities,
                  *((value or {}).get("persistent_goal") for value in inherited)]
    if not any(identity is not None for identity in recorded_identities):
        # A consumed engineering receipt seeds a legacy operation only once.
        # After a real SDK attach/start, its durable identity is independent of
        # that old engine revision and remains authoritative across upgrades.
        from .supervisor_native_goal_recovery import receipt_goal_identity
        identities.append(receipt_goal_identity(session.state, cycle, result))
    for identity in identities:
        if identity is None:
            continue
        if (not isinstance(identity, dict) or identity.get("thread_id") != result["thread_id"]
                or identity.get("objective_sha256") != result["objective_sha256"]):
            raise NativeGoalRecoveryConflict("recorded native Goal identity changed origin")
        previous = result.get("persistent_goal")
        if previous is not None and any(identity.get(key) != previous.get(key)
                for key in ("thread_id", "created_at", "objective_sha256", "token_budget")):
            raise NativeGoalRecoveryConflict("recorded native Goal was replaced")
        if previous is not None and (
                (identity["tokens_used"] > previous["tokens_used"] and identity["time_used_seconds"] < previous["time_used_seconds"])
                or (identity["tokens_used"] < previous["tokens_used"] and identity["time_used_seconds"] > previous["time_used_seconds"])):
            raise NativeGoalRecoveryConflict("recorded native Goal usage was reset")
        if previous is None or (identity["tokens_used"] >= previous["tokens_used"]
                                and identity["time_used_seconds"] >= previous["time_used_seconds"]):
            result["persistent_goal"] = dict(identity)
    return validate_origin(result, handoff, session.identifier)


def _without_identity(origin: dict) -> dict:
    return {key: value for key, value in origin.items() if key != "persistent_goal"}


def execution_limit_delivery_id(session: Any) -> str:
    """Permit only the audited origin's authored ID under a new monitor."""
    origin = getattr(session, "native_goal_origin", None)
    if origin is None:
        return session.invocation_id
    from .scientific_objective import frozen_science_digest
    from .native_goal import execution_limit_setup_binding
    origin = validate_origin(origin, session.handoff_path, session.identifier,
                             expected_frozen=frozen_science_digest(session.state["hypothesis_lock"]))
    if (origin["invocation_id"] == session.invocation_id
            or origin.get("logical_key") != session.logical_key or origin.get("stage") != session.stage
            or origin.get("contract_ref") != session.contract.descriptor()
            or origin["workspace_path"] != str(session.owner.cycle_dir / "workspace")
            or origin["objective"] != session.native_objective
            or origin_from_context(session.setup_context, session.handoff_path, session.identifier) != origin
            or execution_limit_setup_binding(origin["setup_context"], session.handoff_path) != session.limit_binding
            or session.limit_binding is None):
        raise NativeGoalRecoveryConflict("execution-limit delivery changed its original invocation binding")
    return origin["invocation_id"]


def validate_origin(value: Any, handoff: Path, thread_id: str, *, expected_frozen: str | None = None,
                    require_current_handoff: bool = True) -> dict:
    if not isinstance(value, dict) or value.get("schema") != ORIGIN_SCHEMA:
        raise NativeGoalRecoveryConflict("invalid native Goal origin")
    origin_id = value.get("invocation_id")
    workspace = Path(str(value.get("workspace_path") or ""))
    expected = workspace / ".tm-handoff" / f"{origin_id}.json"
    if (not isinstance(origin_id, str) or re.fullmatch(r"inv-[A-Za-z0-9_-]+", origin_id) is None
            or not workspace.is_absolute() or ".." in workspace.parts
            or value.get("handoff_path") != str(expected)
            or (require_current_handoff and Path(handoff) != expected)
            or value.get("thread_id") != thread_id
            or not isinstance(value.get("objective"), str)
            or _hash(value["objective"].strip()) != value.get("objective_sha256")
            or not isinstance(value.get("setup_context"), str)
            or _hash(value["setup_context"]) != value.get("setup_sha256")
            or not isinstance(value.get("frozen_objective_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["frozen_objective_sha256"]) is None
            or (expected_frozen is not None and value["frozen_objective_sha256"] != expected_frozen)
            or any(part.is_symlink() for part in (workspace, *workspace.parents, expected.parent, expected))):
        raise NativeGoalRecoveryConflict("native Goal origin identity or delivery path changed")
    return dict(value)


def origin_from_context(context: str, handoff: Path, thread_id: str) -> dict | None:
    try:
        value, _ = json.JSONDecoder().raw_decode(context.lstrip())
    except (ValueError, TypeError):
        return None
    origin = value.get(ORIGIN_KEY) if isinstance(value, dict) else None
    return validate_origin(origin, handoff, thread_id) if origin is not None else None


def bind_origin_context(origin: dict) -> str:
    """Attach SDK metadata only; the original Setup is not sent again."""
    context = origin["setup_context"]
    value, end = json.JSONDecoder().raw_decode(context.lstrip())
    return json.dumps({**value, ORIGIN_KEY: origin}, ensure_ascii=False) + context.lstrip()[end:]


def goal_identity(goal: Any) -> dict:
    status = getattr(goal.status, "value", goal.status)
    return {"thread_id": goal.thread_id, "created_at": goal.created_at,
            "objective_sha256": _hash(goal.objective.strip()), "status": status,
            "tokens_used": goal.tokens_used, "time_used_seconds": goal.time_used_seconds,
            "token_budget": goal.token_budget}


def verify_existing_goal(goal: Any, origin: dict, *, previous: dict | None = None) -> dict:
    if goal is None:
        raise NativeGoalRecoveryConflict("the original native Goal no longer exists")
    identity = goal_identity(goal)
    if (identity["thread_id"] != origin["thread_id"]
            or identity["objective_sha256"] != origin["objective_sha256"]
            or not isinstance(identity["created_at"], int)):
        raise NativeGoalRecoveryConflict("the stored native Goal is foreign to the frozen origin")
    if previous is not None and (
            any(identity[key] != previous[key] for key in ("thread_id", "created_at", "objective_sha256", "token_budget"))
            or identity["tokens_used"] < previous["tokens_used"]
            or identity["time_used_seconds"] < previous["time_used_seconds"]):
        raise NativeGoalRecoveryConflict("native Goal identity or accumulated usage was reset")
    return identity


@dataclass
class GoalAttachment:
    state: Any
    logical_turn_id: str | None
    snapshot: dict | None
    identity: dict
    terminal_status: str | None = None


def attach_existing_goal(thread: Any, origin: dict, *, check_control=None,
                         start_timeout: float = 60.0) -> GoalAttachment:
    """Observe/resume the same persisted Goal, never start a replacement Goal.

    Registration precedes server reads so future Turn events cannot fall into
    the read/attach gap. Already-running Turns are seeded from the actual read
    snapshot in the SDK adapter, rather than invented as model notifications.
    """
    from .codex_sdk_compat import goal_client
    from openai_codex.generated.v2_all import ActiveThreadStatus, IdleThreadStatus, TurnStatus
    client = goal_client(thread)
    if check_control is not None:
        check_control()
    original = verify_existing_goal(client.get_goal(thread.id), origin, previous=origin.get("persistent_goal"))
    state = client.register_goal_operation(thread.id)
    activated = False
    verified_owned = False
    try:
        goal = client.get_goal(thread.id)
        identity = verify_existing_goal(goal, origin, previous=original)
        verified_owned = True
        if identity["status"] not in {"active", "paused"}:
            client.unregister_goal_operation(state)
            return GoalAttachment(None, None, None, identity, identity["status"])
        actual = client.thread_read(thread.id, include_turns=True).thread
        if actual.id != thread.id or not isinstance(actual.status.root, (ActiveThreadStatus, IdleThreadStatus)):
            raise NativeGoalRecoveryConflict("native Goal thread is not in a resumable operational state")
        active = [turn for turn in actual.turns if turn.status == TurnStatus.in_progress]
        if len(active) > 1:
            raise NativeGoalRecoveryConflict("native Goal has ambiguous active physical Turns")
        snapshot = client.seed_goal_snapshot(state, goal, active[0] if active else None)
        if check_control is not None:
            check_control()
        if not active:
            # Recheck authority immediately before the status-only wakeup. A
            # terminal Goal is never reactivated to manufacture more science.
            goal = client.get_goal(thread.id)
            identity = verify_existing_goal(goal, origin, previous=identity)
            if identity["status"] not in {"active", "paused"}:
                client.unregister_goal_operation(state)
                return GoalAttachment(None, None, None, identity, identity["status"])
            goal = client.activate_existing_goal(thread.id)
            identity = verify_existing_goal(goal, origin, previous=identity)
            activated = True
            snapshot = client.seed_goal_snapshot(state, goal)
        elif identity["status"] == "paused":
            # An operator pause may still be settling an interrupted physical
            # Turn. Do not resume through that Turn or interrupt it implicitly.
            raise NativeGoalRecoveryConflict("paused native Goal still has an active physical Turn")
        deadline = time.monotonic() + start_timeout
        while state.logical_turn_id is None:
            if check_control is not None:
                check_control()
            latest = client.get_goal(thread.id)
            identity = verify_existing_goal(latest, origin, previous=identity)
            if identity["status"] not in {"active", "paused"}:
                client.unregister_goal_operation(state)
                return GoalAttachment(None, None, None, identity, identity["status"])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeGoalRecoveryConflict("timed out waiting for the original native Goal Turn")
            state.wait_for_start(min(0.25, remaining))
        return GoalAttachment(state, state.logical_turn_id, snapshot, identity)
    except BaseException as exc:
        # Only a successful status-only activation is ours to cancel. Foreign
        # identity/read/provider failures never authorize clearing a Goal.
        from .workflow_support import RecoveryStop
        if activated or (verified_owned and isinstance(exc, (RecoveryStop, KeyboardInterrupt))):
            client.cancel_goal_operation(state)
        client.unregister_goal_operation(state)
        raise
