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
from contextlib import nullcontext
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.parse import urlencode

from tm_observer.auth import BasicAuthCredentials
from tm_observer.canvas_md import ExplorerError, build_index, read_document, resolve_link, search_payload, vault_root
from tm_observer.canvas_md_routes import canvas_md_get, canvas_md_image_get
from tm_observer.research_explorer_ui import EXPLORER_HTML, explorer_html
from tm_observer.web import ThreadingHTTPServer, make_handler


class CanvasMDTests(unittest.TestCase):
    def test_unified_cycle_report_reads_the_unique_file_and_keeps_database_verdict(self):
        from theory_master.science_graph_store import ScienceGraphStore
        database = self.notebook / "science_graph.sqlite3"
        body = "## Main results\nA specific bounded result.\n\n## Interpretation\nNecessary limitations."
        with ScienceGraphStore(database) as store:
            store.add_hypothesis("H-0042")
            store.append_outcome(outcome_id="old-1", hypothesis_id="H-0042", cycle_id="1",
                verdict="supported", title="One research report", summary=body,
                judge_rationale="A frozen comparison", report_ref="notes/detail.md", record_only=True,
                report_context={"schema": "theory-master/research-report/v1", "body": body, "documents": []})
        note = self.notebook / "notes/detail.md"
        note.write_text("# User-edited report\n\n" + body + "\n\nA user correction: verdict: rejected.\n", encoding="utf-8")
        before = database.read_bytes(), (self.notebook / "notes/detail.md").read_bytes()
        route = f"/api/canvas-md/tasks/{self.ident}/report"
        raw, status = canvas_md_get(route, "cycle=1", self.repo, self.state)
        report = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertNotIn("report_context", build_index(self.notebook)["nodes"][0]["semantic"]["cycle_outcomes"][0])
        self.assertEqual(report["title"], "One research report")
        self.assertEqual(report["content"].count("A specific bounded result."), 1)
        self.assertEqual(report["content"], note.read_text(encoding="utf-8"))
        self.assertNotIn("A frozen comparison", report["content"])
        self.assertEqual(report["source_sha256"], hashlib.sha256(note.read_bytes()).hexdigest())
        self.assertEqual(report["judge_verdict"], "supported")
        self.assertEqual(report["adjudication_status"], "adjudicated")
        selected, _ = canvas_md_get(route, "cycle=1&section=main-results", self.repo, self.state)
        self.assertIn("A specific bounded result.", json.loads(selected)["content"])
        self.assertNotIn("Necessary limitations", json.loads(selected)["content"])
        alias = read_document(self.notebook, "notes/detail.md")
        self.assertEqual(alias["report_cycle"], "1")
        with self.assertRaises(ExplorerError) as missing:
            canvas_md_get(route, "cycle=2", self.repo, self.state)
        self.assertEqual(missing.exception.status, 404)
        self.assertEqual((database.read_bytes(), (self.notebook / "notes/detail.md").read_bytes()), before)

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
                    "research_root": str(self.research), "task_name": "research test", "state": "COMPLETED"}
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

    def test_images_resolve_document_vault_research_and_repo_paths(self):
        figure = self.notebook / "assets" / "对比 图.svg"
        figure.parent.mkdir()
        figure.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        route = f"/api/canvas-md/tasks/{self.ident}/image"
        for target in ("../assets/对比 图.svg", "assets/对比 图.svg", str(figure)):
            body, mime = canvas_md_image_get(route, urlencode({"path": target,
                "source": "notes/detail.md"}), self.repo, self.state)
            self.assertEqual(body, figure.read_bytes())
            self.assertEqual(mime, "image/svg+xml")
        local = self.notebook / "notes" / "assets" / figure.name
        local.parent.mkdir()
        local.write_bytes(b"document relative")
        for vault, expected in (("0", local.read_bytes()), ("1", figure.read_bytes())):
            body, _ = canvas_md_image_get(route, urlencode({"path": "assets/对比 图.svg",
                "source": "notes/detail.md", "vault": vault}), self.repo, self.state)
            self.assertEqual(body, expected)
        result = self.research / "result.png"
        result.write_bytes(b"\x89PNG\r\n\x1a\n")
        for target in ("result.png", str(result), result.relative_to(self.repo).as_posix()):
            body, mime = canvas_md_image_get(route, urlencode({"path": target}), self.repo, self.state)
            self.assertEqual((body, mime), (result.read_bytes(), "image/png"))

    def test_image_api_rejects_other_tasks_symlinks_types_and_oversize(self):
        from unittest.mock import patch
        outside = self.repo / "other-task.png"
        outside.write_bytes(b"private")
        (self.notebook / "escape.png").symlink_to(outside)
        (self.notebook / "data.csv").write_text("private,data")
        (self.notebook / "large.png").write_bytes(b"12345")
        route = f"/api/canvas-md/tasks/{self.ident}/image"
        for target, status in ((str(outside), 403), ("../../../other-task.png", 403),
                               ("escape.png", 404), ("data.csv", 415), ("missing.png", 404),
                               ("", 400), ("\x00.png", 400)):
            with self.subTest(target=target), self.assertRaises(ExplorerError) as error:
                canvas_md_image_get(route, urlencode({"path": target}), self.repo, self.state)
            self.assertEqual(error.exception.status, status)
        with patch("tm_observer.canvas_md_routes.MAX_IMAGE_BYTES", 4):
            with self.assertRaises(ExplorerError) as error:
                canvas_md_image_get(route, "path=large.png", self.repo, self.state)
            self.assertEqual(error.exception.status, 413)

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
        task = self.repo / "project" / "tasks" / "task-2"
        task.mkdir(parents=True)
        (task / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        config = task / "theory-master.config.json"
        config.write_text(json.dumps({
            "schema": "theory-master/system/v1",
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

    def _external_registered_task(self, task=None):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        research = Path(external.name) / "runtime" / "research"
        research.mkdir(parents=True)
        task = task or self.repo / "project" / "external-task"
        task.mkdir(parents=True, exist_ok=True)
        (task / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        config = task / "theory-master.config.json"
        payload = {
            "schema": "theory-master/system/v1", "path_model": "theory-master/task-path/v2",
            "task_home": str(task), "research_root": str(research),
            "plan_path": str(task / "research_plan.md"),
            "branch_canvas": str(task / "exploration.canvas"),
        }
        config.write_text(json.dumps(payload), encoding="utf-8")
        marker = task / ".theory-master" / "task.json"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(json.dumps({"schema": "theory-master/task/v2", "config_path": str(config)}),
                          encoding="utf-8")
        ident = hashlib.sha256(str(research).encode()).hexdigest()[:16]
        state = {"runs": [dict(self.run, observer_id=ident, task_root=str(task), research_root=str(research))]}
        return task, config, marker, payload, ident, state

    def test_bound_external_runtime_and_task_as_search_root_are_readable(self):
        task, _, _, _, ident, state = self._external_registered_task()
        for search_root in (self.repo, task):
            with self.subTest(search_root=search_root):
                self.assertEqual(vault_root(search_root, ident, state), task)
                payload, status = canvas_md_get(f"/api/canvas-md/tasks/{ident}/index", "", search_root, state)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(payload)["nodes"], [])

    def test_expanded_mapping_requires_every_canonical_sdk_binding_field(self):
        task, config, marker, payload, ident, state = self._external_registered_task()
        for field in ("schema", "path_model", "task_home", "research_root", "plan_path", "branch_canvas"):
            for replacement in (None, "wrong", 7, {"path": "wrong"}):
                with self.subTest(field=field, replacement=replacement):
                    changed = dict(payload)
                    changed[field] = replacement
                    config.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ExplorerError):
                        vault_root(self.repo, ident, state)
        config.write_text(json.dumps(payload), encoding="utf-8")
        for binding in ({"schema": "wrong", "config_path": str(config)},
                        {"schema": "theory-master/task/v2", "config_path": str(task / "other.json")}):
            marker.write_text(json.dumps(binding), encoding="utf-8")
            with self.subTest(binding=binding), self.assertRaises(ExplorerError):
                vault_root(self.repo, ident, state)
        marker.unlink()
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, ident, state)

    def test_external_runtime_does_not_admit_unbound_legacy_or_outside_task_home(self):
        task, _, marker, _, ident, state = self._external_registered_task(self.notebook)
        marker.unlink()
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, ident, state)
        self.assertEqual(task, self.notebook)
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        _, _, _, _, other_id, other_state = self._external_registered_task(Path(elsewhere.name) / "task")
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, other_id, other_state)
        _, _, _, _, notebook_id, notebook_state = self._external_registered_task(self.repo / "笔记区")
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, notebook_id, notebook_state)

    def test_expanded_mapping_rejects_symlink_binding_config_graph_and_raw_home(self):
        task, config, marker, _, ident, state = self._external_registered_task()
        for path in (marker, config, task / "exploration.canvas"):
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            try:
                with self.subTest(path=path), self.assertRaises(ExplorerError):
                    vault_root(self.repo, ident, state)
            finally:
                path.unlink()
                original.rename(path)
        marker.parent.rename(task / ".original-marker")
        marker.parent.symlink_to(task / ".original-marker", target_is_directory=True)
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, ident, state)
        marker.parent.unlink()
        (task / ".original-marker").rename(marker.parent)
        raw_state = {"runs": [dict(state["runs"][0], task_root=str(task / ".." / task.name))]}
        with self.assertRaises(ExplorerError):
            vault_root(self.repo, ident, raw_state)

    def test_in_scope_legacy_without_config_remains_readable(self):
        (self.notebook / "theory-master.config.json").unlink()
        self.assertEqual(vault_root(self.repo, self.ident, self.state), self.notebook)

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
        image = self.notebook / "figure.svg"
        image.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        image_url = f"{base}/api/canvas-md/tasks/{self.ident}/image?path=figure.svg"
        with self.assertRaises(HTTPError) as denied_image:
            build_opener(NoRedirect).open(image_url, timeout=5)
        self.assertEqual(denied_image.exception.code, 303)
        with urlopen(Request(image_url, headers={"Authorization": auth}), timeout=5) as response:
            self.assertEqual(response.read(), image.read_bytes())
            self.assertEqual(response.headers["Content-Type"], "image/svg+xml")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("sandbox", response.headers["Content-Security-Policy"])
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

    def test_numeric_search_prioritizes_exact_cycle_over_full_text(self):
        from unittest.mock import patch
        from tm_observer import canvas_md

        unrelated = [{"id": f"other-{i}", "label": f"Mentions 210 {i}",
                      "content": "210 appears in body", "cycle_ids": []} for i in range(70)]
        exact_node = {"id": "H-0210", "label": "Target hypothesis", "content": "No number here",
                      "cycle_ids": ["210"]}
        exact_doc = {"id": "notes/outcomes/210.md", "label": "Outcome", "content": "No number here"}
        with patch.object(canvas_md, "_search_index", return_value={
            "nodes": unrelated + [exact_node], "documents": [exact_doc],
            "partial": False, "scanned_documents": 1, "scanned_bytes": 1,
        }):
            hits = search_payload(self.notebook, "210")["results"]
            self.assertEqual([(hit["kind"], hit["id"]) for hit in hits[:2]],
                             [("node", "H-0210"), ("doc", "notes/outcomes/210.md")])
            self.assertEqual(len(hits), 60)
            self.assertEqual(search_payload(self.notebook, "21")["results"][0]["id"], "other-0")

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

    def _final_report_fixture(self, name="report-import"):
        folder = self.repo / name
        frozen = folder / "sources/files"
        frozen.mkdir(parents=True)
        body = "# Completed toy comparison\n\n## Answer\nA bounded result. [[file:toy-data]]\n"
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=")
        note = b"# Frozen supplement\n\n![Measured curve](../figures/measured.png)\n"
        data = b'{"estimate": 1.5, "scope": "toy"}'
        files = [("file:toy-data", data, ".json", "document", self.research / "1/workspace/result.json"),
                 ("file:toy-note", note, ".md", "document", self.research / "1/workspace/docs/supplement.md"),
                 ("file:toy-curve", png, ".png", "figure", self.research / "1/workspace/figures/measured.png"),
                 ("file:toy-cycle-report", b"# Recorded Cycle report\n\nThe recorded toy comparison is bounded.\n", ".md", "document", self.notebook / "notes/outcomes/1.md")]
        documents, figures, source_index = [], [], {}
        for ref, raw, suffix, kind, original in files:
            digest = hashlib.sha256(raw).hexdigest()
            relative = "files/" + digest + suffix
            (folder / "sources" / relative).write_bytes(raw)
            row = {"id": ref, "source_ref": ref, "reference": str(original), "source_path": str(original),
                   "frozen_path": relative, "sha256": digest, "frozen_sha256": digest,
                   "size": len(raw), "status": "available", "kind": kind}
            (figures if kind == "figure" else documents).append(row)
            source_index[ref] = {"kind": kind, "id": ref}
        source_index["H-0042"] = {"kind": "hypothesis", "id": "H-0042"}
        source_index["notes/outcomes/1.md"] = {"kind": "document", "id": "file:toy-cycle-report"}
        source_index["O-toy-1"] = {"kind": "outcome", "id": "O-toy-1"}
        snapshot = {"fingerprint": "frozen-toy", "sources": {"research_root": str(self.research)},
                    "goal_terminal": {"verdict": "passed"}, "documents": documents, "figures": figures,
                    "source_index": source_index, "hypotheses": [{"id": "H-0042", "title": "Toy comparison"}],
                    "outcomes": [{"id": "O-toy-1", "source_ref": "O-toy-1", "cycle_id": "1", "hypothesis_id": "H-0042",
                                  "report_ref": "notes/outcomes/1.md", "title": "Toy comparison", "verdict": "supported"}]}
        result = {"state": "ready", "snapshot_fingerprint": "frozen-toy",
                  "report_sha256": hashlib.sha256(body.encode()).hexdigest()}
        (folder / "report.md").write_text(body)
        (folder / "report.json").write_text(json.dumps({"schema_version": "1", "title": "Toy final report", "markdown": body,
            "selected_hypothesis_ids": ["H-0042"], "research_path": [{"hypothesis_id": "H-0042", "role": "main",
                "explanation": "Compare the recorded toy measurements to answer the question.",
                "outcome_ids": [], "source_refs": ["H-0042"]}]}))
        (folder / "result.json").write_text(json.dumps(result))
        (folder / "source-snapshot.json").write_text(json.dumps(snapshot))
        (folder / "review.json").write_text('{"verdict":"pass","issues":[]}')
        (folder / "trajectory.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>H-0042</text></svg>')
        (folder / "agent-runs").mkdir()
        (folder / "agent-runs/private-input.json").write_text('"must not publish"')
        return folder, png

    def test_unfinished_task_has_no_report_metadata_or_document_access(self):
        from unittest.mock import patch
        route = f"/api/canvas-md/tasks/{self.ident}"
        self.run["state"] = "LIVE"
        with patch("tm_observer.final_report_routes.report_metadata") as metadata:
            payload, _ = canvas_md_get(route + "/index", "", self.repo, self.state)
            self.assertFalse(json.loads(payload)["final_report"]["task_complete"])
            metadata.assert_not_called()
        for action in ("final-report", "final-report-source"):
            with self.subTest(action=action), self.assertRaises(ExplorerError) as error:
                canvas_md_get(route + "/" + action, "", self.repo, self.state)
            self.assertEqual(error.exception.status, 404)
        with self.assertRaises(ExplorerError):
            canvas_md_image_get(route + "/image", urlencode({"source": "final-reports/" + "a" * 64 + "/report.md", "path": "research-story.svg"}), self.repo, self.state)

    def test_final_report_is_bound_to_task_and_reuses_document_api_shape(self):
        from theory_master.final_report_publication import publish_report
        route = f"/api/canvas-md/tasks/{self.ident}"
        missing, _ = canvas_md_get(route + "/final-report", "", self.repo, self.state)
        self.assertFalse(json.loads(missing)["available"])
        self.assertFalse((self.research / ".theory-master/final-reports").exists())
        folder, _ = self._final_report_fixture()
        before = (folder / "report.md").read_bytes(), (self.notebook / "exploration.canvas").read_bytes()
        published = publish_report(self.research, folder)
        self.assertFalse((Path(published["directory"]) / "agent-runs").exists())
        index, _ = canvas_md_get(route + "/index", "", self.repo, self.state)
        self.assertEqual(json.loads(index)["final_report"]["version"], published["version"])
        payload, status = canvas_md_get(route + "/final-report", "", self.repo, self.state)
        report = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(report["id"], f"final-reports/{published['version']}/report.md")
        self.assertIn('class="research-lineage-image research-story-image"', report["content"])
        self.assertIn('<img src="research-story.svg" width="420"', report["content"])
        self.assertIn("研究主线概览", report["content"])
        self.assertNotIn("实线为主前序", report["content"])
        self.assertEqual(list(report["source_index"]), ["cycle:1"])
        self.assertIn("[[cycle:1|Cycle 1]]", report["content"])
        self.assertNotIn("[[file:toy-data]]", report["content"])
        cycle, _ = canvas_md_get(route + "/final-report-source", urlencode({"version": published["version"], "ref": "cycle:1"}), self.repo, self.state)
        self.assertEqual(json.loads(cycle)["title"], "Cycle 1")
        self.assertIn("The recorded toy comparison is bounded", json.loads(cycle)["content"])
        self.assertEqual(report["body_source_sha256"], hashlib.sha256(before[0]).hexdigest())
        self.assertNotEqual(report["source_sha256"], report["body_source_sha256"])
        source, _ = canvas_md_get(route + "/final-report-source", urlencode({"version": published["version"], "ref": "file:toy-data"}), self.repo, self.state)
        self.assertIn("```json", json.loads(source)["content"])
        self.assertIn('"estimate": 1.5', json.loads(source)["content"])
        self.assertEqual(before, ((folder / "report.md").read_bytes(), (self.notebook / "exploration.canvas").read_bytes()))

    def test_final_report_images_use_frozen_report_and_original_reference_aliases(self):
        from theory_master.final_report_publication import publish_report
        folder, png = self._final_report_fixture()
        published = publish_report(self.research, folder)
        route = f"/api/canvas-md/tasks/{self.ident}"
        source, _ = canvas_md_get(route + "/final-report-source", urlencode({"version": published["version"], "ref": "file:toy-note"}), self.repo, self.state)
        document = json.loads(source)
        self.assertTrue(document["id"].startswith(f"final-reports/{published['version']}/sources/files/"))
        image, mime = canvas_md_image_get(route + "/image", urlencode({"source": document["id"], "path": "../figures/measured.png"}), self.repo, self.state)
        self.assertEqual((image, mime), (png, "image/png"))
        graph, mime = canvas_md_image_get(route + "/image", urlencode({"source": f"final-reports/{published['version']}/report.md", "path": "trajectory.svg"}), self.repo, self.state)
        self.assertEqual(mime, "image/svg+xml")
        self.assertIn(b"H-0042", graph)
        self.assertFalse((self.research / "1/workspace/figures/measured.png").exists(), "live files were not needed")

    def test_final_report_projects_linear_story_without_changing_frozen_report_or_diagram(self):
        from theory_master.final_report_publication import _lineage_width, final_report_image, publish_report, read_final_report
        folder, _ = self._final_report_fixture()
        drawing = folder / "trajectory.svg"
        drawing.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 214"><text>H-0042</text></svg>')
        original = (folder / "report.md").read_bytes(), drawing.read_bytes()
        publication = publish_report(self.research, folder)
        report = read_final_report(self.research, version=publication["version"])
        self.assertIn('<img src="research-story.svg" width="420"', report["content"])
        self.assertIn('aria-label="研究主线图"', report["content"])
        self.assertFalse((Path(publication["directory"]) / "research-story.svg").exists())
        image, mime = final_report_image(self.research, report["id"], "research-story.svg")
        self.assertEqual(mime, "image/svg+xml")
        self.assertIn(b"Compare the recorded toy measurements", image)
        self.assertNotEqual(image, original[1])
        self.assertEqual(report["body_source_sha256"], hashlib.sha256(original[0]).hexdigest())
        self.assertEqual(original, ((folder / "report.md").read_bytes(), drawing.read_bytes()))
        self.assertEqual(_lineage_width(b'<svg viewBox="0 0 4000 800"/>'), 1600)
        self.assertEqual(_lineage_width(b'<!DOCTYPE svg [<!ENTITY x "expanded">]><svg/>'), 800)

    def test_final_report_rejects_unregistered_paths_and_cross_task_imports(self):
        from theory_master.final_report_publication import publish_report
        folder, _ = self._final_report_fixture()
        with self.assertRaisesRegex(ValueError, "different task"):
            publish_report(self.repo, folder)
        publication = publish_report(self.research, folder)
        source = f"final-reports/{publication['version']}/report.md"
        route = f"/api/canvas-md/tasks/{self.ident}"
        for target in ("../private.png", "agent-runs/private-input.json", "report.json", "%2e%2e/private.png", "%5cprivate.png"):
            with self.subTest(target=target), self.assertRaises(ExplorerError):
                canvas_md_image_get(route + "/image", urlencode({"source": source, "path": target}), self.repo, self.state)
        with self.assertRaises(ExplorerError):
            canvas_md_get(route + "/final-report-source", urlencode({"version": publication["version"], "ref": str(self.repo / "secret.txt")}), self.repo, self.state)
        (Path(publication["directory"]) / "trajectory.svg").write_text("changed")
        with self.assertRaises(ExplorerError):
            canvas_md_image_get(route + "/image", urlencode({"source": source, "path": "trajectory.svg"}), self.repo, self.state)

    def test_final_report_obsidian_images_keep_vault_relative_semantics(self):
        from theory_master.final_report_publication import publish_report
        folder, local_image = self._final_report_fixture()
        snapshot = json.loads((folder / "source-snapshot.json").read_text())
        snapshot["original_question"] = {"path": str(self.notebook / "research_plan.md")}
        local = snapshot["figures"][0]
        local["source_path"] = str(self.research / "1/workspace/docs/assets/curve.png")
        local["reference"] = "assets/curve.png"
        root_image = local_image + b"root-variant"
        digest = hashlib.sha256(root_image).hexdigest()
        (folder / "sources/files" / (digest + ".png")).write_bytes(root_image)
        snapshot["figures"].append({**local, "id": "file:toy-vault-curve", "source_ref": "file:toy-vault-curve",
            "source_path": str(self.notebook / "assets/curve.png"), "frozen_path": "files/" + digest + ".png",
            "sha256": digest, "frozen_sha256": digest, "size": len(root_image)})
        snapshot["source_index"]["file:toy-vault-curve"] = {"kind": "figure", "id": "file:toy-vault-curve"}
        (folder / "source-snapshot.json").write_text(json.dumps(snapshot))
        publication = publish_report(self.research, folder)
        route = f"/api/canvas-md/tasks/{self.ident}"
        payload, _ = canvas_md_get(route + "/final-report-source", urlencode({"version": publication["version"], "ref": "file:toy-note"}), self.repo, self.state)
        source = json.loads(payload)["id"]
        for vault, expected in (("0", local_image), ("1", root_image)):
            image, _ = canvas_md_image_get(route + "/image", urlencode({"source": source, "path": "assets/curve.png", "vault": vault}), self.repo, self.state)
            self.assertEqual(image, expected)

    def test_final_report_discovers_only_internal_job_outputs_without_import_side_effects(self):
        from theory_master.final_report_publication import final_report_descriptor, read_final_report
        registry = self.research / ".theory-master/final-reports"
        jobs = registry / "jobs"
        jobs.mkdir(parents=True)
        job_id = "a" * 64
        job_path = jobs / (job_id + ".json")
        job = {"schema": "sciver/final-report-job/v1", "job_id": job_id, "research_root": str(self.research),
               "state": "queued", "updated_at": "2026-11-20T00:00:00+00:00"}
        job_path.write_text(json.dumps(job))
        queued = final_report_descriptor(self.research)
        self.assertFalse(queued["available"])
        self.assertEqual(queued["state"], "queued")
        outside, _ = self._final_report_fixture("outside-report")
        job.update(state="ready", output_dir=str(outside));job_path.write_text(json.dumps(job))
        self.assertFalse(final_report_descriptor(self.research)["available"], "HTTP cannot trust external output_dir")
        inside = registry / "versions" / job_id / "attempt-0001"
        self._final_report_fixture(str(inside))
        job["output_dir"] = str(inside);job_path.write_text(json.dumps(job))
        ready = final_report_descriptor(self.research)
        self.assertTrue(ready["available"])
        self.assertIn("A bounded result", read_final_report(self.research)["content"])
        self.assertFalse((registry / "publications").exists(), "read paths do not publish or write")

    def test_final_report_job_regeneration_keeps_previous_readable_versions(self):
        from theory_master.final_report_publication import final_report_descriptor, read_final_report
        registry = self.research / ".theory-master/final-reports"
        job_id = "b" * 64
        first = registry / "versions" / job_id / "attempt-0001"
        self._final_report_fixture(str(first))
        jobs = registry / "jobs"; jobs.mkdir()
        job_path = jobs / (job_id + ".json")
        job = {"schema": "sciver/final-report-job/v1", "job_id": job_id, "research_root": str(self.research),
               "state": "ready", "output_dir": str(first), "updated_at": "2026-11-20T00:00:00+00:00"}
        job_path.write_text(json.dumps(job))
        old_version = final_report_descriptor(self.research)["version"]
        old_body = read_final_report(self.research, version=old_version)["content"]
        job.pop("output_dir")
        second = registry / "versions" / job_id / "attempt-0002"
        second.mkdir()
        (second / "status.json").write_text('{"state":"composing"}')
        from theory_master.final_report import _lock
        for state in ("queued", "running", "failed"):
            job["state"] = state;job_path.write_text(json.dumps(job))
            # A running writer owns the generation lock, including CLI writers
            # without a service receipt. Merely persisting 'running' is stale.
            with _lock(registry / f"{job_id}.lock") if state == "running" else nullcontext():
                current = final_report_descriptor(self.research)
            self.assertTrue(current["available"])
            self.assertEqual(current["version"], old_version)
            self.assertEqual(current["latest_job_state"], state)
            self.assertEqual(read_final_report(self.research, version=old_version)["content"], old_body)
        job["state"] = "running";job_path.write_text(json.dumps(job))
        interrupted = final_report_descriptor(self.research)
        self.assertTrue(interrupted["available"])
        self.assertEqual(interrupted["latest_job_state"], "failed")
        self._final_report_fixture(str(second))
        body = (second / "report.md").read_text() + "\nA later clarified scope.\n"
        (second / "report.md").write_text(body)
        report = json.loads((second / "report.json").read_text());report["markdown"] = body
        (second / "report.json").write_text(json.dumps(report))
        result = json.loads((second / "result.json").read_text())
        result.update(state="needs_revision", report_sha256=hashlib.sha256(body.encode()).hexdigest())
        (second / "result.json").write_text(json.dumps(result))
        job.update(state="needs_revision", output_dir=str(second));job_path.write_text(json.dumps(job))
        current = final_report_descriptor(self.research)
        self.assertEqual(current["state"], "needs_revision")
        self.assertEqual(len(current["versions"]), 2)
        self.assertIn(old_version, [row["version"] for row in current["versions"]])
        self.assertEqual(read_final_report(self.research, version=old_version)["content"], old_body)

    def test_final_report_job_discovery_rejects_untrusted_ids_and_symlink_attempts(self):
        from theory_master.final_report_publication import final_report_descriptor
        registry = self.research / ".theory-master/final-reports"
        jobs = registry / "jobs";jobs.mkdir(parents=True)
        outside, _ = self._final_report_fixture("external-attempt")
        bad_id = "untrusted-job"
        target = registry / "versions" / bad_id / "attempt-0001"
        self._final_report_fixture(str(target))
        job = {"schema": "sciver/final-report-job/v1", "job_id": bad_id, "research_root": str(self.research),
               "state": "ready", "output_dir": str(target)}
        (jobs / (bad_id + ".json")).write_text(json.dumps(job))
        self.assertFalse(final_report_descriptor(self.research)["available"])
        job_id = "c" * 64
        symlink = registry / "versions" / job_id / "attempt-0001"
        symlink.parent.mkdir()
        symlink.symlink_to(outside, target_is_directory=True)
        job.update(job_id=job_id, output_dir=str(symlink))
        (jobs / (job_id + ".json")).write_text(json.dumps(job))
        self.assertFalse(final_report_descriptor(self.research)["available"])

    def test_final_report_versions_preserve_old_body_and_reject_changed_frozen_source(self):
        from theory_master.final_report_publication import publish_report, read_final_report
        folder, _ = self._final_report_fixture()
        first = publish_report(self.research, folder)
        self.assertEqual(publish_report(self.research, folder)["version"], first["version"])
        old = read_final_report(self.research, version=first["version"])["content"]
        body = (folder / "report.md").read_text() + "\nA clarified scope.\n"
        (folder / "report.md").write_text(body)
        report = json.loads((folder / "report.json").read_text()); report["markdown"] = body
        (folder / "report.json").write_text(json.dumps(report))
        result = json.loads((folder / "result.json").read_text());result["report_sha256"] = hashlib.sha256(body.encode()).hexdigest()
        (folder / "result.json").write_text(json.dumps(result))
        second = publish_report(self.research, folder)
        self.assertNotEqual(second["version"], first["version"])
        self.assertEqual(read_final_report(self.research, version=first["version"])["content"], old)
        raw = next((folder / "sources/files").glob("*.json"));raw.write_text("changed")
        with self.assertRaisesRegex(ValueError, "frozen report source changed"):
            publish_report(self.research, folder)

if __name__ == "__main__":
    unittest.main()
