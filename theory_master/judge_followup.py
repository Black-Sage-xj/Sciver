"""Bind a successor proposal to the preceding Judge decision on its worker."""
from __future__ import annotations

import json

from .prequalification_holdout import holdout_files
from .science_graph_store import ScienceGraphStore
from .scientific_objective import frozen_science_view
from .specification import cycle_number, list_cycle_dirs


def _worker(state):
    return (state.get("cycle_execution_context") or {}).get("worker_id", state.get("worker_id"))


def qualification_followup(session):
    owner, state = session.owner, session.state
    root = owner.spec.research_root
    worker = getattr(owner, "worker_id", None)
    ordinal = cycle_number(session.run_id)
    if (not state.get("judge_cycle_boundary") or state.get("human_direction")
            or ordinal is None or ordinal <= 1):
        return None
    # Only this worker's preceding Cycle owns its handoff. An intervening
    # completed ordinary Cycle consumes the old direction.
    expected = state.get("incoming_judge_followup")
    for directory in list_cycle_dirs(root, task_mode=owner.spec.task_mode):
        previous = cycle_number(directory.name)
        if previous is None or previous >= ordinal:
            continue
        path = directory / "research_state.json"
        if not path.is_file():
            continue
        source = json.loads(path.read_text(encoding="utf-8"))
        if _worker(source) != worker:
            continue
        if (source.get("status") == "no_qualified_candidate" and not source.get("hypothesis_locked")
                and source.get("incoming_judge_followup")):
            # A failed qualification did not create a scientific successor.
            # Follow its chain back to the actual Judge-owned record.
            inherited = source["incoming_judge_followup"]
            if expected is not None and expected != inherited:
                raise ValueError("Qualification retries disagree on the original Judge decision")
            expected = inherited
            continue
        break
    else:
        if expected:
            raise ValueError("The referenced Judge Cycle is missing")
        return None
    record = source.get("judge_followup")
    if record is None:
        if expected:
            raise ValueError("The persisted successor lost its original Judge decision")
        return None
    if (source.get("goal_id") != owner.spec.goal_id
            or source.get("goal_sha256") != owner.spec.goal_sha256
            or ("source_branch_anchor_id" in record
                and record["source_branch_anchor_id"] != owner.spec.branch_anchor_id)):
        return None
    lock = source.get("hypothesis_lock") or {}
    parent = record.get("hypothesis_id")
    binding = {"cycle": directory.name, "hypothesis_id": parent}
    if (source.get("status") != "over" or record.get("cycle") != directory.name
            or not parent or source.get("next_start_node_id") != parent or lock.get("id") != parent
            or not isinstance(record.get("judge"), dict)
            or (expected is not None and expected != binding)):
        raise ValueError("Judge follow-up must reference its original closed Cycle and locked Hypothesis")
    with ScienceGraphStore(owner._graph_database_path(), readonly=True) as store:
        owned = store.db.execute("SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id=?", (parent,)).fetchone()
        if not owned or owned["cycle_id"] != directory.name:
            raise ValueError("Judge follow-up source disagrees with the canonical graph")
    if "incoming_judge_followup" not in state:
        state["incoming_judge_followup"] = binding
        owner._persist(state)
    view = {**binding, "prior_objective": frozen_science_view(lock)}
    if not holdout_files(getattr(owner.spec, "config_path", None)):
        judge = record["judge"]
        decision = {key: judge[key] for key in ("verdict", "rationale", "missing_evidence") if key in judge}
        objections = judge.get("objections") or [judge.get("objection")]
        decision["objections"] = [
            {key: item[key] for key in ("reason", "requested_resolution", "needs_external_scholarship",
                                        "executor_role") if key in item}
            for item in objections if isinstance(item, dict)
        ]
        target = (judge.get("rework_plan") or {}).get("target_executor")
        if target:
            decision["target_executor"] = target
            if target in {"literature", "investigator"}:
                decision["needs_external_scholarship"] = target == "literature"
        view["judge_decision"] = decision
    view["instruction"] = (
        "Research Lead proposes a successor to this Cycle with hypothesis_id as lineage_main_node_id, "
        "and explains the scientific change and discriminating test. Challenger assesses whether the new "
        "proposal addresses the gap, including whether a reformulation merely repeats the prior experiment."
    )
    return view


def candidate_problem(candidate, followup):
    if not followup:
        return None
    parent = followup["hypothesis_id"]
    if candidate.get("lineage_main_node_id") != parent:
        return f"Judge follow-up requires {parent} as the new conjecture's lineage_main_node_id."
    previous = followup["prior_objective"]

    def normalized(value):
        return " ".join(str(value or "").split())
    if all(normalized(candidate.get(key)) == normalized(previous.get(key))
           for key in ("hypothesis", "scope", "decisive_test")):
        return f"The candidate repeats the claim, scope and test of {parent}; propose a new scientific conjecture or discriminating test addressing its gap."
    return None
