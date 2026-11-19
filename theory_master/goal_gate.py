"""Goal assessment: capture, unlocked Judge invocation, then validated commit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .delivery_repair import (
    _canonical,
    _write_immutable,
    current_delivery_payload,
    freeze_delivery_revision,
)
from .goal_snapshot import (
    GoalAuthoritySnapshot,
    GoalSnapshotSeed,
    materialize_goal_snapshot,
)
from .goal_terminal import goal_sealed, science_commit_barrier
from .runtime import write_json
from .science_graph_store import ScienceGraphStore


@dataclass
class GoalGate:
    owner: Any
    state: dict[str, Any]
    delivery_repair: bool = False

    def _stale(
        self,
        result: dict[str, Any],
        snapshot: GoalAuthoritySnapshot,
        access: dict[str, str],
        reason: str,
    ) -> dict[str, Any]:
        payload = {
            "assessment_status": "stale",
            "judge_result": result,
            "reason": reason,
            **access,
            "snapshot_manifest": str(snapshot.manifest_path),
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
        }
        path = (
            self.owner.cycle_dir
            / "goal_assessments"
            / f"stale-{snapshot.root.name}.json"
        )
        _write_immutable(path, payload)
        self.state["goal_assessment_stale"] = {**payload, "receipt_path": str(path)}
        self.state.pop("goal_delivery_pending", None)
        self.state.pop("goal_submission_gap", None)
        self.state.update(
            status="over", phase="complete", execution_active=False, resumable=False
        )
        sealed = goal_sealed(
            self.owner.spec.research_root,
            self.owner._graph_database_path(),
            spec=self.owner.spec,
        )
        if sealed is not None:
            self.state["goal_status"] = "passed"
            self.state["goal_completion"] = self.owner._sealed_goal_state(sealed)[
                "goal_completion"
            ]
            self.state["phase"] = "goal_complete"
        self.owner._emit(
            self.state, "goal_gate.stale", self.state["goal_assessment_stale"]
        )
        self.owner._persist(self.state)
        return payload

    def _apply(self, result: dict[str, Any], metadata: dict[str, str], seal=None):
        verdict = str(result.get("verdict") or "")
        applied = self.owner._apply_goal_completion(
            self.state,
            result,
            verdict,
            str(result.get("rationale") or "").strip(),
            seal,
        )
        self.state.pop("goal_delivery_pending", None)
        self.state.pop("goal_submission_gap", None)
        self.state["goal_completion"].update(metadata)
        self.state.setdefault("role_outputs", {})["goal_completion"] = result
        return applied

    def run(self) -> dict[str, Any]:
        from .submission_rework import correct_submission_delivery, _stop
        while True:
            gap = self.state.get("goal_submission_gap")
            if gap:
                try:
                    GoalAuthoritySnapshot.restore(Path(gap["snapshot_manifest"]), gap["snapshot_manifest_sha256"])
                except (OSError, ValueError) as error:
                    _stop(self.owner, self.state, "The retained submission snapshot or original scientific source changed: " + str(error))
            result = self._run_once()
            if result.get("assessment_status") != "submission_required":
                return result
            correct_submission_delivery(self.owner, self.state, result["snapshot"],
                                        allow_rework=not self.delivery_repair)

    def _run_once(self) -> dict[str, Any]:
        owner, state = self.owner, self.state
        plan, root, graph = (
            Path(owner.spec.document),
            owner.spec.research_root,
            owner._graph_database_path(),
        )
        if not plan.is_file():
            raise ValueError(
                "goal_completion requires the authoritative research_plan.md"
            )
        # Capture identities and compact authorities only. File duplication and
        # all model work happen after releasing this task-wide writer barrier.
        with science_commit_barrier(root, "exclusive"):
            sealed = goal_sealed(root, graph, spec=owner.spec)
            if sealed is not None:
                state["goal_status"] = "passed"
                return {
                    "verdict": "passed",
                    "rationale": "another Cycle completed the Goal",
                    "source_cycle": str(sealed.get("source_cycle") or ""),
                    "source": sealed["source"],
                }
            closed_status = str(state.get("status") or "over").lower()
            if closed_status not in {"over", "completed"}:
                raise ValueError("goal_completion requires a closed Cycle")
            access = freeze_delivery_revision(
                owner.spec,
                graph,
                owner.cycle_dir,
                require_unchanged_science=self.delivery_repair,
            )
            revision = access["delivery_revision"]
            with ScienceGraphStore(graph, readonly=True) as store:
                existing = store.goal_gate_assessment(
                    owner.cycle_dir.name, delivery_revision=revision
                )
                graph_view = store.graph_view()
                agent_view = store.agent_goal_completion_view()
            if isinstance(existing, dict):
                metadata = {
                    key: existing[key]
                    for key in (
                        "assessment_id",
                        "delivery_revision",
                        "science_digest",
                        "receipt_path",
                    )
                    if key in existing
                }
                return self._apply(
                    {
                        "verdict": existing["verdict"],
                        "rationale": existing["rationale"],
                    },
                    metadata,
                )
            payload = current_delivery_payload(owner.spec, graph, owner.cycle_dir)
            from .scientific_model import load_from_paths

            model = load_from_paths(root, plan, goal_id=owner.spec.goal_id)
            seed = GoalSnapshotSeed(
                plan.read_bytes(),
                _canonical(graph_view),
                _canonical(agent_view),
                _canonical(payload),
                _canonical(access),
                _canonical(model),
            )
        pending = state.get("goal_delivery_pending") or {}
        if (
            pending.get("delivery_revision") == revision
            and pending.get("snapshot_manifest")
            and pending.get("snapshot_manifest_sha256")
        ):
            snapshot = GoalAuthoritySnapshot.restore(
                Path(pending["snapshot_manifest"]), pending["snapshot_manifest_sha256"]
            )
        else:
            snapshot = materialize_goal_snapshot(owner.spec, owner.cycle_dir, seed)
        owner._check_recovery_control(state)
        from .public_submission import check_frozen_submission
        frozen_delivery = json.loads((snapshot.root / "delivery_revision.json").read_text())
        snapshot.validate()
        submission = check_frozen_submission(frozen_delivery, snapshot)
        if submission is not None:
            state["goal_submission_validation"] = submission
            if submission["status"] != "valid":
                return self._submission_required(submission, snapshot, access, closed_status)
        owner._emit(
            state,
            "goal_gate.triggered",
            {
                "trigger": "judge_supported",
                "source_cycle": owner.cycle_dir.name,
                "snapshot_manifest": str(snapshot.manifest_path),
                "snapshot_manifest_sha256": snapshot.manifest_sha256,
            },
        )
        state["goal_delivery_pending"] = {
            "cycle_id": owner.cycle_dir.name,
            "delivery_revision": revision,
            "closed_status": closed_status,
            "snapshot_manifest": str(snapshot.manifest_path),
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
        }
        owner._persist(state)
        result = owner._call(
            state,
            "judge",
            {
                "mode": "goal_completion",
                "research_plan_path": str(snapshot.plan_path),
                "delivery_material_access": snapshot.access,
                "goal_authority_snapshot": snapshot,
            },
            fresh_thread=True,
            output_key=f"goal_completion_{revision}_{snapshot.manifest_sha256}",
        )
        owner._check_recovery_control(state)
        with science_commit_barrier(root, "exclusive"):
            owner._check_recovery_control(state)
            sealed = goal_sealed(root, graph, spec=owner.spec)
            if sealed is not None:
                # The other seal is authoritative. Retain this losing review as
                # a stale receipt; never append a second seal or replace its Judge.
                return self._stale(
                    result, snapshot, access, "another assessment sealed the Goal"
                )
            try:
                snapshot.validate()
                model_now = load_from_paths(root, plan, goal_id=owner.spec.goal_id)
                frozen_model = (snapshot.root / "scientific_model.json").read_bytes()
                if _canonical(model_now) != frozen_model:
                    raise ValueError(
                        "Scientific Model authority changed during Goal assessment"
                    )
                current = current_delivery_payload(owner.spec, graph, owner.cycle_dir)
                if current["science_digest"] != access["science_digest"]:
                    raise ValueError(
                        "scientific authority changed during Goal assessment"
                    )
                if hashlib.sha256(_canonical(current)).hexdigest() != revision:
                    raise ValueError(
                        "delivery materials changed during Goal assessment"
                    )
                submission = check_frozen_submission(frozen_delivery, snapshot)
                if submission is not None and submission["status"] != "valid":
                    return self._submission_required(submission, snapshot, access, closed_status)
                with ScienceGraphStore(graph, readonly=True) as store:
                    if (
                        store.goal_gate_assessment(
                            owner.cycle_dir.name, delivery_revision=revision
                        )
                        is not None
                    ):
                        raise ValueError(
                            "another assessment already committed this delivery revision"
                        )
            except (OSError, ValueError) as error:
                return self._stale(result, snapshot, access, str(error))
            return self._commit(result, access, snapshot, plan, graph)

    def _submission_required(self, check, snapshot, access, closed_status):
        from .submission_rework import persist_submission_gap
        self.state["goal_delivery_pending"] = {
            "cycle_id": self.owner.cycle_dir.name, "delivery_revision": access["delivery_revision"],
            "closed_status": closed_status, "snapshot_manifest": str(snapshot.manifest_path),
            "snapshot_manifest_sha256": snapshot.manifest_sha256}
        persist_submission_gap(self.owner, self.state, check, snapshot, access)
        return {"assessment_status": "submission_required", "snapshot": snapshot}

    def _commit(
        self,
        result: dict[str, Any],
        access: dict[str, str],
        snapshot: GoalAuthoritySnapshot,
        plan: Path,
        graph: Path,
    ) -> dict[str, Any]:
        owner = self.owner
        revision = access["delivery_revision"]
        assessment_id = f"goal:{owner.cycle_dir.name}:{revision}"
        receipt = owner.cycle_dir / "goal_completion_out.json"
        if receipt.is_file():
            old = json.loads(receipt.read_text(encoding="utf-8"))
            old_id = hashlib.sha256(
                json.dumps(old, sort_keys=True).encode()
            ).hexdigest()
            _write_immutable(
                owner.cycle_dir / "goal_assessments" / f"legacy-{old_id}.json", old
            )
        frozen_receipt = owner.cycle_dir / "goal_assessments" / f"{revision}.json"
        payload = {
            "verdict": result.get("verdict"),
            "rationale": result.get("rationale"),
            "assessment_id": assessment_id,
            **access,
            "snapshot_manifest": str(snapshot.manifest_path),
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
            **({"submission_validation": self.state["goal_submission_validation"]}
               if self.state.get("goal_submission_validation") is not None else {}),
        }
        _write_immutable(frozen_receipt, payload)
        write_json(receipt, payload)
        metadata = {
            "assessment_id": assessment_id,
            "delivery_revision": revision,
            "science_digest": access["science_digest"],
            "receipt_path": str(frozen_receipt),
        }
        rationale = str(result.get("rationale") or "").strip()
        seal = None
        if result.get("verdict") == "passed":
            seal = owner._seal_goal_in_sqlite(
                plan, graph, frozen_receipt, rationale, assessment_metadata=metadata
            )
        elif result.get("verdict") == "not_passed":
            with ScienceGraphStore(graph) as store:
                store.record_goal_gate_not_passed(
                    cycle_id=owner.cycle_dir.name, rationale=rationale, **metadata
                )
        else:
            raise ValueError("Goal Judge result has no valid terminal verdict")
        return self._apply(
            result,
            {
                **metadata,
                "snapshot_manifest": str(snapshot.manifest_path),
                "snapshot_manifest_sha256": snapshot.manifest_sha256,
            },
            seal,
        )
