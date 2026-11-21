"""Bounded author delivery correction under the original Investigator authority."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .runtime import now, write_json
from .workflow_support import RecoveryStop


def _gap_digest(gap: dict) -> str:
    return hashlib.sha256(json.dumps(gap, sort_keys=True).encode()).hexdigest()


def persist_submission_gap(
    owner: Any, state: dict, check: dict, snapshot: Any, access: dict
) -> dict:
    gap = {
        "code": check["code"],
        "validation": check,
        "repair_role": "investigator",
        "source_cycle": owner.cycle_dir.name,
        "at": now(),
        "delivery_revision": access["delivery_revision"],
        "snapshot_manifest": str(snapshot.manifest_path),
        "snapshot_manifest_sha256": snapshot.manifest_sha256,
        "authority": "public_submission_structure_only",
    }
    path = owner.cycle_dir / "submission_delivery" / (_gap_digest(gap) + ".json")
    write_json(path, gap)
    state["goal_submission_gap"] = {**gap, "receipt_path": str(path)}
    state["goal_status"] = "active"
    owner._emit(state, "goal.submission_incomplete", state["goal_submission_gap"])
    owner._persist(state)
    return state["goal_submission_gap"]


def _restore_submission_snapshot(cycle: Path, state: dict):
    from .delivery_repair import _canonical
    from .goal_snapshot import GoalAuthoritySnapshot
    from .public_submission import MAX_SUBMISSION_BYTES, read_bounded_regular_file, strict_json

    gap = state["goal_submission_gap"]
    pending = state["goal_delivery_pending"]
    if (gap["source_cycle"] != cycle.name or gap["repair_role"] != "investigator"
            or gap["authority"] != "public_submission_structure_only"
            or gap["validation"]["status"] != "invalid"
            or gap["validation"]["code"] != gap["code"]
            or pending["cycle_id"] != cycle.name or pending["closed_status"] not in {"over", "completed"}
            or any(not gap[key] or gap[key] != pending[key] for key in (
                "delivery_revision", "snapshot_manifest", "snapshot_manifest_sha256"))):
        raise ValueError("Submission gap does not bind the pending delivery")
    evidence = {key: value for key, value in gap.items() if key != "receipt_path"}
    receipt = cycle / "submission_delivery" / (_gap_digest(evidence) + ".json")
    if (gap["receipt_path"] != str(receipt)
            or strict_json(read_bounded_regular_file(receipt, MAX_SUBMISSION_BYTES)) != evidence):
        raise ValueError("Submission gap receipt changed")
    revision = gap["delivery_revision"]
    if not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Invalid frozen submission revision identity")
    revision_path = cycle.parent / "delivery_revisions" / f"{revision}.json"
    frozen = strict_json(read_bounded_regular_file(revision_path, MAX_SUBMISSION_BYTES))
    if (hashlib.sha256(_canonical(frozen)).hexdigest() != revision or frozen["cycle_id"] != cycle.name
            or frozen["submission_contract"]["sha256"] != gap["validation"]["contract_sha256"]):
        raise ValueError("Frozen submission revision changed")
    manifest = Path(gap["snapshot_manifest"])
    if manifest.name != "manifest.json" or manifest.parent.parent != cycle / "goal_snapshots":
        raise ValueError("Submission snapshot belongs to another Cycle")
    read_bounded_regular_file(manifest, MAX_SUBMISSION_BYTES)
    snapshot = GoalAuthoritySnapshot.restore(manifest, gap["snapshot_manifest_sha256"])
    access = snapshot.access
    if (access["delivery_revision"] != gap["delivery_revision"]
            or access["submission_contract"]["sha256"] != gap["validation"]["contract_sha256"]):
        raise ValueError("Submission snapshot changed its delivery or public schema authority")
    return snapshot


def submission_takeover_preflight(cycle: Path, state: dict, *, spec: Any = None) -> dict | None:
    """Classify a stopped submission without dispatching or changing its evidence."""
    attention = state.get("attention")
    if not isinstance(attention, dict) or attention.get("code") != "public_submission_incomplete":
        return None

    def hold(code: str, reason: str) -> dict:
        return {"status": "hold", "code": code, "reason": reason}

    from .delivery_repair import _canonical, current_delivery_payload
    from .goal_terminal import goal_sealed
    from .investigator_budget import budget_status
    from .notebook import graph_database_path
    from .public_submission import check_current_submission
    from .science_graph_store import ScienceGraphStore
    from .specification import TaskSpec

    cycle = Path(cycle).absolute()
    control = cycle / "recovery_control.json"
    try:
        recovery = state.get("recovery") or {}
        if (state.get("cycle_id") != cycle.name or state.get("status") != "needs_attention"
                or state.get("phase") != "goal_submission:delivery_pending"
                or state.get("execution_active") or state.get("active_invocation_id") is not None
                or not state.get("resumable") or state.get("abandoned_cycle")
                or control.exists() or control.is_symlink()
                or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
            return hold("submission_stop_changed", "The original submission stop is no longer available for takeover.")

        spec = spec or TaskSpec.from_task_root(cycle.parent)
        lock, threads = state["hypothesis_lock"], state["threads"]
        judge = state["role_outputs"]["judge"]
        thread = threads.get("investigator")
        native = state.get("native_goal") or {}
        if (Path(spec.research_root).resolve() != cycle.parent or state.get("goal_sha256") != spec.goal_sha256
                or state.get("protocol_health") != "valid" or state.get("goal_status") != "active"
                or state.get("scientific_phase") != "committed" or state.get("scientific_verdict") != "supported"
                or judge.get("verdict") != "supported" or lock.get("status") != "locked" or not lock.get("id")
                or not isinstance(thread, str) or not thread.strip()
                or native.get("thread_id") not in (None, thread)
                or threads.get(f"attempt-{int(state.get('investigation_attempt') or 0):04d}:investigator")
                    not in (None, thread)):
            raise ValueError("Original scientific or Investigator identity has changed")
        graph = spec.graph_database or graph_database_path(Path(spec.branch_canvas).resolve().parent)
        if goal_sealed(cycle.parent, graph, spec=spec) is not None:
            return hold("submission_goal_sealed", "The task Goal is already sealed.")
        with ScienceGraphStore(graph, readonly=True) as store:
            outcome = store.cycle_evaluation(cycle.name)
        if (not outcome or outcome.get("cycle_id") != cycle.name or outcome.get("verdict") != "supported"
                or outcome.get("hypothesis_id") != lock["id"]):
            raise ValueError("No matching committed supported Outcome")

        snapshot = _restore_submission_snapshot(cycle, state)
        current = current_delivery_payload(spec, graph, cycle)
        contract = current.get("submission_contract") or {}
        if contract.get("sha256") != snapshot.access["submission_contract"]["sha256"]:
            raise ValueError("Public submission contract changed")
        check = check_current_submission(current)
        if check and check["status"] == "valid":
            return {"status": "ready_for_goal", "code": "submission_already_valid",
                    "reason": "The registered public submission is ready for the same Cycle’s Goal Gate."}

        saved_budget = state.get("investigator_budget") or {}
        budget = budget_status(cycle, cycle.parent, state)
        if budget["exhausted"] or saved_budget.get("exhausted") or saved_budget.get("rejected"):
            return hold("submission_budget_exhausted", "The original Investigator token allowance is exhausted or declined.")
        count = int(state.get("submission_delivery_repair_count") or 0)
        attempt = state.get("submission_delivery_attempt") or {}
        limit = max(0, int(spec.max_judge_reworks_per_cycle) - int(state.get("judge_rework_count") or 0))
        current_revision = hashlib.sha256(_canonical(current)).hexdigest()
        continuing = (attempt.get("status") == "running"
                      and attempt.get("delivery_revision") == current_revision
                      == state["goal_submission_gap"]["delivery_revision"])
        if count < 0 or (continuing and (count < 1 or attempt.get("count") != count
                                       or attempt.get("output_key") != f"investigator_submission_delivery_{count}")):
            raise ValueError("Original bounded delivery attempt changed")
        if count > limit or (count == limit and not continuing):
            return hold("submission_corrections_exhausted", "The original bounded public submission corrections are exhausted.")
        return {"status": "ready", "code": "submission_correction_available",
                "reason": "The original Investigator can continue the bounded delivery under the existing allowance."}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, sqlite3.Error):
        return hold("submission_authority_unverified", "The original submission evidence or delivery authority could not be verified.")


def _stop(owner: Any, state: dict, reason: str) -> None:
    state.update(
        status="needs_attention",
        phase="goal_submission:delivery_pending",
        resumable=True,
        execution_active=False,
        next_executor="investigator",
    )
    state["attention"] = {
        "code": "public_submission_incomplete",
        "reason": reason,
        "requested_action": "Continue the original Investigator's bounded public submission delivery within its frozen H and existing token allowance.",
    }
    state["human_attention"] = reason
    owner._emit(
        state,
        "goal.submission_pending",
        {
            "reason": reason,
            "repair_count": state.get("submission_delivery_repair_count", 0),
        },
    )
    owner._persist(state)
    raise RecoveryStop(reason)


def correct_submission_delivery(
    owner: Any, state: dict, snapshot: Any, *, allow_rework: bool = True
) -> None:
    """One existing-stage invocation; no fresh H, verdict, token grant or SDK values."""
    from .investigator_budget import budget_status

    gap = state["goal_submission_gap"]
    owner._check_recovery_control(state)
    if not allow_rework:
        _stop(
            owner,
            state,
            "Delivery-only reassessment cannot authorize a new Investigator call; public submission remains incomplete.",
        )
    access = snapshot.access
    contract = access.get("submission_contract")
    operator_delivery = gap.get("authority") == "operator_delivery_structure_only"
    if not operator_delivery and (not isinstance(contract, dict)
            or contract.get("format", "json") not in ("json", "python")
            or contract.get("sha256") != gap["validation"]["contract_sha256"]):
        _stop(owner, state, "The frozen public submission contract does not authorize this correction format.")
    submission_format = "operator" if operator_delivery else contract.get("format", "json")
    lock = state.get("hypothesis_lock") or {}
    if lock.get("status") != "locked" or not state.get("threads", {}).get(
        "investigator"
    ):
        _stop(
            owner,
            state,
            "Public submission correction requires the original frozen H and retained Investigator thread.",
        )
    budget = budget_status(owner.cycle_dir, owner.spec.research_root, state)
    saved_budget = state.get("investigator_budget") or {}
    if (
        budget.get("exhausted")
        or saved_budget.get("exhausted")
        or saved_budget.get("rejected")
    ):
        _stop(
            owner,
            state,
            "The original Investigator token budget guard remains active; public submission is still incomplete.",
        )
    count = int(state.get("submission_delivery_repair_count") or 0)
    attempt = state.get("submission_delivery_attempt") or {}
    continuing = (
        attempt.get("status") == "running"
        and attempt.get("delivery_revision") == gap["delivery_revision"]
    )
    limit = max(
        0,
        int(owner.spec.max_judge_reworks_per_cycle)
        - int(state.get("judge_rework_count") or 0),
    )
    if continuing and count > limit:
        _stop(
            owner,
            state,
            "The configured correction permission no longer covers this interrupted delivery attempt.",
        )
    if not continuing:
        if count >= limit:
            _stop(
                owner,
                state,
                "The bounded public submission delivery corrections were exhausted; retain the exact schema gap without sealing passed.",
            )
        count += 1
        state["submission_delivery_repair_count"] = count
        attempt = {
            "status": "running",
            "count": count,
            "delivery_revision": gap["delivery_revision"],
            "output_key": f"investigator_submission_delivery_{count}",
        }
        state["submission_delivery_attempt"] = attempt
    identity = copy.deepcopy(lock)
    original_thread = state["threads"]["investigator"]
    original_goal = copy.deepcopy(state.get("native_goal"))
    verdict = state.get("scientific_verdict")
    previous_next = state.get("next_executor")
    state.update(
        status="active", phase="investigator:submission_delivery", execution_active=True
    )
    feedback = {
        "thread_id": original_thread,
        "mode": "delivery_only",
        "authority": "operator_delivery_structure_only" if operator_delivery else "public_submission_structure_only",
        "schema_mismatch": copy.deepcopy(gap["validation"]),
        "frozen_material_access": access,
        "instruction": (gap["validation"]["instructions"] if operator_delivery else
            "Author a supplemental, formally registered JSON artifact satisfying the explicit public schema, using retained results. Preserve original scientific files, frozen H and verdict; do not perform new research or ask SDK/Reporter to create the scientific values."),
    }
    if submission_format == "python":
        feedback.update(
            format="python",
            instruction=(
                f"Author and register one supplemental Python artifact named `{contract['filename']}` "
                "using the retained results and the frozen callable signature and import allowlist. "
                "If that filename already exists in the original materials, use a new supplemental directory. "
                "Preserve original scientific files, frozen H and verdict. "
                "Do not execute the artifact, rerun experiments, or make a new scientific claim."
            ),
        )
    owner._emit(
        state,
        "goal.submission_rework_requested",
        {
            "count": count,
            "contract_sha256": gap["validation"]["contract_sha256"],
            "same_investigator": True,
        },
    )
    owner._persist(state)

    owner._call(
        state,
        "investigator",
        {
            "hypothesis_lock": copy.deepcopy(lock),
            "branch": copy.deepcopy(lock),
            "submission_delivery": feedback,
            **({"public_submission": contract} if submission_format == "python" else {}),
            "investigation_attempt": state.get("investigation_attempt"),
            "round": state.get("investigation_round_count"),
        },
        fresh_thread=False,
        output_key=attempt["output_key"],
    )
    if (
        state.get("hypothesis_lock") != identity
        or state.get("scientific_verdict") != verdict
        or state.get("threads", {}).get("investigator") != original_thread
        or state.get("native_goal") != original_goal
    ):
        _stop(
            owner,
            state,
            "Public submission correction changed frozen scientific authority; no Goal seal is permitted.",
        )
    try:
        snapshot.validate()
    except (OSError, ValueError) as exc:
        _stop(
            owner,
            state,
            "An original frozen scientific file changed during submission correction: "
            + str(exc),
        )
    attempt["status"] = "completed"
    attempt["completed_at"] = now()
    state.update(status="over", phase="complete", next_executor=previous_next)
    owner._persist(state)


def _provider_usage_baseline(
    owner: Any, state: dict, thread_id: str | None
) -> int | None:
    """Provider totals belong to the same thread's runtime projection, not Goal usage."""
    from .public_submission import read_bounded_regular_file, strict_json

    candidates = [state]
    projection = owner.cycle_dir / "run_state.json"
    if projection.exists() or projection.is_symlink():
        value = strict_json(read_bounded_regular_file(projection, 16 * 1024 * 1024))
        if not isinstance(value, dict):
            raise ValueError("Investigator runtime projection must be an object")
        candidates.append(value)
    totals = []
    for candidate in candidates:
        agent = (candidate.get("agents") or {}).get("investigator") or {}
        readings = [(candidate.get("thread_usage_baselines") or {}).get(thread_id)]
        if agent.get("thread_id") == thread_id:
            readings.extend(
                agent.get(key)
                for key in ("latest_cumulative_usage", "last_turn_cumulative_usage")
            )
        for raw in readings:
            if isinstance(raw, dict) and raw:
                totals.append(
                    int(raw.get("input_tokens") or 0)
                    + int(raw.get("output_tokens") or 0)
                )
    return max(totals) if totals else None


def delivery_usage_guard(session: Any):
    """Meter an ordinary Turn against the original cumulative token allowance."""
    from .investigator_budget import budget_status

    owner, state = session.owner, session.state
    budget = budget_status(owner.cycle_dir, owner.spec.research_root, state)
    before, allowance = int(budget["observed_tokens"]), int(budget["allowance_tokens"])

    def account(used: int) -> None:
        current = dict(state.get("investigator_budget") or {})
        observed = max(
            before + max(0, int(used)), int(current.get("observed_tokens") or 0)
        )
        current.update(
            observed_tokens=observed,
            allowance_tokens=allowance,
            remaining_tokens=max(0, allowance - observed),
            exhausted=observed >= allowance,
        )
        state["investigator_budget"] = current
        owner._persist(state)
        owner._check_recovery_control(state)
        if observed >= allowance:
            _stop(
                owner,
                state,
                "Public submission Turn reached the original Investigator token allowance; no new allowance or Goal is created.",
            )

    required_thread = (session.context.get("submission_delivery") or {}).get(
        "thread_id"
    )
    account.provider_usage_baseline = _provider_usage_baseline(
        owner, state, required_thread
    )

    def check_thread(identifier: str) -> None:
        required = (session.context.get("submission_delivery") or {}).get("thread_id")
        if not required or identifier != required:
            _stop(
                owner,
                state,
                "Public submission transport cannot replace its original Investigator thread.",
            )

    account.check_thread = check_thread
    account(0)
    return account
