"""Real Goal Gate + fake Judge concurrency; no model or real task is launched."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from theory_master.delivery_repair import repair_delivery
from theory_master.delivery_repair import _canonical, current_delivery_payload
from theory_master.goal_materials import goal_material_sources, resolve_material
from theory_master.goal_snapshot import (
    GoalAuthoritySnapshot,
    GoalSnapshotSeed,
    materialize_goal_snapshot,
)
from theory_master.goal_terminal import science_commit_barrier
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.workflow import Workflow
import test_delivery_repair as fixture


def test_snapshot_preserves_relative_report_and_plan_navigation(tmp_path):
    research = tmp_path / "research"
    cycle = research / "1"
    workspace = cycle / "workspace"
    (workspace / "reports").mkdir(parents=True)
    script = workspace / "reproduce.py"
    script.write_text("print('captured reproduction')\n")
    report = workspace / "reports" / "report.md"
    report.write_text("Run the [script](../reproduce.py).\n")
    plan = tmp_path / "research_plan.md"
    plan.write_text("Read the [report](research/1/workspace/reports/report.md).\n")
    spec = SimpleNamespace(
        research_root=research,
        document=plan,
        branch_canvas=tmp_path / "exploration.canvas",
    )
    view = {"hypotheses": [{"recent_cycle_outcomes": [{"detail_ref": str(report)}]}]}
    payload = {
        "material_index": {"assets": [{"path": str(script)}]},
        "report_versions": [{"path": str(report)}],
    }
    payload["goal_material_sources"] = goal_material_sources(spec, [view, payload])
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            plan.read_bytes(),
            _canonical(view),
            _canonical(view),
            _canonical(payload),
            _canonical({}),
            _canonical({}),
        ),
    )
    frozen_report = Path(
        snapshot.agent_view()["hypotheses"][0]["recent_cycle_outcomes"][0]["detail_ref"]
    )
    frozen_script = (frozen_report.parent / "../reproduce.py").resolve()
    assert frozen_script.is_relative_to(snapshot.root)
    assert frozen_script.read_bytes() == script.read_bytes()
    assert frozen_report.read_bytes() == report.read_bytes()
    assert (
        snapshot.plan_path.parent / "research/1/workspace/reports/report.md"
    ).resolve() == frozen_report
    captured = json.loads((snapshot.root / "delivery_revision.json").read_text())
    assert Path(captured["material_index"]["assets"][0]["path"]) == frozen_script
    restored = GoalAuthoritySnapshot.restore(
        snapshot.manifest_path, snapshot.manifest_sha256
    )
    assert restored.plan_path == snapshot.plan_path
    script.write_text("print('live replacement must not be read')\n")
    assert frozen_script.read_text() == "print('captured reproduction')\n"
    snapshot.validate(compare_sources=False)
    with pytest.raises(ValueError, match="source changed"):
        snapshot.validate()


def test_snapshot_never_follows_inherited_receipts_or_outside_materials(tmp_path):
    task = tmp_path / "new-task"
    research = task / "research"
    cycle = research / "1"
    cycle.mkdir(parents=True)
    plan = task / "research_plan.md"
    plan.write_text("# Final Gate\nCaptured local fixture.\n")
    original = tmp_path / "source-task" / "goal_assessments" / "prior.json"
    original.parent.mkdir(parents=True)
    original.write_text('{"verdict":"not_passed"}')
    spec = SimpleNamespace(
        research_root=research, document=plan, branch_canvas=task / "exploration.canvas"
    )
    view = {"goal_gate_history": [{"receipt_path": str(original)}]}
    payload = {"material_index": {"assets": [{"path": str(original)}]}}
    payload["goal_material_sources"] = goal_material_sources(spec, [view, payload])
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            plan.read_bytes(),
            _canonical(view),
            _canonical(view),
            _canonical(payload),
            _canonical({}),
            _canonical({}),
        ),
    )
    receipt = Path(snapshot.agent_view()["goal_gate_history"][0]["receipt_path"])
    assert (
        receipt.is_relative_to(snapshot.root / "unavailable") and not receipt.exists()
    )
    manifest = json.loads(snapshot.manifest_path.read_text())
    assert str(original) not in {row["path"] for row in manifest["sources"]}
    assert {row["reason"] for row in manifest["unavailable_references"]} == {
        "prior_goal_assessment_provenance",
        "outside_registered_roots",
    }
    snapshot.validate()


def test_absolute_markdown_targets_use_frozen_files_without_changing_literals(tmp_path):
    research = tmp_path / "research"
    cycle = research / "1"
    workspace = cycle / "workspace"
    workspace.mkdir(parents=True)
    script = workspace / "reproduce.py"
    script.write_text("print('original result')\n")
    figure = workspace / "result figure.png"
    figure.write_bytes(b"fixture image")
    outside = tmp_path.parent / "unregistered.md"
    report = workspace / "report.md"
    literals = (
        f"`[literal]({script})`\n"
        f"```python\n[literal code]({script})\n```\n"
        f"    [indented code]({script})\n"
    )
    text = (
        "Scientific claim and result: 42.\n"
        f"[script]({script}#run)\n"
        f"![result](<{figure}>)\n"
        f"[unknown]({outside})\n"
        "[relative](reproduce.py)\n"
        "[remote](https://example.org/source)\n"
        "[protocol remote](//example.org/source)\n"
        f'[script-ref]: {script} "title"\n' + literals
    )
    report.write_text(text)
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Final Gate\nRead registered delivery.\n")
    spec = SimpleNamespace(
        research_root=research,
        document=plan,
        branch_canvas=tmp_path / "exploration.canvas",
    )
    view = {"detail_ref": str(report)}
    payload = {
        "material_index": {
            "assets": [{"path": str(path)} for path in (report, script, figure)]
        }
    }
    payload["goal_material_sources"] = goal_material_sources(spec, [view, payload])
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            plan.read_bytes(),
            _canonical(view),
            _canonical(view),
            _canonical(payload),
            _canonical({}),
            _canonical({}),
        ),
    )
    frozen_report = Path(snapshot.agent_view()["detail_ref"])
    captured = frozen_report.read_text()
    script_target = captured.split("[script](", 1)[1].split(")", 1)[0].split("#", 1)[0]
    assert (
        frozen_report.parent / unquote(script_target)
    ).read_bytes() == script.read_bytes()
    figure_target = captured.split("![result](<", 1)[1].split(">", 1)[0]
    assert (
        frozen_report.parent / unquote(figure_target)
    ).read_bytes() == figure.read_bytes()
    unavailable = captured.split("[unknown](", 1)[1].split(")", 1)[0]
    assert (
        (frozen_report.parent / unavailable)
        .resolve()
        .is_relative_to(snapshot.root / "unavailable")
    )
    assert "Scientific claim and result: 42.\n" in captured
    assert literals in captured
    assert "[relative](reproduce.py)\n" in captured
    assert "[remote](https://example.org/source)\n" in captured
    assert "[protocol remote](//example.org/source)\n" in captured
    assert '[script-ref]: reproduce.py "title"' in captured
    assert report.read_text() == text
    manifest = json.loads(snapshot.manifest_path.read_text())
    assert (
        next(row["sha256"] for row in manifest["sources"] if row["path"] == str(report))
        == hashlib.sha256(text.encode()).hexdigest()
    )
    snapshot.validate()


@pytest.mark.parametrize("pending", [False, True])
def test_changed_dossier_invalidates_cached_and_pending_goal_input(tmp_path, pending):
    spec, workflow, backend, cycle = fixture._completed(tmp_path)
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        reference = store.agent_goal_completion_view()["hypotheses"][0]["dossier_ref"]
    resolved = resolve_material(spec, reference)
    assert resolved["status"] == "available"
    dossier = Path(resolved["path"])
    before = current_delivery_payload(spec, spec.graph_database, cycle)
    original = backend.invoke
    pending_snapshot = None
    if pending:
        dossier.write_text(dossier.read_text() + "\nFirst delivery amendment.\n")

        def interrupted(role, prompt, thread, model, provider):
            if role == "judge" and "Current task: goal_completion " in prompt:
                raise RuntimeError("offline interruption before completed Judge")
            return original(role, prompt, thread, model, provider)

        backend.invoke = interrupted
        with pytest.raises(RuntimeError, match="offline interruption"):
            repair_delivery(workflow, reassess=True)
        state = json.loads((cycle / "research_state.json").read_text())
        pending_snapshot = state["goal_delivery_pending"]["snapshot_manifest"]
        backend.invoke = original
    dossier.write_text(
        dossier.read_text() + "\nRepaired navigation and reproduction instructions.\n"
    )
    after = current_delivery_payload(spec, spec.graph_database, cycle)
    assert before["science_digest"] == after["science_digest"]
    assert before["goal_material_sources"] != after["goal_material_sources"]
    calls = backend.goal_calls
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["verdict"] == "not_passed"
    assert backend.goal_calls == calls + 1
    frozen_ref = backend.goal_context["agent_graph"]["hypotheses"][0]["dossier_ref"]
    assert Path(frozen_ref).read_bytes() == dossier.read_bytes()
    if pending_snapshot:
        receipt = json.loads((cycle / "goal_completion_out.json").read_text())
        assert pending_snapshot != receipt["snapshot_manifest"]
        assert Path(pending_snapshot).is_file()


def changed_delivery(tmp_path):
    spec, workflow, backend, cycle = fixture._completed(tmp_path)
    report = fixture._report(spec, cycle)
    report.write_text(
        report.read_text() + "\nA new delivery revision for assessment.\n"
    )
    return spec, workflow, backend, cycle, report


@pytest.mark.parametrize("change", ["plan", "graph", "model", "delivery", "snapshot"])
def test_unlocked_judge_reads_frozen_inputs_and_drift_never_seals(tmp_path, change):
    spec, workflow, backend, cycle, report = changed_delivery(tmp_path)
    original = backend.invoke
    before_plan = Path(spec.document).read_bytes()
    writer_done = threading.Event()
    frozen_paths = []
    errors = []

    def invoke(role, prompt, thread, model, provider):
        if role != "judge" or "Current task: goal_completion " not in prompt:
            return original(role, prompt, thread, model, provider)
        context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
        plan = Path(context["research_plan_access"]["path"])
        assert plan != Path(spec.document)
        assert plan.read_bytes() == before_plan
        frozen_paths.append(plan)
        revision = json.loads(
            Path(context["delivery_material_access"]["revision_path"]).read_text()
        )
        assert (
            Path(revision["report_versions"][0]["path"]).read_text()
            == report.read_text()
        )
        assert context["agent_graph"]["snapshot_boundary"]

        def writer():
            try:
                # A separate thread must acquire the shared writer barrier while
                # Judge is running. Holding a global exclusive lock fails here.
                with science_commit_barrier(spec.research_root, "shared"):
                    if change == "plan":
                        Path(spec.document).write_bytes(
                            before_plan + b"\nConcurrent authorized note.\n"
                        )
                    elif change == "graph":
                        with ScienceGraphStore(spec.graph_database) as store:
                            store.add_hypothesis(
                                "H-9999", title="Concurrent fixture hypothesis"
                            )
                    elif change == "delivery":
                        report.write_text(
                            report.read_text() + "\nConcurrent report change.\n"
                        )
                    elif change == "model":
                        from theory_master.scientific_model import state_path

                        path = state_path(spec.research_root)
                        model = json.loads(path.read_text())
                        model["version"] += 1
                        path.write_text(json.dumps(model))
                    else:
                        plan.chmod(0o644)
                        plan.write_text("tampered snapshot")
            except BaseException as exc:
                errors.append(exc)
            finally:
                writer_done.set()

        worker = threading.Thread(target=writer, daemon=True)
        worker.start()
        assert writer_done.wait(2), "Goal Judge retained the exclusive writer barrier"
        worker.join(2)
        assert not errors
        if change != "snapshot":
            assert plan.read_bytes() == before_plan
        return str(thread), json.dumps(
            {"verdict": "passed", "rationale": "Frozen fixture satisfies Final Gate."}
        )

    backend.invoke = invoke
    result = repair_delivery(workflow, reassess=True)
    assert result["assessment"]["assessment_status"] == "stale"
    assert frozen_paths and writer_done.is_set()
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal() is None
        assert [row["verdict"] for row in store.goal_gate_history(cycle.name)] == [
            "not_passed"
        ]
    receipt = json.loads((cycle / "goal_completion_out.json").read_text())
    assert receipt["verdict"] == "not_passed"
    stale = json.loads((cycle / "research_state.json").read_text())[
        "goal_assessment_stale"
    ]
    assert (
        stale["snapshot_manifest_sha256"]
        == hashlib.sha256(Path(stale["snapshot_manifest"]).read_bytes()).hexdigest()
    )


def test_two_concurrent_goal_assessments_have_one_seal_winner(tmp_path):
    spec, _, backend, cycle, _ = changed_delivery(tmp_path)
    ready = threading.Barrier(2)
    results, errors = [], []

    def assess():
        workflow = Workflow(spec, backend)
        workflow._bind_cycle(cycle, create=False)
        state = json.loads((cycle / "research_state.json").read_text())

        # Isolate the Goal commit protocol from provider/ledger concurrency; both
        # callbacks stand for independent completed formal Judge invocations.
        def judge(state, role, context, **kwargs):
            assert (
                role == "judge"
                and context["goal_authority_snapshot"].plan_path.is_file()
            )
            ready.wait(3)
            return {
                "verdict": "passed",
                "rationale": "The frozen fixture satisfies its Final Gate.",
            }

        workflow._call = judge
        try:
            results.append(
                workflow._run_goal_completion(state, {"verdict": "supported"})
            )
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=assess, daemon=True) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(6)
    assert all(not worker.is_alive() for worker in workers)
    assert not errors
    assert len([row for row in results if row.get("verdict") == "passed"]) == 1
    assert len([row for row in results if row.get("assessment_status") == "stale"]) == 1
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal()["verdict"] == "passed"
        assert (
            len(
                [
                    row
                    for row in store.goal_gate_history(cycle.name)
                    if row["verdict"] == "passed"
                ]
            )
            == 1
        )
