"""Synthetic preservation checkpoints, with no private research trajectories."""
import json

import pytest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import stage_context, STAGE_CONTEXT_FIELDS
from theory_master.stage_registry import stage_for
from theory_master.research_preservation import (
    PreservationGuard, blocked_checkpoint, preserve_blocked_cycle,
    run_preservation_stage, finalize_preserved_cycle, normalize_preservation_display,
)
from theory_master.science_graph_store import ScienceGraphStore

def _stage_output():
    return {
        "observations": [{
            "statement": "The handwritten normalization fixture accepts two ASCII examples.",
            "conditions": "Only the two strings listed in the fixture README were inspected.",
            "evidence_paths": ["workspace/README.md"],
            "limitations": "The examples do not establish behavior for every Unicode string.",
        }],
        "methods_exhausted": [{
            "method": "Inspect a finite list of normalization examples",
            "limitation": "Finite examples cannot discharge the universal invariant.",
        }],
        "remaining_gap": "A proof for the declared Unicode input class is still missing.",
        "unexecuted_test": "No exhaustive check or proof of idempotence was completed.",
        "canvas_title": "Synthetic normalization work preserved",
        "canvas_summary": "Two ASCII examples were inspected; the Unicode claim remains unadjudicated.",
    }


@pytest.fixture
def synthetic_checkpoint(tmp_path):
    """Construct all inputs by hand; no private task, archive or model is used."""
    cycle = tmp_path / "research" / "1"
    (cycle / "workspace").mkdir(parents=True)
    (cycle / "workspace" / "README.md").write_text(
        "# Synthetic observations\nASCII examples: ' a ' -> 'a'; 'b' -> 'b'.\n"
        "No observations about other strings.\n", encoding="utf-8")
    canvas = tmp_path / "notes" / "exploration.canvas"
    canvas.parent.mkdir()
    lock = {"id": "H-0001", "status": "locked",
            "hypothesis": "The declared normalization operation is idempotent.",
            "scope": "Finite Unicode strings under the specified normalization rules.",
            "decisive_test": "Prove idempotence or provide a verified counterexample."}
    with ScienceGraphStore(canvas.parent / "science_graph.sqlite3") as store:
        store.add_hypothesis("H-0001", title="Synthetic normalization claim", summary=lock["hypothesis"])
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                           status="active", reason="Synthetic fixture awaiting proof")
        from theory_master.science_canvas_export import canvas_projection
        canvas.write_text(json.dumps(canvas_projection(store)), encoding="utf-8")
    state = {"cycle_id": "1", "status": "needs_attention",
             "phase": "investigator:goal_suspended", "execution_active": False,
             "resumable": True, "scientific_verdict": None,
             "attention": {"code": "investigator_native_goal_blocked", "goal_status": "blocked",
                           "reason": "Synthetic proof attempt has no applicable Unicode lemma."},
             "hypothesis_lock": lock, "branch_canvas": str(canvas),
             "cycle_dir": str(cycle), "research_root": str(cycle.parent), "role_outputs": {}}
    for name in ("run_state.json", "research_state.json"):
        (cycle / name).write_text(json.dumps(state), encoding="utf-8")
    return cycle, canvas


def test_stage_contract_and_boundary():
    assert stage_for("investigator", {}, {"mode": "preserve_research"}) == "preserve_research"
    assert "blocked_reason" in STAGE_CONTEXT_FIELDS["investigator", "preserve_research"]
    contract = role_contract("investigator", phase="preserve_research")
    contract.structural_validator(_stage_output(), {})
    ctx = stage_context("investigator", "preserve_research", {
        "hypothesis_lock": {"hypothesis": "H", "scope": "S", "decisive_test": "T"},
        "blocked_reason": "N", "source_cycle": "1", "cycle_workspace": "/tmp/workspace",
    })
    assert ctx["source_cycle"] == "1"
    assert "qualification_handoff" not in ctx


def test_synthetic_checkpoint_is_unadjudicated_and_idempotent(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    state_before = (cycle / "run_state.json").read_bytes()
    source_before = (cycle / "workspace" / "README.md").read_bytes()
    with pytest.raises(PreservationGuard, match="authorization"):
        preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                               stage_output=_stage_output(), authorization="investigator_blocked")
    dry = preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                                 stage_output=_stage_output(), authorization="operator_approved",
                                 dry_run=True)
    assert dry["created"] and dry["scientific_verdict"] is None
    assert not (cycle / "preservation").exists()
    created = preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                                     stage_output=_stage_output(), authorization="operator_approved")
    again = preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                                   stage_output=_stage_output(), authorization="operator_approved")
    assert created["created"] and not again["created"]
    model = json.loads(canvas.read_text(encoding="utf-8"))
    assert not any(n["id"].startswith("external-") for n in model["nodes"])
    assert created["node_id"] == "H-0001"
    assert sum(n["id"] == "H-0001" for n in model["nodes"]) == 1
    h = next(n for n in model["nodes"] if n["id"] == "H-0001")
    assert "frontier_status: parked" in h["text"]
    # An empty compatibility Outcome is not a scientific open verdict.
    assert "scientific_status: none" in h["text"]
    assert "Judge" not in json.dumps(created, ensure_ascii=False)
    assert (cycle / "preservation" / "status.json").is_file()
    assert (cycle / "run_state.json").read_bytes() == state_before
    assert (cycle / "workspace" / "README.md").read_bytes() == source_before


def test_refuse_mismatched_checkpoint_unverified_source_and_fabricated_verdict(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    state = json.loads((cycle / "run_state.json").read_text(encoding="utf-8"))
    state["attention"]["code"] = "provider_model_capacity_timeout"
    (cycle / "run_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PreservationGuard, match="blocked checkpoint"):
        blocked_checkpoint(cycle)
    state["attention"]["code"] = "investigator_native_goal_blocked"
    (cycle / "run_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    bad = _stage_output()
    bad["observations"][0]["evidence_paths"] = ["../../outside_results.json"]
    with pytest.raises(PreservationGuard, match="source"):
        preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                               stage_output=bad, authorization="operator_approved", dry_run=True)
    bad = _stage_output()
    bad["verdict"] = "supported"
    with pytest.raises(ValueError, match="exactly"):
        preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                               stage_output=bad, authorization="operator_approved", dry_run=True)



def test_formal_closure_preserves_checkpoint_without_judge_or_model(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                           stage_output=_stage_output(), authorization="operator_approved")
    old_state = (cycle / "research_state.json").read_bytes()
    old_run = (cycle / "run_state.json").read_bytes()
    no_write = finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved",
                                        dry_run=True)
    assert no_write["ready_to_close"]
    assert (cycle / "research_state.json").read_bytes() == old_state
    with pytest.raises(PreservationGuard, match="authorization"):
        finalize_preserved_cycle(cycle_dir=cycle, authorization="investigator_blocked")
    result = finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved")
    assert result["status"] == "OVER" and result["scientific_verdict"] is None
    assert result["next_cycle_created"] is False
    assert (cycle / "preservation" / "preclose_research_state.json").read_bytes() == old_state
    assert (cycle / "preservation" / "preclose_run_state.json").read_bytes() == old_run
    research = json.loads((cycle / "research_state.json").read_text())
    projected = json.loads((cycle / "run_state.json").read_text())
    assert research["status"] == "over" and projected["status"] == "OVER"
    for saved in (research, projected):
        assert saved["closure_reason"] == "investigator_preservation"
        assert saved["phase"] == "investigator:preserved"
        assert saved["resumable"] is False and saved["execution_active"] is False
        assert saved["scientific_verdict"] is None and saved["attention"] == {}
    assert "judge" not in research["role_outputs"]
    assert "reporter" not in research["role_outputs"]
    assert finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved")[
        "already_closed"] is True
    assert canvas.is_file()
    with pytest.raises(PreservationGuard, match="blocked checkpoint"):
        blocked_checkpoint(cycle)


def test_formal_closure_rejects_changed_source_before_touching_state(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                           stage_output=_stage_output(), authorization="operator_approved")
    preclose = (cycle / "research_state.json").read_bytes()
    (cycle / "workspace" / "README.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(PreservationGuard, match="source hashes changed"):
        finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved")
    assert (cycle / "research_state.json").read_bytes() == preclose
    assert not (cycle / "preservation" / "closure.json").exists()


def test_preservation_B_presentation_must_stay_compact_and_only_summarize_A():
    contract = role_contract("investigator", phase="preserve_research")
    data = _stage_output()
    contract.structural_validator(data, {})
    for name, length in (("canvas_title", 49), ("canvas_summary", 161)):
        invalid = dict(data, **{name: "a" * length})
        with pytest.raises(ValueError, match="B display"):
            contract.structural_validator(invalid, {})
    invalid = dict(data, judge_verdict="supported")
    with pytest.raises(ValueError, match="exactly"):
        contract.structural_validator(invalid, {})


@pytest.mark.parametrize(("title_length", "summary_length"), [(32, 166), (41, 189), (49, 160)])
def test_preservation_normalizes_only_overlong_display_and_preserves_raw_reply(synthetic_checkpoint,
                                                                              title_length, summary_length):
    from unittest.mock import Mock

    cycle, canvas = synthetic_checkpoint
    output = _stage_output()
    output["canvas_title"] = "T" * title_length
    output["canvas_summary"] = "S" * (summary_length - 10) + " uncertain"
    raw = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    backend = Mock()
    backend.ensure_thread.return_value = "archival-thread"
    backend.invoke.return_value = ("archival-thread", raw)

    result = run_preservation_stage(cycle_dir=cycle, canvas_path=canvas, backend=backend,
                                    model="test", provider="test", authorization="operator_approved")

    assert result["scientific_verdict"] is None
    stage = json.loads((cycle / "preservation/stage_output.json").read_text())
    display_limits = {"canvas_title": 48, "canvas_summary": 160}
    for field, original in output.items():
        if field in display_limits and len(original) > display_limits[field]:
            assert len(stage[field]) <= display_limits[field] and stage[field] not in original
        else:
            assert stage[field] == original
    assert (cycle / "preservation/last_model_output.txt").read_bytes() == raw.encode()
    backend.invoke.assert_called_once()


@pytest.mark.parametrize("bad", ["schema", "source", "display_type"])
def test_preservation_display_normalization_keeps_other_validators(synthetic_checkpoint, bad):
    from unittest.mock import Mock

    cycle, canvas = synthetic_checkpoint
    output = _stage_output()
    output["canvas_summary"] = "S" * 189
    if bad == "schema":
        output["scientific_verdict"] = "supported"
    elif bad == "source":
        output["observations"][0]["evidence_paths"] = ["workspace/missing.md"]
    else:
        output["canvas_title"] = []
    normalized = normalize_preservation_display(output)
    assert normalized["observations"] == output["observations"]
    backend = Mock()
    backend.ensure_thread.return_value = "archival-thread"
    backend.invoke.return_value = ("archival-thread", json.dumps(output))

    with pytest.raises(ValueError):
        run_preservation_stage(cycle_dir=cycle, canvas_path=canvas, backend=backend,
                               model="test", provider="test", authorization="operator_approved")

    assert not (cycle / "preservation/stage_output.json").exists()


def test_one_Investigator_A_B_response_then_sdk_C_reuses_archive_without_more_models(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint

    class CountedBackend:
        def __init__(self):
            self.calls = []
        def ensure_thread(self, role, prior, model, provider):
            assert role == "investigator" and prior is None
            self.calls.append(("start", role))
            return "preserve-A-B"
        def invoke(self, role, prompt, thread, model, provider):
            assert role == "investigator" and thread == "preserve-A-B"
            for phrase in ("A first", "Then do B", "SDK alone performs C",
                           "No Reporter, Judge, Literature, Lead"):
                assert phrase in prompt
            self.calls.append(("invoke", role))
            return thread, json.dumps(_stage_output(), ensure_ascii=False)

    backend = CountedBackend()
    first = run_preservation_stage(
        cycle_dir=cycle, canvas_path=canvas, backend=backend,
        model="fake", provider="fake", authorization="operator_approved",
    )
    assert first["scientific_verdict"] is None
    assert backend.calls == [("start", "investigator"), ("invoke", "investigator")]
    note = canvas.parent / "notes" / "scientific_reports" / "Cycle1-preserve_research.md"
    text = note.read_text(encoding="utf-8")
    assert _stage_output()["observations"][0]["statement"] in text
    assert _stage_output()["unexecuted_test"] in text
    projected = json.loads(canvas.read_text())
    node = next(n for n in projected["nodes"]
                if n["id"] == first["node_id"])
    assert _stage_output()["canvas_summary"] in node["text"]
    report = next(n for n in projected["nodes"]
                  if n.get("file") == "notes/scientific_reports/Cycle1-preserve_research.md")
    assert report["type"] == "file"
    assert any(edge["fromNode"] == node["id"] and edge["toNode"] == report["id"]
               for edge in projected["edges"])
    again = run_preservation_stage(
        cycle_dir=cycle, canvas_path=canvas, backend=backend,
        model="fake", provider="fake", authorization="operator_approved",
    )
    assert again["node_id"] == first["node_id"]
    assert backend.calls == [("start", "investigator"), ("invoke", "investigator")]
    result = finalize_preserved_cycle(cycle_dir=cycle, authorization="operator_approved")
    assert result["status"] == "OVER" and result["next_cycle_created"] is False
    final = json.loads((cycle / "research_state.json").read_text(encoding="utf-8"))
    assert final["status"] == "over" and "judge" not in final["role_outputs"]
    assert "reporter" not in final["role_outputs"]
    assert backend.calls == [("start", "investigator"), ("invoke", "investigator")]


def test_preservation_replay_rejects_changed_source_without_extra_turn(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    preserve_blocked_cycle(cycle_dir=cycle, canvas_path=canvas,
                           stage_output=_stage_output(), authorization="operator_approved")
    (cycle / "workspace" / "README.md").write_text("tampered!", encoding="utf-8")
    class NoModel:
        def __getattr__(self, key):
            raise AssertionError("cached preservation must not invoke model")

    with pytest.raises(PreservationGuard, match="existing preservation note differs|status or sources conflict"):
        run_preservation_stage(
            cycle_dir=cycle, canvas_path=canvas, backend=NoModel(),
            model="fake", provider="fake", authorization="operator_approved",
        )


def test_approved_stage_uses_separate_fake_thread_and_frozen_checkpoint(synthetic_checkpoint):
    cycle, canvas = synthetic_checkpoint
    class FakeBackend:
        def __init__(self):
            self.calls = []
        def ensure_thread(self, role, thread, model, provider):
            self.calls.append(("ensure", thread))
            assert thread is None
            return "new-preserve-thread"
        def invoke(self, role, prompt, thread, model, provider):
            self.calls.append(("invoke", prompt))
            assert "preserve_research" in prompt
            assert "new-preserve-thread" == thread
            return thread, json.dumps(_stage_output(), ensure_ascii=False)
    backend = FakeBackend()
    result = run_preservation_stage(cycle_dir=cycle, canvas_path=canvas, backend=backend,
                                    model="fake", provider="fake",
                                    authorization="operator_approved", dry_run=True)
    assert result["created"]
    assert [v[0] for v in backend.calls] == ["ensure", "invoke"]
