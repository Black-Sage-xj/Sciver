"""The sole adapter for private interfaces of the tested Codex SDK release.

Only openai-codex 0.155.1 is supported. Managed preflight verifies this exact
version and the private capabilities before creating a scientific Cycle.
"""
from __future__ import annotations

from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterator

SUPPORTED_SDK_VERSION = "0.155.1"
_MISSING = object()


def verify_sdk() -> None:
    try:
        installed = version("openai-codex")
    except PackageNotFoundError as exc:
        raise RuntimeError("Codex SDK unavailable; install `.[sdk]`") from exc
    if installed != SUPPORTED_SDK_VERSION:
        raise RuntimeError(f"Unsupported Codex SDK {installed}; only tested openai-codex=={SUPPORTED_SDK_VERSION} is supported")
    try:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, Thread
        from openai_codex._goal import _GoalNotificationStream
        from openai_codex._run import _collect_turn_result
        from openai_codex.client import CodexClient
        from openai_codex.generated.v2_all import ThreadGoalStatus, TurnStatus
        required = {
            "Codex": Codex, "CodexConfig": CodexConfig,
            "Thread.turn": getattr(Thread, "turn", None),
            "ApprovalMode.deny_all": getattr(ApprovalMode, "deny_all", None),
            "Sandbox.workspace_write": getattr(Sandbox, "workspace_write", None),
            "Sandbox.read_only": getattr(Sandbox, "read_only", None),
            "Goal stream": _GoalNotificationStream, "Turn collector": _collect_turn_result,
            "Goal status": getattr(ThreadGoalStatus, "active", None),
            "Turn status": getattr(TurnStatus, "completed", None),
        }
        for name in ("start_goal_operation", "next_goal_notification", "cancel_goal_operation",
                     "unregister_goal_operation", "thread_goal_clear", "thread_read", "turn_interrupt"):
            required[name] = getattr(CodexClient, name, None)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("Codex provider runtime is incompatible; missing supported SDK capabilities") from exc
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise RuntimeError("Codex provider runtime is incompatible; missing SDK symbols: " + ", ".join(missing))


def _inner(client: Any) -> Any:
    return getattr(client, "_client", None)


def install_approval_handler(client: Any, handler: Any) -> None:
    inner = _inner(client)
    if inner is not None:
        inner._approval_handler = handler


@contextmanager
def approval_handler_scope(client: Any, callback: Any) -> Iterator[Any]:
    """Restore even a missing/None handler when a callback or Turn fails."""
    inner = _inner(client)
    original = getattr(inner, "_approval_handler", _MISSING)
    if callback is not None and inner is not None:
        inner._approval_handler = callback(None if original is _MISSING else original)
    try:
        yield
    finally:
        if callback is not None and inner is not None:
            if original is _MISSING:
                delattr(inner, "_approval_handler")
            else:
                inner._approval_handler = original


def read_thread(client: Any, thread_id: str, *, include_turns: bool = True) -> Any:
    inner = _inner(client)
    if inner is None or not callable(getattr(inner, "thread_read", None)):
        raise RuntimeError("Codex SDK does not expose thread/read")
    return inner.thread_read(thread_id, include_turns=include_turns)


def clear_goal(thread: Any) -> None:
    goal_client(thread).thread_goal_clear(thread.id)


class GoalClient:
    """Explicit surface used by native Goal and capacity recovery."""
    def __init__(self, thread: Any):
        self._client = thread._client

    def start_goal_operation(self, thread_id: str, objective: str) -> Any:
        return self._client.start_goal_operation(thread_id, objective)

    def next_goal_notification(self, state: Any) -> Any:
        return self._client.next_goal_notification(state)

    def unregister_goal_operation(self, state: Any) -> None:
        self._client.unregister_goal_operation(state)

    def cancel_goal_operation(self, state: Any) -> None:
        self._client.cancel_goal_operation(state)

    def thread_goal_clear(self, thread_id: str) -> Any:
        return self._client.thread_goal_clear(thread_id)

    def thread_read(self, thread_id: str, *, include_turns: bool = True) -> Any:
        return self._client.thread_read(thread_id, include_turns=include_turns)

    def pause_goal(self, thread_id: str) -> Any:
        return self._client.pause_goal(thread_id)

    def turn_interrupt(self, thread_id: str, turn_id: str) -> Any:
        return self._client.turn_interrupt(thread_id, turn_id)


def goal_client(thread: Any) -> GoalClient:
    return GoalClient(thread)


def collect_turn_result(events: list[Any], *, turn_id: str) -> Any:
    from openai_codex._run import _collect_turn_result
    return _collect_turn_result(iter(events), turn_id=turn_id)


def goal_stream(**kwargs: Any) -> Any:
    from openai_codex._goal import _GoalNotificationStream
    return _GoalNotificationStream(**kwargs)
