"""Public v2 benchmark inputs stay explicit, hashed and inside frozen Goal access."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.delivery_repair import _canonical, current_delivery_payload
from theory_master.goal_materials import (
    goal_material_sources,
    material_roots,
    required_benchmark_references,
    resolve_material,
)
from theory_master.goal_snapshot import GoalSnapshotSeed, materialize_goal_snapshot
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import initialize_state
from theory_master.specification import TaskSpec


def v2_inputs(tmp_path):
    home = tmp_path / "notebook"
    home.mkdir()
    research = tmp_path / "runtime" / "research"
    cycle = research / "1"
    cycle.mkdir(parents=True)
    benchmark = tmp_path / "runtime" / "benchmark"
    (benchmark / "datasets").mkdir(parents=True)
    public = benchmark / "instruction.md"
    public.write_text("Public schema, with no private truth.\n")
    data = benchmark / "datasets" / "observed.csv"
    data.write_text("x,y\n1,2\n")
    context = (
        "- benchmark/instruction.md: public schema.\n"
        "- [Observations](benchmark/datasets/observed.csv): public observations.\n"
        "- Do not read benchmark/grader/expected.json.\n"
        "```text\n- benchmark/not-a-registration.json\n```\n"
    )
    plan = home / "research_plan.md"
    plan.write_text(
        "# Goal\nFixture\n\n# Research Contract\nPublic inputs only.\n\n"
        "# Final Gate\nReproducible result.\n\n# Required Context\n\n"
        + context
        + "\n# Scientific Model\n\n"
    )
    spec = TaskSpec(
        document=plan,
        goal_id="goal",
        revision=1,
        max_cycles=1,
        max_rounds=1,
        research_root=research,
        branch_canvas=home / "exploration.canvas",
        branch_anchor_id=None,
        next_step_start_id=None,
        notes_mirror_dir=home / "notes",
        goal="Fixture",
        contract="Public inputs only.",
        final_gate="Reproducible result.",
        required_context=context,
        benchmark_dir=benchmark,
        task_home=home,
        task_mode=False,
    )
    initialize_state(research, goal_id="goal")
    graph = research / "science_graph.sqlite3"
    with ScienceGraphStore(graph):
        pass
    return spec, cycle, graph, public, data


def test_v2_required_inputs_are_hashed_frozen_and_in_actual_judge_context(tmp_path):
    spec, cycle, graph, public, data = v2_inputs(tmp_path)
    assert spec.benchmark_root in material_roots(spec)
    assert spec.benchmark_root.parent not in material_roots(spec)
    assert required_benchmark_references(spec) == (
        "benchmark/datasets/observed.csv",
        "benchmark/instruction.md",
    )
    assert resolve_material(spec, "benchmark/instruction.md")["path"] == str(public)
    payload = current_delivery_payload(spec, graph, cycle)
    by_ref = {
        row["reference"]: row
        for row in payload["goal_material_sources"]
        if row["reference"].startswith("benchmark/")
    }
    assert set(by_ref) == set(required_benchmark_references(spec))
    assert all(row["status"] == "available" for row in by_ref.values())
    with ScienceGraphStore(graph, readonly=True) as store:
        view = store.agent_goal_completion_view()
        full_graph = store.graph_view()
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            spec.document.read_bytes(),
            _canonical(full_graph),
            _canonical(view),
            _canonical(payload),
            _canonical({}),
            _canonical({}),
        ),
    )
    aliases = snapshot.access["required_context_aliases"]
    for reference, original in [
        ("benchmark/instruction.md", public),
        ("benchmark/datasets/observed.csv", data),
    ]:
        path = Path(aliases[reference]["path"])
        assert path.is_relative_to(snapshot.root)
        assert path != original and path.read_bytes() == original.read_bytes()
        assert aliases[reference]["sha256"] == by_ref[reference]["sha256"]
    context = stage_context(
        "judge",
        "goal_completion",
        {
            "research_plan_path": str(snapshot.plan_path),
            "delivery_material_access": snapshot.access,
            "agent_graph": snapshot.agent_view(),
        },
    )
    prompt = build_initial_prompt(
        prefix="",
        role="judge",
        stage="goal_completion",
        contract_ref="fixture",
        contract_schema="{}",
        write_rule="Read only frozen inputs.",
        context=context,
    )
    serialized = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
    assert serialized["delivery_material_access"]["required_context_aliases"] == aliases
    assert (
        "required_context_aliases"
        in serialized["delivery_material_access"]["snapshot_boundary"]
    )
    public.write_text("A repaired public schema.\n")
    changed = current_delivery_payload(spec, graph, cycle)
    assert payload["science_digest"] == changed["science_digest"]
    assert payload["goal_material_sources"] != changed["goal_material_sources"]
    assert (
        Path(aliases["benchmark/instruction.md"]["path"]).read_text()
        == "Public schema, with no private truth.\n"
    )
    snapshot.validate(compare_sources=False)
    with pytest.raises(ValueError, match="source changed"):
        snapshot.validate()


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("benchmark/../grader/expected.json", "invalid_benchmark_reference"),
        ("benchmark/%2e%2e/grader/expected.json", "invalid_benchmark_reference"),
        ("benchmark/grader/expected.json", "benchmark_file_not_in_required_context"),
        ("absolute_private", "benchmark_file_not_in_required_context"),
        ("sibling_private", "outside_registered_roots"),
    ],
)
def test_benchmark_boundary_never_reads_unregistered_private_files(
    tmp_path, monkeypatch, reference, expected
):
    spec, _, _, _, _ = v2_inputs(tmp_path)
    private = spec.benchmark_root / "grader" / "expected.json"
    private.parent.mkdir()
    private.write_text("dummy private fixture, never opened by Goal")
    sibling = spec.benchmark_root.parent / "grader" / "expected.json"
    sibling.parent.mkdir()
    sibling.write_text("dummy sibling fixture, never opened by Goal")
    reference = {"absolute_private": str(private), "sibling_private": str(sibling)}.get(
        reference, reference
    )
    from theory_master import goal_materials

    real_sha = goal_materials.material_sha256

    def only_public(path):
        assert path not in {private, sibling}, "Goal read a private fixture"
        return real_sha(path)

    monkeypatch.setattr(goal_materials, "material_sha256", only_public)
    assert resolve_material(spec, reference)["status"] == expected
    rows = goal_material_sources(spec, [{"path": reference}])
    assert (
        next(row for row in rows if row["reference"] == reference)["status"] == expected
    )


def test_benchmark_directory_symlink_and_unconfigured_alias_are_not_inputs(tmp_path):
    spec, _, _, public, _ = v2_inputs(tmp_path)
    spec = replace(
        spec,
        required_context=spec.required_context
        + "\n- benchmark/datasets\n- benchmark/linked.md\n",
    )
    (spec.benchmark_root / "linked.md").symlink_to(public)
    assert (
        resolve_material(spec, "benchmark/datasets")["status"]
        == "benchmark_reference_is_not_a_file"
    )
    assert resolve_material(spec, "benchmark/linked.md")["status"] == "symlink"
    spec = replace(spec, benchmark_dir=None)
    assert (
        resolve_material(spec, "benchmark/instruction.md")["status"]
        == "invalid_benchmark_reference"
    )


def test_minimal_snapshot_spec_uses_only_required_context_file_entries(tmp_path):
    spec, _, _, _, _ = v2_inputs(tmp_path)
    spec.document.write_text(
        spec.document.read_text() + "\n# Other\n- benchmark/not-context.json\n"
    )
    minimal = SimpleNamespace(
        document=spec.document,
        research_root=spec.research_root,
        branch_canvas=spec.branch_canvas,
        benchmark_root=spec.benchmark_root,
    )
    assert required_benchmark_references(minimal) == required_benchmark_references(spec)
    rows = goal_material_sources(minimal, [{}])
    assert {row["reference"] for row in rows} == set(
        required_benchmark_references(spec)
    )
    assert all(row["status"] == "available" for row in rows)


def test_legacy_seed_without_catalog_materializes_required_context_files(tmp_path):
    spec, cycle, graph, public, data = v2_inputs(tmp_path)
    payload = current_delivery_payload(spec, graph, cycle)
    payload.pop("goal_material_sources")
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            spec.document.read_bytes(),
            _canonical({}),
            _canonical({}),
            _canonical(payload),
            _canonical({}),
            _canonical({}),
        ),
    )
    aliases = snapshot.access["required_context_aliases"]
    for reference, source in [
        ("benchmark/instruction.md", public),
        ("benchmark/datasets/observed.csv", data),
    ]:
        row = aliases[reference]
        frozen = Path(row["path"])
        assert row["status"] == "available" and frozen.is_file()
        assert frozen.is_relative_to(snapshot.root / "files")
        assert frozen.read_bytes() == source.read_bytes()
    frozen_payload = json.loads((snapshot.root / "delivery_revision.json").read_text())
    assert frozen_payload["goal_material_sources"]
    snapshot.validate()
