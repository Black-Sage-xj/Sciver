from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.research_inputs import qualification_input_ids, registered_fixture_inputs, verified_research_inputs


def _spec(tmp_path, references=()):
    home = tmp_path / "task"
    benchmark = tmp_path / "benchmark"
    home.mkdir()
    benchmark.mkdir()
    required = ("benchmark/fixture_manifest.json", *references)
    return SimpleNamespace(task_home=home, benchmark_root=benchmark, document=home / "research_plan.md",
                           required_context="\n".join(f"- {ref}" for ref in required))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _row(ref, data, *, size_key="size"):
    return {"path": str(ref), size_key: len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _fixture(spec, rows, schema="sciver/private-scientific-fixture-manifest/v1"):
    (spec.benchmark_root / "fixture_manifest.json").write_text(json.dumps({"schema": schema, "files": rows}))


def _supplement(spec, data, context=(), schema="theorymaster/public-research-input-manifest/v1"):
    (spec.task_home / "input_manifest.json").write_text(json.dumps({
        "schema": schema, "data_files": data, "derived_context_files": list(context),
    }))


def test_fixture_requires_explicit_plan_registration_and_pinned_hash(tmp_path):
    spec = _spec(tmp_path, ("benchmark/inputs/baseline.csv", "benchmark/public_task.md",
                            "benchmark/provenance/receipt.json", "benchmark/source_manifest.json"))
    entries = {
        "inputs/baseline.csv": b"x,y\n1,2\n",
        "public_task.md": b"Neutral description\n",
        "provenance/receipt.json": b"{}\n",
        "source_manifest.json": b"{\"absolute_path\": \"/different/task\"}\n",
        "inputs/unlisted.csv": b"skip\n",
    }
    for ref, data in entries.items():
        _write(spec.benchmark_root / ref, data)
    _fixture(spec, [_row(f"benchmark/{ref}", data) for ref, data in entries.items()])

    found = verified_research_inputs(spec, include_text=True)

    assert found[0]["reference"] == "benchmark/public_task.md"
    assert found[0]["source_text"] == "Neutral description\n"
    assert {row["reference"]: row["kind"] for row in found} == {
        "benchmark/inputs/baseline.csv": "fixture_input", "benchmark/public_task.md": "provenance",
        "benchmark/provenance/receipt.json": "provenance", "benchmark/source_manifest.json": "provenance",
    }
    assert {row["manifest"] for row in found} == {"benchmark/fixture_manifest.json"}
    assert all("source_text" not in row for row in verified_research_inputs(spec))
    (spec.benchmark_root / "inputs/baseline.csv").write_bytes(b"x,y\n8,9\n")
    assert all(row["kind"] == "provenance" for row in verified_research_inputs(spec))


def test_existing_declaration_verifies_when_pinned_file_is_restored(tmp_path):
    spec = _spec(tmp_path, ("benchmark/inputs/trajectories.csv",))
    data = b"t,x\n0,1\n"
    _fixture(spec, [_row("benchmark/inputs/trajectories.csv", data)])
    manifest = (spec.benchmark_root / "fixture_manifest.json").read_bytes()

    assert verified_research_inputs(spec) == []

    path = _write(spec.benchmark_root / "inputs/trajectories.csv", data)
    assert (spec.benchmark_root / "fixture_manifest.json").read_bytes() == manifest
    assert verified_research_inputs(spec) == [{
        "reference": "benchmark/inputs/trajectories.csv", "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(), "status": "available", "size": len(data),
        "manifest": "benchmark/fixture_manifest.json", "kind": "fixture_input",
    }]


def test_typed_task_home_supplements_only_use_safe_local_artifacts(tmp_path):
    spec = _spec(tmp_path)
    data = b"a,b\n1,2\n"
    path = _write(spec.task_home / "inputs" / "measurement.csv", data)
    context = _write(spec.task_home / "public_context.md", b"Public method notes\n")
    outside = _write(tmp_path / "different-task.csv", data)
    (spec.task_home / "mirror.csv").symlink_to(outside)
    _write(spec.task_home / "expected" / "labels.csv", data)
    _supplement(spec, [_row(ref, data, size_key="bytes") for ref in
                       ("inputs/measurement.csv", outside, "../different-task.csv", "mirror.csv",
                        "expected/labels.csv", "missing.csv")],
                [_row(context, context.read_bytes(), size_key="bytes")])

    found = verified_research_inputs(spec)

    assert [(row["path"], row["kind"]) for row in found] == [
        (str(path), "supplemental_input"), (str(context), "provenance"),
    ]
    assert all(row["manifest"] == str(spec.task_home / "input_manifest.json") for row in found)


def test_retained_provenance_cannot_displace_registered_data_at_input_limit(tmp_path):
    fixture_reference = "benchmark/inputs/public_measurements.csv"
    spec = _spec(tmp_path, (fixture_reference,))
    fixture_bytes = b"t,x\n0,1\n"
    _write(spec.benchmark_root / "inputs/public_measurements.csv", fixture_bytes)
    fixture = _row(fixture_reference, fixture_bytes)
    _fixture(spec, [fixture])

    replay_bytes = b'{"status":"verified","replay_cpu_seconds":2}\n'
    replay_path = _write(spec.task_home / "notes/zz-replay/performance.json", replay_bytes)
    replay = _row(replay_path, replay_bytes, size_key="bytes")
    report_bytes = b"The replay records the public-data feasibility check without edge scores.\n"
    report_path = _write(replay_path.parent / "report.md", report_bytes)
    report = _row(report_path, report_bytes, size_key="bytes")
    replay_priority = frozenset({replay["sha256"]})
    retained = []
    for index in range(42):
        content = f"Prior source note {index}\n".encode()
        path = _write(spec.task_home / "notes/aa-retained" / f"note-{index:02}.md", content)
        retained.append(_row(path, content, size_key="bytes"))

    _supplement(spec, [replay], [*retained[:2], report])
    before_cap = verified_research_inputs(spec)
    assert [(row["reference"], row["kind"]) for row in before_cap] == [
        (retained[0]["path"], "provenance"),
        (retained[1]["path"], "provenance"),
        (str(replay_path), "supplemental_input"),
        (str(report_path), "provenance"),
        (fixture_reference, "fixture_input"),
    ]
    assert all("priority_context_for" not in row for row in before_cap)
    assert [row["reference"] for row in verified_research_inputs(spec, prioritize_sha256=replay_priority)] == [
        row["reference"] for row in before_cap
    ]
    data_rows = [row for row in before_cap if row["kind"] != "provenance"]

    _supplement(spec, [replay], [*retained, report])
    after_cap = verified_research_inputs(spec)
    assert len(after_cap) == 40
    assert [row for row in after_cap if row["kind"] != "provenance"] == data_rows
    assert all(row["reference"] != str(report_path) for row in after_cap)
    assert [row for row in verified_research_inputs(spec, supplemental_only=True)
            if row["kind"] != "provenance"] == [data_rows[0]]

    prioritized = verified_research_inputs(spec, include_text=True, prioritize_sha256=replay_priority)
    assert len(prioritized) == 40
    prioritized_by_reference = {row["reference"]: row for row in prioritized}
    assert prioritized_by_reference[str(report_path)] == {
        **before_cap[3], "priority_context_for": [replay["sha256"]],
        "source_text": report_bytes.decode("utf-8"),
    }


def test_unrecognized_and_conflicting_declarations_grant_no_input(tmp_path):
    spec = _spec(tmp_path, ("benchmark/inputs/data.csv", "benchmark/inputs%2Fdata.csv"))
    data = b"x\n1\n"
    _write(spec.benchmark_root / "inputs/data.csv", data)
    _write(spec.task_home / "data.csv", data)
    fixture = _row("benchmark/inputs/data.csv", data)
    supplement = _row("data.csv", data, size_key="bytes")
    _fixture(spec, [fixture], schema="unknown")
    _supplement(spec, [supplement], schema="sciver/research-input-manifest/v1")
    assert verified_research_inputs(spec) == []

    _fixture(spec, [fixture, _row("benchmark/inputs%2Fdata.csv", data)])
    _supplement(spec, [supplement, _row(spec.task_home / "data.csv", data, size_key="bytes")])
    assert verified_research_inputs(spec) == []


def test_inline_budget_favors_new_verified_input_and_direct_bundle_context(tmp_path):
    spec = _spec(tmp_path)
    historical = [
        _write(spec.task_home / "notes" / f"archive-{i}.md", str(i).encode() * 8_000)
        for i in range(3)
    ]
    bundle = spec.task_home / "notes" / "new-study"
    receipt = _write(bundle / "receipt.json", b"r" * 7_345)
    note = _write(bundle / "qualification.md", b"n" * 1_762)
    probe = _write(bundle / "probe.py", b"p" * 9_331)
    non_utf8 = _write(bundle / "undecodable.md", b"\xff" * 4_001)
    other = _write(spec.task_home / "notes" / "other-study" / "qualification.md", b"o" * 9_331)
    nested = _write(bundle / "nested" / "qualification.md", b"x" * 9_331)
    drifted = _write(bundle / "changed.md", b"not the registered content")
    data = _row(receipt, receipt.read_bytes(), size_key="bytes")
    derived = [_row(path, path.read_bytes(), size_key="bytes") for path in
               (*historical, note, probe, non_utf8, other, nested)]
    _supplement(spec, [data], [*derived, _row(drifted, b"registered", size_key="bytes")])

    default = verified_research_inputs(spec, include_text=True)
    current = verified_research_inputs(spec, include_text=True, prioritize_sha256=frozenset({data["sha256"]}))

    def plain(item):
        return {key: value for key, value in item.items() if key not in {"source_text", "priority_context_for"}}

    assert list(map(plain, current)) == list(map(plain, default))
    assert {row["path"] for row in default if "source_text" in row} == {str(path) for path in historical}
    assert {row["path"] for row in current if "source_text" in row} == {str(receipt), str(note), str(probe)}
    assert {row["path"]: row["priority_context_for"] for row in current if "priority_context_for" in row} == {
        str(note): [data["sha256"]], str(probe): [data["sha256"]], str(non_utf8): [data["sha256"]],
    }
    assert sum(len(row.get("source_text", "").encode()) for row in current) <= 24_000

    receipt.write_bytes(b"unverified new bytes")
    assert verified_research_inputs(spec, include_text=True, prioritize_sha256=frozenset({data["sha256"]})) == (
        verified_research_inputs(spec, include_text=True)
    )


def test_top_level_supplement_folder_does_not_create_context_bundle(tmp_path):
    spec = _spec(tmp_path)
    path = _write(spec.task_home / "notes" / "new.txt", b"new")
    sibling = _write(spec.task_home / "notes" / "old.md", b"old")
    data = _row(path, path.read_bytes(), size_key="bytes")
    _supplement(spec, [data], [_row(sibling, sibling.read_bytes(), size_key="bytes")])

    found = verified_research_inputs(spec, include_text=True, prioritize_sha256=frozenset({data["sha256"]}))
    assert all("priority_context_for" not in row for row in found)


def test_qualification_and_investigator_receive_the_same_verified_supplements(tmp_path, monkeypatch):
    from tests.test_literature_request_exhaustion import make_workflow

    workflow, backend, _ = make_workflow(tmp_path, set())
    workflow.stop_before = None
    spec = workflow.spec
    plan = spec.document.read_bytes()
    data = _write(spec.task_home / "inputs" / "public_trajectories.csv", b"t,x\n0,1\n")
    note = _write(spec.task_home / "review_notes.md", b"Suggested comparison to assess independently.\n")
    invalid = _write(spec.task_home / "unverified.csv", b"wrong bytes\n")
    _supplement(spec, [_row(data, data.read_bytes(), size_key="bytes"),
                       _row(invalid, b"other bytes\n", size_key="bytes")],
                [_row(note, note.read_bytes(), size_key="bytes")])
    prompts = {}
    invoke = backend.invoke

    def capture(role, prompt, thread_id, model, provider):
        if any(f"Current task: {stage}" in prompt for stage in ("frontier_proposal", "candidate_review", "investigate")):
            prompts[role] = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
        return invoke(role, prompt, thread_id, model, provider)

    monkeypatch.setattr(backend, "invoke", capture)
    result = workflow.run()

    assert result["status"] == "over"
    lead = prompts["research_lead"]["supplemental_research_inputs"]
    assert prompts["challenger"]["supplemental_research_inputs"] == lead
    assert prompts["investigator"]["supplemental_research_inputs"] == lead
    inputs = {row["kind"]: row for row in lead["sources"]}
    assert inputs["supplemental_input"]["path"] == str(data)
    assert inputs["provenance"]["source_text"] == note.read_text()
    assert all(row["path"] != str(invalid) for row in lead["sources"])
    assert spec.document.read_bytes().split(b"# Scientific Model", 1)[0] == plan.split(b"# Scientific Model", 1)[0]


def test_registered_fixture_inventory_binds_paths_and_includes_unavailable_pins(tmp_path):
    references = (
        "benchmark/inputs/available.json", "benchmark/inputs/missing.csv",
        "benchmark/inputs/changed.csv", "benchmark/inputs/linked.csv",
        "benchmark/source_notes.md", "benchmark/expected/answer.csv",
    )
    spec = _spec(tmp_path, references)
    data = b'{"never_inline_this": "fixture bytes"}'
    existing = _write(spec.benchmark_root / "inputs/available.json", data)
    (spec.benchmark_root / ".ignore").write_text("inputs/available.json\n")
    changed = _write(spec.benchmark_root / "inputs/changed.csv", b"changed")
    (spec.benchmark_root / "inputs/linked.csv").symlink_to(existing)
    all_references = (*references, "benchmark/inputs/undeclared.csv")
    _fixture(spec, [_row(ref, data) for ref in all_references])

    inventory = registered_fixture_inputs(spec)

    assert [(row["reference"], row["status"]) for row in inventory] == [
        (references[0], "available"), (references[2], "unavailable"),
        (references[3], "unavailable"), (references[1], "unavailable"),
    ]
    assert inventory[0]["path"] == str(existing)
    assert inventory[1]["path"] == str(changed)
    assert all(set(row) == {"reference", "path", "sha256", "status", "size"} for row in inventory)
    assert "never_inline_this" not in json.dumps(inventory)
    assert all(row["sha256"] == hashlib.sha256(data).hexdigest() for row in inventory)


def test_restored_interface_stub_stays_available_without_scientific_wake(tmp_path):
    from theory_master.supervisor_input_support import _available_input_ids
    from theory_master.worker_direction import direction_changed, research_direction_fingerprint

    reference = "benchmark/inputs/contract.py"
    sources = "benchmark/source_manifest.json"
    spec = _spec(tmp_path, (reference, sources))
    spec.research_root = spec.task_home
    spec.graph_database = spec.task_home / "science_graph.sqlite3"
    spec.document.write_text("# Research Plan\n")
    template = (b'"""Public interface."""\nfrom __future__ import annotations\nimport numpy as np\n'
                b'def reconstruct(hits: np.ndarray) -> np.ndarray:\n'
                b'    """Reconstruct observations."""\n    raise NotImplementedError\n')
    template_path = spec.benchmark_root / "inputs/contract.py"
    template_pin = _row(reference, template)
    prior_source = {"relative_path": reference, "bytes": len(template), "sha256": template_pin["sha256"],
                    "path": "/prior/task/contract.py", "role": "public_interface_template", "verified": True}

    def pin_source(row):
        content = json.dumps({"schema": "sciver-local-public-input-manifest/v1", "files": [row]}).encode()
        _write(spec.benchmark_root / "source_manifest.json", content)
        return _row(sources, content)

    _fixture(spec, [template_pin, pin_source(prior_source)])
    context = []
    for index in range(42):
        path = _write(spec.task_home / "notes" / f"prior-{index}.md", f"Prior note {index}\n".encode())
        context.append(_row(path, path.read_bytes(), size_key="bytes"))
    _supplement(spec, [], context)
    waiting = research_direction_fingerprint(spec)
    assert registered_fixture_inputs(spec)[0]["status"] == "unavailable"

    _write(template_path, template)
    visible = verified_research_inputs(spec, include_text=True)
    template_row = next(row for row in visible if row["reference"] == reference)
    assert template_row["kind"] == "fixture_input" and template_row["source_text"] == template.decode()
    assert all(row["reference"] != sources for row in visible)  # Role validation is independent of the context cap.
    assert registered_fixture_inputs(spec)[0]["status"] == "available"
    assert _available_input_ids(spec) == [template_pin["sha256"]]
    assert research_direction_fingerprint(spec) == waiting

    copy = _write(spec.task_home / "inputs/registered-comparison.py", template)
    _supplement(spec, [_row(copy, template, size_key="bytes")], context)
    assert qualification_input_ids(spec) == [template_pin["sha256"]]
    assert direction_changed(waiting, research_direction_fingerprint(spec))

    _supplement(spec, [], context)
    executable = template.replace(b"raise NotImplementedError", b"return np.asarray(hits)")
    _write(template_path, executable)
    executable_pin = _row(reference, executable)
    _fixture(spec, [executable_pin, pin_source({**prior_source, "bytes": len(executable), "sha256": executable_pin["sha256"]})])
    assert qualification_input_ids(spec) == [executable_pin["sha256"]]
    assert direction_changed(waiting, research_direction_fingerprint(spec))


@pytest.mark.parametrize("defect", ["unregistered_manifest", "unverified_manifest", "wrong_role", "foreign_pin",
                                    "duplicate_role", "wrong_schema", "executable_signature", "executable_module"])
def test_interface_stub_suppression_requires_exact_evidence(tmp_path, defect):
    reference = "benchmark/inputs/solution_template.py"
    sources = "benchmark/source_manifest.json"
    spec = _spec(tmp_path, (reference,) if defect == "unregistered_manifest" else (reference, sources))
    template = b"def reconstruct(hits):\n    raise NotImplementedError('implementation requested')\n"
    if defect == "executable_signature":
        template = template.replace(b"(hits)", b"(hits=load_calibration())")
    elif defect == "executable_module":
        template = b"CALIBRATION = load_calibration()\n" + template
    template_pin = _row(reference, template)
    _write(spec.benchmark_root / "inputs/solution_template.py", template)
    row = {"relative_path": reference, "bytes": len(template), "sha256": template_pin["sha256"],
           "role": "public_interface_template", "verified": True}
    if defect == "wrong_role":
        row["role"] = "public_baseline"
    elif defect == "foreign_pin":
        row["sha256"] = "a" * 64
    rows = [row, {**row, "role": "public_baseline"}] if defect == "duplicate_role" else [row]
    schema = "unrecognized" if defect == "wrong_schema" else "sciver-local-public-input-manifest/v1"
    source_bytes = json.dumps({"schema": schema, "files": rows}).encode()
    _write(spec.benchmark_root / "source_manifest.json", source_bytes)
    source_pin = _row(sources, source_bytes + b"\n" if defect == "unverified_manifest" else source_bytes)
    _fixture(spec, [template_pin, source_pin])

    assert qualification_input_ids(spec) == [template_pin["sha256"]]
    assert registered_fixture_inputs(spec)[0]["status"] == "available"


def test_qualification_and_locked_native_setup_receive_scoped_fixture_inventory(tmp_path):
    from tests.test_native_goal_quota_auto_adjust_20261010 import GoalQuotaBackend, NativeGoalQuotaTests
    from tests.test_repair_verification_state_machine_20261022 import RepairFixtureBackend
    from theory_master.specification import TaskSpec
    from theory_master.workflow import Workflow

    qualification_stages = ("frontier_proposal", "candidate_review", "selection_repair", "repair_verification")

    class CaptureNativeSetup(GoalQuotaBackend):
        def __init__(self):
            super().__init__("usageLimited")
            self.fake = RepairFixtureBackend()
            self.contexts = {}
            self.setup = None

        def invoke(self, role, prompt, thread_id, model, provider):
            for stage in qualification_stages:
                if f"Current task: {stage} " in prompt:
                    self.contexts[stage], _ = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])
                    break
            return super().invoke(role, prompt, thread_id, model, provider)

        def invoke_goal(self, role, **kwargs):
            self.setup = kwargs["context_prompt"]
            return super().invoke_goal(role, **kwargs)

    seed = NativeGoalQuotaTests().make_spec(tmp_path)
    config = json.loads(seed.config_path.read_text())
    benchmark = Path(config["benchmark_root"])
    reserved = b'{"label":"held_fixture_numeric_label_never_inline"}'
    reference = "benchmark/inputs/validation.json"
    alias = "benchmark/inputs/fixture-alias.json"
    available = "benchmark/inputs/observations.json"
    missing = "benchmark/inputs/missing.csv"
    public = b'{"observations":"public_fixture_bytes_never_inline"}'
    for ref, data in ((reference, reserved), (alias, reserved), (available, public)):
        _write(benchmark / ref.removeprefix("benchmark/"), data)
    rows = [_row(ref, data) for ref, data in ((reference, reserved), (alias, reserved),
                                              (available, public), (missing, b"t,x\n0,1\n"))]
    (benchmark / "fixture_manifest.json").write_text(json.dumps({
        "schema": "sciver/private-scientific-fixture-manifest/v1", "files": rows,
    }))
    text = seed.document.read_text()
    declarations = "- benchmark/fixture_manifest.json\n" + "".join(f"- {row['path']}\n" for row in rows)
    seed.document.write_text(text.replace("- fixture\n", declarations, 1))
    config["prequalification_holdout"] = {"files": [{"reference": reference, "sha256": rows[0]["sha256"]}]}
    seed.config_path.write_text(json.dumps(config))
    spec = TaskSpec.from_config(seed.config_path)
    backend = CaptureNativeSetup()
    result = Workflow(spec, backend).run()

    assert result["status"] == "paused"
    assert result["hypothesis_lock"]["status"] == "locked"
    assert tuple(backend.contexts) == qualification_stages
    inventory = {
        row["path"]: {"reference": row["path"], "path": str(benchmark / row["path"].removeprefix("benchmark/")),
                      "size": row["size"], "sha256": row["sha256"],
                      "status": "unavailable" if row["path"] == missing else "available"}
        for row in rows
    }
    for projected in backend.contexts.values():
        assert projected["registered_fixture_inputs"] == [inventory[missing], inventory[available]]
        encoded = json.dumps(projected)
        assert inventory[reference]["path"] not in encoded
        assert inventory[alias]["path"] not in encoded
        assert "held_fixture_numeric_label_never_inline" not in encoded
        assert "public_fixture_bytes_never_inline" not in encoded
        assert projected["prequalification_holdout"]["files"][0]["status"] == "verified_reserved"
    context, _ = json.JSONDecoder().raw_decode(backend.setup)
    assert context["registered_fixture_inputs"] == [inventory[ref] for ref in sorted(inventory)]
    assert "held_fixture_numeric_label_never_inline" not in backend.setup
    assert "public_fixture_bytes_never_inline" not in backend.setup
    assert "A directory listing filtered by ignore rules is insufficient evidence of absence." in backend.setup
    assert "input_support_audit:" in backend.setup
