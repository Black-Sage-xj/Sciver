"""Deterministic engineering checks; no model, research replay or effect scoring."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from theory_master.invocations import InvocationLedger
from theory_master.operator_preferences import read, write
from theory_master.skill_evolution import SkillError, SkillLibrary
from theory_master.skill_evolution.runtime import finish, prepare, prompt, safely, task_key


def method(**changes):
    return {"name": "residual-check", "description": "Residual checks for sparse solves",
            "when_to_use": "After a sparse linear solve with known matrix and right-hand side.",
            "body": "Compute the residual with the original matrix; report its norm and scale.",
            "reason": "Retain an already used method", "tags": ["sparse", "residual"], **changes}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = SkillLibrary(self.root / "library")
        self.source = {"invocation": "first", "cycle": "1", "task": "owner"}

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **kw):
        return self.library.maintain(method(**kw), owner="owner", role="investigator", source=self.source)

    def test_source_bound_versions_deduplication_and_rollback(self):
        first = self.create()
        self.assertEqual(first["evidence_level"], "trajectory_derived")
        self.assertEqual(self.create()["status"], "duplicate")
        old = self.library.release(first["skill_id"], first["to_version"])
        old_bytes = (Path(old["path"]) / "SKILL.md").read_bytes()
        updated = self.library.maintain(
            method(operation="update", base_version=first["to_version"], body="Check residual and norm scaling."),
            owner="owner", role="investigator", source={**self.source, "invocation": "second"})
        self.assertNotEqual(updated["to_version"], first["to_version"])
        self.assertEqual((Path(old["path"]) / "SKILL.md").read_bytes(), old_bytes)
        restored = self.library.maintain(
            method(operation="rollback", base_version=updated["to_version"], target_version=first["to_version"]),
            owner="owner", role="investigator", source={"operator": "requested"})
        self.assertEqual(restored["to_version"], first["to_version"])
        self.assertEqual(len(self.library.proposals("owner")), 3)

    def test_concurrent_updates_require_exact_base(self):
        first = self.create()
        def update(n):
            try:
                return self.library.maintain(
                    method(operation="update", base_version=first["to_version"], body=f"Updated procedure {n}"),
                    owner="owner", role="investigator", source={"invocation": str(n)})["status"]
            except SkillError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(update, [1, 2])), ["applied", "conflict"])

    def test_general_skills_need_relevance_and_task_skills_stay_local(self):
        self.create()
        self.create(name="portable-check", scope="general")
        self.assertEqual([x["name"] for x in self.library.catalogue(
            owner="another-task", role="investigator", query="sparse residual")], ["portable-check"])
        self.assertEqual(self.library.catalogue(owner="another-task", role="investigator", query="unrelated"), [])
        self.assertEqual(self.library.catalogue(owner="another-task", role="judge", query="sparse"), [])

    def test_duplicate_content_adds_no_change_event_and_cannot_override(self):
        self.create()
        same = self.library.maintain(method(), owner="owner", role="investigator", source={"invocation": "again"})
        self.assertEqual(same["status"], "unchanged")
        self.assertEqual(len(self.library.proposals("owner")), 1)
        with self.assertRaises(SkillError):
            self.create(body="Different unversioned procedure")

    def test_merge_is_atomic_and_preserves_historical_packages(self):
        first = self.create()
        other = self.create(name="other-check", body="Another compatible procedure")
        merged = self.library.maintain(
            method(operation="merge", base_version=first["to_version"], body="Both reusable procedures.",
                   absorbs=[{"name": "other-check", "version": other["to_version"]}]),
            owner="owner", role="investigator", source={"invocation": "merge"})
        self.assertEqual(merged["status"], "applied")
        self.assertEqual(merged["absorbed_skills"], [{"name": "other-check", "id": other["skill_id"],
                                                     "version": other["to_version"]}])
        self.assertEqual(len(self.library.catalogue(owner="owner", role="investigator")), 1)
        self.assertTrue(self.library.release(other["skill_id"], other["to_version"]))

    def test_integrity_path_and_role_guards(self):
        first = self.create()
        frozen = self.library.release(first["skill_id"], first["to_version"])
        (Path(frozen["path"]) / "SKILL.md").write_text("tampered")
        with self.assertRaisesRegex(SkillError, "integrity"):
            self.library.release(first["skill_id"], first["to_version"])
        with self.assertRaises(SkillError):
            self.library.maintain(method(), owner="a", role="judge", source={})
        with self.assertRaises(SkillError):
            self.library.maintain(method(name="escape"), owner="owner", role="investigator",
                                  source={}, files={"scripts/../../escape.py": b"bad"})

    def test_archive_is_recoverable_and_script_helpers_survive_text_revision(self):
        first = self.library.maintain(method(), owner="owner", role="investigator", source=self.source,
                                      files={"scripts/helper.py": b"print('portable helper')\n"})
        updated = self.library.maintain(
            method(operation="update", base_version=first["to_version"], body="See scripts/helper.py for the procedure."),
            owner="owner", role="investigator", source={"invocation": "revise"})
        release = self.library.release(updated["skill_id"], updated["to_version"])
        self.assertIn("scripts/helper.py", release["files"])
        self.library.maintain(method(operation="archive", base_version=updated["to_version"]),
                              owner="owner", role="investigator", source={"operator": "archive"})
        self.assertEqual(self.library.catalogue(owner="owner", role="investigator"), [])
        archived = self.library.owned(owner="owner", role="investigator", name="residual-check")
        self.assertEqual(archived["status"], "archived")
        with self.assertRaisesRegex(SkillError, "archived"):
            self.library.maintain(method(body="See scripts/helper.py for the procedure."),
                                  owner="owner", role="investigator", source={"invocation": "repeat-create"})
        restored_same = self.library.maintain(
            method(operation="update", base_version=archived["version"], body="See scripts/helper.py for the procedure."),
            owner="owner", role="investigator", source={"operator": "restore-current"})
        self.assertEqual(restored_same["status"], "applied")
        self.assertEqual(restored_same["from_version"], restored_same["to_version"])
        self.library.maintain(method(operation="rollback", base_version=archived["version"], target_version=first["to_version"]),
                              owner="owner", role="investigator", source={"operator": "restore"})
        self.assertEqual(self.library.owned(owner="owner", role="investigator", name="residual-check")["status"], "active")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "task"
        (self.root / ".theory-master").mkdir(parents=True)
        self.cycle = self.root / "1"
        self.workspace = self.cycle / "workspace"
        self.workspace.mkdir(parents=True)
        self.config = self.root / "theory-master.config.json"
        self.config.write_text("{}")
        self.env = patch.dict(os.environ, {"SCIVER_SKILL_LIBRARY": str(Path(self.temp.name) / "library")})
        self.env.start()
        self.library = SkillLibrary()
        self.kw = dict(root=self.root, config_path=self.config, cycle_dir=self.cycle,
                       workspace=self.workspace, role="investigator", query="sparse residual")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def enable(self, value=True):
        write(self.root, kind="flag", key="skill_learning", value=value)

    def committed_note(self, invocation="inv-test", **kw):
        ledger = InvocationLedger(self.cycle / "invocations.jsonl")
        ledger.prepare(invocation_id=invocation, kind="agent", input_payload={}, logical_key=invocation,
                       role="investigator", stage="investigate")
        ledger.transition(invocation, "committed", commit_state="committed", role="investigator")
        (self.workspace / "normal-result.txt").write_text("normal task output")
        path = self.workspace / f".skill-note-{invocation}.json"
        path.write_text(json.dumps({"skill": method(evidence=[{"path": "normal-result.txt",
                                                              "observation": "Original run completed this operation"}], **kw)}))
        return dict(root=self.root, cycle_dir=self.cycle, workspace=self.workspace, role="investigator",
                    invocation_id=invocation, note_path=path)

    def test_default_off_creates_no_library_or_prompt(self):
        self.assertEqual(prepare(logical_key="first", **self.kw), {})
        self.assertFalse(self.library.path.exists())
        self.assertEqual(prompt({}, note_path=self.workspace / "note"), "")
        note = self.committed_note()
        finish(**note)
        self.assertFalse(self.library.path.exists())

    def test_normal_note_publishes_with_proposal_and_no_extra_executor(self):
        self.enable()
        binding = prepare(logical_key="first", **self.kw)
        note = self.committed_note()
        with patch("subprocess.run", side_effect=AssertionError("no extra execution")):
            finish(**note, binding=binding)
        proposals = self.library.proposals(task_key(self.root))
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["state"], "applied_by_enabled_policy")
        events = (self.root / "important_events.jsonl").read_text().splitlines()
        self.assertEqual(len(events), 1)
        event = json.loads(events[0])
        self.assertEqual(event["severity"], "important")
        self.assertEqual(event["kind"], "skill_change")
        self.assertEqual(event["routing"]["strategy"], "record_only")
        self.assertTrue((self.root / ".theory-master/skill-evolution/proposals" / (event["proposal_id"] + ".json")).is_file())
        finish(**note, binding=binding)
        self.assertEqual(len((self.root / "important_events.jsonl").read_text().splitlines()), 1)

    def test_outbox_recovers_without_republishing(self):
        self.enable()
        note = self.committed_note()
        with patch("theory_master.observability.incident_registry.register_cycle_incident", side_effect=OSError("offline")):
            safely("finish", **note)
        self.assertEqual(len(self.library.proposals(task_key(self.root))), 1)
        prepare(logical_key="later", **self.kw)
        self.assertEqual(len(self.library.pending_events(task_key(self.root))), 0)
        self.assertEqual(len(self.library.proposals(task_key(self.root))), 1)

    def test_disable_rejects_inflight_notes_and_keeps_adopted_versions(self):
        self.enable()
        finish(**self.committed_note())
        first = prepare(logical_key="first", **self.kw)
        self.assertEqual(len(first["skills"]), 1)
        self.enable(False)
        finish(**self.committed_note("another", name="not-published"))
        later = prepare(logical_key="later", **self.kw)
        self.assertFalse(later["learning"])
        self.assertEqual(later["skills"], first["skills"])
        self.assertEqual(len(self.library.proposals(task_key(self.root))), 1)
        self.assertNotIn("SILENT PROCEDURAL MEMORY", prompt(later, note_path=self.workspace / "note"))
        notice = prompt(later, note_path=self.workspace / "note", previous_learning=True,
                        previous_catalogue=later["catalogue_version"])
        self.assertIn("now disabled", notice)

    def test_resume_keeps_binding_and_next_slot_sees_update(self):
        self.enable()
        finish(**self.committed_note())
        pinned = prepare(logical_key="frozen", **self.kw)
        first = pinned["skills"][0]
        finish(**self.committed_note("update", operation="update", base_version=first["version"], body="New precise method."))
        same = prepare(logical_key="frozen", **self.kw)
        newer = prepare(logical_key="next", **self.kw)
        self.assertEqual(same["skills"][0]["version"], first["version"])
        self.assertNotEqual(newer["skills"][0]["version"], first["version"])
        self.assertEqual((Path(first["path"]) / "SKILL.md").read_text().count("New precise method."), 0)

    def test_fixture_cannot_inherit_shared_skill(self):
        self.enable()
        self.library.maintain(method(scope="general"), owner="old-task", role="investigator", source={"task": "old"})
        (self.root / ".theory-master/test-instance.json").write_text('{"case":"test_0"}')
        self.assertEqual(prepare(logical_key="fresh-fixture", **self.kw)["skills"], [])

    def test_missing_evidence_and_interrupted_notes_stay_out_of_active_library(self):
        self.enable()
        note = self.committed_note()
        (self.workspace / "normal-result.txt").unlink()
        safely("finish", **note)
        self.assertEqual(self.library.catalogue(owner=task_key(self.root), role="investigator"), [])
        self.assertIn("retained_for_revision", (self.cycle / "skill_maintenance/inv-test.json").read_text())
        interrupted = self.committed_note("interrupted")
        InvocationLedger(self.cycle / "invocations.jsonl").transition("interrupted", "interrupted", commit_state="not_committed")
        safely("finish", **interrupted)
        self.assertEqual(self.library.catalogue(owner=task_key(self.root), role="investigator"), [])

    def test_full_protocol_is_not_repeated_on_every_turn(self):
        self.enable()
        binding = prepare(logical_key="first", **self.kw)
        first = prompt(binding, note_path=self.workspace / "note")
        next_turn = prompt(binding, note_path=self.workspace / "next", protocol_seen=True,
                           previous_catalogue=binding["catalogue_version"])
        self.assertLess(len(next_turn), 600)
        self.assertLess(len(next_turn), len(first) // 2)
        self.assertIn("protocol.md", next_turn)

    def test_preference_writes_do_not_lose_concurrent_keys(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda key: write(self.root, kind="flag", key=key, value=True),
                          ["cycle_audit", "skill_learning"]))
        self.assertEqual(read(self.root)["flags"], {"cycle_audit": True, "skill_learning": True})

    def test_feedback_is_natural_observation_not_automatic_success(self):
        self.enable()
        finish(**self.committed_note())
        binding = prepare(logical_key="using", **self.kw)
        skill = binding["skills"][0]
        self.assertEqual(skill["usage_counts"], {})
        args = self.committed_note("using")
        args["note_path"].write_text(json.dumps({"feedback": [
            {"id": skill["id"], "version": skill["version"], "outcome": "failed",
             "detail": "This input needs a different normalization."}]}))
        finish(**args, binding=binding)
        later = prepare(logical_key="after-feedback", **self.kw)
        self.assertEqual(later["skills"][0]["usage_counts"], {"reported_failure": 1})
        self.assertIn("different normalization", prompt(later, note_path=self.workspace / "next"))
        self.assertNotEqual(later["catalogue_version"], binding["catalogue_version"])

    def test_evidence_cannot_escape_and_fixture_notes_cannot_be_shared(self):
        self.enable()
        args = self.committed_note()
        payload = json.loads(args["note_path"].read_text())
        payload["skill"]["evidence"][0]["path"] = "../outside.txt"
        args["note_path"].write_text(json.dumps(payload))
        safely("finish", **args)
        self.assertEqual(self.library.catalogue(owner=task_key(self.root), role="investigator"), [])
        (self.root / ".theory-master/test-instance.json").write_text('{"case":"test_0"}')
        args = self.committed_note("fixture", scope="general")
        finish(**args, config_path=self.config)
        owned = self.library.catalogue(owner=task_key(self.root), role="investigator")
        self.assertEqual(owned[0]["scope"], "task")
        self.assertEqual(self.library.catalogue(owner="other", role="investigator", query="sparse residual"), [])

    def test_observed_load_is_not_counted_as_success(self):
        self.enable()
        finish(**self.committed_note())
        binding = prepare(logical_key="read", **self.kw)
        args = self.committed_note("read")
        args["note_path"].unlink()
        item = binding["skills"][0]
        event = {"event": "SKILL_LOADED", "invocation_id": "read",
                 "skill_path": str(Path(item["path"]) / "SKILL.md")}
        (self.cycle / "runtime_events.jsonl").write_text(json.dumps(event) + "\n")
        finish(**args, binding=binding)
        latest = self.library.catalogue(owner=task_key(self.root), role="investigator")[0]
        self.assertEqual(latest["usage_counts"], {"loaded": 1})

    def test_cli_toggle_and_inspection_never_launch_runtime(self):
        from types import SimpleNamespace
        from theory_master.skill_evolution.cli import run
        spec = SimpleNamespace(research_root=self.root)
        args = SimpleNamespace(master_id="1", skill_action="on")
        with patch("theory_master.cli_discovery.spec_from_task", return_value=spec), \
             patch("theory_master.runtime_manager.launch_managed_runtime", side_effect=AssertionError("no launch")), \
             patch("builtins.print"):
            self.assertEqual(run(args), 0)
            self.assertTrue(read(self.root)["flags"]["skill_learning"])
            args.skill_action = "status"
            self.assertEqual(run(args), 0)

    def test_operator_dispatch_saves_toggle_without_launching_science(self):
        from types import SimpleNamespace
        from theory_master.operator_control import dispatch
        spec = SimpleNamespace(master_id=1, research_root=self.root)
        command = {"task_root": str(self.root), "research_root": str(self.root), "run_id": 1,
                   "action": "set_operator_preference",
                   "value": {"kind": "flag", "key": "skill_learning", "value": True}}
        with patch("theory_master.operator_control.TaskBinding.load", return_value=SimpleNamespace(config_path=self.config)), \
             patch("theory_master.operator_control.TaskSpec.from_config", return_value=spec), \
             patch("theory_master.runtime_manager.launch_managed_runtime", side_effect=AssertionError("no launch")):
            result = dispatch(command)
        self.assertEqual(result["effective_at"], "next_safe_invocation")
        self.assertEqual(result["extra_model_requests"], 0)
        self.assertTrue(read(self.root)["flags"]["skill_learning"])

    def test_shared_catalogue_does_not_grant_legacy_threads_new_permissions(self):
        self.enable()
        finish(**self.committed_note())
        binding = prepare(logical_key="legacy", allow_catalogue=False, **self.kw)
        self.assertEqual(binding["skills"], [])
        self.assertNotIn("SDK LEARNED TASK-SKILL CATALOGUE UPDATE", prompt(binding, note_path=self.workspace / "next"))

    def test_operator_can_archive_and_restore_adopted_skill_while_learning_is_off(self):
        from types import SimpleNamespace
        from theory_master.skill_evolution.cli import run
        self.enable()
        finish(**self.committed_note())
        binding = prepare(logical_key="adopted", **self.kw)
        item = binding["skills"][0]
        self.enable(False)
        args = SimpleNamespace(master_id="1", skill_action="archive", role="investigator",
                               name=item["name"], reason="Operator archive", version=None)
        with patch("theory_master.cli_discovery.spec_from_task", return_value=SimpleNamespace(research_root=self.root)), \
             patch("builtins.print"):
            self.assertEqual(run(args), 0)
            self.assertEqual(prepare(logical_key="after-archive", **self.kw)["skills"], [])
            args.skill_action, args.version = "rollback", item["version"]
            self.assertEqual(run(args), 0)
            restored = prepare(logical_key="after-restore", **self.kw)
            self.assertEqual(restored["skills"][0]["version"], item["version"])
            args.skill_action, args.version = "archive", None
            self.assertEqual(run(args), 0)
            self.assertEqual(prepare(logical_key="after-second-archive", **self.kw)["skills"], [])
            args.skill_action, args.version = "rollback", item["version"]
            self.assertEqual(run(args), 0)
            self.assertEqual(prepare(logical_key="after-second-restore", **self.kw)["skills"][0]["version"], item["version"])
            self.assertEqual(len(self.library.proposals(task_key(self.root))), 5)


class WorkflowIntegrationTests(unittest.TestCase):
    def test_normal_fake_workflow_captures_skill_without_extra_calls_or_contract_fields(self):
        from theory_master.core import FakeBackend, TaskSpec, Workflow
        class NormalNotesBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=1)
                self.prompts = []
                self.written = False

            def invoke(self, role, message, thread_id, model, provider):
                self.prompts.append((role, message))
                if role == "investigator" and not self.written:
                    found = re.search(r"(/[^\s]+/\.skill-note-inv-[^\s]+\.json)", message)
                    if found:
                        note = Path(found[1])
                        note.parent.mkdir(parents=True, exist_ok=True)
                        (note.parent / "normal-method.txt").write_text("method observed during normal work")
                        note.write_text(json.dumps({"skill": method(evidence=[
                            {"path": "normal-method.txt", "observation": "Normal task artifact"}])}))
                        self.written = True
                return super().invoke(role, message, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"SCIVER_SKILL_LIBRARY": str(Path(tmp) / "lib")}):
            root = Path(tmp)
            plan = root / "research_plan.md"
            plan.write_text("# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\n"
                            "Establish a reproducible result.\n\n# Required Context\n\n- r\n\n"
                            "# Scientific Model\n\nNo prior findings.\n")
            spec = TaskSpec.load(plan)
            config = json.loads(spec.config_path.read_text())
            config["max_cycles"] = 2
            spec.config_path.write_text(json.dumps(config))
            spec = TaskSpec.from_config(spec.config_path)
            write(spec.research_root, kind="flag", key="skill_learning", value=True)
            backend = NormalNotesBackend()
            state = Workflow(spec, backend).run()
            self.assertTrue(backend.written)
            self.assertEqual(len(SkillLibrary().proposals(task_key(spec.research_root))), 1)
            self.assertTrue(all(role not in {"skill_learner", "skill_reviewer"} for role, _ in backend.prompts))
            investigator_prompts = [p for r, p in backend.prompts if r == "investigator"]
            self.assertEqual(len(investigator_prompts), 2)
            self.assertEqual(state["incoming_judge_followup"]["cycle"], "1")
            self.assertIn("SDK LEARNED TASK-SKILL CATALOGUE UPDATE", investigator_prompts[1])
            self.assertIn("learned-residual-check", investigator_prompts[1])
            self.assertTrue(all("skill" not in value for value in state["agent_outputs"].values()))


if __name__ == "__main__":
    unittest.main()
