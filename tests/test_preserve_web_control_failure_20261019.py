"""SDK rejected request vs uncertain mutation, and a durable Web receipt."""
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.worker_runtime import PreservationPreflightRejected
from theory_master import operator_control
from tm_observer import run_control
from tm_observer.run_control import VerifiedPreflightRefusal, OperatorError


def _web_fixture(tmp_path):
    root = tmp_path / "research"
    (root / ".theory-master").mkdir(parents=True)
    cycle = root / "155"
    cycle.mkdir()
    run = {
        "observer_id": "a" * 16,
        "task_root": str(root),
        "research_root": str(root),
        "cycle_dir": str(cycle),
        "cycle": "155",
        "run_id": 1,
        "state": "NEEDS_ATTENTION",
    }
    payload = {
        "action": "worker_control",
        "value": {"worker_id": "worker-1", "operation": "preserve_release"},
        "expected_cycle": "155", "expected_status": "NEEDS_ATTENTION",
        "expected_launch_id": "", "request_id": "a" * 32,
    }
    return root, run, payload


def test_sdk_operator_preflight_reports_unmodified_state(monkeypatch, capsys):
    monkeypatch.setattr(operator_control, "dispatch", lambda _: (
        (_ for _ in ()).throw(PreservationPreflightRejected("frozen owner mismatched"))))
    with patch("sys.stdin", io.StringIO("{}")):
        code = operator_control.main()
    response = json.loads(capsys.readouterr().out)
    assert code == 1
    assert response == {
        "ok": False, "error": "preservation_preflight_rejected",
        "confirmed_no_effect": True,
    }


def test_web_bridge_typed_preflight_is_not_unknown(tmp_path):
    _, run, value = _web_fixture(tmp_path)
    rejected = json.dumps({"ok": False,
                           "error": "preservation_preflight_rejected",
                           "confirmed_no_effect": True})
    with patch.object(run_control.subprocess, "run",
                      return_value=SimpleNamespace(returncode=1, stdout=rejected)):
        with pytest.raises(VerifiedPreflightRefusal, match="已确认未修改"):
            run_control._sdk_apply(run, "worker_control", value["value"])


def test_web_bridge_uncertain_sdk_response_remains_unconfirmed(tmp_path):
    _, run, value = _web_fixture(tmp_path)
    rejected = json.dumps({"ok": False, "error": "ValueError"})
    with patch.object(run_control.subprocess, "run",
                      return_value=SimpleNamespace(returncode=1, stdout=rejected)):
        with pytest.raises(OperatorError, match="SDK 操作未确认完成") as exc:
            run_control._sdk_apply(run, "worker_control", value["value"])
        assert type(exc.value) is OperatorError


def test_known_preflight_refusal_is_recorded_in_request_receipt(tmp_path):
    root, run, payload = _web_fixture(tmp_path)
    fake_registered = [{
        "worker_id": "worker-1", "status": "NEEDS_ATTENTION",
        "current_cycle": "155",
    }]
    with patch("tm_observer.worker_console.registered_workers",
               return_value=fake_registered), patch.object(
               run_control, "_sdk_apply",
               side_effect=VerifiedPreflightRefusal("confirmed no effect", 409)) as sdk:
        with pytest.raises(VerifiedPreflightRefusal, match="confirmed no effect"):
            run_control.execute(run, payload)
        sdk.assert_called_once()
    receipt = root / ".theory-master/web-operator-receipts" / (payload["request_id"] + ".json")
    state = json.loads(receipt.read_text())
    assert state["phase"] == "rejected_no_effect"
    assert state["outcome"]["reason_code"] == "preservation_preflight_rejected"
    # A replay of the same ID never starts a second SDK request.
    with patch("tm_observer.worker_console.registered_workers",
               return_value=fake_registered), patch.object(
               run_control, "_sdk_apply") as sdk:
        with pytest.raises(OperatorError, match="先前保全请求已确认未执行"):
            run_control.execute(run, payload)
        sdk.assert_not_called()
