"""Monitored ordinary Turns, shared by scientific stages and Native Goal Setup.

This layer owns delivery health, retry, interruption and optional UI callbacks.
Prompt construction and scientific contracts remain with its caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import logging
import time
from typing import Any

from .codex_errors import (CodexProviderRequestRejected, CodexTurnCancelled, CodexTurnFailed,
                           ToolApprovalUnavailable, provider_request_rejection)
from .codex_sdk_compat import approval_handler_scope, collect_turn_result
from .observability.codex_events import (extract_agent_delta, extract_agent_text, extract_final_from_turn,
                                        extract_final_from_thread, extract_error, is_transient_reconnect_error,
                                        map_chat_notification, map_notification)
from .provider_errors import ProviderQuotaExhausted, is_provider_quota_exhausted
from .read_trace import paths_from_event
from .skills import turn_input
from .workflow_support import RecoveryStop


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _callback(backend: Any, callback: Any, *args: Any) -> None:
    if callable(callback):
        try:
            callback(*args)
        except Exception:
            getattr(backend, "_logger", logging.getLogger(__name__)).debug("optional Codex UI callback failed", exc_info=True)


class TurnControl:
    """Own the watchdog/cancel watcher for exactly one remote Turn handle."""
    def __init__(self, handle: Any, timeout: float, cancel: Any, waiting: threading.Event):
        self.handle, self.timeout, self.cancel, self.waiting = handle, timeout, cancel, waiting
        self.done = threading.Event()
        self.fired = threading.Event()
        self.lock = threading.Lock()
        self.completed = False
        self.timer: threading.Timer | None = None
        self.watcher = threading.Thread(target=self._watch_cancel, daemon=True)

    def interrupt(self) -> None:
        try:
            self.handle.interrupt()
        except Exception:
            pass

    def _watch_cancel(self) -> None:
        if self.cancel is not None:
            while not self.done.wait(0.1):
                if self.cancel.is_set():
                    self.interrupt()
                    return

    def _timeout(self) -> None:
        with self.lock:
            if self.completed or self.done.is_set():
                return
            if self.waiting.is_set():
                self._arm_locked()
                return
            self.fired.set()
        self.interrupt()

    def _arm_locked(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        self.timer = threading.Timer(self.timeout, self._timeout)
        self.timer.daemon = True
        self.timer.start()

    def start(self) -> None:
        self.watcher.start()
        self.arm()

    def arm(self) -> None:
        with self.lock:
            if not self.completed and not self.done.is_set():
                self._arm_locked()

    def finish(self) -> None:
        with self.lock:
            self.completed = True

    def close(self) -> None:
        self.done.set()
        if self.watcher.ident is not None:
            self.watcher.join(timeout=0.2)
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()


@dataclass
class TurnDelivery:
    events: list[Any] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    deltas: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    error: str | None = None
    approval_error: str | None = None
    completed: bool = False
    status: str | None = None
    turn_id: str | None = None
    usage_tokens: int = 0

    def require_success(self) -> None:
        rejected = provider_request_rejection(self.error)
        if rejected is not None:
            raise rejected
        if self.approval_error:
            raise ToolApprovalUnavailable(self.approval_error)
        if self.error:
            raise CodexTurnFailed(self.error)
        if not self.completed or self.status != "completed":
            raise CodexTurnFailed(f"Codex Turn did not complete successfully (status={self.status or 'missing terminal event'})")


class CodexTurnTransport:
    """Transport policy over a Backend's thread/client owner, without role policy."""
    def __init__(self, backend: Any):
        self.backend = backend
        self.last_usage_tokens = 0
        self._attempt_usage_start = 0
        self._provider_usage_baseline: int | None = None
        self._guard_error: BaseException | None = None

    def _guard_call(self, callback: Any, *args: Any) -> None:
        """Control/resource authority is never an optional UI callback or retry."""
        if callable(callback):
            try:
                callback(*args)
            except BaseException as exc:
                self._guard_error = exc
                raise

    def _account_usage(self, delivery: TurnDelivery, mapped: dict, guard: Any) -> None:
        raw = mapped.get("raw_usage")
        if not isinstance(raw, dict):
            return
        total = max(0, int(raw.get("input_tokens") or 0) + int(raw.get("output_tokens") or 0))
        event = mapped.get("event")
        if event == "TOKEN_USAGE":
            if self._provider_usage_baseline is None:
                last = mapped.get("last_request_usage")
                request = (int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0)
                           if isinstance(last, dict) else 0)
                # Earlier attempts may have exposed only terminal Turn usage.
                # Those already-counted tokens belong after this run's origin.
                self._provider_usage_baseline = max(0, total - request - self._attempt_usage_start)
            # One immutable baseline spans reconnects, including requests whose
            # notifications were lost before the next cumulative observation.
            consumed = max(self.last_usage_tokens, total - self._provider_usage_baseline)
            delivery.usage_tokens = max(delivery.usage_tokens, consumed - self._attempt_usage_start)
        elif event in {"LLM_TURN_COMPLETED", "LLM_TURN_FAILED", "LLM_RECONNECTING"}:
            # Terminal `usage` describes this Turn, not the thread's lifetime.
            # Take its maximum with live usage, never add both representations.
            delivery.usage_tokens = max(delivery.usage_tokens, total)
        else:
            return
        self.last_usage_tokens = max(self.last_usage_tokens,
                                     self._attempt_usage_start + delivery.usage_tokens)
        self._guard_call(guard, self.last_usage_tokens)

    def run(self, role: str, payload: Any, thread_id: str | None, model: str, provider: str, *,
            on_delta: Any = None, cancel_event: Any = None, on_event: Any = None,
            on_handle: Any = None, request_user_input: Any = None,
            usage_guard: Any = None, control_check: Any = None) -> tuple[str, str]:
        backend = self.backend
        identifier = thread_id
        retry_payload = None
        finalization_attempted = False
        self._guard_error = None
        baseline = getattr(usage_guard, "provider_usage_baseline", None)
        self._provider_usage_baseline = max(0, int(baseline)) if baseline is not None else None
        for attempt in range(backend.OUTER_TURN_RETRIES + 1):
            try:
                self._guard_call(control_check)
                identifier = backend.ensure_thread(role, identifier, model, provider)
                self._guard_call(getattr(usage_guard, "check_thread", None), identifier)
                backend._new_thread_ids.discard(identifier)
                self._attempt_usage_start = self.last_usage_tokens
                delivery = self._attempt(role, backend.threads[identifier],
                                         retry_payload if retry_payload is not None else payload,
                                         identifier, provider, on_delta=on_delta, cancel_event=cancel_event,
                                         on_event=on_event, on_handle=on_handle, request_user_input=request_user_input,
                                         usage_guard=usage_guard, control_check=control_check)
                backend._turn_reads = list(dict.fromkeys(delivery.reads))
                return identifier, self._final_response(backend.threads[identifier], delivery)
            except Exception as exc:
                if (exc is self._guard_error
                        or isinstance(exc, (RecoveryStop, CodexTurnCancelled,
                                            CodexProviderRequestRejected, ProviderQuotaExhausted))):
                    raise
                message = str(exc)
                if is_provider_quota_exhausted(message):
                    raise ProviderQuotaExhausted(message, provider=provider,
                                                 account=backend.thread_accounts.get(identifier or "")) from exc
                watchdog = isinstance(exc, TimeoutError) and "inactivity watchdog" in message.lower()
                retryable = is_transient_reconnect_error(message) or any(marker in message.lower() for marker in (
                    "connection", "broken pipe", "eof", "timed out", "timeout", "app-server", "disconnected",
                    "backend delivery error", "process is not running", "process closed stdout"))
                retry_payload = None
                if role == "budget_reviewer" and "turn completed without assistant final response" in message.lower():
                    if finalization_attempted:
                        raise
                    finalization_attempted = True
                    retry_payload = turn_input(role,
                        "The prior read-only Budget Reviewer turn is complete but had no final JSON. "
                        "Do not repeat any research or tools. From the material already inspected, return one decision "
                        "JSON now with decision, reason, next_test, evidence_ids, progress_refs. If a defensible review "
                        "cannot be completed from inspected evidence, say why instead of inventing a scientific finding "
                        "or resource approval.", include_skills=False, task_config_path=backend.task_config_path)
                if watchdog:
                    retry_payload = turn_input(role,
                        "The previous turn was interrupted by the TheoryMaster inactivity watchdog. Continue the same "
                        "assigned task from the current thread and workspace state. Do not restart or repeat work that "
                        "is already complete. Inspect existing artifacts/results only as needed, then return the required "
                        "stage output.", include_skills=False, task_config_path=backend.task_config_path)
                if not retryable or attempt >= backend.OUTER_TURN_RETRIES:
                    raise
                delay = backend.OUTER_RETRY_DELAYS[min(attempt, len(backend.OUTER_RETRY_DELAYS) - 1)]
                self._notify(role, identifier or "", {"event": "LLM_RECONNECTING",
                    "error": f"{message}; retry {attempt + 1}/{backend.OUTER_TURN_RETRIES}", "retry_index": attempt + 1})
                if role in {"research_chat", "system_assistant"}:
                    _callback(backend, on_event, {"type": "reconnecting", "kind": "connection", "status": "running",
                        "label": "正在重连", "retry_index": attempt + 1, "retry_after": delay})
                getattr(backend, "_logger", logging.getLogger(__name__)).warning("Codex turn unavailable for role=%s; retry %d/%d in %ss: %s",
                                        role, attempt + 1, backend.OUTER_TURN_RETRIES, delay, message)
                self._reset_client()
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise CodexTurnCancelled("Codex turn stopped during reconnect")
                else:
                    time.sleep(delay)
        raise AssertionError("Turn retry loop exhausted without a result")

    def _notify(self, role: str, identifier: str, mapped: dict[str, Any]) -> None:
        collector = getattr(self.backend, "collector", None)
        if collector is not None:
            try:
                collector.on_codex(mapped, role=role, thread_id=identifier)
            except Exception:
                getattr(self.backend, "_logger", logging.getLogger(__name__)).debug("optional Codex observability callback failed", exc_info=True)

    def _reset_client(self) -> None:
        backend = self.backend
        old = backend.client
        backend.client = backend.client_account = None
        backend.threads.clear()
        backend._new_thread_ids.clear()
        if old is not None:
            try:
                old.close()
            except Exception:
                getattr(backend, "_logger", logging.getLogger(__name__)).debug("Codex retry client cleanup failed", exc_info=True)

    def _attempt(self, role: str, thread: Any, payload: Any, identifier: str, provider: str, **callbacks: Any) -> TurnDelivery:
        backend = self.backend
        if not callable(getattr(thread, "turn", None)):
            raise RuntimeError("Codex SDK thread lacks the supported monitored Turn interface")
        waiting = threading.Event()
        request_input = callbacks["request_user_input"]
        def override(original: Any):
            def request(method: str, params: Any):
                if role in {"research_chat", "system_assistant"} and method.endswith("/requestUserInput") and callable(request_input):
                    waiting.set()
                    try:
                        return request_input(params or {})
                    finally:
                        waiting.clear()
                return original(method, params) if callable(original) else {}
            return request
        scope = override if role in {"research_chat", "system_assistant"} and callable(request_input) else None
        delivery = TurnDelivery()
        with approval_handler_scope(getattr(backend, "client", None), scope):
            handle = thread.turn(payload)
            control = TurnControl(handle, backend._watchdog_seconds(), callbacks["cancel_event"], waiting)
            try:
                control.start()
                _callback(backend, callbacks["on_handle"], handle)
                for event in handle.stream():
                    control.arm()
                    delivery.events.append(event)
                    self._consume(delivery, event, role, identifier, provider, callbacks)
                    if delivery.completed:
                        control.finish()
                if not delivery.completed:
                    control.interrupt()
            except BaseException:
                if not delivery.completed:
                    control.interrupt()
                raise
            finally:
                control.close()
                _callback(backend, callbacks["on_handle"], None)
            if callbacks["cancel_event"] is not None and callbacks["cancel_event"].is_set():
                raise CodexTurnCancelled("Codex turn stopped by operator")
            if control.fired.is_set():
                raise TimeoutError(f"Codex turn inactivity watchdog timed out after {backend._watchdog_seconds():g}s")
        delivery.require_success()
        return delivery

    def _consume(self, delivery: TurnDelivery, event: Any, role: str, identifier: str,
                 provider: str, callbacks: dict[str, Any]) -> None:
        backend = self.backend
        try:
            mapped = map_notification(event, role=role, thread_id=identifier)
        except Exception:
            mapped = None
        if mapped:
            # The request has already happened. Persist its observed charge
            # before an operator pause can interrupt processing this event.
            if mapped.get("raw_usage"):
                self._notify(role, identifier, mapped)
            self._account_usage(delivery, mapped, callbacks.get("usage_guard"))
        self._guard_call(callbacks.get("control_check"))
        if role in {"research_chat", "system_assistant"}:
            try:
                visible = map_chat_notification(event)
            except Exception:
                visible = None
            if visible:
                _callback(backend, callbacks["on_event"], visible)
        if mapped:
            delivery.reads.extend(paths_from_event(mapped))
            if mapped.get("event", "").startswith("COMPACTION_"):
                mapped.update(episode_id=identifier, compact_threshold_tokens=backend._native_compact_threshold(),
                              compact_scope=backend._native_compact_scope())
            if not mapped.get("raw_usage"):
                self._notify(role, identifier, mapped)
            if is_provider_quota_exhausted(mapped.get("error")):
                raise ProviderQuotaExhausted(str(mapped["error"]), provider=provider,
                                             account=backend.thread_accounts.get(identifier or ""))
            if mapped.get("event") == "TOOL_APPROVAL_UNAVAILABLE":
                delivery.approval_error = str(mapped.get("error") or "automatic approval review failed")
            if mapped.get("event") == "LLM_RECONNECTING":
                route = backend._run_network_guard("llm-reconnecting", required=False)
                if route and route.get("changed"):
                    raise ConnectionError("Codex connection route changed by codex-net after LLM_RECONNECTING; rebuild client and retry turn")
            if mapped.get("event") in {"LLM_TURN_FAILED", "LLM_FAILED"}:
                delivery.error = str(mapped.get("error") or "Codex turn failed")
            if mapped.get("event") == "LLM_TURN_COMPLETED" and mapped.get("error"):
                delivery.error = str(mapped["error"])
        method = str(_get(event, "method", "") or "")
        payload = _get(event, "payload")
        if method == "item/completed" or method.endswith("/agentMessage/completed"):
            text = extract_agent_text(payload)
            if text:
                delivery.texts.append(text)
        elif method == "item/agentMessage/delta" or method.endswith("/agentMessage/delta"):
            text = extract_agent_delta(payload)
            if text:
                delivery.deltas.append(text)
                _callback(backend, callbacks["on_delta"], text)
        elif method == "turn/completed":
            turn = _get(payload, "turn")
            status = _get(turn, "status")
            delivery.status = str(getattr(status, "value", status)) if status is not None else None
            delivery.turn_id = _get(turn, "id")
            delivery.completed = True
            terminal_error = extract_error(payload)
            if terminal_error:
                delivery.error = terminal_error
            text = extract_final_from_turn(payload)
            if text:
                delivery.texts.append(text)

    def _final_response(self, thread: Any, delivery: TurnDelivery) -> str:
        """Recover text only after an explicitly successful terminal Turn."""
        delivery.require_success()
        if delivery.texts:
            return delivery.texts[-1]
        if delivery.deltas:
            return "".join(delivery.deltas)
        try:
            collected = collect_turn_result(delivery.events, turn_id=str(delivery.turn_id or ""))
            if collected.final_response:
                return collected.final_response
        except Exception:
            getattr(self.backend, "_logger", logging.getLogger(__name__)).debug("official Codex result recovery failed", exc_info=True)
        read = getattr(thread, "read", None)
        if callable(read) and delivery.turn_id:
            for attempt in range(self.backend.COMPLETED_RESPONSE_RECOVERY_ATTEMPTS):
                try:
                    text = extract_final_from_thread(read(include_turns=True), turn_id=delivery.turn_id)
                except Exception:
                    text = None
                if text:
                    return text
                if attempt + 1 < self.backend.COMPLETED_RESPONSE_RECOVERY_ATTEMPTS:
                    time.sleep(self.backend.COMPLETED_RESPONSE_RECOVERY_DELAY_SECONDS)
        raise RuntimeError("Codex backend delivery error: turn completed without assistant final response")
