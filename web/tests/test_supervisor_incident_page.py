"""Supervisor incident page and human message route."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from theory_master.supervisor_incident_session import open_notification_incident, publish_review_incident
from tm_observer.supervisor_incident_ui import dialogue_html, page_body
from tm_observer.web import make_handler


def _open(root: Path):
    cycle = root / "cycle-70"
    cycle.mkdir()
    return publish_review_incident(
        research_root=root, cycle_dir=cycle, state={
            "cycle_id": "cycle-70", "protocol_health": "violation", "router_ready": False,
            "status": "needs_attention",
        },
        trigger_type="protocol_violation", trigger_event="protocol_health=violation",
        analysis="建议检查状态迁移。", model_invoked=True, system_note=None,
        tool_reads=["workflow_state.py"],
    )


class IncidentPageTests(unittest.TestCase):
    def test_page_shows_dialogue_and_input_only_while_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _open(Path(tmp))
            from theory_master.supervisor_incident_session import load_incident, resolve_incident
            loaded, messages = load_incident(Path(tmp), record["id"])
            html = page_body(loaded, messages)
            dom = BeautifulSoup(html, "html.parser")
            self.assertIn("Supervisor Incident", dom.get_text())
            self.assertIn("Cycle cycle-70", dom.get_text())
            self.assertIn("protocol_health=violation", dom.get_text())
            self.assertIn("建议检查状态迁移。", dom.get_text())
            self.assertIn("read workflow_state.py", dom.get_text())
            self.assertIsNotNone(dom.select_one("loquix-chat-composer"))
            self.assertIsNotNone(dom.select_one("loquix-attachment-panel"))
            resolve_incident(Path(tmp), record["id"])
            loaded, messages = load_incident(Path(tmp), record["id"])
            closed = BeautifulSoup(page_body(loaded, messages), "html.parser")
            self.assertIsNone(closed.select_one("loquix-chat-composer"))
            self.assertIn("resolved", closed.get_text())

    def test_notification_opens_reusable_chat_without_fabricating_supervisor_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle-2"
            cycle.mkdir()
            first = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="task-123",
                notification_id="current-stop:0123456789abcdef01",
                code="no_qualified_candidate", summary="Candidate reused H-0001 for a different proposition.",
                source={"observer_id": "abcdef0123456789"},
            )
            second = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="task-123",
                notification_id="current-stop:0123456789abcdef01",
                code="no_qualified_candidate", summary="Candidate reused H-0001 for a different proposition.",
            )
            self.assertEqual(first["id"], second["id"])
            from theory_master.supervisor_incident_session import load_incident
            record, messages = load_incident(root, first["id"])
            self.assertEqual(record["audit"]["source"], "notification")
            self.assertEqual([m["role"] for m in messages], ["tool"])
            self.assertEqual(messages[0]["metadata"]["source"], "notification_context")
            html = page_body(record, messages, task_id="abcdef0123456789")
            dom = BeautifulSoup(html, "html.parser")
            self.assertIn("通知上下文 · 只读来源记录", dom.get_text())
            self.assertIn("Candidate reused H-0001", dom.get_text())
            self.assertEqual(len(dom.select("loquix-message-item")), 0)
            self.assertIsNotNone(dom.select_one("loquix-chat-composer"))

    def test_notification_route_opens_same_chat_without_supervisor_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle-70"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({"status": "NEEDS_ATTENTION"}), encoding="utf-8")
            task = {"observer_id": "abcdef0123456789", "research_root": str(root),
                    "task_root": str(root), "cycle": "cycle-70", "cycle_dir": str(cycle),
                    "task_name": "Test task", "supervisor_enabled": False}

            class Cache:
                search_root = root
                def get(self): return {"runs": [task]}

            with patch("tm_observer.supervisor_notifications.monitor_data",
                       return_value={"status": "NEEDS_ATTENTION", "updated_at": "2026-10-18T00:00:00"}):
                from tm_observer.supervisor_notifications import notification_feed
                item = notification_feed([task])["items"][0]
            from urllib.parse import urlencode
            query = urlencode({"cycle": item["cycle"], "notification_id": item.get("session_id") or item.get("id")})
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/supervisor-notification/{task['observer_id']}?{query}"
                with patch("tm_observer.supervisor_notifications.monitor_data",
                           return_value={"status": "NEEDS_ATTENTION", "updated_at": "2026-10-18T00:00:00"}):
                    with urlopen(url, timeout=3) as response:
                        body = response.read().decode("utf-8")
                self.assertIn("Supervisor 接管对话", body)
                self.assertIn("Supervisor 历史", body)
                self.assertIn('data-mode="supervisor"', body)
                self.assertIn('/research-chat/assets/research-chat.js', body)
                asset_url = f"http://{server.server_address[0]}:{server.server_address[1]}/research-chat/assets/research-chat.js"
                with urlopen(asset_url, timeout=3) as response:
                    bundle = response.read()
                self.assertIn(b"/api/supervisor-session/", bundle)
                self.assertIn("只发送图片".encode("utf-8"), bundle)
                session_files = list((root / ".theory-master" / "supervisor_incidents" / "sessions").glob("SIS-*.json"))
                self.assertEqual(len(session_files), 1)
                session = json.loads(session_files[0].read_text(encoding="utf-8"))
                self.assertEqual(session["task_id"], "Test task")
                self.assertNotEqual(session["task_id"], task["observer_id"])
                self.assertEqual(session["trigger_type"], "needs_attention")
                state_url = f"http://{server.server_address[0]}:{server.server_address[1]}/api/supervisor-session/{session['id']}/state"
                with urlopen(state_url, timeout=3) as response:
                    chat_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual([row["role"] for row in chat_state["messages"]], ["system"])
                self.assertIn("通知上下文（只读来源记录", chat_state["messages"][0]["content"])
                self.assertIn("task_stopped", chat_state["messages"][0]["content"])
                progress_url = f"http://{server.server_address[0]}:{server.server_address[1]}/api/supervisor-session/{session['id']}/progress"
                with patch("theory_master.supervisor_incident_session.follow_up_pending", return_value=True):
                    with urlopen(progress_url, timeout=3) as response:
                        progress = json.loads(response.read().decode("utf-8"))
                self.assertTrue(progress["streaming"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_shared_supervisor_history_lists_notification_sessions_and_selects_exact_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle-70"
            cycle.mkdir()
            first = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="task-123",
                notification_id="current-stop:0123456789abcdef01",
                code="task_stopped", summary="first notification context",
            )
            second = open_notification_incident(
                research_root=root, cycle_dir=cycle, task_id="task-123",
                notification_id="current-stop:0123456789abcdef02",
                code="runtime_failure", summary="second notification context",
            )
            non_notification = publish_review_incident(
                research_root=root, cycle_dir=cycle, state={"cycle_id": "cycle-70", "status": "needs_attention"},
                trigger_type="protocol_violation", trigger_event="protocol_health=violation",
                analysis="runtime incident", model_invoked=True, system_note=None, tool_reads=[],
            )
            task = {"observer_id": "abcdef0123456789", "research_root": str(root), "task_name": "Test task"}
            from tm_observer.research_chat_ui import page_body as shared_chat_page_body
            from tm_observer.supervisor_chat import snapshot as supervisor_chat_snapshot
            view = supervisor_chat_snapshot(task, first["id"])
            self.assertEqual(view["session"]["id"], first["id"])
            self.assertEqual({row["id"] for row in view["sessions"]}, {first["id"], second["id"]})
            self.assertNotIn(non_notification["id"], {row["id"] for row in view["sessions"]})
            body = shared_chat_page_body(view, mode="supervisor")
            self.assertIn(f'data-session-id="{first["id"]}"', body)
            self.assertIn(f'/run/abcdef0123456789/supervisor/?session={first["id"]}', body)
            self.assertIn(f'/run/abcdef0123456789/supervisor/?session={second["id"]}', body)
            self.assertNotIn(non_notification["id"], body)

    def test_file_reads_are_collapsed(self):
        html = dialogue_html("SIS-0123456789abcdef", [
            {"role": "tool", "content": "read !events.jsonl"},
            {"role": "tool", "content": "read 107/run_state.json"},
            {"role": "tool", "content": "read /tmp/example-config/config.toml"},
            {"role": "supervisor", "content": "模型账号不匹配"},
        ], active=False, waiting=False)
        dom = BeautifulSoup(html, "html.parser")
        group = dom.select_one("details.tm-incident-reads")
        self.assertIsNotNone(group)
        self.assertIsNone(group.get("open"))
        self.assertIn("3", group.select_one("summary").get_text())
        self.assertIn("events.jsonl", group.get_text())
        self.assertEqual(len(dom.select("loquix-message-item")), 1)

    def test_http_get_and_post_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _open(root)
            task = {"observer_id": "abcdef0123456789", "research_root": str(root), "cycle": "cycle-70"}

            class Cache:
                search_root = root

                def get(self):
                    return {"runs": [task]}

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host = "127.0.0.1:" + str(server.server_address[1])
                page = "http://" + host + "/supervisor-session/" + record["id"]
                with urlopen(page, timeout=3) as response:
                    body = response.read().decode("utf-8")
                self.assertIn("Supervisor 接管对话", body)
                self.assertIn('data-mode="supervisor"', body)
                self.assertIn('href="/run/abcdef0123456789/"', body)
                self.assertIn("Supervisor 历史", body)
                state_url = "http://" + host + "/api/supervisor-session/" + record["id"] + "/state"
                with urlopen(state_url, timeout=3) as response:
                    chat_state = json.loads(response.read().decode("utf-8"))
                self.assertIn("开始分析原因。", "\n".join(row["content"] for row in chat_state["messages"]))
                url = "http://" + host + "/api/supervisor-session/" + record["id"] + "/message"
                raw = json.dumps({"content": "检查一下 router 的 ready 判断"}).encode()

                def post(headers):
                    req = Request(url, data=raw, headers=headers, method="POST")
                    try:
                        with urlopen(req, timeout=3) as response:
                            return response.status, response.read()
                    except HTTPError as error:
                        return error.code, error.read()

                self.assertEqual(post({})[0], 403)
                with patch("tm_observer.supervisor_incident_ui.submit_human", return_value={"ok": True}) as handler:
                    status, payload = post({
                        "Origin": "http://" + host,
                        "X-TM-Operator-Intent": "1",
                        "Content-Type": "application/json",
                    })
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(payload)["ok"], True)
                handler.assert_called_once()
                self.assertEqual(handler.call_args.args[2], "检查一下 router 的 ready 判断")
                with urlopen("http://" + host + "/vendor/loquix-incident.js", timeout=3) as response:
                    bundle = response.read()
                self.assertIn(b"loquix-chat-composer", bundle)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
