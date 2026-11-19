"""Web projection validates new delivery seals without writing job state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever, request
from theory_master.literature_service import run_literature
from tm_observer.research_chat_literature import _projection


@pytest.mark.parametrize("target", ["body", "output", "report"])
def test_broken_sealed_delivery_is_projected_incomplete_without_mutation(
    tmp_path, target
):
    ident = "a" * 32
    root = tmp_path / ident
    result = run_literature(
        root, request(), backend=LiteratureBackend(), retriever=Retriever()
    )
    assert result["status"] == "completed"
    state = json.loads((root / "manifest.json").read_text())
    entry = {
        "job_id": ident,
        "request": state["request"],
        "created_at": state["created_at"],
    }
    binding = {"workspace_root": str(tmp_path)}
    assert _projection(entry, binding, "b" * 32)["status"] == "completed"
    path = {
        "body": next(row["text_path"] for row in state["sources"] if row["text_path"]),
        "output": state["invocations"][-1]["output_path"],
        "report": root / "report.md",
    }[target]
    Path(path).unlink()
    before = {
        str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    projected = _projection(entry, binding, "b" * 32, include_details=True)
    assert (
        projected["status"] == "incomplete"
        and projected["stop_reason"] == "delivery_integrity_failed"
    )
    assert projected["error"] and not projected["report_available"]
    assert before == {
        str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_legacy_projection_rechecks_surviving_material_without_writing(tmp_path):
    ident, backend = "c" * 32, LiteratureBackend()
    root = tmp_path / ident
    run_literature(root, request(), backend=backend, retriever=Retriever())
    state = json.loads((root / "manifest.json").read_text())
    state.pop("delivery_integrity")
    (root / "manifest.json").write_text(json.dumps(state))
    entry = {
        "job_id": ident,
        "request": state["request"],
        "created_at": state["created_at"],
    }
    binding = {"workspace_root": str(tmp_path)}
    assert (
        _projection(entry, binding, "d" * 32)["delivery_verification"]
        == "legacy_unsealed"
    )
    Path(state["invocations"][-1]["output_path"]).unlink()
    before = (root / "manifest.json").read_bytes()
    projected = _projection(entry, binding, "d" * 32)
    assert projected["status"] == "incomplete" and not projected["report_available"]
    assert (root / "manifest.json").read_bytes() == before and len(backend.calls) == 4


def test_report_endpoint_refuses_sealed_report_drift_without_mutation(
    tmp_path, monkeypatch
):
    from tm_observer import (
        research_chat as chat,
        research_chat_literature as literature,
    )

    state, search = tmp_path / "state", tmp_path / "workspace"
    search.mkdir()
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    ident = chat.create(state, {"runs": []}, {"mode": "preparation", "entry": "clear"})[
        "session_id"
    ]
    result = literature.run_study(
        state,
        search,
        ident,
        request(),
        backend=LiteratureBackend(),
        retriever=Retriever(),
    )
    report = Path(result["report"])
    report.write_text("tampered delivery")
    manifest = Path(result["manifest"])
    before = manifest.read_bytes()
    with pytest.raises(literature.ChatLiteratureError, match="缺损"):
        literature.read_report(state, ident, result["job_id"])
    assert manifest.read_bytes() == before and report.read_text() == "tampered delivery"
