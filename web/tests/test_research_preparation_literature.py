"""Preparation retrieval contract with mocked host transport; no network or LLM."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from theory_master.skills import developer_instructions, role_skills
from tm_observer import research_preparation_literature as literature


@pytest.fixture
def preparation(tmp_path):
    ident = "a" * 32
    root = tmp_path / "research-preparations" / ident
    root.mkdir(parents=True)
    (root / "metadata.json").write_text(json.dumps({
        "schema": "sciver/research-preparation/v1", "id": ident, "session_id": ident,
        "entry": "exploratory", "created_task": None, "creation": None,
    }))
    (root / "research_plan.md").write_text("A draft that retrieval must preserve.")
    return root


def host_transport(*, text_available=False, missing_text=False, empty=False):
    def retrieve(task, workspace, *, timeout_s):
        assert timeout_s == 120
        root = workspace / "literature/sdk_retrieval" / task["source_gap_id"]
        retrieval = root / "retrieval"
        retrieval.mkdir(parents=True)
        papers = [] if empty else [
            {"title": "Reference A", "doi": "10.1000/a", "abstract": "A bounded abstract", "source": "crossref"},
            {"title": "Reference B", "doi": "10.1000/b", "abstract": "", "source": "openalex"},
        ]
        (retrieval / "papers.json").write_text(json.dumps(papers))
        (retrieval / "evidence_index.md").write_text("# Retrieved metadata\nReference A\n")
        (retrieval / "papers_top.json").write_text(json.dumps(papers[:1]))
        result = {"status": "empty" if empty else "ready", "transport_ok": True,
                  "metadata_hit_count": len(papers), "papers_json": str(retrieval / "papers.json"),
                  "evidence_index_md": str(retrieval / "evidence_index.md"),
                  "papers_top_json": str(retrieval / "papers_top.json"), "reading": {}}
        if text_available:
            reading = root / "reading"
            reading.mkdir()
            (reading / "pdf_manifest.csv").write_text("paper,url,status,method,reason\nReference A,https://example.org/paper,read,html/text,\n")
            if not missing_text:
                (reading / "paper-01.md").write_text("# Reference A\n\nA materialized passage.\n")
            result["reading"] = {"pdf_manifest": str(reading / "pdf_manifest.csv")}
        (root / "transport.json").write_text(json.dumps(result))
        (retrieval / "search_log.md").write_text(json.dumps({"path": str(retrieval)}))
        return result
    return retrieve


def test_no_seed_landscape_publishes_sources_hashes_and_preserves_draft(preparation):
    original = (preparation / "research_plan.md").read_bytes()
    transport = Mock(side_effect=host_transport())
    result = literature.run_search(preparation, "An uncertain observed phenomenon", scope="Specific scientific conditions", retrieve=transport)
    assert result["status"] == "completed" and result["source_count"] == 2
    assert not result["model_invoked"] and not result["literature_agent_invoked"]
    task, staging = transport.call_args.args
    assert task["task_type"] == "landscape" and "source_lock" not in task
    assert staging.parent.name == ".literature-running" and not staging.exists()
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["claim_verification_performed"] is False
    assert manifest["scientific_evidence_created"] is False
    assert [row["reading_level"] for row in manifest["sources"]] == ["abstract_available", "metadata_available"]
    assert all(row["claim_verification"] == "not_performed" for row in manifest["sources"])
    assert Path(result["evidence_index"]).is_file() and Path(result["papers_top"]).is_file()
    assert str(staging) not in Path(result["manifest"]).read_text()
    for artifact in manifest["artifacts"]:
        file = preparation / artifact["path"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == artifact["sha256"]
        if file.name in {"transport.json", "search_log.md"}:
            assert str(staging) not in file.read_text()
    assert (preparation / "research_plan.md").read_bytes() == original
    assert not (preparation / "theory-master.config.json").exists()
    assert not (preparation / "science_graph.sqlite3").exists()


@pytest.mark.parametrize("missing_text,expected", [(False, "text_materialized_not_reviewed"), (True, "abstract_available")])
def test_targeted_one_source_distinguishes_materialization_from_verification(preparation, missing_text, expected):
    transport = Mock(side_effect=host_transport(text_available=True, missing_text=missing_text))
    result = literature.run_search(preparation, "Check this exact claim", mode="targeted_source", doi="https://doi.org/10.1000/a", retrieve=transport)
    assert transport.call_args.args[0]["source_lock"] == {"doi": "10.1000/a", "title": "", "status": "locked"}
    manifest = json.loads(Path(result["manifest"]).read_text())
    source = manifest["sources"][0]
    assert source["reading_level"] == expected and source["claim_verification"] == "not_performed"
    assert manifest["literature_agent_invoked"] is False
    if not missing_text:
        assert Path(source["text_path"]).is_file()
    else:
        assert source["text_path"] == "" and source["materialization_status"] == "unverified"


@pytest.mark.parametrize("fields", [
    {"mode": "targeted_source"}, {"mode": "landscape", "doi": "10.1000/a"},
    {"mode": "unknown"}, {"query": " "}, {"scope": "x" * 4001},
    {"mode": "targeted_source", "doi": "not a DOI"},
])
def test_invalid_scope_or_unbounded_target_is_rejected_before_transport(preparation, fields):
    retrieve = Mock(side_effect=AssertionError("No transport allowed"))
    request = {"query": "Scoped question", **fields}
    with pytest.raises(literature.PreparationLiteratureError):
        literature.run_search(preparation, **request, retrieve=retrieve)
    retrieve.assert_not_called()


@pytest.mark.parametrize("state", [{"created_task": {"task_id": 1}}, {"creation": {"target_root": "reserved"}}, {"session_id": "b" * 32}, {"schema": "other"}])
def test_workspace_identity_or_creation_boundary_rejects_before_writing(preparation, state):
    marker = preparation / "metadata.json"
    marker.write_text(json.dumps({**json.loads(marker.read_text()), **state}))
    retrieve = Mock(side_effect=AssertionError("No transport allowed"))
    with pytest.raises(literature.PreparationLiteratureError):
        literature.run_search(preparation, "Question", retrieve=retrieve)
    retrieve.assert_not_called()
    assert not (preparation / ".literature-running").exists()


def test_symlink_workspace_and_outside_paths_are_rejected(preparation, tmp_path):
    linked = preparation.parent / ("b" * 32)
    linked.symlink_to(preparation, target_is_directory=True)
    with pytest.raises(literature.PreparationLiteratureError):
        literature.run_search(linked, "Question", retrieve=Mock())
    outside = tmp_path / "not-preparation"
    outside.mkdir()
    with pytest.raises(literature.PreparationLiteratureError):
        literature.run_search(outside, "Question", retrieve=Mock())


def test_creation_during_retrieval_cannot_publish_new_materials(preparation):
    def retrieve(task, workspace, **kwargs):
        result = host_transport()(task, workspace, **kwargs)
        marker = preparation / "metadata.json"
        marker.write_text(json.dumps({**json.loads(marker.read_text()), "creation": {"reviewed_revision": "frozen"}}))
        return result
    with pytest.raises(literature.PreparationLiteratureError, match="立项确认"):
        literature.run_search(preparation, "Question", retrieve=retrieve)
    assert list((preparation / "literature").iterdir()) == []
    assert len(list((preparation / ".literature-running").iterdir())) == 1


def test_empty_and_transport_failure_are_persisted_without_novelty_claim(preparation):
    empty = literature.run_search(preparation, "Question", retrieve=host_transport(empty=True))
    assert empty["status"] == "empty" and empty["source_count"] == 0
    assert "不证明新颖性" in empty["notice"]
    failed = literature.run_search(preparation, "Question", retrieve=Mock(side_effect=TimeoutError("Unavailable provider")))
    assert failed["status"] == "blocked" and Path(failed["manifest"]).is_file()
    assert "TimeoutError" in failed["error"] and not failed["model_invoked"]


def test_cli_uses_host_transport_and_prints_manifest_receipt(preparation):
    output = io.StringIO()
    with patch("theory_master.scholarly_transport.retrieve_scholarly", side_effect=host_transport()) as retrieve, redirect_stdout(output):
        code = literature.main(["--workspace", str(preparation), "--query", "A clear question", "--scope", "A domain"])
    assert code == 0 and retrieve.call_count == 1
    receipt = json.loads(output.getvalue())
    assert Path(receipt["manifest"]).is_file() and not receipt["literature_agent_invoked"]


def test_preparation_skill_is_real_whitelisted_content_without_literature_role_expansion():
    refs = {ref.directory: ref for ref in role_skills("research_chat")}
    skill = refs["research-preparation"]
    body = skill.skill_md.read_text(encoding="utf-8")
    assert "tm_observer.research_chat_literature" in developer_instructions("research_chat")
    assert "共享 Literature 服务" in body and "四个 H1" in body
    assert str(skill.skill_md) in developer_instructions("research_chat")
    assert "research-preparation" not in {ref.directory for ref in role_skills("literature")}


def test_preparation_joins_failed_read_by_doi_not_fallback_title(tmp_path):
    from tm_observer.research_preparation_literature import _sources
    index = tmp_path / "papers.json"
    index.write_text(json.dumps([{"title": "", "doi": "10.1234/abc", "source": "openalex"}]))
    manifest = tmp_path / "pdf_manifest.csv"
    manifest.write_text("paper,doi,status,reason\nPaper 1,10.1234/abc,conversion_disabled,disabled\n")
    sources = _sources(tmp_path, {"papers_json": str(index), "reading": {"pdf_manifest": str(manifest)}})
    assert sources[0]["materialization_status"] == "conversion_disabled"
    assert sources[0]["reading_level"] == "metadata_available"
