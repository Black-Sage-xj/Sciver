"""Verify administrative cancellation before an owning SDK invocation exits."""
from __future__ import annotations

import time
from typing import Any

from .native_goal_recovery import goal_identity, verify_existing_goal


class NativeGoalStopUnconfirmed(RuntimeError):
    """The SDK must retain the failure instead of advancing past live work."""


def cancel_owned_goal(client: Any, state: Any, expected: dict, *,
                      attempts: int = 20, poll_interval: float = 0.1) -> dict:
    """Fence the official best-effort cancel with actual Goal/Turn reads.

    A completed physical Turn can already have an automatic successor when
    the stream consumes its boundary. Never infer quiescence from that old
    Turn or the operation cursor, which unregister clears. Preserve the
    persisted Goal and its usage; administrative cancellation is not science.
    """
    thread_id = expected.get("thread_id")
    if not thread_id or getattr(state, "thread_id", None) != thread_id:
        raise NativeGoalStopUnconfirmed("native Goal stop changed its owning thread")
    origin = {"thread_id": thread_id, "objective_sha256": expected.get("objective_sha256")}
    last_identity = dict(expected)
    interrupted: list[str] = []
    stable = 0
    official_cancelled = False

    def read_owned():
        nonlocal last_identity
        goal = client.get_goal(thread_id)
        if goal is not None:
            last_identity = verify_existing_goal(goal, origin, previous=last_identity)
        return goal

    try:
        # Do not let the official thread-scoped cancel touch a replacement.
        initial = read_owned()
        initial_status = goal_identity(initial)["status"] if initial is not None else None
        if initial_status not in {None, "active", "paused", "blocked", "complete", "budgetLimited", "usageLimited"}:
            raise NativeGoalStopUnconfirmed("native Goal stop has an unknown original status")
        if initial_status == "active":
            client.cancel_goal_operation(state)
            official_cancelled = True
        for attempt in range(attempts):
            goal = read_owned()
            snapshot = client.thread_read(thread_id, include_turns=True).thread
            if getattr(snapshot, "id", None) != thread_id:
                raise NativeGoalStopUnconfirmed("native Goal stop read a foreign thread")
            root = getattr(getattr(snapshot, "status", None), "root", None)
            activity = getattr(root, "type", None)
            turns = list(getattr(snapshot, "turns", None) or [])
            active = [turn for turn in turns
                      if getattr(getattr(turn, "status", None), "value", getattr(turn, "status", None)) == "inProgress"]
            status = goal_identity(goal)["status"] if goal is not None else None
            if status == "active":
                # Surface errors the SDK's best-effort implementation swallows.
                if read_owned() is None:
                    raise NativeGoalStopUnconfirmed("Goal disappeared before administrative pause")
                client.pause_goal(thread_id)
                stable = 0
            elif status not in {None, "paused", "blocked", "complete", "budgetLimited", "usageLimited"}:
                raise NativeGoalStopUnconfirmed("native Goal stop has an unknown Goal status")
            if activity == "active" and active:
                if goal is None:
                    raise NativeGoalStopUnconfirmed("active Turn has lost its owning Goal identity")
                if len(active) != 1 or not turns or turns[-1].id != active[0].id:
                    raise NativeGoalStopUnconfirmed("native Goal stop has ambiguous active Turns")
                if read_owned() is None:
                    raise NativeGoalStopUnconfirmed("Goal disappeared before Turn interruption")
                turn_id = active[0].id
                try:
                    client.turn_interrupt(thread_id, turn_id)
                except Exception as exc:
                    # Only a stale observed Turn permits another exact read;
                    # never obtain or guess a replacement ID from error text.
                    from openai_codex.errors import InvalidRequestError
                    if not isinstance(exc, InvalidRequestError) or not exc.message.startswith("expected active turn id"):
                        raise
                else:
                    interrupted.append(turn_id)
                stable = 0
            elif activity == "idle" and not active and status != "active":
                # A second read across an RPC/poll barrier catches queued tails.
                final_goal = read_owned()
                final_status = goal_identity(final_goal)["status"] if final_goal is not None else None
                if final_status != "active":
                    stable += 1
                    if stable >= 2:
                        return {"confirmed": True, "thread_id": thread_id,
                                "goal_identity": last_identity, "goal_status": final_status,
                                "thread_status": "idle", "official_cancel_requested": official_cancelled,
                                "interrupted_turn_ids": interrupted, "reads": attempt + 1}
            else:
                stable = 0
            if attempt + 1 < attempts:
                time.sleep(poll_interval)
    except NativeGoalStopUnconfirmed:
        raise
    except Exception as exc:
        raise NativeGoalStopUnconfirmed(f"could not confirm owned native Goal stop: {type(exc).__name__}: {exc}") from exc
    raise NativeGoalStopUnconfirmed("owned native Goal or latest physical Turn did not stop")
