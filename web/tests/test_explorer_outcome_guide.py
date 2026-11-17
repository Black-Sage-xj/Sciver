"""Read-only Web guides and executable split-card navigation checks."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from theory_master.hypothesis_display import compose_hypothesis_summary
from theory_master.science_graph_store import ScienceGraphStore
from tm_observer.canvas_md import build_index, search
from test_research_explorer_g6 import HARNESS, SCRIPT


GUIDE = {"conclusion": "The local mean bound remains unresolved.",
         "basis": "Both intervals cross the frozen threshold.",
         "scope": "Twelve historically exposed independent samples.",
         "open_question": "Does the mean meet the bound under this measure?"}


@pytest.mark.parametrize("status", ["active", "parked", "closed"])
def test_web_projects_guide_and_branch_reason_without_touching_scientific_records(tmp_path: Path, status: str):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="A local bound", summary=compose_hypothesis_summary(
            "The local mean meets the bound", "Historical sample measure"))
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="open", summary="Full report preserved.", graph_summary=GUIDE,
                             record_only=True)
        reason = "The local test is complete; the mean bound is unresolved."
        store.set_frontier(branch_id="f", hypothesis_id="H-0001", status=status, reason=reason)
    before = database.read_bytes()
    node = build_index(tmp_path)["nodes"][0]
    assert node["semantic"]["cycle_outcomes"][0]["graph_summary"] == GUIDE
    assert node["semantic"]["cycle_outcomes"][0]["summary"] == "Full report preserved."
    assert node["semantic"]["hypothesis_summary"] == compose_hypothesis_summary(
        "The local mean meets the bound", "Historical sample measure")
    assert "整体 H 尚未登记裁决" in node["text"]
    assert "状态：待检验" not in node["text"]
    assert reason in node["text"]
    assert search(tmp_path, "Twelve historically exposed")[0]["id"] == "H-0001"
    assert database.read_bytes() == before
    assert not (tmp_path / "exploration.canvas").exists()


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_guide_leads_split_card_and_one_report_entry_replaces_repeated_full_body():
    bootstrap, marker, _ = HARNESS.partition("  onReady();await tick();")
    assert marker
    script = bootstrap + r"""
  const guide = {
    conclusion:'Local mean bound unresolved',
    basis:'Both confidence intervals cross 0.3',
    scope:'Historical independent samples only',
    open_question:'The local mean bound is still undecided'
  };
  const outcome={cycle_id:'1',verdict:'open',summary:'FULL REPORT BACKGROUND FIRST',
    graph_summary:guide,judge_rationale:'Formal Judge rationale',
    findings:['Inherited knowledge'],open_questions:['Task-wide open problem']};
  nodes[0].semantic.cycle_outcomes=[outcome];nodes[0].semantic.outcome=outcome;
  nodes[0].semantic.frontier_status='parked';
  onReady();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  const html=elements['ex-reader'].innerHTML;
  assert(html.includes('ex-outcome-guide'));
  for(const value of Object.values(guide))assert(html.includes(value));
  assert(!html.includes('FULL REPORT BACKGROUND FIRST'));
  assert(!html.includes('ex-outcome-record'));
  assert(html.includes('data-ref="report:1"'));
  assert.equal((html.match(/阅读研究报告/g)||[]).length,1);
  const card=elements['ex-outcome-cards'].children.find(c=>c.dataset.nodeId==='A');
  assert(card.children[1].children[1].textContent.includes(guide.conclusion));
  assert(!card.children[1].children[1].textContent.includes('FULL REPORT'));
  assert(card.children[1].title.includes(guide.basis));
  const savedGuide=JSON.stringify(outcome.graph_summary);
  card.children[1].onclick({preventDefault(){},stopPropagation(){}});await tick();
  assert.equal(history.state.tmExplorerView.selected.anchor,'outcome');
  assert.equal(JSON.stringify(outcome.graph_summary),savedGuide);
  delete outcome.graph_summary;
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  const legacy=elements['ex-reader'].innerHTML;
  assert(legacy.includes('FULL REPORT BACKGROUND FIRST'));
  assert(!legacy.includes('ex-outcome-guide'));
  const legacyCard=elements['ex-outcome-cards'].children.find(c=>c.dataset.nodeId==='A');
  assert(legacyCard.children[1].children[1].textContent.includes('FULL REPORT'));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
