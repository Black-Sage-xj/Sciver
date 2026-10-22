"""Cross-layer SQLite -> Explorer -> Markdown contract regressions.

Source-of-truth records are immutable. Public links must resolve to a real
allowlisted Markdown document; graph lineage and edge provenance survive intact.
"""
from pathlib import Path

from tm_observer.canvas_md import build_index, load_canvas, read_document
from theory_master.science_graph_store import ScienceGraphStore


def _write(root: Path, name: str, body: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_frozen_dossier_and_legacy_missing_db_ref_are_readable(tmp_path: Path):
    _write(tmp_path, "notes/hypotheses/H-0155.md",
           "<!-- theory-master/hypothesis-dossier/v1 -->\n"
           "# H-0155 · Frozen scientific claim\n"
           "<!-- frozen-cycle:155 -->\n## Scope and decisive test\nUnabridged science.")
    _write(tmp_path, "notes/hypotheses/H-0090.md",
           "# H-0090\nExisting original dossier, missing DB pointer.")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0155", title="Dual-source backbone",
                             summary="Short node summary",
                             dossier_ref="notes/hypotheses/H-0155")
        store.add_hypothesis("H-0090", title="Historical", summary="Legacy science")
        store.add_hypothesis("H-0156", title="Invalid", dossier_ref="../other/secrets")
    indexed = build_index(tmp_path)
    nodes = {n["id"]: n for n in indexed["nodes"]}
    for hid in ("H-0155", "H-0090"):
        note = f"notes/hypotheses/{hid}.md"
        assert nodes[hid]["semantic"]["dossier_ref"] == note
        assert f"[[{note}|查看完整猜想文档]]" in nodes[hid]["text"]
        assert {"kind": "doc", "id": note, "anchor": ""} in indexed["references"][f"node:{hid}"]
        assert read_document(tmp_path.resolve(), note)["content"]
    assert nodes["H-0155"]["semantic"]["cycle"] == 155
    assert nodes["H-0156"]["semantic"]["dossier_ref"] is None
    assert nodes["H-0156"]["semantic"]["unavailable_dossier_ref"] == "../other/secrets"
    assert indexed["references"]["node:H-0156"] == []


def test_raw_report_stems_resolve_and_missing_reports_are_flagged(tmp_path: Path):
    _write(tmp_path, "notes/outcomes/155.md", "# Cycle 155 adjudication")
    _write(tmp_path, "notes/reports/155-science.md", "# Original report\nDetailed science")
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0155", title="Frozen test")
        store.add_hypothesis("H-0156", title="Missing report")
        store.append_outcome(outcome_id="155-outcome", hypothesis_id="H-0155",
                             cycle_id="155", verdict="open",
                             report_ref="notes/reports/155-science")
        store.append_outcome(outcome_id="156-outcome", hypothesis_id="H-0156",
                             cycle_id="156", verdict="open",
                             report_ref="notes/reports/missing")
    snap = load_canvas(tmp_path)
    nodes = {node["id"]: node for node in snap["nodes"]}
    recorded = nodes["H-0155"]["cycle_outcomes"][0]
    assert recorded["source_report_ref"] == "notes/reports/155-science"
    assert recorded["report_ref"] == "notes/reports/155-science.md"
    assert read_document(tmp_path.resolve(), recorded["report_ref"])["title"] == "Original report"
    assert nodes["H-0155"]["outcome"]["report_ref"] == recorded["report_ref"]
    absent = nodes["H-0156"]["cycle_outcomes"][0]
    assert absent["source_report_ref"] == "notes/reports/missing"
    assert absent["report_ref"] is None
    assert absent["unavailable_report_ref"] == "notes/reports/missing"
    assert absent["document_ref"] is None
    assert absent["unavailable_document_ref"] == "notes/outcomes/156.md"
    assert nodes["H-0155"]["cycle_outcomes"][0]["document_ref"] == "notes/outcomes/155.md"
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3", readonly=True) as store:
        # View normalization must not rewrite persisted scientific references.
        assert store.cycle_outcomes("H-0155")[0]["report_ref"] == "notes/reports/155-science"


def test_specialized_edges_and_source_provenance_survive_projection(tmp_path: Path):
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0090", title="Parent")
        store.add_hypothesis("H-0155", title="Derived")
        store.add_evolution(edge_id="lineage", source="H-0090", target="H-0155",
                            operator="specialize", outcome_id="90-outcome")
        store.db.execute(
            "UPDATE scientific_edges SET provenance_json=? WHERE id='lineage'",
            ('{"source":"test historical provenance"}',),
        )
        for eid in ("E-one", "E-two"):
            store.db.execute(
                "INSERT INTO external_records(id,content_type,source,title,content,created_at)"
                " VALUES (?,?,?,?,?,?)", (eid, "StageSummary", "test", eid, "Source text", "2026-10-19"),
            )
        store.db.execute(
            "INSERT INTO external_edges(id,hypothesis_id,external_id,edge_type,originating_outcome)"
            " VALUES (?,?,?,?,?)",
            ("science-source", "H-0155", "E-one", "research_source", "90-outcome"),
        )
        store.db.execute(
            "INSERT INTO external_relations(id,source_external,target_external,edge_type)"
            " VALUES (?,?,?,?)",
            ("historical-lineage", "E-one", "E-two", "scientific_lineage"),
        )
    projected = {e["id"]: e for e in build_index(tmp_path)["edges"]}
    assert projected["lineage"]["evolution_operator"] == "specialize"
    assert projected["lineage"]["originating_outcome"] == "90-outcome"
    assert projected["lineage"]["provenance_json"] == '{"source":"test historical provenance"}'
    assert projected["science-source"]["edge_type"] == "research_source"
    assert projected["science-source"]["originating_outcome"] == "90-outcome"
    assert projected["historical-lineage"]["edge_type"] == "scientific_lineage"


def test_browser_reader_exposes_dossier_and_scientific_lineage():
    """Read-only G6 harness checks real reader affordances, not just source tokens."""
    import runpy
    import shutil
    import subprocess
    import pytest

    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    prior = runpy.run_path(str(Path(__file__).with_name("test_research_explorer_g6.py")))
    prefix = prior["HARNESS"].split("  onReady();await tick();", 1)[0]
    script = prefix + r"""
  nodes[0].semantic.dossier_ref='notes/hypotheses/H-0155.md';
  nodes[0].text='type: Hypothesis\nid: A\n\n# Primary hypothesis\n\nBrief summary\n\n[[notes/hypotheses/H-0155.md|查看完整猜想文档]]';
  index.references['node:A']=[{kind:'doc',id:'notes/hypotheses/H-0155.md',anchor:''}];
  Object.assign(edges.find(e=>e.id==='BC'),{
    edge_type:'hypothesis_evolution',evolution_operator:'specialize',
    originating_outcome:'155-outcome',
    provenance_json:'{"source":"frozen cycle 155"}'
  });
  location.hash='#node%3AA';
  onReady();await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('data-ref="doc:notes/hypotheses/H-0155.md"'),
    'a scientific H must expose the canonical full-dossier button');
  assert(elements['ex-reader'].innerHTML.includes('查看完整猜想文档'));
  latestGraph.events['edge:click']({target:{id:'BC'}});
  await tick();await tick();
  const detail=elements['ex-reader'].innerHTML;
  assert(detail.includes('specialize'), 'edge reader must show the scientific operator');
  assert(detail.includes('155-outcome'), 'edge reader must show originating Outcome');
  assert(detail.includes('frozen cycle 155'), 'edge reader must show retained provenance');
  console.log('science dossier and lineage reader OK');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(prior["SCRIPT"])],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "science dossier and lineage reader OK" in result.stdout
