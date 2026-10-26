"""Offline replay of the real, incomplete Cycle 86 research checkpoint.

No Codex request and no writes to the original Cycle/Canvas.
"""
import copy
import json
import shutil
from pathlib import Path

import pytest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import stage_context, STAGE_CONTEXT_FIELDS
from theory_master.stage_registry import stage_for
from theory_master.research_preservation import (
    PreservationGuard, blocked_checkpoint, preserve_blocked_cycle,
    run_preservation_stage, finalize_preserved_cycle,
)
from theory_master.science_graph_store import ScienceGraphStore

ROOT = Path(__file__).resolve().parents[3]
CYCLE_86 = (
    ROOT / "gs_study" / ".theory-master-tasks"
    / "1-matrix-cavity-green-g-closure-h8-dd60a2f2780a" / "research" / "86"
)
CANVAS_86 = ROOT / "笔记区" / "探索台账" / "矩阵腔体Green闭合-H8" / "exploration.canvas"


def _stage_output():
    return {
        "observations": [{
            "statement": "在所测拓扑中全局割改变没有造成相当大小的 root 电阻变化",
            "conditions": "20K 有界换边、特定 root、无真实 cut response",
            "evidence_paths": ["workspace/README.md"],
            "limitations": "不能推及所有结构干预或物理释放量",
        }],
        "methods_exhausted": [{
            "method": "20K 图上的短路径保护换边",
            "limitation": "测得电阻差未达到预定可辨门槛；不是一般无解证明",
        }],
        "remaining_gap": "没有可冻结的 root 相关低信息幅值公式及合格独立网络",
        "unexecuted_test": "未冻结预测与误差预算，也未开展隔离物理评分",
        "canvas_title": "C86 中尺度混合研究阶段保全",
        "canvas_summary": "历史探索说明全局混合不足以确定 root 响应；仅为未裁决局部发现。",
    }


@pytest.fixture
def c86_clone(tmp_path):
    if not (CYCLE_86 / "run_state.json").is_file() or not CANVAS_86.is_file():
        pytest.skip("original Cycle86 benchmark is not available in this checkout")
    cycle = tmp_path / "research" / "86"
    cycle.mkdir(parents=True)
    shutil.copy2(CYCLE_86 / "workspace" / "README.md", tmp_path / "source_readme.md")
    (cycle / "workspace").mkdir()
    shutil.copy2(tmp_path / "source_readme.md", cycle / "workspace" / "README.md")
    canvas = tmp_path / "notes" / "exploration.canvas"
    canvas.parent.mkdir()
    model = json.loads(CANVAS_86.read_text(encoding="utf-8"))
    old = "external-c86-preservation-20261013"
    model["nodes"] = [n for n in model["nodes"] if n.get("id") != old]
    model["edges"] = [e for e in model["edges"] if e.get("toNode") != old and e.get("fromNode") != old]
    h = next(n for n in model["nodes"] if n.get("id") == "H-0056")
    h["text"] = h["text"].replace("frontier_status: parked", "frontier_status: active")
    canvas.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    # SQLite is authoritative in V2; build a minimal canonical graph fixture rather
    # than relying on the Canvas copy as an implicit data source.
    with ScienceGraphStore(canvas.parent / "science_graph.sqlite3") as store:
        h_node = next(n for n in model["nodes"] if n.get("id") == "H-0056")
        title = next((line.lstrip("# ").strip() for line in str(h_node.get("text") or "").splitlines()
                      if line.startswith("# ")), "Cycle 86 preserved hypothesis")
        store.add_hypothesis("H-0056", title=title, summary="Cycle 86 frozen hypothesis")
        store.register_cycle_hypothesis(cycle_id="86", hypothesis_id="H-0056")
        store.set_frontier(branch_id="frontier:H-0056", hypothesis_id="H-0056",
                           status="active", reason="Cycle 86 preservation fixture")
    original_run = (CYCLE_86 / "preservation" / "preclose_run_state.json")
    original_science = (CYCLE_86 / "preservation" / "preclose_research_state.json")
    state = json.loads((original_run if original_run.is_file() else CYCLE_86 / "run_state.json").read_text(encoding="utf-8"))
    state["branch_canvas"] = str(canvas)
    (cycle / "run_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    scientific = json.loads((original_science if original_science.is_file() else CYCLE_86 / "research_state.json").read_text(encoding="utf-8"))
    scientific["cycle_dir"] = str(cycle)
    (cycle / "research_state.json").write_text(json.dumps(scientific, ensure_ascii=False), encoding="utf-8")
    return cycle, canvas


def test_stage_contract_and_boundary():
    assert stage_for("investigator", {}, {"mode": "preserve_research"}) == "preserve_research"
    assert "blocked_reason" in STAGE_CONTEXT_FIELDS["investigator", "preserve_research"]
    contract = role_contract("investigator", phase="preserve_research")
    contract.structural_validator(_stage_output(), {})
    ctx = stage_context("investigator", "preserve_research", {
        "hypothesis_lock": {"hypothesis": "H", "scope": "S", "decisive_test": "T"},
        "blocked_reason": "N", "source_cycle": "86", "cycle_workspace": "/tmp/workspace",
    })
    assert ctx["source_cycle"] == "86"
    assert "qualification_handoff" not in ctx


def test_replay_real_cycle86_checkpoint_is_unadjudicated_and_idempotent(c86_clone):
    cycle, canvas = c86_clone
    state_before = (cycle / "run_state.json").read_bytes()
    original_before = CANVAS_86.read_bytes()
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
    assert sum(n["id"] == "external-c86-preservation-20261013" for n in model["nodes"]) == 0
    assert created["node_id"] == "H-0056"
    assert sum(n["id"] == "H-0056" for n in model["nodes"]) == 1
    h = next(n for n in model["nodes"] if n["id"] == "H-0056")
    assert "frontier_status: parked" in h["text"]
    # An empty compatibility Outcome is not a scientific open verdict.
    assert "scientific_status: none" in h["text"]
    assert "Judge" not in json.dumps(created, ensure_ascii=False)
    assert (cycle / "preservation" / "status.json").is_file()
    assert (cycle / "run_state.json").read_bytes() == state_before
    assert CANVAS_86.read_bytes() == original_before


def test_refuse_mismatched_checkpoint_unverified_source_and_fabricated_verdict(c86_clone):
    cycle, canvas = c86_clone
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



def test_formal_closure_replays_preserved_cycle86_without_judge_or_model(c86_clone):
    cycle, canvas = c86_clone
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
    assert CANVAS_86.is_file()
    with pytest.raises(PreservationGuard, match="blocked checkpoint"):
        blocked_checkpoint(cycle)


def test_formal_closure_rejects_changed_source_before_touching_state(c86_clone):
    cycle, canvas = c86_clone
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


def test_one_Investigator_A_B_response_then_sdk_C_reuses_archive_without_more_models(c86_clone):
    cycle, canvas = c86_clone

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
    note = canvas.parent / "notes" / "scientific_reports" / "Cycle86-preserve_research.md"
    text = note.read_text(encoding="utf-8")
    assert _stage_output()["observations"][0]["statement"] in text
    assert _stage_output()["unexecuted_test"] in text
    node = next(n for n in json.loads(canvas.read_text())["nodes"]
                if n["id"] == first["node_id"])
    assert _stage_output()["canvas_summary"] in node["text"]
    assert "[[notes/scientific_reports/Cycle86-preserve_research.md]]" in node["text"]
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


def test_preservation_replay_rejects_changed_source_without_extra_turn(c86_clone):
    cycle, canvas = c86_clone
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


def test_approved_stage_uses_separate_fake_thread_and_real_checkpoint(c86_clone):
    cycle, canvas = c86_clone
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
