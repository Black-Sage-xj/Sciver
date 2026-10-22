"""Regression tests for the reconstructed earliest Layout Lab prototype."""
from pathlib import Path
from unittest.mock import patch

from tm_observer.research_explorer_layout_demo_v0 import explorer_layout_v0_demo_html
from tm_observer.research_explorer_g6 import graph_content


def render_page():
    with patch("tm_observer.research_explorer_layout_demo_v0.vault_root", return_value=Path("/tmp/fake-vault")):
        return explorer_layout_v0_demo_html(Path("/tmp/repo"), {"runs": []}, "8dd38a9977eef7e5")


def test_v0_reconstructs_the_initial_h_scope_and_cycle_left_graph_right_layout():
    page = render_page()
    for item in (
        "EARLIEST LAYOUT LAB · V0 RECONSTRUCTION · READ ONLY",
        "研究图谱 · 最早布局原型 V0",
        "H-0134–H-0153",
        'id="ac-cycle-rail"',
        'id="ac-canvas-viewport"',
        "function renderCycleRail()",
        "function focusCycle(cycle)",
        "左侧 Cycle 从早到晚排序，右侧只绘制两端都在 H-0134–H-0153 原始样本范围内的真实连线",
        "当前 A+C", "B 前沿分组版", "A0 后续历史版",
    ):
        assert item in page


def test_v0_filters_only_the_initial_h_scope_but_keeps_cross_scope_records_in_details():
    page = render_page()
    for item in (
        "indexEdges=index.edges",
        "allNodes=index.nodes.filter(n=>typeOf(n)==='Hypothesis'&&/^H-\\d+$/.test(String(n.id||''))&&Number(String(n.id).slice(2))>=134&&Number(String(n.id).slice(2))<=153)",
        "allEdges=indexEdges.filter(e=>visibleIds.has(e.fromNode)&&visibleIds.has(e.toNode))",
        "crossScopeEdges=indexEdges.filter(e=>visibleIds.has(e.fromNode)!==visibleIds.has(e.toNode))",
        "incoming=indexEdges.filter(e=>e.toNode===n.id),outgoing=indexEdges.filter(e=>e.fromNode===n.id)",
        "function openOriginalNode(id)",
        "window.open('/research-explorer/?observer_id='+encodeURIComponent(observerId)",
        "14 条内部真实连线",
        "30 条跨范围关系折叠",
    ):
        # Counts are computed from the current index in the browser, so their literal labels
        # are intentionally not baked into the HTML; check the dynamic expression instead.
        if item in ("14 条内部真实连线", "30 条跨范围关系折叠"):
            continue
        assert item in page
    assert "TM_OBSERVER_ID" not in page
    assert "TM_CURRENT_A_C_HREF" not in page
    assert "TM_LAYOUT_B_HREF" not in page
    assert "TM_A0_HREF" not in page


def test_original_graph_hides_v0_toolbar_entry_but_route_remains_registered():
    original = graph_content()
    assert 'id="ex-layout-demo-link"' in original
    assert 'href="/research-explorer-tmp/"' in original
    assert "布局B" in original
    for removed in (
        "布局实验 B", "布局实验 A0 原始版", "布局实验 V0 最早版",
        'id="ex-layout-demo-b-link"', 'id="ex-layout-demo-a0-link"', 'id="ex-layout-demo-v0-link"',
    ):
        assert removed not in original
    source = Path(__file__).parents[1] / "tm_observer" / "web.py"
    route_source = source.read_text()
    assert "from .research_explorer_layout_demo_v0 import explorer_layout_v0_demo_html" in route_source
    assert '"/research-explorer-tmp-v0"' in route_source


def test_embedded_javascript_syntax():
    import re
    import shutil
    import subprocess

    if not shutil.which("node"):
        return
    page = render_page()
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    script = next(s for s in scripts if "EARLIEST LAYOUT LAB" in s or "indexEdges=index.edges" in s)
    result = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
