"""Returned delivery remains recoverable when later semantic registration fails."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from theory_master.native_delivery_journal import record_native_delivery


def delivery(tmp_path, *, native=None):
    handoff = tmp_path / "workspace" / ".tm-handoff" / "inv-test.json"
    handoff.parent.mkdir(parents=True)
    handoff.write_text('{"findings": [{"summary": "original finding"}]}')
    owner = SimpleNamespace(
        cycle_dir=tmp_path,
        backend=SimpleNamespace(last_native_goal=native or {}),
        _persist=Mock(),
    )
    return SimpleNamespace(
        owner=owner, invocation_id="inv-test", identifier="thread-test",
        logical_key="original-slot", role="investigator", stage="investigate",
        contract=SimpleNamespace(descriptor=lambda: {"contract_id": "investigator", "version": 1}),
        handoff_path=handoff, raw=handoff.read_text(), native_objective="frozen objective",
        limit_binding={"frozen_objective_sha256": "original-science"}, state={},
    )


def test_returned_delivery_is_immutable_and_persisted_before_semantic_failure(tmp_path):
    session = delivery(tmp_path, native={"status": "complete", "thread_id": "thread-test"})
    path = record_native_delivery(session)
    before = path.read_bytes()
    payload = json.loads(before)
    assert payload["authority"] == "sdk_return"
    assert payload["native_goal"]["status"] == "complete"
    assert payload["raw_sha256"] == hashlib.sha256(session.raw.encode()).hexdigest()
    assert payload["handoff_sha256"] == hashlib.sha256(session.handoff_path.read_bytes()).hexdigest()
    assert session.state["native_goal"]["delivery_receipt"] == str(path)
    session.owner._persist.assert_called_once_with(session.state)
    record_native_delivery(session)
    assert path.read_bytes() == before
    session.raw = '{"findings": []}'
    with pytest.raises(ValueError, match="conflicts"):
        record_native_delivery(session)
    assert path.read_bytes() == before


def test_return_does_not_invent_missing_native_terminal_metadata(tmp_path):
    session = delivery(tmp_path)
    payload = json.loads(record_native_delivery(session).read_text())
    assert payload["native_goal"] == {}
    assert "status" not in session.state["native_goal"]


def test_semantic_failure_keeps_the_previously_persisted_return(tmp_path):
    session = delivery(tmp_path, native={"status": "complete"})
    session.owner._persist.side_effect = OSError("checkpoint failure")
    with pytest.raises(OSError, match="checkpoint failure"):
        record_native_delivery(session)
    path = tmp_path / "native_deliveries" / "inv-test.json"
    assert json.loads(path.read_text())["raw"] == session.raw


def test_unsafe_handoff_cannot_create_a_return_receipt(tmp_path):
    session = delivery(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(session.raw)
    session.handoff_path.unlink()
    session.handoff_path.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe"):
        record_native_delivery(session)
    assert not (tmp_path / "native_deliveries" / "inv-test.json").exists()
