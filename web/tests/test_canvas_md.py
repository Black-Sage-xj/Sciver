"""Regression tests for the stable-ID canvas-md read-only integration."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from tm_observer.auth import BasicAuthCredentials
from tm_observer.canvas_md import ExplorerError, build_index, read_document, resolve_link, search_payload, vault_root
from tm_observer.canvas_md_routes import canvas_md_get
from tm_observer.research_explorer_ui import EXPLORER_HTML, explorer_html
from tm_observer.web import ThreadingHTTPServer, make_handler


class CanvasMDTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.notebook = self.repo / "笔记区" / "探索台账" / "task-1"
        self.notebook.mkdir(parents=True)
        self.research = self.repo / "gs_study" / "task-1" / "research"
        self.research.mkdir(parents=True)
        self.ident = hashlib.sha256(str(self.research).encode()).hexdigest()[:16]
        self.run = {"observer_id": self.ident, "run_id": 1, "task_root": str(self.notebook),
                    "research_root": str(self.research), "task_name": "research test"}
        self.state = {"runs": [self.run]}
        (self.notebook / "theory-master.config.json").write_text(
            json.dumps({"research_root": str(self.research),
                        "branch_canvas": str(self.notebook / "exploration.canvas")}),
            encoding="utf-8",
        )
        (self.notebook / "nodes").mkdir()
        (self.notebook / "notes").mkdir()
        (self.notebook / "nodes" / "proof.md").write_text(
            "# Proof\n\n[[H-0042]] and [[notes/detail.md#结论]]\n\n$x^2$.", encoding="utf-8")
        (self.notebook / "notes" / "detail.md").write_text(
            "# Detail\n\n[[nodes/proof|Proof]]\n", encoding="utf-8")
        (self.notebook / "exploration.canvas").write_text(json.dumps({
            "nodes": [
                {"id": "H-0042", "type": "text", "text": "# H-0042 hypothesis\n[[nodes/proof|Proof]]",
                 "x": 12, "y": 20, "width": 400, "height": 200},
                {"id": "next", "type": "text", "text": "# Follow-up\n[[H-0042]]",
                 "x": 700, "y": 20, "width": 430, "height": 230},
            ],
            "edges": [{"id": "scientific-inheritance", "fromNode": "H-0042", "toNode": "next",
                       "label": "research parent"}],
        }, ensure_ascii=False), encoding="utf-8")

    def test_active_worker_markers_follow_frozen_h_and_never_show_stale_process(self):
        from unittest.mock import patch
        from theory_master.science_graph_store import ScienceGraphStore
        from tm_observer.canvas_md_routes import running_worker_markers

        db_file = self.notebook / "science_graph.sqlite3"
        with ScienceGraphStore(db_file) as store:
            store.add_hypothesis("H-0135", title="Current frozen question")
            store.add_hypothesis("H-0128", title="Claimed frontier before freeze")
            store.register_cycle_hypothesis(cycle_id="135", hypothesis_id="H-0135")
            before_events = store.db.execute("SELECT count(*) FROM graph_events").fetchone()[0]
        folder = self.research / ".theory-master" / "workers"
        for number, cycle, claim, status in [
            (1, "135", "H-0090", "RUNNING"),
            (2, "136", "H-0128", "RUNNING"),
            (3, "137", "H-0128", "RUNNING"),
            (4, "138", "H-0128", "PAUSED"),
        ]:
            worker = folder / f"worker-{number}"
            worker.mkdir(parents=True)
            (worker / "runtime.json").write_text(json.dumps({
                "schema": "theory-master/worker-runtime/v1",
                "worker_id": f"worker-{number}", "status": status,
                "current_cycle": cycle, "target_h_id": claim,
            }), encoding="utf-8")

        with patch("tm_observer.worker_console._verified_active_worker",
                   side_effect=lambda _root, _entry, worker: worker in {"worker-1", "worker-2"}):
            payload, code = canvas_md_get(
                f"/api/canvas-md/tasks/{self.ident}/workers",
                "", self.repo, self.state)
            assignments = json.loads(payload)["workers"]
            self.assertEqual(code, 200)
            self.assertEqual(assignments, [
                {"worker_id": "worker-1", "node_id": "H-0135",
                 "cycle_id": "135", "association": "frozen"},
                {"worker_id": "worker-2", "node_id": "H-0128",
                 "cycle_id": "136", "association": "claimed"},
            ])
            self.assertEqual(
                running_worker_markers(self.notebook, "0000000000000000", self.state), [])
        with ScienceGraphStore(db_file, readonly=True) as store:
            after_events = store.db.execute("SELECT count(*) FROM graph_events").fetchone()[0]
        self.assertEqual(before_events, after_events, "Worker marker GET is strictly read-only")

    def test_graph_index_cache_reuses_stable_snapshot_and_invalidates_on_document_change(self):
        from unittest.mock import patch
        import tm_observer.canvas_md as canvas_module

        root = vault_root(self.repo, self.ident, self.state)
        with patch("tm_observer.canvas_md._build_index_uncached",
                   wraps=canvas_module._build_index_uncached) as builder:
            first = build_index(root)
            second = build_index(root)
            self.assertIs(first, second)
            self.assertEqual(builder.call_count, 1)

            document = self.notebook / "notes" / "detail.md"
            document.write_text("# Detail changed\n\n[[nodes/proof.md]]\n", encoding="utf-8")
            third = build_index(root)
            self.assertEqual(builder.call_count, 2)
            self.assertIn("changed", third["documents"][1]["title"].lower())
            self.assertEqual({node["id"] for node in third["nodes"]}, {"H-0042", "next"})

    def test_stable_id_graph_docs_and_backlinks(self):
        root = vault_root(self.repo, self.ident, self.state)
        index = build_index(root)
        self.assertEqual({k: index["stats"][k] for k in ("node_count", "edge_count", "doc_count")},
                         {"node_count": 2, "edge_count": 1, "doc_count": 2})
        self.assertFalse(index["stats"]["partial"])
        self.assertEqual(index["edges"][0]["label"], "research parent")
        self.assertEqual([e["id"] for e in index["edges"]], ["scientific-inheritance"])
        self.assertEqual(index["references"]["node:H-0042"][0]["id"], "nodes/proof.md")
        self.assertTrue(any(r["id"] == "nodes/proof.md" for r in index["backlinks"]["node:H-0042"]))
        self.assertTrue(any(r["id"] == "next" for r in index["backlinks"]["node:H-0042"]))
        self.assertEqual(resolve_link("notes/detail.md#结论", index["aliases"])["id"], "notes/detail.md")
        self.assertIn("$x^2$", read_document(root, "nodes/proof.md")["content"])
        payload, status = canvas_md_get(
            f"/api/canvas-md/tasks/{self.ident}/index", "", self.repo, self.state,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["observer_id"], self.ident)
        result, status = canvas_md_get(
            f"/api/canvas-md/tasks/{self.ident}/search", "q=Proof", self.repo, self.state,
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(r["id"] == "nodes/proof.md" for r in json.loads(result)["results"]))
        result, status = canvas_md_get(
            f"/api/canvas-md/tasks/{self.ident}/document",
            "path=nodes%2Fproof.md", self.repo, self.state,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(result)["title"], "Proof")
        self.assertIn(self.ident, explorer_html(self.repo, self.state, self.ident))
        direct_from_run = explorer_html(self.repo, self.state, self.ident)
        self.assertIn('<label hidden style="display:none">科研任务', direct_from_run)
        self.assertIn(f'value="{self.ident}" selected', direct_from_run)
        manual_explorer = explorer_html(self.repo, self.state)
        self.assertIn('<label>科研任务 <select id="ex-task">', manual_explorer)

    def test_client_script_syntax(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js not installed")
        script = re.search(r"<script>(.*?)</script>", EXPLORER_HTML, re.S).group(1)
        proc = subprocess.run(["node", "--check"], input=script, text=True,
                              capture_output=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_trackpad_hover_pan_and_pinch_zoom(self):
        """Exercise the actual inline handler without triggering pointerdown."""
        if shutil.which("node") is None:
            self.skipTest("Node.js not installed")
        script = re.search(r"<script>(.*?)</script>", EXPLORER_HTML, re.S).group(1)
        harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync(0, 'utf8');
const elements = {};
function elem(id) {
  if (!elements[id]) elements[id] = {
    value: 'task', clientWidth: 1000, clientHeight: 500, listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
    setAttribute(name, value) { this[name] = String(value); },
    getBoundingClientRect() { return {left:100, top:50, width:1000, height:500}; }
  };
  return elements[id];
}
vm.runInNewContext(src, {
  document: {getElementById: elem},
  window: {addEventListener() {}},
  console
});
const graph = elem('ex-graph'), svg = elem('ex-svg');
assert.equal(typeof graph.listeners.wheel, 'function');
assert.equal(typeof svg.listeners.pointerdown, 'function');
function emit(extra = {}) {
  let stopped = false;
  const e = {
    deltaX:0, deltaY:0, deltaMode:0, ctrlKey:false, metaKey:false, shiftKey:false,
    clientX:600, clientY:300, target:{closest:()=>null},
    preventDefault(){stopped=true}, ...extra
  };
  graph.listeners.wheel(e);
  return stopped;
}
function view(){return svg.viewBox.split(' ').map(Number)}
assert.equal(emit({deltaX:100, deltaY:50}), true);
let [x,y,w,h]=view();
assert.equal(x,10); assert.equal(y,10); assert.equal(w,100); assert.equal(h,100);
assert.equal(emit({deltaX:0, deltaY:2, deltaMode:1}), true);
assert.equal(view()[1], 16.4);
const [beforeX,beforeY,beforeW,beforeH]=view();
assert.equal(emit({deltaY:40, ctrlKey:true}), true);
[x,y,w,h]=view();
assert.ok(w>beforeW && h>beforeH);
assert.ok(Math.abs(x + w*.5 - (beforeX+beforeW*.5))<1e-7);
assert.ok(Math.abs(y + h*.5 - (beforeY+beforeH*.5))<1e-7);
const old = svg.viewBox;
assert.equal(emit({deltaY:20,target:{closest:()=>({})}}), false);
assert.equal(svg.viewBox, old);
assert.equal(emit({deltaY:-10,metaKey:true}), true);
assert.ok(view()[2]<w);
console.log('trackpad pan/pinch assertions passed');
"""
        proc = subprocess.run(["node", "-e", harness], input=script, text=True,
                              capture_output=True, timeout=15, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("trackpad pan/pinch assertions passed", proc.stdout)

    def test_path_and_task_identity_fail_closed(self):
        root = vault_root(self.repo, self.ident, self.state)
        for path in ("../theory-master.config.json", "/etc/passwd", "notes/../theory-master.config.json",
                     "theory-master.config.json", "notes/not-found.md", ".theory-master/task.json"):
            with self.subTest(path=path), self.assertRaises(ExplorerError):
                read_document(root, path)
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, "1", self.state)
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, "0123456789abcdef", self.state)
        with self.assertRaises(ExplorerError):
            canvas_md_get(f"/api/canvas-md/tasks/{self.ident}/unknown", "", self.repo, self.state)
        with self.assertRaises(ExplorerError):
            canvas_md_get(f"/api/canvas-md/tasks/{self.ident}/document",
                          "path=..%2F..%2Fetc%2Fpasswd", self.repo, self.state)
        with self.assertRaises(ExplorerError):
            canvas_md_get(f"/api/canvas-md/tasks/{self.ident}/search",
                          "q=" + "z" * 90, self.repo, self.state)
        # An in-vault symlink pointing outside is never indexable.
        (self.notebook / "notes" / "external.md").symlink_to(self.repo / "secret.md")
        (self.repo / "secret.md").write_text("SECRET", encoding="utf-8")
        with self.assertRaises(ExplorerError):
            read_document(root, "notes/external.md")
        self.assertNotIn("notes/external.md", [d["id"] for d in build_index(root)["documents"]])

    def test_mismatched_research_root_and_unsafe_config(self):
        corrupted = {"runs": [dict(self.run, research_root=str(self.repo / "wrong"))]}
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, self.ident, corrupted)
        config = self.notebook / "theory-master.config.json"
        config.write_text(json.dumps({"research_root": str(self.repo / "gs_study" / "wrong")}),
                          encoding="utf-8")
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, self.ident, self.state)

    def test_registered_task_outside_legacy_notebook_is_readable(self):
        task = self.repo / "gs_study" / "task-2"
        task.mkdir(parents=True)
        (task / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        config = task / "theory-master.config.json"
        config.write_text(json.dumps({
            "task_home": str(task), "research_root": str(self.research),
            "branch_canvas": str(task / "exploration.canvas"),
        }), encoding="utf-8")
        marker = task / ".theory-master" / "task.json"
        marker.parent.mkdir()
        marker.write_text(json.dumps({
            "schema": "theory-master/task/v2", "config_path": str(config),
        }), encoding="utf-8")
        indexed = {"runs": [dict(self.run, task_root=str(task))]}
        self.assertEqual(vault_root(self.repo, self.ident, indexed), task)

        marker.unlink()
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, self.ident, indexed)
        marker.write_text(json.dumps({
            "schema": "theory-master/task/v2", "config_path": str(config),
        }), encoding="utf-8")
        config.write_text(json.dumps({
            "task_home": str(self.notebook), "research_root": str(self.research),
            "branch_canvas": str(task / "exploration.canvas"),
        }), encoding="utf-8")
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, self.ident, indexed)

    def test_http_api_requires_auth_and_is_readonly(self):
        class Cache:
            search_root = self.repo

            def get(inner):
                return self.state

        credentials = BasicAuthCredentials("viewer", "secret")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), auth=credentials))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        url = f"{base}/api/canvas-md/tasks/{self.ident}/index"
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with self.assertRaises(HTTPError) as denied:
            build_opener(NoRedirect).open(url, timeout=5)
        # Authentication is required even for the read-only index endpoint.
        self.assertEqual(denied.exception.code, 303)
        auth = "Basic " + base64.b64encode(b"viewer:secret").decode("ascii")
        with urlopen(Request(url, headers={"Authorization": auth}), timeout=5) as response:
            data = json.loads(response.read())
        self.assertEqual(data["stats"]["node_count"], 2)
        worker_url = f"{base}/api/canvas-md/tasks/{self.ident}/workers"
        with self.assertRaises(HTTPError) as denied_worker:
            build_opener(NoRedirect).open(worker_url, timeout=5)
        self.assertEqual(denied_worker.exception.code, 303,
                         "live Worker assignments require the same authentication")
        with urlopen(Request(worker_url, headers={"Authorization": auth}), timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"workers": []})
        with urlopen(Request(f"{base}/research-explorer/?observer_id={self.ident}",
                             headers={"Authorization": auth}), timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn("/api/canvas-md/tasks/", page)
        self.assertIn(self.ident, page)
        with self.assertRaises(HTTPError) as bad:
            urlopen(Request(f"{base}/api/canvas-md/tasks/{self.ident}/document?path=..%2Fprivate.md",
                            headers={"Authorization": auth}), timeout=5)
        self.assertEqual(bad.exception.code, 404)


    def test_search_index_is_reused_and_invalidated_after_file_changes(self):
        from unittest.mock import patch
        from tm_observer import canvas_md
        with patch('tm_observer.canvas_md._document_preview', wraps=canvas_md._document_preview) as reader:
            first = search_payload(self.notebook, 'Proof')
            self.assertTrue(first['results'])
            initial_reads = reader.call_count
            self.assertGreater(initial_reads, 0)
            second = search_payload(self.notebook, 'Detail')
            self.assertTrue(second['results'])
            self.assertEqual(reader.call_count, initial_reads, 'a warm search must not reread Markdown')
            proof = self.notebook / 'nodes' / 'proof.md'
            proof.write_text(proof.read_text(encoding='utf-8') + '\\nFresh cache invalidation token.\\n', encoding='utf-8')
            third = search_payload(self.notebook, 'Fresh cache invalidation token')
            self.assertTrue(any(row['id'] == 'nodes/proof.md' for row in third['results']))
            self.assertGreater(reader.call_count, initial_reads, 'changed mtime/size must invalidate the cache')

    def test_graph_and_search_indexes_have_a_total_document_read_budget(self):
        from unittest.mock import patch
        large = self.notebook / 'notes' / 'large.md'
        large.write_text('# Budgeted document\\nbudget-prefix-token\\n' + ('x' * 100_000), encoding='utf-8')
        with patch('tm_observer.canvas_md.MAX_INDEX_TOTAL_BYTES', 65536):
            graph = build_index(self.notebook)
            self.assertTrue(graph['stats']['partial'])
            self.assertLessEqual(graph['stats']['indexed_document_bytes'], 65536)
            payload = search_payload(self.notebook, 'budget-prefix-token')
            self.assertTrue(payload['partial'])
            self.assertLessEqual(payload['scanned_bytes'], 65536)
            self.assertTrue(any(row['id'] == 'notes/large.md' for row in payload['results']))

if __name__ == "__main__":
    unittest.main()
