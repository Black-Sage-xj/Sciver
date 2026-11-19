"""Retrieval reuse binds a frozen request and original local bytes, offline."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.scholarly_request import (
    pin_scholarly_transport,
    scholarly_request_identity,
)
from theory_master.scholarly_transport import retrieve_scholarly
from theory_master.workflow_investigation import InvestigationMixin


def task(**extra):
    return {
        "gap_id": "gap-same",
        "source_gap_id": "gap-same",
        "task_type": "targeted_source",
        "domain_context": "polymer mechanics",
        "search_target": "Check original paper",
        "new_search_lead": "10.1000/old",
        "source_lock": {"doi": "10.1000/old"},
        **extra,
    }


def corpus(workspace, request=None):
    request = request or task()
    root = workspace / "literature" / "sdk_retrieval" / "old-corpus"
    root.mkdir(parents=True)
    papers = root / "papers.json"
    papers.write_text('[{"title":"Original","doi":"10.1000/old"}]')
    text = root / "paper.md"
    text.write_text("Original scientific source text")
    manifest = root / "pdf_manifest.csv"
    manifest.write_text(
        "doi,status,markdown_path\n10.1000/old,read," + str(text) + "\n"
    )
    transport = {
        "schema": "theory-master/scholarly-transport/v1",
        "status": "ready",
        "transport_ok": True,
        "source_gap_id": request["source_gap_id"],
        "task_type": request["task_type"],
        "query": "OLD DOI10.1000/old",
        "papers_json": str(papers),
        "output_dir": str(root),
        "reading": {"pdf_manifest": str(manifest)},
    }
    return pin_scholarly_transport(request, transport, workspace)


def reuse(workspace, transport, request):
    return InvestigationMixin._reusable_scholarly_transport(
        {"last_sdk_scholarly_retrieval": transport}, request, workspace=workspace
    )


class Owner(InvestigationMixin):
    def __init__(self, workspace):
        self.cycle_workspace = workspace
        self.spec = SimpleNamespace(research_root=None)
        self.calls = []
        self._literature_domain_context = lambda state: "polymer mechanics"
        self._emit = lambda *args, **kwargs: None
        self._persist = lambda *args, **kwargs: None

    def _call(self, state, role, context, **kwargs):
        self.calls.append(context)
        return {"evidence_records": []}


def test_new_lead_new_doi_is_queued_but_cannot_reuse_old_gap_corpus(tmp_path):
    owner = Owner(tmp_path)
    old = corpus(tmp_path)
    state = {
        "last_sdk_scholarly_retrieval": old,
        "literature_task_history": [{**task(), "status": "completed"}],
    }
    new = task(
        search_target="Check different paper",
        new_search_lead="10.2000/new",
        source_lock={"doi": "10.2000/new"},
    )
    assert owner._queue_literature_task(state, new)[0]
    assert reuse(tmp_path, old, state["pending_literature_task"]) is None


@pytest.mark.parametrize(
    "change",
    [
        {"task_type": "targeted_source"},
        {"search_target": "Different scientific target"},
        {"new_search_lead": "New author and theorem"},
        {"domain_context": "Another domain"},
        {"source_lock": {"doi": "10.2000/new"}},
        {"search_queries": ["new query"]},
        {"relevance_terms": ["different anchor"]},
        {"max_candidates": 2},
    ],
)
def test_request_semantics_are_frozen_for_reuse(tmp_path, change):
    original = task(task_type="landscape", source_lock={})
    saved = corpus(tmp_path, original)
    assert reuse(tmp_path, saved, original) is not None
    assert reuse(tmp_path, saved, {**original, **change}) is None


@pytest.mark.parametrize("filename", ["papers.json", "paper.md", "pdf_manifest.csv"])
def test_changed_source_bytes_prevent_reuse_even_when_request_matches(
    tmp_path, filename
):
    saved = corpus(tmp_path)
    assert reuse(tmp_path, saved, task()) is not None
    path = Path(saved["output_dir"]) / filename
    path.write_text(path.read_text() + " altered")
    assert reuse(tmp_path, saved, task()) is None


def test_legacy_transport_without_identity_or_hashes_is_not_verified_reuse(tmp_path):
    saved = corpus(tmp_path)
    saved.pop("request_identity")
    saved.pop("artifact_snapshot")
    assert reuse(tmp_path, saved, task()) is None


def test_navigation_cannot_be_repointed_to_a_different_attested_file(tmp_path):
    saved = corpus(tmp_path)
    saved["papers_json"] = str(Path(saved["output_dir"]) / "paper.md")
    assert reuse(tmp_path, saved, task()) is None


def test_queue_retains_query_plan_and_ranking_anchors(tmp_path):
    owner = Owner(tmp_path)
    request = task(
        search_queries=[
            {"query": "Exact scientific query", "purpose": "disambiguation"}
        ],
        relevance_terms=["polymer topology"],
        max_candidates=7,
    )
    state = {}
    assert owner._queue_literature_task(state, request)[0]
    pending = state["pending_literature_task"]
    assert pending["search_queries"] == request["search_queries"]
    assert pending["relevance_terms"] == request["relevance_terms"]
    assert pending["max_candidates"] == 7


def test_completed_gap_without_new_lead_still_exhausts_without_retrieval(tmp_path):
    owner = Owner(tmp_path)
    state = {"literature_task_history": [{**task(), "status": "completed"}]}
    queued, reason = owner._queue_literature_task(state, task(new_search_lead=""))
    assert not queued and reason == "duplicate_external_gap_without_new_search_lead"
    assert state["pending_literature_task"] is None


def receipt_runner(calls):
    def runner(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if Path(command[1]).name == "lit_retrieve.py":
            query = command[command.index("--query") + 1]
            (output / "papers.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Original paper",
                            "doi": query,
                            "year": 2024,
                            "authors": ["Alice Original"],
                            "source": "openalex",
                            "abstract": "Actual provider source " + query,
                        }
                    ]
                )
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return runner


def test_same_gap_different_requests_keep_both_original_corpora(tmp_path):
    calls = []
    old = retrieve_scholarly(task(), tmp_path, runner=receipt_runner(calls))
    original_path = Path(old["papers_json"])
    original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
    new = retrieve_scholarly(
        task(new_search_lead="10.2000/new", source_lock={"doi": "10.2000/new"}),
        tmp_path,
        runner=receipt_runner(calls),
    )
    assert old["output_dir"] != new["output_dir"]
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash
    assert json.loads(original_path.read_text())[0]["doi"] == "10.1000/old"
    assert json.loads(Path(new["papers_json"]).read_text())[0]["doi"] == "10.2000/new"
    assert old["request_identity"] != new["request_identity"]
    assert old["request_identity"]["sha256"] in Path(old["output_dir"]).parts


def test_even_explicit_same_request_acquisition_does_not_overwrite_old_files(tmp_path):
    calls = []
    old = retrieve_scholarly(task(), tmp_path, runner=receipt_runner(calls))
    original = Path(old["papers_json"]).read_bytes()
    again = retrieve_scholarly(task(), tmp_path, runner=receipt_runner(calls))
    assert old["output_dir"] != again["output_dir"]
    assert old["request_identity"] == again["request_identity"]
    assert Path(old["papers_json"]).read_bytes() == original


def test_normalized_query_whitespace_and_mapping_order_have_stable_identity():
    left = task(
        task_type="landscape", source_lock={}, search_queries=["  polymer   topology "]
    )
    right = dict(
        reversed(
            list(
                task(
                    task_type="landscape",
                    source_lock={},
                    search_queries=["polymer topology"],
                ).items()
            )
        )
    )
    assert scholarly_request_identity(left) == scholarly_request_identity(right)


def test_interrupted_same_request_reuses_pinned_acquisition_without_new_retrieval(
    tmp_path,
):
    owner = Owner(tmp_path)
    retrieval_calls = []
    commands = []

    def retrieve(request, workspace):
        retrieval_calls.append(copy.deepcopy(request))
        return retrieve_scholarly(request, workspace, runner=receipt_runner(commands))

    owner.scholarly_retriever = retrieve
    state = {}
    assert owner._queue_literature_task(state, task())[0]
    request = state["pending_literature_task"]

    def interrupted(*args, **kwargs):
        raise RuntimeError("Interrupted before committed Literature output")

    original_call = owner._call
    owner._call = interrupted
    with pytest.raises(RuntimeError, match="Interrupted"):
        owner._execute_lead_literature_task(
            state, request, lead_stage="frontier_proposal"
        )
    original_snapshot = copy.deepcopy(state["last_sdk_scholarly_retrieval"])
    owner._call = original_call
    owner._execute_lead_literature_task(state, request, lead_stage="frontier_proposal")
    assert len(retrieval_calls) == 1
    assert state["last_sdk_scholarly_retrieval"] == original_snapshot
    assert state["pending_literature_task"] is None
    assert state["literature_task_history"][-1]["status"] == "completed"


def test_authorized_new_source_reaches_literature_with_its_own_corpus(tmp_path):
    owner = Owner(tmp_path)
    old = corpus(tmp_path)
    state = {
        "last_sdk_scholarly_retrieval": old,
        "literature_task_history": [{**task(), "status": "completed"}],
    }
    new = task(new_search_lead="10.2000/new", source_lock={"doi": "10.2000/new"})
    assert owner._queue_literature_task(state, new)[0]
    commands = []
    owner.scholarly_retriever = lambda request, workspace: retrieve_scholarly(
        request, workspace, runner=receipt_runner(commands)
    )
    owner._execute_lead_literature_task(
        state, state["pending_literature_task"], lead_stage="frontier_proposal"
    )
    delivered = owner.calls[-1]["sdk_scholarly_retrieval"]
    assert delivered["source_lock"]["doi"] == "10.2000/new"
    assert (
        json.loads(Path(delivered["papers_json"]).read_text())[0]["doi"]
        == "10.2000/new"
    )
    assert Path(old["papers_json"]).read_text().find("10.1000/old") >= 0
    assert (
        len(
            [
                command
                for command in commands
                if Path(command[1]).name == "lit_retrieve.py"
            ]
        )
        == 1
    )


def test_queued_request_cannot_be_changed_after_its_fingerprint_is_frozen(tmp_path):
    owner = Owner(tmp_path)
    state = {}
    assert owner._queue_literature_task(state, task())[0]
    state["pending_literature_task"]["source_lock"]["doi"] = "10.2000/changed"
    assert owner._validate_pending_literature_task(state) == (
        False,
        "literature_request_identity_changed",
    )


@pytest.mark.parametrize("operation", ["delete", "add", "symlink"])
def test_inventory_change_or_symlink_prevents_reuse(tmp_path, operation):
    saved = corpus(tmp_path)
    root = Path(saved["output_dir"])
    if operation == "delete":
        (root / "paper.md").unlink()
    elif operation == "add":
        (root / "new.md").write_text("New unattested source")
    else:
        (root / "paper.md").unlink()
        outside = tmp_path / "outside.md"
        outside.write_text("Outside source")
        (root / "paper.md").symlink_to(outside)
    assert reuse(tmp_path, saved, task()) is None


def test_missing_optional_navigation_does_not_repeat_the_same_acquisition(tmp_path):
    saved = corpus(tmp_path)
    saved["reading"]["reading_index"] = str(
        Path(saved["output_dir"]) / "unavailable-index.md"
    )
    saved = pin_scholarly_transport(task(), saved, tmp_path)
    assert reuse(tmp_path, saved, task()) is not None


def test_same_request_fake_transport_is_bound_but_legacy_stub_is_not(tmp_path):
    from theory_master.scholarly_transport import fake_scholarly_retrieval

    saved = fake_scholarly_retrieval(task(), tmp_path)
    assert reuse(tmp_path, saved, task()) is not None
    assert reuse(tmp_path, saved, task(new_search_lead="different lead")) is None
    saved.pop("request_identity")
    saved.pop("artifact_snapshot")
    assert reuse(tmp_path, saved, task()) is None


def test_arxiv_only_source_lock_overrides_broad_planned_queries(tmp_path):
    calls = []
    identifier = "arXiv:2001.00001v2"
    request = task(
        source_lock={"arxiv_id": identifier},
        search_queries=["unrelated broad planned query"],
    )

    def runner(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if Path(command[1]).name == "lit_retrieve.py":
            assert command[command.index("--query") + 1] == identifier
            (output / "papers.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Original paper",
                            "arxiv_id": "2001.00001",
                            "year": 2024,
                            "authors": ["Alice Original"],
                            "source": "arxiv",
                        }
                    ]
                )
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = retrieve_scholarly(request, tmp_path, runner=runner)
    assert result["query_variants"] == [identifier]
    assert result["request_identity"]["request"]["search_queries"] == [identifier]
    rows = json.loads(Path(result["papers_json"]).read_text())
    assert len(rows) == 1 and rows[0]["identity_resolution"] == "exact_arxiv"
