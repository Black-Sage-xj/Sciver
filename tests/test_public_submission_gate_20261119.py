"""Public delivery structure is machine checked without scientific answer synthesis."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.public_submission import (
    SCHEMA,
    load_submission_contract,
    strict_json,
)


def stage(prompt):
    return prompt.split("Current task: ", 1)[1].split(" ", 1)[0]


def context(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]


def make_spec(root, *, reworks=1, declared=True):
    from theory_master.task import bootstrap

    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\n\nComplete deterministic fixture.\n\n# Research Contract\n\nUse only task-local inputs.\n\n# Final Gate\n\nThe deterministic scientific result and public delivery exist.\n\n# Required Context\n\n- benchmark/submission.json\n"
    )
    binding = bootstrap(plan)
    spec = TaskSpec.from_config(binding.config_path)
    benchmark = Path(json.loads(spec.config_path.read_text())["benchmark_root"])
    benchmark.mkdir(parents=True, exist_ok=True)
    contract = {
        "schema": SCHEMA,
        "json_schema": {
            "type": "object",
            "required": ["estimate", "procedure", "check"],
            "properties": {
                "estimate": {"type": "number"},
                "procedure": {"type": "string", "minLength": 1},
                "check": {"type": "string", "minLength": 1},
            },
        },
    }
    (benchmark / "submission.json").write_text(json.dumps(contract))
    config = json.loads(spec.config_path.read_text())
    config["max_cycles"] = 1
    config["max_judge_reworks_per_cycle"] = reworks
    config["benchmark_root"] = str(benchmark)
    if declared:
        config["submission_contract"] = "benchmark/submission.json"
    spec.config_path.write_text(json.dumps(config))
    return TaskSpec.from_config(spec.config_path)


class AuthorBackend(FakeBackend):
    def __init__(self, *, initial_valid=False, repair_valid=True, mutate=None):
        super().__init__(reworks=0)
        self.cwd = None
        self.initial_valid, self.repair_valid, self.mutate = (
            initial_valid,
            repair_valid,
            mutate,
        )
        self.calls = []
        self.prompts = []
        self.goal_calls = 0
        self.investigator_threads = []
        self.original = None
        self.repair_hook = None

    def invoke(self, role, prompt, thread_id, model, provider):
        current, inputs = stage(prompt), context(prompt)
        self.calls.append((role, current))
        if role == "investigator":
            self.prompts.append(prompt)
            self.investigator_threads.append(thread_id)
            correction = bool(inputs.get("submission_delivery"))
            valid = self.repair_valid if correction else self.initial_valid
            value = (
                {
                    "estimate": 2.5,
                    "procedure": "retained derivation",
                    "check": "retained check",
                }
                if valid
                else {"fit": [2.5]}
            )
            name = "authored_submission.json" if correction else "initial_result.json"
            (Path(self.cwd) / name).write_text(json.dumps(value))
            if not correction:
                self.original = (Path(self.cwd) / name).read_bytes()
            if self.repair_hook and correction:
                self.repair_hook()
        if role == "judge" and current == "goal_completion":
            self.goal_calls += 1
            if self.mutate:
                self.mutate(inputs)
            return str(thread_id), json.dumps(
                {"verdict": "passed", "rationale": "offline fixture complete"}
            )
        return super().invoke(role, prompt, thread_id, model, provider)


def test_missing_shape_is_corrected_by_original_investigator_then_sealed(tmp_path):
    spec = make_spec(tmp_path / "task")
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert state["goal_status"] == "passed", state.get("error")
    assert state["scientific_verdict"] == "supported"
    assert backend.goal_calls == 1
    assert len(backend.prompts) == 2
    assert len(set(backend.investigator_threads)) == 1
    correction = context(backend.prompts[-1])
    assert correction["public_submission"]["json_schema"]["required"] == [
        "estimate",
        "procedure",
        "check",
    ]
    assert (
        correction["submission_delivery"]["schema_mismatch"]["code"]
        == "registered_submission_schema_absent"
    )
    assert "original cumulative token allowance" in backend.prompts[-1]
    assert (
        workflow.cycle_workspace / "initial_result.json"
    ).read_bytes() == backend.original
    assert state["submission_delivery_repair_count"] == 1
    assert state["submission_delivery_attempt"]["status"] == "completed"
    assert "goal_submission_gap" not in state
    receipt = json.loads(Path(state["goal_completion"]["receipt_path"]).read_text())
    assert receipt["submission_validation"]["status"] == "valid"
    assert receipt["submission_validation"]["submission"]["path"].endswith(
        "authored_submission.json"
    )
    assert sum(role == "reporter" for role, _ in backend.calls) == 1
    assert (
        sum(role == "judge" and s == "terminal_assessment" for role, s in backend.calls)
        == 1
    )


def test_python_delivery_uses_original_thread_and_frozen_structure_only(tmp_path):
    spec = make_spec(tmp_path / "task")
    declaration = {
        "schema": "theory-master/public-python-submission-contract/v1",
        "filename": "solution.py",
        "callable": {"name": "solve", "parameters": ["x", "y"]},
        "allowed_imports": ["numpy"],
    }
    (spec.benchmark_root / "submission.json").write_text(json.dumps(declaration))
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    initial_source = "def solve(x):\n    return x\n"
    supplemental_source = (
        "def solve(x, y):\n"
        "    raise AssertionError('structural verification must not call the entrypoint')\n"
    )
    invoke = backend.invoke

    def write_python(role, prompt, *args):
        if role == "investigator":
            workspace = Path(backend.cwd)
            if context(prompt).get("submission_delivery"):
                (workspace / "supplemental").mkdir()
                (workspace / "supplemental" / "solution.py").write_text(supplemental_source)
            else:
                (workspace / "solution.py").write_text(initial_source)
        return invoke(role, prompt, *args)

    with patch.object(backend, "invoke", side_effect=write_python):
        state = workflow.run()

    assert state["goal_status"] == "passed", state.get("error")
    assert state["scientific_verdict"] == "supported"
    assert len(backend.prompts) == 2
    assert len(set(backend.investigator_threads)) == 1
    assert backend.goal_calls == 1
    assert state["submission_delivery_repair_count"] == 1
    assert state["submission_delivery_attempt"]["status"] == "completed"
    correction = context(backend.prompts[-1])
    feedback = correction["submission_delivery"]
    assert feedback["format"] == "python"
    assert correction["public_submission"] == feedback["frozen_material_access"]["submission_contract"]
    assert correction["public_submission"]["callable"] == declaration["callable"]
    assert correction["public_submission"]["allowed_imports"] == ["numpy"]
    assert "supplemental Python artifact named `solution.py`" in feedback["instruction"]
    assert "This stage checks structure only." in backend.prompts[-1]
    assert "Do not execute the artifact" in feedback["instruction"]
    assert "supplemental JSON artifact" not in backend.prompts[-1]
    assert (workflow.cycle_workspace / "solution.py").read_text() == initial_source
    receipt = json.loads(Path(state["goal_completion"]["receipt_path"]).read_text())
    validation = receipt["submission_validation"]
    assert validation["verification"] == "python_ast_structure"
    assert validation["submission"]["path"].endswith("supplemental/solution.py")


def test_delivery_rejects_an_unknown_frozen_format_before_dispatch():
    from theory_master.submission_rework import correct_submission_delivery
    from theory_master.workflow_support import RecoveryStop

    owner = SimpleNamespace(
        _check_recovery_control=lambda state: None,
        _emit=lambda *args: None,
        _persist=lambda state: None,
    )
    state = {"goal_submission_gap": {"validation": {"contract_sha256": "pinned"}}}
    snapshot = SimpleNamespace(access={"submission_contract": {"format": "unknown", "sha256": "pinned"}})
    with pytest.raises(RecoveryStop, match="does not authorize this correction format"):
        correct_submission_delivery(owner, state, snapshot)
    assert state["attention"]["code"] == "public_submission_incomplete"
    assert "submission_delivery_repair_count" not in state


def test_valid_authored_delivery_has_no_extra_turn(tmp_path):
    spec = make_spec(tmp_path / "task")
    backend = AuthorBackend(initial_valid=True)
    state = Workflow(spec, backend).run()
    assert state["goal_status"] == "passed"
    assert len(backend.prompts) == 1
    assert backend.goal_calls == 1
    assert not state.get("submission_delivery_repair_count")


@pytest.mark.parametrize("limit", [0, 1])
def test_exhausted_actual_config_holds_without_goal_or_supervisor_call(tmp_path, limit):
    spec = make_spec(tmp_path / "task", reworks=limit)
    backend = AuthorBackend(repair_valid=False)
    workflow = Workflow(spec, backend)
    with patch.object(workflow, "supervisor_enabled", return_value=True):
        state = workflow.run()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["scientific_verdict"] == "supported"
    assert state["goal_status"] != "passed"
    assert state["attention"]["code"] == "public_submission_incomplete"
    assert state["goal_submission_gap"]["validation"]["artifact_errors"]
    assert len(backend.prompts) == 1 + limit
    assert backend.goal_calls == 0
    assert not any(role == "supervisor" for role, _ in backend.calls)
    assert state["supervisor_attention_review"]["action"] == "hold"
    assert (
        state["submission_delivery_repair_count"] == limit
        if limit
        else not state.get("submission_delivery_repair_count")
    )


def test_without_explicit_contract_preserves_legacy_gate(tmp_path):
    spec = make_spec(tmp_path / "task", declared=False)
    backend = AuthorBackend()
    state = Workflow(spec, backend).run()
    assert state["goal_status"] == "passed"
    assert len(backend.prompts) == 1
    assert (
        "public_submission" not in context(backend.prompts[0])
        or context(backend.prompts[0])["public_submission"] is None
    )


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "1e999", '{"x":1,"x":2}']
)
def test_standard_json_only(value):
    with pytest.raises(ValueError):
        strict_json(value)


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.org/schema.json",
        "../secret.json",
        "benchmark/../secret.json",
        "/tmp/schema.json",
    ],
)
def test_contract_requires_explicit_safe_benchmark_reference(tmp_path, reference):
    with pytest.raises(ValueError):
        load_submission_contract(
            SimpleNamespace(submission_contract_ref=reference, benchmark_root=tmp_path)
        )


def test_no_root_no_symlink_no_remote_schema(tmp_path):
    source = tmp_path / "contract.json"
    source.write_text(
        json.dumps(
            {"schema": SCHEMA, "json_schema": {"$ref": "https://example.org/never"}}
        )
    )
    with pytest.raises(ValueError, match="explicit safe"):
        load_submission_contract(
            SimpleNamespace(
                submission_contract_ref="benchmark/contract.json", benchmark_root=None
            )
        )
    with patch(
        "socket.create_connection", side_effect=AssertionError("network forbidden")
    ):
        with pytest.raises(ValueError, match="in-document"):
            load_submission_contract(
                SimpleNamespace(
                    submission_contract_ref="benchmark/contract.json",
                    benchmark_root=tmp_path,
                )
            )
    source.write_text(json.dumps({"schema": SCHEMA, "json_schema": {"type": "object"}}))
    (tmp_path / "alias.json").symlink_to(source)
    with pytest.raises(ValueError, match="Unsafe"):
        load_submission_contract(
            SimpleNamespace(
                submission_contract_ref="benchmark/alias.json", benchmark_root=tmp_path
            )
        )


@pytest.mark.parametrize("which", ["source", "frozen", "contract"])
def test_goal_cas_rejects_content_drift_after_successful_judge(tmp_path, which):
    spec = make_spec(tmp_path / "task")

    def mutate(inputs):
        access = inputs["delivery_material_access"]
        if which == "source":
            path = next(spec.research_root.glob("*/workspace/initial_result.json"))
        elif which == "contract":
            path = Path(spec.benchmark_root) / "submission.json"
        else:
            path = Path(access["submission_contract"]["path"])
        path.chmod(0o644)
        path.write_text("{}")

    backend = AuthorBackend(initial_valid=True, mutate=mutate)
    state = Workflow(spec, backend).run()
    assert state.get("goal_status") != "passed"
    assert state["goal_assessment_stale"]["reason"]


def test_schema_const_path_is_opaque_and_alias_is_frozen(tmp_path):
    spec = make_spec(tmp_path / "task")
    declaration_path = Path(spec.benchmark_root) / "submission.json"
    document = json.loads(declaration_path.read_text())
    document["json_schema"]["properties"]["procedure"] = {"type": "string"}
    document["json_schema"]["properties"]["extra"] = {
        "const": {"path": "scientific-source.csv"}
    }
    declaration_path.write_text(json.dumps(document))
    backend = AuthorBackend(initial_valid=True)
    state = Workflow(spec, backend).run()
    assert state["goal_status"] == "passed", state.get("error")
    snapshot = json.loads(
        Path(state["goal_completion"]["snapshot_manifest"]).read_text()
    )
    access = json.loads(
        (
            Path(state["goal_completion"]["snapshot_manifest"]).parent
            / "delivery_revision.json"
        ).read_text()
    )
    assert access["submission_contract"]["json_schema"] == document["json_schema"]
    assert (
        hashlib.sha256(
            Path(access["submission_contract"]["path"]).read_bytes()
        ).hexdigest()
        == access["submission_contract"]["sha256"]
    )
    assert snapshot["files"]


def test_resume_delivery_gap_only_reenters_goal_without_rewriting_science(tmp_path):
    from theory_master.submission_rework import _stop, submission_takeover_preflight

    spec = make_spec(tmp_path / "task")
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    original_call = workflow._call
    paused = False

    def pause_once(state, role, inputs, **kwargs):
        nonlocal paused
        if role == "investigator" and inputs.get("submission_delivery") and not paused:
            paused = True
            _stop(workflow, state, "offline pause before original-thread delivery Turn")
        return original_call(state, role, inputs, **kwargs)

    with patch.object(workflow, "_call", side_effect=pause_once):
        state = workflow.run()
    assert state["status"] == "needs_attention"
    assert state["scientific_phase"] == "committed"
    assert state["submission_delivery_repair_count"] == 1
    cycle = workflow.cycle_dir
    persisted = (cycle / "research_state.json").read_bytes()
    assert submission_takeover_preflight(cycle, state)["status"] == "ready"
    assert (cycle / "research_state.json").read_bytes() == persisted
    preserved = {
        path: path.read_bytes()
        for path in cycle.rglob("*")
        if path.is_file()
        and (
            path.name
            in {
                "report.md",
                "scientific_model.json",
                "scientific_synthesis_out.json",
                "judge_out.json",
            }
            or path.suffix == ".md"
        )
    }
    assert preserved
    calls = list(backend.calls)
    resumed = Workflow(spec, backend)
    with (
        patch.object(
            resumed,
            "_investigate_and_judge",
            side_effect=AssertionError("science repeated"),
        ),
        patch.object(
            resumed,
            "_scientific_synthesis_commit",
            side_effect=AssertionError("model rewritten"),
        ),
        patch.object(
            resumed,
            "_maintain_frontier_state",
            side_effect=AssertionError("frontier repeated"),
        ),
        patch.object(
            resumed,
            "_write_cycle_report",
            side_effect=AssertionError("report rewritten"),
        ),
    ):
        state = resumed.resume(str(spec.master_id))
    assert state["goal_status"] == "passed", state.get("error")
    assert state["submission_delivery_repair_count"] == 1
    assert len(backend.prompts) == 2
    assert len(set(backend.investigator_threads)) == 1
    assert backend.calls[len(calls) :] == [
        ("investigator", "investigate"),
        ("judge", "goal_completion"),
    ]
    assert all(path.read_bytes() == content for path, content in preserved.items())


def test_resume_exhausted_gap_cannot_buy_another_correction(tmp_path):
    from theory_master.submission_rework import submission_takeover_preflight

    spec = make_spec(tmp_path / "task", reworks=1)
    backend = AuthorBackend(repair_valid=False)
    workflow = Workflow(spec, backend)
    state = workflow.run()
    assert state["submission_delivery_repair_count"] == 1
    assert submission_takeover_preflight(workflow.cycle_dir, state)["code"] == "submission_corrections_exhausted"
    count = len(backend.calls)
    state = Workflow(spec, backend).resume(str(spec.master_id))
    assert state["status"] == "needs_attention", state.get("error")
    assert state["submission_delivery_repair_count"] == 1
    assert len(backend.calls) == count
    assert state["goal_status"] != "passed"


@pytest.mark.parametrize("drift", [False, True])
def test_supervisor_gate_only_resume_spends_no_extra_correction(tmp_path, drift):
    from theory_master import submission_rework

    spec = make_spec(tmp_path / "task", reworks=2)
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    correct = submission_rework.correct_submission_delivery

    def stop_after_authoring(owner, state, snapshot, **kwargs):
        correct(owner, state, snapshot, **kwargs)
        submission_rework._stop(owner, state, "offline interruption after registration")

    with patch.object(submission_rework, "correct_submission_delivery", side_effect=stop_after_authoring):
        state = workflow.run()
    cycle = workflow.cycle_dir
    assert state["submission_delivery_repair_count"] == 1
    if not drift:
        state["investigator_budget"] = {"exhausted": True}
        (cycle / "research_state.json").write_text(json.dumps(state))
    assert submission_rework.submission_takeover_preflight(cycle, state)["status"] == "ready_for_goal"
    calls = len(backend.calls)
    resumed = Workflow(spec, backend)
    goal_gate = resumed._run_goal_completion

    def change_after_preflight(*args, **kwargs):
        if drift:
            (cycle / "workspace/authored_submission.json").unlink()
        return goal_gate(*args, **kwargs)

    with patch.object(resumed, "_run_goal_completion", side_effect=change_after_preflight):
        state = resumed.resume(str(spec.master_id), supervisor_takeover=True)

    assert state["submission_delivery_repair_count"] == 1
    assert resumed.cycle_dir == cycle
    if drift:
        assert state["attention"]["code"] == "public_submission_incomplete"
        assert backend.calls[calls:] == []
    else:
        assert state["goal_status"] == "passed"
        assert backend.calls[calls:] == [("judge", "goal_completion")]


def test_supervisor_preflight_declines_changed_submission_receipt(tmp_path):
    from theory_master.submission_rework import submission_takeover_preflight

    spec = make_spec(tmp_path / "task", reworks=0)
    workflow = Workflow(spec, AuthorBackend())
    state = workflow.run()
    receipt = Path(state["goal_submission_gap"]["receipt_path"])
    content = json.loads(receipt.read_text())
    content["source_cycle"] = "other-cycle"
    receipt.write_text(json.dumps(content))

    result = submission_takeover_preflight(workflow.cycle_dir, state, spec=spec)
    assert result["status"] == "hold"
    assert result["code"] == "submission_authority_unverified"


def test_valid_but_unregistered_workspace_json_cannot_satisfy_gate(tmp_path):
    spec = make_spec(tmp_path / "task", reworks=0)
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    original = workflow._write_cycle_report

    def late_unregistered(*args, **kwargs):
        result = original(*args, **kwargs)
        (workflow.cycle_workspace / "not_registered.json").write_text(
            json.dumps(
                {"estimate": 2.5, "procedure": "author text", "check": "author check"}
            )
        )
        return result

    with patch.object(workflow, "_write_cycle_report", side_effect=late_unregistered):
        state = workflow.run()
    assert state["status"] == "needs_attention"
    assert state["goal_status"] != "passed"
    assert backend.goal_calls == 0
    assert not any(
        "not_registered" in row["path"]
        for row in state["goal_submission_gap"]["validation"]["artifact_errors"]
    )


def test_correction_preserves_completed_native_goal_identity_and_scientific_files(
    tmp_path,
):
    spec = make_spec(tmp_path / "task")
    backend = AuthorBackend()
    workflow = Workflow(spec, backend)
    original = workflow._write_cycle_report
    identity = {
        "status": "complete",
        "thread_id": "native-original",
        "goal_id": "original-goal",
        "tokens_used": 123,
        "token_budget": 1000,
        "origin_identity": {"objective_sha256": "f" * 64},
    }

    def add_original_goal(state, *args, **kwargs):
        result = original(state, *args, **kwargs)
        state["native_goal"] = json.loads(json.dumps(identity))
        return result

    with patch.object(workflow, "_write_cycle_report", side_effect=add_original_goal):
        state = workflow.run()
    assert state["goal_status"] == "passed", state.get("error")
    assert state["native_goal"] == identity
    assert len(backend.prompts) == 2
    assert (
        workflow.cycle_workspace / "initial_result.json"
    ).read_bytes() == backend.original


def test_during_author_correction_old_science_content_cannot_be_replaced(tmp_path):
    spec = make_spec(tmp_path / "task")
    backend = AuthorBackend()
    backend.repair_hook = lambda: (
        Path(backend.cwd) / "initial_result.json"
    ).write_text("{}")
    state = Workflow(spec, backend).run()
    assert state["status"] == "needs_attention", state.get("error")
    assert "original frozen scientific file changed" in state["attention"]["reason"]
    assert state["scientific_verdict"] == "supported"
    assert backend.goal_calls == 0
    count = len(backend.calls)
    resumed = Workflow(spec, backend).resume(str(spec.master_id))
    assert resumed["status"] == "needs_attention"
    assert resumed["goal_status"] != "passed"
    assert "retained submission snapshot" in resumed["attention"]["reason"]
    assert len(backend.calls) == count


def test_ambiguous_registered_json_cannot_be_arbitrarily_selected(tmp_path):
    spec = make_spec(tmp_path / "task", reworks=0)

    class TwoAuthorFiles(AuthorBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            result = super().invoke(role, prompt, thread_id, model, provider)
            if role == "investigator":
                path = Path(self.cwd) / "second_result.json"
                path.write_bytes((Path(self.cwd) / "initial_result.json").read_bytes())
            return result

    backend = TwoAuthorFiles(initial_valid=True)
    state = Workflow(spec, backend).run()
    assert state["status"] == "needs_attention"
    assert state["goal_submission_gap"]["code"] == "registered_submission_ambiguous"
    assert len(state["goal_submission_gap"]["validation"]["matching_artifacts"]) == 2
    assert backend.goal_calls == 0
    assert state["goal_status"] != "passed"


def test_nonstandard_nan_in_registered_json_never_reaches_goal_judge(tmp_path):
    spec = make_spec(tmp_path / "task", reworks=0)

    class NonstandardAuthor(AuthorBackend):
        def invoke(self, role, prompt, thread_id, model, provider):
            result = super().invoke(role, prompt, thread_id, model, provider)
            if role == "investigator":
                (Path(self.cwd) / "initial_result.json").write_text(
                    '{"estimate":NaN,"procedure":"M","check":"C"}'
                )
            return result

    backend = NonstandardAuthor(initial_valid=True)
    state = Workflow(spec, backend).run()
    assert state["status"] == "needs_attention"
    assert backend.goal_calls == 0
    assert "Non-standard JSON" in json.dumps(
        state["goal_submission_gap"]["validation"]["artifact_errors"]
    )


def test_loader_uses_explicit_root_before_task_config_resolves_symlinks(tmp_path):
    spec = make_spec(tmp_path / "task")
    alias = tmp_path / "alias"
    alias.symlink_to(spec.benchmark_root, target_is_directory=True)
    config = json.loads(spec.config_path.read_text())
    config["benchmark_root"] = str(alias)
    spec.config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="symlinks"):
        TaskSpec.from_config(spec.config_path)


def test_local_schema_references_resolve_offline_and_literal_ref_data_is_opaque(
    tmp_path,
):
    source = tmp_path / "contract.json"
    source.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "json_schema": {
                    "$defs": {"N": {"type": "number"}},
                    "type": "object",
                    "properties": {
                        "estimate": {"$ref": "#/$defs/N"},
                        "literal": {"const": {"$ref": "authored-scientific-identity"}},
                    },
                },
            }
        )
    )
    declaration = load_submission_contract(
        SimpleNamespace(
            submission_contract_ref="benchmark/contract.json", benchmark_root=tmp_path
        )
    )
    assert (
        declaration["json_schema"]["properties"]["literal"]["const"]["$ref"]
        == "authored-scientific-identity"
    )
    source.write_text(
        json.dumps({"schema": SCHEMA, "json_schema": {"$ref": "#/$defs/missing"}})
    )
    with pytest.raises(ValueError, match="Unresolved in-document"):
        load_submission_contract(
            SimpleNamespace(
                submission_contract_ref="benchmark/contract.json",
                benchmark_root=tmp_path,
            )
        )


def test_delivery_budget_baseline_reads_same_thread_runtime_projection(tmp_path):
    from theory_master.submission_rework import delivery_usage_guard

    cycle = tmp_path / "1"
    cycle.mkdir()
    (cycle / "run_state.json").write_text(
        json.dumps(
            {
                "thread_usage_baselines": {
                    "original-thread": {"input_tokens": 258295, "output_tokens": 5326},
                    "other-thread": {"input_tokens": 9999999},
                },
                "agents": {
                    "investigator": {
                        "thread_id": "original-thread",
                        "latest_cumulative_usage": {},
                        "raw_usage": {"input_tokens": 33731, "output_tokens": 237},
                    }
                },
            }
        )
    )
    state = {"native_goal": {"status": "complete", "tokens_used": 7}}
    owner = SimpleNamespace(
        cycle_dir=cycle,
        spec=SimpleNamespace(research_root=tmp_path),
        _persist=lambda state: None,
        _check_recovery_control=lambda state: None,
    )
    session = SimpleNamespace(
        owner=owner,
        state=state,
        context={"submission_delivery": {"thread_id": "original-thread"}},
    )
    with patch(
        "theory_master.investigator_budget.budget_status",
        return_value={"observed_tokens": 263621, "allowance_tokens": 500000},
    ):
        guard = delivery_usage_guard(session)
    assert guard.provider_usage_baseline == 263621
    guard(10)
    assert state["investigator_budget"]["observed_tokens"] == 263631
    assert state["native_goal"] == {"status": "complete", "tokens_used": 7}
