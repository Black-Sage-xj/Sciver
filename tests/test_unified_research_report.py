"""Single-report generation, frozen supplements and targeted Agent reads; no LLM."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.reporting import ReportingMixin
from theory_master.research_report import (
    expand_documents, load_report, read_report, report_cycle_for_path, sections,
    snapshot_documents,
)
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_graph_tools import execute


GUIDE = {"conclusion": "The bounded claim is supported.", "basis": "The frozen comparison resolves it.",
         "scope": "The supplied independent trajectories.", "open_question": ""}


def test_one_report_reuses_overview_and_freezes_supplement(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    notes = tmp_path / "notes"
    notes.mkdir()
    supplement = notes / "derivation.md"
    original = "# Supplement\n\n## Derivation\nExact supplied identity.\n\n## Other\nUnrelated material.\n"
    supplement.write_text(original)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Frozen question")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")

    class Reporter(ReportingMixin, CanvasProjectionMixin):
        def _graph_database_path(self): return database
        def _path_anchors(self): return {"repo_root": str(tmp_path)}
        def _evidence_context(self): return {"evidence_manifest": {"records": []}}
        def _call(self, *args, **kwargs): raise AssertionError("no model calls")
        def _emit(self, *args, **kwargs): pass

    reporter = Reporter()
    reporter.cycle_dir = tmp_path / "research/1"
    (reporter.cycle_dir / "checkpoints").mkdir(parents=True)
    reporter.notes_dir = notes
    reporter.branch_canvas = None
    reporter.spec = SimpleNamespace(notes_mirror_dir=None)
    body = ("## Question and necessary conditions\nFrozen question and conditions.\n\n"
            "## Main results\nThe decisive supplied comparison.\n\n"
            "![[notes/derivation.md#Derivation|Full derivation]]\n\n"
            "## Interpretation and limitations\nBounded interpretation.")
    output = {"title": "A bounded result", "summary": body, "graph_summary": copy.deepcopy(GUIDE)}
    state = {"role_outputs": {"reporter": output}, "last_seq": 1,
             "hypothesis_lock": {"id": "H-0001", "hypothesis": "Frozen question", "scope": "Frozen scope"}}
    branch = {"title": "Frozen question", "hypothesis_ref": "H-0001", "decisive_test": "Frozen comparison"}
    reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Original Judge rationale"},
                                 "Frozen question", "Frozen hypothesis", "supported")
    assert Path(branch["note_path"]) == notes / "outcomes/1.md"
    assert not list(notes.glob("1--*.md"))
    assert state["cycle_outcome_record"]["report_ref"] == "notes/outcomes/1.md"
    frozen = state["cycle_outcome_record"]["report_context"]["documents"][0]
    assert frozen["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert "Exact supplied identity" in frozen["content"]
    assert "Unrelated material" not in frozen["content"]
    reporter._commit_cycle_science(state, database, tmp_path / "exploration.canvas", state["cycle_outcome_record"], None)
    exported = json.loads((tmp_path / "exploration.canvas").read_text())
    assert '"report_context"' not in json.dumps(exported)
    saved = (notes / "outcomes/1.md").read_text()
    assert saved.count(GUIDE["conclusion"]) == 1
    assert 'class="research-supplement"' in saved
    assert "Original Judge rationale" in saved
    assert saved.count("## 复现索引") == 1
    supplement.write_text("# Changed later\nA different unadjudicated claim.")
    reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Original Judge rationale"},
                                 "Frozen question", "Frozen hypothesis", "supported")
    before = database.read_bytes()
    report = load_report(database, "1")
    assert "Exact supplied identity" in report["content"]
    assert "different unadjudicated claim" not in report["content"]
    part = execute(database, "report-read", "1", section="Main results", max_content_chars=60)
    assert part["content"].startswith("## Main results")
    assert part["next_offset"] is not None
    remaining = execute(database, "report-read", "1", section="Main results", offset=part["next_offset"])
    assert "Exact supplied identity" in part["content"] + remaining["content"]
    assert database.read_bytes() == before
    assert report_cycle_for_path(database, "notes/outcomes/1.md") == "1"
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.agent_hypothesis_detail("H-0001")["cycle_outcomes"][0]["report_ref"] == "notes/outcomes/1.md"


def test_supplements_are_explicit_bounded_and_ignore_literal_code(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    secret = tmp_path.parent / (tmp_path.name + "-outside.md")
    secret.write_text("outside")
    try:
        for reference in (str(secret), "notes/missing.md"):
            with pytest.raises(ValueError, match="outside task"):
                snapshot_documents(f"![[{reference}]]", task_root=tmp_path,
                                   note_path=notes / "outcomes/1.md", materials=[])
        literal = "```markdown\n![[notes/missing.md]]\n```\n`![[notes/missing.md]]`"
        assert snapshot_documents(literal, task_root=tmp_path, note_path=notes / "outcomes/1.md", materials=[]) == []
        assert expand_documents(literal, []) == literal
    finally:
        secret.unlink()


def test_registered_execution_document_resolves_its_relative_alias(tmp_path: Path):
    vault = tmp_path / "vault"
    document = tmp_path / "execution" / "analysis" / "proof.md"
    document.parent.mkdir(parents=True)
    raw = "# Proof\n\n## Result\nThe supplied identity.\n\n## Other\nOther content.\n"
    document.write_text(raw)
    asset = {"path": str(document), "relative_path": "analysis/proof.md",
             "kind": "document", "status": "available"}
    body = "![[analysis/proof.md#Result|Existing proof]]"
    rows = snapshot_documents(body, task_root=vault,
                              note_path=vault / "notes/outcomes/1.md",
                              materials=[{"assets": [asset, dict(asset)]}])
    assert len(rows) == 1
    assert rows[0]["reference"] == "analysis/proof.md#Result"
    assert rows[0]["source_ref"] == str(document)
    assert rows[0]["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert "The supplied identity." in rows[0]["content"]
    assert "Other content." not in rows[0]["content"]
    assert "The supplied identity." in expand_documents(body, rows)


@pytest.mark.parametrize("overrides", [
    {"status": "drifted"}, {"status": "missing"}, {"kind": "figure"},
    {"relative_path": "other/proof.md"}, {"relative_path": ""},
])
def test_execution_document_alias_requires_available_exact_document(tmp_path: Path, overrides):
    vault = tmp_path / "vault"
    document = tmp_path / "execution" / "proof.md"
    document.parent.mkdir()
    document.write_text("# Existing proof\n")
    asset = {"path": str(document), "relative_path": "analysis/proof.md",
             "kind": "document", "status": "available", **overrides}
    with pytest.raises(ValueError, match="missing, ambiguous or outside task"):
        snapshot_documents("![[analysis/proof.md]]", task_root=vault,
                           note_path=vault / "notes/outcomes/1.md",
                           materials=[{"assets": [asset]}])


def test_execution_document_alias_rejects_two_distinct_registered_paths(tmp_path: Path):
    vault = tmp_path / "vault"
    assets = []
    for name in ("first", "second"):
        document = tmp_path / name / "proof.md"
        document.parent.mkdir()
        document.write_text("# " + name + "\n")
        assets.append({"path": str(document), "relative_path": "analysis/proof.md",
                       "kind": "document", "status": "available"})
    with pytest.raises(ValueError, match="ambiguous"):
        snapshot_documents("![[analysis/proof.md]]", task_root=vault,
                           note_path=vault / "notes/outcomes/1.md",
                           materials=[{"assets": assets}])


def test_report_uses_the_original_markdown_body_without_composing_a_second_body(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    old = tmp_path / "notes" / "old.md"
    old.parent.mkdir()
    body = "## 结论摘要\nLegacy abstract.\n\n## 主要结果\nOriginal full scientific body."
    old.write_text("# Old report\n" + body + "\n\n## 复现索引\nOriginal data pointers.")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(outcome_id="old", hypothesis_id="H-0001", cycle_id="1", verdict="open",
                             summary=body, graph_summary=GUIDE, report_ref="notes/old.md", record_only=True)
    before = database.read_bytes(), old.read_bytes()
    report = load_report(database, "1")
    assert report["content"].count("Original full scientific body.") == 1
    assert report["content"] == old.read_text()
    assert "Original data pointers" in report["content"]
    assert report_cycle_for_path(database, "notes/old.md") == "1"
    assert (database.read_bytes(), old.read_bytes()) == before


def test_section_ids_ignore_fences_and_disambiguate_repeated_titles():
    text = "# Report\n\n## Result\nFirst.\n```python\n## Not a section\n```\n## Result\nSecond.\n"
    assert [s["id"] for s in sections(text)] == ["report", "result", "result-1"]


def test_unadjudicated_report_never_acquires_judge_verdict(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    note = tmp_path / "notes/preserved.md"
    note.parent.mkdir()
    note.write_text("# 阶段性研究记录 · 未裁决\nUnfinished research")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.record_preserved_cycle(hypothesis_id="H-0001", cycle_id="1", summary="Unfinished research",
            report_ref="notes/preserved.md", source_attention_code="investigator_budget_review_rejected",
            prior_judge_objection="Missing frozen comparison", stage_output_sha256="a" * 64)
    report = load_report(database, "1")
    assert report["judge_verdict"] is None
    assert report["adjudication_status"] == "unadjudicated"
    assert "阶段性研究记录 · 未裁决" in report["content"]
    assert "Judge verdict:" not in report["content"]
    assert "Unfinished research" in report["content"]
