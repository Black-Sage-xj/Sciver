from __future__ import annotations

"""Task-level Goal completion gate, triggered only by H-level support."""

import json
import hashlib
from pathlib import Path
from typing import Any

from .runtime import write_json


def goal_authority_digest(plan: Path, graph_path: Path) -> dict[str, str]:
    """Fingerprint the actual Goal authorities, not the Canvas projection."""
    if not plan.is_file() or not graph_path.is_file():
        return {}
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = store.graph_view()
    graph_digest = hashlib.sha256(json.dumps(
        graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "research_plan": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "science_graph": graph_digest,
    }


def verified_completed_goal_state(spec: Any, graph_path: Path) -> dict[str, Any] | None:
    """One authority check shared by Workflow and the Worker scheduler.

    A later, unfinished Cycle must not hide an earlier valid Goal verdict.
    Unverified historical ``passed`` strings are not sufficient evidence.
    """
    from .specification import latest_cycle_dir, list_cycle_dirs
    from .science_graph_store import ScienceGraphStore

    root = Path(spec.research_root)
    latest = latest_cycle_dir(root, task_mode=spec.task_mode)
    cycles = ([latest] if latest is not None else []) + list_cycle_dirs(root, task_mode=spec.task_mode)
    digest: dict[str, str] | None = None
    open_frontiers: int | None = None
    seen: set[Path] = set()
    for cycle in cycles:
        if cycle in seen:
            continue
        seen.add(cycle)
        try:
            state = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
            completion = state.get("goal_completion") if isinstance(state, dict) else None
            if not isinstance(completion, dict) or completion.get("verdict") != "passed":
                continue
            if str(completion.get("source_cycle") or "") != cycle.name:
                continue
            receipt = json.loads((cycle / "goal_completion_out.json").read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("verdict") != "passed":
                continue
        except (OSError, ValueError, TypeError):
            continue
        if digest is None:
            digest = goal_authority_digest(Path(spec.document), Path(graph_path))
        # Old receipts without authority fingerprints are not silently promoted.
        if not digest or completion.get("authority_digest") != digest:
            continue
        if open_frontiers is None:
            with ScienceGraphStore(graph_path, readonly=True) as store:
                open_frontiers = store.db.execute(
                    "SELECT COUNT(*) FROM frontier_branches WHERE status IN ('active','parked')"
                ).fetchone()[0]
        if open_frontiers == 0:
            return state
    return None


class GoalCompletionMixin:
    @staticmethod
    def _goal_completion_passed(state: dict[str, Any]) -> bool:
        result = state.get("goal_completion") if isinstance(state.get("goal_completion"), dict) else {}
        return str(result.get("verdict") or "") == "passed"

    def _goal_authority_digest(self) -> dict[str, str]:
        return goal_authority_digest(Path(self.spec.document), self._graph_database_path())

    def _latest_goal_completion_state(self) -> dict[str, Any] | None:
        # Kept as a public compatibility name; completion is task-wide, not latest-Cycle-only.
        from .goal_terminal import goal_sealed
        terminal = goal_sealed(self.spec.research_root, self._graph_database_path(), spec=self.spec)
        if terminal is None:
            return None
        if terminal["source"] == "legacy_receipt":
            return terminal["state"]
        return self._sealed_goal_state(terminal)

    def _sealed_goal_state(self, terminal: dict[str, Any]) -> dict[str, Any]:
        """research_state of the sealing Cycle, or a minimal view of the SQLite seal."""
        from .specification import find_cycle
        detail = terminal.get("detail") if isinstance(terminal.get("detail"), dict) else {}
        completion = {
            "verdict": "passed",
            "rationale": str(detail.get("rationale") or ""),
            "trigger": str(detail.get("trigger") or "judge_supported"),
            "source_cycle": str(terminal.get("source_cycle") or ""),
            "sealed_at": terminal.get("sealed_at"),
            "authority": "sqlite",
        }
        try:
            cycle = find_cycle(self.spec.research_root, completion["source_cycle"],
                               task_mode=self.spec.task_mode)
            state = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            state = None
        if not isinstance(state, dict):
            state = {"status": "over", "phase": "goal_complete", "resumable": False,
                     "execution_active": False}
        state["goal_status"] = "passed"
        state["goal_completion"] = {**(state.get("goal_completion") or {}), **completion} \
            if isinstance(state.get("goal_completion"), dict) else completion
        return state

    def _run_goal_completion(self, state: dict[str, Any], judge: dict[str, Any]) -> dict[str, Any] | None:
        """Run the fresh task-level Gate after a supported H and closed Cycle.

        N=1 and N>=2 share this path. The exclusive science barrier covers
        the Judge call and the SQLite seal, so the graph cannot change while
        the Goal is judged. Projection happens after the seal and never undoes it.
        """
        if str(judge.get("verdict") or "") != "supported":
            return None
        from .goal_terminal import goal_sealed, science_commit_barrier

        plan_path = self.spec.document
        if not plan_path.is_file():
            raise ValueError("goal_completion requires the authoritative research_plan.md")
        root = self.spec.research_root
        graph_path = self._graph_database_path()
        with science_commit_barrier(root, "exclusive"):
            sealed = goal_sealed(root, graph_path, spec=self.spec)
            if sealed is not None:
                state["goal_status"] = "passed"
                return {"verdict": "passed", "rationale": "another Cycle completed the Goal",
                        "source_cycle": str(sealed.get("source_cycle") or ""),
                        "source": sealed["source"]}
            self._emit(state, "goal_gate.triggered", {
                "trigger": "judge_supported",
                "source_cycle": str(self.cycle_dir.name),
            })
            raw_cycle_status = str(state.get("status") or "over").lower()
            if raw_cycle_status not in {"over", "completed"}:
                raise ValueError("goal_completion requires a closed Cycle")
            existing = state.setdefault("role_outputs", {}).get("goal_completion")
            if isinstance(existing, dict):
                result = existing
            else:
                result = self._call(
                    state,
                    "judge",
                    {
                        "mode": "goal_completion",
                        "research_plan_path": str(plan_path),
                    },
                    fresh_thread=True,
                    output_key="goal_completion",
                )
            receipt = self.cycle_dir / "goal_completion_out.json"
            write_json(receipt, {
                "verdict": result.get("verdict"),
                "rationale": result.get("rationale"),
            })
            verdict = str(result.get("verdict") or "")
            rationale = str(result.get("rationale") or "").strip()
            seal: dict[str, Any] | None = None
            if verdict == "passed":
                seal = self._seal_goal_in_sqlite(plan_path, graph_path, receipt, rationale)
            elif verdict == "not_passed":
                from .science_graph_store import ScienceGraphStore
                with ScienceGraphStore(graph_path) as store:
                    store.record_goal_gate_not_passed(
                        cycle_id=str(self.cycle_dir.name), rationale=rationale,
                    )
        return self._apply_goal_completion(state, result, verdict, rationale, seal)

    def _seal_goal_in_sqlite(self, plan_path: Path, graph_path: Path, receipt: Path,
                             rationale: str) -> dict[str, Any]:
        """Caller holds the exclusive science barrier; take canvas lock, then SQLite."""
        from .multi_worker import canvas_write_lock
        from .science_graph_store import ScienceGraphStore
        root = Path(self.spec.research_root)
        try:
            receipt_ref = receipt.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            receipt_ref = str(receipt)
        detail = {
            "rationale": rationale,
            "trigger": "judge_supported",
            "worker_id": getattr(self, "worker_id", None),
        }

        def seal() -> dict[str, Any]:
            with ScienceGraphStore(graph_path) as store:
                return store.seal_goal(
                    goal_id=str(getattr(self.spec, "goal_id", "") or "goal"),
                    source_cycle=str(self.cycle_dir.name),
                    research_plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                    judge_receipt_ref=receipt_ref,
                    detail=detail,
                )
        if getattr(self, "_canvas_write_locked", False):
            return seal()
        with canvas_write_lock(root):
            self._canvas_write_locked = True
            try:
                return seal()
            finally:
                self._canvas_write_locked = False

    def _apply_goal_completion(self, state: dict[str, Any], result: dict[str, Any], verdict: str,
                               rationale: str, seal: dict[str, Any] | None) -> dict[str, Any]:
        # `completed` is accepted only when reading a historical Cycle artifact.
        # Current Cycle persistence always normalizes the terminal spelling to `over`.
        cycle_terminal_status = "over"
        state.pop("remaining_goal_gap", None)
        state["goal_completion"] = {
            "verdict": verdict,
            "rationale": rationale,
            "trigger": "judge_supported",
            "source_cycle": str(self.cycle_dir.name),
        }
        if verdict == "passed":
            assert seal is not None
            state["goal_completion"]["sealed_at"] = seal.get("sealed_at")
            state["goal_completion"]["authority"] = "sqlite"
            state["next_start_node_id"] = None
            closed_ids = list(seal.get("closed_frontier_ids") or [])
            self._seal_frontier_after_task_completed(state)
            state["goal_status"] = "passed"
            state.update({
                "status": cycle_terminal_status,
                "phase": "goal_complete",
                "resumable": False,
                "execution_active": False,
            })
            try:
                self._close_open_frontier_tips()
            except Exception as exc:
                # The SQLite seal is authoritative; a projection is retryable.
                warning = {"kind": "goal_projection_failed", "retryable": True,
                           "error": f"{type(exc).__name__}: {exc}"}
                state.setdefault("goal_projection_warnings", []).append(warning)
                self._emit(state, "goal.projection_failed", warning)
            self._emit(state, "goal.completed", {
                "source_cycle": str(self.cycle_dir.name),
                "trigger": "judge_supported",
                "closed_frontier_ids": closed_ids,
                "goal_completed_stop_ids": list(seal.get("goal_completed_stop_ids") or []),
            })
            return result

        # not_passed allows another Cycle. It does not write a planner gap.
        state["goal_status"] = "active"
        state.update({
            "status": cycle_terminal_status,
            "phase": "complete",
            "resumable": False,
            "execution_active": False,
        })
        self._emit(state, "goal.not_completed", {
            "verdict": "not_passed",
            "rationale": rationale,
            "source_cycle": str(self.cycle_dir.name),
        })
        return result

    def _close_open_frontier_tips(self) -> list[str]:
        """Task COMPLETE: frontiers are closed in SQLite, then the Canvas is re-projected.

        After a SQLite seal the frontiers were already closed inside the seal
        transaction, so only a read-only projection runs. Legacy (unsealed)
        completion keeps the historical close-then-project behavior.
        """
        if not getattr(self, "_canvas_write_locked", False):
            from .multi_worker import canvas_write_lock
            with canvas_write_lock(self.spec.research_root):
                self._canvas_write_locked = True
                try:
                    return self._close_open_frontier_tips()
                finally:
                    self._canvas_write_locked = False
        canvas_path = self.branch_canvas or self.spec.branch_canvas
        graph_path = self._graph_database_path()
        from .goal_terminal import goal_sealed
        from .science_graph_store import GOAL_CLOSE_REASON, ScienceGraphStore
        from .science_canvas_export import canvas_projection
        closed: list[str] = []
        if goal_sealed(self.spec.research_root, graph_path) is None:
            with ScienceGraphStore(graph_path) as writable:
                closed = writable.close_all_frontiers_for_goal(reason=GOAL_CLOSE_REASON)
        with ScienceGraphStore(graph_path, readonly=True) as store:
            projected = canvas_projection(store)
            if canvas_path is not None:
                from .science_canvas_export import write_external_detail_documents
                write_external_detail_documents(
                    store, notes_root=canvas_path.resolve().parent / "notes"
                )
        if canvas_path is not None:
            write_json(canvas_path, projected)
        return list(dict.fromkeys(closed))

    def _render_canvas(self, state: dict[str, Any]) -> None:
        """After the SQLite Goal seal the Canvas is a read-only projection.

        The sealed graph rejects every write transaction, so the later Cycle
        checkpoints must not replay already committed frontier maintenance.
        A still-pending Cycle Outcome falls through and surfaces GoalSealedError.
        """
        if (self._goal_completion_passed(state)
                and (state.get("goal_completion") or {}).get("authority") == "sqlite"
                and not isinstance(state.get("cycle_outcome_record"), dict)):
            from .goal_terminal import goal_sealed
            if goal_sealed(self.spec.research_root, self._graph_database_path()) is not None:
                self._close_open_frontier_tips()
                state.setdefault("canvas_projection", {})["next_start_node_id"] = str(
                    state.get("next_start_node_id") or ""
                )
                return
        return super()._render_canvas(state)  # type: ignore[misc]

    def _seal_frontier_after_task_completed(self, state: dict[str, Any]) -> None:
        """A completed task has no open SQLite frontier or continuation pointer."""
        state["next_start_node_id"] = None
        state.pop("frontier_state", None)
