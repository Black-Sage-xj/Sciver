"""One evidence-based SPV selection per unchanged qualification failure."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contract_closure import close_agent_output
from .contracts import role_contract
from .goal_terminal import assert_goal_open
from .invocations import stable_hash
from .research_inputs import verified_research_inputs
from .science_graph_store import ScienceGraphStore
from .specification import cycle_number, list_cycle_dirs
from .supervisor_intervention import (
    DEFAULT_PROVIDER, SupervisorSession, mark_returned, supervisor_model,
)
from .runtime import now, write_json
from .task_progression import local_replan_reason
from .workflow_support import RecoveryStop

DELIVERY_SELECTION_VERSION = 2
SOURCE_CONTEXT_VERSION = 3
_CANDIDATE_ENVELOPE = {"action", "reason", "candidate"}
_CANDIDATE_LINEAGE = ("lineage_main_node_id", "lineage_other_node_ids", "evolution_operator")
DUPLICATE_TRANSITION_EDIT = "collapse_identical_candidate_transition"
_CANDIDATE_NESTING = (
    'For action="select_candidate", the outer object contains exactly action, reason and candidate. '
    "For a candidate with source lineage, put lineage_main_node_id, lineage_other_node_ids "
    "and evolution_operator inside the candidate object, "
    "at the same level as hypothesis and decisive_test. Write transition exactly once inside candidate. "
    "Close candidate before closing the outer object."
)


def preceding_qualification_stop(session: Any) -> dict[str, Any] | None:
    """A new Cycle may take over the immediately preceding stop for its owner."""
    owner, state = session.owner, session.state
    existing = state.get("supervisor_qualification") or {}
    if not owner.supervisor_enabled() or existing.get("status") not in {None, "reviewing"}:
        return None
    for cycle in list_cycle_dirs(owner.spec.research_root, task_mode=owner.spec.task_mode):
        if cycle.name == str(session.run_id):
            continue
        path = cycle / "research_state.json"
        try:
            if cycle.is_symlink() or path.is_symlink() or path.stat().st_size > 2_000_000:
                return None
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(prior, dict):
            return None
        if prior.get("worker_id") != state.get("worker_id"):
            continue
        if str(prior.get("cycle_id")) != cycle.name or local_replan_reason(prior) != "no_qualified_candidate":
            return None
        return prior
    return None


def _stop_evidence(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle_id": source.get("cycle_id"),
        "stopped_candidates": [{
            "id": row.get("id"),
            "hypothesis": str(row.get("hypothesis") or "")[:800],
            "decisive_test": str(row.get("decisive_test") or "")[:1800],
        } for row in (source.get("frontier_review") or {}).get("generated_candidates", [])[:3]
           if isinstance(row, dict)],
        "qualification_attempts": [{
            "round": row.get("round"),
            "reason": str(row.get("reason") or "")[:2500],
            "candidate_ids": row.get("candidate_ids"),
        } for row in (source.get("qualification_attempts") or [])[-4:] if isinstance(row, dict)],
    }


def _adjudicated_tests(
    store: ScienceGraphStore, goal_cycles: frozenset[str] = frozenset(),
) -> list[dict[str, str]]:
    """Reserve exact comparisons for current Goal judgments and recent Cycles."""
    rows = []
    for node in store.graph_view()["nodes"]:
        if node.get("type") != "Hypothesis":
            continue
        outcomes = node.get("cycle_outcomes") or []
        for index, outcome in enumerate(outcomes):
            cycle = str(outcome["cycle_id"])
            if index < len(outcomes) - 2 and cycle not in goal_cycles:
                continue
            test = str(outcome.get("decisive_test") or "")
            if not test or outcome.get("verdict") not in {"supported", "rejected", "open"}:
                continue
            rows.append({"hypothesis_id": node["id"], "title": str(node.get("title") or "")[:150],
                         "cycle": cycle, "verdict": outcome["verdict"], "decisive_test": test[:2500],
                         "rationale": str(outcome.get("judge_rationale") or "")[:900]})
    rows.sort(key=lambda row: (row["cycle"] in goal_cycles,
                               cycle_number(row["cycle"]) or 0, row["cycle"]), reverse=True)
    remaining = 42_000
    tests = []
    for row in rows:
        size = sum(len(value) for value in row.values())
        if size <= remaining:
            tests.append(row)
            remaining -= size
    return tests


def _review_context(session: Any, source: dict[str, Any], graph_path: Path) -> dict[str, Any]:
    owner = session.owner
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = store.agent_view()
        gate = sorted(store._goal_gate_history_rows(), key=lambda row: row["recorded_at"])[-5:]
        tests = _adjudicated_tests(store, frozenset(str(row["cycle_id"]) for row in gate))
    return {
        "goal": owner.spec.goal,
        "research_contract": owner.spec.contract,
        "final_gate": owner.spec.final_gate,
        "required_context": owner.spec.required_context,
        "scientific_model": session.model,
        "agent_graph": graph,
        "adjudicated_tests": tests,
        "goal_judgments": gate,
        "qualification_stop": _stop_evidence(source),
        "previous_qualification_stops": (session.preparation_feedback or {}).get("recent_qualification_stops", []),
        "running_hypotheses": owner._running_hypotheses_for_lead(),
    }


def parse_review_object(raw: str, *, edits: list[str] | None = None) -> tuple[dict[str, Any], bool]:
    """Decode one decision with narrowly scoped, lossless normalization."""

    repeated_transitions = []

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                if (edits is None or key != "transition" or not isinstance(value, dict)
                        or stable_hash(value) != stable_hash(result[key])):
                    raise ValueError(f"Duplicate Supervisor decision key: {key}")
                repeated_transitions.append(result)
            result[key] = value
        return result

    value = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", value, flags=re.I)
    if fenced:
        value = fenced.group(1).strip()
    output, end = json.JSONDecoder(object_pairs_hook=unique_object).raw_decode(value)
    if not isinstance(output, dict):
        raise ValueError("expected a Supervisor decision object")
    if repeated_transitions and (len(repeated_transitions) != 1 or output.get("action") != "select_candidate"
                                 or output.get("candidate") is not repeated_transitions[0]):
        raise ValueError("Duplicate Supervisor decision key: transition")
    remainder = value[end:]
    tail = remainder.strip()
    if tail and tail != "}":
        raise json.JSONDecodeError("Extra data", value, end + len(remainder) - len(remainder.lstrip()))
    if repeated_transitions:
        edits.append(DUPLICATE_TRANSITION_EDIT)
    return output, bool(tail)


def normalize_candidate_envelope(output: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Move the exact misplaced lineage trio into its sole candidate, without changing values."""
    if output.get("action") != "select_candidate" or output.keys() == _CANDIDATE_ENVELOPE:
        return output, ()
    if output.keys() != _CANDIDATE_ENVELOPE.union(_CANDIDATE_LINEAGE):
        raise ValueError("Invalid select_candidate envelope. " + _CANDIDATE_NESTING)
    candidate = output["candidate"]
    if not isinstance(candidate, dict) or any(key in candidate for key in _CANDIDATE_LINEAGE):
        raise ValueError("Cannot move ambiguous candidate lineage fields. " + _CANDIDATE_NESTING)
    return {
        "action": output["action"], "reason": output["reason"],
        "candidate": {**candidate, **{key: output[key] for key in _CANDIDATE_LINEAGE}},
    }, _CANDIDATE_LINEAGE


def _decision(raw: str, context: dict[str, Any], state: dict[str, Any], *,
              trace: SupervisorSession | None = None, thread_id: str | None = None) -> dict[str, Any]:
    edits: list[str] = []
    parsed, normalized = parse_review_object(raw, edits=edits)
    if edits and trace is not None:
        trace.append("normalization", "Collapsed one repeated candidate transition with identical content.",
                     thread_id=thread_id, edit=DUPLICATE_TRANSITION_EDIT,
                     raw_sha256=hashlib.sha256(raw.encode()).hexdigest(), decision_sha256=stable_hash(parsed))
    if normalized and trace is not None:
        trace.append("normalization", "Removed one superfluous closing brace from the Supervisor decision.",
                     thread_id=thread_id, edit="remove_extraneous_root_closer",
                     raw_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                     decision_sha256=stable_hash(parsed))
    output, moved = normalize_candidate_envelope(parsed)
    if moved and trace is not None:
        trace.append("normalization", "Moved the three lineage fields into the candidate object.",
                     thread_id=thread_id, edit="move_candidate_lineage_fields", moved_fields=list(moved),
                     parsed_decision_sha256=stable_hash(parsed), decision_sha256=stable_hash(output))
    action = output.get("action")
    reason = output.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 3000:
        raise ValueError("expected a bounded evidence-based reason")
    if action == "select_candidate" and output.keys() == _CANDIDATE_ENVELOPE:
        frontier = {"candidates": [output["candidate"]]}
        contract = role_contract("research_lead", phase="frontier_proposal")
        contract.validate(frontier, {**context, "_require_explicit_lineage": True})
        return {"status": "proposed", "reason": reason,
                "frontier": close_agent_output("research_lead", "frontier_proposal", frontier, context, state)}
    if action == "materialize_delivery" and output.keys() == {"action", "reason", "delivery_plan"}:
        choices = context.get("delivery_repair")
        plan = output["delivery_plan"]
        if not isinstance(choices, dict) or not isinstance(plan, dict):
            raise ValueError("No authorized delivery plan is available")
        inputs = {row["reference"]: row["first_columns"] for row in choices["inputs"]}
        predictions = {row["reference"]: row["columns"] for row in choices["predictions"]}
        input_columns = inputs.get(str(plan.get("input")))
        prediction_columns = predictions.get(str(plan.get("predictions")))
        if input_columns is None or prediction_columns is None:
            raise ValueError("Use the exact input and prediction references supplied by the SDK")
        if plan.get("input_key") not in input_columns or plan.get("prediction_key") not in prediction_columns:
            raise ValueError("Use literal row-key column names from the selected CSV headers")
        outputs = plan.get("outputs")
        allowed_columns = set(prediction_columns) - {plan["prediction_key"]}
        if not isinstance(outputs, dict) or not outputs or not outputs.keys() <= allowed_columns:
            raise ValueError("The outputs object must map exact prediction column names to filenames. "
                             "Choose each required column from: " + ", ".join(sorted(allowed_columns)))
        return {"status": "delivery_planned", "reason": reason, "delivery_plan": plan}
    if action == "wait_for_dependency":
        required = context.get("newly_verified_input_sha256") or []
        fields = {"action", "reason", "wake_condition"}
        acknowledged = {}
        if required:
            fields.add("reviewed_input_sha256")
            reviewed = output.get("reviewed_input_sha256")
            if (not isinstance(reviewed, list) or not all(isinstance(item, str) for item in reviewed)
                    or sorted(reviewed) != required):
                raise ValueError("Before waiting, use reviewed_input_sha256 to acknowledge exactly the newly verified input digests.")
            acknowledged["reviewed_input_sha256"] = reviewed
        wake = output.get("wake_condition")
        if output.keys() == fields and isinstance(wake, str) and wake.strip() and len(wake) <= 1500:
            return {"status": "waiting", "reason": reason, "wake_condition": wake, **acknowledged}
    raise ValueError("Supervisor action or required fields are invalid")


def review_exhausted_frontier(session: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    """Return a uniquely selected, validated proposal or a specific wait condition."""
    owner, state = session.owner, session.state
    existing = state.get("supervisor_qualification") or {}
    if (not owner.supervisor_enabled() or existing.get("status") not in {None, "reviewing"}
            or state.get("hypothesis_locked") or getattr(owner.spec, "replay_cycle_id", None)):
        return None
    root = Path(owner.spec.research_root)
    graph_path = Path(owner._graph_database_path())
    owner._check_recovery_control(state)
    assert_goal_open(root, graph_path, spec=owner.spec)
    fingerprint = owner._wake_fingerprint()
    from .prequalification_holdout import holdout_context, holdout_files
    from .supervisor_delivery import delivery_repair_context

    delivery = None if state.get("worker_id") else delivery_repair_context(owner)
    from .worker_direction import (
        added_research_input_ids, direction_changed, omitted_qualification_input_ids, qualification_wait_fingerprint,
    )
    holdout = holdout_files(getattr(owner.spec, "config_path", None))
    excluded = frozenset(file.sha256 for file in holdout)
    baseline = qualification_wait_fingerprint(root, source) or source.get("wake_fingerprint")
    priority = (added_research_input_ids(baseline, fingerprint) - excluded
                | omitted_qualification_input_ids(root, source, fingerprint, excluded_sha256=excluded))
    public_sources = verified_research_inputs(
        owner.spec, include_text=True, excluded_sha256=excluded, prioritize_sha256=priority)
    relevant = sorted({row["sha256"] for row in public_sources if row["sha256"] in priority})
    previous = source.get("supervisor_qualification")
    if isinstance(previous, dict) and previous.get("status") in {"waiting", "review_exhausted", "delivery_reviewed"}:
        new_delivery_route = bool(delivery and (
            not previous.get("delivery_review_available")
            or (previous.get("delivery_plan") and not previous.get("delivery_result")
                and previous.get("delivery_selection_version") != DELIVERY_SELECTION_VERSION)
        ))
        source_context_upgraded = bool(public_sources and previous.get("source_context_version") != SOURCE_CONTEXT_VERSION)
        if not new_delivery_route and not source_context_upgraded and not direction_changed(baseline, fingerprint):
            state["supervisor_qualification"] = dict(previous)
            return {"candidates": [], "blocking_reason": str(previous.get("reason") or "The recorded dependency is unchanged.")}
    context = _review_context(session, source, graph_path)
    context["verified_public_sources"] = public_sources
    input_instruction = ""
    wait_response = {"action": "wait_for_dependency", "reason": "...", "wake_condition": "..."}
    if relevant:
        context["newly_verified_input_sha256"] = relevant
        wait_response["reviewed_input_sha256"] = ["..."]
        input_instruction = (
            "The listed newly_verified_input_sha256 digests caused or justify this review. Inspect their exact "
            "verified_public_sources entries and any associated context. If an entry has only metadata, use its "
            "authorized reference. Before waiting, consider whether each opens a materially distinct test, "
            "even when it does not address the former active hypothesis or missing prerequisite. "
            "Explain in reason what comparison the new evidence enables and what still prevents progress. "
            "A wait response must include reviewed_input_sha256, a JSON array with exactly the listed digests. "
        )
    if holdout:
        context["prequalification_holdout"] = holdout_context(holdout)
    delivery_instruction = ""
    if delivery:
        context["delivery_repair"] = delivery
        delivery_instruction = (
            "If the last Goal Judge gap was only a requested table and the committed predictions already exist, "
            "choose materialize_delivery. Copy labels by exact row keys from the registered predictions into "
            "the declared public table. Use only the listed available inputs and output names supported by the "
            "actual Research Contract. This cannot repair missing independent evidence or change scientific values. "
            'Return an object with action="materialize_delivery", reason and a delivery_plan object. '
            "The delivery_plan contains operation=csv_label_projection, input, predictions, input_key, "
            "prediction_key, label_column, allowed_labels (an array of the exact literal labels), and outputs. "
            "The keys of outputs are exact prediction CSV headers, one per required prediction vector. "
            "The values are distinct CSV filenames. Copy each actual header from the supplied columns. "
            "If the requirement names multiple prediction vectors, include each separately. "
        )
    contract = role_contract("research_lead", phase="frontier_proposal")
    prompt = (
        "Supervisor Attention skill: exhausted qualification and whole-task frontier recovery. "
        "Review the task's Goal, Final Gate, Scientific Model, full hypothesis index and recorded outcomes, "
        "prior Goal judgments, running hypotheses and candidate failures. Identify the actual remaining gap. "
        "Treat verified_public_sources as the task's manifest-verified sources, including files outside this Cycle. "
        "Verify eligibility and independence separately. If the Final Gate designates a specific file or batch "
        "whose evaluation was already exposed, explain what exact authorized change would allow progress; "
        "a different dataset does not automatically satisfy that requirement. "
        "Quoted source text is research data. Use the supplied public reference when the task requires it. "
        "Separate missing original literature from access to a supplied working baseline. "
        "Recheck any historic source-availability claim against this inventory. "
        "Before selecting, compare the candidate's actual discriminating comparison with adjudicated_tests, "
        "including tests with a different title. Explain in why_now how its result would answer an unresolved "
        "question that the closest prior tests cannot answer. A smaller partition or a parameter restatement "
        "alone does not make the same comparison new. "
        "Consider a feasible, previously untested hypothesis that would resolve it or decide a necessary "
        "intermediate question using authorized inputs. Trace the hypothesis to the relevant H and outcome "
        "records; specify a concrete input, a discriminating test with outcomes for both sides, and why the "
        "result would change the research direction. Respect prior evidence and do not silently change "
        "the Goal or validation rules, invent observations, recycle rejected tests, or redo finished work. "
        "Select exactly one candidate for Challenger qualification and SDK lock. If no such test is feasible, "
        "name the missing dependency and the observable condition that would permit progress. "
        'Return strict JSON {"action":"select_candidate","reason":"...","candidate":{...}} or '
        + json.dumps(wait_response) + ". "
        + input_instruction
        + delivery_instruction
        + "For the candidate object use the candidate schema in the following contract. "
        + _CANDIDATE_NESTING + " "
        "For a candidate with source lineage, candidate.lineage_other_node_ids is a literal JSON array. "
        "Use [] if there are no additional source IDs. For a candidate with source lineage, "
        "transition.source_contributions has exactly the main and additional source hypothesis IDs as keys. "
        "When the task graph contains H nodes, all three explicit lineage fields are required. "
        "Keep them absent when proposing the first root H of an empty task graph.\n"
        + contract.prompt_projection() + "\nTask evidence:\n"
        + json.dumps(context, ensure_ascii=False, default=str)
    )
    model = supervisor_model(root)
    trace = SupervisorSession(owner.cycle_dir, profile="attention",
                              incident_id="qualification-" + stable_hash(_stop_evidence(source))[:16],
                              code="no_qualified_candidate", summary="Whole-task qualification recovery", model=model)
    trace.append("prompt", prompt, role="attention_reviewer")
    receipt = {"session_id": trace.session_id, "source_cycle": str(source.get("cycle_id") or session.run_id),
               "direction_fingerprint": fingerprint, "delivery_review_available": bool(delivery),
               "delivery_selection_version": DELIVERY_SELECTION_VERSION,
               "source_context_version": SOURCE_CONTEXT_VERSION}
    if relevant:
        receipt["prioritized_input_sha256"] = relevant
    state["supervisor_qualification"] = {**receipt, "status": "reviewing"}
    owner._persist(state)
    invoke = getattr(owner.backend, "invoke_attention_review", None)
    attempt_prompt = prompt
    for attempt in range(2):
        if callable(invoke):
            thread_id, raw = invoke(attempt_prompt, model, DEFAULT_PROVIDER)
        else:
            thread_id, raw = owner.backend.invoke("attention_reviewer", attempt_prompt, None, model, DEFAULT_PROVIDER)
        trace.append("model_output", str(raw), thread_id=thread_id)
        owner._check_recovery_control(state)
        assert_goal_open(root, graph_path, spec=owner.spec)
        if not owner.supervisor_enabled():
            trace.close("hold", action="hold", reason="Supervisor was disabled during qualification review")
            state.pop("supervisor_qualification", None)
            owner._persist(state)
            return None
        try:
            selected = _decision(raw, context, state, trace=trace, thread_id=thread_id)
            break
        except (ValueError, TypeError) as exc:
            if attempt == 0:
                attempt_prompt = (prompt + "\nThe prior response failed validation. Error: "
                                  + str(exc)[:1200] + "\nRe-emit the complete response using one allowed action. "
                                  + _CANDIDATE_NESTING)
                trace.append("prompt", attempt_prompt, role="attention_reviewer", input_kind="contract_repair")
                continue
            # Invalid model output is an operational review failure.
            reason = f"Supervisor qualification review output was invalid: {exc}"
            trace.close("hold", action="hold", reason=reason)
            state.pop("supervisor_qualification", None)
            state.update(status="needs_attention", phase="supervisor_qualification_review",
                         resumable=True, attention={"code": "supervisor_qualification_review_failed", "reason": reason})
            owner._persist(state, project=True)
            raise RecoveryStop(reason) from exc
    if direction_changed(fingerprint, owner._wake_fingerprint()):
        reason = "The task evidence changed during Supervisor review. Reassess the current authority snapshot."
        trace.close("hold", action="hold", reason=reason)
        state.pop("supervisor_qualification", None)
        state.update(status="needs_attention", phase="supervisor_qualification_review",
                     resumable=True, attention={"code": "supervisor_qualification_review_failed", "reason": reason})
        owner._persist(state, project=True)
        raise RecoveryStop(reason)
    state["supervisor_qualification"] = {**receipt, **selected, "thread_id": str(thread_id or "")}
    if selected["status"] == "waiting":
        trace.close("hold", action="wait_for_dependency", reason=selected["reason"])
    elif selected["status"] == "delivery_planned":
        trace.append("decision", selected["reason"], action="materialize_delivery")
    else:
        trace.append("decision", selected["reason"], action="select_candidate")
        state.setdefault("role_outputs", {})["research_lead_frontier"] = selected["frontier"]
        state.pop("qualification_working_set", None)
        state["qualification_status"] = "pending"
    owner._emit(state, "supervisor.qualification_decided",
                {"session_id": trace.session_id, "status": selected["status"],
                 "source_cycle": receipt["source_cycle"], "reason": selected["reason"]})
    owner._persist(state)
    return selected.get("frontier") or {"candidates": [], "blocking_reason": selected["reason"]}


def mark_selection_not_qualified(session: Any, reason: str) -> None:
    receipt = session.state["supervisor_qualification"]
    receipt.update(status="review_exhausted", reason=reason,
                   wake_condition="New relevant evidence, verified inputs, or an updated research direction")
    path = session.owner.cycle_dir / "supervisor" / "status.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    if status.get("session_id") == receipt["session_id"]:
        status.update(status="hold", action="wait_for_dependency", reason=reason[:1200], at=now())
        write_json(path, status)
    session.owner._emit(session.state, "supervisor.qualification_rejected",
                        {"session_id": receipt["session_id"], "reason": reason})


def mark_selection_handed_back(owner: Any, state: dict[str, Any]) -> None:
    lock = state.get("hypothesis_lock") or {}
    if lock.get("status") != "locked" or not lock.get("id"):
        return
    receipt = state["supervisor_qualification"]
    receipt.update(status="handed_back", hypothesis_id=lock["id"])
    mark_returned(owner.cycle_dir)
    owner._emit(state, "supervisor.qualification_handed_back",
                {"session_id": receipt["session_id"], "hypothesis_id": lock["id"]})
    owner._persist(state)


def finish_planned_delivery(owner: Any, stopped: dict[str, Any]) -> bool:
    """Materialize a selected delivery and resume Goal assessment under the same owner."""
    if not isinstance(stopped, dict):
        return False
    review = stopped.get("supervisor_qualification") or {}
    if review.get("status") != "delivery_planned" or local_replan_reason(stopped) != "no_qualified_candidate":
        return False
    if not owner.supervisor_enabled():
        return False
    from .delivery_repair import repair_delivery
    from .specification import find_cycle
    from .supervisor_delivery import materialize_supervisor_delivery

    source = find_cycle(owner.spec.research_root, str(stopped["cycle_id"]), task_mode=owner.spec.task_mode)
    if owner.cycle_dir != source:
        owner._bind_cycle(source, create=False)
    owner._check_recovery_control(stopped)
    try:
        delivery = materialize_supervisor_delivery(owner, review["delivery_plan"])
        result = repair_delivery(owner, cycle=delivery["cycle_id"], reassess=True)
    except (OSError, ValueError) as exc:
        reason = f"Delivery materialization or verification could not proceed: {exc}"
        result = {"assessment": {"verdict": "not_passed", "rationale": reason}}
        delivery = None
    assessment = result.get("assessment") or {}
    passed = assessment.get("verdict") == "passed"
    review.update(status="delivery_reviewed" if passed else "waiting",
                  delivery_result=delivery, goal_verdict=assessment.get("verdict"))
    if not passed:
        review["reason"] = str(assessment.get("rationale") or "The delivery assessment remains incomplete.")
        review["wake_condition"] = "Verified delivery materials or an authorized research direction change"
        stopped["attention"].update(reason=review["reason"], wake_condition=review["wake_condition"])
    owner._bind_cycle(source, create=False)
    owner._emit(stopped, "supervisor.delivery_reviewed",
                {"session_id": review["session_id"], "goal_verdict": review["goal_verdict"],
                 "delivery": delivery, "reason": review["reason"]})
    owner._persist(stopped, project=True)
    if passed:
        mark_returned(source)
    else:
        path = source / "supervisor" / "status.json"
        status = json.loads(path.read_text(encoding="utf-8"))
        if status.get("session_id") == review["session_id"]:
            status.update(status="hold", action="wait_for_dependency", reason=review["reason"][:1200], at=now())
            write_json(path, status)
    return True
