"""Storage and filesystem-boundary regressions for chat organization."""
from __future__ import annotations

import json
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from tm_observer import research_chat as chat
from tm_observer.research_chat_management import artifact_content, export_session, list_sessions, manage_session


class ResearchChatManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "state"
        self.task = self.root / "task"
        self.task.mkdir()
        self.state = {"runs": [{"observer_id": "a" * 16, "task_root": str(self.task), "research_root": str(self.task)}]}
        self.one = self._session("Earlier chat", "2026-11-17T10:00:00+00:00", "alpha", "首条科研证据 MixedCASE")
        self.two = self._session("Later chat", "2026-11-18T10:00:00+00:00", "beta", "another result")

    def _session(self, title, stamp, task, content):
        sid = chat.create(self.state_dir, {}, {"mode": "migration"})["session_id"]
        row = chat._read(chat._store(self.state_dir), sid)
        row.update(title=title, updated_at=stamp, task_id=task,
                   messages=[{"id": "message-" + sid, "role": "assistant", "content": content, "created_at": stamp}],
                   codex_thread_id="private-native-thread", provider_token="private-provider-token")
        chat._write(chat._store(self.state_dir), row)
        return sid

    def test_title_and_full_message_search_return_matching_message_and_project_scope(self):
        found = list_sessions(self.state_dir, query="科研证据")
        self.assertEqual([item["id"] for item in found], [self.one])
        self.assertEqual(found[0]["match_message_id"], "message-" + self.one)
        self.assertIn("科研证据", found[0]["snippet"])
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir, query="mixedcase")], [self.one])
        self.assertEqual(list_sessions(self.state_dir, query="mixedcase", task_id="beta"), [])
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir, query="LATER")], [self.two])
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir, task_id="alpha")], [self.one])

    def test_pins_override_recency_without_changing_conversation(self):
        before = (chat._store(self.state_dir) / f"{self.one}.json").read_bytes()
        self.assertEqual(list_sessions(self.state_dir)[0]["id"], self.two)
        manage_session(self.state_dir, self.one, {"action": "pin"})
        self.assertEqual(list_sessions(self.state_dir)[0]["id"], self.one)
        manage_session(self.state_dir, self.one, {"action": "pin", "value": False})
        self.assertEqual(list_sessions(self.state_dir)[0]["id"], self.two)
        self.assertEqual((chat._store(self.state_dir) / f"{self.one}.json").read_bytes(), before)

    def test_archive_soft_delete_restore_and_view_filters_preserve_files(self):
        manage_session(self.state_dir, self.one, {"action": "archive"})
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir)], [self.two])
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir, view="archived")], [self.one])
        self.assertEqual(len(list_sessions(self.state_dir, view="all")), 2)
        manage_session(self.state_dir, self.one, {"action": "delete"})
        self.assertEqual(list_sessions(self.state_dir, view="archived"), [])
        self.assertEqual([item["id"] for item in list_sessions(self.state_dir, view="trash")], [self.one])
        self.assertTrue((chat._store(self.state_dir) / f"{self.one}.json").is_file())
        manage_session(self.state_dir, self.one, {"action": "restore"})
        self.assertEqual(list_sessions(self.state_dir, view="trash"), [])
        self.assertEqual(len(list_sessions(self.state_dir)), 2)

    def test_running_chat_cannot_archive_or_delete_but_can_pin(self):
        with patch.dict(chat._JOBS, {self.one: {"status": "streaming"}}):
            for action in ("archive", "delete"):
                with self.subTest(action=action), self.assertRaises(chat.ResearchChatError) as rejected:
                    manage_session(self.state_dir, self.one, {"action": action})
                self.assertEqual(rejected.exception.status, 409)
            self.assertTrue(manage_session(self.state_dir, self.one, {"action": "pin"})["pinned"])

    def test_exports_preserve_unicode_and_attachment_names_without_runtime_identity(self):
        row = chat._read(chat._store(self.state_dir), self.one)
        row["messages"][0]["attachments"] = [{"id": "f" * 32, "name": "论文.pdf", "url": "/safe-download"}]
        chat._write(chat._store(self.state_dir), row)
        body, kind, name = export_session(self.state_dir, self.one, "json")
        exported = json.loads(body)
        self.assertEqual(exported["messages"][0]["content"], "首条科研证据 MixedCASE")
        self.assertEqual(exported["messages"][0]["attachments"][0]["name"], "论文.pdf")
        self.assertNotIn(b"private-provider-token", body)
        self.assertNotIn(b"private-native-thread", body)
        self.assertEqual(kind, "application/json; charset=utf-8")
        self.assertTrue(name.endswith(".json"))
        body, kind, name = export_session(self.state_dir, self.one)
        self.assertIn("论文.pdf", body.decode())
        self.assertIn("## Sciver", body.decode())
        self.assertTrue(name.endswith(".md"))
        with self.assertRaises(chat.ResearchChatError):
            export_session(self.state_dir, self.one, "html")

    def test_corrupt_and_symlink_session_records_are_not_enumerated(self):
        folder = chat._store(self.state_dir)
        (folder / ("c" * 32 + ".json")).write_text("not json")
        outside = self.root / "session.json"
        outside.write_text(json.dumps({"id": "d" * 32, "title": "private"}))
        (folder / ("d" * 32 + ".json")).symlink_to(outside)
        self.assertEqual(len(list_sessions(self.state_dir)), 2)
        with self.assertRaises(chat.ResearchChatError):
            manage_session(self.state_dir, "../outside", {"action": "delete"})

    def _artifact_row(self, path: Path):
        row = chat._read(chat._store(self.state_dir), self.one)
        row["task_id"] = "a" * 16
        row["messages"] = [{"id": "linked-output", "role": "assistant", "content": f"[result]({path})"}]
        chat._write(chat._store(self.state_dir), row)

    def test_artifact_reads_bound_task_and_unknown_html_is_download_only(self):
        text = self.task / "result.txt"
        text.write_text("科研成果", encoding="utf-8")
        self._artifact_row(text)
        with patch("tm_observer.canvas_md.vault_root", return_value=self.task):
            body, kind, name = artifact_content(self.state_dir, self.root, self.state, self.one, str(text))
            self.assertEqual(body.decode(), "科研成果")
            self.assertEqual(kind, "text/plain")
            html = self.task / "result.html"
            html.write_text("<script>alert(1)</script>")
            self.assertEqual(artifact_content(self.state_dir, self.root, self.state, self.one, str(html))[1], "application/octet-stream")

    def test_artifact_rejects_outside_hidden_and_encoded_traversal(self):
        outside = self.root / "outside.txt"
        outside.write_text("private")
        hidden = self.task / ".env"
        hidden.write_text("private")
        self._artifact_row(outside)
        with patch("tm_observer.canvas_md.vault_root", return_value=self.task):
            for target in (str(outside), str(hidden), "%2e%2e/outside.txt", "https://example.com/result.txt", "file://evilhost/etc/passwd", "../outside.txt"):
                with self.subTest(target=target), self.assertRaises(chat.ResearchChatError):
                    artifact_content(self.state_dir, self.root, self.state, self.one, target)

    def test_artifact_rejects_symlink_files_and_directories_even_inside_allowed_root(self):
        nested = self.task / "nested"
        nested.mkdir()
        content = nested / "report.txt"
        content.write_text("valid output")
        file_link = self.task / "file-link.txt"
        file_link.symlink_to(content)
        dir_link = self.task / "dir-link"
        dir_link.symlink_to(nested, target_is_directory=True)
        self._artifact_row(content)
        with patch("tm_observer.canvas_md.vault_root", return_value=self.task):
            for target in (file_link, dir_link / "report.txt"):
                with self.subTest(target=target), self.assertRaises(chat.ResearchChatError):
                    artifact_content(self.state_dir, self.root, self.state, self.one, str(target))

    def test_shared_workspace_requires_link_in_same_session(self):
        shared = self.state_dir / "research-chat-model"
        shared.mkdir()
        output = shared / "result.txt"
        output.write_text("session result")
        row = chat._read(chat._store(self.state_dir), self.one)
        row["messages"] = [{"id": "m", "role": "assistant", "content": f"[result]({output})"}]
        chat._write(chat._store(self.state_dir), row)
        self.assertEqual(artifact_content(self.state_dir, self.root, {}, self.one, str(output))[0], b"session result")
        with self.assertRaises(chat.ResearchChatError):
            artifact_content(self.state_dir, self.root, {}, self.two, str(output))

    def test_legacy_plot_restores_in_snapshot_and_survives_preparation_handoff(self):
        from tm_observer.research_chat_attachments import attachment_content
        from tm_observer.research_preparation import workspace
        sid = chat.create(self.state_dir, {}, {"mode": "preparation", "entry": "clear"})["session_id"]
        folder = workspace(self.state_dir, sid) / "anderson_fig3"
        folder.mkdir()
        image = io.BytesIO()
        Image.new("RGB", (12, 8), "blue").save(image, "PNG")
        (folder / "fig3_pilot.png").write_bytes(image.getvalue())
        (folder / "summary.csv").write_text("L,xi\n4,0.283")
        row = chat._read(chat._store(self.state_dir), sid)
        original = "图在 `anderson_fig3/fig3_pilot.png`，数据在 `anderson_fig3/summary.csv:1`。"
        row["messages"] = [{"id": "pilot", "role": "assistant", "content": original, "artifacts": []}]
        chat._write(chat._store(self.state_dir), row)
        before = (chat._store(self.state_dir) / f"{sid}.json").read_bytes()
        view = chat.snapshot(self.state_dir, {}, sid)["session"]
        plot, table = view["messages"][0]["artifacts"]
        self.assertEqual((plot["kind"], table["name"]), ("image", "summary.csv"))
        self.assertEqual(view["messages"][0]["content"], original)
        self.assertEqual((chat._store(self.state_dir) / f"{sid}.json").read_bytes(), before)
        self.assertEqual(attachment_content(self.state_dir, sid, plot["id"])[0], image.getvalue())
        row.update(mode="project", task_id="a" * 16)
        chat._write(chat._store(self.state_dir), row)
        with patch("tm_observer.canvas_md.vault_root", return_value=self.task):
            self.assertEqual(artifact_content(self.state_dir, self.root, self.state, sid, "anderson_fig3/summary.csv:1")[0], b"L,xi\n4,0.283")
        self.assertEqual(chat.snapshot(self.state_dir, {}, sid)["session"]["messages"][0]["artifacts"][0]["id"], plot["id"])
        with self.assertRaises(chat.ResearchChatError):
            artifact_content(self.state_dir, self.root, {}, self.two, str(folder / "fig3_pilot.png"))
        other_account = self.root / "other-account"
        chat._store(other_account)
        with self.assertRaises(chat.ResearchChatError):
            artifact_content(other_account, self.root, {}, sid, str(folder / "fig3_pilot.png"))

    def test_shared_workspace_rejects_user_mentions_and_nonmatching_assistant_links(self):
        shared = self.state_dir / "research-chat-model"
        shared.mkdir()
        output = shared / "result.txt"
        output.write_text("another chat's result")
        row = chat._read(chat._store(self.state_dir), self.two)
        for message in (
            {"role": "user", "content": "请分析 result.txt"},
            {"role": "user", "content": f"[result]({output})"},
            {"role": "assistant", "content": "文件名为 result.txt"},
            {"role": "assistant", "content": "[result](other-result.txt)"},
        ):
            with self.subTest(message=message):
                row["messages"] = [message]
                chat._write(chat._store(self.state_dir), row)
                for target in ("result.txt", str(output)):
                    with self.assertRaises(chat.ResearchChatError) as rejected:
                        artifact_content(self.state_dir, self.root, {}, self.two, target)
                    self.assertEqual(rejected.exception.status, 404)
        row["messages"] = [{"role": "assistant", "content": "[result](result.txt)"}]
        chat._write(chat._store(self.state_dir), row)
        self.assertEqual(artifact_content(self.state_dir, self.root, {}, self.two, "result.txt")[0],
                         b"another chat's result")

    def test_concurrent_organization_changes_do_not_lose_other_sessions(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda pair: manage_session(self.state_dir, pair[0], pair[1]),
                [(self.one, {"action": "pin", "value": True}), (self.two, {"action": "archive", "value": True}),
                 (self.one, {"action": "archive", "value": True}), (self.two, {"action": "pin", "value": True})]))
        self.assertTrue(all(item["ok"] for item in results))
        rows = list_sessions(self.state_dir, view="archived")
        self.assertEqual({row["id"] for row in rows}, {self.one, self.two})
        self.assertTrue(all(row["pinned"] for row in rows))


if __name__ == "__main__":
    unittest.main()
