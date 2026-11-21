"""Native Goal orchestration: setup, resource monitoring, delivery and recovery.

The SDK Goal remains authoritative for scientific completion. This runner only
routes operational failures, enforces existing budgets and validates handoffs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import native_goal as native
from .backend_config import ACCOUNT_PROVIDERS
from .codex_sdk_compat import clear_goal
from .native_goal_guidance import PausedGoalGuidanceError
from .provider_errors import ProviderQuotaExhausted, is_provider_quota_exhausted
from .provider_capacity import (
    CapacityReconnectWindow, CAPACITY_WAIT_LIMIT_SECONDS, is_model_at_capacity,
    is_native_goal_thread_busy, CapacityRecoveryTimeout, settle_failed_capacity_turn,
)


class NativeGoalRunner:
    """One invocation owns its recovery window, usage baseline and handoff binding."""
    def __init__(self, backend: Any, role: str, *, goal_objective: str, context_prompt: str,
                 handoff_path: str, thread_id: str | None, model: str, provider: str,
                 token_allowance: int | None = None, tokens_before_goal: int = 0,
                 control_check: Any = None, goal_identity_observer: Any = None):
        self.backend = backend
        self.role = role
        self.goal_objective = goal_objective
        self.context_prompt = context_prompt
        self.handoff_path = handoff_path
        self.thread_id = thread_id
        self.model = model
        self.provider = provider
        self.token_allowance = token_allowance
        self.tokens_before_goal = tokens_before_goal
        self.control_check = control_check
        self.goal_identity_observer = goal_identity_observer
        self.origin = None
        self.attachment = None
        if self.role != "investigator":
            raise ValueError("Native Goal execution is reserved for Investigator")
        if len(self.goal_objective) > native.CODEX_GOAL_MAX_CHARS:
            raise ValueError(
                f"native Goal objective exceeds Codex's {native.CODEX_GOAL_MAX_CHARS}-character limit: {len(self.goal_objective)}"
            )
        native.verify_native_goal_context_reference(self.goal_objective, self.context_prompt)
        self.limit_binding = native.execution_limit_setup_binding(self.context_prompt, Path(self.handoff_path))
        self.fixture_inputs = native.execution_limit_fixture_inputs(self.context_prompt) if self.limit_binding else None
        if self.control_check is not None:
            self.control_check()
        if self.token_allowance is not None and self.tokens_before_goal >= self.token_allowance:
            self.backend.last_native_goal = {
                "status": "budgetLimited", "source": "native_goal_setup_token_guard",
                "thread_id": self.thread_id, "observed_tokens": self.tokens_before_goal,
                "allowance_tokens": self.token_allowance,
            }
            raise native.NativeGoalBudgetExceeded(self.thread_id or "", self.tokens_before_goal, self.token_allowance)
        from .boot_native_goal import pending_boot_native_goal, require_boot_native_origin
        from .native_goal_recovery import origin_from_context

        self.origin = origin_from_context(self.context_prompt, Path(self.handoff_path), self.thread_id or "")
        self.boot_pin = pending_boot_native_goal(self.backend)
        if self.boot_pin is not None:
            require_boot_native_origin(
                self.boot_pin, self.origin, role=self.role, stage=self.boot_pin["stage"],
                logical_key=self.boot_pin["logical_key"], thread_id=self.thread_id,
                provider=self.provider, model=self.model,
            )
        from .native_goal_budget_notice import ensure_review_notice
        ensure_review_notice(Path(self.handoff_path),self.origin,
                             allowance=self.token_allowance,observed=self.tokens_before_goal)
        from .native_goal_guidance import pending_guidance
        self.resume_guidance = pending_guidance(
            Path(self.handoff_path), self.origin, token_allowance=self.token_allowance,
            tokens_before_goal=self.tokens_before_goal,
        )
        if self.origin is not None:
            from .native_goal_recovery import ensure_no_foreign_goal_owner
            from .codex_sdk_compat import server_pid
            ensure_no_foreign_goal_owner(self.origin, current_server_pid=server_pid(getattr(self.backend, "client", None)))
        self.identifier = self.backend.ensure_thread(self.role, self.thread_id, self.model, self.provider)
        self.thread = self.backend.threads[self.identifier]
        if self.origin is not None and self.origin["objective"].strip() != self.goal_objective.strip():
            from .native_goal_recovery import NativeGoalRecoveryConflict
            raise NativeGoalRecoveryConflict("resumed Goal objective differs from its delivery origin")
        self.capacity_window = CapacityReconnectWindow(
            Path(self.handoff_path), thread_id=self.identifier, model=self.model, provider=self.provider
        )
        self.prior_window = self.capacity_window.snapshot() if self.provider in ACCOUNT_PROVIDERS else None
        if self.prior_window is not None and self.capacity_window.remaining(self.prior_window) <= 0:
            self._capacity_timeout(self.prior_window, str(self.prior_window.get("last_error") or "capacity wait expired"))
        if self.origin is None:
            self._setup_context()
        self.provider_usage_baseline: int | None = None
        self.observed_goal_tokens = int(self.tokens_before_goal)
        self.capacity_error = ""
        self.terminal_provider_error = ""
        self.failed_capacity_turn_id = ""
        self.recovered_progress = False
        self.execution_limit_boundary_turn = ""
        self.stop_identity: dict | None = None
        self.stop_confirmation: dict | None = None

    def _setup_context(self):
        """Bind immutable context through the ordinary monitored Turn transport."""
        if self.identifier in self.backend._new_thread_ids or self.context_prompt:
            # Context/qualifier/paths are supplied in a separate ordinary turn,
            # not confused with the Goal's immutable scientific finish condition.
            from .codex_turn_transport import CodexTurnTransport
            setup_transport = CodexTurnTransport(self.backend)
            def setup_usage_guard(used: int) -> None:
                observed = int(self.tokens_before_goal) + used
                if self.token_allowance is not None and observed >= int(self.token_allowance):
                    self.backend.last_native_goal = {
                        "status": "budgetLimited", "source": "native_goal_setup_token_guard",
                        "thread_id": self.identifier, "observed_tokens": observed,
                        "allowance_tokens": int(self.token_allowance),
                    }
                    raise native.NativeGoalBudgetExceeded(self.identifier, observed, int(self.token_allowance))
            self.identifier, _ = setup_transport.run(
                self.role, native.native_goal_setup_turn(self.context_prompt, self.goal_objective), self.identifier, self.model, self.provider,
                usage_guard=setup_usage_guard,
            )
            self.tokens_before_goal += setup_transport.last_usage_tokens
            self.thread = self.backend.threads[self.identifier]
            self.backend._new_thread_ids.discard(self.identifier)
            if self.token_allowance is not None and self.tokens_before_goal >= self.token_allowance:
                self.backend.last_native_goal = {
                    "status": "budgetLimited", "source": "native_goal_setup_token_guard",
                    "thread_id": self.identifier, "observed_tokens": self.tokens_before_goal,
                    "allowance_tokens": self.token_allowance,
                }
                raise native.NativeGoalBudgetExceeded(self.identifier, self.tokens_before_goal, self.token_allowance)

    def _emit_capacity(self, event_name: str, *, wait: float = 0.0, retry: int = 0, elapsed: float = 0.0, error: str = ""):
        if self.backend.collector is not None:
            try:
                self.backend.collector.on_codex({
                    "event": event_name, "agent": self.role, "provider": self.provider,
                    "duration_s": wait,
                    "detail": f"attempt={retry}; elapsed={elapsed:.1f}s; max=1800s",
                    "error": error[:400],
                }, role=self.role, thread_id=self.identifier)
            except Exception:
                self.backend._logger.debug("capacity wait observability failed", exc_info=True)

    def _capacity_timeout(self, receipt: dict, error: str):
        """After a bounded provider retry window, escalate OPERATIONS, not science."""
        self.backend.last_native_goal = {
            "status": "failed", "thread_id": self.identifier,
            "capacity_reconnect": {
                "status": "timeout", "retry_count": int(receipt["retries"]),
                "busy_retries": int(receipt.get("busy_retries") or 0),
                "limit_seconds": CAPACITY_WAIT_LIMIT_SECONDS, "error": error[:400],
            },
        }
        self._emit_capacity("CAPACITY_RETRY_EXHAUSTED",
                       retry=int(receipt["retries"]),
                       elapsed=CAPACITY_WAIT_LIMIT_SECONDS, error=error)
        self.capacity_window.clear()
        raise CapacityRecoveryTimeout(
            thread_id=self.identifier,
            detail=f"Provider capacity recovery timed out after "
                  f"{CAPACITY_WAIT_LIMIT_SECONDS}s "
                  f"(capacity failures={receipt['retries']}, "
                  f"thread busy={receipt.get('busy_retries', 0)}): {error}",
        )

    def _on_event(self, event: Any):
        """Observe provider health and enforce the live native Goal allowance."""
        if self.control_check is not None:
            self.control_check()
        from .observability.codex_events import map_notification
        mapped = map_notification(event, role=self.role, thread_id=self.identifier)
        if not mapped:
            return
        if (mapped.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED"}
                or (mapped.get("event") == "LLM_TURN_COMPLETED" and mapped.get("error"))):
            self.terminal_provider_error = str(mapped.get("error") or "provider turn failed")
        if mapped.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED", "LLM_TURN_COMPLETED"}:
            if is_model_at_capacity(mapped.get("error")):
                self.capacity_error = str(mapped["error"])
                self.failed_capacity_turn_id = str(mapped.get("turn_id") or "")
        if mapped.get("event") in {"LLM_MESSAGE", "TOOL_ACTIVITY"}:
            # Fresh model output AFTER an old transport failure is evidence
            # that the earlier failure is not this Goal's terminal cause.
            self.terminal_provider_error = ""
            self.recovered_progress = True
            # An earlier capacity warning is no longer terminal if the
            # provider has since delivered fresh scientific work.
            self.capacity_error = ""
            self.failed_capacity_turn_id = ""
        if self.backend.collector is not None:
            try:
                self.backend.collector.on_codex(mapped, role=self.role, thread_id=self.identifier)
            except Exception:
                self.backend._logger.debug("native Goal observability event failed", exc_info=True)
        self._observe_usage(mapped)

    def _observe_usage(self, mapped: dict):
        if self.token_allowance is None or mapped.get("event") != "TOKEN_USAGE":
            return
        raw = mapped.get("raw_usage")
        if not isinstance(raw, dict):
            return
        # thread/tokenUsage.total is lifetime cumulative (cached inputs included).
        # First event's last-request amount counts usage NEW to this Goal.
        provider_total = int(raw.get("input_tokens") or 0) + int(raw.get("output_tokens") or 0)
        if self.provider_usage_baseline is None:
            last = mapped.get("last_request_usage")
            request_total = (
                int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0)
                if isinstance(last, dict) else 0
            )
            self.provider_usage_baseline = max(0, provider_total - request_total)
        observed = int(self.tokens_before_goal) + max(0, provider_total - self.provider_usage_baseline)
        self.observed_goal_tokens = max(self.observed_goal_tokens, observed)
        if self.backend.collector is not None:
            self.backend.collector.record_native_goal_usage(
                self.role, observed_tokens=observed, allowance_tokens=int(self.token_allowance)
            )
        if observed >= int(self.token_allowance):
            raise native.NativeGoalBudgetExceeded(self.identifier, observed, int(self.token_allowance))

    def _read_execution_limit(self, goal: native.NativeGoalResult) -> dict[str, Any] | None:
        return native.read_execution_limit_handoff(
            goal, Path(self.handoff_path), invocation_id=self.limit_binding["invocation_id"],
            frozen_objective_sha256=self.limit_binding["frozen_objective_sha256"],
            workspace_path=Path(self.limit_binding["workspace_path"]),
            fixture_inputs=self.fixture_inputs,
        )

    def _verified_limit_exit(self, limit: dict[str, Any]) -> native.NativeGoalExecutionLimit:
        audit = limit.get("input_support_audit")
        pins = None
        if audit is not None:
            refs = set(audit["fixture_references"])
            pins = [{key: row[key] for key in ("reference", "path", "size", "sha256")}
                    for row in self.fixture_inputs if row["reference"] in refs]
        return native.NativeGoalExecutionLimit(self.identifier, limit, Path(self.handoff_path),
                                               fixture_audit_pins=pins)

    def _on_turn_boundary(self, event: Any):
        """Accept an administrative input limit only at a completed physical Turn boundary."""
        from openai_codex.generated.v2_all import TurnCompletedNotification, TurnStatus
        if self.control_check is not None:
            self.control_check()
        payload = getattr(event, "payload", None)
        if (self.limit_binding is None or getattr(event, "method", "") != "turn/completed"
                or not isinstance(payload, TurnCompletedNotification)
                or payload.thread_id != self.identifier or payload.turn.status != TurnStatus.completed
                or payload.turn.error is not None):
            return
        # Usage/resource authority wins over an administrative handoff.
        if self.token_allowance is not None and self.observed_goal_tokens >= int(self.token_allowance):
            raise native.NativeGoalBudgetExceeded(self.identifier, self.observed_goal_tokens, int(self.token_allowance))
        if self.terminal_provider_error or self.capacity_error:
            return
        try:
            limit = self._read_execution_limit(native.NativeGoalResult("execution_limited", self.identifier, 0, 0))
        except native.NativeGoalDeliveryError:
            return  # An incomplete/invalid file grants no permission to interrupt.
        if limit is not None:
            if not self._execution_limit_matches_reviewed_plan(limit):
                return  # A stale Plan quote cannot cancel the live scientific Goal.
            self.execution_limit_boundary_turn = payload.turn.id
            raise self._verified_limit_exit(limit)

    def _execution_limit_matches_reviewed_plan(self, limit: dict[str, Any]) -> bool:
        if limit.get("kind") != "authorization_pending":
            return True
        try:
            context, _ = json.JSONDecoder().raw_decode(self.context_prompt.lstrip())
        except (ValueError, TypeError):
            return False
        return (
            isinstance(context, dict)
            and isinstance(context.get("research_contract"), str)
            and isinstance(context.get("final_gate"), str)
            and limit.get("authorization_clause") in context["research_contract"]
            and limit.get("deliverable_clause") in context["final_gate"]
        )

    def _run_attempt(self):
        self.stop_identity = None
        self.stop_confirmation = None
        try:
            boundary = {"on_turn_boundary": self._on_turn_boundary} if self.limit_binding is not None else {}
            if self.control_check is not None:
                boundary["control_check"] = self.control_check
            if self.goal_identity_observer is not None or self.limit_binding is not None or self.origin is not None:
                boundary["on_operation_started"] = self._observe_goal_identity
                boundary["cancel_owned_operation"] = self._cancel_owned_operation
            if self.origin is not None:
                from .native_goal_recovery import attach_existing_goal
                extra = {}
                if self.resume_guidance is not None:
                    from .codex_sdk_compat import goal_client
                    from .native_goal_guidance import require_paused_owner
                    require_paused_owner(goal_client(self.thread), self.origin, self.resume_guidance)
                    extra = {"on_attached": self._deliver_resume_guidance,
                             "on_stop_confirmed": self._record_resume_stop}
                retained = getattr(self.backend, "retained_goal_home", None)
                boot_pin = getattr(self, "boot_pin", None)
                if boot_pin is not None and (retained is None or retained.binding != boot_pin["account_binding"]
                        or self.backend.client_account != retained.account or self.identifier != retained.thread_id):
                    from .native_goal_recovery import NativeGoalRecoveryConflict
                    raise NativeGoalRecoveryConflict("Boot Resume lost its original Native Goal account or thread")
                quota_resume = (boot_pin is None and retained is not None and not self.backend.retained_goal_delivered
                                and self.origin["thread_id"] == retained.thread_id
                                and self.provider == retained.provider
                                and self.backend.client_account == retained.account)
                self.attachment = attach_existing_goal(
                    self.thread, self.origin, check_control=self.control_check,
                    verified_quota_resume=retained.binding if quota_resume else None,
                    **({"verified_boot_resume": retained.binding} if boot_pin is not None else {}),
                    **extra,
                )
                boundary["attachment"] = self.attachment
            goal = native.run_native_goal(self.thread, self.goal_objective, on_event=self._on_event, **boundary)
        except native.NativeGoalExecutionLimit as exc:
            # run_native_goal has already cancelled the official Goal and
            # closed/unregistered its stream. This is an SDK execution exit,
            # never a forged native blocked or scientific completion.
            self.capacity_window.clear()
            self.backend.last_native_goal = {
                "status": "execution_limited", "source": "sdk_turn_boundary_handoff",
                "native_goal_status": (self.stop_confirmation["goal_status"]
                                       if self.stop_confirmation is not None else "administrative_cancel_requested"),
                "native_goal_cancel_requested": True,
                **({"native_goal_stop_confirmed": True, "stop_confirmation": self.stop_confirmation}
                   if self.stop_confirmation is not None else {}),
                "thread_id": self.identifier, "boundary_turn_id": self.execution_limit_boundary_turn,
                "execution_limit_kind": exc.execution_limit["kind"],
            }
            raise
        except native.NativeGoalBudgetExceeded as exc:
            self.backend.last_native_goal = {
                "status": "budgetLimited", "source": "theory_master_live_token_guard",
                "thread_id": self.identifier, "observed_tokens": exc.observed_tokens,
                "allowance_tokens": exc.allowance_tokens,
                **({"native_goal_stop_confirmed": True, "stop_confirmation": self.stop_confirmation}
                   if self.stop_confirmation is not None else {}),
            }
            raise
        except PausedGoalGuidanceError:
            self.backend.last_native_goal = {
                "status": "guidance_pending", "thread_id": self.identifier,
                "source": "unconfirmed_paused_goal_guidance",
                **({"native_goal_stop_confirmed": True, "stop_confirmation": self.stop_confirmation}
                   if self.stop_confirmation is not None else {}),
            }
            raise
        except Exception as exc:
            # A direct capacity exception may occur before a turn event.
            # Do not retry unrelated transport, quota, contract or SDK errors.
            if self.origin is None and is_native_goal_thread_busy(exc, self.identifier) and self.prior_window is not None:
                # A rejected Goal start does not create another Goal. Never
                # call thread_goal_clear() on a potentially ACTIVE Goal:
                # preserve the original thread and wait for server idle.
                receipt = self.capacity_window.record_busy(str(exc))
                self.prior_window = receipt
                if self.capacity_window.remaining(receipt) <= 0:
                    self._capacity_timeout(receipt, str(exc))
                delay = self.capacity_window.busy_delay(receipt)
                self._emit_capacity("CAPACITY_THREAD_BUSY_WAIT", wait=delay,
                               retry=int(receipt["busy_retries"]),
                               elapsed=CAPACITY_WAIT_LIMIT_SECONDS - self.capacity_window.remaining(receipt),
                               error=str(exc))
                import time
                time.sleep(delay)
                if self.capacity_window.remaining(receipt) <= 0:
                    self._capacity_timeout(receipt, str(exc))
                return None
            if is_provider_quota_exhausted(exc):
                # Native Goal can raise an explicit account quota error before
                # returning a terminal status. Route it through Workflow's
                # durable PAUSED/quota-watcher protocol; never retry it as
                # model capacity or collapse it into a generic FAILED Worker.
                self.capacity_window.clear()
                accounts = getattr(self.backend, "thread_accounts", {})
                account = accounts.get(self.identifier or "") if isinstance(accounts, dict) else None
                self.backend.last_native_goal = {
                    "status": "usageLimited",
                    "thread_id": self.identifier,
                    "runtime_failure_kind": "provider_quota_exhausted",
                    "provider": self.provider,
                    "account": account,
                }
                raise ProviderQuotaExhausted(
                    str(exc), provider=self.provider, account=account
                ) from exc
            if self.origin is not None or not is_model_at_capacity(exc):
                raise
            goal = native.NativeGoalResult("blocked", self.identifier, 0, 0)
            self.capacity_error = str(exc)
        return goal

    def _record_resume_stop(self, receipt):
        self.stop_confirmation = receipt

    def _deliver_resume_guidance(self, attachment):
        from .codex_sdk_compat import goal_client
        from .native_goal_guidance import deliver_guidance
        from .observability.codex_events import map_notification

        self.stop_identity = attachment.identity

        def budget_guard():
            if self.control_check is not None:
                self.control_check()
            for event in goal_client(self.thread).queued_goal_notifications(attachment.state):
                mapped = map_notification(event, role=self.role, thread_id=self.identifier)
                if mapped:
                    self._observe_usage(mapped)
            if (self.token_allowance is not None
                    and self.observed_goal_tokens >= int(self.token_allowance)):
                raise native.NativeGoalBudgetExceeded(
                    self.identifier, self.observed_goal_tokens, int(self.token_allowance))

        self.guidance_receipt = deliver_guidance(
            goal_client(self.thread), attachment, self.origin, self.resume_guidance,
            check_budget=budget_guard, check_control=self.control_check,
        )
        self.resume_guidance = None
        if self.backend.collector is not None:
            try:
                self.backend.collector.on_codex(
                    {"event": "NATIVE_GOAL_GUIDANCE_ACK", **self.guidance_receipt},
                    role=self.role, thread_id=self.identifier,
                )
            except Exception:
                self.backend._logger.debug("paused Goal correction observability failed", exc_info=True)

    def _observe_goal_identity(self):
        from .codex_sdk_compat import goal_client
        from .native_goal_recovery import goal_identity, verify_existing_goal
        client = goal_client(self.thread)
        actual = client.get_goal(self.identifier)
        identity = (verify_existing_goal(actual, self.origin, previous=self.attachment.identity)
                    if self.origin is not None else goal_identity(actual))
        if identity["objective_sha256"] != hashlib.sha256(self.goal_objective.strip().encode()).hexdigest():
            from .native_goal_recovery import NativeGoalRecoveryConflict
            raise NativeGoalRecoveryConflict("started Goal differs from its frozen objective")
        self.stop_identity = identity
        if self.goal_identity_observer is not None:
            if self.origin is None and getattr(self.backend, "goal_account_origin", None):
                from .retained_goal_quota import goal_rollout_identity
                try:
                    original = client.thread_read(self.identifier, include_turns=False).thread
                    self.backend.goal_rollout_origin = goal_rollout_identity(original.path)
                except Exception:
                    self.backend.goal_rollout_origin = None  # Capture failure leaves ordinary work untouched.
            self.goal_identity_observer(identity)

    def _cancel_owned_operation(self, state):
        from .codex_sdk_compat import goal_client
        from .native_goal_stop import NativeGoalStopUnconfirmed, cancel_owned_goal
        try:
            if self.stop_identity is None:
                raise NativeGoalStopUnconfirmed("native Goal stop lacks its original operation identity")
            self.stop_confirmation = cancel_owned_goal(goal_client(self.thread), state, self.stop_identity)
        except Exception as exc:
            self.backend.last_native_goal = {
                "status": "stop_unconfirmed", "thread_id": self.identifier,
                "source": "owned_native_goal_stop", "native_goal_stop_confirmed": False,
                "error": str(exc),
            }
            raise

    def _fixture_availability_conflict(self, exc: native.NativeGoalFixtureAvailabilityConflict):
        from .supervisor_incident import NativeGoalRuntimeFailure

        self.backend.last_native_goal.update(
            runtime_failure_kind="native_goal_fixture_availability_conflict",
            execution_limit_delivery_error=exc.detail,
            fixture_availability_conflict={
                "references": exc.references,
                "handoff_sha256": exc.handoff_sha256,
                "invocation_id": self.limit_binding["invocation_id"],
            },
        )
        raise NativeGoalRuntimeFailure(thread_id=self.identifier, error=exc.detail) from exc

    def _deliver(self, goal: native.NativeGoalResult):
        """Deliver Findings only from native complete; preserve every other stop category."""
        if goal.status == "complete":
            self.capacity_window.clear()
            if self.limit_binding is not None:
                try:
                    limit = self._read_execution_limit(goal)
                except native.NativeGoalFixtureAvailabilityConflict as exc:
                    self._fixture_availability_conflict(exc)
                if limit is not None:
                    if not self._execution_limit_matches_reviewed_plan(limit):
                        raise native.NativeGoalDeliveryError(
                            self.identifier, "authorization hold cites a stale or unreviewed Research Plan clause"
                        )
                    if self.origin is None:
                        clear_goal(self.thread)
                    self.backend.last_native_goal["execution_limit_kind"] = limit["kind"]
                    raise self._verified_limit_exit(limit)
            findings = native.read_goal_findings(goal, Path(self.handoff_path))
            if self.origin is None:
                clear_goal(self.thread)
            return self.identifier, findings
        if goal.status == "blocked" and not self.capacity_error and not self.terminal_provider_error:
            self.capacity_window.clear()
            if self.limit_binding is not None:
                try:
                    limit = self._read_execution_limit(goal)
                except native.NativeGoalFixtureAvailabilityConflict as exc:
                    self._fixture_availability_conflict(exc)
                except native.NativeGoalDeliveryError as exc:
                    self.backend.last_native_goal["execution_limit_delivery_error"] = exc.detail
                else:
                    if limit is not None:
                        if not self._execution_limit_matches_reviewed_plan(limit):
                            self.backend.last_native_goal["execution_limit_delivery_error"] = (
                                "authorization hold cites a stale or unreviewed Research Plan clause"
                            )
                            limit = None
                    if limit is not None:
                        self.backend.last_native_goal["execution_limit_kind"] = limit["kind"]
                        if self.origin is None:
                            clear_goal(self.thread)
                        raise self._verified_limit_exit(limit)
            from .supervisor_incident import NativeGoalRuntimeFailure, _safe
            self.backend.last_native_goal.update(
                runtime_failure_kind="native_goal_dependency_blocked",
                objective_sha256=hashlib.sha256(self.goal_objective.encode()).hexdigest(),
                reported_block_reason=_safe(goal.final_response, 1800),
                automatic_reentries=0,
            )
            raise NativeGoalRuntimeFailure(
                thread_id=self.identifier,
                error="Native Goal returned blocked without a terminal provider error; "
                      "SDK dependency or contract diagnosis is required before resuming. "
                      "No scientific verdict or preservation is authorized.",
            )
        if goal.status != "blocked" or not self.capacity_error or self.provider not in ACCOUNT_PROVIDERS:
            self.capacity_window.clear()
            if goal.status == "blocked" and self.terminal_provider_error:
                # Do not turn provider LLM_FAILED into an Investigator
                # scientific verdict. Runtime Supervisor receives the exact
                # error and may authorize a *bounded* managed resume.
                from .supervisor_incident import NativeGoalRuntimeFailure
                self.backend.last_native_goal["provider_failure"] = self.terminal_provider_error[:1200]
                raise NativeGoalRuntimeFailure(
                    thread_id=self.identifier, error=self.terminal_provider_error,
                    provider_event="LLM_FAILED",
                )
            raise native.NativeGoalIncomplete(goal.status, self.identifier)

    def _wait_for_capacity(self):
        """Recover only explicit capacity failures within their durable deadline."""
        receipt = self.capacity_window.record_failure(self.capacity_error)
        self.prior_window = receipt
        remaining = self.capacity_window.remaining(receipt)
        elapsed = min(CAPACITY_WAIT_LIMIT_SECONDS,
                      CAPACITY_WAIT_LIMIT_SECONDS - remaining)
        if remaining <= 0:
            self._capacity_timeout(receipt, self.capacity_error)
        wait = self.capacity_window.delay(receipt)
        # Some providers emit a terminal capacity failure while the exact
        # physical turn remains active. Only when that failed turn is still
        # the last active turn, settle it before a new native Goal. Never
        # interrupt another turn or clear an unconfirmed active Goal.
        if self.failed_capacity_turn_id and settle_failed_capacity_turn(self.thread, self.failed_capacity_turn_id):
            self._emit_capacity("CAPACITY_FAILED_TURN_SETTLED",
                           retry=int(receipt["retries"]), error=self.capacity_error)
        clear_goal(self.thread)
        self._emit_capacity("CAPACITY_RETRY_WAIT", wait=wait, retry=receipt["retries"],
                       elapsed=elapsed, error=self.capacity_error)
        import time
        time.sleep(wait)
        # Re-read the durable deadline before retry to avoid crossing the
        # window after a long scheduling pause or Runner interruption.
        if self.capacity_window.remaining(receipt) <= 0:
            self._capacity_timeout(receipt, self.capacity_error)

    def run(self) -> tuple[str, str]:
        while True:
            self.capacity_error = ""
            self.terminal_provider_error = ""
            self.failed_capacity_turn_id = ""
            self.recovered_progress = False
            goal = self._run_attempt()
            if goal is None:
                continue
            if self.recovered_progress and self.prior_window is not None:
                # Real scientific work after reconnect ends that outage episode;
                # a later capacity failure gets a fresh 30-minute window.
                self.capacity_window.clear()
                self.prior_window = None
            self.backend.last_native_goal = {
                "status": goal.status, "thread_id": self.identifier,
                "physical_turns": goal.physical_turns, "tools_started": goal.tools_started,
            }
            if self.origin is not None:
                from .codex_sdk_compat import goal_client
                from .native_goal_recovery import verify_existing_goal
                identity = verify_existing_goal(goal_client(self.thread).get_goal(self.identifier), self.origin,
                                                previous=self.attachment.identity)
                if goal.status == "complete" and identity["status"] != "complete":
                    from .native_goal_recovery import NativeGoalRecoveryConflict
                    raise NativeGoalRecoveryConflict("original native Goal is no longer complete at delivery")
                self.backend.last_native_goal.update(
                    origin_binding={key: value for key, value in self.origin.items()
                                    if key not in {"objective", "setup_context"}},
                    persistent_goal=identity,
                )
            result = self._deliver(goal)
            if result is not None:
                return result
            if self.origin is not None:
                # A resumed operation cannot be replaced by the legacy
                # capacity retry (which clears/recreates a Goal).
                raise native.NativeGoalIncomplete(goal.status, self.identifier)
            self._wait_for_capacity()
