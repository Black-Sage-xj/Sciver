"""Real HTTP/browser regression for historical inline-code artifact citations.

This creates only temporary local fixtures and never invokes a model or Cycle.
"""
from __future__ import annotations

import io
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / "web")]

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from tm_observer import research_chat as chat
from tm_observer.research_chat_models import cached_catalog
from tm_observer.research_preparation import workspace
from tm_observer.web import ThreadingHTTPServer, make_handler


def main():
    with tempfile.TemporaryDirectory(prefix="sciver-chat-artifacts-") as folder:
        root = Path(folder)
        state_dir = root / "account-state"
        state = {"search_root": str(root), "runs": []}
        sid = chat.create(state_dir, state, {"mode": "preparation", "entry": "clear"})["session_id"]
        output = workspace(state_dir, sid) / "anderson_fig3"
        output.mkdir()
        image = io.BytesIO()
        Image.new("RGB", (900, 500), "#73a7c7").save(image, "PNG")
        (output / "fig3_pilot.png").write_bytes(image.getvalue())
        (output / "summary.csv").write_text("L,xi\n4,0.283", encoding="utf-8")
        (output / "reproduce.py").write_text("raise RuntimeError('This fixture must never run')", encoding="utf-8")
        record = chat._read(chat._store(state_dir), sid)
        record["messages"] = [{"id": "historical-pilot", "role": "assistant", "artifacts": [],
            "content": "已有结果图在 `anderson_fig3/fig3_pilot.png`，数据在 `anderson_fig3/summary.csv:1`，脚本在 `anderson_fig3/reproduce.py:1`。"}]
        chat._write(chat._store(state_dir), record)
        original = (chat._store(state_dir) / f"{sid}.json").read_bytes()

        class Cache:
            search_root = root
            def get(self):
                return state

        handler = make_handler(Cache(), field_notes_state_dir=state_dir)
        class QuietHandler(handler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with patch("theory_master.codex_backend.CodexBackend", side_effect=AssertionError("No model calls permitted")), \
                 patch("tm_observer.research_chat_models.refresh_catalog", side_effect=lambda **kwargs: cached_catalog()), \
                 sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/research-chat/?session=" + sid)
                plot = page.locator(".sc-message-image img")
                expect(plot).to_be_visible()
                assert plot.evaluate("image => image.complete && image.naturalWidth === 900")
                assert plot.bounding_box()["width"] >= 500
                page.get_by_role("button", name="查看图片 fig3_pilot.png", exact=True).click()
                expect(page.get_by_role("dialog", name="fig3_pilot.png")).to_be_visible()
                assert page.locator(".sc-preview-image").evaluate("image => image.complete && image.naturalHeight === 500")
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).to_have_count(0)
                table = page.locator(".tm-rich-content a").filter(has_text="anderson_fig3/summary.csv:1")
                response = page.request.get(origin + table.get_attribute("href"))
                assert response.status == 200 and response.text() == "L,xi\n4,0.283"
                source = page.locator(".tm-rich-content a").filter(has_text="anderson_fig3/reproduce.py:1")
                response = page.request.get(origin + source.get_attribute("href"))
                assert response.status == 200 and response.headers["content-type"] == "application/octet-stream"
                page.reload()
                expect(page.locator(".sc-message-image img")).to_be_visible()
                assert page.locator(".sc-message-image img").evaluate("image => image.complete && image.naturalWidth === 900")
                assert not errors, errors
                browser.close()
            assert (chat._store(state_dir) / f"{sid}.json").read_bytes() == original
            print("PASS: historical code citations show a readable inline image, enlarge in chat, survive reload and open data/source; no model or research execution")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    main()
