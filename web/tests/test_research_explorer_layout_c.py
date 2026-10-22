"""Read-only layout C regression tests; no scientific Cycle or model calls."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from tm_observer.canvas_md import ExplorerError
from tm_observer.research_explorer_g6 import graph_content
from tm_observer.research_explorer_layout_c import explorer_layout_c_html

BASE = Path(__file__).resolve().parents[1] / "tm_observer"
OBSERVER = "8dd38a9977eef7e5"


def page():
    with patch("tm_observer.research_explorer_layout_c.vault_root",
               return_value=Path("/tmp/test-graph")):
        return explorer_layout_c_html(Path("/tmp"), {"runs": []}, OBSERVER)


def test_original_and_layout_b_unchanged_and_c_linked():
    original = graph_content()
    assert 'id="ex-layout-demo-link"' in original
    assert 'href="/research-explorer-tmp/"' in original
    assert 'id="ex-layout-c-link"' in original
    assert 'href="/research-explorer-tmp-c/"' in original
    source = (BASE / "research_explorer_g6.js").read_text(encoding="utf-8")
    assert "'/research-explorer-tmp-c/?observer_id='" in source
    assert "const c=$('ex-layout-c-link')" in source


def test_layout_c_standalone_and_read_only():
    html = page()
    assert 'id="tm-tree-c"' in html
    assert 'id="c-svg"' in html
    assert 'href="/research-explorer-tmp/?observer_id=' + OBSERVER + '"' in html
    assert 'href="/research-explorer-tmp-c/?observer_id=' + OBSERVER + '"' in html
    assert "布局 C" in html
    assert "fetch('/api/canvas-md/tasks/'" in html
    assert "buildTree(data.nodes,data.edges)" in html
    assert "secondary.length" in html
    assert "selected?ancestorPath(selected)" in html
    assert "setPointerCapture" in html
    # The visualization only uses GET index and cannot mutate the science graph.
    assert "method:'POST'" not in html
    assert "method:'PUT'" not in html
    assert "/api/canvas-md/tasks/" in html


def test_invalid_task_is_safe():
    with patch("tm_observer.research_explorer_layout_c.vault_root",
               side_effect=ExplorerError("找不到任务", 404)):
        output = explorer_layout_c_html(Path("/tmp"), {}, "invalid")
    assert "找不到任务" in output
    assert 'id="tm-tree-c"' not in output


def test_route_exists_without_replacing_legacy_routes():
    content = (BASE / "web.py").read_text(encoding="utf-8")
    for route in ("/research-explorer-tmp-c", "/research-explorer-tmp-b",
                  "/research-explorer-tmp", "/research-explorer"):
        assert route in content
    assert "explorer_layout_c_html(cache.search_root, state, selected)" in content


def test_layout_c_javascript_syntax():
    if not shutil.which("node"):
        return
    source = (BASE / "research_explorer_layout_c.js").read_text(encoding="utf-8")
    assert "</script>" not in source.lower()
    subprocess.run(["node", "--check", str(BASE / "research_explorer_layout_c.js")],
                   check=True, timeout=10)


def test_branch_projection_multi_parent_and_frontier_semantics():
    if not shutil.which("node"):
        return
    source = BASE / "research_explorer_layout_c.js"
    script = r"""
const assert = require('node:assert/strict');
const {buildTree,nodeStatus,verdict,freezeCycle}=require(process.argv[1]);
const N=(id,cycle,status,extra={})=>({id,label:id,semantic:{type:'Hypothesis',cycle,
  frontier_status:status,...extra}});
const ns=[
 N('H-001',1,'closed'), N('H-002',2,'closed'),
 N('H-003',3,'active'), N('H-004',4,'parked'),
 N('H-005',5,'active',{cycle_outcomes:[{verdict:'supported',cycle_id:'5'}]}),
 N('H-006',null,null), N('H-007',7,null)
];
const E=(a,b,id)=>({fromNode:a,toNode:b,id,edge_type:'hypothesis_evolution'});
const es=[E('H-001','H-002','e1'),E('H-001','H-003','e2'),
 E('H-002','H-004','e3'), E('H-003','H-004','e4'),
 E('H-004','H-005','e5'),E('H-007','H-002','backward'),
 E('H-006','H-005','unknown_to_known')];
const t=buildTree(ns,es);
assert.equal(t.nodes.length,7);
assert.equal(t.allEdges.length,7);
assert.equal(t.primaryEdges.length+t.secondary.length,7);
assert.equal(new Set(t.pos.keys()).size,7);
assert.equal(t.parent.get('H-004'),'H-003'); // nearest eligible predecessor, display only
assert(t.secondary.some(e=>e.id==='e3'));
assert(t.secondary.some(e=>e.id==='backward'));
assert.equal(t.subtree.get('H-001').active,2); // closed ancestor includes active descendants
assert.equal(nodeStatus(ns[0]),'closed');
assert.equal(verdict(ns[4]),'supported');
assert.equal(freezeCycle(ns[5]),null);
assert(t.pos.get('H-002').x<t.pos.get('H-004').x);
assert(t.pos.get('H-002').y!==t.pos.get('H-003').y);
const t2=buildTree(ns,es);
assert.deepEqual([...t.pos], [...t2.pos]); // deterministic tree layout
console.log('layout C tree projection OK');
"""
    result = subprocess.run(["node", "-e", script, str(source)], check=True,
                            capture_output=True, text=True, timeout=15)
    assert "layout C tree projection OK" in result.stdout
