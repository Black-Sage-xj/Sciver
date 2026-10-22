"""Bounded Codex provider capacity re-entry, separate from account quota guards.

A terminal at-capacity error is evidence of a temporary provider outage, not
scientific Goal completion and not an account usage-limit event. Only those
exact errors may enter this persistent, thread-scoped backoff window.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .runtime import atomic

CAPACITY_WAIT_LIMIT_SECONDS = 30 * 60
CAPACITY_BACKOFF_MAX_SECONDS = 60


def is_model_at_capacity(error: object) -> bool:
    """Deliberately narrow; never confuse usageLimit, 403 or scientific blocked."""
    message = str(error or "").casefold()
    return ("selected model is at capacity" in message
            or "model is at capacity" in message
            or "model_at_capacity" in message)


def is_native_goal_thread_busy(error: object, thread_id: str) -> bool:
    """Only a rejected Goal start on this exact thread qualifies for waiting.

    A vague app-server busy/timeout can mean something very different; do not
    mistake it for permission to replay a frozen scientific Goal.
    """
    message = str(error or "").casefold()
    return (bool(thread_id) and "thread must be idle before starting a goal:" in message
            and "json-rpc error -32600" in message
            and message.rsplit("thread must be idle before starting a goal:", 1)[1].strip() == thread_id.casefold())


def settle_failed_capacity_turn(thread: object, turn_id: str) -> bool:
    """Pause and interrupt only a *terminally failed* physical provider turn.

    Called after a native Goal has returned blocked with a matching terminal
    model-capacity event, never during live science or on a vague busy error.
    The exact failed turn must still be the thread's last active turn. A failed
    or ambiguous probe does nothing; the existing bounded busy-wait remains.
    This does not clear the scientific conversation, checkpoint, or Goal budget.
    """
    if not turn_id:
        return False
    try:
        client = thread._client
        snapshot = client.thread_read(thread.id, include_turns=True).thread
        root = getattr(getattr(snapshot, "status", None), "root", None)
        turns = getattr(snapshot, "turns", None) or []
        if (snapshot.id != thread.id or getattr(root, "type", None) != "active"
                or not turns or getattr(turns[-1], "id", None) != turn_id):
            return False
        client.pause_goal(thread.id)
        client.turn_interrupt(thread.id, turn_id)
        return True
    except (AttributeError, ValueError, RuntimeError, OSError):
        # No guessed thread/turn ID and no unbounded retries of cleanup itself.
        return False


class CapacityRecoveryTimeout(RuntimeError):
    """Bounded provider recovery exhausted; escalate to Runtime Supervisor."""

    def __init__(self, thread_id: str, detail: str):
        self.thread_id = thread_id
        super().__init__(
            f"Investigator Native Goal provider recovery failed (thread={thread_id}): {detail}"
        )


class CapacityReconnectWindow:
    """A durable outage window scoped to one Cycle, model/provider, and thread.

    The receipt survives an unexpected Runner loss so a restart cannot silently
    restart a new 30-minute window. A different model/provider never inherits it.
    """

    def __init__(self, handoff_path: Path, *, thread_id: str, model: str, provider: str):
        key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:20]
        self.path = Path(handoff_path).parent / ("capacity-retry-" + key + ".json")
        self.thread_id, self.model, self.provider = thread_id, model, provider

    def snapshot(self) -> dict | None:
        if not self.path.is_file():
            return None
        if self.path.is_symlink() or self.path.stat().st_size > 10_000:
            raise ValueError("unsafe Codex capacity retry receipt")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid Codex capacity retry receipt")
        if (data.get("thread_id"), data.get("model"), data.get("provider")) != (
            self.thread_id, self.model, self.provider
        ):
            # The user deliberately switched routes; the old model's capacity
            # receipt must not constrain the newly chosen model.
            return None
        start = data.get("first_failure_epoch")
        if not isinstance(start, (float, int)) or isinstance(start, bool) or start <= 0:
            raise ValueError("invalid capacity retry start time")
        attempts = data.get("retries", 0)
        if not isinstance(attempts, int) or attempts < 0:
            raise ValueError("invalid capacity retry count")
        return data

    def record_failure(self, message: str) -> dict:
        prior = self.snapshot()
        t = time.time()
        data = dict(prior or {
            "schema": "theory-master/codex-capacity-reconnect/v1",
            "thread_id": self.thread_id,
            "model": self.model,
            "provider": self.provider,
            "first_failure_epoch": t,
            "retries": 0,
        })
        data["last_failure_epoch"] = t
        data["last_error"] = str(message)[:400]
        data["retries"] += 1
        atomic(self.path, json.dumps(data, ensure_ascii=False, sort_keys=True))
        return data

    def record_busy(self, message: str) -> dict:
        """Persist thread-busy backoff within the original capacity deadline."""
        prior = self.snapshot()
        if prior is None:
            raise ValueError("thread busy without a confirmed capacity episode")
        prior["busy_retries"] = int(prior.get("busy_retries") or 0) + 1
        prior["last_busy_error"] = str(message)[:400]
        prior["last_busy_epoch"] = time.time()
        atomic(self.path, json.dumps(prior, ensure_ascii=False, sort_keys=True))
        return prior

    @staticmethod
    def busy_delay(data: dict) -> float:
        """Retry rejected starts gently; a single capacity episode lasts 30m."""
        attempt = max(1, int(data.get("busy_retries") or 1))
        return min(CAPACITY_BACKOFF_MAX_SECONDS,
                   10.0 * 2 ** min(4, attempt - 1),
                   CapacityReconnectWindow.remaining(data))

    @staticmethod
    def remaining(data: dict) -> float:
        return max(0.0, (float(data["first_failure_epoch"]) +
                         CAPACITY_WAIT_LIMIT_SECONDS) - time.time())

    @staticmethod
    def delay(data: dict) -> float:
        return min(CAPACITY_BACKOFF_MAX_SECONDS,
                   5.0 * 2 ** min(5, max(0, data["retries"] - 1)),
                   CapacityReconnectWindow.remaining(data))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
