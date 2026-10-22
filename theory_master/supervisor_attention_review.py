"""Supervisor-owned SDK Attention decisions (scientific blocking and qualification).

Supervisor retains legacy continue/preserve/hold authority for previously
persisted native blocked Investigator Goals. New unexpected blocked events
are operational interruptions, not valid scientific stop requests; they
receive deterministic hold with no extra model call or preservation authority. This is an in-process Supervisor attention-profile
model review, NOT the legacy external Attention Takeover agent.

THEORY_MASTER_SUPERVISOR gates this review. Legacy Attention names are only
compatibility aliases. Keep the historic SDK receipts/schema readable.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .investigator_budget import BUDGET_ATTENTION_CODE
from .observability.incident_registry import register_cycle_incident, record_repair_handoff
from .runtime import write_json, now
from .supervisor_intervention import SupervisorSession, supervisor_model

REVIEW_MODEL = "gpt-6-luna"
REVIEW_PROVIDER = "CodexHTTPS"
POLICY_VERSION = 5
MAX_SCIENTIFIC_CONTINUES = 1
TECHNICAL_BUDGET_REVIEW_CODES = frozenset({
    "investigator_budget_review_failed",
    "investigator_budget_supervisor_review_failed",
})
HARD_CODES = frozenset({
    BUDGET_ATTENTION_CODE, "investigator_budget_review_failed",
    "investigator_budget_review_rejected",
    "investigator_budget_cap_reached", "investigator_budget_supervisor_declined",
    "investigator_budget_supervisor_review_required",
    "investigator_budget_supervisor_review_failed",
    "cycle_cost_budget_exhausted", "cycle_token_budget_exhausted",
    "investigator_internal_turn_limit", "investigation_round_budget_exhausted",
    "runtime_failure", "supervisor_unrecoverable",
    "provider_model_capacity_timeout",
    "investigator_native_goal_unexpected_blocked",
})
MAX_QUALIFICATION_RETRIES = 1


def _brief(value: Any, limit: int = 1700) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_response(raw: str) -> dict[str, str]:
    value = str(raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", value, flags=re.I).strip()
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("internal Attention reviewer must return strict JSON") from exc
    if not isinstance(result, dict) or result.get("action") not in {"retry_qualification", "continue", "preserve", "hold"}:
        raise ValueError("Supervisor action must be retry_qualification, continue, preserve or hold")
    if not isinstance(result.get("reason"), str) or len(result["reason"].strip()) < 20:
        raise ValueError("internal Attention reason is missing")
    guidance = result.get("guidance") or ""
    if not isinstance(guidance, str) or (result["action"] in {"retry_qualification", "continue"} and len(guidance.strip()) < 35):
        raise ValueError("internal Attention guidance is invalid")
    return {"action": result["action"], "reason": result["reason"].strip(),
            "guidance": guidance.strip()[:4000]}


def review_supervisor_attention(
    *, backend: Any, cycle_dir: Path, state: dict[str, Any],
    goal: str, research_contract: str, final_gate: str,
) -> dict[str, str]:
    """Run one bounded Supervisor review at a persisted NEEDS_ATTENTION boundary.

    This entrypoint has no control over frozen science. A blocked Investigator
    Goal may result in continue, preserve or hold only after Supervisor checks.
    """
    selected_model = supervisor_model(cycle_dir.parent)
    attention = state.get("attention") or {}
    code = str(attention.get("code") or "needs_attention")
    allowed_retry = (
        code == "no_qualified_candidate"
        and str((state.get("hypothesis_lock") or {}).get("status") or "unlocked") != "locked"
        and int(state.get("internal_attention_retry_count") or 0) < MAX_QUALIFICATION_RETRIES
    )
    from .supervisor_intervention import supervisor_enabled
    blocked_science = (
        supervisor_enabled(cycle_dir.parent)
        and code == "investigator_native_goal_blocked"
        and str((state.get("hypothesis_lock") or {}).get("status") or "") == "locked"
        and (attention.get("goal_status") or "blocked") == "blocked"
        and bool(state.get("resumable"))
        and not bool(state.get("execution_active"))
        and bool(state.get("last_safe_checkpoint"))
        and not bool((state.get("investigator_budget") or {}).get("exhausted"))
    )
    can_continue = (blocked_science and
        int(state.get("supervisor_scientific_continue_count") or 0) < MAX_SCIENTIFIC_CONTINUES)

    incident, _ = register_cycle_incident(
        cycle_dir, severity="attention", kind="needs_attention",
        source_event="sdk.internal_attention", code=code,
        summary=_brief(attention.get("reason"), 1100),
        discriminator=f"{state.get('phase')}:{code}",
        fields={"phase": state.get("phase"), "resumable": bool(state.get("resumable"))},
    )
    session = SupervisorSession(
        cycle_dir, profile="attention", incident_id=str(incident["incident_id"]), code=code,
        summary=_brief(attention.get("reason"), 1100), model=selected_model,
    )
    # A hard guard can suppress model invocation, but its source-backed
    # diagnostic evidence must still be visible to the operator.
    from .supervisor_incident import incident_context
    evidence = incident_context(cycle_dir, state)
    session.append("evidence", json.dumps(evidence, ensure_ascii=False)[:11000])
    model_invoked = False
    tool_reads: list[str] = []
    captured_exchange: dict[str, str] = {}
    if code in HARD_CODES or (code == "no_qualified_candidate" and not allowed_retry) or (code == "investigator_native_goal_blocked" and not blocked_science):
        result = {
            "action": "hold", "reason": (
                "Unexpected native Goal blocked: no Investigator scientific stop authority; "
                "retain the checkpoint for operational recovery without preservation."
                if code == "investigator_native_goal_unexpected_blocked" else
                "Budget Reviewer technical delivery or contract failure: SDK exhausted "
                "its bounded retries without a valid approval/rejection. Preserve the "
                "scientific checkpoint and budget ledger; recheck provider and safely "
                "resume the exact Cycle when a Reviewer response becomes available."
                if code in TECHNICAL_BUDGET_REVIEW_CODES else
                "Hard resource/runtime guard requires an objective state change; "
                "the SDK cannot bypass this stop or allocate new resources."
                if code in HARD_CODES else
                "The single SDK-internal qualification retry was used. "
                "Further retries require a new operator-directed change."
            ), "guidance": "",
        }
        from .supervisor_recovery import OPERATIONAL_ATTENTION
        if code in OPERATIONAL_ATTENTION:
            result['next_executor'] = 'sdk:supervisor_engineering'
            result['recovery_scope'] = 'operational_only'
            result['reason'] += ' Runtime owner will diagnose or wait with a recorded dependency after this Worker exits.'
        session.append("system", (
            "硬 guard：确定性 hold，未调用模型。" if code in HARD_CODES else
            "同 Cycle 的唯一一次资格重提已用完，未再调用模型。"
        ) + " " + result["reason"])
    else:
        reviews = (state.get("frontier_review") or {}).get("reviews") or []
        candidates = (state.get("frontier_review") or {}).get("generated_candidates") or []
        context = {
            "goal": _brief(goal), "research_contract": _brief(research_contract, 2400),
            "final_gate": _brief(final_gate), "attention_code": code,
            "incident_evidence": evidence,
            "attention_reason": _brief(attention.get("reason"), 2000),
            "candidate_reviews": [
                {"verdict": _brief(x.get("verdict"), 35), "reason": _brief(x.get("reason"), 1000)}
                for x in reviews[-3:] if isinstance(x, dict)
            ],
            "rejected_hypotheses": [
                _brief(x.get("hypothesis"), 800) for x in candidates[-3:] if isinstance(x, dict)
            ],
            "allowed_action": (
                "retry_qualification once; or hold" if allowed_retry else
                "continue once, preserve, or hold" if can_continue else
                "preserve or hold" if blocked_science else "hold only"
            ),
        }
        prompt = (
            "You are an SDK-internal, read-only TheoryMaster operational reviewer. "
            "You are NOT Research Lead, Challenger, Judge, or a code-patching Agent. "
            "If this is a qualification rejection, assess whether one fresh "
            "Research Lead frontier proposal addressing the scientific defects "
            "is useful. For other Attention codes, diagnose the recorded blocker "
            "without pretending that a code repair or scientific decision happened. "
            "When code is investigator_native_goal_blocked, YOU ARE THE EXISTING "
            "SUPERVISOR (not a new Stop Reviewer). Decide continue if you can "
            "identify a specific, feasible, previously untried in-scope scientific "
            "action with discriminating value; give concrete guidance. Otherwise "
            "preserve existing work if genuine scientific blockage is documented; "
            "do not claim the hypothesis rejected, falsified or inconclusive. "
            "Hold on ambiguous provenance, quota, provider or runtime errors. "
            "Return strict JSON {action:'retry_qualification'|'continue'|'preserve'|'hold', "
            "reason:string, guidance:string}. For retry, guidance must ask Lead to compare truly "
            "competitive Goal-aligned directions under the ORIGINAL Research Contract; "
            "it is not a binding scientific hypothesis, new allowed input, or Judge decision. "
            "Do not edit files or launch tasks. Hold whenever this code is not "
            "qualified for an SDK-authorized retry or continuation is unsafe. "
            "Treat provenance-backed recent operational events as separate from "
            "Investigator's public scientific claims. A terminal LLM_FAILED may "
            "indicate provider failure; never call that a scientific obstruction "
            "without checking event ordering. Diagnose concrete evidence gaps "
            "and whether a safe runtime review is needed, but do not grant "
            "unlisted actions, override frozen science or pretend repair succeeded. "
            "Use only context, not extra literature, computations or tools.\nContext:\n"
            + json.dumps(context, ensure_ascii=False)
        )
        thread: str | None = None
        native_snapshot: dict[str, Any] = {"status": "unavailable"}
        snapshot_name = f"reviewer_visible_thread_{uuid.uuid4().hex}.jsonl"
        session.append("prompt", prompt, role="attention_reviewer")
        try:
            invoke = getattr(backend, "invoke_attention_review", None)
            if callable(invoke):
                thread, raw = invoke(prompt, selected_model, REVIEW_PROVIDER)
            else:
                thread, raw = backend.invoke("attention_reviewer", prompt, None, selected_model, REVIEW_PROVIDER)
            # Save only the actual SDK-visible user Prompt and model reply.
            # No synthetic tools or private model reasoning are recorded.
            captured_exchange = {"prompt": prompt, "model_output": str(raw),
                                 "thread_id": str(thread or "")}
            session.append("model_output", str(raw), thread_id=str(thread or ""))
            from .supervisor_incident_session import capture_turn_reads
            tool_reads = capture_turn_reads(backend)
            model_invoked = True
            result = _parse_response(raw)
        except Exception as exc:
            result = {"action": "hold", "reason": (
                f"Internal Attention review failed safely ({type(exc).__name__}: {_brief(exc, 400)}). "
                "No automatic scientific or resource override was made."
            ), "guidance": ""}
            session.append("system", result["reason"])
        finally:
            if thread:
                # Preserve provider-visible tools before dropping this fresh
                # Reviewer thread. Archive failures do not change decisions.
                try:
                    snapshot = getattr(backend, "snapshot_budget_review_thread", None)
                    if callable(snapshot):
                        native_snapshot = snapshot(thread, cycle_dir / "attention" / snapshot_name)
                except Exception as exc:
                    native_snapshot = {"status": "unavailable", "reason": type(exc).__name__}
                if captured_exchange:
                    captured_exchange["visible_thread"] = {
                        "status": native_snapshot.get("status", "unavailable"),
                        "file": snapshot_name if native_snapshot.get("status") == "available" else None,
                        "archive_truncated": bool(native_snapshot.get("archive_truncated", False)),
                        "excluded_private_items": native_snapshot.get("excluded_private_items", 0),
                    }
                drop = getattr(backend, "drop_thread", None)
                if callable(drop):
                    try:
                        drop(thread)
                    except Exception:
                        pass
    if result["action"] in {"continue", "preserve"} and not blocked_science:
        result = {"action": "hold", "reason": "Supervisor scientific exit is forbidden for this Attention state.", "guidance": ""}
    if result["action"] == "continue" and not can_continue:
        result = {"action": "hold", "reason": "Bounded Supervisor scientific continuation has been exhausted.", "guidance": ""}
    if not allowed_retry and result["action"] == "retry_qualification":
        result = {"action": "hold", "reason": "Retry is forbidden for this Attention code.", "guidance": ""}
        session.append("system", "SDK 拒绝模型给出的重提：该 Attention code 不允许重提。")
    retry = result["action"] == "retry_qualification"
    session.close(
        "returned" if retry or result["action"] in {"continue", "preserve"} else "hold", action=result["action"],
        reason=(result["reason"] + ("\n\nSupervisor guidance:\n" + result["guidance"] if result["action"] in {"continue", "retry_qualification"} else "")),
    )
    # Separate invocations must not overwrite earlier Reviewer receipts.
    review_path = cycle_dir / "attention" / (
        f"sdk_internal_review_{int(state.get('internal_attention_retry_count') or 0)+1:02d}_{uuid.uuid4().hex}.json"
    )
    record_repair_handoff(
        cycle_dir, incident=incident,
        status="retry_authorized" if result["action"] in {"retry_qualification", "continue", "preserve"} else "blocked",
        target="sdk:internal_attention",
        error=None if result["action"] == "retry_qualification" else result["reason"],
        policy_version=POLICY_VERSION, review_ref=review_path.name,
    )
    write_json(review_path, {
        "schema": "theory-master/internal-attention-review/v1", "at": now(),
        "attention_code": code, "policy_version": POLICY_VERSION,
        "decision_authority": "supervisor",
        "review_scope": "scientific_block" if code == "investigator_native_goal_blocked" else "attention",
        "model": selected_model if captured_exchange else None,
        "incident_id": incident["incident_id"], "supervisor_session": session.session_id, **result,
        **({"exchange": captured_exchange} if captured_exchange else {}),
    })
    prose = result["reason"]
    if result["action"] in {"continue", "retry_qualification"} and result.get("guidance"):
        prose += "\n\nSupervisor guidance:\n" + result["guidance"]
    _publish_attention_incident(
        cycle_dir, state, code, str(incident["incident_id"]), session.session_id,
        prose, model_invoked, tool_reads, action=result["action"],
    )
    return dict(result, session_id=session.session_id)


def _publish_attention_incident(
    cycle_dir: Path, state: dict[str, Any], code: str, incident_id: str, session_id: str,
    prose: str, model_invoked: bool, tool_reads: list[str], *, action: str,
) -> None:
    """Record the existing Attention review. continue/preserve/hold logic stays above."""
    try:
        from .supervisor_incident_session import publish_review_incident
        violation = state.get("protocol_health") == "violation"
        if violation:
            trigger_type, trigger_event = "protocol_violation", "protocol_health=violation"
        elif action == "hold":
            trigger_type, trigger_event = "hold", code
        else:
            trigger_type, trigger_event = "needs_attention", code
        publish_review_incident(
            research_root=Path(cycle_dir).parent, cycle_dir=cycle_dir, state=state,
            trigger_type=trigger_type, trigger_event=trigger_event,
            analysis=prose if model_invoked else None,
            model_invoked=model_invoked,
            system_note=None if model_invoked else prose,
            tool_reads=tool_reads,
            audit={"supervisor_session_id": session_id, "incident_id": incident_id,
                   "source": "attention_review"},
        )
    except Exception as exc:
        import sys
        print(f"TheoryMaster incident session was not opened: {type(exc).__name__}: {str(exc)[:200]}",
              file=sys.stderr)
