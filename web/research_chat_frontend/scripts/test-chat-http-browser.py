"""Real Python HTTP + built React integration, with only Codex replaced by a fake."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / "web")]

from PIL import Image
from playwright.sync_api import sync_playwright, expect
from tm_observer import research_chat as chat
from tm_observer.research_chat_models import cached_catalog
from tm_observer.web import ThreadingHTTPServer, make_handler
from theory_master.codex_backend import CodexTurnCancelled


class FakeCodex:
    calls = []
    released = threading.Event()
    project = None

    def __init__(self, *args, **kwargs):
        self.steered = []

    def ensure_thread(self, role, thread, model, provider):
        return thread or "offline-thread"

    def steer(self, value):
        self.steered.append(value)

    def close(self):
        pass

    def invoke(self, role, prompt, thread, model, provider, *, on_delta=None, on_event=None,
               on_handle=None, input_images=None, cancel_event=None, request_user_input=None):
        self.calls.append({"images": input_images, "prompt": prompt})
        on_handle(self)
        on_event({"type": "reasoning_summary", "kind": "reasoning", "item_id": "summary", "text": "核对附件和数据"})
        on_event({"type": "tool", "item_id": "file-read", "label": "读取附件", "status": "complete"})
        on_delta("正在整理结果。")
        while not self.released.wait(0.02):
            if cancel_event.is_set():
                raise CodexTurnCancelled("stopped")
        if "choice-flow" in prompt:
            answer = request_user_input({"itemId": "offline-choice", "questions": [{"id": "choice", "header": "方法", "question": "选择方法", "options": [{"label": "方法甲"}, {"label": "方法乙"}]}]})
            assert answer == {"answers": {"choice": {"answers": ["方法甲"]}}}
        on_event({"type": "usage", "raw_usage": {"input_tokens": 50, "output_tokens": 20}, "last_request_usage": {"input_tokens": 50, "output_tokens": 20}})
        return thread, "# 验证结果\n\n## 数据\n\n| 指标 | 值 |\n|---|---|\n| 数值 | 3 |\n\n## 代码\n\n```python\nprint(3)\n```\n\n## 文件\n\n[结果文件](" + str(self.project / "result.txt") + ")\n\n离线浏览器验收完成。"


def main():
    with tempfile.TemporaryDirectory(prefix="sciver-chat-http-") as folder:
        root = Path(folder)
        project = root / "笔记区" / "浏览器验收"
        project.mkdir(parents=True)
        (project / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (project / "result.txt").write_text("离线成果文件", encoding="utf-8")
        task = hashlib.sha256(str(project).encode()).hexdigest()[:16]
        state_dir = root / "state"
        state = {"runs": [{"observer_id": task, "task_name": "浏览器验收", "task_root": str(project), "research_root": str(project)}]}
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
        sid = chat.create(state_dir, state, {"mode": "project", "task_id": task})["session_id"]
        FakeCodex.calls = []
        FakeCodex.project = project
        FakeCodex.released.clear()
        try:
            with patch("theory_master.codex_backend.CodexBackend", FakeCodex), patch("tm_observer.research_chat_models.refresh_catalog", side_effect=lambda **kwargs: cached_catalog()), sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/research-chat/?session=" + sid)
                composer = page.get_by_role("textbox", name="发送给 Sciver 的消息", exact=True)
                expect(composer).to_be_visible()
                image = io.BytesIO(); Image.new("RGB", (12, 10), "blue").save(image, "PNG")
                page.locator('input[type="file"]').set_input_files({"name": "证据.png", "mimeType": "image/png", "buffer": image.getvalue()})
                expect(page.get_by_label("待发送附件")).to_contain_text("已就绪")
                composer.fill("图片证据：分析附件")
                page.get_by_label("发送消息", exact=True).click()
                expect(page.get_by_label("生成期间发送方式")).to_be_visible()
                composer.fill("后续排队问题")
                page.get_by_label("加入待发送队列", exact=True).click()
                expect(page.get_by_label("待发送消息队列")).to_contain_text("后续排队问题")
                FakeCodex.released.set()
                expect(page.locator('[data-chat-message-id]')).to_have_count(4, timeout=15000)
                expect(page.locator('.sc-queue-panel')).to_have_count(0)
                assert len(FakeCodex.calls) == 2 and FakeCodex.calls[0]["images"]
                expect(page.locator('.sc-run-metadata').first).to_contain_text("用时")
                page.reload()
                expect(page.locator('[data-chat-message-id]')).to_have_count(4)
                expect(page.locator('.sc-message-time').first).to_be_visible()
                page.get_by_label("本聊天文件", exact=True).click()
                expect(page.get_by_label("本聊天文件列表")).to_contain_text("result.txt")
                page.get_by_label("本聊天文件列表").get_by_role("button", name="result.txt").click()
                expect(page.get_by_role("dialog")).to_contain_text("离线成果文件")
                page.get_by_label("关闭附件预览", exact=True).click()
                page.get_by_label("导出对话", exact=True).click()
                with page.expect_download() as download:
                    page.get_by_role("link", name="导出 Markdown", exact=True).click()
                assert download.value.suggested_filename.endswith('.md')
                page.get_by_label("搜索全部对话").fill("后续排队问题")
                expect(page.locator('#tm-research-history-list a')).to_have_count(1)
                expect(page.locator('#tm-research-history-list a')).to_have_attribute('href', re.compile('#message-'))
                page.locator('#tm-research-history-list a').click()
                expect(page.locator('.sc-anchor-current')).to_be_visible()
                composer = page.get_by_role("textbox", name="发送给 Sciver 的消息", exact=True)
                composer.fill("choice-flow")
                page.get_by_label("发送消息", exact=True).click()
                expect(page.locator('.sc-question-panel')).to_be_visible()
                page.get_by_role('radio', name='方法甲', exact=False).check()
                page.get_by_role('button', name='提交回答', exact=True).click()
                expect(page.locator('.sc-question-panel')).to_have_count(0)
                expect(page.locator('[data-chat-message-id]')).to_have_count(6)
                if not page.get_by_label("本聊天文件列表").is_visible():
                    page.get_by_label("本聊天文件", exact=True).click()
                with page.expect_download() as result_download:
                    page.get_by_label("本聊天文件列表").get_by_role('link', name='下载', exact=True).last.click()
                assert result_download.value.suggested_filename
                before = len(FakeCodex.calls)
                page.goto(origin + '/research-chat/?new=1')
                page.get_by_label('选择项目任务', exact=True).select_option(task)
                page.get_by_label('科研问题', exact=True).fill('带附件的新问题草稿')
                page.locator('[data-chat-first-files]').set_input_files({"name":"材料.txt","mimeType":"text/plain","buffer":"新草稿材料".encode()})
                expect(page.get_by_role('textbox', name='发送给 Sciver 的消息', exact=True)).to_have_value('带附件的新问题草稿')
                expect(page.get_by_label('待发送附件')).to_contain_text('材料.txt')
                assert len(FakeCodex.calls) == before, 'adding first attachment must not call the model'
                assert not errors, errors
                browser.close()
            print('PASS: real HTTP/React attachment -> native-image input -> durable queue -> output artifact preview/download -> refresh timing -> history search anchor -> SDK question answer -> first-attachment draft; no model calls')
        finally:
            FakeCodex.released.set()
            with chat._JOBS_LOCK:
                jobs = list(chat._JOBS.values())
            for job in jobs:
                job.get('cancel', threading.Event()).set()
                if job.get('thread'):
                    job['thread'].join(timeout=3)
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()
