"""Skill settings and exact, escaped, read-only proposal projection."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from tm_observer.important_events import read_important_events
from tm_observer.run_control import _validated_value
from tm_observer.run_views import _important_timeline
from tm_observer.skill_evolution_ui import proposal_details
from tm_observer.task_settings_ui import task_settings_body


class SkillSettingsTests(unittest.TestCase):
    def test_switch_is_default_off_task_scoped_and_uses_canonical_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "1"
            cycle.mkdir()
            (cycle / "run_state.json").write_text('{"status":"RUNNING","cycle_id":"1"}')
            run = {"observer_id": "a" * 16, "research_root": str(root), "cycle_dir": str(cycle)}
            page = task_settings_body(run)
            dom = BeautifulSoup(page, "html.parser")
            switch = dom.select_one('[data-setting-key="skill_learning"]')
            self.assertIsNotNone(switch)
            self.assertFalse(switch.has_attr("checked"))
            self.assertIn("不另启模型", dom.get_text())
            self.assertIn("expected_launch_id:page.dataset.launch", page)
            self.assertEqual(switch.get("data-setting-mode"), "flag")
            selected = {"kind": "flag", "key": "skill_learning", "value": True}
            self.assertEqual(_validated_value("set_operator_preference", selected), selected)
            self.assertFalse((root / ".theory-master").exists())

    def test_exact_proposal_diff_is_escaped_and_never_follows_external_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "1"
            cycle.mkdir()
            proposal_id = "SKP-" + "1" * 24
            directory = root / ".theory-master/skill-evolution/proposals"
            directory.mkdir(parents=True)
            payload = {"id": proposal_id, "state": "applied_by_enabled_policy",
                       "reason": "<img src=x>", "after_text": "new <script>alert(1)</script>\n",
                       "absorbed_skills": [{"name": "source-method", "version": "a" * 64}]}
            (directory / (proposal_id + ".json")).write_text(json.dumps(payload))
            run = {"research_root": str(root), "cycle_dir": str(cycle)}
            result = proposal_details(run, proposal_id)
            self.assertIn("查看技能变更与提请", result)
            self.assertIn(proposal_id, result)
            self.assertIn("合并后归档：source-method @ aaaaaaaaaaaa", result)
            self.assertNotIn("<script>", result)
            self.assertIn("&lt;script&gt;", result)
            self.assertEqual(proposal_details(run, "../../outside"), "")
            self.assertEqual(proposal_details(run, "SKP-" + "2" * 24), "")
            event = {"at": "2026-11-18T00:00:00+08:00", "severity": "important", "kind": "skill_change",
                     "lifecycle": "registered", "proposal_id": proposal_id, "summary": "Skill changed"}
            (root / "important_events.jsonl").write_text(json.dumps(event) + "\n")
            rendered = _important_timeline(read_important_events(run), run=run)
            self.assertIn("技能变更", rendered)
            self.assertIn("查看技能变更与提请", rendered)
            (directory / (proposal_id + ".json")).unlink()
            (directory / (proposal_id + ".json")).symlink_to(root / "important_events.jsonl")
            self.assertEqual(proposal_details(run, proposal_id), "")


if __name__ == "__main__":
    unittest.main()
