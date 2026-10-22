"""Supervisor takeover: merged switch, two profiles, visible session (no real LLM)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from theory_master.backends import FakeBackend
from theory_master.internal_attention_review import review_attention_in_sdk
from theory_master.operator_preferences import write as write_pref
from theory_master.runtime_supervisor_review import review_runtime_failure
from theory_master.specification import TaskSpec
from theory_master.supervisor import _handle_failed_state
from theory_master.supervisor_intervention import (
    KEY, LEGACY_ATTENTION_KEY, supervisor_enabled, supervisor_model, write_supervisor_setting,
)
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


def _rows(cycle: Path) -> list[dict]:
    path = cycle / "supervisor" / "session.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _status(cycle: Path) -> dict:
    return json.loads((cycle / "supervisor" / "status.json").read_text(encoding="utf-8"))


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in (KEY, LEGACY_ATTENTION_KEY, "THEORY_MASTER_ERROR_REPAIR"):
            os.environ.pop(key, None)

    def tearDown(self):
        self._env.stop()

    def test_default_on_and_legacy_explicit_off_keeps_task_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            self.assertTrue(supervisor_enabled(root))
            (root / ".theory-master" / "env").write_text(f"{LEGACY_ATTENTION_KEY}=0\n")
            self.assertFalse(supervisor_enabled(root))
            (root / ".theory-master" / "env").write_text(f"{LEGACY_ATTENTION_KEY}=1\n")
            write_pref(root, kind="flag", key="error_repair", value=False)
            self.assertFalse(supervisor_enabled(root))
            write_supervisor_setting(root, True)
            self.assertTrue(supervisor_enabled(root))
            text = (root / ".theory-master" / "env").read_text()
            self.assertIn(f"{KEY}=1", text)
            self.assertNotIn(LEGACY_ATTENTION_KEY, text)
            write_supervisor_setting(root, False)
            self.assertFalse(supervisor_enabled(root))

    def test_model_prefers_supervisor_then_legacy_attention_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".theory-master").mkdir()
            self.assertEqual(supervisor_model(root), "gpt-6-luna")
            write_pref(root, kind="model", key="attention_review", value="gpt-6-sol")
            self.assertEqual(supervisor_model(root), "gpt-6-sol")
            write_pref(root, kind="model", key="supervisor", value="gpt-6.1-sol")
            self.assertEqual(supervisor_model(root), "gpt-6.1-sol")

    def test_supervisor_roles_explicitly_use_medium_effort(self):
        from theory_master.codex_backend import CodexBackend

        backend = object.__new__(CodexBackend)
        backend.routes = {}
        for role in ("runtime_supervisor", "attention_reviewer", "incident_supervisor"):
            with self.subTest(role=role):
                self.assertEqual(
                    backend._thread_config(role, "CodexHTTPS")["model_reasoning_effort"],
                    "medium",
                )


class AttentionProfileTests(unittest.TestCase):
    def make(self, root: Path):
        plan = root / "research_plan.md"
        plan.write_text("# Goal\ng\n\n# Research Contract\nc\n\n# Final Gate\nf\n\n# Required Context\n- r\n")
        binding = bootstrap(plan)
        wf = Workflow(TaskSpec.from_config(binding.config_path), FakeBackend(reworks=0))
        wf._ensure_cycle()
        return wf, wf._new_state()

    def test_hard_guard_records_system_hold_without_model(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.make(Path(temp))
            state.update(status="needs_attention", phase="investigator:budget",
                         attention={"code": "cycle_cost_budget_exhausted", "reason": "spent"})
            wf.backend.invoke = MagicMock()
            result = review_attention_in_sdk(backend=wf.backend, cycle_dir=wf.cycle_dir, state=state,
                                             goal="g", research_contract="c", final_gate="f")
            wf.backend.invoke.assert_not_called()
            self.assertEqual(result["action"], "hold")
            kinds = [row["kind"] for row in _rows(wf.cycle_dir)]
            self.assertEqual(kinds, ["opened", "evidence", "system", "decision"])
            self.assertTrue(all(row["profile"] == "attention" for row in _rows(wf.cycle_dir)))
            self.assertEqual(_status(wf.cycle_dir)["status"], "hold")

    def test_retry_records_prompt_reply_and_returns_to_science(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.make(Path(temp))
            state.update(status="needs_attention", phase="qualification",
                         hypothesis_lock={"status": "unlocked"},
                         attention={"code": "no_qualified_candidate", "reason": "rejected"})
            answer = json.dumps({"action": "retry_qualification",
                                 "reason": "A new Goal-aligned candidate could pass review.",
                                 "guidance": "Compare competitive Goal-aligned directions under the original contract."})
            wf.backend.invoke = MagicMock(return_value=("t1", answer))
            result = review_attention_in_sdk(backend=wf.backend, cycle_dir=wf.cycle_dir, state=state,
                                             goal="g", research_contract="c", final_gate="f")
            self.assertEqual(result["action"], "retry_qualification")
            kinds = [row["kind"] for row in _rows(wf.cycle_dir)]
            self.assertEqual(kinds, ["opened", "evidence", "prompt", "model_output", "decision"])
            self.assertEqual(_status(wf.cycle_dir)["status"], "returned")
            receipt = next((wf.cycle_dir / "attention").glob("sdk_internal_review_*.json"))
            self.assertEqual(json.loads(receipt.read_text())["supervisor_session"],
                             _status(wf.cycle_dir)["session_id"])


class RuntimeProfileTests(unittest.TestCase):
    def cycle(self, root: Path, **state):
        cycle = root / "7"
        cycle.mkdir(parents=True)
        (cycle / ".theory-master-cycle").write_text("theory-master/v2\ntest\n")
        base = {"status": "failed", "phase": "failed", "resumable": True,
                "role_outputs": {"research_lead": {"status": "committed"}},
                "recovery": {"action": "error", "kind": "TimeoutError", "message": "provider timed out"}}
        base.update(state)
        (cycle / "research_state.json").write_text(json.dumps(base))
        (cycle / "run_state.json").write_text(json.dumps({"run_id": "7"}))
        return cycle, base

    def test_no_checkpoint_holds_without_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle, state = self.cycle(root, resumable=False, role_outputs={})
            backend = MagicMock()
            result = review_runtime_failure(backend=backend, research_root=root, cycle_dir=cycle,
                                            state=state, state_path=cycle / "research_state.json",
                                            incident_id="INC-" + "a" * 16)
            backend.invoke_runtime_review.assert_not_called()
            self.assertEqual(result["action"], "hold")
            self.assertEqual(_status(cycle)["profile"], "runtime")
            self.assertEqual(_status(cycle)["status"], "hold")

    def test_resume_once_then_recurrence_holds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle, state = self.cycle(root)
            backend = MagicMock()
            backend.invoke_runtime_review.return_value = ("rt-1", json.dumps({
                "action": "resume", "reason": "Transient provider timeout; checkpoint is intact.",
                "diagnosis": "timeout", "repair_suggestion": ""}))
            first = review_runtime_failure(backend=backend, research_root=root, cycle_dir=cycle,
                                           state=state, state_path=cycle / "research_state.json",
                                           incident_id="INC-" + "a" * 16)
            self.assertEqual(first["action"], "resume")
            self.assertEqual(_status(cycle)["status"], "returned")
            saved = json.loads((cycle / "research_state.json").read_text())
            self.assertEqual(saved["supervisor_runtime_resume_count"], 1)
            from theory_master.supervisor_intervention import MAX_RUNTIME_RESUMES
            for _ in range(MAX_RUNTIME_RESUMES - 1):
                returned = review_runtime_failure(backend=backend, research_root=root, cycle_dir=cycle,
                                                   state=saved, state_path=cycle / "research_state.json",
                                                   incident_id="INC-" + "a" * 16)
                self.assertEqual(returned["action"], "resume")
            final = review_runtime_failure(backend=backend, research_root=root, cycle_dir=cycle,
                                           state=saved, state_path=cycle / "research_state.json",
                                           incident_id="INC-" + "a" * 16)
            self.assertEqual(final["action"], "hold")
            self.assertEqual(backend.invoke_runtime_review.call_count, MAX_RUNTIME_RESUMES)
            self.assertEqual(_status(cycle)["status"], "hold")
            prompts = [row for row in _rows(cycle) if row["kind"] == "prompt"]
            self.assertEqual(len(prompts), MAX_RUNTIME_RESUMES)
            self.assertIn("runtime_supervisor", prompts[0]["role"])

    def test_invalid_model_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle, state = self.cycle(root)
            backend = MagicMock()
            backend.invoke_runtime_review.return_value = ("rt-1", "resume please")
            result = review_runtime_failure(backend=backend, research_root=root, cycle_dir=cycle,
                                            state=state, state_path=cycle / "research_state.json",
                                            incident_id="INC-" + "a" * 16)
            self.assertEqual(result["action"], "hold")

    def test_supervisor_failed_state_resumes_through_runtime_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            cycle, _ = self.cycle(root)
            backend = MagicMock()
            backend.invoke_runtime_review.return_value = ("rt-1", json.dumps({
                "action": "resume", "reason": "Transient provider timeout; checkpoint is intact."}))
            with patch("theory_master.supervisor._supervisor_backend", return_value=backend):
                self.assertTrue(_handle_failed_state(root, repair_enabled=True))
            handoff = json.loads((cycle / "run_state.json").read_text())["repair_handoff"]
            self.assertEqual(handoff["status"], "supervisor_resume")
            from theory_master.supervisor_intervention import MAX_RUNTIME_RESUMES
            for i in range(MAX_RUNTIME_RESUMES - 1):
                current = json.loads((cycle / "research_state.json").read_text())
                current['recovery']['at'] = 'new-failure-' + str(i)
                (cycle / "research_state.json").write_text(json.dumps(current))
                with patch("theory_master.supervisor._supervisor_backend", return_value=backend):
                    self.assertTrue(_handle_failed_state(root, repair_enabled=True))
            with patch("theory_master.supervisor._supervisor_backend", return_value=backend):
                self.assertFalse(_handle_failed_state(root, repair_enabled=True))
            self.assertEqual(backend.invoke_runtime_review.call_count, MAX_RUNTIME_RESUMES)

    def test_disabled_switch_never_opens_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            cycle, _ = self.cycle(root)
            with patch("theory_master.supervisor._supervisor_backend") as factory:
                self.assertFalse(_handle_failed_state(root, repair_enabled=False))
            factory.assert_not_called()
            self.assertFalse((cycle / "supervisor").exists())


if __name__ == "__main__":
    unittest.main()
