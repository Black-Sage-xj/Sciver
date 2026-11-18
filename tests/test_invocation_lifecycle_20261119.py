"""Failure injection across a real Workflow invocation; all providers are fake."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from theory_master.backends import FakeBackend
from theory_master.invocations import InvocationLedger
from theory_master.provider_errors import ProviderQuotaExhausted
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow
from theory_master.workflow_support import RecoveryStop


class FailingRepairBackend(FakeBackend):
    failure_mode = ""
    investigator_calls = 0

    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "investigator" and self.failure_mode:
            self.investigator_calls += 1
            thread = self.ensure_thread(role, thread_id, model, provider)
            if self.failure_mode == "initial":
                raise RuntimeError("initial sentinel")
            if self.investigator_calls == 1:
                if self.failure_mode.startswith("json"):
                    return thread, "not JSON"
                return thread, json.dumps(
                    {
                        "findings": [
                            {
                                "statement": "invalid current output",
                                "evidence_kind": "literature",
                                "scope": "test",
                                "limitations": "",
                            }
                        ]
                    }
                )
            if self.failure_mode.endswith("quota"):
                raise ProviderQuotaExhausted(
                    "quota sentinel", provider="fake", account="1"
                )
            raise RuntimeError("repair sentinel")
        return super().invoke(role, prompt, thread_id, model, provider)


def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    notes = tmp_path / "notes"
    notes.mkdir()
    plan = notes / "research_plan.md"
    plan.write_text(
        "# Goal\n\nOffline lifecycle.\n\n# Research Contract\n\nLocal fixture.\n\n"
        "# Final Gate\n\nIndependent support.\n\n# Required Context\n\nLocal.\n"
    )
    spec = TaskSpec.from_config(bootstrap(plan).config_path)
    backend = FailingRepairBackend(reworks=0)
    workflow = Workflow(spec, backend, stop_before="investigator")
    state = workflow.run()
    assert state["status"] == "checkpoint_reached"
    workflow.stop_before = None
    return workflow, backend, workflow.cycle_dir


@pytest.mark.parametrize(
    "phase",
    [
        "initial",
        "json",
        "contract",
        "normalization",
        "validation",
        "semantic",
        "evidence",
        "closure",
        "checkpoint",
        "commit",
    ],
)
def test_every_phase_failure_has_terminal_ledger_and_cleared_active_fields(
    tmp_path, monkeypatch, phase
):
    workflow, backend, cycle = prepared(tmp_path, monkeypatch)
    import theory_master.invocation_output as execution
    import theory_master.invocation_commit as commit
    import theory_master.contract_closure as closure

    if phase in {"initial", "json", "contract"}:
        backend.failure_mode = phase
    elif phase in {"normalization", "validation"}:
        name = (
            "normalize_agent_output"
            if phase == "normalization"
            else "validate_agent_output"
        )
        monkeypatch.setattr(
            execution,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("phase sentinel")
            ),
        )
    elif phase == "semantic":
        monkeypatch.setattr(
            closure,
            "close_agent_output",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("semantic sentinel")
            ),
        )
    elif phase == "evidence":
        monkeypatch.setattr(
            workflow,
            "_register_evidence",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("evidence sentinel")
            ),
        )
    elif phase == "closure":
        original = commit.write_json

        def fail(path, payload):
            if path.parent.name == "semantic_closure":
                raise OSError("closure sentinel")
            return original(path, payload)

        monkeypatch.setattr(commit, "write_json", fail)
    elif phase == "checkpoint":
        original = workflow._checkpoint

        def fail(state, name):
            if name == "investigator":
                raise RuntimeError("checkpoint sentinel")
            return original(state, name)

        monkeypatch.setattr(workflow, "_checkpoint", fail)
    else:
        original = InvocationLedger.transition

        def fail(ledger, invocation_id, status, **details):
            if "investigator" in invocation_id and status == "committed":
                raise OSError("commit sentinel")
            return original(ledger, invocation_id, status, **details)

        monkeypatch.setattr(InvocationLedger, "transition", fail)
    with pytest.raises((RuntimeError, OSError)):
        workflow.resume("1")
    state = json.loads((cycle / "research_state.json").read_text())
    invocation = next(
        row
        for row in reversed(InvocationLedger(cycle / "invocations.jsonl").snapshots())
        if row.get("role") == "investigator"
    )
    expected = "completed" if phase in {"checkpoint", "commit"} else "failed"
    assert invocation["state"] == expected
    assert invocation.get("commit_state") == "not_committed"
    assert state.get("active_invocation_id") is None
    assert state.get("execution_active") is False
    assert state.get("active_role") is None
    manifest = json.loads(Path(invocation["invocation_artifact_manifest"]).read_text())
    assert manifest["status"] == expected
    assert invocation["failure_phase"] in {
        "invoke",
        "validation",
        "semantic_closure",
        "commit",
    }


@pytest.mark.parametrize("mode", ["json_quota", "contract_quota"])
def test_repair_quota_keeps_typed_pause_and_interrupted_invocation(
    tmp_path, monkeypatch, mode
):
    workflow, backend, cycle = prepared(tmp_path, monkeypatch)
    backend.failure_mode = mode
    state = workflow.resume("1")
    assert state["status"] == "paused"
    assert state["recovery"]["kind"] == "ProviderQuotaExhausted"
    assert state["recovery"]["codex_account"] == "1"
    assert state.get("active_invocation_id") is None
    row = next(
        row
        for row in reversed(InvocationLedger(cycle / "invocations.jsonl").snapshots())
        if row.get("role") == "investigator"
    )
    assert row["state"] == "interrupted"
    assert (
        json.loads(Path(row["invocation_artifact_manifest"]).read_text())["status"]
        == "interrupted"
    )


def test_semantic_control_signal_is_not_reclassified_as_failure(tmp_path, monkeypatch):
    workflow, _, cycle = prepared(tmp_path, monkeypatch)
    import theory_master.contract_closure as closure

    monkeypatch.setattr(
        closure,
        "close_agent_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(RecoveryStop("pause sentinel")),
    )
    state = workflow.resume("1")
    assert state.get("active_invocation_id") is None and not state["execution_active"]
    row = next(
        row
        for row in reversed(InvocationLedger(cycle / "invocations.jsonl").snapshots())
        if row.get("role") == "investigator"
    )
    assert row["state"] == "interrupted" and row["error_type"] == "RecoveryStop"
