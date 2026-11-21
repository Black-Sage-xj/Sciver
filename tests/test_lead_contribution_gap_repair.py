"""Offline regression for an omitted declared source contribution."""
from __future__ import annotations

import json
from unittest.mock import patch

from theory_master.backend_fakes import FakeBackend
from theory_master.research_transition import validate_source_contributions
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow
from tests.test_core import TheoryMasterTest
from tests.test_source_contributions_20261119 import proposal


def test_validation_names_the_missing_and_extra_sources():
    value = proposal()["candidates"][0]
    notes = value["transition"]["source_contributions"]
    notes.pop("H-0008")
    notes["H-0009"] = "Undeclared source."
    try:
        validate_source_contributions(value, ["H-0007", "H-0008"], required=True)
    except ValueError as exc:
        assert "missing explanations for ['H-0008']" in str(exc)
        assert "undeclared explanations for ['H-0009']" in str(exc)
    else:
        raise AssertionError("Invalid contribution set was accepted")


def test_lead_repair_identifies_gap_and_escalates_an_unchanged_reply(tmp_path):
    class GapBackend(FakeBackend):
        def __init__(self):
            super().__init__(reworks=0)
            self.repairs = []

        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and (
                "Current task: frontier_proposal " in prompt
                or "Contract validation failed for stage frontier_proposal" in prompt
            ):
                value = proposal()
                if "Contract validation failed" in prompt:
                    self.repairs.append(prompt)
                if len(self.repairs) < 2:
                    value["candidates"][0]["transition"]["source_contributions"].pop("H-0008")
                return self.ensure_thread(role, thread_id, model, provider), json.dumps(value)
            return super().invoke(role, prompt, thread_id, model, provider)

    spec, _ = TheoryMasterTest().make(tmp_path, reworks=0)
    with ScienceGraphStore(spec.graph_database) as store:
        for source in ("H-0007", "H-0008"):
            store.add_hypothesis(source, title=source)
    backend = GapBackend()
    state = Workflow(spec, backend).run()
    assert state["status"] in {"over", "completed"}
    assert state["contract_repair_count"] == 2
    assert len(backend.repairs) == 2
    assert "missing explanations for ['H-0008']" in backend.repairs[0]
    assert '"missing_source_ids": ["H-0008"]' in backend.repairs[0]
    assert "previous 1 repair response(s) repeated" in backend.repairs[1]
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        transition = store.agent_hypothesis_detail("H-0001")["research_transition"]
    assert {row["hypothesis_id"] for row in transition["sources"]} == {"H-0007", "H-0008"}


def test_supervisor_finds_local_codex_when_service_path_omits_it(tmp_path):
    from theory_master import supervisor_runtime_repair as repair

    binary = tmp_path / "bin" / "codex.real"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    commands = []

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)

        def communicate(self, **kwargs):
            return None

    with patch.object(repair.shutil, "which", return_value=None), \
         patch.object(repair.Path, "home", return_value=tmp_path), \
         patch.object(repair.subprocess, "Popen", side_effect=Child):
        success, _ = repair._invoke_codex(tmp_path, "Fake", "diagnose")
    assert success
    assert commands[0][0] == str(binary)
