from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.contracts import role_contract
from theory_master.core import FakeBackend, TaskSpec, Workflow, list_cycle_dirs
from theory_master.task import bootstrap


def _stage(prompt: str) -> str:
    marker = "Current task: "
    if marker not in prompt:
        return ""
    return prompt.split(marker, 1)[1].split(" ", 1)[0].strip()


def _context(prompt: str) -> dict:
    if "Context:\n" not in prompt:
        return {}
    return json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]


def _make_task(root: Path, *, max_cycles: int = 3) -> TaskSpec:
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\n\nComplete the deterministic Goal Gate test.\n\n"
        "# Research Contract\n\nUse only the task-local fixture.\n\n"
        "# Final Gate\n\nThe deterministic scientific result and its reproducible record exist.\n\n"
        "# Required Context\n\n- task-local fixture\n",
        encoding="utf-8",
    )
    binding = bootstrap(plan)
    config = json.loads(binding.config_path.read_text(encoding="utf-8"))
    config["max_cycles"] = max_cycles
    binding.config_path.write_text(json.dumps(config), encoding="utf-8")
    return TaskSpec.from_config(binding.config_path)


class PassingGoalGateBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.judge_threads: list[str] = []
        self.goal_context: dict = {}
        self.goal_prompt = ""
        self.graph_hypotheses_at_gate = 0

    def invoke(self, role, prompt, thread_id, model, provider):
        stage = _stage(prompt)
        if role == "judge":
            self.judge_threads.append(str(thread_id or ""))
        if role == "judge" and stage == "goal_completion":
            self.goal_context = _context(prompt)
            self.goal_prompt = prompt
            graph = self.goal_context["agent_graph"]
            self.graph_hypotheses_at_gate = len(graph.get("hypotheses") or [])
            return str(thread_id), json.dumps({
                "verdict": "passed",
                "rationale": "the trajectory satisfies the original Goal Contract and Final Gate",
            })
        return super().invoke(role, prompt, thread_id, model, provider)


class MilestoneOnlyBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.goal_calls = 0

    def invoke(self, role, prompt, thread_id, model, provider):
        stage = _stage(prompt)
        if role == "judge" and stage == "goal_completion":
            self.goal_calls += 1
        if role == "judge" and stage == "terminal_assessment":
            return str(thread_id), json.dumps({
                "verdict": "milestone_advanced",
                "rationale": "the local test is complete but the hypothesis remains open",
                "objection": None,
            })
        return super().invoke(role, prompt, thread_id, model, provider)


class GoalCompletionGateTest(unittest.TestCase):
    def test_contract_is_minimal_and_conditional(self):
        contract = role_contract("judge", phase="goal_completion")
        self.assertEqual(contract.version, 2)
        contract.validate({
            "verdict": "passed",
            "rationale": "all completion-critical Goal Contract requirements are satisfied",
        }, {})
        contract.validate({
            "verdict": "not_passed",
            "rationale": "the requested final prediction has not been produced",
        }, {})
        with self.assertRaisesRegex(ValueError, "rationale"):
            contract.validate({"verdict": "passed"}, {})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            contract.validate({
                "verdict": "passed",
                "rationale": "the Goal Contract is satisfied",
                "remaining_gap": "extra",
            }, {})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            contract.validate({
                "verdict": "not_passed",
                "rationale": "a required deliverable is missing",
                "remaining_gap": "one required deliverable remains",
            }, {})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            contract.validate({
                "verdict": "passed",
                "rationale": "complete",
                "requirements": [],
            }, {})

    def test_supported_triggers_fresh_goal_gate_after_sqlite_outcome_and_passes_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            root.mkdir()
            spec = _make_task(root, max_cycles=3)
            backend = PassingGoalGateBackend()

            state = Workflow(spec, backend).run()

            self.assertEqual(state["status"], "over")
            self.assertEqual(state["runtime_state"], "over")
            self.assertEqual(state["goal_status"], "passed")
            self.assertEqual(state["phase"], "goal_complete")
            self.assertEqual(state["goal_completion"]["verdict"], "passed")
            from theory_master.observability.projection import project_from_scientific
            self.assertEqual(project_from_scientific(spec.research_root)["status"], "COMPLETE")
            self.assertEqual(
                state["goal_completion"]["rationale"],
                "the trajectory satisfies the original Goal Contract and Final Gate",
            )
            self.assertEqual(len(list_cycle_dirs(spec.research_root, task_mode=True)), 1)
            self.assertEqual(set(backend.goal_context), {"research_plan_access", "agent_graph"})
            self.assertEqual(Path(backend.goal_context["research_plan_access"]["path"]), spec.document)
            self.assertGreater(backend.graph_hypotheses_at_gate, 0)
            self.assertNotIn("canvas_access", backend.goal_context)
            self.assertIn("Goal + Research Contract + Final Gate + Required Context", backend.goal_prompt)
            self.assertIn("A supported H or Cycle is never sufficient by itself", backend.goal_prompt)
            self.assertIn("concrete deliverable against the Final Gate", backend.goal_prompt)
            self.assertIn("Do not add requirement ledgers", backend.goal_prompt)
            self.assertIn("optional robustness, external generalization", backend.goal_prompt)
            self.assertGreaterEqual(len(backend.judge_threads), 2)
            self.assertNotEqual(backend.judge_threads[-2], backend.judge_threads[-1])
            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            self.assertTrue(all(node.get("id", "").startswith(("H-", "external-"))
                                for node in canvas.get("nodes") or []))
            from theory_master.science_graph_store import ScienceGraphStore
            with ScienceGraphStore(spec.branch_canvas.with_name("science_graph.sqlite3"), readonly=True) as store:
                outcomes = [row for node in store.graph_view()["nodes"] if node["type"] == "Hypothesis"
                            for row in node.get("cycle_outcomes") or []]
                self.assertTrue(any(row.get("id") == "cycle-outcome-1" for row in outcomes))
                self.assertTrue(all(row["frontier_status"] == "closed" for row in store.frontier_view().values()))
            continuation = (state.get("frontier_state") or {}).get("continuation_context") or {}
            self.assertNotIn("remaining_goal_gap", continuation)
            self.assertNotIn("scientific_synthesis", continuation)

    def test_non_supported_terminal_verdict_does_not_trigger_goal_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            root.mkdir()
            spec = _make_task(root, max_cycles=1)
            backend = MilestoneOnlyBackend()

            state = Workflow(spec, backend).run()

            self.assertEqual(state["status"], "over")
            self.assertEqual(backend.goal_calls, 0)
            cycle = list_cycle_dirs(spec.research_root, task_mode=True)[-1]
            self.assertFalse((cycle / "goal_completion_out.json").exists())

    def test_not_passed_records_verdict_without_planner_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            root.mkdir()
            spec = _make_task(root, max_cycles=1)

            state = Workflow(spec, FakeBackend(reworks=0)).run()

            self.assertEqual(state["status"], "over")
            self.assertEqual(state["goal_completion"]["verdict"], "not_passed")
            self.assertEqual(
                state["goal_completion"]["rationale"],
                "the deterministic smoke trajectory does not yet satisfy the task Final Gate",
            )
            self.assertEqual(state["goal_status"], "active")
            self.assertNotIn("remaining_goal_gap", state)
            # V2 does not keep a parallel, mutable frontier_state cache.
            self.assertNotIn("frontier_state", state)
            self.assertNotIn("remaining_gap", state["goal_completion"])
            canvas = json.loads(spec.branch_canvas.read_text(encoding="utf-8"))
            self.assertTrue(all("type: Decision" not in str(node.get("text") or "")
                                for node in canvas.get("nodes") or []))
            receipt_path = list_cycle_dirs(spec.research_root, task_mode=True)[-1] / "goal_completion_out.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["verdict"], "not_passed")
            self.assertEqual(receipt["rationale"], state["goal_completion"]["rationale"])
            cycle = list_cycle_dirs(spec.research_root, task_mode=True)[-1]
            events = (cycle / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("goal.not_completed", events)
            self.assertNotIn("remaining_goal_gap", events)


if __name__ == "__main__":
    unittest.main()
