"""Regression: a blocked Native Goal must not become an opaque Attention hold."""
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.workflow_invocation import _native_goal_last_public_report, _native_goal_attention


def message(role: str, thread: str, content: str) -> str:
    return json.dumps(
        {"event": "LLM_MESSAGE", "role": role, "thread_id": thread,
         "content": content}, ensure_ascii=False,
    ) + "\n"


class NativeGoalBlockerReasonTest(unittest.TestCase):
    def test_recover_last_public_message_for_exact_investigator_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            events = root / "runtime_events.jsonl"
            events.write_text(
                message("investigator", "same", "First discovery report")
                + message("investigator", "other", "This is a different thread")
                + message("judge", "same", "Judge cannot write Investigator findings")
                + message("investigator", "same", "无法构造低信息联合传播律；缺少误差界。")
                + json.dumps({"event": "TOKEN_USAGE", "role": "investigator",
                              "thread_id": "same", "tokens_total": 10}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(_native_goal_last_public_report(root, "same"),
                             "无法构造低信息联合传播律；缺少误差界。")

    def test_no_internal_tools_or_private_reasoning_are_treated_as_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime_events.jsonl").write_text(
                json.dumps({"event": "TOOL_ACTIVITY", "role": "investigator",
                            "thread_id": "same", "content": "pretend blocker"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(_native_goal_last_public_report(root, "same"), "")

    def test_missing_or_invalid_log_and_no_thread_return_no_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(_native_goal_last_public_report(root, "same"), "")
            (root / "runtime_events.jsonl").write_text("{invalid}\n")
            self.assertEqual(_native_goal_last_public_report(root, "same"), "")
            self.assertEqual(_native_goal_last_public_report(root, ""), "")

    def test_bounded_tail_and_redaction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime_events.jsonl").write_text(
                "x" * 19000 + "\n"
                + message("investigator", "same",
                          "missing joint law; password=would-be-secret token:private"),
                encoding="utf-8",
            )
            result = _native_goal_last_public_report(root, "same", tail_bytes=3000)
            self.assertIn("missing joint law", result)
            self.assertIn("password=[redacted]", result)
            self.assertIn("token=[redacted]", result)
            self.assertNotIn("would-be-secret", result)
            self.assertNotIn("private", result)

    def test_blocked_projection_passes_reason_without_asserting_scientific_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime_events.jsonl").write_text(
                message("investigator", "scientific-thread",
                        "No legal low-information predictor for B1 and L8."),
                encoding="utf-8",
            )
            projected = _native_goal_attention(
                root, goal_status="blocked", thread_id="scientific-thread",
            )
            self.assertEqual(projected["code"], "investigator_native_goal_unexpected_blocked")
            self.assertIn("no authority", projected["reason"])
            self.assertIn("operational review", projected["reason"])
            self.assertEqual(projected["reason_source"], "investigator_public_report")
            self.assertIn("No legal low-information predictor", projected["reason"])
            self.assertIn("no Finding is ready for Judge", projected["reason"])
            self.assertNotIn("scientific_verdict", projected)
            self.assertNotIn("resume", projected.keys())

    def test_missing_explanation_remains_hold_not_fake_result(self):
        with tempfile.TemporaryDirectory() as folder:
            projected = _native_goal_attention(Path(folder), goal_status="blocked", thread_id="t")
            self.assertEqual(projected["reason_source"], "sdk_status_only")
            self.assertIn("No public blocker explanation", projected["reason"])
            self.assertNotIn("supported", projected["reason"])

    def test_other_native_goal_statuses_not_misclassified_as_scientific_block(self):
        with tempfile.TemporaryDirectory() as folder:
            for status in ("paused", "usageLimited", "budgetLimited"):
                with self.subTest(status=status):
                    projected = _native_goal_attention(Path(folder), goal_status=status, thread_id="t")
                    self.assertEqual(projected["code"], "investigator_native_goal_"+status)
                    self.assertEqual(projected["reason_source"], "sdk_status_only")
                    self.assertNotIn("last public scientific", projected["reason"])

    def test_cannot_read_symlink_to_another_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source").write_text(message("investigator", "same", "unsafe"))
            (root / "runtime_events.jsonl").symlink_to(root / "source")
            self.assertEqual(_native_goal_last_public_report(root, "same"), "")


if __name__ == "__main__":
    unittest.main()
