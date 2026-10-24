"""Regression: Research Plan Web edits are task-scoped and preserve other fields."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tm_observer.research_plan_editor import ResearchPlanError, read_plan, save_section, history_plan
from tm_observer.research_plan_history import project
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler

PLAN = (
    "# Goal\n\nOriginal goal\n\n"
    "# Research Contract\n\nKeep all networks\n\n"
    "# Final Gate\n\nScientific acceptance criterion\n\n"
    "# Required Context\n\n[[canonical]]\n\n"
    "# Scientific Model\n\n$G=1$\n"
)


class ResearchPlanEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.task = self.repo / "笔记区" / "探索台账" / "test-plan"
        (self.task / ".theory-master").mkdir(parents=True)
        (self.task / ".theory-master" / "task.json").write_text("{}", encoding="utf-8")
        self.path = self.task / "research_plan.md"
        self.path.write_text(PLAN, encoding="utf-8")
        self.ident = hashlib.sha256(str(self.task.resolve()).encode()).hexdigest()[:16]
        self.run = {"observer_id": self.ident, "task_name": "test-plan",
                    "task_root": str(self.task), "research_root": str(self.task)}

    def test_read_and_isolated_section_update(self):
        before = read_plan(self.repo, self.run)
        goal = before["sections"][0]
        scientific = before["sections"][-1]
        saved = save_section(self.repo, self.run, {
            "section": "Goal", "text": "\nChanged goal", "expected_revision": goal["revision"], "change_reason": "修正 Goal 内容",
        })
        self.assertIn("Changed goal", self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["sections"][-1], scientific)
        self.assertEqual(saved["sections"][1:4], before["sections"][1:4])
        self.assertEqual([item["name"] for item in saved["sections"]],
                         ["Goal", "Research Contract", "Final Gate", "Required Context", "Scientific Model"])

    def test_stale_same_section_conflicts_but_different_section_rebases(self):
        original = read_plan(self.repo, self.run)
        original_model = original["sections"][-1]
        save_section(self.repo, self.run, {
            "section": "Scientific Model", "text": "\nUpdated by another editor\n",
            "expected_revision": original_model["revision"], "change_reason": "修正科学模型",
        })
        with self.assertRaises(ResearchPlanError) as caught:
            save_section(self.repo, self.run, {
                "section": "Scientific Model", "text": "\nStale save\n",
                "expected_revision": original_model["revision"], "change_reason": "修正科学模型",
            })
        self.assertEqual(caught.exception.status, 409)
        saved = save_section(self.repo, self.run, {
            "section": "Goal", "text": "\nGoal updated\n\n",
            "expected_revision": original["sections"][0]["revision"], "change_reason": "修正目标",
        })
        self.assertIn("Updated by another editor", saved["sections"][-1]["text"])
        self.assertIn("Goal updated", saved["sections"][0]["text"])

    def test_bad_top_level_structure_rejected_without_writing(self):
        before = self.path.read_bytes()
        version = read_plan(self.repo, self.run)["sections"][0]["revision"]
        with self.assertRaises(ResearchPlanError) as caught:
            save_section(self.repo, self.run, {
                "section": "Goal", "text": "\nText\n# Extra H1\n", "expected_revision": version, "change_reason": "新增无效顶层标题",
            })
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(self.path.read_bytes(), before)

    def test_reject_symlink_and_forged_task_path(self):
        other = self.task / "other.md"
        other.write_text(PLAN, encoding="utf-8")
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaises(ResearchPlanError):
            read_plan(self.repo, self.run)
        self.path.unlink()
        self.path.write_text(PLAN, encoding="utf-8")
        forged = {**self.run, "research_root": str(self.repo)}
        with self.assertRaises(ResearchPlanError):
            read_plan(self.repo, forged)

    def test_versions_diff_restore_and_external_edit(self):
        initial = read_plan(self.repo, self.run)
        self.assertEqual(initial["version"], 1)
        with self.assertRaises(ResearchPlanError):
            save_section(self.repo, self.run, {"section": "Goal", "text": "\nNo explanation",
                "expected_revision": initial["sections"][0]["revision"]})
        self.assertEqual(len(history_plan(self.repo, self.run)["history"]), 1)
        saved = save_section(self.repo, self.run, {"section": "Goal", "text": "\nUpdated Goal\n",
            "expected_revision": initial["sections"][0]["revision"], "change_reason": "修正旧目标"})
        self.assertEqual(saved["version"], 2)
        history = history_plan(self.repo, self.run, 1)
        self.assertEqual(len(history["history"]), 2)
        self.assertEqual(history["history"][0]["reason"], "修正旧目标")
        self.assertEqual(history["content"], project(PLAN))
        self.assertIn("-Original goal", history["diff"])
        with self.assertRaises(ResearchPlanError) as stale:
            save_section(self.repo, self.run, {"operation": "restore", "version": 1,
                "expected_revision": initial["revision"], "change_reason": "回滚"})
        self.assertEqual(stale.exception.status, 409)
        restored = save_section(self.repo, self.run, {"operation": "restore", "version": 1,
            "expected_revision": saved["revision"], "change_reason": "恢复最初版本"})
        self.assertEqual(restored["version"], 3)
        self.assertEqual(self.path.read_text(encoding="utf-8"), PLAN)
        self.path.write_text(PLAN.replace("Original goal", "External goal"), encoding="utf-8")
        observed = read_plan(self.repo, self.run)
        self.assertEqual(observed["version"], 4)
        self.assertEqual(history_plan(self.repo, self.run)["history"][0]["source"], "external-observed")

    def test_model_updates_and_scoped_restore_preserve_other_stream(self):
        initial = read_plan(self.repo, self.run)
        model_edit = save_section(self.repo, self.run, {
            "section": "Scientific Model", "text": "\nNew science\n",
            "expected_revision": initial["sections"][-1]["revision"], "change_reason": "Cycle evidence"})
        self.assertEqual(model_edit["version"], 1)
        self.assertEqual(model_edit["model_version"], 2)
        plan_edit = save_section(self.repo, self.run, {
            "section": "Goal", "text": "\nRevised goal\n\n",
            "expected_revision": initial["sections"][0]["revision"], "change_reason": "Human direction"})
        self.assertEqual((plan_edit["version"], plan_edit["model_version"]), (2, 2))
        restored_model = save_section(self.repo, self.run, {
            "operation": "restore", "scope": "model", "version": 1,
            "expected_revision": model_edit["scope_revisions"]["model"], "change_reason": "Restore science"})
        self.assertEqual((restored_model["version"], restored_model["model_version"]), (2, 3))
        self.assertIn("Revised goal", self.path.read_text())
        restored_plan = save_section(self.repo, self.run, {
            "operation": "restore", "scope": "plan", "version": 1,
            "expected_revision": plan_edit["scope_revisions"]["plan"], "change_reason": "Restore plan"})
        self.assertEqual((restored_plan["version"], restored_plan["model_version"]), (3, 3))
        self.assertEqual(self.path.read_text(), PLAN)
        model_history = history_plan(self.repo, self.run, 2, "model")
        self.assertNotIn("# Goal", model_history["content"])
        self.assertIn("New science", model_history["content"])
        with self.assertRaises(ResearchPlanError) as caught:
            save_section(self.repo, self.run, {
                "operation": "restore", "scope": "model", "version": 2,
                "expected_revision": model_edit["scope_revisions"]["model"], "change_reason": "Stale model"})
        self.assertEqual(caught.exception.status, 409)

    def test_legacy_full_history_is_projected_without_modifying_archive(self):
        home = self.task / ".theory-master" / "research-plan-versions"
        home.mkdir()
        journal = []
        for i, raw in enumerate([PLAN, PLAN.replace("$G=1$", "$G=2$"),
                                 PLAN.replace("$G=1$", "$G=3$").replace("Original goal", "New goal")], 1):
            sha = hashlib.sha256(raw.encode()).hexdigest()
            (home / (sha + ".md")).write_text(raw)
            journal.append({"version": i, "sha256": sha, "recorded_at": "2026-10-23",
                            "source": "sdk-scientific-model" if i == 2 else "web-edit",
                            "reason": "legacy change", "section": "Scientific Model" if i == 2 else "Goal"})
        old = "".join(json.dumps(r) + "\n" for r in journal)
        (home / "history.jsonl").write_text(old)
        self.path.write_text(PLAN.replace("$G=1$", "$G=3$").replace("Original goal", "New goal"))
        current = read_plan(self.repo, self.run)
        self.assertEqual((current["version"], current["model_version"]), (2, 3))
        self.assertEqual((home / "history.jsonl").read_text(), old)
        self.assertEqual(history_plan(self.repo, self.run)["history"][0]["legacy_version"], 3)
        # Repeat reads do not create versions or rerun migration.
        self.assertEqual(read_plan(self.repo, self.run), current)
        self.assertEqual(len(history_plan(self.repo, self.run, scope="model")["history"]), 3)
        with self.assertRaises(ResearchPlanError):
            history_plan(self.repo, self.run, scope="invalid")

    def test_http_page_auth_boundary_write_and_conflict(self):
        cache = StateCache(self.repo, ttl_s=500)
        cache._state = {"runs": [self.run]}
        cache._at = time.monotonic()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join(timeout=3)))
        base = "http://127.0.0.1:" + str(server.server_address[1])
        with urlopen(base + "/research-plan/?observer_id=" + self.ident, timeout=4) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Research Plan", page)
        self.assertIn("rp-task", page)
        self.assertIn("rp-history-scope", page)
        self.assertIn("Scientific Model 记录科学认识", page)
        with urlopen(base + "/api/run/" + self.ident + "/research-plan", timeout=4) as response:
            data = json.load(response)
        self.assertEqual(len(data["sections"]), 5)
        payload = {
            "section": "Final Gate", "text": "\nUpdated Final Gate\n\n",
            "expected_revision": data["sections"][2]["revision"], "change_reason": "调整终局门槛",
        }
        url = base + "/api/run/" + self.ident + "/research-plan"
        def post(content: dict, origin: bool = True):
            headers = {"Content-Type": "application/json", "X-TM-Operator-Intent": "1"}
            if origin:
                headers["Origin"] = base
            return urlopen(Request(url, json.dumps(content).encode(), headers=headers, method="POST"), timeout=4)
        with self.assertRaises(HTTPError) as rejected:
            post(payload, origin=False)
        self.assertEqual(rejected.exception.code, 403)
        with post(payload) as response:
            saved = json.load(response)
        self.assertIn("Updated Final Gate", saved["sections"][2]["text"])
        self.assertEqual(saved["sections"][4], data["sections"][4])
        with self.assertRaises(HTTPError) as conflict:
            post(payload)
        self.assertEqual(conflict.exception.code, 409)
        self.assertIn("Updated Final Gate", self.path.read_text(encoding="utf-8"))
        with urlopen(url + "/history", timeout=4) as response:
            journal = json.load(response)
        self.assertEqual([row["version"] for row in journal["history"]], [2, 1])
        self.assertEqual(journal["history"][0]["reason"], "调整终局门槛")
        with urlopen(url + "/history?version=1", timeout=4) as response:
            old = json.load(response)
        self.assertEqual(old["content"], project(PLAN))
        self.assertIn("Scientific acceptance criterion", old["diff"])
        with urlopen(url + "/history?scope=model&version=1", timeout=4) as response:
            model = json.load(response)
        self.assertEqual(model["scope"], "model")
        self.assertEqual(model["content"], project(PLAN, "model"))


if __name__ == "__main__":
    unittest.main()
