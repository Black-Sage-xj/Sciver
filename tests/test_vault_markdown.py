"""File-backed vault reports and append-only publications; no model calls."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import cycle_outcome_documents
from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.cycle_outcome_documents import write_outcome_document
from theory_master.hypothesis_dossier import (
    append_hypothesis_cycle_report, append_hypothesis_evidence, write_hypothesis_dossier,
)
from theory_master.notebook import atomic_json
from theory_master.research_report import load_report, read_report
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.vault_canvas import register_canvas_binding


def outcome(cycle: str = "1") -> dict:
    return {"id": "outcome-" + cycle, "cycle_id": cycle, "hypothesis_id": "H-0001",
            "verdict": "supported", "summary": "Original scientific result"}


def register_report(database: Path, ref: str) -> None:
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(outcome_id="outcome-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="supported", summary="Database source summary", report_ref=ref,
                             record_only=True)


def test_report_content_sections_and_pagination_hash_follow_the_unique_file(tmp_path: Path):
    database = tmp_path / "runtime/science_graph.sqlite3"
    database.parent.mkdir()
    vault = tmp_path / "user-selected-vault"
    note = vault / "notes/task-A/outcomes/1.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Original\n\n## Result\nOriginal result\n", encoding="utf-8")
    register_report(database, "notes/task-A/outcomes/1.md")
    original = load_report(database, "1", vault_root=vault)
    edited = "# User report\n\n## New interpretation\n用户的修改，包含文中的 verdict: rejected。\n"
    note.write_text(edited, encoding="utf-8")
    database_before = database.read_bytes()
    result = read_report(database, "1", vault_root=vault, section="New interpretation", max_chars=16)
    assert result["source_sha256"] == hashlib.sha256(edited.encode()).hexdigest()
    assert result["source_sha256"] != original["source_sha256"]
    assert result["id"] == "notes/task-A/outcomes/1.md"
    assert result["judge_verdict"] == "supported"
    assert result["adjudication_status"] == "adjudicated"
    assert [row["title"] for row in result["sections"]] == ["User report", "New interpretation"]
    rest = read_report(database, "1", vault_root=vault, section="New interpretation", offset=result["next_offset"])
    assert result["content"] + rest["content"] == edited[edited.index("## New interpretation"):]
    assert database.read_bytes() == database_before

    config = tmp_path / "system.json"
    atomic_json(config, {"schema": "theory-master/system/v1", "vault_root": str(vault),
                         "task_home": str(vault), "research_root": str(database.parent),
                         "graph_database": str(database)})
    atomic_json(database.parent / ".theory-master/task.json",
                {"schema": "theory-master/task/v2", "config_path": str(config)})
    assert load_report(database, "1")["content"] == edited


def test_missing_report_is_not_reconstructed_from_database(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    register_report(database, "notes/outcomes/1.md")
    with pytest.raises(FileNotFoundError, match="Markdown is missing"):
        load_report(database, "1")
    assert not (tmp_path / "notes").exists()


@pytest.mark.parametrize("ref", ["../outside.md", "/tmp/outside.md", "notes/report.json", "notes/report"])
def test_report_reference_cannot_escape_the_vault_or_read_non_markdown(tmp_path: Path, ref: str):
    database = tmp_path / "science_graph.sqlite3"
    register_report(database, ref)
    with pytest.raises(ValueError, match="Markdown file inside its vault"):
        load_report(database, "1")


def test_symlink_cannot_read_report_outside_its_vault(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "private.md").write_text("Private content")
    (vault / "linked.md").symlink_to(tmp_path / "private.md")
    database = vault / "science_graph.sqlite3"
    register_report(database, "linked.md")
    with pytest.raises(ValueError, match="outside its vault"):
        load_report(database, "1")


def test_repeat_publication_does_not_even_render_an_existing_markdown(tmp_path: Path, monkeypatch):
    notes = tmp_path / "notes/task-A"
    target = write_outcome_document(notes, outcome())
    assert target == notes / "outcomes/1.md"
    target.write_text("# Entirely user edited\nNo generation marker.\n", encoding="utf-8")
    original = target.read_bytes(), target.stat().st_mtime_ns
    monkeypatch.setattr(cycle_outcome_documents, "render_outcome_document",
                        lambda *args, **kwargs: pytest.fail("existing Markdown must not be regenerated"))
    write_outcome_document(notes, {**outcome(), "summary": "New machine-only source"})
    assert (target.read_bytes(), target.stat().st_mtime_ns) == original


def test_deleted_published_outcome_is_reported_without_recreating_it(tmp_path: Path):
    notes = tmp_path / "notes"
    target = write_outcome_document(notes, outcome())
    target.unlink()
    with pytest.raises(FileNotFoundError, match="Markdown is missing"):
        write_outcome_document(notes, outcome())
    assert not target.exists()


def dossier_args(root: Path) -> dict:
    return {"canvas_path": root / "task.canvas", "notes_root": root / "notes/task-A",
            "hypothesis_id": "H-0001", "cycle_id": "1", "hypothesis": "Frozen claim",
            "scope": "Frozen scope", "why_now": "Known reason", "goal_advance": "Goal",
            "decisive_test": "Frozen comparison", "display_title": "Short title",
            "display_summary": "Human guide"}


def test_dossier_preserves_the_entire_edited_body_and_appends_only_new_cycles(tmp_path: Path):
    args = dossier_args(tmp_path)
    ref = write_hypothesis_dossier(**args)
    target = tmp_path / (ref + ".md")
    report = tmp_path / "notes/task-A/outcomes/1.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Report")
    append_hypothesis_evidence(canvas_path=args["canvas_path"], wikilink_stem=ref,
                               cycle_id="1", evidence_refs=["E-1"])
    append_hypothesis_cycle_report(canvas_path=args["canvas_path"], notes_root=args["notes_root"],
                                  hypothesis_id="H-0001", cycle_id="1", note_path=report)
    edited = b"# User title\n\nAll scientific text and generation markers were edited.\n\n"
    target.write_bytes(edited)
    write_hypothesis_dossier(**{**args, "hypothesis": "Unexpected retry change"})
    append_hypothesis_evidence(canvas_path=args["canvas_path"], wikilink_stem=ref,
                               cycle_id="1", evidence_refs=["E-1"])
    append_hypothesis_cycle_report(canvas_path=args["canvas_path"], notes_root=args["notes_root"],
                                  hypothesis_id="H-0001", cycle_id="1", note_path=report)
    assert target.read_bytes() == edited
    write_hypothesis_dossier(**{**args, "cycle_id": "2", "decisive_test": "New comparison"})
    assert target.read_bytes().startswith(edited)
    assert target.read_text().count("<!-- frozen-cycle:2 -->") == 1
    assert "New comparison" in target.read_text()
    assert "Unexpected retry change" not in target.read_text()


def test_deleted_published_dossier_is_not_recreated(tmp_path: Path):
    args = dossier_args(tmp_path)
    ref = write_hypothesis_dossier(**args)
    target = tmp_path / (ref + ".md")
    target.unlink()
    with pytest.raises(FileNotFoundError, match="dossier is missing"):
        write_hypothesis_dossier(**args)
    assert not target.exists()


def test_runtime_and_automatic_graph_refresh_never_invoke_markdown_publishers(tmp_path: Path, monkeypatch):
    database = tmp_path / "runtime/science_graph.sqlite3"
    vault = tmp_path / "vault"
    note = vault / "notes/hypotheses/H-0001.md"
    note.parent.mkdir(parents=True)
    note.write_text("# User document\nThe entire body is user editable.\n")
    with ScienceGraphStore(database, auto_refresh_canvas=False) as store:
        store.add_hypothesis("H-0001", dossier_ref="notes/hypotheses/H-0001")
    register_canvas_binding(database, vault_root=vault)

    def unexpected_publication(*args, **kwargs):
        pytest.fail("Canvas refresh must not invoke Markdown publication")

    monkeypatch.setattr(cycle_outcome_documents, "write_outcome_document", unexpected_publication)
    monkeypatch.setattr("theory_master.hypothesis_dossier.write_hypothesis_dossier", unexpected_publication)
    monkeypatch.setattr("theory_master.science_canvas_export.write_external_detail_documents", unexpected_publication)
    before = note.read_bytes(), note.stat().st_mtime_ns

    class Projection(CanvasProjectionMixin):
        def _graph_database_path(self):
            return database

    runner = Projection()
    runner.branch_canvas = vault / "exploration.canvas"
    runner.spec = SimpleNamespace(research_root=database.parent, branch_canvas=runner.branch_canvas)
    runner._render_canvas_from_sqlite({})
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0002", title="A graph node awaiting its initial publication")
    assert runner.branch_canvas.is_file()
    assert (note.read_bytes(), note.stat().st_mtime_ns) == before
    assert not (vault / "notes/hypotheses/H-0002.md").exists()
