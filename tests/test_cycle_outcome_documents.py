from pathlib import Path

import pytest

from theory_master.cycle_outcome_documents import (
    _render_outcome_document_v1, backfill_outcome_documents,
    outcome_document_json_ref, outcome_document_ref, render_outcome_document,
    write_outcome_document,
)
from theory_master.science_graph_store import ScienceGraphStore


def test_backfill_preserves_each_judgment_and_never_invents_unjudged_outcome(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    notes = tmp_path / "notes"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(
            outcome_id="5-decision", hypothesis_id="H-0001", cycle_id="5",
            verdict="supported", summary="First scoped claim holds",
            findings=["Exact identity"], record_only=True,
        )
        store.append_outcome(
            outcome_id="6-decision", hypothesis_id="H-0001", cycle_id="6",
            verdict="rejected", summary="Different scoped claim fails",
            judge_rationale="Frozen prediction contradicted", record_only=True,
        )
    preview = backfill_outcome_documents(database, notes)
    assert preview["outcomes"] == 2
    assert not notes.exists()
    written = backfill_outcome_documents(database, notes, preview=False)
    assert written["conflicts"] == []
    assert backfill_outcome_documents(database, notes, preview=False)["conflicts"] == []
    first = (notes / "outcomes" / "5.md").read_text(encoding="utf-8")
    second = (notes / "outcomes" / "6.md").read_text(encoding="utf-8")
    assert "First scoped claim holds" in first
    assert "Different scoped claim fails" not in first
    assert "Judge verdict: `rejected`" in second
    assert "Frozen prediction contradicted" in second
    assert "```json" not in second
    assert "[[notes/outcomes/6.json|打开独立 JSON 审计记录]]" in second
    structured = (notes / "outcomes" / "6.json").read_text(encoding="utf-8")
    assert '"_generated_by": "theory-master/cycle-outcome-json"' in structured
    assert "Different scoped claim fails" in structured
    assert not (notes / "outcomes" / "7.md").exists()
    assert not (notes / "outcomes" / "7.json").exists()


def test_existing_outcome_document_is_never_silently_overwritten(tmp_path: Path):
    notes = tmp_path / "notes"
    doc = notes / "outcomes" / "11.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("human annotation", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        write_outcome_document(notes, {
            "id": "11-decision", "cycle_id": "11", "hypothesis_id": "H-0001",
            "verdict": "open", "summary": "Unresolved",
        })
    assert doc.read_text(encoding="utf-8") == "human annotation"


def test_generated_outcome_documents_refresh_on_renderer_change_but_not_after_human_edits(tmp_path: Path):
    notes = tmp_path / "notes"
    outcome = {
        "id": "12-decision", "cycle_id": "12", "hypothesis_id": "H-0012",
        "verdict": "open", "summary": "Initial scoped conclusion",
        "decisive_test": "Compare the frozen predictors",
        "findings": ["The sign is correct"], "open_questions": ["Does the absolute error meet budget?"],
    }
    target = write_outcome_document(notes, outcome, lock={"hypothesis": "Frozen claim", "scope": "Scope A"})
    json_target = target.with_suffix(".json")
    assert target.exists() and json_target.exists()
    original_markdown = target.read_text(encoding="utf-8")
    assert "Initial scoped conclusion" in original_markdown
    assert "```json" not in original_markdown

    changed = {**outcome, "summary": "Updated scoped conclusion"}
    write_outcome_document(notes, changed, lock={"hypothesis": "Frozen claim", "scope": "Scope A"})
    refreshed = target.read_text(encoding="utf-8")
    assert "Updated scoped conclusion" in refreshed
    assert "Initial scoped conclusion" not in refreshed
    assert "Updated scoped conclusion" in json_target.read_text(encoding="utf-8")

    target.write_text(refreshed + "\nHuman annotation: keep this note.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        write_outcome_document(notes, {**changed, "summary": "Another revision"}, lock={"hypothesis": "Frozen claim", "scope": "Scope A"})
    assert "Human annotation: keep this note." in target.read_text(encoding="utf-8")


def test_legacy_sdk_outcome_document_can_be_migrated_only_when_unedited(tmp_path: Path):
    notes = tmp_path / "notes"
    outcome = {
        "id": "13-decision", "cycle_id": "13", "hypothesis_id": "H-0013",
        "verdict": "supported", "summary": "Scoped support",
        "decisive_test": "Check the frozen criterion",
    }
    lock = {"hypothesis": "Frozen H", "scope": "Scope B"}
    original_decision = "verdict: supported\nOriginal decision body"
    target = notes.parent / outcome_document_ref("13")
    target.parent.mkdir(parents=True)
    target.write_text(_render_outcome_document_v1(outcome, lock=lock, original_decision=original_decision), encoding="utf-8")
    write_outcome_document(notes, outcome, lock=lock, original_decision=original_decision)
    assert "/v2 source:" in target.read_text(encoding="utf-8").splitlines()[0]
    assert (notes.parent / outcome_document_json_ref("13")).exists()
