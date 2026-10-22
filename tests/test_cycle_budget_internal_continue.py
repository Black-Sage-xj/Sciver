"""Internal Supervisor continuation reuses the current logical Cycle slot."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from theory_master.cycle_runner import CycleRunner
from theory_master.observability.collector import Collector
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


def _spec(root: Path) -> TaskSpec:
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\nCheck a small deterministic hypothesis.\n\n"
        "# Research Contract\nUse local synthetic inputs only.\n\n"
        "# Final Gate\nA deterministic independent check.\n\n"
        "# Required Context\nNone.\n",
        encoding="utf-8",
    )
    return TaskSpec.from_config(bootstrap(plan).config_path)


def test_workflow_internal_continue_reuses_existing_invocation_slot():
    workflow = Workflow.__new__(Workflow)
    workflow.invocation_cycle_index = 1

    assert workflow._claim_invocation_cycle_slot(same_cycle_continuation=True) == 1
    assert workflow.invocation_cycle_index == 1
    assert workflow._claim_invocation_cycle_slot() == 2
    assert workflow.invocation_cycle_index == 2


def test_cycle_runner_forwards_internal_continuation_explicitly():
    class Owner:
        def _execute_cycle(self, resume_state=None, *, same_cycle_continuation=False):
            return (resume_state, same_cycle_continuation)

    state = {"cycle_id": "1"}
    assert CycleRunner(Owner()).run(state, same_cycle_continuation=True) == (state, True)
    assert CycleRunner(Owner()).run(state) == (state, False)


def test_collector_internal_continue_keeps_same_cycle_index_and_start_semantics():
    with tempfile.TemporaryDirectory() as temp:
        spec = _spec(Path(temp))
        first_cycle = spec.research_root / "1"
        first_cycle.mkdir()
        collector = Collector(spec)
        collector.start_run("1", spec, run_dir=first_cycle)
        assert collector.snapshot()["cycle_index"] == 1

        collector.start_run("1", spec, run_dir=first_cycle, same_cycle_continuation=True)
        assert collector.snapshot()["cycle_index"] == 1
        events = collector.runtime_events(limit=0)
        assert len([event for event in events if event.get("event") == "CYCLE_STARTED"]) == 1
        assert len([event for event in events if event.get("event") == "CYCLE_CONTINUED"]) == 1
        collector.finish()

        next_cycle = spec.research_root / "2"
        collector.start_run("2", spec, run_dir=next_cycle)
        assert collector.snapshot()["cycle_index"] == 2
        persisted = json.loads((next_cycle / "run_state.json").read_text(encoding="utf-8"))
        assert persisted["cycle_index"] == 2
        collector.finish()
