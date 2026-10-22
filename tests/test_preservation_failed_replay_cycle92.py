"""Offline regression for failed Supervisor preservation replay (zero LLM calls)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master.research_preservation import (
    PreservationGuard, blocked_checkpoint, run_preservation_stage, verified_sources,
)


def _output():
    return {
        "observations": [{
            "statement": "Qualified existing graph partition checked.",
            "conditions": "Known historical-label exposure.",
            "evidence_paths": ["input_audit.json", "workspace/input_audit.json"],
            "limitations": "Not a protected prospective blind test.",
        }],
        "methods_exhausted": [{"method": "Train-only fit", "limitation": "Not stable"}],
        "remaining_gap": "No justified shared kernel",
        "unexecuted_test": "No frozen evaluation predictions",
        "canvas_title": "Blocked two-kernel closure",
        "canvas_summary": "Unadjudicated training-only result",
    }


def _failed_cycle(tmp_path):
    cycle = tmp_path / "92"
    (cycle / "workspace").mkdir(parents=True)
    (cycle / "preservation").mkdir()
    (cycle / "supervisor").mkdir()
    (cycle / "workspace" / "input_audit.json").write_text('{"ok":true}')
    (cycle / "preservation" / "last_model_output.txt").write_text(json.dumps(_output()))
    state = {
        "status": "NEEDS_ATTENTION", "phase": "investigator:goal_suspended",
        "execution_active": False,
        "attention": {"code": "investigator_preservation_failed", "reason": "file path"},
        "hypothesis_lock": {
            "status": "locked", "id": "H-0062", "hypothesis": "H",
            "scope": "S", "decisive_test": "T",
        },
        "branch_canvas": str(tmp_path / "exploration.canvas"),
    }
    (cycle / "run_state.json").write_text(json.dumps(state))
    (cycle / "important_events.jsonl").write_text(json.dumps({
        "code": "investigator_native_goal_blocked", "kind": "needs_attention",
        "lifecycle": "registered", "cycle_id": "92",
    }) + "\n")
    (cycle / "preservation" / "supervisor_authorization.json").write_text(json.dumps({
        "source": "supervisor_attention", "action": "preserve",
        "hypothesis_id": "H-0062", "session_id": "SUP-replay",
    }))
    (cycle / "supervisor" / "status.json").write_text(json.dumps({
        "code": "investigator_native_goal_blocked", "status": "returned",
        "profile": "attention", "action": "preserve", "session_id": "SUP-replay",
    }))
    return cycle


def test_workspace_bare_paths_are_normalized_and_deduplicated(tmp_path):
    cycle = _failed_cycle(tmp_path)
    sources = verified_sources(cycle, _output())
    assert len(sources) == 1
    assert sources[0]["path"] == "workspace/input_audit.json"
    assert len(sources[0]["sha256"]) == 64


@pytest.mark.parametrize("path", ["../external.json", "/tmp/fabricated.json", "nonexistent.json"])
def test_missing_and_outside_sources_still_rejected(tmp_path, path):
    cycle = _failed_cycle(tmp_path)
    result = _output()
    result["observations"][0]["evidence_paths"] = [path]
    with pytest.raises(PreservationGuard, match="source"):
        verified_sources(cycle, result)


def test_symlink_source_rejected(tmp_path):
    cycle = _failed_cycle(tmp_path)
    (cycle / "workspace" / "linked.json").symlink_to("input_audit.json")
    result = _output()
    result["observations"][0]["evidence_paths"] = ["linked.json"]
    with pytest.raises(PreservationGuard, match="source"):
        verified_sources(cycle, result)


def test_failed_checkpoint_requires_prior_native_block_and_supervisor_receipt(tmp_path):
    cycle = _failed_cycle(tmp_path)
    assert blocked_checkpoint(cycle)["hypothesis_lock"]["id"] == "H-0062"
    (cycle / "important_events.jsonl").write_text("")
    with pytest.raises(PreservationGuard, match="original authorization"):
        blocked_checkpoint(cycle)
    (cycle / "important_events.jsonl").write_text(json.dumps({
        "code": "investigator_native_goal_blocked", "kind": "needs_attention",
        "lifecycle": "registered", "cycle_id": "92",
    }) + "\n")
    (cycle / "supervisor" / "status.json").write_text('{"action":"hold"}')
    with pytest.raises(PreservationGuard, match="original authorization"):
        blocked_checkpoint(cycle)


def test_existing_stage_response_reused_without_llm(tmp_path):
    cycle = _failed_cycle(tmp_path)
    backend = MagicMock()
    with patch(
        "theory_master.research_preservation._check_preservation_authorization",
    ), patch(
        "theory_master.research_preservation.preserve_blocked_cycle",
        return_value={"created": True},
    ) as preserve:
        result = run_preservation_stage(
            cycle_dir=cycle, canvas_path=Path("/tmp/unused.canvas"),
            backend=backend, model="not-called", provider="not-called",
            authorization="supervisor_approved",
        )
    assert result["created"]
    backend.ensure_thread.assert_not_called()
    backend.invoke.assert_not_called()
    preserve.assert_called_once()
    assert preserve.call_args.kwargs["stage_output"] == _output()
