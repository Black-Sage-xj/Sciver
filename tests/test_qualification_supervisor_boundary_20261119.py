"""Exercise whole-task SPV selection through real qualification and SDK lock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from theory_master.backends import FakeBackend
from theory_master.specification import TaskSpec, list_cycle_dirs
from theory_master.supervisor_intervention import KEY
from theory_master.task import bootstrap
from theory_master.task_progression import qualification_replan_allowed
from theory_master.workflow import Workflow


class QualificationBackend(FakeBackend):
    def __init__(self, *, decision="select_candidate", qualify=True, empty_lead=False, supervisor_suffix=""):
        super().__init__(reworks=0)
        self.decision = decision
        self.qualify = qualify
        self.empty_lead = empty_lead
        self.supervisor_suffix = supervisor_suffix
        self.proposals = 0
        self.reviews = []
        self.supervisor_contexts = []
        self.omit_next_input_review = False
        self.candidate = {
            "hypothesis": "A new controlled observation distinguishes the two candidate mechanisms",
            "scope": "Only the existing approved synthetic measurements and fixed sampling times",
            "why_now": "A discriminating check on the authorized inputs is available after review of failed routes",
            "goal_advance": "Decide which of the two mechanisms can meet the independent comparison",
            "decisive_test": "Use source B to check the signed residual, with opposite signs for the two models",
            "transition": {"prior_gap": "The existing tests cannot discriminate the two mechanisms",
                           "current_change": "Use the signed response under the controlled condition",
                           "source_contributions": {}},
        }

    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "attention_reviewer":
            evidence, _ = json.JSONDecoder().raw_decode(prompt.split("\nTask evidence:\n", 1)[1])
            self.supervisor_contexts.append(evidence)
            decision = {"action": self.decision, "reason": "The task needs an independent controlled comparison."}
            if self.decision == "select_candidate":
                decision["candidate"] = self.candidate
            elif self.decision == "wait_for_dependency":
                decision["wake_condition"] = "Register the approved new measurement in the task input manifest"
                input_ids = evidence.get("newly_verified_input_sha256")
                if input_ids:
                    if self.omit_next_input_review:
                        self.omit_next_input_review = False
                    else:
                        decision["reviewed_input_sha256"] = input_ids
            return self.ensure_thread(role, thread_id, model, provider), json.dumps(decision) + self.supervisor_suffix
        thread, raw = super().invoke(role, prompt, thread_id, model, provider)
        value = json.loads(raw)
        if role == "research_lead" and value.get("candidates"):
            self.proposals += 1
            if self.empty_lead:
                return thread, json.dumps({"candidates": [], "blocking_reason": "No qualifying independent comparison found."})
            for item in value["candidates"]:
                item["hypothesis"] += f" proposal {self.proposals}"
        if role == "challenger" and value.get("reviews"):
            self.reviews.append([item["candidate_id"] for item in value["reviews"]])
            if not self.qualify or self.proposals > 0 and not self.supervisor_contexts:
                for review in value["reviews"]:
                    review.update(verdict="reject", reason="The proposed test does not discriminate the alternatives.")
        return thread, json.dumps(value)


def workflow(tmp_path: Path, backend: QualificationBackend) -> Workflow:
    plan = tmp_path / "research_plan.md"
    plan.write_text(
        "# Goal\nFind a scientific mechanism\n\n"
        "# Research Contract\nUse frozen inputs only\n\n"
        "# Final Gate\nIndependent bounded test\n\n"
        "# Required Context\n- local\n", encoding="utf-8",
    )
    binding = bootstrap(plan)
    return Workflow(TaskSpec.from_config(binding.config_path), backend, stop_before="investigator")


def write_input_manifest(home: Path, *, data_files=(), derived_context_files=()):
    def row(path):
        data = path.read_bytes()
        return {"path": str(path.relative_to(home)), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    manifest = {"schema": "theorymaster/public-research-input-manifest/v1",
                "data_files": [row(path) for path in data_files],
                "derived_context_files": [row(path) for path in derived_context_files]}
    (home / "input_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_new_verified_alternative_reaches_supervisor_before_wait(tmp_path, monkeypatch):
    from theory_master.supervisor_activation import stopped_recovery

    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(decision="wait_for_dependency", empty_lead=True)
    wf = workflow(tmp_path, backend)
    home = Path(wf.spec.task_home)
    archive = []
    for index, size in enumerate((7_900, 7_900, 7_900, 7_200)):
        old = home / f"archive-{index}.md"
        old.write_text(str(index) * size, encoding="utf-8")
        archive.append(old)
    write_input_manifest(home, derived_context_files=archive)

    before = wf.run()
    old_cycle = wf.cycle_dir
    assert stopped_recovery(old_cycle, before, include_wait=True) == "qualification_wait"
    assert not backend.supervisor_contexts[-1].get("newly_verified_input_sha256")

    alternative = home / "notes" / "new-alternative"
    alternative.mkdir(parents=True)
    proof = alternative / "feasibility.json"
    proof.write_text(json.dumps({"timed_cpu_seconds": 0.1, "scientific_outcome": None,
                                 "checks": ["fixed inputs", "stable output", "no scoring"] * 16}),
                     encoding="utf-8")
    note = alternative / "qualification_note.md"
    note.write_text("An independent approach using existing inputs. " * 12, encoding="utf-8")
    proof_id = hashlib.sha256(proof.read_bytes()).hexdigest()
    unregistered = alternative / "unregistered.md"
    unregistered.write_text("Not authorized for review.", encoding="utf-8")
    write_input_manifest(home, data_files=[proof], derived_context_files=[*archive, note])
    assert stopped_recovery(old_cycle, before, include_wait=True) == "qualification"

    backend.omit_next_input_review = True
    resumed = Workflow(wf.spec, backend, stop_before="investigator")
    after = resumed.resume(wf.spec.master_id, supervisor_takeover=True)

    assert after["status"] == "no_qualified_candidate"
    assert not after["hypothesis_locked"] and after["scientific_verdict"] is None
    assert after["supervisor_qualification"]["source_cycle"] == before["cycle_id"]
    assert after["supervisor_qualification"]["source_context_version"] == 3
    fresh, repaired = backend.supervisor_contexts[1:]
    assert fresh == repaired and fresh["newly_verified_input_sha256"] == [proof_id]
    inputs = {row["path"]: row for row in fresh["verified_public_sources"]}
    assert inputs[str(proof)]["source_text"] == proof.read_text(encoding="utf-8")
    assert inputs[str(note)]["source_text"] == note.read_text(encoding="utf-8")
    assert inputs[str(note)]["priority_context_for"] == [proof_id]
    assert str(unregistered) not in inputs
    trace_path = resumed.cycle_dir / "supervisor/session.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    reviews = [json.loads(row["content"]) for row in trace if row["kind"] == "model_output"]
    assert "reviewed_input_sha256" not in reviews[0]
    assert reviews[1]["reviewed_input_sha256"] == [proof_id]
    assert after["supervisor_qualification"]["reviewed_input_sha256"] == [proof_id]
    assert stopped_recovery(resumed.cycle_dir, after, include_wait=True) == "qualification_wait"


@pytest.mark.parametrize("version, delivered, rerun", [
    (2, False, True),
    (2, True, False),
    (3, False, False),
])
def test_legacy_wait_wakes_only_for_input_missing_from_its_original_prompt(
    tmp_path, monkeypatch, version, delivered, rerun,
):
    from theory_master.supervisor_activation import stopped_recovery
    from theory_master.worker_direction import omitted_qualification_input_ids, research_direction_fingerprint

    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(decision="wait_for_dependency", empty_lead=True)
    wf = workflow(tmp_path, backend)
    first = wf.run()
    old_state = wf.cycle_dir / "research_state.json"
    original_old_state = old_state.read_bytes()
    home = Path(wf.spec.task_home)
    evidence = home / "feasibility.json"
    evidence.write_text('{"checks": ["repeatable unscored public check"]}', encoding="utf-8")
    evidence_id = hashlib.sha256(evidence.read_bytes()).hexdigest()
    write_input_manifest(home, data_files=[evidence])

    resumed = Workflow(wf.spec, backend, stop_before="investigator")
    stopped = resumed.resume(wf.spec.master_id, supervisor_takeover=True)
    cycle = resumed.cycle_dir
    receipt = stopped["supervisor_qualification"]
    assert receipt["source_cycle"] == first["cycle_id"]
    receipt["source_context_version"] = version
    saved_state = cycle / "research_state.json"
    saved_state.write_text(json.dumps(stopped), encoding="utf-8")

    trace_path = cycle / "supervisor/session.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    prompt, = [row for row in trace if row["kind"] == "prompt"]
    prefix, marker, raw = prompt["content"].partition("\nTask evidence:\n")
    prior_context = json.loads(raw)
    prior_context.pop("newly_verified_input_sha256", None)
    item, = [row for row in prior_context["verified_public_sources"] if row["sha256"] == evidence_id]
    if not delivered:
        item.pop("source_text", None)
    prompt["content"] = prefix + marker + json.dumps(prior_context)
    for row in trace:
        if row["kind"] == "model_output":
            output = json.loads(row["content"])
            output.pop("reviewed_input_sha256", None)
            row["content"] = json.dumps(output)
    trace_path.write_text("".join(json.dumps(row) + "\n" for row in trace), encoding="utf-8")
    archived_state, archived_trace = saved_state.read_bytes(), trace_path.read_bytes()

    current = research_direction_fingerprint(wf.spec)
    assert receipt["direction_fingerprint"] == current
    assert set(omitted_qualification_input_ids(wf.spec.research_root, stopped, current)) == (
        {evidence_id} if rerun else set())
    assert stopped_recovery(cycle, stopped, include_wait=True) == (
        "qualification" if rerun else "qualification_wait")

    if rerun:
        recovered = Workflow(wf.spec, backend, stop_before="investigator")
        new_stop = recovered.resume(wf.spec.master_id, supervisor_takeover=True)
        newest = backend.supervisor_contexts[-1]
        assert newest["newly_verified_input_sha256"] == [evidence_id]
        row, = [row for row in newest["verified_public_sources"] if row["sha256"] == evidence_id]
        assert row["source_text"] == evidence.read_text(encoding="utf-8")
        assert new_stop["supervisor_qualification"]["source_context_version"] == 3
        assert stopped_recovery(recovered.cycle_dir, new_stop, include_wait=True) == "qualification_wait"
        assert len(list_cycle_dirs(wf.spec.research_root)) == 3
    assert saved_state.read_bytes() == archived_state and trace_path.read_bytes() == archived_trace
    assert old_state.read_bytes() == original_old_state


def test_spv_selects_one_candidate_and_sdk_locks_after_challenger(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend()
    wf = workflow(tmp_path, backend)
    state = wf._execute_cycle()

    assert state["status"] == "checkpoint_reached"
    assert state["hypothesis_lock"]["hypothesis"] == backend.candidate["hypothesis"]
    assert backend.proposals == 3 and len(backend.reviews) == 4
    assert state["hypothesis_lock"]["candidate_id"] in backend.reviews[-1]
    assert len(backend.supervisor_contexts) == 1
    context = backend.supervisor_contexts[0]
    assert len(context["qualification_stop"]["qualification_attempts"]) == 3
    assert "agent_graph" in context and "scientific_model" in context and "goal_judgments" in context
    assert state["supervisor_qualification"]["status"] == "proposed"
    assert wf.invocation_cycle_index == 1


@pytest.mark.parametrize("suffix", ["", "}"])
def test_typed_empty_lead_exit_reaches_the_same_spv_skill(tmp_path, monkeypatch, suffix):
    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(empty_lead=True, supervisor_suffix=suffix)
    wf = workflow(tmp_path, backend)
    state = wf._execute_cycle()

    assert state["hypothesis_lock"]["hypothesis"] == backend.candidate["hypothesis"]
    assert backend.proposals == 1 and len(backend.reviews) == 1
    context = backend.supervisor_contexts[0]
    assert context["qualification_stop"]["qualification_attempts"][0]["candidate_ids"] == []

    trace = [json.loads(line) for line in (wf.cycle_dir / "supervisor/session.jsonl").read_text().splitlines()]
    normalizations = [row for row in trace if row["kind"] == "normalization"]
    original_prompt = next(row["content"] for row in trace if row["kind"] == "prompt")
    assert "candidate.lineage_other_node_ids is a literal JSON array" in original_prompt
    assert "Use [] if there are no additional source IDs." in original_prompt
    assert len(backend.supervisor_contexts) == 1
    if suffix:
        original, = [row for row in trace if row["kind"] == "model_output"]
        repair, = normalizations
        assert repair["seq"] == original["seq"] + 1
        assert repair["session_id"] == original["session_id"]
        assert repair["thread_id"] == original["thread_id"]
        assert repair["edit"] == "remove_extraneous_root_closer"
        assert repair["raw_sha256"] == hashlib.sha256(original["content"].encode()).hexdigest()
    else:
        assert not normalizations


def test_stopped_task_takeover_reviews_before_a_new_lead_without_editing_the_old_cycle(tmp_path, monkeypatch):
    backend = QualificationBackend()
    monkeypatch.setenv(KEY, "0")
    wf = workflow(tmp_path, backend)
    stopped = wf._execute_cycle()
    previous = wf.cycle_dir / "research_state.json"
    saved = previous.read_bytes()
    assert stopped["status"] == "no_qualified_candidate"

    monkeypatch.setenv(KEY, "1")
    resumed = Workflow(wf.spec, backend, stop_before="investigator")
    state = resumed.resume(wf.spec.master_id)

    assert previous.read_bytes() == saved
    assert len(list_cycle_dirs(wf.spec.research_root)) == 2
    assert state["supervisor_qualification"]["source_cycle"] == stopped["cycle_id"]
    assert state["hypothesis_lock"]["hypothesis"] == backend.candidate["hypothesis"]
    assert backend.proposals == 3 and len(backend.reviews) == 4


def test_verified_reply_repair_retries_review_at_the_same_prelock_checkpoint(tmp_path, monkeypatch):
    from theory_master import supervisor_qualification_recovery
    from theory_master.runtime_supervisor_review import review_runtime_failure

    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(empty_lead=True, supervisor_suffix="}")
    wf = workflow(tmp_path, backend)
    with monkeypatch.context() as legacy:
        legacy.setattr(supervisor_qualification_recovery, "parse_review_object",
                       lambda raw, **_: (json.loads(raw), False))
        stopped = wf._execute_cycle()
    assert stopped["status"] == "needs_attention"
    assert backend.proposals == 1 and len(backend.supervisor_contexts) == 2
    cycle = wf.cycle_dir
    review = review_runtime_failure(
        backend=None, research_root=wf.spec.research_root, cycle_dir=cycle, state=stopped,
        state_path=cycle / "research_state.json", incident_id="malformed-review", scheduler_locked=True,
    )
    assert review["action"] == "resume", review
    state = Workflow(wf.spec, backend, stop_before="investigator").resume(
        wf.spec.master_id, supervisor_takeover=True)
    assert state["status"] == "checkpoint_reached"
    assert state["hypothesis_lock"]["hypothesis"] == backend.candidate["hypothesis"]
    assert len(list_cycle_dirs(wf.spec.research_root)) == 1
    assert backend.proposals == 1 and len(backend.supervisor_contexts) == 3


@pytest.mark.parametrize("defect", ["outer_lineage", "identical_transition", "different_transition"])
def test_checkpointless_reply_reentry_qualifies_only_the_original_supervisor_candidate(
    tmp_path, monkeypatch, defect,
):
    from theory_master import supervisor_qualification_recovery
    from theory_master.runtime_supervisor_review import review_runtime_failure
    from theory_master.supervisor_repair_receipts import inspect_repair_receipt

    monkeypatch.setenv(KEY, "0")
    seed = workflow(tmp_path, FakeBackend(reworks=0))
    seed.stop_before = None
    seeded = seed._execute_cycle()
    anchor = seeded["hypothesis_lock"]["id"]

    class EnvelopeBackend(QualificationBackend):
        def __init__(self):
            super().__init__(empty_lead=True)
            original = {
                **self.candidate,
                "decisive_test": "Require a smaller paired width on all three predeclared conditions.",
                "transition": {
                    **self.candidate["transition"],
                    "source_contributions": {anchor: "Supplies the previous comparison under fixed conditions."},
                },
            }
            retry = {**original, "decisive_test": "Require a smaller paired width on any one condition."}
            self.candidates = [original, retry]
            self.challenger_candidates = []
            self.original_raw = None

        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "attention_reviewer" and "\nTask evidence:\n" in prompt:
                index = len(self.supervisor_contexts)
                assert index < 2, "a restored decision must not request another model proposal"
                context, _ = json.JSONDecoder().raw_decode(prompt.split("\nTask evidence:\n", 1)[1])
                self.supervisor_contexts.append(context)
                reply = {
                    "action": "select_candidate",
                    "reason": "The previous comparison did not test the new fixed conditions.",
                    "candidate": self.candidates[index],
                }
                lineage = {
                    "lineage_main_node_id": anchor,
                    "lineage_other_node_ids": [],
                    "evolution_operator": "counter_hypothesis" if index == 0 else "refine",
                }
                if defect == "outer_lineage":
                    raw = json.dumps({**reply, **lineage}) + "}"
                elif index == 0:
                    reply["candidate"] = {**self.candidates[index], **lineage}
                    duplicate = dict(self.candidates[index]["transition"])
                    if defect == "different_transition":
                        duplicate["current_change"] = "Change the comparison to a different test."
                    raw = json.dumps(reply)[:-2] + ', "transition": ' + json.dumps(duplicate) + "}}"
                    self.original_raw = raw
                else:
                    reply["candidate"] = {**self.candidates[index], "transition": {
                        **self.candidates[index]["transition"], "source_contributions": {},
                    }}
                    raw = json.dumps(reply)
                return self.ensure_thread(role, thread_id, model, provider), raw
            thread, raw = super().invoke(role, prompt, thread_id, model, provider)
            if role != "challenger":
                return thread, raw
            context, _ = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])
            candidates = [{"id": candidate["id"], **candidate["frozen_objective"],
                           **candidate["proposal_context"]} for candidate in context["candidate"]]
            self.challenger_candidates.extend(candidates)
            output = json.loads(raw)
            for candidate, review in zip(candidates, output["reviews"], strict=True):
                if candidate["decisive_test"] != self.candidates[0]["decisive_test"]:
                    review.update(verdict="reject", reason="The test weakened the original decisive criterion.")
            return thread, json.dumps(output)

    backend = EnvelopeBackend()
    previous = Workflow(seed.spec, backend, stop_before="investigator")
    before = previous._execute_cycle()
    assert before["status"] == "no_qualified_candidate"
    previous_path = previous.cycle_dir / "research_state.json"
    previous_state = previous_path.read_bytes()

    monkeypatch.setenv(KEY, "1")
    interrupted = Workflow(seed.spec, backend, stop_before="investigator")
    with monkeypatch.context() as legacy:
        if defect == "outer_lineage":
            legacy.setattr(supervisor_qualification_recovery, "normalize_candidate_envelope",
                           lambda output: (output, ()))
        else:
            parse = supervisor_qualification_recovery.parse_review_object

            def reject_original_duplicate(raw, *args, **kwargs):
                if raw == backend.original_raw:
                    raise ValueError("Duplicate Supervisor decision key: transition")
                return parse(raw, *args, **kwargs)

            legacy.setattr(supervisor_qualification_recovery, "parse_review_object", reject_original_duplicate)
        stopped = interrupted.resume(seed.spec.master_id, supervisor_takeover=True)
    cycle = interrupted.cycle_dir
    assert stopped["status"] == "needs_attention"
    assert stopped["phase"] == "supervisor_qualification_review"
    assert stopped["hypothesis_lock"] == {"status": "unlocked"}
    assert not stopped["last_safe_checkpoint"] and not stopped["role_outputs"]
    assert backend.proposals == 1 and len(backend.supervisor_contexts) == 2
    assert not backend.challenger_candidates

    state_path = cycle / "research_state.json"
    review = review_runtime_failure(
        backend=None, research_root=seed.spec.research_root, cycle_dir=cycle, state=stopped,
        state_path=state_path, incident_id="overscoped-envelope", scheduler_locked=True,
    )
    if defect == "different_transition":
        assert review["action"] == "hold", review
        assert not stopped.get("supervisor_repair_receipt")
        assert not backend.challenger_candidates and len(backend.supervisor_contexts) == 2
        assert previous_path.read_bytes() == previous_state
        return
    assert review["action"] == "resume", review
    if defect == "identical_transition":
        proof = json.loads(inspect_repair_receipt(cycle, json.loads(state_path.read_bytes()))["tests"])
        assert proof["mode"] == "original_candidate_duplicate_transition"
        assert proof["original_response_sha256"] == hashlib.sha256(backend.original_raw.encode()).hexdigest()
    trace_path = cycle / "supervisor/session.jsonl"
    saved_trace = trace_path.read_text(encoding="utf-8")
    trace_path.write_text(saved_trace.replace("all three predeclared conditions", "two of three conditions", 1),
                          encoding="utf-8")
    with pytest.raises(ValueError, match="response evidence changed"):
        inspect_repair_receipt(cycle, json.loads(state_path.read_text(encoding="utf-8")))
    trace_path.write_text(saved_trace, encoding="utf-8")

    resumed = Workflow(seed.spec, backend, stop_before="investigator")
    locked = resumed.resume(seed.spec.master_id, supervisor_takeover=True)
    assert resumed.cycle_dir == cycle
    assert len(list_cycle_dirs(seed.spec.research_root)) == 3
    assert locked["status"] == "checkpoint_reached" and locked["hypothesis_locked"]
    candidate, = backend.challenger_candidates
    assert {key: candidate[key] for key in backend.candidates[0]} == backend.candidates[0]
    assert candidate["lineage_main_node_id"] == anchor and candidate["evolution_operator"] == "counter_hypothesis"
    assert locked["hypothesis_lock"]["candidate_id"] == candidate["id"]
    assert locked["hypothesis_lock"]["decisive_test"] == backend.candidates[0]["decisive_test"]
    assert locked["supervisor_qualification_reentries"] == [locked["supervisor_qualification"]["session_id"]]
    assert len(locked["supervisor_qualification_reentry_audit"]) == 1
    assert backend.proposals == 1 and len(backend.supervisor_contexts) == 2 and len(backend.reviews) == 1
    assert previous_path.read_bytes() == previous_state


def test_unavailable_evidence_is_a_concrete_wait_without_automatic_rerolls(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(decision="wait_for_dependency")
    wf = workflow(tmp_path, backend)
    stopped = wf.run()

    assert stopped["status"] == "no_qualified_candidate" and not stopped["hypothesis_locked"]
    assert stopped["scientific_verdict"] is None
    assert stopped["attention"]["wake_condition"] == "Register the approved new measurement in the task input manifest"
    assert stopped["supervisor_qualification"]["status"] == "waiting"
    assert not qualification_replan_allowed(wf.spec, stopped)
    assert backend.proposals == 3 and len(backend.reviews) == 3 and len(backend.supervisor_contexts) == 1
    assert wf.invocation_cycle_index == 1


def test_spv_candidate_without_qualification_never_locks(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(qualify=False)
    wf = workflow(tmp_path, backend)
    stopped = wf.run()

    assert stopped["status"] == "no_qualified_candidate" and not stopped["hypothesis_locked"]
    assert stopped["supervisor_qualification"]["status"] == "review_exhausted"
    assert backend.proposals == 3 and len(backend.reviews) == 4 and len(backend.supervisor_contexts) == 1
    assert stopped["scientific_verdict"] is None and wf.invocation_cycle_index == 1


def test_spv_selection_hands_the_frozen_candidate_to_investigator_and_judge(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "1")
    backend = QualificationBackend(empty_lead=True)
    wf = workflow(tmp_path, backend)
    wf.stop_before = None
    wf.set_max_cycles(1)

    result = wf.run()

    assert result["supervisor_qualification"]["status"] == "handed_back"
    assert result["supervisor_qualification"]["hypothesis_id"] == result["hypothesis_lock"]["id"]
    assert result["hypothesis_lock"]["hypothesis"] == backend.candidate["hypothesis"]
    assert result["scientific_verdict"] == "supported"
    assert backend.judges == 1


def test_delivery_plan_binds_exact_registered_prediction_columns():
    from theory_master.supervisor_qualification_recovery import _decision

    context = {"delivery_repair": {
        "inputs": [{"reference": "benchmark/public.csv", "first_columns": ["", "feature"]}],
        "predictions": [{"reference": "predictions.csv",
                         "columns": ["public_test_index", "group_a", "group_b"]}],
    }}
    plan = {"input": "benchmark/public.csv", "predictions": "predictions.csv",
            "input_key": "", "prediction_key": "public_test_index",
            "outputs": {"prediction_column": "output.csv"}}
    output = {"action": "materialize_delivery", "reason": "Only the predictor tables remain.",
              "delivery_plan": plan}
    with pytest.raises(ValueError, match="exact prediction column names"):
        _decision(json.dumps(output), context, {})
    plan["outputs"] = {"group_a": "a.csv", "group_b": "b.csv"}
    assert _decision(json.dumps(output), context, {})["status"] == "delivery_planned"


def test_task_level_benchmark_sources_need_explicit_paths_and_manifest_hashes(tmp_path):
    import hashlib
    from types import SimpleNamespace
    from theory_master.research_inputs import verified_research_inputs

    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    source = benchmark / "baseline.py"
    source.write_text("def reconstruct(value): return value\n")
    hidden = benchmark / "unlisted.py"
    hidden.write_text("private = True\n")
    files = [{"path": f"benchmark/{path.name}", "size": path.stat().st_size,
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (source, hidden)]
    (benchmark / "fixture_manifest.json").write_text(json.dumps({
        "schema": "sciver/private-scientific-fixture-manifest/v1", "files": files}))
    spec = SimpleNamespace(benchmark_root=benchmark, branch_canvas=tmp_path / "exploration.canvas",
                           research_root=tmp_path, document=tmp_path / "research_plan.md",
                           required_context="- benchmark/fixture_manifest.json\n- benchmark/baseline.py")
    found = verified_research_inputs(spec, include_text=True)
    assert [row["reference"] for row in found] == ["benchmark/baseline.py"]
    assert found[0]["source_text"] == source.read_text()
    source.write_text("tampered = True\n")
    assert verified_research_inputs(spec, include_text=True) == []


def test_whole_task_archive_supplies_exact_comparisons_beyond_short_node_titles():
    from theory_master.supervisor_qualification_recovery import _adjudicated_tests
    from unittest.mock import Mock

    store = Mock()
    store.graph_view.return_value = {"nodes": [
        {"type": "Hypothesis", "id": "H-1", "title": "Baseline",
         "cycle_outcomes": [{"cycle_id": "9", "verdict": "rejected",
                             "decisive_test": "Remove only the fixed unsharp filter",
                             "judge_rationale": "No partition meets the minimum coverage"}]},
        {"type": "External", "id": "external", "cycle_outcomes": []},
    ]}
    assert _adjudicated_tests(store) == [{
        "hypothesis_id": "H-1", "title": "Baseline", "cycle": "9", "verdict": "rejected",
        "decisive_test": "Remove only the fixed unsharp filter",
        "rationale": "No partition meets the minimum coverage",
    }]


def test_spv_archive_keeps_latest_and_goal_tied_comparisons_when_context_is_full():
    from unittest.mock import Mock
    from theory_master.supervisor_qualification_recovery import _adjudicated_tests

    def outcome(cycle):
        return {"cycle_id": str(cycle), "verdict": "supported",
                "decisive_test": f"Frozen test {cycle} " + "x" * 2_500,
                "judge_rationale": "r" * 900}

    nodes = [{"type": "Hypothesis", "id": f"H-{number:04d}", "cycle_outcomes": [outcome(number)]}
             for number in range(4, 51)]
    nodes.insert(0, {"type": "Hypothesis", "id": "H-0001", "cycle_outcomes":
                    [outcome(1), outcome(2), outcome(3)]})
    store = Mock()
    store.graph_view.return_value = {"nodes": nodes}
    comparisons = _adjudicated_tests(store, frozenset({"1", "49"}))
    assert [row["cycle"] for row in comparisons[:5]] == ["49", "1", "50", "48", "47"]
    assert sum(len(value) for row in comparisons for value in row.values()) <= 42_000


@pytest.mark.parametrize(
    "wrapper, normalized",
    [
        (lambda raw: raw, False),
        (lambda raw: raw + "}", True),
        (lambda raw: raw + "\n}\t", True),
        (lambda raw: "```json\n" + raw + "\n```", False),
        (lambda raw: "```json\n" + raw + "}\n```", True),
    ],
)
def test_spv_review_parser_drops_only_one_extra_root_closer(wrapper, normalized):
    from theory_master.supervisor_qualification_recovery import _decision, parse_review_object

    decision = {"action": "wait_for_dependency", "reason": "Need {one} verified source.",
                "wake_condition": "A matching record exists."}
    raw = wrapper(json.dumps(decision))
    assert parse_review_object(raw) == (decision, normalized)
    assert _decision(raw, {}, {})["status"] == "waiting"


@pytest.mark.parametrize("wrapper", [
    lambda raw: raw + "}}",
    lambda raw: raw + "} extra prose",
    lambda raw: raw + raw,
    lambda raw: "Decision follows " + raw,
    lambda raw: "[" + raw + "]}",
    lambda raw: "```json\n" + raw,
    lambda raw: "```json\n" + raw + "\n```\n" + raw,
    lambda raw: raw[:-1] + ', "action": "select_candidate"}',
    lambda raw: raw[:-1] + ', "ignored": {"same": 1, "same": 2}}}',
])
def test_spv_review_parser_rejects_ambiguous_output(wrapper):
    from theory_master.supervisor_qualification_recovery import parse_review_object

    decision = {"action": "wait_for_dependency", "reason": "Gap", "wake_condition": "Source verified"}
    with pytest.raises(ValueError):
        parse_review_object(wrapper(json.dumps(decision)))


def test_spv_review_normalization_does_not_relax_action_validation():
    from theory_master.supervisor_qualification_recovery import _decision

    decision = {"action": "select_candidate", "reason": "Reason", "candidate": {}}
    with pytest.raises(ValueError):
        _decision(json.dumps(decision) + "}", {}, {})
