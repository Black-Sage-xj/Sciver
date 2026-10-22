"""A resumed Investigator does not count as a fresh Judge rework verdict."""
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.observability.collector import Collector
from theory_master.tui.web_bridge import _readable_action_history
import test_observability as fixture_module


class ResumeReworkTelemetryTests(unittest.TestCase):
    def test_resume_restores_previous_judge_count_not_fake_rework(self):
        with tempfile.TemporaryDirectory() as dirname:
            spec, _ = fixture_module.ObservabilityTest().make(Path(dirname), reworks=0)
            run_id="cycle-70"
            run_dir=spec.research_root/run_id
            first=Collector(spec)
            first.start_run(run_id,spec,run_dir=run_dir)
            state_path=run_dir/"run_state.json"
            prior=json.loads(state_path.read_text(encoding="utf-8"))
            prior.update({
                "judge_rework_count": 1, "rework_count": 1,
                "investigation_attempt": 2,
                "phase": "investigator:running",
                "status": "RUNNING",
            })
            state_path.write_text(json.dumps(prior),encoding="utf-8")
            restarted=Collector(spec)
            restarted.start_run(run_id,spec,run_dir=run_dir)
            self.assertEqual(restarted._last_rework,1)
            sci={
                "status":"active","phase":"investigator:running",
                "judge_rework_count":1,"rework_count":1,
                "investigation_attempt":2,
                "execution_active":True,
            }
            restarted.on_scientific("role.started",{
                "role":"investigator", "investigation_attempt":2,
                "stage":"judge_rework", "thread_id":"thr-review",
            },sci)
            self.assertEqual(restarted.snapshot()["rework_count"],1)
            self.assertFalse(any(
                e.get("event")=="JUDGE_REWORK_REQUESTED"
                for e in restarted.runtime_events(limit=0)
            ))
            # A genuinely NEW Judge decision with a strictly higher count
            # should still result in one rework request.
            sci["judge_rework_count"]=2
            sci["rework_count"]=2
            restarted.on_scientific("decision.recorded",{"role":"judge"},sci)
            requested=[e for e in restarted.runtime_events(limit=0)
                       if e.get("event")=="JUDGE_REWORK_REQUESTED"]
            self.assertEqual(len(requested),1)
            self.assertEqual(requested[0]["judge_retry_index"],2)

    def test_readonly_web_history_retains_true_request_and_discards_resume_replays(self):
        actions=[
            {"at":"2026-10-12T10:44:43+08:00","event":"CYCLE_STARTED","text":"Cycle started · 70"},
            {"at":"2026-10-12T11:48:04+08:00","event":"JUDGE_REWORK_REQUESTED",
             "text":"Judge returned → upstream stage for rework #1"},
            {"at":"2026-10-12T12:15:31+08:00","event":"CYCLE_STARTED","text":"Cycle started · 70"},
            {"at":"2026-10-12T12:15:31+08:00","event":"JUDGE_REWORK_REQUESTED",
             "text":"Judge returned → upstream stage for rework #1"},
            {"at":"2026-10-12T17:57:33+08:00","event":"CYCLE_STARTED","text":"Cycle started · 70"},
            {"at":"2026-10-12T17:57:33+08:00","event":"JUDGE_REWORK_REQUESTED",
             "text":"Judge returned → upstream stage for rework #1"},
        ]
        untouched=json.loads(json.dumps(actions))
        result=_readable_action_history(actions)
        self.assertEqual(actions,untouched)
        self.assertEqual(len(result),4)
        self.assertEqual([(e["event"],e["at"]) for e in result if
                          e["event"]=="JUDGE_REWORK_REQUESTED"],
                         [("JUDGE_REWORK_REQUESTED","2026-10-12T11:48:04+08:00")])
        self.assertEqual([e["event"] for e in result][-1],"CYCLE_STARTED")

    def test_different_cycle_or_rework_number_is_not_suppressed(self):
        history=[
            {"at":"11:00","event":"CYCLE_STARTED","text":"Cycle started · 1"},
            {"at":"11:20","event":"JUDGE_REWORK_REQUESTED","text":"Judge returned → rework #1"},
            {"at":"11:30","event":"JUDGE_REWORK_REQUESTED","text":"Judge returned → rework #2"},
            {"at":"12:00","event":"CYCLE_STARTED","text":"Cycle started · 2"},
            {"at":"12:20","event":"JUDGE_REWORK_REQUESTED","text":"Judge returned → rework #1"},
        ]
        self.assertEqual(_readable_action_history(history),history)
