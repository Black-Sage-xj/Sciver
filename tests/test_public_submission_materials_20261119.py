"""Public schema bytes and registered artifact identity survive Goal freezing."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from theory_master.delivery_repair import _canonical, current_delivery_payload
from theory_master.evidence import EvidenceRegistry
from theory_master.goal_snapshot import GoalSnapshotSeed, materialize_goal_snapshot
from theory_master.invocation_artifacts import InvocationArtifactTracker
from theory_master.public_submission import check_frozen_submission
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import initialize_state, load_from_paths
from theory_master.specification import TaskSpec

REFERENCE = "benchmark/public_format.json"


def _task(tmp_path: Path, schema: dict):
    home = tmp_path / "notebook"
    home.mkdir()
    research = tmp_path / "runtime" / "research"
    cycle = research / "1"
    (cycle / "workspace").mkdir(parents=True)
    benchmark = tmp_path / "runtime" / "benchmark"
    benchmark.mkdir()
    declaration = benchmark / "public_format.json"
    declaration.write_text(
        json.dumps(
            {
                "schema": "theory-master/public-submission-contract/v1",
                "json_schema": schema,
            }
        ),
        encoding="utf-8",
    )
    context = f"- {REFERENCE}: explicit public delivery format.\n"
    plan = home / "research_plan.md"
    plan.write_text(
        "# Goal\nToy retained result.\n\n"
        "# Research Contract\nUse only the declared public inputs.\n\n"
        "# Final Gate\nIndependently assess the retained result.\n\n"
        "# Required Context\n\n" + context + "\n# Scientific Model\n\n",
        encoding="utf-8",
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
        goal="Toy retained result.",
        contract="Use only the declared public inputs.",
        final_gate="Independently assess the retained result.",
        required_context=context,
        benchmark_dir=benchmark,
        task_home=home,
        task_mode=False,
        submission_contract_ref=REFERENCE,
    )
    initialize_state(research, goal_id=spec.goal_id)
    graph = research / "science_graph.sqlite3"
    with ScienceGraphStore(graph):
        pass
    return spec, cycle, graph, declaration


def _register_submission(spec, cycle, document):
    workspace = cycle / "workspace"
    producer = {
        "invocation_id": "inv-1-investigator",
        "role": "investigator",
        "stage": "investigate",
    }
    tracker = InvocationArtifactTracker.start(
        cycle_dir=cycle, workspace=workspace, **producer
    )
    artifact = workspace / "retained_submission.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    manifest = tracker.finalize(status="completed")
    registry = EvidenceRegistry(cycle, workspace, research_root=spec.research_root)
    registry.register_role_output(
        "investigator",
        {
            "evidence": [
                {
                    "summary": "A self-contained retained finding with its authored artifact.",
                    "artifact_pointers": [str(artifact)],
                }
            ]
        },
        epoch="post_lock_test",
        producer_invocation=producer,
        invocation_artifact_manifest=manifest,
    )
    return artifact


def _snapshot(spec, cycle, graph):
    payload = current_delivery_payload(spec, graph, cycle)
    with ScienceGraphStore(graph, readonly=True) as store:
        full, view = store.graph_view(), store.agent_goal_completion_view()
    access = {
        "delivery_revision": hashlib.sha256(_canonical(payload)).hexdigest(),
        "science_digest": payload["science_digest"],
    }
    model = load_from_paths(spec.research_root, spec.document, goal_id=spec.goal_id)
    snapshot = materialize_goal_snapshot(
        spec,
        cycle,
        GoalSnapshotSeed(
            spec.document.read_bytes(),
            _canonical(full),
            _canonical(view),
            _canonical(payload),
            _canonical(access),
            _canonical(model),
        ),
    )
    snapshot.validate()
    frozen = json.loads((snapshot.root / "delivery_revision.json").read_text())
    return snapshot, payload, frozen


def _asset_views(payload):
    return [
        asset
        for group in payload["material_index"]["materials"]
        for asset in group["assets"]
        if Path(asset.get("path", "")).name == "retained_submission.json"
    ]


def test_required_context_contract_alias_points_to_the_existing_frozen_contract(
    tmp_path,
):
    spec, cycle, graph, declaration = _task(tmp_path, {"type": "object"})
    raw = declaration.read_bytes()
    snapshot, payload, frozen = _snapshot(spec, cycle, graph)
    source = next(
        row for row in payload["goal_material_sources"] if row["reference"] == REFERENCE
    )
    alias = snapshot.access["required_context_aliases"][REFERENCE]
    path = Path(alias["path"])
    assert alias["status"] == source["status"] == "available"
    assert path.is_file() and path.is_relative_to(snapshot.root)
    assert path != declaration
    assert path.read_bytes() == raw
    assert alias["sha256"] == source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert alias["path"] == frozen["submission_contract"]["path"]
    assert alias["path"] == snapshot.access["submission_contract"]["path"]

    declaration.write_text('{"changed":true}', encoding="utf-8")
    assert path.read_bytes() == raw
    snapshot.validate(compare_sources=False)
    with pytest.raises(ValueError, match="source changed"):
        snapshot.validate()


@pytest.mark.parametrize(
    "schema,document",
    [
        (
            {
                "type": "object",
                "const": {
                    "path": "observations.csv",
                    "file": "public/record.json",
                    "source": "retained measurement",
                    "reference": "scientific-source.csv",
                },
            },
            {
                "path": "observations.csv",
                "file": "public/record.json",
                "source": "retained measurement",
                "reference": "scientific-source.csv",
            },
        ),
        (
            {
                "type": "object",
                "enum": [
                    {
                        "path": "../observations.csv",
                        "file": "result.json",
                        "source": {"path": "raw/observation.csv"},
                    }
                ],
            },
            {
                "path": "../observations.csv",
                "file": "result.json",
                "source": {"path": "raw/observation.csv"},
            },
        ),
        (
            {
                "type": "object",
                "required": ["record"],
                "properties": {
                    "record": {
                        "const": {
                            "path": "not-a-material-reference.csv",
                            "file": "not-a-file-grant.json",
                            "source": "ordinary schema data",
                        }
                    }
                },
            },
            {
                "record": {
                    "path": "not-a-material-reference.csv",
                    "file": "not-a-file-grant.json",
                    "source": "ordinary schema data",
                }
            },
        ),
    ],
    ids=["const-data", "enum-data", "nested-property-data"],
)
def test_schema_const_enum_path_file_and_source_data_remain_opaque(
    tmp_path, schema, document
):
    spec, cycle, graph, declaration = _task(tmp_path, schema)
    artifact = _register_submission(spec, cycle, document)
    snapshot, payload, frozen = _snapshot(spec, cycle, graph)
    assert payload["submission_contract"]["json_schema"] == schema
    assert frozen["submission_contract"]["json_schema"] == schema
    assert snapshot.access["submission_contract"]["json_schema"] == schema
    frozen_contract = Path(frozen["submission_contract"]["path"])
    assert frozen_contract.read_bytes() == declaration.read_bytes()
    assert json.loads(frozen_contract.read_bytes())["json_schema"] == schema

    result = check_frozen_submission(frozen, snapshot)
    assert result["status"] == "valid"
    assert (
        result["submission"]["sha256"]
        == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )
    assert json.loads(Path(result["submission"]["path"]).read_bytes()) == document


@pytest.mark.parametrize(
    "reverse_groups", [False, True], ids=["pointer-first", "batch-first"]
)
def test_real_registry_pointer_and_invocation_manifest_merge_one_artifact(
    tmp_path, reverse_groups
):
    spec, cycle, graph, _ = _task(
        tmp_path, {"type": "object", "required": ["measurement"]}
    )
    artifact = _register_submission(spec, cycle, {"measurement": 1})
    snapshot, payload, frozen = _snapshot(spec, cycle, graph)
    original_views = _asset_views(payload)
    assert len(original_views) == 2
    assert {row["path"] for row in original_views} == {str(artifact)}
    assert sum(bool(row.get("sha256")) for row in original_views) == 1
    if reverse_groups:
        frozen["material_index"]["materials"].reverse()

    result = check_frozen_submission(frozen, snapshot)
    assert result["status"] == "valid"
    assert (
        result["submission"]["sha256"]
        == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )

    # A weak compatibility view must never erase the stronger registered hash,
    # regardless of the order in which the two legitimate views are read.
    mismatched = copy.deepcopy(frozen)
    strong = next(row for row in _asset_views(mismatched) if row.get("sha256"))
    strong["sha256"] = "0" * 64
    invalid = check_frozen_submission(mismatched, snapshot)
    assert invalid["status"] == "invalid"
    assert not invalid["matching_artifacts"]
    assert any(
        "hash changed" in error["message"]
        for row in invalid["artifact_errors"]
        for error in row["errors"]
    )


@pytest.mark.parametrize(
    "reverse_groups", [False, True], ids=["pointer-first", "batch-first"]
)
def test_two_strong_hashes_for_one_registered_artifact_fail_closed(
    tmp_path, reverse_groups
):
    spec, cycle, graph, _ = _task(tmp_path, {"type": "object"})
    _register_submission(spec, cycle, {"measurement": 1})
    snapshot, _, frozen = _snapshot(spec, cycle, graph)
    pointer = next(row for row in _asset_views(frozen) if not row.get("sha256"))
    pointer["sha256"] = "0" * 64
    if reverse_groups:
        frozen["material_index"]["materials"].reverse()
    with pytest.raises(
        ValueError, match="Conflicting registered submission content hashes"
    ):
        check_frozen_submission(frozen, snapshot)
