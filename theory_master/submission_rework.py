"""Bounded author delivery correction under the original Investigator authority."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .runtime import now, write_json
from .workflow_support import RecoveryStop


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
    digest = hashlib.sha256(json.dumps(gap, sort_keys=True).encode()).hexdigest()
    path = owner.cycle_dir / "submission_delivery" / (digest + ".json")
    write_json(path, gap)
    state["goal_submission_gap"] = {**gap, "receipt_path": str(path)}
    state["goal_status"] = "active"
    owner._emit(state, "goal.submission_incomplete", state["goal_submission_gap"])
    owner._persist(state)
    return state["goal_submission_gap"]


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
        "authority": "public_submission_structure_only",
        "schema_mismatch": copy.deepcopy(gap["validation"]),
        "frozen_material_access": snapshot.access,
        "instruction": "Author a supplemental, formally registered JSON artifact satisfying the explicit public schema, using retained results. Preserve original scientific files, frozen H and verdict; do not perform new research or ask SDK/Reporter to create the scientific values.",
    }
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
