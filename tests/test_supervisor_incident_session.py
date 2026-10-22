"""Supervisor incident session: storage, review hook, human follow-up."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from theory_master.runtime_supervisor_review import review_runtime_failure
from theory_master.supervisor_incident_session import (
    apply_operational_edits, close_incident, image_bytes, load_incident,
    open_notification_incident, post_human_message, publish_review_incident,
    resolve_incident, supervisor_prose, IncidentSessionError,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _Backend:
    def __init__(self) -> None:
        self.calls = 0
        self.dropped = None

    def invoke_runtime_review(self, prompt, model, provider):
        self.calls += 1
        self.prompt = prompt
        return "thread-1", "发现 snapshot 与 ledger 不一致"

    def invoke(self, role, prompt, thread_id, model, provider):
        self.role = role
        self.prompt = prompt
        return self.invoke_runtime_review(prompt, model, provider)

    def consume_turn_reads(self):
        return ["runtime/router.py"]

    def drop_thread(self, thread):
        self.dropped = thread


class IncidentSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cycle = self.root / "cycle-70"
        self.cycle.mkdir()
        self.state = {
            "task_id": "task-70",
            "cycle_id": "cycle-70",
            "status": "failed",
            "phase": "failed",
            "protocol_health": "violation",
            "router_ready": False,
            "resumable": False,
            "recovery": {"kind": "ValueError", "message": "router contract broken"},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_normal_cycle_allocates_nothing(self):
        self.assertFalse((self.root / ".theory-master" / "supervisor_incidents").exists())

    def test_publish_writes_opening_and_reuses_active_session(self):
        first = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="protocol_violation", trigger_event="protocol_health=violation",
            analysis=None, model_invoked=False,
            system_note="确定性 hold，未调用模型。", tool_reads=["runtime/router.py"],
            audit={"incident_id": "INC-1"},
        )
        second = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="protocol_violation", trigger_event="protocol_health=violation",
            analysis=None, model_invoked=False,
            system_note="确定性 hold，未调用模型。", tool_reads=["runtime/router.py"],
            audit={"incident_id": "INC-1"},
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "active")
        self.assertIsNone(first["closed_at"])
        record, messages = load_incident(self.root, first["id"])
        roles = [row["role"] for row in messages]
        self.assertEqual(roles, ["supervisor", "tool", "system"])
        self.assertIn("protocol_health=violation", messages[0]["content"])
        self.assertIn("router_ready=False", messages[0]["content"])
        self.assertIn("开始分析原因。", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "read runtime/router.py")
        self.assertEqual(record["task_id"], "task-70")
        self.assertEqual(record["cycle_id"], "cycle-70")
        link = json.loads((self.cycle / "supervisor" / "incident_link.json").read_text(encoding="utf-8"))
        self.assertEqual(link["session_id"], first["id"])
        self.assertEqual(link["status"], "active")

    def test_runtime_review_hold_does_not_change_decision_or_state_file(self):
        state_path = self.cycle / "research_state.json"
        before = dict(self.state)
        state_path.write_text(json.dumps(before), encoding="utf-8")
        result = review_runtime_failure(
            backend=None, research_root=self.root, cycle_dir=self.cycle, state=self.state,
            state_path=state_path, incident_id="INC-rt",
        )
        self.assertEqual(result["action"], "hold")
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["protocol_health"], "violation")
        self.assertNotIn("supervisor_runtime_review", json.loads(state_path.read_text(encoding="utf-8")))
        folder = self.root / ".theory-master" / "supervisor_incidents" / "sessions"
        files = list(folder.glob("SIS-*.json"))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["trigger_type"], "protocol_violation")
        _record, messages = load_incident(self.root, record["id"])
        self.assertEqual(messages[0]["role"], "supervisor")
        self.assertTrue(any(row["role"] == "system" for row in messages))

    def test_human_follow_up_stays_in_session_and_does_not_touch_science_state(self):
        opened = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="runtime_failure", trigger_event="run_failed",
            analysis="建议检查状态迁移。", model_invoked=True, system_note=None, tool_reads=[],
            audit={"source": "runtime_review"},
        )
        science = self.cycle / "research_state.json"
        science.write_text('{"status":"failed","frozen":true}\n', encoding="utf-8")
        backend = _Backend()
        outcome = post_human_message(
            self.root, opened["id"], "检查一下 router 的 ready 判断", backend=backend,
        )
        self.assertEqual(backend.calls, 1)
        self.assertEqual(backend.dropped, "thread-1")
        self.assertIn("检查一下 router 的 ready 判断", backend.prompt)
        roles = [row["role"] for row in outcome["messages"]]
        self.assertEqual(roles, ["supervisor", "supervisor", "human", "tool", "supervisor"])
        self.assertEqual(outcome["messages"][-1]["content"], "发现 snapshot 与 ledger 不一致")
        self.assertEqual(science.read_text(encoding="utf-8"), '{"status":"failed","frozen":true}\n')
        resolve_incident(self.root, opened["id"], "fixed")
        with self.assertRaises(IncidentSessionError) as closed:
            post_human_message(self.root, opened["id"], "again", backend=backend)
        self.assertEqual(closed.exception.status, 409)
        record, messages = load_incident(self.root, opened["id"])
        self.assertEqual(record["status"], "resolved")
        self.assertIsNotNone(record["closed_at"])
        self.assertTrue(any(row["role"] == "human" for row in messages))
        close_again = close_incident
        self.assertTrue(callable(close_again))

    def test_repeat_click_does_not_stack_the_same_line_and_web_send_returns(self):
        opened = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="hold", trigger_event="run_failed",
            analysis=None, model_invoked=False, system_note="hold", tool_reads=[],
        )
        backend = _Backend()
        backend.invoke_runtime_review = lambda prompt, model, provider: ("t", '{"answer":"模型账号不匹配"}')
        first = post_human_message(self.root, opened["id"], "这是出啥问题了?", backend=backend)
        self.assertEqual(first["messages"][-1]["content"], "模型账号不匹配")
        self.assertEqual(supervisor_prose('{"explanation":"配置不匹配"}'), "配置不匹配")
        with patch("theory_master.supervisor_incident_session._schedule_follow_up") as scheduled:
            post_human_message(self.root, opened["id"], "啥意思?", backend=None)
            second = post_human_message(self.root, opened["id"], "啥意思?", backend=None)
        self.assertEqual(scheduled.call_count, 2)
        humans = [row["content"] for row in second["messages"] if row["role"] == "human"]
        self.assertEqual(humans[-2:], ["这是出啥问题了?", "啥意思?"])
        self.assertTrue(second["pending"])

    def test_image_attachment_is_stored_without_sending_pixels_to_the_prompt(self):
        opened = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="hold", trigger_event="run_failed",
            analysis=None, model_invoked=False, system_note="hold", tool_reads=[],
        )
        backend = _Backend()
        outcome = post_human_message(
            self.root, opened["id"], "看这张图",
            images=[{"name": "../shot.png", "data": _PNG}], backend=backend,
        )
        human = next(row for row in outcome["messages"] if row["role"] == "human")
        stored = human["metadata"]["images"][0]
        self.assertEqual(stored["name"], "shot.png")
        self.assertEqual(stored["media_type"], "image/png")
        self.assertIn("shot.png", backend.prompt)
        self.assertNotIn(_PNG.decode("latin1"), backend.prompt)
        blob, media = image_bytes(self.root, opened["id"], stored["id"])
        self.assertEqual((blob, media), (_PNG, "image/png"))
        with self.assertRaises(IncidentSessionError):
            post_human_message(self.root, opened["id"], "", images=[{"name": "x.bin", "data": b"not-an-image"}],
                               backend=backend)
        self.assertEqual(backend.calls, 1)

    def test_operator_can_change_allowlisted_model_config(self):
        from theory_master.identity import identity_block
        opened = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="hold", trigger_event="run_failed",
            analysis=None, model_invoked=False, system_note="hold", tool_reads=[],
        )
        config = self.root / "theory-master.config.json"
        config.write_text('{"tier":"gpt-6.1-sol"}\n', encoding="utf-8")
        science = self.cycle / "research_state.json"
        science.write_text('{"status":"failed","frozen":true}\n', encoding="utf-8")
        backend = _Backend()
        reply = (
            "二档已改为 gpt-6-sol。\n"
            '```json\n{"edits":[{"path":"theory-master.config.json","old":"gpt-6.1-sol","new":"gpt-6-sol"}]}\n```'
        )
        backend.invoke_runtime_review = lambda prompt, model, provider: ("t", reply)
        outcome = post_human_message(self.root, opened["id"], "把二档位的模型固定改为6", backend=backend)
        self.assertEqual(backend.role, "incident_supervisor")
        self.assertIn("not a read-only commentator", backend.prompt)
        self.assertNotIn("Do not edit files", backend.prompt)
        self.assertIn("theory-master.config.json", backend.prompt)
        self.assertEqual(config.read_text(encoding="utf-8"), '{"tier":"gpt-6-sol"}\n')
        self.assertEqual(science.read_text(encoding="utf-8"), '{"status":"failed","frozen":true}\n')
        self.assertEqual(outcome["messages"][-1]["content"], "二档已改为 gpt-6-sol。")
        self.assertTrue(any(row["content"] == "已修改 theory-master.config.json" for row in outcome["messages"]))
        self.assertIn("You are not read-only", identity_block("incident_supervisor"))
        blocked = _Backend()
        blocked.invoke_runtime_review = lambda prompt, model, provider: (
            "t", '不行。\n```json\n{"edits":[{"path":"cycle-70/research_state.json","old":"failed","new":"over"}]}\n```',
        )
        refused = post_human_message(self.root, opened["id"], "改科学状态", backend=blocked)
        self.assertEqual(science.read_text(encoding="utf-8"), '{"status":"failed","frozen":true}\n')
        self.assertTrue(any(row["content"].startswith("未修改") for row in refused["messages"]))

    def test_operational_edit_rejects_symlink_before_resolving_target(self):
        science = self.cycle / "research_state.json"
        science.write_text('{"status":"failed"}\n', encoding="utf-8")
        link = self.root / "theory-master.config.json"
        link.symlink_to(science)
        with self.assertRaisesRegex(IncidentSessionError, "symlink"):
            apply_operational_edits(self.root, [{
                "path": "theory-master.config.json", "old": "failed", "new": "over",
            }])
        self.assertEqual(science.read_text(encoding="utf-8"), '{"status":"failed"}\n')

    def test_operational_edit_resolves_v2_config_to_task_home(self):
        from theory_master.task import bootstrap

        task_home = self.root / "v2-task"
        task_home.mkdir()
        plan = task_home / "research_plan.md"
        plan.write_text(
            "# Goal\nTest a task-local setting.\n\n# Research Contract\nLocal only.\n\n"
            "# Final Gate\nDeterministic check.\n\n# Required Context\nNone.\n",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        config_path = binding.config_path
        self.assertNotEqual(config_path.parent.resolve(), binding.research_root.resolve())
        notes = apply_operational_edits(binding.research_root, [{
            "path": "theory-master.config.json", "old": '"max_cycles": 5',
            "new": '"max_cycles": 7',
        }])
        self.assertEqual(notes, ["已修改 theory-master.config.json"])
        self.assertIn('"max_cycles": 7', config_path.read_text(encoding="utf-8"))
        self.assertFalse((binding.research_root / "theory-master.config.json").exists())

    def test_operational_edits_merge_same_file_and_reject_duplicate_targets(self):
        config = self.root / "theory-master.config.json"
        original = '{"model":"low","effort":"medium"}\n'
        config.write_text(original, encoding="utf-8")
        apply_operational_edits(self.root, [
            {"path": "theory-master.config.json", "old": '"model":"low"', "new": '"model":"high"'},
            {"path": "theory-master.config.json", "old": '"effort":"medium"', "new": '"effort":"low"'},
        ])
        updated = config.read_text(encoding="utf-8")
        self.assertIn('"model":"high"', updated)
        self.assertIn('"effort":"low"', updated)
        with self.assertRaisesRegex(IncidentSessionError, "duplicate edit target"):
            apply_operational_edits(self.root, [
                {"path": "theory-master.config.json", "old": '"model":"high"', "new": '"model":"low"'},
                {"path": "theory-master.config.json", "old": '"model":"high"', "new": '"model":"medium"'},
            ])
        self.assertEqual(config.read_text(encoding="utf-8"), updated)

    def test_multi_file_operational_edit_rolls_back_after_replace_failure(self):
        first = self.root / "theory-master.config.json"
        second = self.root / "config.toml"
        first.write_text("model = low\n", encoding="utf-8")
        second.write_text("effort = medium\n", encoding="utf-8")
        real_replace = os.replace
        calls = 0

        def fail_second_replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated second-file replacement failure")
            return real_replace(source, target)

        with patch("theory_master.supervisor_incident_session.os.replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(IncidentSessionError, "prior files were restored"):
                apply_operational_edits(self.root, [
                    {"path": "theory-master.config.json", "old": "low", "new": "high"},
                    {"path": "config.toml", "old": "medium", "new": "low"},
                ])
        self.assertEqual(first.read_text(encoding="utf-8"), "model = low\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "effort = medium\n")
        self.assertEqual(list(self.root.glob(".*.tm-edit-*")), [])

    def test_notification_metadata_uses_canonical_task_id_and_trigger_nature(self):
        from theory_master.task import bootstrap

        task_home = self.root / "notification-task"
        task_home.mkdir()
        plan = task_home / "research_plan.md"
        plan.write_text(
            "# Goal\nTest notification metadata.\n\n# Research Contract\nLocal only.\n\n"
            "# Final Gate\nDeterministic check.\n\n# Required Context\nNone.\n",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        research_root = binding.research_root
        cycle = research_root / "1"
        cycle.mkdir()
        config = json.loads(binding.config_path.read_text(encoding="utf-8"))
        config["task_id"] = 0
        binding.config_path.write_text(json.dumps(config), encoding="utf-8")
        notification = open_notification_incident(
            research_root=research_root, cycle_dir=cycle, task_id="observer-url-id",
            notification_id="current-stop:0123456789abcdef01", code="no_qualified_candidate",
            summary="No candidate passed qualification.",
            source={"observer_id": "observer-url-id", "run_status": "NEEDS_ATTENTION"},
        )
        self.assertEqual(notification["task_id"], "0")
        self.assertNotEqual(notification["task_id"], "observer-url-id")
        self.assertEqual(notification["trigger_type"], "needs_attention")
        failed = open_notification_incident(
            research_root=research_root, cycle_dir=cycle, task_id="observer-url-id",
            notification_id="current-stop:1123456789abcdef01", code="run_failed",
            summary="Runner failed.", source={"run_status": "FAILED"},
        )
        self.assertEqual(failed["trigger_type"], "runtime_failure")
        protocol = open_notification_incident(
            research_root=research_root, cycle_dir=cycle, task_id="observer-url-id",
            notification_id="current-stop:2123456789abcdef01", code="protocol_health_violation",
            summary="Runtime protocol violation.", source={"run_status": "NEEDS_ATTENTION"},
        )
        self.assertEqual(protocol["trigger_type"], "protocol_violation")

    def test_close_keeps_dialogue(self):
        opened = publish_review_incident(
            research_root=self.root, cycle_dir=self.cycle, state=self.state,
            trigger_type="needs_attention", trigger_event="contract_repair_failed",
            analysis=None, model_invoked=False, system_note="hold", tool_reads=[],
        )
        close_incident(self.root, opened["id"])
        record, messages = load_incident(self.root, opened["id"])
        self.assertEqual(record["status"], "closed")
        self.assertGreaterEqual(len(messages), 2)
        link = json.loads((self.cycle / "supervisor" / "incident_link.json").read_text(encoding="utf-8"))
        self.assertEqual(link["status"], "closed")
