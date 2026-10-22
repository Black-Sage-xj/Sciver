"""Regression tests for the independent full-data A+C Research Explorer."""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from tm_observer.canvas_md import ExplorerError
from tm_observer.research_explorer_layout_demo import explorer_layout_demo_html
from tm_observer.research_explorer_g6 import graph_content


def render_page():
    with patch("tm_observer.research_explorer_layout_demo.vault_root",
               return_value=Path("/tmp/fake-vault")):
        return explorer_layout_demo_html(Path("/tmp/repo"), {"runs": []}, "8dd38a9977eef7e5")


def test_temp_view_is_its_own_timeline_branch_visual():
    page = render_page()
    for item in ("tm-layout-ac", "时间 × 分支视图", "ac-viewport", "ac-cycle-rail", "ac-canvas-viewport", "ac-detail",
                 "ac-fit-width", "ac-fit-all", "ac-zoom-in", "ac-zoom-out", "ac-back", "ac-forward", "ac-local", "ac-all", "ac-frontier", "ac-fullscreen", "ac-ref-toggle"):
        assert item in page
    assert "tm-layout-b" not in page
    assert 'href="/research-explorer/?observer_id=8dd38a9977eef7e5"' in page
    assert 'href="/research-explorer-tmp-b/?observer_id=8dd38a9977eef7e5"' in page


def test_all_nodes_and_all_edges_are_consumed_without_default_node_cutoff():
    page = render_page()
    for item in ("allNodes=index.nodes", "allEdges=index.edges",
                 "allNodes.forEach", "allEdges.forEach", "index.stats.node_count",
                 "index.stats.edge_count", "未标记时间"):
        assert item in page
    assert "H-0128" not in page
    assert "allNodes=allNodes.filter" not in page
    assert "nodes=allNodes.filter" not in page
    assert "sourceKey='__external_sources__'" in page
    assert "allEdges.forEach((e,i)" in page
    assert "danglingEdgeCount" in page


def test_node_detail_relation_navigation_and_markdown_reader_are_present():
    page = render_page()
    for item in ("function nodeDetail", "function selectEdge", "function md(raw,refs=[],sourcePath='')", "data-ref-kind", "data-ref-anchor", "history.back", "history.forward", "buildFocusSet", "function zoom", "data-jump-node",
                 "index.references", "index.backlinks", "/document?path=", "window.marked", "window.DOMPurify", "window.katex", "splitNodeHeader",
                 "加载后续 Markdown 内容", "function focusNode"):
        assert item in page


def test_cycle_axis_is_a_separate_left_rail_and_tracks_vertical_scroll():
    page = render_page()
    assert '<aside id="ac-cycle-rail" class="ac-cycle-rail"' in page
    assert '<div id="ac-canvas-viewport" class="ac-canvas-viewport">' in page
    assert "HYPOTHESIS BRANCHES" in page
    assert "EXTERNAL SOURCES" in page
    assert "external-outside" not in page
    assert "轴外" not in page
    assert "function renderCycleRail()" in page
    assert "function syncCycleRail()" in page
    assert "viewport.addEventListener('scroll',syncCycleRail" in page
    assert "function focusCycle(cycle)" in page
    assert "const old=scale,cx=" in page
    assert "el('text',{class:'ac-cycle-label'" not in page


def test_cycle_provenance_and_edge_rendering_keep_scientific_semantics():
    page = render_page()
    for item in ("function buildCycleMeta()", "function inferHypothesis", "sourceCycleForExternal", "rowCycleOf",
                 "function predecessorCycle", "reason:'time'", "reason:'predecessor'", "cycleValues=[...new Set([...cycleMeta.values()].map(m=>m.value)",
                 "textContent=hypothesisCount?'CYCLE':'SOURCE CYCLE'", "来源定位，不代表 H 冻结 Cycle", "SOURCES",
                 "sourceKey='__external_sources__'", "pathFor(e,edgeIndex=0)",
                 "reference-hidden", "selected.id===e.fromNode?e.toNode:e.fromNode",
                 "当前索引没有标记为 active"):
        assert item in page


def test_legacy_graph_page_is_not_replaced_by_the_temporary_view():
    old = graph_content()
    assert "tm-layout-ac" not in old
    assert 'id="ex-g6"' in old
    assert 'id="ex-layout-demo-link"' in old
    assert 'href="/research-explorer-tmp/"' in old
    assert "布局B" in old
    for removed in (
        "布局实验 A+C", "布局实验 B", "布局实验 A0 原始版", "布局实验 V0 最早版",
        'id="ex-layout-demo-b-link"', 'id="ex-layout-demo-a0-link"', 'id="ex-layout-demo-v0-link"',
    ):
        assert removed not in old


def test_invalid_task_fails_closed():
    with patch("tm_observer.research_explorer_layout_demo.vault_root",
               side_effect=ExplorerError("任务未在 Run 索引中", 404)):
        page = explorer_layout_demo_html(Path("/tmp/repo"), {"runs": []}, "bad-id")
    assert "任务未在 Run 索引中" in page
    assert "id=\"tm-layout-ac\"" not in page


def test_embedded_javascript_syntax():
    if not shutil.which("node"):
        return
    page = render_page()
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    script = next(s for s in scripts if "tm-layout-ac" in s)
    result = subprocess.run(["node", "--check"], input=script, text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
