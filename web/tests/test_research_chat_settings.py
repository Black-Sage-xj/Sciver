from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from tm_observer.research_chat import (
    ResearchChatError, create, set_model, set_reasoning_effort, snapshot,
)
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class ResearchChatSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "observer-state"
        self.state_dir.mkdir()
        self.state = {"runs": []}

    def test_model_and_effort_are_validated_and_persisted(self):
        ident = create(self.state_dir, self.state, {"mode": "migration"})["session_id"]
        initial = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(initial["model"], "gpt-6-sol")
        self.assertEqual(initial["reasoning_effort"], "medium")
        self.assertEqual(set_model(self.state_dir, ident, "gpt-6-sol")["model"], "gpt-6-sol")
        self.assertEqual(set_reasoning_effort(self.state_dir, ident, "high")["reasoning_effort"], "high")
        updated = snapshot(self.state_dir, self.state, ident)["session"]
        self.assertEqual(updated["model"], "gpt-6-sol")
        self.assertEqual(updated["reasoning_effort"], "high")
        with self.assertRaises(ResearchChatError):
            set_model(self.state_dir, ident, "not-a-model")
        with self.assertRaises(ResearchChatError):
            set_reasoning_effort(self.state_dir, ident, "extreme")

    def test_selected_effort_is_applied_to_codex_thread_config(self):
        from tm_observer.research_chat import _chat_routes
        from theory_master.codex_backend import CodexBackend

        routes = _chat_routes("gpt-6.1-sol", "xhigh")
        backend = CodexBackend.__new__(CodexBackend)
        backend.routes = routes
        provider = routes["research_chat"].provider
        config = backend._thread_config("research_chat", provider)
        self.assertEqual(config["model_reasoning_effort"], "xhigh")
        self.assertEqual(routes["research_chat"].model, "gpt-6.1-sol")

    def test_settings_http_api_persists_both_choices(self):
        cache = StateCache(self.root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache,
                                      field_notes_state_dir=self.state_dir))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            create_request = Request(base + "/api/research-chat/session",
                                     data=json.dumps({"mode": "migration"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Origin": base,
                                              "X-TM-Operator-Intent": "1"})
            with urlopen(create_request, timeout=5) as response:
                ident = json.loads(response.read())["session_id"]

            with urlopen(base + f"/api/research-chat/{ident}/settings", timeout=5) as response:
                settings = json.loads(response.read())
            self.assertEqual(settings["model"], "gpt-6-sol")
            self.assertEqual(settings["reasoning_effort"], "medium")
            self.assertIn("xhigh", settings["available_reasoning_efforts"])

            for suffix, payload in (("model", {"model": "gpt-6-sol"}), ("effort", {"effort": "high"})):
                request = Request(base + f"/api/research-chat/{ident}/{suffix}",
                                  data=json.dumps(payload).encode("utf-8"),
                                  headers={"Content-Type": "application/json", "Origin": base,
                                           "X-TM-Operator-Intent": "1"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)

            with urlopen(base + f"/api/research-chat/{ident}/settings", timeout=5) as response:
                settings = json.loads(response.read())
            self.assertEqual(settings["model"], "gpt-6-sol")
            self.assertEqual(settings["reasoning_effort"], "high")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
