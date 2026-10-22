"""Supervisor Runtime profile: bounded review of a persisted FAILED Cycle.

Runs in the Runtime Supervisor process after the Runner exited. The model only
diagnoses (read-only tools) and returns ``resume`` or ``hold``; the SDK checks
the safe checkpoint and the per-Cycle resume budget before any resume.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .runtime import now, write_json
from .supervisor_intervention import (
    DEFAULT_PROVIDER, MAX_RUNTIME_RESUMES, SupervisorSession, supervisor_model,
)

POLICY_VERSION = 1
_COUNT_KEY = "supervisor_runtime_resume_count"


def _brief(value: Any, limit: int = 1700) -> str:
    return " ".join(str(value or "").split())[:limit]


def _log_tail(research_root: Path, limit: int = 60, *, log_path: Path | None = None) -> str:
    path = Path(log_path) if log_path is not None else Path(research_root) / ".theory-master" / "runner.log"
    if path.is_symlink():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 24_000))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    text = "\n".join(lines[-limit:])
    return re.sub(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)


def _parse(raw: str) -> dict[str, str]:
    value = str(raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", value, flags=re.I).strip()
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Runtime Supervisor must return strict JSON") from exc
    if not isinstance(result, dict) or result.get("action") not in {"resume", "hold"}:
        raise ValueError("Runtime Supervisor action must be resume or hold")
    if not isinstance(result.get("reason"), str) or len(result["reason"].strip()) < 20:
        raise ValueError("Runtime Supervisor reason is missing")
    suggestion = result.get("repair_suggestion") or ""
    diagnosis = result.get("diagnosis") or ""
    if not isinstance(suggestion, str) or not isinstance(diagnosis, str):
        raise ValueError("Runtime Supervisor fields must be strings")
    return {"action": result["action"], "reason": result["reason"].strip(),
            "diagnosis": diagnosis.strip()[:4000], "repair_suggestion": suggestion.strip()[:4000]}


def resume_blocker(state: dict[str, Any], *, for_repair: bool = False,
                   budget_revalidated: bool = False,
                   runtime_revalidated: bool = False) -> str | None:
    """Deterministic gate; None means a managed resume is structurally allowed."""
    if state.get("abandoned_cycle") or str(state.get("phase") or "") == "abandon_requested":
        return "Cycle was abandoned; resume is forbidden."
    if not state.get("resumable"):
        return "Runner marked this Cycle as not resumable."
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    if recovery.get("action") in {"pause", "interrupt"}:
        return "Operator pause or interruption remains in force; repair does not authorize resume."
    if state.get("protocol_health") == "violation":
        return "Protocol/contract violation requires a verified repair; no plain managed resume."
    if ((recovery.get("category") == "budget_validation" and not budget_revalidated)
            or (recovery.get("category") == "runtime_validation"
                and not (for_repair or runtime_revalidated))):
        return "Runtime/budget validation failure requires objective revalidation before resume."
    failure_text = str(recovery.get("message") or "").lower()
    if any(flag in failure_text for flag in (
        "selected model is not supported", "model is not supported",
        "authentication failed", "invalid_api_key", "403 forbidden",
        "permission denied", "unauthorized access",
    )):
        return "Provider configuration, authentication or permission must be fixed before retry."
    budget = state.get("investigator_budget") or {}
    if isinstance(budget, dict) and (budget.get("exhausted") or budget.get("rejected")):
        return "Investigator budget guard is active; Supervisor cannot override it."
    checkpoint = state.get("last_safe_checkpoint")
    valid_checkpoint = bool(checkpoint and Path(str(checkpoint)).is_file()
                            and not Path(str(checkpoint)).is_symlink())
    if not (valid_checkpoint or state.get("role_outputs")):
        return "No existing safe checkpoint or committed role boundary exists for managed resume."
    from .supervisor_repair_receipts import unused_repair_credit
    if (int(state.get(_COUNT_KEY) or 0) >= MAX_RUNTIME_RESUMES and not for_repair
            and not (runtime_revalidated and unused_repair_credit(state))):
        return (f"The {MAX_RUNTIME_RESUMES} authorized Supervisor runtime resumes "
                "for this Cycle were used; repeated failure requires review.")
    return None


def _recovered_capacity_thread_is_idle(research_root: Path, cycle_dir: Path,
                                       state: dict[str, Any]) -> bool:
    """Read-only provider check before another bounded managed resume.

    A timed-out busy thread can become idle later. Never clear or interrupt an
    active Goal, reset retry budgets, or fabricate a scientific completion.
    """
    from .provider_capacity import is_model_at_capacity

    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    if not is_model_at_capacity(recovery.get("message")):
        return False
    events = cycle_dir / "events.jsonl"
    if not events.is_file() or events.is_symlink() or events.stat().st_size > 2_000_000:
        return False
    try:
        rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
        started = [r.get("payload", {}).get("thread_id") for r in rows
                   if r.get("type") == "role.started" and
                   r.get("payload", {}).get("role") == "investigator"]
        previous_failures = [str(r.get("payload", {}).get("error") or "") for r in rows
                             if r.get("type") == "run.failed"]
        if not started or not previous_failures:
            return False
        thread_id = str(started[-1] or "")
        if not any("Provider capacity recovery timed out after 1800s" in error
                   and "thread must be idle before starting a goal: " + thread_id in error
                   for error in previous_failures):
            return False
        from .worker_runtime import list_workers, worker_dir
        worker_id = str(state.get("worker_id") or "")
        workers = list_workers(research_root)
        owner = next((row for row in workers if row.get("worker_id") == worker_id), None)
        if not owner or owner.get("process_alive") or owner.get("status") not in {"FAILED", "NEEDS_ATTENTION"}:
            return False
        private_home = worker_dir(research_root, worker_id) / "codex-home"
        if not private_home.is_dir() or private_home.is_symlink():
            return False
        from openai_codex import Codex, CodexConfig
        with Codex(CodexConfig(env={"CODEX_HOME": str(private_home)})) as client:
            client.thread_resume(thread_id, include_turns=False)
            thread = client._client.thread_read(thread_id).thread
            return (getattr(getattr(thread.status, "root", None), "type", None)
                    == "idle" and thread.id == thread_id)
    except (OSError, ValueError, RuntimeError, ImportError, AttributeError, TypeError) as exc:
        __import__("logging").getLogger(__name__).info(
            "Capacity thread idle probe unavailable: %s", type(exc).__name__)
        return False


def review_runtime_failure(
    *, backend: Any | None, research_root: Path, cycle_dir: Path, state: dict[str, Any],
    state_path: Path, incident_id: str, log_path: Path | None = None,
    readonly_provider_probe_only: bool = False,
    scheduler_locked: bool = False,
) -> dict[str, str]:
    from .budget_reconciliation import LEGACY_ERROR, reconcile_legacy_failure
    reconciliation_error = None
    reconciled = None
    validated_repair = None
    if not readonly_provider_probe_only:
        from .supervisor_repair_receipts import inspect_repair_receipt, validate_existing_method
        try:
            validated_repair = inspect_repair_receipt(cycle_dir, state)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if validated_repair is None:
            import subprocess
            try:
                validated_repair = validate_existing_method(
                    cycle_dir, state, engine=Path(__file__).resolve().parent,
                    log=_log_tail(research_root, log_path=log_path))
                if validated_repair:
                    write_json(state_path, state)
            except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
                pass
    if validated_repair is None and not readonly_provider_probe_only:
        from .supervisor_configuration_repair import configuration_failure, validate_configuration
        if configuration_failure(state):
            try:
                candidate = validate_configuration(research_root, cycle_dir, state,
                    log=_log_tail(research_root, log_path=log_path))
                if candidate.get('phase') == 'repair_validated':
                    validated_repair = candidate
                    write_json(state_path, state)
            except (OSError, ValueError, TypeError, KeyError):
                pass
    # Revalidate objective repairs before consuming a sticky historical flag.
    # No model is required to verify hashes and a budget ledger.
    if ((state.get("protocol_health") == "violation"
            or (state.get("recovery") or {}).get("category") == "budget_validation")
            and (state.get("recovery") or {}).get("message") == LEGACY_ERROR
            and not state.get("budget_failure_reconciliation")
            and not readonly_provider_probe_only):
        try:
            reconciled = reconcile_legacy_failure(
                cycle_dir, state=state, scheduler_locked=scheduler_locked)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            reconciliation_error = str(exc)
    from .supervisor_recovery import failure_details
    recovery = failure_details(state)
    message = str(recovery.get("message") or state.get("human_attention") or "Persisted TheoryMaster state is FAILED.")
    model = supervisor_model(research_root)
    from .supervisor_incident import incident_context
    evidence = incident_context(cycle_dir, state)
    session = SupervisorSession(
        cycle_dir, profile="runtime", incident_id=incident_id, code="run_failed",
        summary=f"{recovery.get('kind') or 'Error'}: {_brief(message, 1000)}", model=model,
    )
    session.append("evidence", json.dumps(evidence, ensure_ascii=False)[:11000])
    model_invoked = False
    tool_reads: list[str] = []
    blocker = resume_blocker(state, budget_revalidated=reconciled is not None,
                             runtime_revalidated=validated_repair is not None)
    # Engine faults with an attributable allowlisted traceback go to isolated
    # repair instead of the resume-only reviewer, regardless of whether the
    # generic ValueError mapper already marked protocol_health=violation.
    from .supervisor_runtime_repair import repair_target, _log_for_repair, attempt_runtime_repair
    repair_safe = resume_blocker({**state, "protocol_health": "valid"}, for_repair=True) is None
    target = (repair_target(_log_for_repair(research_root, log_path=log_path), state,
                            Path(__file__).resolve().parent)
              if repair_safe else None)
    if reconciliation_error is not None:
        result = {"action": "hold", "reason": "Budget migration revalidation failed: " + reconciliation_error,
                  "diagnosis": "Budget migration evidence remains incomplete or unsafe.",
                  "repair_suggestion": "Inspect the original ledger, approval receipt and legacy bridge; repair the attributable migration cause and rerun SDK budget_reconciliation. Do not clear protocol flags manually."}
        session.append("system", result["reason"])
    elif reconciled is not None and blocker is None:
        result = {"action": "resume", "reason": "SDK verified the legacy budget bridge, ledger, frozen checkpoint and stopped ownership; stale protocol classification corrected. Request a guarded resume of the same Cycle.",
                  "diagnosis": "Deterministic budget migration repair validated; runtime activity is still unverified.",
                  "repair_suggestion": ""}
        session.append("repair_validated", json.dumps(reconciled, ensure_ascii=False))
    elif validated_repair is not None:
        result = {"action": "hold" if blocker else "resume",
                  "reason": blocker or "Existing SDK repair receipt matches this failure, checkpoint and installed source; request bounded managed resume.",
                  "diagnosis": "already_fixed: independently validated repair; runtime activity remains unverified.",
                  "repair_suggestion": ""}
        session.append("repair_validated", json.dumps(validated_repair, ensure_ascii=False))
    elif readonly_provider_probe_only and (target is not None or blocker is not None):
        # Another Worker is live: shared Python edits and budget/contract
        # bypasses remain prohibited, even during an isolated review.
        result = {"action": "hold",
                  "reason": "Exclusive engineering window or frozen resume guard required.",
                  "diagnosis": str(blocker or "Attributable engine traceback")[:700],
                  "repair_suggestion": ""}
        session.append("system", result["reason"])
    elif target is not None:
        result = attempt_runtime_repair(
            root=research_root, cycle_dir=cycle_dir, state=state,
            state_path=state_path, session=session, model=model,
            incident_id=incident_id, **({"log_path": log_path} if log_path is not None else {}),
        )
    elif blocker is not None:
        # A blocked plain resume can still be a repairable engineering fault.
        # Distinguish engineering work from unsafe scientific/ownership replay.
        suggestion = ""
        if blocker.startswith("Provider configuration"):
            suggestion = ("Inspect the task model route, supported tier and provider configuration; "
                          "repair only authorized configuration. Never rewrite credentials or scientific state.")
        elif blocker.startswith("Protocol/contract violation"):
            suggestion = ("Inspect and fix the attributable engine protocol/contract implementation "
                          "in a guarded engineering workspace; validate before any checkpoint resume.")
        elif blocker.startswith("Runtime/budget validation"):
            suggestion = ("Repair the attributable runtime or budget validation failure; run its "
                          "objective validator and return evidence to the SDK before requesting resume.")
        elif blocker.startswith("The ") and "authorized Supervisor runtime resumes" in blocker:
            suggestion = ("Investigate recurring failure after bounded resumes; repair the cause and "
                          "verify its change instead of forcing another plain resume or resetting guards.")
        result = {"action": "hold", "reason": blocker, "diagnosis": "",
                  "repair_suggestion": suggestion}
        session.append("system", "确定性 hold，未调用模型。" + blocker)
    elif _recovered_capacity_thread_is_idle(research_root, cycle_dir, state):
        result = {"action": "resume",
                  "reason": "The previously capacity-stuck Investigator thread is now confirmed idle in its pinned Worker Codex home; request one bounded managed resume from the unchanged safe checkpoint.",
                  "diagnosis": "Live provider thread status is idle; no Goal was cleared, interrupted or restarted by the probe.",
                  "repair_suggestion": ""}
        session.append("system", result["reason"])
    elif readonly_provider_probe_only:
        # No blind retry on a busy/unobservable native model thread. This
        # path is read-only and never invokes a Supervisor LLM.
        result = {"action": "hold",
                  "reason": "Pinned provider thread not positively verified idle; engineering deferred until peer-safe window.",
                  "diagnosis": "", "repair_suggestion": ""}
        session.append("system", result["reason"])
    elif backend is None:
        result = {"action": "hold", "reason": "No SDK backend is available for the Runtime Supervisor.",
                  "diagnosis": "", "repair_suggestion": ""}
        session.append("system", result["reason"])
    else:
        context = {
            "incident_evidence": evidence,
            "phase": state.get("phase"), "error_kind": recovery.get("kind"),
            "failure_category": recovery.get("category"),
            "budget_reconciliation": state.get("budget_failure_reconciliation"),
            "error_message": _brief(message, 3000),
            "protocol_health": state.get("protocol_health"),
            "resumable": bool(state.get("resumable")),
            "last_safe_checkpoint": state.get("last_safe_checkpoint"),
            "committed_roles": sorted((state.get("role_outputs") or {}).keys()),
            "runner_log_tail": _log_tail(research_root, log_path=log_path),
            "cycle_dir": str(cycle_dir),
        }
        prompt = (
            "You are the single TheoryMaster Supervisor operating its read-only runtime triage skill. "
            "For recoverable provider faults the SDK performs guarded thread/worker checks and resume; "
            "for faults needing engineering edits, delegate internally to the same Supervisor engineering repair skill. Do not instruct the human to patch code. "
            "A Cycle stopped with runtime FAILED. Decide whether a plain managed "
            "resume from the existing checkpoint is likely to succeed WITHOUT any "
            "code, config or data change. You may inspect files in cycle_dir and the "
            "task with read-only tools. Do not edit files, launch tasks, or make any "
            "scientific judgement. Return strict JSON {action:'resume'|'hold', "
            "reason:string, diagnosis:string, repair_suggestion:string}. Choose hold "
            "for deterministic bugs, protocol/contract violations, corrupted state or "
            "uncertainty, and describe a concrete engineering handoff for the empowered Incident Supervisor in repair_suggestion. "
            "Prioritize the thread-scoped terminal provider errors and their "
            "ordering over stale Investigator public scientific messages. "
            "A confirmed transient model connection failure is operational, NOT "
            "proof of scientific block. Prefer a bounded managed resume when "
            "the provider interruption is plausibly transient and a safe "
            "checkpoint is intact; hold on deterministic configuration, "
            "authorization, contract or corrupted-state faults. A scientific "
            "blocked with no provider error must not be bypassed. "
            "Authorization to resume does not certify success: "
            "the Runner must actually restart at its safe checkpoint and produce "
            "new role/model activity before the incident may be marked recovered. "
            "A ValueError class name alone is not evidence of an Agent protocol violation. "
            "Use the failure category, original event and current validator results. "
            "Distinguish diagnosis, applied repair, validated repair and verified resumed activity; "
            "an active incident session or repair suggestion is not an executing repair job. "
            "Respect the bounded retry budget; do not modify hypotheses, data, "
            "scientific contracts or budget guards.\nContext:\n"
            + json.dumps(context, ensure_ascii=False)
        )
        session.append("prompt", prompt, role="runtime_supervisor")
        thread = None
        try:
            invoke = getattr(backend, "invoke_runtime_review", None)
            if callable(invoke):
                thread, raw = invoke(prompt, model, DEFAULT_PROVIDER)
            else:
                thread, raw = backend.invoke("runtime_supervisor", prompt, None, model, DEFAULT_PROVIDER)
            session.append("model_output", str(raw), thread_id=str(thread or ""))
            from .supervisor_incident_session import capture_turn_reads
            tool_reads = capture_turn_reads(backend)
            model_invoked = True
            result = _parse(raw)
        except Exception as exc:
            from .supervisor_incident_session import capture_turn_reads
            if not tool_reads:
                tool_reads = capture_turn_reads(backend)
            result = {"action": "hold", "reason": (
                f"Runtime Supervisor review failed safely ({type(exc).__name__}: {_brief(exc, 400)})."
            ), "diagnosis": "", "repair_suggestion": ""}
            session.append("system", result["reason"])
        finally:
            drop = getattr(backend, "drop_thread", None)
            if thread and callable(drop):
                try:
                    drop(thread)
                except Exception:
                    pass
    if result["action"] == "resume":
        # Even the isolated repair path must not waive an independent protocol
        # violation or an operator/budget guard. Re-read SDK receipt evidence.
        from .supervisor_repair_receipts import inspect_repair_receipt
        try:
            validated_repair = inspect_repair_receipt(cycle_dir, state)
        except (OSError, ValueError, TypeError, KeyError):
            validated_repair = None
        final_blocker = resume_blocker(state, budget_revalidated=reconciled is not None,
                                      runtime_revalidated=validated_repair is not None)
        if final_blocker:
            result = {"action": "hold", "reason": final_blocker,
                      "diagnosis": result.get("diagnosis", ""), "repair_suggestion": ""}
    if result["action"] == "resume":
        if validated_repair:
            from .supervisor_repair_receipts import repair_credit_key
            state.setdefault('supervisor_consumed_repairs', []).append(repair_credit_key(state))
        state[_COUNT_KEY] = int(state.get(_COUNT_KEY) or 0) + 1
        state["supervisor_runtime_review"] = {"at": now(), "session_id": session.session_id,
                                              "action": "resume", "policy_version": POLICY_VERSION}
        session.append("repair_binding", "Repair evidence authorized for this managed resume.",
                       repair_receipt=state.get("supervisor_repair_receipt") if validated_repair else None,
                       budget_reconciliation=state.get("budget_failure_reconciliation") if reconciled else None)
        write_json(state_path, state)
    reason = result["reason"]
    if result.get("diagnosis"):
        reason += "\n\n诊断：" + result["diagnosis"]
    if result.get("repair_suggestion"):
        reason += "\n\n建议修复：" + result["repair_suggestion"]
    session.close("returned" if result["action"] == "resume" else "hold",
                  action=result["action"], reason=reason)
    _publish_runtime_incident(
        research_root, cycle_dir, state, incident_id, session.session_id,
        reason, model_invoked, tool_reads, hold=result["action"] != "resume",
    )
    return dict(result, session_id=session.session_id)


def _publish_runtime_incident(
    research_root: Path, cycle_dir: Path, state: dict[str, Any], incident_id: str,
    session_id: str, reason: str, model_invoked: bool, tool_reads: list[str], *, hold: bool,
) -> None:
    """Record the existing review in the incident dialogue. Decision stays above."""
    try:
        from .supervisor_incident_session import publish_review_incident
        violation = state.get("protocol_health") == "violation"
        publish_review_incident(
            research_root=research_root, cycle_dir=cycle_dir, state=state,
            trigger_type="protocol_violation" if violation else (
                "hold" if hold else "runtime_failure"),
            trigger_event="protocol_health=violation" if violation else "run_failed",
            analysis=reason if model_invoked else None,
            model_invoked=model_invoked,
            system_note=None if model_invoked else reason,
            tool_reads=tool_reads,
            audit={"supervisor_session_id": session_id, "incident_id": incident_id,
                   "source": "runtime_review"},
        )
    except Exception as exc:
        print(f"TheoryMaster incident session was not opened: {type(exc).__name__}: {str(exc)[:200]}",
              file=__import__("sys").stderr)
