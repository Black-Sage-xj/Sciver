from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from theory_master.report_synthesis.snapshot import build_snapshot, materialize_snapshot
from report_synthesis_fixtures import SQLITE_SCHEMA


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def research(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text("# Research question\nCompare two synthetic alternatives.\n\n## Scientific Model\nA bounded relation.\n\n## Deliverables\nA documented answer.\n")
    database = root / "science_graph.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(SQLITE_SCHEMA)
        connection.execute("INSERT INTO hypotheses(id,title,summary,created_at) VALUES ('H-0001','Synthetic relation','Scoped claim','now')")
        connection.execute("INSERT INTO outcomes(id,hypothesis_id,created_at,updated_at) VALUES ('whole-1','H-0001','now','now')")
        connection.execute("INSERT INTO task_goal_terminal VALUES (?,?,?,?,?,?,?)",
                           ("goal", "passed", "1", "now", digest(plan), None, "{}"))
    return root, plan, database


def add_outcome(database, cycle, *, report_ref=None, evidence_refs=None, context=None, verdict="supported"):
    item = {"id": f"outcome-{cycle}", "hypothesis_id": "H-0001", "cycle_id": str(cycle), "verdict": verdict,
            "summary": "A synthetic test result", "scope": "Synthetic observations",
            "boundary": ["Only the recorded synthetic setting"], "report_ref": report_ref,
            "evidence_refs": evidence_refs or [], "report_context": context or {}}
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO cycle_evaluations VALUES (?,?,?,?,?,?)",
                           (item["id"], "H-0001", str(cycle), verdict, json.dumps(item), "now"))
    return item


def test_all_rounds_and_frozen_lineage_are_collected_without_scientific_writes(research):
    root, plan, database = research
    for number in range(1, 9):
        add_outcome(database, number, verdict="rejected" if number == 1 else "supported")
    transition = {"prior_gap": "Alternative unresolved", "current_change": "Change the discriminating test",
                  "why_now": "Evidence is available", "goal_advance": "Answer the question", "sources": []}
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO hypothesis_reading_context VALUES (?,?,?)", ("H-0001", json.dumps(transition), "now"))
        connection.execute("INSERT INTO hypotheses(id,title,summary,created_at) VALUES ('H-0002','Refinement','New bounded claim','later')")
        connection.execute("INSERT INTO scientific_edges VALUES (?,?,?,?,?,?)",
                           ("edge-1", "H-0001", "H-0002", "refine", "outcome-1", '{"lineage_role":"main"}'))
        connection.execute("INSERT INTO cycle_preservations VALUES (?,?,?,?)",
                           ("9", "H-0001", json.dumps({"verdict": "unadjudicated", "termination_reason": "goal_completed_stop"}), "now"))
    before = digest(database)
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert len(snapshot["outcomes"]) == 8
    assert snapshot["outcomes"][0]["verdict"] == "rejected"
    assert next(row for row in snapshot["hypotheses"] if row["id"] == "H-0001")["research_transition"] == transition
    assert snapshot["lineage"][0]["role"] == "main"
    assert snapshot["preservations"][0]["verdict"] == "unadjudicated"
    assert snapshot["goal_terminal"]["source_ref"] == "goal:terminal"
    assert snapshot["source_index"]["goal:terminal"]["kind"] == "goal_terminal"
    assert snapshot["scientific_model"]["content"] == "A bounded relation."
    assert digest(database) == before
    assert snapshot["fingerprint"] == build_snapshot(database, research_root=root, plan_path=plan)["fingerprint"]


def test_unsealed_report_requires_explicit_override(research):
    root, plan, database = research
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM task_goal_terminal")
    with pytest.raises(ValueError, match="passed Goal seal"):
        build_snapshot(database, research_root=root, plan_path=plan)
    snapshot = build_snapshot(database, research_root=root, plan_path=plan, require_seal=False)
    assert snapshot["goal_terminal"] is None
    assert "goal_seal_missing:report_is_not_a_completion_receipt" in snapshot["warnings"]


def test_separated_bound_roots_and_explicit_plan_do_not_open_plan_siblings(research, tmp_path):
    root, _, database = research
    vault = tmp_path / "vault"
    vault.mkdir()
    plan = vault / "research_plan.md"
    plan.write_text("# Question\nSee [private sibling](private.txt).")
    (vault / "private.txt").write_text("must not enter snapshot")
    (vault / "notes").mkdir()
    (vault / "notes/round.md").write_text("# Recorded result")
    add_outcome(database, 1, report_ref="notes/round.md")
    narrow = build_snapshot(database, research_root=root, plan_path=plan)
    assert len(narrow["documents"]) == 1
    assert "must not enter snapshot" not in json.dumps(narrow)
    bound = build_snapshot(database, research_root=root, plan_path=plan, allowed_roots=[vault])
    assert any(row["reference"] == "notes/round.md" for row in bound["documents"])


def test_report_links_and_registered_figures_are_frozen(research, tmp_path):
    root, plan, database = research
    (root / "notes").mkdir()
    (root / "result").mkdir()
    figure = root / "result/curve.png"
    figure.write_bytes(PNG)
    report = root / "notes/round.md"
    report.write_text("# Result\n\n![Measured curve](../result/curve.png)")
    (root / "1").mkdir()
    (root / "1/report_material_catalog.json").write_text(json.dumps({"materials": [{"assets": [
        {"status": "available", "kind": "figure", "path": str(figure), "sha256": digest(figure)}]}]}))
    add_outcome(database, 1, report_ref="notes/round.md")
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert len(snapshot["figures"]) == 1
    item = snapshot["figures"][0]
    assert item["status"] == "available"
    assert snapshot["source_index"][item["id"]]["kind"] == "figure"
    frozen = materialize_snapshot(snapshot, tmp_path / "frozen")
    frozen_figure = frozen["snapshot"]["figures"][0]
    assert frozen_figure["path"] == frozen_figure["frozen_path"]
    assert (tmp_path / "frozen" / frozen_figure["path"]).read_bytes() == PNG
    with pytest.raises(ValueError, match="new directory"):
        materialize_snapshot(snapshot, tmp_path / "frozen")


def test_unsafe_svg_outside_symlink_and_credentials_are_not_published(research, tmp_path):
    root, plan, database = research
    outside = tmp_path / "outside.txt"
    outside.write_text("private outside content")
    (root / "linked.txt").symlink_to(outside)
    (root / ".env").write_text("private credential")
    (root / "bad.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    (root / "fake.png").write_text("not an image")
    add_outcome(database, 1, evidence_refs=[str(outside), "linked.txt", ".env", "bad.svg", "fake.png"])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert {row["status"] for row in snapshot["figures"]} == {"unsafe_or_invalid_figure"}
    assert "private outside content" not in json.dumps(snapshot)
    assert "private credential" not in json.dumps(snapshot)
    frozen = materialize_snapshot(snapshot, tmp_path / "frozen")
    assert not any("frozen_path" in row for row in frozen["snapshot"]["figures"])


def test_changed_evidence_and_changed_at_freeze_have_explicit_status(research, tmp_path):
    root, plan, database = research
    evidence = root / "evidence.txt"
    evidence.write_text("current content")
    add_outcome(database, 1, evidence_refs=["evidence.txt"], context={"body": "[evidence](evidence.txt)",
                "evidence_manifest": [{"reference": "evidence.txt", "sha256": "0" * 64}]})
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert next(row for row in snapshot["documents"] if row["reference"] == "evidence.txt")["status"] == "source_changed"
    plan.write_text("# Changed Plan")
    frozen = materialize_snapshot(snapshot, tmp_path / "frozen")
    frozen_plan = next(row for row in frozen["snapshot"]["documents"] if "research_plan" in row["roles"])
    assert frozen_plan["status"] == "source_changed_at_freeze"
    assert not frozen_plan.get("frozen_path")
    assert frozen["snapshot"]["original_question"]["content"].startswith("# Research question")


def test_collects_final_model_and_goal_receipt(research):
    root, plan, database = research
    model = root / "scientific_model_state.json"
    model.write_text('{"version": 3, "updated_from_cycle": "7"}')
    receipt = root / "final-receipt.json"
    receipt.write_text('{"verdict": "passed", "rationale": "Synthetic requirement met"}')
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE task_goal_terminal SET judge_receipt_ref=?", (str(receipt),))
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert snapshot["scientific_model"]["metadata"]["version"] == 3
    assert json.loads(snapshot["goal_receipts"][0]["content"])["verdict"] == "passed"


def test_limits_evidence_without_reading_large_contents_and_rejects_fingerprint_tampering(research, monkeypatch, tmp_path):
    root, plan, database = research
    from theory_master.report_synthesis import snapshot as module
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 2048)
    (root / "large.txt").write_text("X" * 4096)
    add_outcome(database, 1, evidence_refs=["large.txt"])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    large = next(row for row in snapshot["documents"] if row["reference"] == "large.txt")
    assert large["status"] == "not_frozen_size_limit"
    assert "content" not in large and "sha256" not in large
    snapshot["outcomes"][0]["verdict"] = "rejected"
    with pytest.raises(ValueError, match="fingerprint"):
        materialize_snapshot(snapshot, tmp_path / "bad")


def test_evidence_ids_resolve_registry_projection_and_invocation_materials(research):
    root, plan, database = research
    cycle = root / "1"
    (cycle / "workspace").mkdir(parents=True)
    (cycle / "evidence").mkdir()
    (cycle / "invocation_artifacts").mkdir()
    result = cycle / "workspace/result.txt"
    result.write_text("A recorded synthetic result")
    figure = cycle / "workspace/result.png"
    figure.write_bytes(PNG)
    invocation = cycle / "invocation_artifacts/invocation-1.json"
    invocation.write_text(json.dumps({"created": [{"path": str(figure), "sha256": digest(figure)}]}))
    record = {"evidence_id": "E-1-synthetic", "summary": "A bounded comparison", "scope": "Synthetic",
              "artifact_pointers": [str(result)], "integrity": {"artifact_hashes": {str(result): digest(result)}},
              "execution_materials": {"manifest_path": str(invocation), "manifest_sha256": digest(invocation)}}
    (cycle / "evidence/registry.jsonl").write_text(json.dumps(record) + "\n")
    (cycle / "workspace/evidence_manifest.json").write_text(json.dumps({"records": [
        {**record, "adjudicable": False, "lifecycle_status": "retracted"}]}))
    add_outcome(database, 1, evidence_refs=[record["evidence_id"]])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert snapshot["source_index"][record["evidence_id"]]["kind"] == "evidence"
    assert snapshot["evidence_records"][0]["adjudicable"] is False
    assert snapshot["evidence_records"][0]["lifecycle_status"] == "retracted"
    assert any(row["source_path"] == str(result) for row in snapshot["documents"])
    assert snapshot["figures"][0]["source_path"] == str(figure)
    assert not any("missing:E-" in warning for warning in snapshot["warnings"])


def test_legacy_evidence_text_resolves_named_workspace_deliverable(research):
    root, plan, database = research
    workspace = root / "1" / "workspace"
    (workspace / "outputs").mkdir(parents=True)
    delivered = workspace / "outputs" / "results.csv"
    delivered.write_text("row,value\n1,2\n")
    (workspace / "outputs" / "unmentioned.csv").touch()
    record = {"evidence_id": "E-1-synthetic", "artifact_pointers": [],
              "result": {"outputs": ["outputs/results.csv"]}}
    (workspace / "evidence_manifest.json").write_text(json.dumps({"records": [record]}))
    add_outcome(database, 1, evidence_refs=[record["evidence_id"]])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    products = [row for row in snapshot["documents"] if "recorded_evidence_artifact" in row["roles"]]
    assert [row["source_path"] for row in products] == [str(delivered)]
    assert snapshot["source_index"][products[0]["source_ref"]]["sha256"] == digest(delivered)


def test_primary_round_evidence_is_collected_before_recursive_supplements(research, monkeypatch):
    root, plan, database = research
    monkeypatch.setattr("theory_master.report_synthesis.snapshot.MAX_FILES", 6)
    first_workspace = root / "1" / "workspace"
    first_workspace.mkdir(parents=True)
    second_workspace = root / "2" / "workspace"
    second_workspace.mkdir(parents=True)
    script = first_workspace / "optional.py"
    decoys = [f"raw-{i}.csv" for i in range(10)]
    for name in decoys:
        (first_workspace / name).write_text("row,value\n1,3\n")
    script.write_text("FILES = " + repr(decoys))
    add_outcome(database, 1, evidence_refs=[str(script)])
    recorded = {"evidence_id": "E-2-synthetic", "summary": "The second result"}
    (second_workspace / "evidence_manifest.json").write_text(json.dumps({"records": [recorded]}))
    add_outcome(database, 2, evidence_refs=[recorded["evidence_id"]])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    assert recorded["evidence_id"] in snapshot["source_index"]
    assert any(row["source_ref"] == recorded["evidence_id"] for row in snapshot["evidence_records"])


def test_identical_figures_have_unique_stable_source_ids(research):
    root, plan, database = research
    for name in ("first.png", "second.png"):
        (root / name).write_bytes(PNG)
    add_outcome(database, 1, evidence_refs=["first.png", "second.png"])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    figures = snapshot["figures"]
    assert len(figures) == 2 and figures[0]["id"] != figures[1]["id"]
    assert all(row["id"] in snapshot["source_index"] for row in figures)


def test_follows_existing_literal_file_references_without_scanning_directories(research):
    root, plan, database = research
    analysis = root / "analysis"
    analysis.mkdir()
    verifier = analysis / "verify.py"
    verifier.write_text("from pathlib import Path\nresult = Path(__file__).parent / 'result.json'\n")
    (analysis / "result.json").write_text('{"estimate":1.5}')
    (analysis / "unreferenced.json").write_text('{"must_stay_outside_snapshot":true}')
    add_outcome(database, 1, evidence_refs=["analysis/verify.py"])
    snapshot = build_snapshot(database, research_root=root, plan_path=plan)
    files = {Path(row["source_path"]).name for row in snapshot["documents"]}
    assert "result.json" in files
    assert "unreferenced.json" not in files
