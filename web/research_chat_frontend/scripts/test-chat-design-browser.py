"""Visual/keyboard acceptance against real HTTP and the built chat, without a model.

Run after building the frontend. Screenshots and measurements are written outside
the repository; --quick produces the two desktop/dark review images first.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / "web")]

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from tm_observer import research_chat as chat
from tm_observer.research_chat_attachments import capture_artifacts, store_upload
from tm_observer.research_chat_models import cached_catalog
from tm_observer.web import ThreadingHTTPServer, make_handler


class ForbiddenCodex:
    calls = 0

    def __init__(self, *args, **kwargs):
        type(self).calls += 1
        raise AssertionError("Design acceptance must never initialize a model backend")


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def pdf_fixture():
    writer = PdfWriter()
    page = writer.add_blank_page(width=480, height=640)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 18 Tf 36 580 Td (Research evidence - offline preview) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    data = io.BytesIO()
    writer.write(data)
    return data.getvalue()


def seed_sessions(state_dir, state, project, task):
    root = chat._store(state_dir)
    sessions = {}
    png = io.BytesIO()
    image = Image.new("RGB", (240, 140), "#d9d0f2")
    draw = ImageDraw.Draw(image)
    draw.line([(18, 116), (70, 88), (112, 98), (166, 48), (220, 26)], fill="#6c5496", width=5)
    image.save(png, "PNG")
    (project / "研究摘要.md").write_text("# 研究摘要\n\n证据已整理，下一步比较两组边界条件。\n", encoding="utf-8")
    for scenario in ("messages", "activity"):
        sid = chat.create(state_dir, state, {"mode": "project", "task_id": task})["session_id"]
        sessions[scenario] = sid
        attachments = [store_upload(state_dir, sid, "观测曲线.png", "image/png", png.getvalue()),
                       store_upload(state_dir, sid, "研究材料.pdf", "application/pdf", pdf_fixture())]
        artifacts = capture_artifacts(state_dir, sid, f"[研究摘要]({project / '研究摘要.md'})", [project])
        run = {"id": f"design-{scenario}", "status": "complete", "stage": "complete",
               "model": "gpt-6-sol", "reasoning_effort": "high", "started_at": stamp(-84),
               "completed_at": stamp(-42), "duration_ms": 42000,
               "usage": {"input_tokens": 2148, "cached_input_tokens": 1024, "output_tokens": 386, "total_tokens": 2534},
               "context": {"native_thread_resumed": True, "available_messages": 6,
                           "image_replay_count": 1, "context_tokens": 2534, "context_window": 128000},
               "events": [{"id": "evidence", "type": "tool", "label": "读取研究材料", "status": "complete", "created_at": stamp(-70)},
                          {"id": "reasoning", "type": "reasoning_summary", "text": "对照图片趋势与文档中的实验条件。", "status": "complete", "created_at": stamp(-66)}]}
        row = chat._read(root, sid)
        row.update(title="讨论实验观察与下一步验证", title_source="user", reasoning_effort="high")
        row["messages"] = [
            {"id": "design-user-1", "role": "user", "content": "结合这张观测曲线和研究材料，帮我梳理关键发现，再确定下一步最值得验证的问题。", "created_at": stamp(-86), "attachments": attachments},
            {"id": "design-assistant-1", "role": "assistant", "content": "## 目前的关键发现\n\n观测曲线在后半段持续上升，值得先核对实验条件是否一致。现有材料已经整理为一份研究摘要。\n\n- 先比较两组边界条件，并记录相同的观测窗口。\n- 用重复实验确认趋势是否稳定。\n\n下一步可以从**边界条件的对照实验**开始。", "created_at": stamp(-42), "run": run, "run_id": run["id"], "artifacts": artifacts},
        ]
        row["runs"] = [run]
        row["active_run"] = run
        if scenario == "activity":
            active = {"id": "design-live", "status": "streaming", "stage": "thinking", "started_at": stamp(-23),
                      "model": "gpt-6-sol", "reasoning_effort": "high", "events": [
                          {"id": "plan-live", "type": "plan", "label": "核对对照实验", "status": "running", "created_at": stamp(-20), "plan": [{"step": "核对观测窗口与边界条件", "status": "completed"}, {"step": "整理可验证的实验方案", "status": "in_progress"}]},
                          {"id": "tool-live", "type": "tool", "label": "查看研究材料", "status": "complete", "created_at": stamp(-15)},
                          {"id": "reasoning-live", "type": "reasoning_summary", "text": "正在比较可重复性与实验成本。", "status": "running", "created_at": stamp(-8)}]}
            row["messages"].append({"id": "design-user-2", "role": "user", "content": "如果先做一个小规模对照实验，应该怎样安排？", "created_at": stamp(-24)})
            row["active_run"] = active
            chat._runtime_update(root, sid, lambda data: data.update({"run": active, "queue": [{"id": "design-queue", "content": "也请列出需要记录的实验指标。", "created_at": stamp(-10), "status": "queued"}]}))
            with chat._JOBS_LOCK:
                chat._JOBS[sid] = {"id": active["id"], "status": "streaming", "text": "可以先固定观测窗口，再比较两组边界条件。", "cancel": threading.Event(), "started_at": time.time() - 23, "context": (state_dir, project, state)}
        chat._write(root, row)
    return sessions


def bounds(locator):
    value = locator.bounding_box()
    assert value, "Expected visible element with a bounding box"
    return {key: round(number, 2) for key, number in value.items()}


def assert_in_view(locator, viewport, name):
    value = bounds(locator)
    assert value["x"] >= -1 and value["y"] >= -1, f"{name} begins outside viewport: {value}"
    assert value["x"] + value["width"] <= viewport["width"] + 1, f"{name} overflows horizontally: {value}"
    assert value["y"] + value["height"] <= viewport["height"] + 1, f"{name} below viewport: {value}"
    return value


def tab_to(scope, locator, limit=18):
    for _ in range(limit):
        scope.locator("body").press("Tab")
        if locator.evaluate("element => element === document.activeElement"):
            return
    raise AssertionError(f"Control is not reachable by Tab in {limit} steps")


def check_popovers(scope, textbox, viewport, new):
    trigger = scope.locator('[aria-label="模型与推理强度"]')
    textbox.focus()
    tab_to(scope, trigger)
    trigger.press("Enter")
    panel = scope.locator(".tm-chat-model-menu .tm-chat-popover-panel") if new else scope.get_by_role("dialog", name="模型与推理设置", exact=True)
    expect(panel).to_be_visible()
    assert_in_view(panel, viewport, "Model popover")
    expect(scope.get_by_label("新对话模型" if new else "模型", exact=True)).to_be_visible()
    expect(scope.get_by_label("新对话推理强度" if new else "推理强度", exact=True)).to_be_visible()
    scope.locator("body").press("Escape")
    expect(panel).to_be_hidden()
    expect(trigger).to_be_focused()
    trigger.click()
    expect(panel).to_be_visible()
    scope.locator(".tm-research-toolbar").click(position={"x": 5, "y": 5})
    expect(panel).to_be_hidden()
    more = scope.locator('[aria-label="添加附件或更多操作"]') if new else scope.get_by_role("button", name="更多输入选项", exact=True)
    textbox.focus()
    tab_to(scope, more)
    more.press("Enter")
    more_panel = scope.locator(".tm-chat-add-menu .tm-chat-popover-panel") if new else scope.get_by_role("dialog", name="输入选项", exact=True)
    expect(more_panel).to_be_visible()
    assert_in_view(more_panel, viewport, "Input options popover")
    expect(more_panel.get_by_role("button", name="展开输入框", exact=True)).to_be_visible()
    if not new:
        expect(scope.get_by_label("发送快捷键", exact=True)).to_be_visible()
    scope.locator("body").press("Escape")
    expect(more_panel).to_be_hidden()
    expect(more).to_be_focused()
    more.click()
    scope.locator(".tm-research-toolbar").click(position={"x": 5, "y": 5})
    expect(more_panel).to_be_hidden()


def run_case(browser, origin, sessions, output, surface, theme, scenario):
    viewport = {"desktop": {"width": 1440, "height": 1000}, "mobile": {"width": 390, "height": 844}, "embedded": {"width": 480, "height": 800}}[surface]
    key = f"{surface}-{theme}-{scenario}"
    result = {"case": key, "viewport": viewport, "screenshots": [], "failures": []}
    context = browser.new_context(viewport=viewport, locale="zh-CN", color_scheme="dark" if theme == "black" else "light", reduced_motion="reduce", has_touch=surface == "mobile")
    context.add_init_script(f"localStorage.setItem('sciver-theme-v1', {json.dumps(theme)})")
    context.set_default_timeout(8000)
    page = context.new_page()
    errors, forbidden_requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: forbidden_requests.append(request.url) if request.method == "POST" and any(f"/{part}" in urlsplit(request.url).path for part in ("message", "send", "queue", "steer", "answer", "retry")) else None)
    query = {"new": "1"} if scenario == "new" else {"session": sessions[scenario]}
    if surface == "embedded":
        query["embed"] = "1"
        page.goto(origin + "/__design-embed?" + urlencode(query))
        scope = page.frame_locator('iframe[title="Embedded Research Chat"]')
    else:
        page.goto(origin + "/research-chat/?" + urlencode(query))
        scope = page
    screenshot = output / f"{key}.png"
    try:
        textbox = scope.get_by_role("textbox", name="科研问题" if scenario == "new" else "发送给 Sciver 的消息", exact=True)
        expect(textbox).to_be_visible()
        if scenario != "new":
            expect(scope.locator(".sc-codex-shell")).to_be_visible()
            expect(scope.locator('[data-chat-message-id="design-user-1"]')).to_have_count(1)
            expect(scope.locator(".sc-attachment-card").filter(has_text="研究摘要.md")).to_have_count(1)
        if scenario == "activity":
            expect(scope.get_by_label("待发送消息队列", exact=True)).to_be_visible()
            expect(scope.get_by_role("button", name="停止生成", exact=True)).to_be_visible()
            details = scope.locator(".sc-run-events").last
            if details.count():
                details.locator("summary").click()
        page.screenshot(path=str(screenshot), animations="disabled")
        result["screenshots"].append(str(screenshot))
        print(f"SCREENSHOT {screenshot}", flush=True)
        textbox.fill("继续梳理这个问题，先保存在草稿里。")
        composer = scope.locator(".tm-research-direct" if scenario == "new" else ".sc-codex-composer")
        send = scope.get_by_role("button", name="发送" if scenario == "new" else "加入待发送队列" if scenario == "activity" else "发送消息", exact=True)
        result["composer"] = assert_in_view(composer, viewport, "Composer")
        result["send"] = assert_in_view(send, viewport, "Send button")
        result["input"] = assert_in_view(textbox, viewport, "Text input")
        assert result["input"]["width"] >= max(180, result["composer"]["width"] * .70), f"Text input too narrow: {result}"
        result["document"] = textbox.evaluate("""element => ({scrollWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth, bodyWidth: document.body.scrollWidth, theme: document.documentElement.dataset.sciverTheme})""")
        assert result["document"]["scrollWidth"] <= viewport["width"] + 1, f"Document horizontal overflow: {result['document']}"
        assert result["document"]["bodyWidth"] <= viewport["width"] + 1, f"Body horizontal overflow: {result['document']}"
        assert result["document"]["theme"] == theme
        check_popovers(scope, textbox, viewport, scenario == "new")
        if scenario == "messages":
            details = scope.locator(".sc-run-metadata").first
            details.locator(":scope > summary").click()
            expect(details.locator(".sc-run-detail-body")).to_be_visible()
            assert_in_view(composer, viewport, "Composer after opening reply details")
            assert_in_view(send, viewport, "Send after opening reply details")
            details_screenshot = output / f"{key}-details.png"
            page.screenshot(path=str(details_screenshot), animations="disabled")
            result["screenshots"].append(str(details_screenshot))
        if scenario != "new":
            viewport_before = scope.locator('.tm-aui-viewport').bounding_box()
            search = scope.get_by_label("搜索当前对话", exact=True)
            expect(search).to_be_hidden()
            scope.get_by_role("button", name="查找对话内容", exact=True).click()
            expect(search).to_be_focused()
            assert_in_view(scope.locator('.sc-codex-search-panel'), viewport, 'Search panel')
            assert scope.locator('.tm-aui-viewport').bounding_box() == viewport_before, 'Search must overlay without resizing messages'
            scope.get_by_role('button', name='本聊天文件', exact=True).click()
            expect(search).to_be_hidden()
            files = scope.get_by_role('dialog', name='本聊天文件列表', exact=True)
            expect(files).to_be_visible()
            assert_in_view(files, viewport, 'Files panel')
            assert scope.locator('.tm-aui-viewport').bounding_box() == viewport_before, 'Files must overlay without resizing messages'
            textbox.click()
            expect(files).to_be_hidden()
            scope.get_by_role('button', name='本聊天文件', exact=True).click()
            textbox.press('Escape')
            expect(files).to_be_hidden()
            expect(scope.get_by_role('button', name='本聊天文件', exact=True)).to_be_focused()
            scope.get_by_role('button', name='查找对话内容', exact=True).click()
            search.press("Escape")
            expect(search).to_be_hidden()
        assert not errors, f"Browser errors: {errors}"
        assert not forbidden_requests, f"Unexpected model-related requests: {forbidden_requests}"
    except Exception as exc:
        result["failures"].append(str(exc))
        failure = output / f"{key}-failure.png"
        page.screenshot(path=str(failure), animations="disabled")
        result["screenshots"].append(str(failure))
        print(f"FAIL {key}: {exc}", flush=True)
    finally:
        result["page_errors"] = errors
        result["model_requests"] = forbidden_requests
        context.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp/sciver-chat-redesign"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ForbiddenCodex.calls = 0
    with tempfile.TemporaryDirectory(prefix="sciver-chat-design-") as temporary:
        root = Path(temporary)
        project = root / "笔记区" / "边界条件与实验观察"
        project.mkdir(parents=True)
        (project / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        task = hashlib.sha256(str(project).encode()).hexdigest()[:16]
        state_dir = root / "state"
        state = {"runs": [{"observer_id": task, "task_name": "边界条件与实验观察", "task_root": str(project), "research_root": str(project)}]}
        class Cache:
            search_root = root
            def get(self):
                return state
        sessions = seed_sessions(state_dir, state, project, task)
        handler = make_handler(Cache(), field_notes_state_dir=state_dir)
        class DesignHandler(handler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if urlsplit(self.path).path != "/__design-embed":
                    return super().do_GET()
                query = urlencode({key: values[-1] for key, values in parse_qs(urlsplit(self.path).query).items()})
                html = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}iframe{width:100%;height:100%;border:0;display:block}</style></head><body><iframe title="Embedded Research Chat" src="/research-chat/?' + query.replace("&", "&amp;") + '"></iframe></body></html>').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
        server = ThreadingHTTPServer(("127.0.0.1", 0), DesignHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        results = []
        try:
            with patch("theory_master.codex_backend.CodexBackend", ForbiddenCodex), patch("tm_observer.research_chat_models.refresh_catalog", side_effect=lambda **kwargs: cached_catalog()), sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                cases = [("desktop", "black", "new"), ("desktop", "black", "messages")] if args.quick else [(surface, theme, scenario) for surface in ("desktop", "mobile", "embedded") for theme in ("black", "white") for scenario in ("new", "messages", "activity")]
                for surface, theme, scenario in cases:
                    results.append(run_case(browser, origin, sessions, args.output, surface, theme, scenario))
                browser.close()
        finally:
            with chat._JOBS_LOCK:
                for sid in sessions.values():
                    chat._JOBS.pop(sid, None)
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        assert ForbiddenCodex.calls == 0, "No model backend may be initialized"
        report = args.output / ("results-quick.json" if args.quick else "results.json")
        report.write_text(json.dumps({"results": results, "model_backend_calls": ForbiddenCodex.calls}, ensure_ascii=False, indent=2), encoding="utf-8")
        failed = [result["case"] for result in results if result["failures"]]
        print(f"{'FAIL' if failed else 'PASS'}: {len(results) - len(failed)}/{len(results)} design cases; zero model calls; report {report}")
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
