"""Versioned reading references: read-only preparation, send validation and history."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from theory_master.science_graph_store import ScienceGraphStore
from tm_observer.canvas_md import ExplorerError, read_document
from tm_observer.research_chat import ResearchChatError, begin_send, create, send, snapshot
from tm_observer.research_chat_sources import prepare_source, source_context, validate_sources, validate_task_sources
from tm_observer.web import ThreadingHTTPServer, make_handler


class ResearchChatSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "笔记区/探索台账/source-test"
        self.vault.mkdir(parents=True)
        self.research = self.root / "research"
        self.research.mkdir()
        self.task_id = hashlib.sha256(str(self.research).encode()).hexdigest()[:16]
        self.task = {"observer_id": self.task_id, "task_root": str(self.vault),
                     "research_root": str(self.research), "task_name": "source test"}
        self.state = {"runs": [self.task]}
        (self.vault / "theory-master.config.json").write_text(json.dumps({
            "research_root": str(self.research), "branch_canvas": str(self.vault / "exploration.canvas"),
        }))
        (self.vault / "notes").mkdir()
        self.report = self.vault / "notes/report.md"
        self.report.write_text("# Study\n\n## Results\nA **bounded** result with [evidence](https://example.org).\n\n## Limits\nOnly one sample.\n")
        self.database = self.vault / "science_graph.sqlite3"
        with ScienceGraphStore(self.database) as store:
            store.add_hypothesis("H-0001", title="A conjecture", summary="A bounded proposition")
            store.append_outcome(outcome_id="out-1", hypothesis_id="H-0001", cycle_id="1",
                verdict="supported", title="Study", summary="A bounded result", judge_rationale="Exact criterion",
                report_ref="notes/report.md", record_only=True)

    def reference(self, **changes):
        return prepare_source(self.root, self.state, self.task_id,
                              {"kind": "report", "id": "1", "section": "results", **changes})

    def test_prepare_and_send_keep_verified_quote_identity_and_link(self):
        before = self.database.read_bytes(), self.report.read_bytes()
        source = self.reference(quote="A bounded result with evidence.")
        self.assertEqual(source["hypothesis_id"], "H-0001")
        self.assertEqual(source["cycle_id"], "1")
        self.assertEqual(source["source_version"], hashlib.sha256(self.report.read_bytes()).hexdigest())
        self.assertIn("report%3A1%23results", source["href"])
        self.assertEqual(validate_task_sources(self.root, self.task, [source])[0]["quote"], source["quote"])
        self.assertEqual((self.database.read_bytes(), self.report.read_bytes()), before)

        class FakeBackend:
            prompt = ""
            def invoke(inner, role, prompt, thread, model, provider):
                inner.prompt = prompt
                return "offline-thread", "仅讨论这项证据。"
        backend = FakeBackend()
        state_dir = self.root / "state"
        session = create(state_dir, self.state, {"mode": "project", "task_id": self.task_id})["session_id"]
        send(state_dir, self.root, self.state, session, {"content": "解释这个结果", "sources": [source]}, backend=backend)
        message = snapshot(state_dir, self.state, session)["session"]["messages"][0]
        self.assertEqual(message["sources"][0]["source_version"], source["source_version"])
        self.assertIn(source["quote"], backend.prompt)
        self.assertIn("snapshot", backend.prompt)
        self.assertIn("discussion only", backend.prompt)
        self.assertIn(source["href"], backend.prompt)

    def test_rejects_cross_task_forged_quote_old_version_and_invalid_sections(self):
        cases = [({"task_id": "b" * 16}, 403), ({"quote": "Invented scientific result"}, 400),
                 ({"source_version": "0" * 64}, 409), ({"section": "missing"}, 400),
                 ({"quote": "Only one sample."}, 400)]
        for fields, status in cases:
            with self.subTest(fields=fields), self.assertRaises(ExplorerError) as caught:
                self.reference(**fields)
            self.assertEqual(caught.exception.status, status)
        source = self.reference()
        self.report.write_text(self.report.read_text() + "\nA revision.\n")
        with self.assertRaises(ExplorerError) as caught:
            validate_sources(self.root, self.state, self.task_id, [source])
        self.assertEqual(caught.exception.status, 409)

    def test_node_uses_canonical_graph_and_doc_boundary_stays_scoped(self):
        node = prepare_source(self.root, self.state, self.task_id, {"kind": "node", "id": "H-0001"})
        self.assertEqual(node["hypothesis_id"], "H-0001")
        self.assertEqual(validate_task_sources(self.root, self.task, [node])[0]["source_version"], node["source_version"])
        doc = prepare_source(self.root, self.state, self.task_id, {"kind": "doc", "id": "notes/report.md", "quote": "Only one sample."})
        self.assertEqual(doc["document_id"], "notes/report.md")
        for ident in ("../private.md", str(self.report)):
            with self.assertRaises(ExplorerError):
                prepare_source(self.root, self.state, self.task_id, {"kind": "doc", "id": ident})
        (self.vault / "notes/escaped.md").symlink_to(self.root / "private.md")
        with self.assertRaises(ExplorerError):
            prepare_source(self.root, self.state, self.task_id, {"kind": "doc", "id": "notes/escaped.md"})

    def test_rejected_send_never_creates_user_turn_or_worker_thread(self):
        state_dir = self.root / "state"
        session = create(state_dir, self.state, {"mode": "project", "task_id": self.task_id})["session_id"]
        source = self.reference()
        source["quote"] = "Forged quote"
        with patch("tm_observer.research_chat.threading.Thread") as thread:
            with self.assertRaises(ResearchChatError):
                begin_send(state_dir, self.root, self.state, session, {"content": "讨论", "sources": [source]})
        thread.assert_not_called()
        self.assertEqual(snapshot(state_dir, self.state, session)["session"]["messages"], [])
        source.pop("source_version")
        with self.assertRaises(ExplorerError):
            validate_sources(self.root, self.state, self.task_id, [source])
        with self.assertRaises(ExplorerError):
            validate_sources(self.root, self.state, self.task_id, [self.reference()] * 5)

    def test_doc_read_version_prevents_attaching_a_stale_reading(self):
        # A normal read can advance atime; it does not change the source version.
        stat = self.report.stat()
        os.utime(self.report, ns=(1, stat.st_mtime_ns))
        document = read_document(self.vault, "notes/report.md")
        self.assertEqual(document["source_sha256"], hashlib.sha256(self.report.read_bytes()).hexdigest())
        self.report.write_text(self.report.read_text() + "\nLater correction.\n")
        with self.assertRaises(ExplorerError) as caught:
            prepare_source(self.root, self.state, self.task_id,
                {"kind": "doc", "id": document["id"], "source_version": document["source_sha256"]})
        self.assertEqual(caught.exception.status, 409)

    def test_history_context_is_bounded_and_explicitly_retains_snapshots(self):
        source = self.reference()
        source["quote"] = "A" * 4000
        history = [{"id": str(i), "role": "user", "sources": [source] * 4} for i in range(20)]
        result = source_context(history)
        self.assertLess(len(result), 26000)
        self.assertIn('"message_id": "19"', result)
        self.assertIn("omitted", result)
        self.assertIn("historical snapshots may have changed", result)

    def test_http_preparation_requires_same_origin_and_never_calls_model(self):
        class Cache:
            search_root = self.root
            def get(inner): return self.state
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=self.root / "observer"))
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:" + str(server.server_port)
        body = json.dumps({"task_id": self.task_id, "source": {"kind": "report", "id": "1", "quote": "Only one sample."}}).encode()
        headers = {"Content-Type": "application/json", "X-TM-Operator-Intent": "1", "Origin": origin}
        with urlopen(Request(origin + "/api/research-chat/sources", data=body, headers=headers)) as response:
            self.assertEqual(json.load(response)["source"]["cycle_id"], "1")
        headers["Origin"] = "https://foreign.example"
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(origin + "/api/research-chat/sources", data=body, headers=headers))
        self.assertEqual(caught.exception.code, 403)

    def test_browser_reader_to_new_and_existing_chat_preserves_drafts(self):
        from playwright.sync_api import sync_playwright, expect
        from tm_observer.web import StateCache

        (self.vault / 'notes/hypotheses').mkdir()
        (self.vault / 'notes/hypotheses/H-0001.md').write_text('# A conjecture\n\n<!-- frozen-cycle:1 -->\n')
        with ScienceGraphStore(self.database) as store:
            store.add_hypothesis('H-0000', title='Predecessor mechanism',
                                 summary='主张：Earlier mechanism remains unresolved；条件：Bounded observations；状态：待检验。')
            store.add_hypothesis('H-0002', title='Alternative interpretation', summary='An alternative remains open.')
            store.add_evolution(edge_id='main', source='H-0000', target='H-0001', lineage_role='main')
            store.add_evolution(edge_id='ref', source='H-0002', target='H-0001', lineage_role='other')
            store.record_research_transition('H-0001', {
                'transition': {'prior_gap': 'Existing observations do not identify the mechanism.',
                               'current_change': 'Compare a bounded proposition under the same observations.',
                               'source_contributions': {'H-0000': 'Carries the unresolved mechanism into the new comparison.',
                                                        'H-0002': 'Provides an alternative for the same comparison.'}},
                'why_now': 'Both interpretations can now be compared.',
                'goal_advance': 'Identify which observation distinguishes the interpretations.',
            })
            store.register_cycle_hypothesis(cycle_id='1', hypothesis_id='H-0001')
        catalog = {"available_models": ["gpt-6-sol"], "available_reasoning_efforts": ["medium"],
                   "model_reasoning_efforts": {"gpt-6-sol": ["medium"]}}
        fake_graph = """window.G6={Graph:class {
          constructor(){this.data={nodes:[],edges:[]};this.zoom=1;this.position=[0,0]}
          on(){} setData(data){this.data=data} async render(){} async draw(){} resize(){}
          getNodeData(id){return id?this.data.nodes.find(n=>n.id===id):this.data.nodes}
          getEdgeData(){return this.data.edges} updateNodeData(){} updateEdgeData(){}
          async setElementVisibility(){} async fitView(){} async focusElement(){}
          getZoom(){return this.zoom} getPosition(){return this.position}
          async zoomTo(z){this.zoom=z} async translateTo(p){this.position=p}
          getElementPosition(){return [0,0]} getViewportByCanvas(p){return [p[0]+200,p[1]+200]}
        }};"""
        class FakeBackend:
            def invoke(inner, role, prompt, thread, model, provider):
                return "offline", "这是一项有范围限制的研究记录。"
        accepted = []
        sends_started = [threading.Event(), threading.Event()]
        sends_released = [threading.Event(), threading.Event()]
        state_dir = self.root / "observer"
        def offline_send(notes, search_root, state, session, payload):
            accepted.append(payload)
            index = len(accepted) - 1
            if index < 2:
                sends_started[index].set()
                if not sends_released[index].wait(10):
                    raise AssertionError("Offline browser fixture did not release the pending send")
            return send(notes, search_root, state, session, payload, backend=FakeBackend())
        cache = StateCache(self.root)
        cache.get = lambda: self.state
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, field_notes_state_dir=state_dir))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:" + str(server.server_port)
        with patch("tm_observer.research_chat.begin_send", side_effect=offline_send), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            def route(request):
                url = request.request.url
                if "/api/research-chat/models" in url:
                    request.fulfill(json=catalog)
                elif url.startswith(origin) and (url.endswith("/model") or url.endswith("/effort")):
                    request.fulfill(json={"ok": True})
                elif url.startswith(origin):
                    request.continue_()
                else:
                    request.fulfill(body=fake_graph if "/g6@" in url else "", content_type="application/javascript")
            page.route("**/*", route)
            def wait_for_send(index):
                # Pump Playwright's route callbacks while the HTTP handler waits.
                for _ in range(30):
                    if sends_started[index].is_set():
                        return
                    page.wait_for_timeout(100)
                self.fail("The offline browser send did not reach the HTTP boundary")
            page.goto(origin + "/research-explorer/?observer_id=" + self.task_id + "#report%3A1%23results")
            def select_reader_text():
                expect(page.locator('#ex-reader .ex-document-title, #ex-reader .ex-hypothesis-title')).to_be_visible()
                page.locator('#ex-reader .ex-document-title, #ex-reader .ex-hypothesis-title').scroll_into_view_if_needed()
                page.evaluate("""() => {
                  const reader=document.querySelector('#ex-reader');
                  const text=reader.querySelector('.ex-hypothesis-title, .ex-md pre').firstChild;
                  const range=document.createRange();
                  if(reader.querySelector('.ex-hypothesis-title')) range.selectNodeContents(text);
                  else { const start=text.textContent.indexOf('bounded'); range.setStart(text,start);range.setEnd(text,start+7); }
                  const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
                  reader.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
                }""")
                expect(page.locator('[data-selection-popover]')).to_be_visible()
                self.assertTrue(page.evaluate("""() => {
                  const p=document.querySelector('[data-selection-popover]').getBoundingClientRect();
                  const r=document.querySelector('#ex-reader').getBoundingClientRect();
                  return p.left>=r.left && p.right<=r.right && p.top>=r.top && p.bottom<=r.bottom;
                }"""))
            title = page.locator('#ex-reader .ex-document-title')
            expect(title).to_be_visible()
            box = title.bounding_box()
            page.mouse.move(box['x'] + 3, box['y'] + box['height'] / 2)
            page.mouse.down()
            page.mouse.move(box['x'] + 45, box['y'] + box['height'] / 2, steps=6)
            page.mouse.up()
            expect(page.locator('[data-selection-popover]')).to_be_visible()
            select_reader_text()
            expect(page.locator("[data-discuss-selection]")).to_be_visible()
            page.locator("#sc-chat-launcher").click()
            frame = page.frame_locator("#sc-chat-frame")
            direct = frame.locator("[data-research-prompt]")
            direct.fill("My existing draft")
            page.locator("#sc-chat-minimize").click()
            select_reader_text()
            page.locator("[data-discuss-selection]").click()
            expect(frame.locator(".tm-research-direct-sources>div")).to_have_count(1)
            expect(frame.locator('.tm-research-direct-sources blockquote')).to_have_text('bounded')
            expect(direct).to_have_value("My existing draft")
            page.locator("#sc-chat-minimize").click()
            page.evaluate("""() => {
              const text=document.querySelector('#ex-reader .ex-md pre').firstChild;
              const start=text.textContent.indexOf('A **bounded** result');
              const range=document.createRange();range.setStart(text,start);range.setEnd(text,start+21);
              const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
              document.querySelector('#ex-reader').dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
            }""")
            page.locator("[data-discuss-selection]").click()
            expect(frame.locator(".tm-research-direct-sources>div")).to_have_count(2)
            frame.locator(".tm-research-direct-sources button").first.click()
            expect(frame.locator(".tm-research-direct-sources>div")).to_have_count(1)
            expect(direct).to_have_value("My existing draft")
            self.assertEqual(accepted, [], "Adding/removing a source must not send a turn")
            frame.locator("[data-research-direct] button[type=submit]").click()
            wait_for_send(0)
            page.locator("#sc-chat-minimize").click()
            page.evaluate("location.hash='node%3AH-0001'")
            expect(page.locator(".ex-hypothesis-title")).to_have_text("A conjecture")
            select_reader_text()
            page.locator("[data-discuss-selection]").click()
            expect(frame.locator(".tm-research-direct-sources>div")).to_have_count(2)
            expect(frame.locator('.tm-research-direct-sources blockquote').last).to_have_text('A conjecture')
            sends_released[0].set()
            composer = frame.get_by_role("textbox", name="发送给 Sciver 的消息")
            expect(composer).to_be_visible()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(1)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]["sources"][0]["quote"], "A **bounded** result ".strip())
            composer.fill("Keep this second draft")
            page.locator("#sc-chat-minimize").click()
            page.evaluate("location.hash='node%3AH-0001'")
            expect(page.locator(".ex-hypothesis-title")).to_have_text("A conjecture")
            select_reader_text()
            page.locator("[data-discuss-selection]").click()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(1)
            expect(frame.locator('.tm-aui-reading-source blockquote')).to_have_text('A conjecture')
            expect(composer).to_have_value("Keep this second draft")
            self.assertEqual(accepted[0]["sources"][0]["kind"], "report")
            page.evaluate("""() => document.querySelector('#sc-chat-frame').contentWindow.postMessage({
              type:'sciver-chat-add-source',task_id:'bbbbbbbbbbbbbbbb',source:{task_id:'bbbbbbbbbbbbbbbb'}},location.origin)""")
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(1)
            frame.locator(".tm-aui-reading-source button").click()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(0)
            expect(composer).to_have_value("Keep this second draft")
            page.locator("#sc-chat-minimize").click()
            page.locator("[data-discuss-cycle]").click()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(1)
            if os.environ.get("SCIVER_SOURCE_PREVIEW"):
                page.evaluate("document.documentElement.setAttribute('data-sciver-theme','white')")
                page.locator("#sc-chat-panel").screenshot(path=os.environ["SCIVER_SOURCE_PREVIEW"])
            composer.press("Enter")
            wait_for_send(1)
            page.locator("#sc-chat-minimize").click()
            select_reader_text()
            page.locator("[data-discuss-selection]").click()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(2)
            sends_released[1].set()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(1)
            frame.locator(".tm-aui-reading-source button").click()
            expect(frame.locator(".tm-aui-reading-source")).to_have_count(0)
            self.assertEqual(len(accepted), 2)
            self.assertEqual(accepted[1]["sources"][0]["cycle_id"], "1")
            self.assertEqual(accepted[1]["sources"][0]["hypothesis_id"], "H-0001")
            expect(frame.get_by_text("已核验快照", exact=False).last).to_be_visible()
            frame.locator('.tm-aui-user-actions [aria-label="编辑并重发"]').last.click()
            editor = frame.get_by_role("textbox", name="编辑消息")
            expect(editor).to_have_value("Keep this second draft")
            editor.fill("Edited question")
            frame.locator(".tm-aui-edit-submit").click()
            expect(editor).to_have_count(0)
            self.assertEqual(accepted[-1]["content"], "Edited question")
            self.assertEqual(len(accepted[-1]["sources"]), 1)
            page.locator('#sc-chat-minimize').click()
            page.locator('#ex-reader').evaluate('e=>e.scrollTop=0')
            expect(page.locator('.ex-reader-cycle')).to_have_text('Cycle 1')
            for theme in ('black', 'blue', 'white'):
                page.evaluate('t=>document.documentElement.setAttribute("data-sciver-theme",t)', theme)
                main_color = page.locator('.ex-source-group[data-source-role="main"] button').evaluate('e=>getComputedStyle(e).color')
                ref_color = page.locator('.ex-source-group[data-source-role="other"] button').evaluate('e=>getComputedStyle(e).color')
                self.assertNotEqual(main_color, ref_color)
                self.assertEqual(page.locator('.ex-research-transition').evaluate('e=>getComputedStyle(e).borderTopWidth'), '0px')
                if os.environ.get('SCIVER_READER_PREVIEW'):
                    page.locator('#ex-reader').screenshot(path=os.environ['SCIVER_READER_PREVIEW'] + '-' + theme + '.png')
            for selector, expected_quote, expected_node in (
                ('[data-discussion-node="H-0000"]', 'Earlier mechanism remains unresolved', 'H-0000'),
                ('.ex-transition-contribution', 'Carries the unresolved mechanism into the new comparison.', 'H-0001'),
            ):
                paragraph = page.locator(selector).first
                paragraph.scroll_into_view_if_needed()
                paragraph.evaluate('''e=>{
                  const range=document.createRange();range.selectNodeContents(e);
                  const s=getSelection();s.removeAllRanges();s.addRange(range);
                  e.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));
                }''')
                page.locator('[data-discuss-selection]').click()
                expect(frame.locator('.tm-aui-reading-source')).to_have_count(1)
                expect(frame.locator('.tm-aui-reading-source blockquote')).to_have_text(expected_quote)
                expect(frame.locator('.tm-aui-reading-source>a')).to_have_attribute('href', re.compile('node%3A' + expected_node))
                frame.locator('.tm-aui-reading-source button').click()
                page.locator('#sc-chat-minimize').click()
            browser.close()


if __name__ == "__main__":
    unittest.main()
