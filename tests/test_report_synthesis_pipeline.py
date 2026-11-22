"""Publication isolation, independent contexts and bounded backend behavior."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import xml.etree.ElementTree as ET

import pytest

from theory_master.report_synthesis.pipeline import generate_report
from theory_master.report_synthesis.codex import CodexFinalReportBackend
from theory_master.report_synthesis.prompts import REVIEW_CRITERIA
from theory_master.report_synthesis.render import lineage_svg, render_report
from theory_master.report_synthesis.snapshot import build_snapshot
from report_synthesis_fixtures import SQLITE_SCHEMA


def fixture_snapshot(root):
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text("# Question\nCompare two toy settings.")
    database = root / "science_graph.sqlite3"
    outcome = {"id": "O-1", "hypothesis_id": "H-0001", "cycle_id": "1", "verdict": "supported", "summary": "Toy settings differ", "evidence_refs": []}
    with sqlite3.connect(database) as db:
        db.executescript(SQLITE_SCHEMA)
        db.execute("INSERT INTO hypotheses(id,title,summary,created_at) VALUES ('H-0001','Toy comparison','Toy settings differ','now')")
        db.execute("INSERT INTO cycle_evaluations VALUES (?,?,?,?,?,?)", ("O-1", "H-0001", "1", "supported", json.dumps(outcome), "now"))
        db.execute("INSERT INTO task_goal_terminal VALUES (?,?,?,?,?,?,?)", ("goal", "passed", "1", "now", hashlib.sha256(plan.read_bytes()).hexdigest(), None, "{}"))
    return build_snapshot(database, research_root=root, plan_path=plan), database


def report_value():
    return {"schema_version": "2", "title": "Toy report", "abstract": "Recorded settings differ.",
        "markdown": "# Toy report\n\n## Answer\nRecorded settings differ. [[O-1]]",
        "claims": [{"id": "C-1", "text": "Recorded settings differ.", "kind": "answer", "hypothesis_ids": ["H-0001"], "outcome_ids": ["O-1"], "source_refs": ["O-1"], "scope": "Two toy settings"}],
        "selected_hypothesis_ids": ["H-0001"], "figures": [],
        "requirements": [{"requirement_id": "Q-1", "question": "Compare settings", "answer": "They differ", "claim_ids": ["C-1"], "status": "answered"}],
        "research_path": [{"hypothesis_id": "H-0001", "role": "main", "explanation": "Answers question", "outcome_ids": ["O-1"], "source_refs": ["O-1"]}],
        "research_story": [{"title": "Compare toy settings", "action": "Compare the recorded settings",
            "finding": "Recorded settings differ", "transition": "Use this comparison to answer the original question",
            "hypothesis_ids": ["H-0001"], "outcome_ids": ["O-1"], "source_refs": ["O-1"]}], "limitations": []}


def review_value(verdict="pass"):
    return {"schema_version": "2", "verdict": verdict, "summary": "Checked toy sources",
        "dimensions": [{"criterion": key, "status": "pass", "reason": "Compared recorded toy evidence", "source_refs": ["O-1"]} for key in REVIEW_CRITERIA],
        "issues": []}


def test_report_publication_preserves_science_and_separates_writer_review(tmp_path):
    snapshot, database = fixture_snapshot(tmp_path / "research")
    before = database.read_bytes()
    calls = []
    def backend(role, prompt, payload, schema):
        calls.append((role, prompt, payload))
        return report_value() if role == "writer" else review_value()
    out = tmp_path / "publication"
    result = generate_report(snapshot, out, backend)
    assert result["state"] == "ready", result
    assert [row[0] for row in calls] == ["writer", "reviewer"]
    assert calls[0][1] != calls[1][1]
    assert "conversation" not in calls[1][2] and calls[1][2]["report"] == report_value()
    assert calls[1][2]["derived_visuals"][0]["kind"] == "research_story"
    assert database.read_bytes() == before
    assert Path(result["html_path"]).is_file() and (out / "research-story.svg").is_file()
    assert generate_report(snapshot, out, backend) == result
    assert len(calls) == 2


def test_reviewer_blocker_never_publishes_ready(tmp_path):
    snapshot, _ = fixture_snapshot(tmp_path / "research")
    review = review_value("revise")
    review["dimensions"][0]["status"] = "fail"
    review["issues"] = [{"severity": "major", "criterion": "answer_coverage", "report_location": "markdown#Answer", "problem": "Toy question not fully covered", "source_refs": ["research_plan"], "system_prompt_recommendation": "Map every explicit requirement to a sourced answer"}]
    result = generate_report(snapshot, tmp_path / "publication", lambda role, *_: report_value() if role == "writer" else review)
    assert result["state"] == "needs_revision"
    assert json.loads(Path(result["review_path"]).read_text())["issues"]


def test_legacy_writer_cannot_publish_a_new_report_without_the_active_story_contract(tmp_path):
    snapshot, _ = fixture_snapshot(tmp_path / "research")
    legacy = report_value()
    legacy["schema_version"] = "1"
    legacy.pop("research_story")
    result = generate_report(snapshot, tmp_path / "publication", lambda role, *_: legacy if role == "writer" else review_value())
    assert result["state"] == "needs_revision"
    assert any("new reports must use active schema 2" in error for error in result["validation_errors"])


def test_model_failure_is_recorded_without_an_automatic_retry(tmp_path):
    snapshot, _ = fixture_snapshot(tmp_path / "research")
    calls = []
    def fail(*args):
        calls.append(args[0])
        raise RuntimeError("synthetic provider failure")
    result = generate_report(snapshot, tmp_path / "publication", fail)
    assert result["state"] == "failed" and calls == ["writer"]
    assert not (tmp_path / "publication/.generating").exists()


def test_codex_calls_are_fresh_bounded_and_keep_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "config"))
    observed = []
    def run(command, **kwargs):
        observed.append((command, kwargs))
        Path(command[command.index("-o") + 1]).write_text('{"value":"synthetic"}')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    backend = CodexFinalReportBackend(work_dir=tmp_path / "runs",
                                      writer_feedback={"issues": ["Explain the baseline"]})
    schema = {"type": "object"}
    assert backend("writer", "write", {"snapshot": {}}, schema) == {"value": "synthetic"}
    backend("reviewer", "review independently", {"snapshot": {}}, schema)
    with pytest.raises(ValueError, match="budget"):
        backend("writer", "retry", {}, schema)
    for command, _ in observed:
        assert "resume" not in command and "--ephemeral" in command and "--model" not in command
        assert "shell_tool" in command and "web_search=\"disabled\"" in command
    assert observed[0][0][observed[0][0].index("-C") + 1] != observed[1][0][observed[1][0].index("-C") + 1]
    assert "revision_feedback" in json.loads((tmp_path / "runs/01-writer/input.json").read_text())
    assert "revision_feedback" not in json.loads((tmp_path / "runs/02-reviewer/input.json").read_text())
    assert (tmp_path / "runs/02-reviewer/invocation.json").is_file()


def test_lineage_compatibility_visual_uses_narrative_arrows_without_claiming_graph_edges():
    snapshot = {"hypotheses": [{"id": "H-0001", "title": "First"}, {"id": "H-0002", "title": "Second"}], "lineage": []}
    report = {"selected_hypothesis_ids": ["H-0001", "H-0002"], "research_path": [
        {"hypothesis_id": "H-0001", "role": "main", "explanation": "First recorded step"},
        {"hypothesis_id": "H-0002", "role": "main", "explanation": "Second recorded step"}]}
    graph = lineage_svg(report, snapshot)
    assert 'data-kind="narrative-step"' in graph and "箭头表示叙述推进" in graph
    snapshot["lineage"] = [{"source_hypothesis": "H-0001", "target_hypothesis": "H-0002", "role": "main"}]
    assert lineage_svg(report, snapshot) == graph
    assert lineage_svg({"selected_hypothesis_ids": ["H-0001", "H-0002"]}, snapshot) == ""


def test_lineage_compatibility_visual_preserves_author_explanation_instead_of_verdict_badges():
    graph = lineage_svg({"selected_hypothesis_ids": ["H-0001"], "research_path": [
        {"hypothesis_id": "H-0001", "role": "turning_point", "explanation": "The rejected test motivates a different comparison."}]}, {
        "hypotheses": [{"id": "H-0001", "whole_h_assessment": {"verdict": "open", "summary": ""}}],
        "outcomes": [{"hypothesis_id": "H-0001", "cycle_id": "1", "verdict": "rejected"}], "lineage": []})
    text = "".join(item.text or "" for item in ET.fromstring(graph).findall(".//{*}text"))
    assert "The rejected test motivates a different comparison." in text
    assert "整体：" not in graph and "逐轮：" not in graph


def test_provider_schema_subset_and_role_routing_do_not_leak_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "config"))
    observed = []
    def run(command, **kwargs):
        observed.append((command, kwargs))
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert "uniqueItems" not in schema["properties"]["refs"]
        Path(command[command.index("-o") + 1]).write_text('{}')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    backend = CodexFinalReportBackend(work_dir=tmp_path / "runs",
        model_by_role={"writer": "toy-writer", "reviewer": "toy-reviewer"},
        reasoning_effort_by_role={"writer": "low", "reviewer": "high"},
        config_overrides_by_role={"writer": {"model_provider": "toy_provider"}},
        env_by_role={"writer": {"TOY_SECRET": "synthetic-secret-value"}})
    schema = {"type": "object", "properties": {"refs": {"type": "array", "uniqueItems": True}}}
    backend("writer", "write", {}, schema)
    backend("reviewer", "review", {}, schema)
    assert observed[0][1]["env"]["TOY_SECRET"] == "synthetic-secret-value"
    assert observed[0][0][observed[0][0].index("--model") + 1] == "toy-writer"
    assert observed[1][0][observed[1][0].index("--model") + 1] == "toy-reviewer"
    assert "synthetic-secret-value" not in str(observed[0][0])
    for path in (tmp_path / "runs").rglob("*"):
        if path.is_file():
            assert "synthetic-secret-value" not in path.read_text()
