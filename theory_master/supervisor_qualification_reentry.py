"""Resume the same pre-lock Cycle after an evidenced Supervisor reply defect."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .supervisor_intervention import SESSION_SCHEMA

VALIDATOR = "supervisor-qualification-reply-reentry/v1"
_FAILURE_PREFIX = "Supervisor qualification review output was invalid: "
_ENVELOPE_ERROR = _FAILURE_PREFIX + "Supervisor action or required fields are invalid"
_DUPLICATE_TRANSITION_ERROR = "Duplicate Supervisor decision key: transition"
_MISSING_LINEAGE_ERROR = _FAILURE_PREFIX + "new frontier proposal requires lineage_main_node_id and optional lineage_other_node_ids"
_ORIGINAL_CANDIDATE_ERRORS = {_ENVELOPE_ERROR, _MISSING_LINEAGE_ERROR}


def is_reply_failure(state: dict) -> bool:
    attention = state.get("attention") or {}
    return (state.get("status") == "needs_attention"
            and state.get("phase") == "supervisor_qualification_review"
            and isinstance(attention, dict)
            and attention.get("code") == "supervisor_qualification_review_failed"
            and (str(attention.get("reason") or "").startswith(_FAILURE_PREFIX + "Extra data:")
                 or attention.get("reason") in _ORIGINAL_CANDIDATE_ERRORS))


def _has_control(cycle: Path) -> bool:
    control = cycle / "recovery_control.json"
    return control.exists() or control.is_symlink()



def is_untouched_envelope_failure(cycle: Path, state: dict) -> bool:
    """Recognize the before-first-call boundary, independent of a repair proof."""
    frontier = state.get("frontier_review") or {}
    return (is_reply_failure(state) and state["attention"]["reason"] in _ORIGINAL_CANDIDATE_ERRORS
            and str(state.get("cycle_id")) == cycle.name and state.get("resumable") is True
            and state.get("protocol_health") == "valid" and state.get("qualification_status") == "pending"
            and state.get("hypothesis_lock") == {"status": "unlocked"} and not _has_control(cycle)
            and not any(state.get(key) for key in (
                "abandoned_cycle", "execution_active", "active_invocation_id", "last_invocation_id",
                "last_safe_checkpoint", "hypothesis_locked", "hypothesis_id", "native_goal",
                "role_outputs", "branches", "candidates", "qualification_working_set",
                "scientific_verdict", "goal_completion", "supervisor_qualification",
                "human_direction", "operator_guidance_pending",
            ))
            and frontier.get("status") == "pending"
            and not any(frontier.get(key) for key in ("generated_candidates", "selected_candidate_id", "reviews")))


def _envelope_proof(cycle: Path, state: dict) -> tuple[dict, dict] | None:
    from .goal_terminal import assert_goal_open
    from .invocations import stable_hash
    from .prequalification_holdout import holdout_context, holdout_files
    from .public_submission import read_bounded_regular_file
    from .research_inputs import verified_research_inputs
    from .science_graph_store import ScienceGraphStore
    from .scientific_model import load as load_scientific_model
    from .specification import TaskSpec
    from .supervisor_qualification_recovery import (
        DUPLICATE_TRANSITION_EDIT, _decision, _stop_evidence, normalize_candidate_envelope, parse_review_object,
    )
    from .task_progression import local_replan_reason
    from .worker_direction import research_direction_fingerprint
    from .worker_store import _graph_path

    if not is_untouched_envelope_failure(cycle, state):
        return None
    reason = state["attention"]["reason"]
    duplicate_transition = reason == _MISSING_LINEAGE_ERROR
    ledger = cycle / "invocations.jsonl"
    if ledger.is_symlink() or (ledger.exists() and read_bounded_regular_file(ledger, 4_000_000).strip()):
        return None
    trace = read_bounded_regular_file(cycle / "supervisor" / "session.jsonl", 4_000_000)
    attempts = [row for line in trace.splitlines() if line.strip()
                if (row := json.loads(line)).get("schema") == SESSION_SCHEMA
                and row.get("profile") == "attention" and row.get("code") == "no_qualified_candidate"]
    if not attempts:
        return None
    session = attempts[-1].get("session_id")
    rows = [row for row in attempts if row.get("session_id") == session]
    steps = [row for row in rows if row.get("kind") != "normalization"]
    if (not session or [row.get("kind") for row in steps] !=
            ["opened", "prompt", "model_output", "prompt", "model_output", "decision"]
            or any(a["seq"] >= b["seq"] for a, b in zip(rows, rows[1:]))
            or steps[3].get("input_kind") != "contract_repair"
            or steps[-1].get("action") != "hold" or steps[-1].get("content") != reason):
        return None
    original_prompt = steps[1].get("content") or ""
    first_error = _DUPLICATE_TRANSITION_ERROR if duplicate_transition else _ENVELOPE_ERROR.removeprefix(_FAILURE_PREFIX)
    repair_error = "\nThe prior response failed validation. Error: " + first_error + "\n"
    if not str(steps[3].get("content") or "").startswith(original_prompt + repair_error):
        return None
    context = json.loads(original_prompt.partition("\nTask evidence:\n")[2])
    if not isinstance(context, dict) or not isinstance(context.get("qualification_stop"), dict):
        return None
    responses = [steps[2], steps[4]]
    if any(not isinstance(row.get("content"), str) or not row.get("thread_id") for row in responses):
        return None
    if duplicate_transition:
        edits: list[str] = []
        try:
            value, removed_closer = parse_review_object(responses[0]["content"], edits=edits)
            _, moved = normalize_candidate_envelope(value)
        except ValueError:
            return None
        if edits != [DUPLICATE_TRANSITION_EDIT] or removed_closer or moved:
            return None
        try:
            _decision(responses[1]["content"], context, state)
        except (ValueError, TypeError) as error:
            if _FAILURE_PREFIX + str(error) != reason:
                return None
        else:
            return None
        selected = _decision(responses[0]["content"], context, state)
        if selected["status"] != "proposed":
            return None
    else:
        normalized = []
        for response in responses:
            value, _ = parse_review_object(response["content"])
            repaired, moved = normalize_candidate_envelope(value)
            if not moved:
                return None
            proposal = _decision(json.dumps(repaired, ensure_ascii=False), context, state)
            if proposal.get("status") != "proposed":
                return None
            normalized.append(proposal)
        # Preserve the first authored proposal when later repair text changed it.
        selected = normalized[0]

    root = cycle.parent
    source = str(context["qualification_stop"].get("cycle_id") or "")
    if not source.isascii() or not source.isdecimal() or not 0 < int(source) < int(cycle.name):
        return None
    prior = json.loads(read_bounded_regular_file(root / source / "research_state.json", 2_000_000))
    if (prior.get("worker_id") != state.get("worker_id")
            or local_replan_reason(prior) != "no_qualified_candidate" or _stop_evidence(prior) != context["qualification_stop"]):
        return None
    spec = TaskSpec.from_task_root(root)
    graph = _graph_path(spec)
    assert_goal_open(root, graph, spec=spec)
    with ScienceGraphStore(graph, readonly=True) as store:
        if store.agent_view() != context.get("agent_graph"):
            return None
    if any(context.get(key) != getattr(spec, field) for key, field in (
            ("goal", "goal"), ("research_contract", "contract"),
            ("final_gate", "final_gate"), ("required_context", "required_context"))):
        return None
    fingerprint = research_direction_fingerprint(spec)
    if (fingerprint["scientific_model_version"] != state.get("scientific_model_version_in")
            or load_scientific_model(root, goal_id=spec.goal_id) != context.get("scientific_model")):
        return None
    holdout = holdout_files(spec.config_path)
    if (holdout_context(holdout) if holdout else None) != context.get("prequalification_holdout"):
        return None
    priority = frozenset(context.get("newly_verified_input_sha256") or [])
    inventory = verified_research_inputs(spec, include_text=True,
        excluded_sha256=frozenset(file.sha256 for file in holdout), prioritize_sha256=priority)
    if inventory != context.get("verified_public_sources"):
        return None

    evidence = {
        "mode": "original_candidate_duplicate_transition" if duplicate_transition else "original_candidate_envelope",
        "session_id": session,
        "source_cycle": source,
        "thread_id": steps[2]["thread_id"], "trace_sha256": stable_hash(rows),
        "original_response_sha256": hashlib.sha256(steps[2]["content"].encode()).hexdigest(),
        "original_selection_sha256": stable_hash(selected),
        "direction_fingerprint": fingerprint, "prioritized_input_sha256": sorted(priority),
    }
    return evidence, selected

def _legacy_evidence(cycle: Path, state: dict) -> dict | None:
    from .supervisor_qualification_recovery import parse_review_object

    trace = cycle / "supervisor" / "session.jsonl"
    if any(part.is_symlink() for part in (trace, trace.parent, cycle)) or not trace.is_file():
        return None
    with trace.open("rb") as handle:
        content = handle.read(4_000_001)
    if len(content) > 4_000_000:
        return None
    attempts = [row for raw in content.splitlines() if raw.strip()
                if isinstance((row := json.loads(raw)), dict) and row.get("schema") == SESSION_SCHEMA
                and row.get("profile") == "attention" and row.get("code") == "no_qualified_candidate"]
    if not attempts:
        return None
    session = attempts[-1].get("session_id")
    rows = [row for row in attempts if row.get("session_id") == session]
    reason = state["attention"]["reason"]
    if (not session or [row.get("kind") for row in rows] !=
            ["opened", "prompt", "model_output", "prompt", "model_output", "decision"]
            or any(a["seq"] >= b["seq"] for a, b in zip(rows, rows[1:]))
            or rows[3].get("input_kind") != "contract_repair"
            or rows[-1].get("action") != "hold" or rows[-1].get("content") != reason):
        return None
    response = rows[4]
    reply = response.get("content")
    if not isinstance(reply, str) or not response.get("thread_id"):
        return None
    try:
        json.loads(reply.strip())
    except json.JSONDecodeError as error:
        if _FAILURE_PREFIX + str(error) != reason:
            return None
    else:
        return None
    value, repaired = parse_review_object(reply)
    if not repaired or value.get("action") not in {"select_candidate", "wait_for_dependency", "materialize_delivery"}:
        return None
    # A fresh review uses current authority and the ordinary candidate validator.
    # The receipt never selects the historical response.
    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"session_id": session, "thread_id": response["thread_id"],
            "trace_sha256": hashlib.sha256(encoded).hexdigest()}


def _evidence(cycle: Path, state: dict) -> dict | None:
    if (state.get("attention") or {}).get("reason") in _ORIGINAL_CANDIDATE_ERRORS:
        result = _envelope_proof(cycle, state)
        return result[0] if result else None
    return _legacy_evidence(cycle, state)


def validate_qualification_reply_reentry(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import _digest, _record_validation, source_manifest

    if (not is_reply_failure(state) or state.get("execution_active") or state.get("active_invocation_id")
            or state.get("hypothesis_locked") or state.get("hypothesis_lock") != {"status": "unlocked"}
            or state.get("scientific_verdict") is not None or state.get("native_goal")
            or state.get("protocol_health") != "valid"
            or not (state.get("last_safe_checkpoint") or is_untouched_envelope_failure(cycle, state))
            or _has_control(cycle) or resume_blocker(state, for_repair=True, repair_cycle=cycle)):
        return None
    source = source_manifest(engine)
    evidence = _evidence(cycle, state)
    if (not evidence or evidence["session_id"] in (state.get("supervisor_qualification_reentries") or [])
            or source_manifest(engine) != source):
        return None
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module="supervisor_qualification_recovery.py", change_id="existing-source:" + _digest(source),
        tests=json.dumps(evidence, sort_keys=True), manifest=source, disposition="already_fixed")


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    cycle = Path(receipt["cycle"])
    if (not is_reply_failure(state) or _has_control(cycle)
            or json.loads(receipt["tests"]) != _evidence(cycle, state)):
        raise ValueError("Supervisor qualification response evidence changed")


def consume_qualification_reply_reentry(cycle: Path, state: dict) -> None:
    if not is_reply_failure(state):
        return
    from .runtime import now
    from .supervisor_repair_receipts import inspect_repair_receipt, repair_credit_key

    receipt = inspect_repair_receipt(cycle, state)
    if receipt["validator"] != VALIDATOR:
        raise ValueError("Supervisor qualification response needs its verified repair receipt")
    proof = json.loads(receipt["tests"])
    session_id = proof["session_id"]
    consumed = state.setdefault("supervisor_qualification_reentries", [])
    if session_id in consumed:
        raise ValueError("Supervisor qualification response reentry was already consumed")
    if proof.get("mode") in {"original_candidate_envelope", "original_candidate_duplicate_transition"}:
        from .supervisor_qualification_recovery import DELIVERY_SELECTION_VERSION, SOURCE_CONTEXT_VERSION

        if repair_credit_key(state) not in (state.get("supervisor_consumed_repairs") or []):
            raise ValueError("Envelope recovery requires the authorized Supervisor retry")
        match = _envelope_proof(cycle, state)
        if match is None or match[0] != proof:
            raise ValueError("Original Supervisor candidate boundary changed")
        _, original = match
        state["supervisor_qualification"] = {
            **original, "session_id": session_id, "thread_id": proof["thread_id"],
            "source_cycle": proof["source_cycle"], "direction_fingerprint": proof["direction_fingerprint"],
            "delivery_review_available": False, "delivery_selection_version": DELIVERY_SELECTION_VERSION,
            "source_context_version": SOURCE_CONTEXT_VERSION,
            "prioritized_input_sha256": proof["prioritized_input_sha256"],
            "recovered_original_response_sha256": proof["original_response_sha256"],
        }
        state.setdefault("role_outputs", {})["research_lead_frontier"] = original["frontier"]
    consumed.append(session_id)
    state.setdefault("supervisor_qualification_reentry_audit", []).append({
        "at": now(), "session_id": session_id, "original_attention": state.pop("attention"),
        "repair_receipt": state["supervisor_repair_receipt"],
    })
    state.pop("human_attention", None)
