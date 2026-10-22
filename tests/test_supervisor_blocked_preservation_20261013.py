"""Supervisor alone owns Native Goal blocked routing (no separate Stop Reviewer)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master.internal_attention_review import review_attention_in_sdk
from theory_master.research_preservation import _check_preservation_authorization, PreservationGuard
from theory_master.supervisor_intervention import KEY, write_supervisor_setting


def _blocked(cycle_dir: Path, *, review_count: int = 0) -> dict:
    checkpoint = cycle_dir / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    return {
        "status": "needs_attention", "phase": "investigator:goal_suspended",
        "execution_active": False, "resumable": True,
        "attention": {"code": "investigator_native_goal_blocked", "goal_status": "blocked",
                      "reason": "All tried methods have insufficient discriminating power."},
        "hypothesis_lock": {"status": "locked", "id": "H-0056"},
        "last_safe_checkpoint": str(checkpoint),
        "supervisor_scientific_continue_count": review_count,
        "research_root": str(cycle_dir.parent),
    }


@pytest.mark.parametrize("action,guidance", [
    ("continue", "Try another prepared, root-coupled contrast under the same frozen H without changing the estimand."),
    ("preserve", ""),
])
def test_supervisor_blocked_routes_continue_or_preserve(tmp_path, action, guidance):
    cycle = tmp_path / "86"
    cycle.mkdir()
    with patch.dict("os.environ", {KEY: "1"}):
        backend = MagicMock()
        backend.snapshot_budget_review_thread = None
        backend.invoke_attention_review.return_value = (
            "thread1", json.dumps({
                "action": action,
                "reason": "This choice respects the existing scientific evidence and frozen test.",
                "guidance": guidance,
            }),
        )
        decision = review_attention_in_sdk(
            backend=backend, cycle_dir=cycle, state=_blocked(cycle),
            goal="g", research_contract="c", final_gate="f",
        )
    assert decision["action"] == action
    assert decision["session_id"].startswith("SUP-")
    assert backend.invoke_attention_review.call_count == 1
    receipt = json.loads((cycle / "supervisor" / "status.json").read_text())
    assert receipt["action"] == action and receipt["profile"] == "attention"


def test_supervisor_cannot_continue_again_after_bounded_scientific_retry(tmp_path):
    cycle = tmp_path / "86"
    cycle.mkdir()
    with patch.dict("os.environ", {KEY: "1"}):
        backend = MagicMock()
        backend.snapshot_budget_review_thread = None
        backend.invoke_attention_review.return_value = (
            "thread2", json.dumps({
                "action": "continue",
                "reason": "The earlier method might be improved but reviewer has no new contrast.",
                "guidance": "Run another investigation turn on the same frozen hypothesis and same test.",
            }),
        )
        decision = review_attention_in_sdk(
            backend=backend, cycle_dir=cycle, state=_blocked(cycle, review_count=1),
            goal="g", research_contract="c", final_gate="f",
        )
    assert decision["action"] == "hold"


def test_new_unexpected_native_blocked_is_not_scientific_stop(tmp_path):
    cycle = tmp_path / "new_cycle"
    cycle.mkdir()
    state = _blocked(cycle)
    state["attention"]["code"] = "investigator_native_goal_unexpected_blocked"
    with patch.dict("os.environ", {KEY: "1"}):
        backend = MagicMock()
        result = review_attention_in_sdk(
            backend=backend, cycle_dir=cycle, state=state,
            goal="g", research_contract="c", final_gate="f",
        )
    assert result["action"] == "hold"
    assert "without preservation" in result["reason"]
    backend.invoke_attention_review.assert_not_called()
    backend.invoke.assert_not_called()
    receipt = json.loads((cycle / "supervisor" / "status.json").read_text())
    assert receipt["action"] == "hold"


def test_supervisor_off_native_blocked_stays_needs_attention(tmp_path):
    cycle = tmp_path / "86"
    cycle.mkdir()
    with patch.dict("os.environ", {KEY: "0"}):
        from theory_master.supervisor_intervention import supervisor_enabled
        assert supervisor_enabled(cycle.parent) is False
        backend = MagicMock()
        # The workflow must not invoke review_attention_in_sdk when disabled.
        # If mistakenly called, the blocked route still fails closed.
        result = review_attention_in_sdk(
            backend=backend, cycle_dir=cycle, state=_blocked(cycle),
            goal="g", research_contract="c", final_gate="f",
        )
        assert result["action"] == "hold"
        backend.invoke_attention_review.assert_not_called()


def test_supervisor_authorization_requires_matching_scoped_receipt(tmp_path):
    cycle = tmp_path / "86"
    cycle.mkdir()
    (cycle / "run_state.json").write_text(json.dumps({
        "status": "NEEDS_ATTENTION", "phase": "investigator:goal_suspended",
        "execution_active": False,
        "attention": {"code": "investigator_native_goal_blocked", "goal_status": "blocked"},
        "hypothesis_lock": {
            "status": "locked", "id": "H-0056",
            "hypothesis": "H", "scope": "scope", "decisive_test": "test",
        },
        "research_root": str(cycle.parent),
    }))
    approval = cycle / "preservation" / "supervisor_authorization.json"
    approval.parent.mkdir()
    approval.write_text(json.dumps({
        "action": "preserve", "source": "supervisor_attention",
        "hypothesis_id": "H-0056", "session_id": "SUP-0000000000000001",
    }))
    supervisor = cycle / "supervisor" / "status.json"
    supervisor.parent.mkdir()
    supervisor.write_text(json.dumps({
        "profile": "attention", "code": "investigator_native_goal_blocked",
        "action": "preserve", "status": "returned",
        "session_id": "SUP-0000000000000001",
    }))
    with patch.dict("os.environ", {KEY: "0"}):
        with pytest.raises(PreservationGuard, match="disabled"):
            _check_preservation_authorization(cycle, "supervisor_approved", "H-0056")
    with patch.dict("os.environ", {KEY: "1"}):
        _check_preservation_authorization(cycle, "supervisor_approved", "H-0056")
        with pytest.raises(PreservationGuard, match="frozen Hypothesis"):
            _check_preservation_authorization(cycle, "supervisor_approved", "H-0057")
