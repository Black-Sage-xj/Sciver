from __future__ import annotations

import unittest
from types import SimpleNamespace

from theory_master.observability.codex_events import (
    extract_agent_text,
    extract_final_from_turn,
    is_transient_reconnect_error,
    map_notification,
)


class _Event:
    def __init__(self, method, payload):
        self.method = method
        self.payload = payload


class CodexEventMapTest(unittest.TestCase):
    def test_auto_review_service_failure_is_not_a_policy_denial(self):
        event = _Event("item/autoApprovalReview/completed", SimpleNamespace(
            turn_id="turn-503",
            review=SimpleNamespace(
                status="denied",
                rationale="Automatic approval review failed: unexpected status 503 Service Unavailable: No available channel for model codex-auto-review",
            ),
            action=SimpleNamespace(root=SimpleNamespace(server="theory_master_gateway", tool_name="exec")),
        ))
        mapped = map_notification(event, role="investigator", thread_id="thr")
        self.assertEqual(mapped["event"], "TOOL_APPROVAL_UNAVAILABLE")
        self.assertEqual(mapped["mcp_server"], "theory_master_gateway")
        self.assertEqual(mapped["mcp_tool"], "exec")
        self.assertIn("codex-auto-review", mapped["error"])

        tool_error = _Event("item/completed", SimpleNamespace(item=SimpleNamespace(root=SimpleNamespace(
            type="mcpToolCall", server="theory_master_gateway", tool="exec",
            error=SimpleNamespace(message="Automatic approval review failed: unexpected status 503 Service Unavailable"),
        ))))
        fallback = map_notification(tool_error, role="investigator", thread_id="thr")
        self.assertEqual(fallback["event"], "TOOL_APPROVAL_UNAVAILABLE")
        self.assertEqual(fallback["mcp_tool"], "exec")

        policy_denial = _Event("item/autoApprovalReview/completed", SimpleNamespace(
            review=SimpleNamespace(status="denied", rationale="Unsafe command"),
        ))
        self.assertIsNone(map_notification(policy_denial, role="investigator", thread_id="thr"))

    def test_reconnect_is_not_terminal_failure(self):
        self.assertTrue(is_transient_reconnect_error("Reconnecting... 3/5"))
        mapped = map_notification(_Event("error", SimpleNamespace(message="Reconnecting... 3/5")), role="research_lead", thread_id="thr")
        self.assertEqual(mapped["event"], "LLM_RECONNECTING")
        self.assertEqual(mapped["agent"], "research_lead")
        self.assertFalse(is_transient_reconnect_error("context window exceeded"))

    def test_command_started_and_completed(self):
        item = SimpleNamespace(root=SimpleNamespace(type="commandExecution", command="python scripts/check.py"))
        started = map_notification(_Event("item/started", SimpleNamespace(item=item, turn_id="t1")), role="numerical", thread_id="thr")
        self.assertIsNone(started)
        done = map_notification(_Event("item/completed", SimpleNamespace(item=item, turn_id="t1")), role="numerical", thread_id="thr")
        self.assertEqual(done["event"], "TOOL_ACTIVITY")
        self.assertEqual(done["tool"], "command")
        self.assertEqual(done["detail"], "python scripts/check.py")

    def test_deltas_are_dropped(self):
        self.assertIsNone(map_notification(_Event("item/agentMessage/delta", SimpleNamespace(delta="x"))))
        self.assertIsNone(map_notification(_Event("item/commandExecution/outputDelta", SimpleNamespace(delta="out"))))
        self.assertIsNone(map_notification(_Event("item/reasoning/textDelta", SimpleNamespace(delta="r"))))

    def test_turn_and_usage(self):
        started = map_notification(_Event("turn/started", SimpleNamespace(turn=SimpleNamespace(id="turn-9"))))
        self.assertEqual(started["event"], "LLM_TURN_STARTED")
        self.assertEqual(started["turn_id"], "turn-9")
        done = map_notification(_Event(
            "turn/completed",
            SimpleNamespace(turn=SimpleNamespace(id="turn-9", status="completed", usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12))),
        ), role="investigator", thread_id="thr")
        self.assertEqual(done["event"], "LLM_TURN_COMPLETED")
        self.assertEqual(done["tokens_total"], 12)
        self.assertEqual(done["raw_usage"], {"input_tokens": 10, "output_tokens": 2, "provider_specific_usage": {}})

    def test_completed_turn_items_supply_final_response(self):
        item = SimpleNamespace(root=SimpleNamespace(type="agentMessage", text='{"candidates": []}'))
        done = map_notification(_Event(
            "turn/completed",
            SimpleNamespace(turn=SimpleNamespace(id="turn-10", status="completed", items=[item])),
        ), role="research_lead", thread_id="thr")
        self.assertEqual(extract_final_from_turn(SimpleNamespace(turn=SimpleNamespace(items=[item]))), '{"candidates": []}')

    def test_timeline_item_wrapper_supplies_final_response(self):
        item = SimpleNamespace(
            root=SimpleNamespace(
                item=SimpleNamespace(
                    root=SimpleNamespace(type="agent_message", content=[SimpleNamespace(text="answer")])
                )
            )
        )
        done = map_notification(_Event(
            "turn/completed",
            SimpleNamespace(turn=SimpleNamespace(items=[item])),
        ), role="research_lead", thread_id="thr")
        self.assertEqual(
            extract_final_from_turn(SimpleNamespace(turn=SimpleNamespace(items=[item]))),
            "answer",
        )

    def test_unknown_notification_params_supply_final_response(self):
        item = SimpleNamespace(
            root=SimpleNamespace(
                type="agent_message",
                content=[SimpleNamespace(root=SimpleNamespace(text="answer"))],
            )
        )
        self.assertEqual(
            extract_agent_text(SimpleNamespace(params=SimpleNamespace(item=item))),
            "answer",
        )

    def test_responses_message_item_content_supplies_final_response(self):
        item = SimpleNamespace(
            type="message",
            role="assistant",
            content=[SimpleNamespace(type="output_text", text="answer")],
        )
        self.assertEqual(
            extract_agent_text(SimpleNamespace(params=SimpleNamespace(item=item))),
            "answer",
        )

    def test_user_and_developer_messages_are_never_agent_output(self):
        for role in ("user", "developer", "system"):
            item = SimpleNamespace(
                type="message",
                role=role,
                content=[SimpleNamespace(type="input_text", text="input prompt")],
            )
            self.assertIsNone(
                extract_agent_text(SimpleNamespace(params=SimpleNamespace(item=item)))
            )

    def test_roleless_plain_message_is_not_agent_output(self):
        item = SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text="ambiguous")],
        )
        self.assertIsNone(
            extract_agent_text(SimpleNamespace(params=SimpleNamespace(item=item)))
        )

    def test_thread_token_usage_total(self):
        payload = SimpleNamespace(
            turn_id="t1",
            token_usage=SimpleNamespace(
                total=SimpleNamespace(input_tokens=80, output_tokens=20, total_tokens=100),
                last=SimpleNamespace(input_tokens=8, output_tokens=2, total_tokens=10),
                model_context_window=258400,
            ),
        )
        mapped = map_notification(_Event("thread/tokenUsage/updated", payload), role="investigator", thread_id="thr")
        self.assertEqual(mapped["event"], "TOKEN_USAGE")
        self.assertEqual(mapped["tokens_total"], 100)
        self.assertEqual(mapped["tokens_in"], 80)
        self.assertEqual(mapped["raw_usage"]["input_tokens"], 80)
        self.assertEqual(mapped["raw_usage"]["output_tokens"], 20)
        self.assertEqual(mapped["last_request_usage"]["input_tokens"], 8)
        self.assertEqual(mapped["last_request_usage"]["output_tokens"], 2)
        self.assertEqual(mapped["context_tokens"], 8)
        self.assertEqual(mapped["context_window"], 258400)

    def test_native_compaction_notifications(self):
        started_item = SimpleNamespace(root=SimpleNamespace(type="contextCompaction"))
        started = map_notification(
            _Event("item/started", SimpleNamespace(item=started_item, turn_id="tc1")),
            role="investigator", thread_id="thr",
        )
        self.assertEqual(started["event"], "COMPACTION_STARTED")
        self.assertEqual(started["episode_id"], "thr")
        completed = map_notification(
            _Event("thread/compacted", SimpleNamespace(thread_id="thr", turn_id="tc1")),
            role="investigator", thread_id="thr",
        )
        self.assertEqual(completed["event"], "COMPACTION_COMPLETED")
        self.assertIsNone(completed["context_after"])
        failed = map_notification(
            _Event("thread/error", SimpleNamespace(message="context compaction failed")),
            role="investigator", thread_id="thr",
        )
        self.assertEqual(failed["event"], "COMPACTION_FAILED")

    def test_file_and_reasoning(self):
        file_item = SimpleNamespace(root=SimpleNamespace(type="fileChange", path="result/curve.csv"))
        mapped = map_notification(_Event("item/completed", SimpleNamespace(item=file_item)), role="numerical")
        self.assertEqual(mapped["event"], "TOOL_ACTIVITY")
        self.assertEqual(mapped["tool"], "file")
        self.assertEqual(mapped["detail"], "result/curve.csv")
        reason = map_notification(_Event("item/started", SimpleNamespace(item=SimpleNamespace(root=SimpleNamespace(type="reasoning")))), role="investigator")
        self.assertIsNone(reason)

    def test_agent_message_is_recorded_as_conversation(self):
        item = SimpleNamespace(root=SimpleNamespace(type="agentMessage", text="answer"))
        mapped = map_notification(_Event("item/completed", SimpleNamespace(item=item, turn_id="t1")), role="investigator")
        self.assertEqual(mapped["event"], "LLM_MESSAGE")
        self.assertEqual(mapped["agent"], "investigator")
        self.assertEqual(mapped["message_role"], "assistant")
        self.assertEqual(mapped["content"], "answer")

    def test_unknown_is_ignored(self):
        self.assertIsNone(map_notification(_Event("thread/queue/changed", SimpleNamespace())))


if __name__ == "__main__":
    unittest.main()
