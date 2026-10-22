"""Tests for the isolated A0 historical-layout demonstration."""
from pathlib import Path
from unittest.mock import patch

from tm_observer.research_explorer_layout_demo_a0 import explorer_layout_a0_demo_html


def render_page():
    with patch("tm_observer.research_explorer_layout_demo_a0.vault_root", return_value=Path("/tmp/fake-vault")):
        return explorer_layout_a0_demo_html(Path("/tmp/repo"), {"runs": []}, "8dd38a9977eef7e5")


def test_a0_demo_is_separate_and_links_to_comparison_views():
    page = render_page()
    assert "A0 SNAPSHOT · ORIGINAL LAYOUT · READ ONLY" in page
    assert "研究图谱 · A0 原始布局演示" in page
    assert 'href="/research-explorer/?observer_id=8dd38a9977eef7e5"' in page
    assert 'href="/research-explorer-tmp/?observer_id=8dd38a9977eef7e5"' in page
    assert 'href="/research-explorer-tmp-b/?observer_id=8dd38a9977eef7e5"' in page


def test_a0_snapshot_keeps_early_layout_algorithm_and_all_data():
    page = render_page()
    for item in (
        "allNodes=index.nodes", "allEdges=index.edges", "allNodes.forEach", "allEdges.forEach",
        "const cycles=[...new Set(allNodes.map(cycleOf).filter(x=>x!==null))]",
        "function pathFor(e){", "function nodeDetail", "function selectEdge", "index.references", "index.backlinks",
        "未标记时间 · 不推断 Cycle",
    ):
        assert item in page
    # The A0 snapshot intentionally keeps its historical canvas-embedded Cycle bands;
    # it must not silently inherit the later separate-rail/source-lane redesign.
    assert "ac-cycle-rail" not in page
    assert "cycleMeta=new Map()" not in page
    assert "sourceKey='__external_sources__'" not in page
    assert "allNodes=allNodes.filter" not in page
    assert "nodes=allNodes.filter" not in page


def test_a0_demo_module_has_no_unresolved_template_placeholders():
    page = render_page()
    assert "TM_OBSERVER_ID" not in page
    assert "TM_ORIGINAL_HREF" not in page
    assert "TM_LAYOUT_B_HREF" not in page
    assert "TM_CURRENT_A_C_HREF" not in page
