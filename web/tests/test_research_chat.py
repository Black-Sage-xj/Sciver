from __future__ import annotations

import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tm_observer.research_chat import (
    ResearchChatError, apply_action, begin_retry, begin_send, create, rename_session, send, snapshot,
)
from tm_observer.collector import collect_state
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class FakeBackend:
    def __init__(self):
        self.calls = []

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((role, prompt, model, provider))
        return "fake-thread", "需要进一步核验文献与实验记录。"

    def drop_thread(self, thread):
        pass




class PersistentFakeBackend:
    def __init__(self):
        self.calls = []
        self.ensured = []

    def ensure_thread(self, role, thread, model, provider):
        self.ensured.append((role, thread, model, provider))
        return thread or "native-thread-1"

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((role, prompt, thread, model, provider))
        return thread or "native-thread-1", "根据已提供的真实 Cycle 记录，仍需区分已裁决与中断的工作。"

    def close(self):
        pass


class InvalidPlanBackend(FakeBackend):
    def invoke(self, role, prompt, thread, model, provider):
        return "fake-thread", "invalid plan"


class ResearchChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "observer-state"
        self.task_root = self.root / "project"
        self.task_root.mkdir()
        self.research_root = self.root / "research"
        self.research_root.mkdir()
        (self.task_root / "research_plan.md").write_text("# Goal\n\n检查研究证据。\n", encoding="utf-8")
        (self.task_root / "theory-master.config.json").write_text(json.dumps({
            "branch_canvas": str(self.task_root / "exploration.canvas"),
        }), encoding="utf-8")
        self.canvas = self.task_root / "exploration.canvas"
        self.canvas.write_text(json.dumps({"nodes": [
            {"id": "H-1", "type": "text", "text": "type: Hypothesis\nid: H-1\nfrontier_status: active\n\n# Idea\nA"},
        ], "edges": []}), encoding="utf-8")
        self.state = {"runs": [{"observer_id": "a" * 16, "task_name": "project",
                               "task_root": str(self.task_root), "research_root": str(self.research_root)}]}

    def test_first_user_turn_gets_deterministic_title_and_manual_title_wins(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        fake = FakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "请分析冻结的判据"}, backend=fake)
        session = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(session["title"], "分析冻结的判据")
        self.assertEqual(session["title_source"], "auto")
        renamed = rename_session(self.state_dir, ident, "人工命名：冻结判据")
        self.assertEqual(renamed["title"], "人工命名：冻结判据")
        send(self.state_dir, self.root, self.state, ident, {"content": "第二个问题"}, backend=fake)
        session = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(session["title"], "人工命名：冻结判据")
        self.assertEqual(session["title_source"], "manual")

    def test_session_title_validation_and_manual_rename(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        self.assertEqual(rename_session(self.state_dir, ident, "  有用的标题  ")['title'], "有用的标题")
        with self.assertRaises(ResearchChatError):
            rename_session(self.state_dir, ident, "  ")
        with self.assertRaises(ResearchChatError):
            rename_session(self.state_dir, ident, "x" * 81)

    def test_edit_and_resend_truncates_later_turns_and_resets_native_thread(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        fake = FakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "第一个问题"}, backend=fake)
        send(self.state_dir, self.root, self.state, ident, {"content": "第二个问题"}, backend=fake)
        record_path = self.state_dir / "research-chat" / f"{ident}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        first_user = next(message for message in record["messages"] if message["role"] == "user")
        record["codex_thread_id"] = "thread-from-old-content"
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        class HeldThread:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass

        from tm_observer import research_chat as module
        try:
            with patch("tm_observer.research_chat.threading.Thread", HeldThread):
                accepted = begin_send(self.state_dir, self.root, self.state, ident, {
                    "content": "修订后的首个问题", "edit_message_id": first_user["id"],
                })
            self.assertTrue(accepted["streaming"])
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual([message["role"] for message in record["messages"]], ["assistant", "user"])
            self.assertEqual(record["messages"][-1]["id"], first_user["id"])
            self.assertEqual(record["messages"][-1]["content"], "修订后的首个问题")
            self.assertEqual(record["title"], "修订后的首个问题")
            self.assertIsNone(record["codex_thread_id"])
            self.assertEqual(record["active_run"]["user_message_id"], first_user["id"])
        finally:
            with module._JOBS_LOCK:
                module._JOBS.pop(ident, None)

    def test_frontier_chat_uses_read_only_role_and_persists(self):
        ident = create(self.state_dir, self.state, {"mode": "frontier", "task_id": "a" * 16})["session_id"]
        fake = FakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "有哪些前沿？"}, backend=fake)
        row = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(fake.calls[0][0], "research_chat")
        self.assertEqual(row["messages"][-1]["content"], "需要进一步核验文献与实验记录。")

    def test_authority_identity_refresh_restarts_old_thread_with_history(self):
        from tm_observer import research_chat as module
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        store = module._store(self.state_dir)
        record = module._read(store, ident)
        record["codex_thread_id"] = "old-authority-thread"
        record["agent_policy_version"] = 3
        module._message(record, "user", "此前的科研问题")
        module._write(store, record)
        fake = PersistentFakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "继续核对前沿"}, backend=fake)
        self.assertIsNone(fake.ensured[0][1])
        self.assertIn("此前的科研问题", fake.calls[0][1])
        row = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(row["agent_policy_version"], 4)
        self.assertEqual(row["codex_thread_id"], "native-thread-1")

    def test_native_codex_thread_is_resumed_across_chat_turns(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        fake = PersistentFakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "第一次问题"}, backend=fake)
        send(self.state_dir, self.root, self.state, ident, {"content": "第二次问题"}, backend=fake)
        row = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(row["codex_thread_id"], "native-thread-1")
        self.assertIn("第一次问题", fake.calls[0][1])
        self.assertIn("第二次问题", fake.calls[1][1])
        self.assertNotIn("第一次问题", fake.calls[1][1])
        self.assertEqual(fake.calls[1][2], "native-thread-1")
        self.assertEqual(fake.ensured[1][1], "native-thread-1")

    def test_snapshot_recovers_persisted_partial_reply_after_restart(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        record_path = self.state_dir / "research-chat" / f"{ident}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["messages"].append({"id": "user-1", "role": "user", "content": "最近进展？"})
        record["active_run"] = {"id": "run-1", "status": "streaming", "content": "最近进展？",
                                 "user_message_id": "user-1", "stream_text": "Cycle 132 被裁决为 rejected。"}
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        session = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(session["active_run"]["status"], "interrupted")
        self.assertIn("Cycle 132 被裁决为 rejected。", session["messages"][-1]["content"])
        self.assertIn("未完成", session["messages"][-1]["content"])
        self.assertEqual(session["stream_text"], "")

    def test_async_acceptance_persists_user_message_before_worker_starts(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        class HeldThread:
            def __init__(self, *args, **kwargs): self.args = args
            def start(self): pass
        from tm_observer import research_chat as module
        try:
            with patch("tm_observer.research_chat.threading.Thread", HeldThread):
                accepted = begin_send(self.state_dir, self.root, self.state, ident,
                                      {"content": "这条消息必须先落盘"})
            record = json.loads((self.state_dir / "research-chat" / f"{ident}.json").read_text(encoding="utf-8"))
            self.assertTrue(accepted["streaming"])
            self.assertEqual(record["messages"][-1]["content"], "这条消息必须先落盘")
            self.assertEqual(record["active_run"]["status"], "starting")
        finally:
            with module._JOBS_LOCK:
                module._JOBS.pop(ident, None)

    def test_prompt_includes_real_cycle_evidence_and_interrupted_boundary(self):
        cycle132 = self.research_root / "132"
        cycle133 = self.research_root / "133"
        cycle132.mkdir()
        cycle133.mkdir()
        for cycle in (cycle132, cycle133):
            (cycle / ".theory-master-cycle").write_text("cycle\n", encoding="utf-8")
        (cycle132 / "run_state.json").write_text(json.dumps({
            "status": "over", "phase": "over", "started_at": "2026-10-18T01:00:00+08:00",
        }), encoding="utf-8")
        (cycle132 / "research_state.json").write_text(json.dumps({
            "status": "over", "hypothesis_lock": {"id": "H-1", "title": "固定假说"},
        }), encoding="utf-8")
        (cycle132 / "run_report.md").write_text(
            "# TheoryMaster run index\n\n- run_id: `132`\n- cycle_status: `over`\n- branch_verdict: `rejected`\n",
            encoding="utf-8")
        (cycle132 / "cycle_synthesis_out.json").write_text(json.dumps({
            "scientific_synthesis": {
                "conclusions": ["冻结均匀介质替代误差区间完整超出既定预算。"],
                "open_questions": ["独立网络上的前瞻预测仍未确立。"],
            },
        }), encoding="utf-8")
        (self.task_root / "notes" / "outcomes").mkdir(parents=True)
        (self.task_root / "notes" / "outcomes" / "132.md").write_text(
            "# Cycle 132 Outcome\n\nverdict: rejected\n", encoding="utf-8")
        (cycle133 / "run_state.json").write_text(json.dumps({
            "status": "INTERRUPTED", "phase": "interrupted", "current_agent": "investigator",
            "current_activity": "investigation attempt 1", "started_at": "2026-10-18T01:42:14+08:00",
        }), encoding="utf-8")
        (cycle133 / "research_state.json").write_text(json.dumps({
            "status": "interrupted", "hypothesis_lock": {
                "id": "H-2", "title": "共享幅值机制", "decisive_test": "冻结g=6至12并检查共同幅值。",
            },
        }), encoding="utf-8")
        (cycle133 / "runtime_events.jsonl").write_text(
            json.dumps({"ts": "2026-10-18T01:56:08+08:00", "event": "TOKEN_USAGE"}) + "\n",
            encoding="utf-8")
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        fake = FakeBackend()
        send(self.state_dir, self.root, self.state, ident, {"content": "介绍最近两天的进展，科学性如何？"}, backend=fake)
        prompt = fake.calls[0][1]
        self.assertIn("Cycle 132", prompt)
        self.assertIn("branch_verdict: `rejected`", prompt)
        self.assertIn("Cycle 133", prompt)
        self.assertIn("INTERRUPTED", prompt)
        self.assertIn("H-2", prompt)
        self.assertIn("notes/outcomes/132.md", prompt)
        self.assertIn("不得把它当成已完成实验结果", prompt)
        self.assertIn("Live Worker/scheduler identity check", prompt)
        self.assertIn("current-running-status questions", prompt)

    def test_legacy_orphaned_user_turn_becomes_retryable(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        record_path = self.state_dir / "research-chat" / f"{ident}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["messages"].append({"id": "old-user-message", "role": "user",
                                   "content": "介绍最近两天的进展，科学性如何？", "created_at": "2026-10-18T09:08:49+00:00"})
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        view = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(view["active_run"]["status"], "interrupted")
        self.assertIn("服务重启", view["messages"][-1]["content"])
        with patch("tm_observer.research_chat.begin_send", return_value={"ok": True}) as mocked:
            result = begin_retry(self.state_dir, self.root, self.state, ident)
        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_args.args[4]["content"], "介绍最近两天的进展，科学性如何？")

    def test_preferred_focus_requires_explicit_confirmation_and_active_tip(self):
        ident = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        send(self.state_dir, self.root, self.state, ident, {"content": "/preference 优先检查方案 A"})
        before = json.loads(self.canvas.read_text(encoding="utf-8"))
        self.assertEqual(len(before["nodes"]), 1)
        action = snapshot(self.state_dir, self.state, ident)["session"]["pending_action"]
        apply_action(self.state_dir, self.root, self.state, ident,
                     {"action_id": action["id"], "decision": "confirm", "tip_id": "H-1"})
        after = json.loads(self.canvas.read_text(encoding="utf-8"))
        self.assertIn("priority: preferred", after["nodes"][0]["text"])
        focus = after["nodes"][1]
        self.assertIn("content_type: ResearchFocus", focus["text"])
        self.assertIn("source: human", focus["text"])
        self.assertEqual(after["edges"][0]["edge_type"], "external_reference")

    def test_migration_rejects_directory_outside_search_root(self):
        ident = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        (self.root / "scope").mkdir()
        with self.assertRaises(ResearchChatError):
            send(self.state_dir, self.root / "scope", self.state, ident, {"content": str(self.root)})

    def test_migration_bootstrap_becomes_discoverable(self):
        work = self.root / "new-work"
        work.mkdir()
        (work / "notes.md").write_text("# Existing result\nPreliminary observation needs replication.\n", encoding="utf-8")
        ident = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        send(self.state_dir, self.root, self.state, ident, {"content": str(work)})
        send(self.state_dir, self.root, self.state, ident,
             {"content": "检验现有观察是否可复现"}, backend=InvalidPlanBackend())
        action = snapshot(self.state_dir, self.state, ident)["session"]["pending_action"]
        self.assertIn("notes.md", action["details"])
        apply_action(self.state_dir, self.root, self.state, ident,
                     {"action_id": action["id"], "decision": "confirm"})
        self.assertTrue((work / "theory-master.config.json").is_file())
        self.assertTrue(any(row.get("task_root") == str(work) for row in collect_state(self.root)["runs"]))

    def test_migration_preview_can_be_revised_before_creation(self):
        work = self.root / "draft-work"
        work.mkdir()
        ident = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        send(self.state_dir, self.root, self.state, ident, {"content": str(work)})
        send(self.state_dir, self.root, self.state, ident,
             {"content": "研究可复现性"}, backend=InvalidPlanBackend())
        action = snapshot(self.state_dir, self.state, ident)["session"]["pending_action"]
        revised = action["details"].replace("# Goal\n\n研究可复现性", "# Goal\n\n研究可复现性与误差来源")
        apply_action(self.state_dir, self.root, self.state, ident,
                     {"action_id": action["id"], "decision": "revise", "details": revised})
        current = snapshot(self.state_dir, self.state, ident)["session"]["pending_action"]
        self.assertNotEqual(current["id"], action["id"])
        self.assertFalse((work / "research_plan.md").exists())
        with self.assertRaises(ResearchChatError):
            apply_action(self.state_dir, self.root, self.state, ident,
                         {"action_id": action["id"], "decision": "confirm"})
        apply_action(self.state_dir, self.root, self.state, ident,
                     {"action_id": current["id"], "decision": "confirm"})
        self.assertIn("误差来源", (work / "research_plan.md").read_text(encoding="utf-8"))

    def test_home_skips_migration_but_keeps_explicit_history(self):
        migration = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        self.assertIsNone(snapshot(self.state_dir, self.state)["session"])
        self.assertEqual(snapshot(self.state_dir, self.state, migration)["session"]["mode"], "migration")
        project = create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        newer_migration = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        self.assertEqual(snapshot(self.state_dir, self.state)["session"]["id"], project)
        self.assertEqual(snapshot(self.state_dir, self.state, newer_migration)["session"]["mode"], "migration")
        self.assertIsNone(snapshot(self.state_dir, self.state, "__new__")["session"])

    def test_first_message_api_flow_configures_and_accepts_without_model_call(self):
        class Cache:
            search_root = self.root
            def get(inner_self): return self.state

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=self.state_dir))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, body):
            request = Request(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
                "Content-Type": "application/json", "Origin": base, "X-TM-Operator-Intent": "1",
            })
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))

        try:
            status, created = post("/api/research-chat/session", {"mode": "project", "task_id": "a" * 16})
            self.assertEqual(status, 200)
            ident = created["session_id"]
            status, renamed = post(f"/api/research-chat/{ident}/title", {"title": "接口重命名测试"})
            self.assertEqual(status, 200)
            self.assertEqual(renamed["title"], "接口重命名测试")
            status, model = post(f"/api/research-chat/{ident}/model", {"model": "gpt-6.1-sol"})
            self.assertEqual(status, 200)
            self.assertEqual(model["model"], "gpt-6.1-sol")
            status, effort = post(f"/api/research-chat/{ident}/effort", {"effort": "low"})
            self.assertEqual(status, 200)
            self.assertEqual(effort["reasoning_effort"], "low")
            with patch("tm_observer.research_chat.begin_send", return_value={"ok": True, "session_id": ident, "streaming": True}) as begin:
                status, accepted = post(f"/api/research-chat/{ident}/message", {"content": "请分析最近的实验结果。"})
                self.assertEqual(status, 202)
                self.assertTrue(accepted["streaming"])
                self.assertEqual(begin.call_args.args[3], ident)
                self.assertEqual(begin.call_args.args[4]["content"], "请分析最近的实验结果。")
                long_content = "科研" * 2000  # 4,000 Unicode characters, over the old 9 KB UTF-8 body cap.
                status, accepted = post(f"/api/research-chat/{ident}/message", {"content": long_content})
                self.assertEqual(status, 202)
                self.assertTrue(accepted["streaming"])
                self.assertEqual(begin.call_args.args[4]["content"], long_content)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_web_route_requires_origin_and_serves_chat_page(self):
        cache = StateCache(self.root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache,
                                      field_notes_state_dir=self.state_dir))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/research-chat/", timeout=5) as response:
                self.assertEqual(response.status, 200)
                html = response.read().decode("utf-8")
                self.assertIn("Research Chat", html)
                self.assertIn('data-research-task', html)
                self.assertIn('data-research-direct', html)
                self.assertIn('data-new-chat-model', html)
                self.assertIn('data-new-chat-effort', html)
                self.assertIn('class="tm-research-new"', html)
                self.assertIn('href="/research-chat/?new=1"', html)
                self.assertIn('<h3>历史对话</h3>', html)
                self.assertIn('class="tm-research-history"', html)
                self.assertIn('分析研究进展与科学性', html)
                self.assertNotIn('loquix-chat-composer', html)
                self.assertLess(html.index('data-research-mode="project"'), html.index('data-research-mode="frontier"'))
                self.assertIn('<details class="tm-research-migrate">', html)
                self.assertIn('data-research-mode="migration"', html)
            body = json.dumps({"mode": "migration"}).encode("utf-8")
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(base + "/api/research-chat/session", data=body,
                                headers={"Content-Type": "application/json"}), timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            with urlopen(Request(base + "/api/research-chat/session", data=body,
                                 headers={"Content-Type": "application/json", "Origin": base,
                                          "X-TM-Operator-Intent": "1"}), timeout=5) as response:
                self.assertEqual(response.status, 200)
                ident = json.loads(response.read())["session_id"]
            with urlopen(base + "/research-chat/?session=" + ident, timeout=5) as response:
                session_html = response.read().decode("utf-8")
                self.assertIn(f'id="tm-research-chat-app" data-session-id="{ident}"', session_html)
                self.assertIn('/research-chat/assets/research-chat.js', session_html)
                self.assertNotIn('loquix-chat-composer', session_html)
            with urlopen(base + f"/api/research-chat/{ident}/state", timeout=5) as response:
                state_payload = json.loads(response.read())
                self.assertEqual(state_payload["id"], ident)
                self.assertIn("请提供已有工作的绝对目录路径。", state_payload["messages"][0]["content"])
            with urlopen(base + "/research-chat/assets/research-chat.js", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/javascript", response.headers.get("Content-Type", ""))
                self.assertGreater(len(response.read()), 100_000)
            with urlopen(base + "/research-chat/assets/research-chat.css", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/css", response.headers.get("Content-Type", ""))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
