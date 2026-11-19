"""Real fake-backed Literature receipts must survive bounded read-only replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

import pytest

from theory_master.literature_delivery_integrity import (
    LiteratureDeliveryIntegrityError,
    verify_completed_delivery,
)
from theory_master.literature_service import run_literature
from tests.test_literature_service import LiteratureBackend, Retriever, request


def complete(tmp_path):
    root, backend, retrieve = tmp_path / "job", LiteratureBackend(), Retriever()
    result = run_literature(root, request(), backend=backend, retriever=retrieve)
    assert result["status"] == "completed", result
    return root, backend, retrieve, json.loads((root / "manifest.json").read_text())


def hashes(root):
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "target",
    [
        "raw",
        "agent",
        "output",
        "metadata",
        "reading",
        "body",
        "report",
        "state_output",
        "request",
        "route",
    ],
)
def test_completed_replay_detects_missing_or_drifted_authority_without_model(
    tmp_path, target
):
    root, backend, retrieve, state = complete(tmp_path)
    invocation = state["invocations"][-1]
    paths = {
        "raw": invocation["raw_output_path"],
        "agent": str(Path(invocation["output_path"]).parent / "agent_output.json"),
        "output": invocation["output_path"],
        "metadata": state["transports"][0]["transport"]["papers_json"],
        "reading": state["transports"][1]["transport"]["reading"]["pdf_manifest"],
        "body": next(row["text_path"] for row in state["sources"] if row["text_path"]),
        "report": root / "report.md",
    }
    if target in paths:
        path = Path(paths[target])
        path.write_bytes(path.read_bytes() + b"\nchanged")
    elif target == "state_output":
        state["output"]["remaining_scientific_question"] = "Changed question"
    elif target == "request":
        state["request"]["query"] = "Entirely different assigned question"
    elif target == "route":
        state["route"]["model"] = "different-model"
    (root / "manifest.json").write_text(json.dumps(state))
    before_calls = len(backend.calls), len(retrieve.calls)
    with pytest.raises(LiteratureDeliveryIntegrityError):
        verify_completed_delivery(root, state)
    if target != "route":
        result = run_literature(
            root, state["request"], backend=backend, retriever=retrieve
        )
        assert (
            result["status"] == "failed"
            and result["stop_reason"] == "delivery_integrity_failed"
        )
    assert (len(backend.calls), len(retrieve.calls)) == before_calls


def test_deleted_output_and_fulltext_cannot_stay_completed(tmp_path):
    root, backend, retrieve, state = complete(tmp_path)
    Path(state["invocations"][-1]["output_path"]).unlink()
    Path(
        next(row["text_path"] for row in state["sources"] if row["text_path"])
    ).unlink()
    result = run_literature(root, request(), backend=backend, retriever=retrieve)
    assert result["status"] == "failed" and "absent" in result["error"]
    assert len(backend.calls) == 4 and len(retrieve.calls) == 3


def test_failed_integrity_status_does_not_bypass_the_seal_on_later_reentry(tmp_path):
    root, backend, retrieve, _ = complete(tmp_path)
    (root / "report.md").write_text("changed original report")
    for _ in range(3):
        result = run_literature(root, request(), backend=backend, retriever=retrieve)
        assert (
            result["status"] == "failed"
            and result["stop_reason"] == "delivery_integrity_failed"
        )
    assert (root / "report.md").read_text() == "changed original report"
    assert len(backend.calls) == 4 and len(retrieve.calls) == 3


def test_healthy_seal_is_read_only_and_binds_final_synthesis(tmp_path):
    root, backend, retrieve, state = complete(tmp_path)
    before = hashes(root)
    assert verify_completed_delivery(root, state) == {"status": "sealed"}
    result = run_literature(root, request(), backend=backend, retriever=retrieve)
    assert (
        result["status"] == "completed" and result["delivery_verification"] == "sealed"
    )
    assert hashes(root) == before and len(backend.calls) == 4
    assert (
        json.loads(Path(state["invocations"][-1]["output_path"]).read_text())
        == state["output"]
    )


def test_legacy_job_rechecks_existing_contracts_without_inventing_a_seal(tmp_path):
    root, backend, retrieve, state = complete(tmp_path)
    state.pop("delivery_integrity")
    for invocation in state["invocations"]:
        invocation.pop("delivery_integrity")
        invocation.pop("context_path")  # historical manifests inferred this sibling
    for entry in state["transports"]:
        entry.pop("delivery_integrity")
    (root / "manifest.json").write_text(json.dumps(state))
    before = hashes(root)
    assert verify_completed_delivery(root, state) == {"status": "legacy_unsealed"}
    result = run_literature(root, request(), backend=backend, retriever=retrieve)
    assert result["delivery_verification"] == "legacy_unsealed"
    assert hashes(root) == before and "delivery_integrity" not in state
    agent = Path(state["invocations"][0]["output_path"]).parent / "agent_output.json"
    agent.write_text('{"invalid_contract":true}')
    with pytest.raises(LiteratureDeliveryIntegrityError):
        verify_completed_delivery(root, state)


@pytest.mark.parametrize("value", [[], None])
def test_legacy_legal_json_with_invalid_delivery_shape_fails_cleanly(tmp_path, value):
    root, backend, retrieve, state = complete(tmp_path)
    state.pop("delivery_integrity")
    for invocation in state["invocations"]:
        invocation.pop("delivery_integrity")
    path = Path(state["invocations"][0]["output_path"]).parent / "agent_output.json"
    path.write_text(json.dumps(value))
    (root / "manifest.json").write_text(json.dumps(state))
    with pytest.raises(LiteratureDeliveryIntegrityError):
        verify_completed_delivery(root, state)
    result = run_literature(root, request(), backend=backend, retriever=retrieve)
    assert (
        result["status"] == "failed"
        and result["stop_reason"] == "delivery_integrity_failed"
    )


@pytest.mark.parametrize("tamper", [None, "authored", "wrong_source"])
def test_legacy_early_arxiv_navigation_survives_later_doi_enrichment(tmp_path, tamper):
    root, _, _, state = complete(tmp_path)
    state.pop("delivery_integrity")
    for invocation in state["invocations"]:
        invocation.pop("delivery_integrity")
    for entry in state["transports"]:
        entry.pop("delivery_integrity")
    corpus = root / "corpus/retrieval/papers.json"
    papers = json.loads(corpus.read_text())
    papers[0]["arxiv_id"] = "2401.12345"
    corpus.write_text(json.dumps(papers))
    early = state["invocations"][0]
    for path in (
        Path(early["output_path"]),
        Path(early["output_path"]).parent / "agent_output.json",
    ):
        output = json.loads(path.read_text())
        row = output["landscape"]["major_routes"][0]["representative_papers"][0]
        # The original stage saw an arXiv record. Its shared context corpus now
        # also knows the DOI, so replaying closure would otherwise rewrite it.
        row["doi_or_url"] = "https://arxiv.org/abs/2401.12345"
        path.write_text(json.dumps(output))
    if tamper:
        agent_path = Path(early["output_path"]).parent / "agent_output.json"
        agent = json.loads(agent_path.read_text())
        route = agent["landscape"]["major_routes"][0]
        if tamper == "authored":
            route["focus"] = "A different scientific interpretation"
        else:
            row = route["representative_papers"][0]
            row["paper_title"] = papers[1]["title"]
            row["doi_or_url"] = papers[1]["doi"]
        agent_path.write_text(json.dumps(agent))
    (root / "manifest.json").write_text(json.dumps(state))
    before = hashes(root)
    if tamper:
        with pytest.raises(LiteratureDeliveryIntegrityError):
            verify_completed_delivery(root, state)
    else:
        assert verify_completed_delivery(root, state) == {"status": "legacy_unsealed"}
    assert hashes(root) == before


@pytest.mark.parametrize("target", ["output", "metadata", "body"])
def test_midjob_replay_checks_original_material_before_catalog_or_model(
    tmp_path, target
):
    cancel, backend, retrieve = threading.Event(), LiteratureBackend(), Retriever()
    root = tmp_path / "job"

    def stop(event):
        if (
            event["event"] == "literature.agent_completed"
            and event["stage"] == "targeted_source"
        ):
            cancel.set()

    paused = run_literature(
        root,
        request(),
        backend=backend,
        retriever=retrieve,
        cancel_event=cancel,
        on_event=stop,
    )
    assert paused["status"] == "paused"
    state = json.loads((root / "manifest.json").read_text())
    path = {
        "output": state["invocations"][0]["output_path"],
        "metadata": state["transports"][0]["transport"]["papers_json"],
        "body": next(row["text_path"] for row in state["sources"] if row["text_path"]),
    }[target]
    Path(path).write_text("changed")
    calls = len(backend.calls), len(retrieve.calls)
    catalog = (root / "corpus/retrieval/papers.json").read_bytes()
    cancel.clear()
    result = run_literature(
        root, request(), backend=backend, retriever=retrieve, cancel_event=cancel
    )
    assert (
        result["status"] == "failed"
        and result["stop_reason"] == "delivery_integrity_failed"
    )
    assert calls == (len(backend.calls), len(retrieve.calls))
    assert (root / "corpus/retrieval/papers.json").read_bytes() == catalog


@pytest.mark.parametrize("directory", [False, True])
def test_sealed_files_and_parent_directories_cannot_be_replaced_by_symlinks(
    tmp_path, directory
):
    root, _, _, state = complete(tmp_path)
    output = Path(state["invocations"][-1]["output_path"])
    original = output.parent if directory else output
    moved = tmp_path / "moved"
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=directory)
    with pytest.raises(LiteratureDeliveryIntegrityError, match="unsafe"):
        verify_completed_delivery(root, state)


def test_declared_blocked_import_is_not_allowed_to_call_a_model(tmp_path):
    root, _, _, state = complete(tmp_path)
    home = tmp_path / "import"
    home.mkdir()
    source = home / "manifest.json"
    source.write_text(
        json.dumps(
            {
                "schema": "sciver/preparation-literature/v1",
                "status": "blocked",
                "transport": state["transports"][0]["transport"],
                "artifacts": [],
            }
        )
    )
    backend = LiteratureBackend()
    result = run_literature(
        tmp_path / "new",
        request(retrieval_manifests=[str(source)]),
        backend=backend,
        retriever=Retriever(),
    )
    assert result["status"] == "failed" and result["stop_reason"] == "transport_blocked"
    assert not backend.calls
