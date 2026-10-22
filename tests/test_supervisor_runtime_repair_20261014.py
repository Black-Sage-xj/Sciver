"""Offline tests for Supervisor isolated repair; all repair model calls mocked."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master import supervisor_runtime_repair as repair
from theory_master.runtime_supervisor_review import review_runtime_failure


class Session:
    def __init__(self):
        self.rows = []

    def append(self, kind, content, **fields):
        self.rows.append((kind, content))


def fixture(root: Path) -> tuple[Path, Path, Path, dict]:
    engine = root / "tool" / "theory_master" / "theory_master"
    tests = root / "tool" / "theory_master" / "tests"
    engine.mkdir(parents=True)
    tests.mkdir(parents=True)
    target = engine / "canvas_projection.py"
    target.write_text("VALUE = 'broken'\n", encoding="utf-8")
    (engine / "other.py").write_text("OTHER = True\n", encoding="utf-8")
    for name in repair._TESTS:
        (tests / name).write_text("def test_offline():\n    assert True\n", encoding="utf-8")
    cycle = root / "task" / "research" / "87"
    cycle.mkdir(parents=True)
    log = cycle.parents[1] / ".theory-master" / "runner.log"
    log.parent.mkdir(parents=True)
    error = "invalid Canvas node"
    log.write_text(
        'Traceback (most recent call last):\n'
        f'  File "{target}", line 1, in projection\n'
        f'ValueError: {error}\n', encoding="utf-8")
    state = {"status": "failed", "resumable": True,
             "protocol_health": "violation", "role_outputs": {"research_lead": {"done": True}},
             "recovery": {"kind": "ValueError", "message": error}}
    return target, cycle, log, state


class RuntimeRepairTests(unittest.TestCase):
    def test_attribution_is_strict(self):
        with tempfile.TemporaryDirectory() as td:
            target, cycle, log, state = fixture(Path(td))
            content = log.read_text()
            self.assertEqual(repair.repair_target(content, state, target.parent), target)
            self.assertIsNone(repair.repair_target(content, {
                **state, "recovery": {"kind": "ValueError", "message": "another incident"},
            }, target.parent))
            self.assertIsNone(repair.repair_target(content, {
                **state, "recovery": {"kind": "AuthenticationError", "message": "invalid Canvas node"},
            }, target.parent))
            target.unlink()
            self.assertIsNone(repair.repair_target(content, state, target.parent))

    def test_candidate_promoted_only_after_scoped_tested_patch(self):
        with tempfile.TemporaryDirectory() as td:
            target, cycle, log, state = fixture(Path(td))
            path = cycle / "research_state.json"
            state["protocol_health"] = "valid"
            session = Session()
            def change(sandbox, model, prompt):
                staged = sandbox / "tool/theory_master/theory_master/canvas_projection.py"
                staged.write_text("VALUE = 'fixed'\n", encoding="utf-8")
                return True, "one-line repair"
            with patch.object(repair, "__file__", str(target.parent/"supervisor_runtime_repair.py")), \
                 patch.object(repair, "_invoke_codex", side_effect=change) as codex, \
                 patch.object(repair, "_register_auto_change", return_value="TMC-test"), \
                 patch.object(repair, "_verify_auto_change", return_value=True), \
                 patch.object(repair, "_promote_patch", wraps=repair._promote_patch) as promote:
                result = repair.attempt_runtime_repair(
                    root=log.parents[1], cycle_dir=cycle, state=state,
                    state_path=path, session=session, model="test", incident_id="id-1")
            self.assertEqual(result["action"], "resume")
            self.assertEqual(promote.call_count, 1)
            self.assertEqual(state["supervisor_runtime_repair_count"], 1)
            self.assertEqual(state["supervisor_runtime_repair"]["change_id"], "TMC-test")
            self.assertEqual(state["supervisor_runtime_repair"]["status"],
                             "validated_patch_pending_runtime_verification")
            self.assertEqual(codex.call_count, 1)
            # No task-local science file was changed.
            self.assertEqual(target.read_text(), "VALUE = 'fixed'\n")

    def test_each_installed_patch_uses_official_cli_change_registry(self):
        from theory_master import change_register
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "change_register.json"
            with patch.object(change_register, "DEFAULT_STORE", store):
                ident = repair._register_auto_change(
                    incident_id="mock-incident-1", cycle_dir=Path(td)/"87",
                    relative=Path("theory_master/canvas_projection.py"),
                    before=b"before", after=b"after")
                self.assertTrue(ident.startswith("TMC-"))
                self.assertTrue(repair._verify_auto_change(
                    ident, tests="3 offline tests passed", after=b"after"))
            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["state"], "verified")
            self.assertIn("installed_sha256=", data["items"][0]["verification"])

    def test_promotion_uses_compare_and_swap(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "canvas_projection.py"
            source.write_bytes(b"original\n")
            self.assertFalse(repair._promote_patch(source, b"stale\n", b"fixed\n"))
            self.assertEqual(source.read_bytes(), b"original\n")
            self.assertTrue(repair._promote_patch(source, b"original\n", b"fixed\n"))
            self.assertEqual(source.read_bytes(), b"fixed\n")

    def test_added_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            target, cycle, log, state = fixture(Path(td))
            def unsafe(sandbox, model, prompt):
                package = sandbox / "tool/theory_master/theory_master"
                (package/"canvas_projection.py").write_text("VALUE = 'fixed'\n")
                (package/"hidden").symlink_to("/etc/passwd")
                return True, "symlink"
            with patch.object(repair, "__file__", str(target.parent/"supervisor_runtime_repair.py")), \
                 patch.object(repair, "_invoke_codex", side_effect=unsafe), \
                 patch.object(repair, "_promote_patch") as promote:
                result = repair.attempt_runtime_repair(
                    root=log.parents[1], cycle_dir=cycle, state=state,
                    state_path=cycle/"research_state.json", session=Session(),
                    model="test", incident_id="id-1")
            self.assertEqual(result["action"], "hold")
            promote.assert_not_called()

    def test_no_checkpoint_or_repeat_attempt_never_calls_codex(self):
        with tempfile.TemporaryDirectory() as td:
            target, cycle, log, state = fixture(Path(td))
            for mutation in ({"role_outputs": {}}, {"supervisor_runtime_repair_count": 1},
                             {"investigator_budget": {"rejected": True}}):
                with self.subTest(mutation=mutation), patch.object(repair, "_invoke_codex") as call:
                    result = repair.attempt_runtime_repair(
                        root=log.parents[1], cycle_dir=cycle, state={**state, **mutation},
                        state_path=cycle/"research_state.json", session=Session(),
                        model="test", incident_id="id-1")
                    self.assertEqual(result["action"], "hold")
                    call.assert_not_called()

    def test_unapproved_second_file_blocks_promotion(self):
        with tempfile.TemporaryDirectory() as td:
            target, cycle, log, state = fixture(Path(td))
            def rogue(sandbox, model, prompt):
                package = sandbox / "tool/theory_master/theory_master"
                (package/"canvas_projection.py").write_text("VALUE = 'fixed'\n")
                (package/"other.py").write_text("OTHER = False\n")
                return True, "changed too much"
            with patch.object(repair, "__file__", str(target.parent/"supervisor_runtime_repair.py")), \
                 patch.object(repair, "_invoke_codex", side_effect=rogue), \
                 patch.object(repair, "_promote_patch") as promote:
                result = repair.attempt_runtime_repair(
                    root=log.parents[1], cycle_dir=cycle, state=state,
                    state_path=cycle/"research_state.json", session=Session(),
                    model="test", incident_id="id-1")
            self.assertEqual(result["action"], "hold")
            promote.assert_not_called()

    def test_review_keeps_independent_protocol_guard_after_engine_patch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target, cycle, log, state = fixture(root)
            state_path = cycle / "research_state.json"
            fake = {"action": "resume", "reason": "verified patch", "diagnosis": "",
                    "repair_suggestion": ""}
            with patch("theory_master.supervisor_runtime_repair.repair_target", return_value=target), \
                 patch("theory_master.supervisor_runtime_repair.attempt_runtime_repair",
                       return_value=fake) as attempt:
                result = review_runtime_failure(
                    backend=None, research_root=root/"task", cycle_dir=cycle,
                    state=state, state_path=state_path, incident_id="id-1")
            self.assertEqual(result["action"], "hold")
            attempt.assert_called_once()
            self.assertEqual(state["protocol_health"], "violation")
            self.assertNotIn("supervisor_runtime_resume_count", state)


if __name__ == "__main__":
    unittest.main()
