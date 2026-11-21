"""Explicit paused-Goal corrections, acknowledged by the actual resumed Turn."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from .native_goal_recovery import NativeGoalRecoveryConflict, verify_existing_goal
from .runtime import now, write_json
from .scientific_objective import frozen_science_digest

SCHEMA = "theory-master/paused-goal-guidance/v1"
FILENAME = "native_goal_resume_guidance.json"


class PausedGoalGuidanceError(RuntimeError):
    """A correction remains pending; its owning operation must stay paused."""


def _path(cycle: Path) -> Path:
    result = cycle / FILENAME
    if any(part.is_symlink() for part in (result, cycle, *cycle.parents)):
        raise ValueError("paused Goal guidance cannot use symlinks")
    return result


def _paused_investigator_owner(cycle: Path, state: dict, identity: dict) -> bool:
    role = state.get("active_role")
    if role is not None:
        return role == "investigator"
    # Normal pause finalization clears active_role. The interrupted invocation
    # and its original Goal, rather than any historical role thread, own resume.
    from .invocations import InvocationLedger

    if state.get("next_executor") != "investigator" or not state.get("last_invocation_id"):
        return False
    last = InvocationLedger(cycle / "invocations.jsonl").snapshot(state["last_invocation_id"])
    if (not last or last.get("role") != "investigator" or last.get("state") != "interrupted"
            or last.get("thread_id") != identity["thread_id"]
            or (state.get("threads") or {}).get("investigator") != identity["thread_id"]):
        return False
    recorded = last.get("native_goal_identity") or {}
    return all(recorded.get(key) == identity.get(key)
               for key in ("thread_id", "created_at", "objective_sha256", "token_budget"))


def queue_guidance(cycle: Path, text: str, *, source: str) -> dict:
    """Queue operator facts only at an inactive, resumable Investigator pause."""
    from .runtime_manager import runtime_health

    cycle = Path(cycle)
    state = json.loads((cycle / "research_state.json").read_text())
    if (state.get("status") != "paused" or state.get("execution_active")
            or not state.get("resumable")):
        raise ValueError("correction requires an inactive resumable Investigator pause")
    if runtime_health(cycle.parent).get("runner_alive"):
        raise ValueError("paused Investigator still has a live Runner")
    if not text.strip() or not source.strip():
        raise ValueError("correction text and source are required")
    native = state.get("native_goal") or {}
    identity = native.get("origin_identity") or native.get("persistent_goal")
    if (not isinstance(identity, dict) or identity.get("thread_id") != native.get("thread_id")
            or not isinstance(identity.get("created_at"), int)
            or not identity.get("objective_sha256")):
        raise ValueError("paused correction requires the recorded native Goal generation")
    if not _paused_investigator_owner(cycle, state, identity):
        raise ValueError("correction requires an inactive resumable Investigator pause")
    run = json.loads((cycle / "run_state.json").read_text())
    budgets = [state.get("investigator_budget") or {}, run.get("investigator_budget") or {}]
    allowance = {item.get("allowance_tokens") for item in budgets}
    if len(allowance) != 1 or not isinstance(next(iter(allowance)), int):
        raise ValueError("paused correction requires a consistent existing Investigator allowance")
    return queue_bound_guidance(cycle, text, source=source, identity=identity,
        frozen=frozen_science_digest(state["hypothesis_lock"]),
        budget={"allowance_tokens":next(iter(allowance)),
                "observed_tokens":max(int(item.get("observed_tokens") or 0) for item in budgets)})


def queue_bound_guidance(cycle: Path, text: str, *, source: str, identity: dict,
                         frozen: str, budget: dict, extra: dict | None = None) -> dict:
    """Internal queue after an explicit pause or an independently bound SDK grant."""
    path = _path(cycle)
    if path.exists():
        previous = json.loads(path.read_text())
        if previous.get("status") != "consumed":
            raise ValueError("a paused Goal correction is already pending")
        archive = cycle / "native_goal_guidance_history"
        if archive.is_symlink() or not isinstance(previous.get("id"), str) or not all(
                char in "0123456789abcdef" for char in previous["id"]) or len(previous["id"]) != 32:
            raise ValueError("invalid paused correction history binding")
        archive.mkdir(exist_ok=True)
        write_json(archive / (previous["id"] + ".json"), previous)
    record = {
        "schema": SCHEMA, "id": uuid.uuid4().hex, "status": "pending", "at": now(),
        "text": text.strip(), "text_sha256": hashlib.sha256(text.strip().encode()).hexdigest(),
        "source": source.strip(), "goal_identity": identity,
        "frozen_objective_sha256": frozen, "budget": dict(budget), "attempts": [],
        **(extra or {}),
    }
    write_json(path, record)
    return {**record, "path": str(path)}


def pending_guidance(handoff: Path, origin: dict | None, *,
                     token_allowance: int | None, tokens_before_goal: int) -> dict | None:
    if len(Path(handoff).parents) < 3:
        return None
    path = _path(Path(handoff).parents[2])
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if record.get("status") == "consumed":
        return None
    if (record.get("schema") != SCHEMA or record.get("status") != "pending"
            or not isinstance(record.get("text"), str) or not record["text"].strip()
            or hashlib.sha256(record["text"].encode()).hexdigest() != record.get("text_sha256")):
        raise PausedGoalGuidanceError("paused correction record is invalid")
    expected = record.get("goal_identity") or {}
    if (origin is None or expected.get("thread_id") != origin["thread_id"]
            or expected.get("objective_sha256") != origin["objective_sha256"]
            or record.get("frozen_objective_sha256") != origin["frozen_objective_sha256"]):
        raise PausedGoalGuidanceError("paused correction belongs to another frozen Goal")
    budget = record.get("budget") or {}
    if (not all(isinstance(budget.get(key), int) and budget[key] >= 0
                for key in ("allowance_tokens", "observed_tokens"))
            or not isinstance(record.get("attempts"), list)):
        raise PausedGoalGuidanceError("paused correction has invalid resource accounting")
    if (token_allowance is None or token_allowance < int(budget["allowance_tokens"])
            or tokens_before_goal < int(budget["observed_tokens"])):
        raise PausedGoalGuidanceError("paused correction cannot reset its existing resource accounting")
    if record.get("budget_review") is not None:
        from .native_goal_budget_notice import validate_notice
        try:
            validate_notice(Path(handoff).parents[2], record)
        except (OSError,ValueError,KeyError) as exc:
            raise PausedGoalGuidanceError("resource approval changed before delivery") from exc
    return {**record, "path": str(path)}


def require_paused_owner(client: Any, origin: dict, record: dict) -> None:
    try:
        identity = verify_existing_goal(client.get_goal(origin["thread_id"]), origin,
                                        previous=record["goal_identity"])
    except NativeGoalRecoveryConflict as exc:
        raise PausedGoalGuidanceError("paused correction lost its original Goal generation") from exc
    if identity["status"] != "paused":
        raise PausedGoalGuidanceError("pending correction requires its original paused Goal")


def deliver_guidance(client: Any, attachment: Any, origin: dict, record: dict, *,
                     check_budget: Callable[[], None], check_control: Callable[[], None] | None) -> dict:
    """Steer one observed Turn once; an RPC acknowledgement is required.

    The installed SDK cannot atomically steer a paused Goal. Activation may
    race with physical execution; this receipt acknowledges delivery, never
    claims that no work ran before the acknowledgement.
    """
    expected = dict(attachment.identity)
    thread_id = origin["thread_id"]

    def owned_active_turn() -> str:
        nonlocal expected
        expected = verify_existing_goal(client.get_goal(thread_id), origin, previous=expected)
        if expected["status"] != "active":
            raise PausedGoalGuidanceError("Goal stopped before correction acknowledgement")
        actual = client.thread_read(thread_id, include_turns=True).thread
        turns = list(actual.turns or [])
        active = [turn for turn in turns
                  if getattr(turn.status, "value", turn.status) == "inProgress"]
        if (actual.id != thread_id or getattr(actual.status.root, "type", None) != "active"
                or len(active) != 1 or not turns
                or turns[-1].id != active[0].id):
            raise PausedGoalGuidanceError("correction has no unambiguous current physical Turn")
        return active[0].id

    path = Path(record["path"])
    current = json.loads(path.read_text())
    if (current.get("id") != record["id"] or current.get("status") != "pending"
            or current.get("text") != record["text"]
            or current.get("text_sha256") != record["text_sha256"]):
        raise PausedGoalGuidanceError("paused correction changed during attachment")
    if check_control is not None:
        check_control()
    check_budget()
    turn_id = owned_active_turn()
    attempt = {"at": now(), "expected_turn_id": turn_id, "status": "requested"}
    current["attempts"].append(attempt)
    write_json(path, current)
    try:
        # Check both authority and the actual latest Turn immediately before
        # the server's expectedTurnId guard. Never retry a guessed successor.
        check_budget()
        if owned_active_turn() != turn_id:
            raise PausedGoalGuidanceError("physical Turn changed before correction delivery")
        response = client.turn_steer(
            thread_id, turn_id,
            "Operator technical correction [" + record["id"] + "]\n" + record["text"]
            + "\nRetain the frozen scientific objective, original evidence and resource authority.",
        )
        attempt["acknowledged_turn_id"] = response.turn_id
        check_budget()
        if response.turn_id != turn_id or owned_active_turn() != turn_id:
            raise PausedGoalGuidanceError("correction acknowledgement crossed a physical Turn boundary")
        if check_control is not None:
            check_control()
    except BaseException as exc:
        attempt.update(status="unconfirmed", error=f"{type(exc).__name__}: {exc}")
        write_json(path, current)
        # Budget/control exceptions retain their existing authority and exit.
        from .native_goal import NativeGoalBudgetExceeded
        from .workflow_support import RecoveryStop
        if isinstance(exc, (NativeGoalBudgetExceeded, RecoveryStop, KeyboardInterrupt, SystemExit,
                            NativeGoalRecoveryConflict)):
            raise
        raise PausedGoalGuidanceError("paused correction delivery was not confirmed") from exc
    attempt["status"] = "acknowledged"
    current.update(status="consumed", consumed_at=now(), acknowledged_turn_id=turn_id,
                   goal_identity_after_ack=expected)
    write_json(path, current)
    return {"guidance_id": record["id"], "thread_id": thread_id, "turn_id": turn_id,
            "text_sha256": record["text_sha256"], "acknowledged": True, "path": str(path)}
