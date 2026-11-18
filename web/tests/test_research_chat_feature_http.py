"""Exercise public feature routes through a real server, with no model calls."""
from __future__ import annotations

import base64
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote, urlencode
from unittest.mock import patch

from PIL import Image

from tm_observer import research_chat as chat
from tm_observer.auth import BasicAuthCredentials
from tm_observer.web import ThreadingHTTPServer, make_handler


class ResearchChatFeatureHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "state"
        self.state = {"runs": [{"observer_id": "a" * 16, "task_name": "project", "task_root": str(self.root / "task"), "research_root": str(self.root / "task/research")}]}
        outer = self
        class Cache:
            search_root = outer.root
            def get(self):
                return outer.state
        handler = make_handler(Cache(), auth=BasicAuthCredentials("test", "temporary-password"), field_notes_state_dir=self.state_dir)
        class QuietHandler(handler):
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.authorization = "Basic " + base64.b64encode(b"test:temporary-password").decode()
        self.sid = chat.create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]
        self.other = chat.create(self.state_dir, self.state, {"mode": "project", "task_id": "a" * 16})["session_id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        with chat._JOBS_LOCK:
            for file in chat._store(self.state_dir).glob("*.json"):
                chat._JOBS.pop(file.stem, None)

    def request(self, path, body=None, *, method=None, auth=True, origin=True, intent=True, media="application/json", headers=None):
        request_headers = {}
        if auth:
            request_headers["Authorization"] = self.authorization
        if body is not None:
            request_headers["Content-Type"] = media
            if origin:
                request_headers["Origin"] = self.origin if origin is True else origin
            if intent:
                request_headers["X-TM-Operator-Intent"] = "1"
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method or ("POST" if body is not None else "GET"), path, body, request_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def upload(self, sid=None, *, auth=True, origin=True, intent=True, data=None, name="实验记录.txt", media="text/plain"):
        return self.request(f"/api/research-chat/{sid or self.sid}/attachments", data if data is not None else "原始文字内容".encode(), auth=auth, origin=origin, intent=intent, media=media, headers={"X-File-Name": quote(name)})

    def test_authentication_and_csrf_apply_before_upload_or_management(self):
        status, _, _ = self.upload(auth=False)
        self.assertEqual(status, 401)
        for kwargs in ({"origin": False}, {"origin": "https://evil.example"}, {"intent": False}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.upload(**kwargs)[0], 403)
                self.assertEqual(self.request(f"/api/research-chat/{self.sid}/manage", {"action": "delete"}, **kwargs)[0], 403)
        for endpoint in ("/api/research-chat/sessions", f"/api/research-chat/{self.sid}/export", "/research-chat/history.js"):
            status, body, headers = self.request(endpoint, auth=False)
            self.assertIn(status, {303, 401})
            self.assertNotIn(b"messages", body)
        status, body, _ = self.request("/api/research-chat/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["sessions"]), 2)

    def test_raw_upload_metadata_download_and_cross_session_isolation(self):
        data = "论文材料\n实验数值：1,2,3".encode()
        status, body, _ = self.upload(data=data)
        self.assertEqual(status, 201)
        meta = json.loads(body)["attachment"]
        self.assertEqual(meta["name"], "实验记录.txt")
        self.assertEqual(meta["status"], "ready")
        self.assertNotIn("storage_name", meta)
        self.assertNotIn(str(self.state_dir), body.decode())
        status, content, headers = self.request(meta["url"] + "?download=1")
        self.assertEqual((status, content), (200, data))
        self.assertIn("attachment;", headers["Content-Disposition"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("UTF-8''", headers["Content-Disposition"])
        self.assertEqual(self.request(meta["url"].replace(self.sid, self.other))[0], 404)
        self.assertEqual(self.request(f"/api/research-chat/{self.other}/attachments/{meta['id']}")[0], 404)
        status, body, _ = self.request(f"/api/research-chat/{self.sid}/attachments")
        self.assertEqual(json.loads(body)["attachments"][0]["id"], meta["id"])

    def test_real_image_upload_inline_response_has_sandbox_and_no_sniff(self):
        output = io.BytesIO()
        Image.new("RGB", (6, 4), "red").save(output, format="PNG")
        status, body, _ = self.upload(data=output.getvalue(), name="plot.png", media="image/png")
        self.assertEqual(status, 201)
        meta = json.loads(body)["attachment"]
        status, body, headers = self.request(meta["preview_url"])
        self.assertEqual(body, output.getvalue())
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_upload_rejects_spoofed_content_empty_files_and_invalid_session(self):
        self.assertEqual(self.upload(data=b"not an image", name="fake.png", media="image/png")[0], 400)
        self.assertEqual(self.upload(data=b"")[0], 413)
        self.assertEqual(self.upload(sid="z" * 32)[0], 404)
        self.assertEqual(self.upload(sid="f" * 32)[0], 404)
        self.assertEqual(self.upload(data=b"doc", name="old.doc", media="application/msword")[0], 400)

    def test_manage_and_fulltext_project_filters_work_over_http(self):
        row = chat._read(chat._store(self.state_dir), self.sid)
        row["messages"] = [{"id": "message-evidence", "role": "assistant", "content": "边界条件证据与实验材料"}]
        chat._write(chat._store(self.state_dir), row)
        path = "/api/research-chat/sessions?" + urlencode({"q": "边界条件证据", "task_id": "a" * 16})
        status, body, _ = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sessions"][0]["match_message_id"], "message-evidence")
        for action, view in (("pin", "active"), ("archive", "archived"), ("delete", "trash"), ("restore", "active")):
            status, _, _ = self.request(f"/api/research-chat/{self.sid}/manage", {"action": action})
            self.assertEqual(status, 200)
            rows = json.loads(self.request("/api/research-chat/sessions?view=" + view)[1])["sessions"]
            self.assertIn(self.sid, [row["id"] for row in rows])

    def test_exports_have_download_headers_and_preserve_unicode(self):
        row = chat._read(chat._store(self.state_dir), self.sid)
        row.update(title="导出实验", codex_thread_id="private-thread", token="private-token")
        row["messages"] = [{"id": "one", "role": "user", "content": "中文科研问题"}]
        chat._write(chat._store(self.state_dir), row)
        for fmt, media in (("markdown", "text/markdown"), ("json", "application/json")):
            status, body, headers = self.request(f"/api/research-chat/{self.sid}/export?format=" + fmt)
            self.assertEqual(status, 200)
            self.assertIn("中文科研问题", body.decode())
            self.assertNotIn(b"private-token", body)
            self.assertNotIn(b"private-thread", body)
            self.assertTrue(headers["Content-Type"].startswith(media))
            self.assertIn("attachment;", headers["Content-Disposition"])
        self.assertEqual(self.request(f"/api/research-chat/{self.sid}/export?format=html")[0], 400)

    def test_long_chinese_message_is_durably_accepted_and_character_limit_rejected(self):
        content = "科研" * (chat.MAX_MESSAGE_CHARS // 2)
        self.assertGreater(len(json.dumps({"content": content}, ensure_ascii=False).encode()), 9000)
        invoked = threading.Event()
        def fake_send(*args, **kwargs):
            invoked.set()
            return {"ok": True}
        with patch("tm_observer.research_chat.send", side_effect=fake_send):
            status, body, _ = self.request(f"/api/research-chat/{self.sid}/message", {"content": content})
            self.assertTrue(invoked.wait(2), "HTTP worker must enter the fake send before patch cleanup")
        self.assertEqual(status, 202, body)
        stored = chat._read(chat._store(self.state_dir), self.sid)
        self.assertEqual(stored["messages"][-1]["content"], content)
        self.assertEqual(self.request(f"/api/research-chat/{self.other}/message", {"content": content + "字"})[0], 400)

    def test_runtime_routes_dispatch_to_correct_backend_function(self):
        cases = [("queue", {"action": "add", "content": "next"}, "queue_message"),
                 ("queue", {"action": "edit", "queue_id": "q", "content": "edited"}, "update_queued_message"),
                 ("queue", {"action": "remove", "queue_id": "q"}, "remove_queued_message"),
                 ("queue", {"action": "resume"}, "resume_queue"),
                 ("steer", {"content": "supplement"}, "steer"),
                 ("branch", {"branch_id": "b"}, "switch_branch"),
                 ("fork", {"message_id": "m"}, "fork_branch"),
                 ("answer", {"request_id": "r", "answers": {"q": ["a"]}}, "answer_question")]
        for route, payload, function in cases:
            with self.subTest(route=route, action=payload.get("action")):
                with patch("tm_observer.research_chat." + function, return_value={"ok": True, "mapped": function}) as mocked:
                    status, body, _ = self.request(f"/api/research-chat/{self.sid}/" + route, payload)
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["mapped"], function)
                mocked.assert_called_once()
                self.assertIn(self.sid, mocked.call_args.args)

    def test_history_script_and_artifact_error_are_served_without_sensitive_data(self):
        status, body, headers = self.request("/research-chat/history.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        for marker in ("X-TM-Operator-Intent", "textContent", "match_message_id", "view", "task_id"):
            self.assertIn(marker, body.decode())
        self.assertNotIn("innerHTML", body.decode())
        allowed = self.root / "task"
        allowed.mkdir()
        with patch("tm_observer.canvas_md.vault_root", return_value=allowed):
            status, body, _ = self.request(f"/api/research-chat/{self.sid}/artifact?" + urlencode({"path": "/etc/passwd"}))
        self.assertEqual(status, 404)
        self.assertNotIn(b"root:", body)

    def test_artifact_invalid_task_mapping_is_a_safe_http_error(self):
        # The mock cache intentionally has a stale/unverified mapping. Invalid
        # graph bindings should produce JSON, never reset the HTTP connection.
        status, body, _ = self.request(f"/api/research-chat/{self.sid}/artifact?" + urlencode({"path": "/etc/passwd"}))
        self.assertIn(status, {403, 404, 409})
        self.assertIn("error", json.loads(body))

    def test_artifact_malformed_url_is_rejected_without_resetting_connection(self):
        for target in ("file://[", "//[", "file://localhost/[broken", "%00.txt", "folder%5Cprivate.txt"):
            with self.subTest(target=target), patch("tm_observer.canvas_md.vault_root", return_value=self.root):
                status, body, _ = self.request(f"/api/research-chat/{self.sid}/artifact?" + urlencode({"path": target}))
                self.assertIn(status, {400, 403, 404})
                self.assertIn("error", json.loads(body))

    def test_question_answers_cross_http_while_model_holds_session_lock(self):
        pending = threading.Event()
        received = threading.Event()
        sdk_replies = []
        class FakeHandle:
            def steer(self, *args):
                raise AssertionError("Do not send RPC steering while SDK reader waits for a question")
        def fake_model_reply(*args, **kwargs):
            kwargs["on_handle"](FakeHandle())
            pending.set()
            answer = kwargs["request_user_input"]({"itemId": "request-http", "isBlocking": True,
                "questions": [{"id": "choice", "header": "范围", "question": "请选择范围", "isOther": True,
                               "options": [{"label": "当前项目", "description": "只审查当前数据"}]}]})
            sdk_replies.append(answer)
            received.set()
            return "已收到你的范围选择。"
        try:
            with patch("tm_observer.research_chat._model_reply", side_effect=fake_model_reply):
                status, body, _ = self.request(f"/api/research-chat/{self.sid}/message", {"content": "请先询问范围"})
                self.assertEqual(status, 202, body)
                self.assertTrue(pending.wait(2))
                deadline = time.monotonic() + 2
                while not chat._runtime(chat._store(self.state_dir), self.sid).get("pending_questions") and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(chat._runtime(chat._store(self.state_dir), self.sid).get("pending_questions"))
                self.assertEqual(self.request(f"/api/research-chat/{self.sid}/steer", {"content": "额外要求"})[0], 409)
                payload = {"request_id": "request-http", "answers": {"choice": ["当前项目"]}}
                self.assertEqual(self.request(f"/api/research-chat/{self.other}/answer", payload)[0], 409)
                self.assertEqual(self.request(f"/api/research-chat/{self.sid}/answer", {"request_id": "request-http", "answers": {}})[0], 400)
                status, body, _ = self.request(f"/api/research-chat/{self.sid}/answer", payload)
                self.assertEqual(status, 200, body)
                self.assertTrue(json.loads(body)["answered"])
                self.assertTrue(received.wait(2), "Question callback must unblock from the HTTP answer")
                self.assertEqual(sdk_replies, [{"answers": {"choice": {"answers": ["当前项目"]}}}])
                # Repeated delivery of the same request is safe; a changed
                # answer to an already resolved question is rejected.
                self.assertEqual(self.request(f"/api/research-chat/{self.sid}/answer", payload)[0], 200)
                self.assertEqual(self.request(f"/api/research-chat/{self.sid}/answer", {"request_id": "request-http", "answers": {"choice": ["other"]}})[0], 409)
        finally:
            self.request(f"/api/research-chat/{self.sid}/stop", {})

    def test_slow_steer_does_not_hold_global_lock_or_block_stop(self):
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()
        results = {}
        class SlowHandle:
            def steer(self, value):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test steering timed out")
        with chat._JOBS_LOCK:
            chat._JOBS[self.sid] = {"id": "test-active", "status": "streaming", "handle": SlowHandle(), "cancel": threading.Event()}
        chat._runtime_update(chat._store(self.state_dir), self.sid, lambda value: value.update({"run": {"id": "test-active", "status": "streaming"}}))
        def steering():
            results["steer"] = self.request(f"/api/research-chat/{self.sid}/steer", {"content": "正在传递的要求"})
        def stopping():
            results["stop"] = self.request(f"/api/research-chat/{self.sid}/stop", {})
            stopped.set()
        steer_thread = threading.Thread(target=steering)
        stop_thread = threading.Thread(target=stopping)
        steer_thread.start()
        try:
            self.assertTrue(entered.wait(2))
            stop_thread.start()
            self.assertTrue(stopped.wait(0.5), "Stop must set cancellation before the blocked steering RPC returns")
            self.assertEqual(results["stop"][0], 200)
            self.assertTrue(chat._JOBS[self.sid]["cancel"].is_set())
        finally:
            release.set()
            steer_thread.join(timeout=4)
            if stop_thread.ident:
                stop_thread.join(timeout=4)


if __name__ == "__main__":
    unittest.main()
