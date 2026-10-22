"""MW-E: Scientific Model CAS conflicts become durable pending updates (I-08).

A conflicted synthesis is never lost: it is persisted, injected into the next
cycle_synthesis, must be explicitly disposed of, and the disposition is durable.
"""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.contract_closure import close_agent_output
from theory_master.contract_stages import PHASE_CONTRACTS
from theory_master.goal_terminal import GoalSealedError
from theory_master.output_validation import validate_agent_output
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import (
    ScientificModelVersionConflict,
    commit_authored_model,
    list_pending,
    load,
    pending_path,
)
from theory_master.task import bootstrap
from theory_master.workflow_science import WorkflowScienceMixin


def make_task(base: Path):
    root = base / "task"
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text("# Goal\nG\n\n# Research Contract\nC\n\n# Final Gate\nF\n\n"
                    "# Required Context\n- stable/context.md\n", encoding="utf-8")
    return bootstrap(plan)


def replace(summary: str, dispositions=None) -> dict:
    output = {"scientific_synthesis": {"conclusions": [summary], "open_questions": []},
              "scientific_model_update": {"action": "replace", "model": {"summary": summary}}}
    if dispositions is not None:
        output["pending_dispositions"] = dispositions
    return output


class Harness(WorkflowScienceMixin):
    def __init__(self, research_root: Path, goal_id: str, cycle: str, *,
                 worker_id: str | None = "worker-1", lead=None, before_call=None):
        self.spec = SimpleNamespace(research_root=research_root, goal_id=goal_id)
        self.cycle_dir = research_root / cycle
        self.cycle_dir.mkdir(exist_ok=True)
        if worker_id:
            self.worker_id = worker_id
        self.lead = lead
        self.before_call = before_call
        self.contexts: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.routes = {"research_lead": SimpleNamespace()}

    def _graph_database_path(self) -> Path:
        return self.spec.research_root / "science_graph.sqlite3"

    def _evidence_context(self):
        return {"evidence_manifest": {"records": []}}

    def _call(self, state, role, context, **_kw):
        self.contexts.append(context)
        if self.before_call:
            self.before_call()
        return self.lead(context) if callable(self.lead) else self.lead

    def _emit(self, state, kind, payload):
        self.events.append((kind, payload))

    def _persist(self, state, **_kw):
        pass

    def _save_research_lead_state(self, **_kw):
        pass

    def synthesize(self) -> dict:
        state: dict = {"threads": {}}
        self._scientific_synthesis_commit(state, {}, {"verdict": "supported", "rationale": "ok"})
        return state


def _stage_in(harness: Harness) -> None:
    """Freeze the model this Cycle read (scientific_model_in.json) before the race."""
    from theory_master.runtime import write_json
    write_json(harness.cycle_dir / "scientific_model_in.json",
               load(harness.spec.research_root, goal_id=harness.spec.goal_id))


class PendingConflictTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.binding = make_task(Path(self._temp.name))
        self.root = self.binding.research_root
        self.goal = self.binding.goal_id

    def tearDown(self):
        self._temp.cleanup()

    def _conflict(self) -> tuple[Harness, dict]:
        """Cycle 2 read v0, Cycle 1 commits v1 first, Cycle 2 conflicts."""
        loser = Harness(self.root, self.goal, "2", lead=replace("Loser synthesis."))
        _stage_in(loser)
        Harness(self.root, self.goal, "1", lead=replace("Winner synthesis.")).synthesize()
        return loser, loser.synthesize()

    def test_cas_conflict_creates_durable_pending_record(self):
        loser, state = self._conflict()
        self.assertEqual(load(self.root)["summary"], "Winner synthesis.")
        self.assertEqual(state["scientific_model_update_status"], "deferred_conflict")
        conflict = state["scientific_model_conflict"]
        self.assertEqual(conflict["pending_id"], "smp-cycle-2")
        self.assertEqual(conflict["basis_version"], 0)
        self.assertEqual(conflict["current_version"], 1)
        self.assertIn("scientific_model.update_deferred", [kind for kind, _ in loser.events])
        registry = json.loads(pending_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(registry["schema"], "theory-master/scientific-model-pending/v1")
        (item,) = registry["items"]
        self.assertEqual({k: item[k] for k in ("id", "cycle", "worker", "base_version",
                                               "current_version_at_conflict", "status")},
                         {"id": "smp-cycle-2", "cycle": "2", "worker": "worker-1", "base_version": 0,
                          "current_version_at_conflict": 1, "status": "pending"})
        self.assertEqual(item["update"]["model"]["summary"], "Loser synthesis.")
        for key in ("disposition", "resolved_by_cycle", "resolved_at"):
            self.assertIsNone(item[key])
        # Crash-safe replay of the conflicted Cycle does not duplicate the record.
        loser.synthesize()
        self.assertEqual(len(list_pending(self.root, status=None)), 1)

    def test_pending_is_injected_into_next_synthesis_context(self):
        self._conflict()
        nxt = Harness(self.root, self.goal, "3", lead=replace("Merged.", [
            {"pending_id": "smp-cycle-2", "status": "integrated", "rationale": "kept its bound"}]))
        nxt.synthesize()
        pending = nxt.contexts[0]["scientific_model_pending"]
        self.assertEqual([item["pending_id"] for item in pending["items"]], ["smp-cycle-2"])
        self.assertEqual(pending["items"][0]["update"]["model"]["summary"], "Loser synthesis.")
        self.assertIn("pending_dispositions", pending["rule"])
        frozen = json.loads((nxt.cycle_dir / "scientific_model_pending_in.json").read_text(encoding="utf-8"))
        self.assertEqual([item["pending_id"] for item in frozen], ["smp-cycle-2"])

    def test_no_pending_means_no_context_field_and_no_disposition_required(self):
        harness = Harness(self.root, self.goal, "1", lead=replace("Only synthesis."))
        harness.synthesize()
        self.assertNotIn("scientific_model_pending", harness.contexts[0])

    def test_missing_disposition_is_rejected_and_nothing_is_lost(self):
        self._conflict()
        nxt = Harness(self.root, self.goal, "3", lead=replace("Merged."))
        with self.assertRaisesRegex(ValueError, "lack a disposition: smp-cycle-2"):
            nxt.synthesize()
        self.assertEqual([item["id"] for item in list_pending(self.root)], ["smp-cycle-2"])

    def test_validator_rejects_missing_unknown_and_invalid_dispositions(self):
        contract = PHASE_CONTRACTS[("research_lead", "cycle_synthesis")]
        context = {"scientific_model": load(self.root),
                   "scientific_model_pending": {"rule": "x", "items": [{"pending_id": "smp-cycle-2"}]}}
        state = {"cycle_id": "3"}

        def codes(output):
            result = validate_agent_output("research_lead", "cycle_synthesis", output, state, context, contract)
            return [error.code for error in result.errors]

        self.assertIn("pending_disposition_invalid", codes(replace("S.")))
        self.assertIn("pending_disposition_invalid", codes(replace("S.", [
            {"pending_id": "smp-cycle-9", "status": "rejected", "rationale": "r"}])))
        self.assertIn("pending_disposition_invalid", codes(replace("S.", [
            {"pending_id": "smp-cycle-2", "status": "kept", "rationale": "r"}])))
        self.assertEqual(codes(replace("S.", [
            {"pending_id": "smp-cycle-2", "status": "superseded", "rationale": "covered"}])), [])
        # Without injected items the field stays optional.
        self.assertEqual(codes_no_pending(contract, self.root), [])

    def test_closure_preserves_pending_dispositions(self):
        output = replace("S.", [{"pending_id": "smp-cycle-2", "status": "rejected", "rationale": "r"}])
        closed = close_agent_output("research_lead", "cycle_synthesis", output, {}, {})
        self.assertEqual(closed["pending_dispositions"], output["pending_dispositions"])

    def test_dispositions_applied_and_persisted_after_successful_save(self):
        self._conflict()
        nxt = Harness(self.root, self.goal, "3", lead=replace("Merged.", [
            {"pending_id": "smp-cycle-2", "status": "integrated", "rationale": "kept its bound"}]))
        state = nxt.synthesize()
        self.assertEqual(load(self.root)["summary"], "Merged.")
        self.assertEqual(list_pending(self.root), [])
        (item,) = list_pending(self.root, status=None)
        self.assertEqual(item["status"], "integrated")
        self.assertEqual(item["disposition"], {"status": "integrated", "rationale": "kept its bound"})
        self.assertEqual(item["resolved_by_cycle"], "3")
        self.assertIsNotNone(item["resolved_at"])
        self.assertTrue(state["scientific_model_pending_dispositions"][0]["applied"])
        # Replay of the resolving Cycle is idempotent.
        nxt.synthesize()
        self.assertEqual(list_pending(self.root, status=None)[0]["resolved_at"], item["resolved_at"])

    def test_conflicted_synthesis_does_not_apply_its_dispositions(self):
        self._conflict()
        # Cycle 3 commits while Cycle 4's Lead is still synthesizing over v1.
        other = Harness(self.root, self.goal, "3", lead=replace("Merged.", [
            {"pending_id": "smp-cycle-2", "status": "integrated", "rationale": "kept"}]))
        late = Harness(self.root, self.goal, "4", lead=replace("Late.", [
            {"pending_id": "smp-cycle-2", "status": "rejected", "rationale": "r"}]),
            before_call=other.synthesize)
        state = late.synthesize()
        self.assertEqual(state["scientific_model_update_status"], "deferred_conflict")
        items = {item["id"]: item for item in list_pending(self.root, status=None)}
        self.assertEqual(items["smp-cycle-2"]["status"], "integrated")
        self.assertEqual(items["smp-cycle-2"]["resolved_by_cycle"], "3")
        self.assertEqual(items["smp-cycle-4"]["status"], "pending")

    def test_single_runner_without_worker_also_disposes_pending(self):
        self._conflict()
        nxt = Harness(self.root, self.goal, "3", worker_id=None, lead=replace("N1.", [
            {"pending_id": "smp-cycle-2", "status": "superseded", "rationale": "covered"}]))
        nxt.synthesize()
        self.assertEqual(list_pending(self.root, status=None)[0]["status"], "superseded")

    def test_save_after_goal_seal_raises_goal_sealed(self):
        with ScienceGraphStore(self.root / "science_graph.sqlite3") as store:
            store.seal_goal(goal_id=self.goal, source_cycle="9", research_plan_sha256="0" * 64,
                            judge_receipt_ref=None, detail={})
        harness = Harness(self.root, self.goal, "1", lead=replace("Late."))
        with self.assertRaises(GoalSealedError):
            harness.synthesize()
        self.assertEqual(load(self.root)["version"], 0)

    def test_crash_after_save_does_not_self_conflict(self):
        commit_authored_model(self.root, {"summary": "Mine."}, cycle_id="5", action="replace",
                              expected_version=0)
        again = commit_authored_model(self.root, {"summary": "Mine."}, cycle_id="5", action="replace",
                                      expected_version=0)
        self.assertEqual(again["version"], 1)
        with self.assertRaises(ScientificModelVersionConflict):
            commit_authored_model(self.root, {"summary": "Other."}, cycle_id="6", action="replace",
                                  expected_version=0)


def codes_no_pending(contract, root):
    output = replace("S.")
    result = validate_agent_output("research_lead", "cycle_synthesis", output, {"cycle_id": "3"},
                                   {"scientific_model": load(root)}, contract)
    return [error.code for error in result.errors]


def _race_worker(root: str, goal: str, cycle: str, worker: str, barrier, out: str) -> None:
    harness = Harness(Path(root), goal, cycle, worker_id=worker,
                      lead=replace(f"Synthesis from {cycle}."),
                      before_call=lambda: barrier.wait(30))
    try:
        state = harness.synthesize()
        result = {"status": state["scientific_model_update_status"],
                  "pending_id": (state.get("scientific_model_conflict") or {}).get("pending_id")}
    except BaseException as exc:  # pragma: no cover - surfaced through the parent
        result = {"error": f"{type(exc).__name__}: {exc}"}
    Path(out).write_text(json.dumps(result), encoding="utf-8")


class TwoProcessRaceTest(unittest.TestCase):
    def test_concurrent_updates_never_lose_a_synthesis(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as temp:
            binding = make_task(Path(temp))
            root = binding.research_root
            barrier = ctx.Barrier(2)
            outs = [Path(temp) / f"out-{n}.json" for n in (1, 2)]
            procs = [ctx.Process(target=_race_worker,
                                 args=(str(root), binding.goal_id, str(n), f"worker-{n}", barrier, str(outs[n - 1])))
                     for n in (1, 2)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(60)
                self.assertEqual(proc.exitcode, 0)
            results = [json.loads(path.read_text(encoding="utf-8")) for path in outs]
            for result in results:
                self.assertNotIn("error", result, result)
            statuses = sorted(result["status"] for result in results)
            self.assertEqual(statuses, ["committed", "deferred_conflict"])
            model = load(root)
            self.assertEqual(model["version"], 1)
            winner = next(n for n, r in zip((1, 2), results) if r["status"] == "committed")
            loser = 3 - winner
            self.assertEqual(model["summary"], f"Synthesis from {winner}.")
            (pending,) = list_pending(root)
            self.assertEqual(pending["id"], f"smp-cycle-{loser}")
            self.assertEqual(pending["update"]["model"]["summary"], f"Synthesis from {loser}.")


if __name__ == "__main__":
    unittest.main()
