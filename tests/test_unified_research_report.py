"""Single-report generation, frozen supplements and targeted Agent reads; no LLM."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.canvas_projection import CanvasProjectionMixin
from theory_master.reporting import ReportingMixin
from theory_master.research_report import (
    expand_documents, load_report, read_report, report_cycle_for_path, sections,
    snapshot_documents,
)
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.science_graph_tools import execute


GUIDE = {"conclusion": "The bounded claim is supported.", "basis": "The frozen comparison resolves it.",
         "scope": "The supplied independent trajectories.", "open_question": ""}


def test_one_report_reuses_overview_and_freezes_supplement(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    notes = tmp_path / "notes"
    notes.mkdir()
    supplement = notes / "derivation.md"
    original = "# Supplement\n\n## Derivation\nExact supplied identity.\n\n## Other\nUnrelated material.\n"
    supplement.write_text(original)
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Frozen question")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")

    class Reporter(ReportingMixin, CanvasProjectionMixin):
        def _graph_database_path(self): return database
        def _path_anchors(self): return {"repo_root": str(tmp_path)}
        def _evidence_context(self): return {"evidence_manifest": {"records": []}}
        def _call(self, *args, **kwargs): raise AssertionError("no model calls")
        def _emit(self, *args, **kwargs): pass

    reporter = Reporter()
    reporter.cycle_dir = tmp_path / "research/1"
    (reporter.cycle_dir / "checkpoints").mkdir(parents=True)
    reporter.notes_dir = notes
    reporter.branch_canvas = None
    reporter.spec = SimpleNamespace(notes_mirror_dir=None)
    body = ("## Question and necessary conditions\nFrozen question and conditions.\n\n"
            "## Main results\nThe decisive supplied comparison.\n\n"
            "![[notes/derivation.md#Derivation|Full derivation]]\n\n"
            "## Interpretation and limitations\nBounded interpretation.")
    output = {"title": "A bounded result", "summary": body, "graph_summary": copy.deepcopy(GUIDE)}
    state = {"role_outputs": {"reporter": output}, "last_seq": 1,
             "hypothesis_lock": {"id": "H-0001", "hypothesis": "Frozen question", "scope": "Frozen scope"}}
    branch = {"title": "Frozen question", "hypothesis_ref": "H-0001", "decisive_test": "Frozen comparison"}
    reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Original Judge rationale"},
                                 "Frozen question", "Frozen hypothesis", "supported")
    assert Path(branch["note_path"]) == notes / "outcomes/1.md"
    assert not list(notes.glob("1--*.md"))
    assert state["cycle_outcome_record"]["report_ref"] == "notes/outcomes/1.md"
    frozen = state["cycle_outcome_record"]["report_context"]["documents"][0]
    assert frozen["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert "Exact supplied identity" in frozen["content"]
    assert "Unrelated material" not in frozen["content"]
    reporter._commit_cycle_science(state, database, tmp_path / "exploration.canvas", state["cycle_outcome_record"], None)
    exported = json.loads((tmp_path / "exploration.canvas").read_text())
    assert '"report_context"' not in json.dumps(exported)
    saved = (notes / "outcomes/1.md").read_text()
    assert saved.count(GUIDE["conclusion"]) == 1
    assert 'class="research-supplement"' in saved
    assert "Original Judge rationale" in saved
    assert saved.count("## 复现索引") == 1
    supplement.write_text("# Changed later\nA different unadjudicated claim.")
    reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Original Judge rationale"},
                                 "Frozen question", "Frozen hypothesis", "supported")
    before = database.read_bytes()
    report = load_report(database, "1")
    assert "Exact supplied identity" in report["content"]
    assert "different unadjudicated claim" not in report["content"]
    part = execute(database, "report-read", "1", section="Main results", max_content_chars=60)
    assert part["content"].startswith("## Main results")
    assert part["next_offset"] is not None
    remaining = execute(database, "report-read", "1", section="Main results", offset=part["next_offset"])
    assert "Exact supplied identity" in part["content"] + remaining["content"]
    assert database.read_bytes() == before
    assert report_cycle_for_path(database, "notes/outcomes/1.md") == "1"
    with ScienceGraphStore(database, readonly=True) as store:
        assert store.agent_hypothesis_detail("H-0001")["cycle_outcomes"][0]["report_ref"] == "notes/outcomes/1.md"


def test_supplements_are_explicit_bounded_and_ignore_literal_code(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    secret = tmp_path.parent / (tmp_path.name + "-outside.md")
    secret.write_text("outside")
    try:
        for reference in (str(secret), "notes/missing.md"):
            with pytest.raises(ValueError, match="outside task"):
                snapshot_documents(f"![[{reference}]]", task_root=tmp_path,
                                   note_path=notes / "outcomes/1.md", materials=[])
        literal = "```markdown\n![[notes/missing.md]]\n```\n`![[notes/missing.md]]`"
        assert snapshot_documents(literal, task_root=tmp_path, note_path=notes / "outcomes/1.md", materials=[]) == []
        assert expand_documents(literal, []) == literal
    finally:
        secret.unlink()


def test_registered_execution_document_resolves_its_relative_alias(tmp_path: Path):
    vault = tmp_path / "vault"
    document = tmp_path / "execution" / "analysis" / "proof.md"
    document.parent.mkdir(parents=True)
    raw = "# Proof\n\n## Result\nThe supplied identity.\n\n## Other\nOther content.\n"
    document.write_text(raw)
    asset = {"path": str(document), "relative_path": "analysis/proof.md",
             "kind": "document", "status": "available"}
    body = "![[analysis/proof.md#Result|Existing proof]]"
    rows = snapshot_documents(body, task_root=vault,
                              note_path=vault / "notes/outcomes/1.md",
                              materials=[{"assets": [asset, dict(asset)]}])
    assert len(rows) == 1
    assert rows[0]["reference"] == "analysis/proof.md#Result"
    assert rows[0]["source_ref"] == str(document)
    assert rows[0]["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert "The supplied identity." in rows[0]["content"]
    assert "Other content." not in rows[0]["content"]
    assert "The supplied identity." in expand_documents(body, rows)


@pytest.mark.parametrize("overrides", [
    {"status": "drifted"}, {"status": "missing"}, {"kind": "figure"},
    {"relative_path": "other/proof.md"}, {"relative_path": ""},
])
def test_execution_document_alias_requires_available_exact_document(tmp_path: Path, overrides):
    vault = tmp_path / "vault"
    document = tmp_path / "execution" / "proof.md"
    document.parent.mkdir()
    document.write_text("# Existing proof\n")
    asset = {"path": str(document), "relative_path": "analysis/proof.md",
             "kind": "document", "status": "available", **overrides}
    with pytest.raises(ValueError, match="missing, ambiguous or outside task"):
        snapshot_documents("![[analysis/proof.md]]", task_root=vault,
                           note_path=vault / "notes/outcomes/1.md",
                           materials=[{"assets": [asset]}])


def test_execution_document_alias_rejects_two_distinct_registered_paths(tmp_path: Path):
    vault = tmp_path / "vault"
    assets = []
    for name in ("first", "second"):
        document = tmp_path / name / "proof.md"
        document.parent.mkdir()
        document.write_text("# " + name + "\n")
        assets.append({"path": str(document), "relative_path": "analysis/proof.md",
                       "kind": "document", "status": "available"})
    with pytest.raises(ValueError, match="ambiguous"):
        snapshot_documents("![[analysis/proof.md]]", task_root=vault,
                           note_path=vault / "notes/outcomes/1.md",
                           materials=[{"assets": assets}])


def test_report_uses_the_original_markdown_body_without_composing_a_second_body(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    old = tmp_path / "notes" / "old.md"
    old.parent.mkdir()
    body = "## 结论摘要\nLegacy abstract.\n\n## 主要结果\nOriginal full scientific body."
    old.write_text("# Old report\n" + body + "\n\n## 复现索引\nOriginal data pointers.")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.append_outcome(outcome_id="old", hypothesis_id="H-0001", cycle_id="1", verdict="open",
                             summary=body, graph_summary=GUIDE, report_ref="notes/old.md", record_only=True)
    before = database.read_bytes(), old.read_bytes()
    report = load_report(database, "1")
    assert report["content"].count("Original full scientific body.") == 1
    assert report["content"] == old.read_text()
    assert "Original data pointers" in report["content"]
    assert report_cycle_for_path(database, "notes/old.md") == "1"
    assert (database.read_bytes(), old.read_bytes()) == before


def test_section_ids_ignore_fences_and_disambiguate_repeated_titles():
    text = "# Report\n\n## Result\nFirst.\n```python\n## Not a section\n```\n## Result\nSecond.\n"
    assert [s["id"] for s in sections(text)] == ["report", "result", "result-1"]


def test_unadjudicated_report_never_acquires_judge_verdict(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    note = tmp_path / "notes/preserved.md"
    note.parent.mkdir()
    note.write_text("# 阶段性研究记录 · 未裁决\nUnfinished research")
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
        store.record_preserved_cycle(hypothesis_id="H-0001", cycle_id="1", summary="Unfinished research",
            report_ref="notes/preserved.md", source_attention_code="investigator_budget_review_rejected",
            prior_judge_objection="Missing frozen comparison", stage_output_sha256="a" * 64)
    report = load_report(database, "1")
    assert report["judge_verdict"] is None
    assert report["adjudication_status"] == "unadjudicated"
    assert "阶段性研究记录 · 未裁决" in report["content"]
    assert "Judge verdict:" not in report["content"]
    assert "Unfinished research" in report["content"]


def _final_report_spec(tmp_path: Path, *, sealed: bool = True):
    plan = tmp_path / "research_plan.md"
    plan.write_text("# Research question\nA synthetic bounded question.\n", encoding="utf-8")
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Synthetic claim")
        if sealed:
            store.seal_goal(goal_id="synthetic-goal", source_cycle="1",
                research_plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
                judge_receipt_ref=None, detail={"rationale": "Synthetic final gate passed"})
    return SimpleNamespace(research_root=tmp_path, document=plan, graph_database=database,
                           task_home=tmp_path, branch_canvas=None, notes_mirror_dir=None,
                           master_id=1, goal_id="synthetic-goal", task_mode=True)


def test_task_report_queue_requires_seal_and_preserves_scientific_bytes(tmp_path: Path):
    from theory_master.final_report import ensure_final_report_job
    unsealed = tmp_path / "unsealed"
    unsealed.mkdir()
    assert ensure_final_report_job(_final_report_spec(unsealed, sealed=False)) is None
    assert not (unsealed / ".theory-master/final-reports").exists()
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    spec = _final_report_spec(sealed)
    before = spec.graph_database.read_bytes(), spec.document.read_bytes()
    first = ensure_final_report_job(spec)
    assert first["state"] == "queued"
    assert first["attempt"] == 0
    assert ensure_final_report_job(spec) == first
    assert len(list((sealed / ".theory-master/final-reports/jobs").glob("*.json"))) == 1
    assert (spec.graph_database.read_bytes(), spec.document.read_bytes()) == before


def _fake_final_report_modules(monkeypatch, *, build, generate):
    import sys
    import types
    package = types.ModuleType("graph_research")
    package.__path__ = []
    snapshot_module = types.ModuleType("graph_research.final_report_snapshot")
    snapshot_module.build_snapshot = build
    report_module = types.ModuleType("graph_research.final_report")
    report_module.generate_report = generate
    monkeypatch.setitem(sys.modules, "graph_research", package)
    monkeypatch.setitem(sys.modules, snapshot_module.__name__, snapshot_module)
    monkeypatch.setitem(sys.modules, report_module.__name__, report_module)


def test_task_report_generation_is_explicit_and_quality_is_independent(tmp_path: Path, monkeypatch):
    from theory_master.final_report import ensure_final_report_job, generate_final_report
    spec = _final_report_spec(tmp_path)
    notebook = tmp_path / "notebook"
    notebook.mkdir()
    spec.branch_canvas = notebook / "research.canvas"
    calls = []
    def build(database, **kwargs):
        calls.append(("snapshot", kwargs))
        assert kwargs["require_seal"] is True
        assert notebook in kwargs["allowed_roots"]
        return {"fingerprint": "synthetic-fingerprint"}
    def generate(snapshot, destination, backend):
        calls.append(("generate", destination))
        assert backend is sentinel
        return {"state": "needs_revision", "snapshot_fingerprint": snapshot["fingerprint"],
                "review_path": str(destination / "review.json")}
    sentinel = object()
    _fake_final_report_modules(monkeypatch, build=build, generate=generate)
    before = spec.graph_database.read_bytes(), spec.document.read_bytes()
    ensure_final_report_job(spec)
    assert calls == []
    report = generate_final_report(spec, sentinel)
    assert report["state"] == "needs_revision"
    assert report["attempt"] == 1
    assert len(calls) == 2
    assert generate_final_report(spec, sentinel) == report
    assert len(calls) == 2
    revised = generate_final_report(spec, sentinel, regenerate=True)
    assert revised["attempt"] == 2
    assert revised["output_dir"] != report["output_dir"]
    assert (spec.graph_database.read_bytes(), spec.document.read_bytes()) == before


def test_task_report_failure_never_changes_completion_or_retries(tmp_path: Path, monkeypatch):
    from theory_master.final_report import generate_final_report
    spec = _final_report_spec(tmp_path)
    calls = []
    def fail(*args, **kwargs):
        calls.append("writer")
        raise RuntimeError("synthetic report failure")
    _fake_final_report_modules(monkeypatch, build=lambda *a, **kw: {"fingerprint": "one"}, generate=fail)
    before = spec.graph_database.read_bytes()
    result = generate_final_report(spec, object())
    assert result["state"] == "failed"
    assert "synthetic report failure" in result["error"]
    assert calls == ["writer"]
    assert spec.graph_database.read_bytes() == before
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        assert store.goal_terminal()["verdict"] == "passed"


def test_task_report_concurrent_consumer_never_calls_another_writer(tmp_path: Path, monkeypatch):
    from theory_master.final_report import generate_final_report
    spec = _final_report_spec(tmp_path)
    calls = []
    def generate(*args):
        calls.append("one writer")
        second = generate_final_report(spec, object())
        assert second["busy"] is True
        assert second["state"] == "running"
        return {"state": "ready"}
    _fake_final_report_modules(monkeypatch, build=lambda *a, **kw: {}, generate=generate)
    assert generate_final_report(spec, object())["state"] == "ready"
    assert calls == ["one writer"]


def test_ready_report_returns_before_backend_initialization(tmp_path: Path, monkeypatch):
    from theory_master.final_report import generate_final_report
    spec = _final_report_spec(tmp_path)
    _fake_final_report_modules(monkeypatch, build=lambda *a, **kw: {},
                               generate=lambda *args: {"state": "ready", "report_path": "first-report.md"})
    original = generate_final_report(spec, object())
    initialized = []
    def unavailable_backend():
        initialized.append(True)
        raise ValueError("synthetic route configuration is temporarily unavailable")
    assert generate_final_report(spec, backend_factory=unavailable_backend) == original
    assert initialized == []
    # An explicitly requested new attempt owns and records its initialization failure.
    failed = generate_final_report(spec, backend_factory=unavailable_backend, regenerate=True)
    assert failed["state"] == "failed" and failed["attempt"] == 2
    assert "temporarily unavailable" in failed["error"]
    assert "report_path" not in failed
    assert initialized == [True]


def test_busy_report_never_initializes_or_overwrites_running_job(tmp_path: Path, monkeypatch):
    from theory_master.final_report import generate_final_report, report_registry
    spec = _final_report_spec(tmp_path)
    initialized = []
    def unavailable_backend():
        initialized.append(True)
        raise ValueError("synthetic initialization failure must not overwrite the owner")
    def generate(*args):
        competing = generate_final_report(spec, backend_factory=unavailable_backend)
        assert competing["busy"] is True and competing["state"] == "running"
        saved = json.loads((report_registry(spec) / "jobs" / f"{competing['job_id']}.json").read_text())
        assert saved["state"] == "running" and saved["attempt"] == 1
        assert "error" not in saved
        return {"state": "ready"}
    _fake_final_report_modules(monkeypatch, build=lambda *a, **kw: {}, generate=generate)
    result = generate_final_report(spec, object())
    assert result["state"] == "ready" and result["attempt"] == 1
    assert initialized == []


def test_task_report_regeneration_failure_keeps_old_artifacts_out_of_new_result(tmp_path: Path, monkeypatch):
    from theory_master.final_report import generate_final_report
    spec = _final_report_spec(tmp_path)
    broken = False
    def build(*args, **kwargs):
        if broken:
            raise OSError("synthetic source unavailable")
        return {"fingerprint": "first-snapshot"}
    def generate(snapshot, destination, backend):
        destination.mkdir(parents=True)
        report = destination / "report.md"
        report.write_text("Original report", encoding="utf-8")
        return {"state": "ready", "report_path": str(report),
                "html_path": str(destination / "report.html"), "review_path": str(destination / "review.json"),
                "snapshot_fingerprint": snapshot["fingerprint"], "prompt_metadata": {"writer": "old"},
                "validation_errors": [], "report_sha256": "old-report"}
    _fake_final_report_modules(monkeypatch, build=build, generate=generate)
    original = generate_final_report(spec, object())
    broken = True
    retry = generate_final_report(spec, object(), regenerate=True)
    assert retry["state"] == "failed"
    assert retry["attempt"] == 2
    assert "synthetic source unavailable" in retry["error"]
    for field in ("report_path", "html_path", "review_path", "snapshot_fingerprint", "prompt_metadata",
                  "validation_errors", "report_sha256"):
        assert field not in retry
    assert Path(original["report_path"]).read_text() == "Original report"


def test_task_report_queue_rejects_symlink_registry(tmp_path: Path):
    from theory_master.final_report import ensure_final_report_job
    spec = _final_report_spec(tmp_path)
    runtime = tmp_path / ".theory-master"
    runtime.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime / "final-reports").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        ensure_final_report_job(spec)
    assert list(outside.iterdir()) == []


def _registered_report_spec(tmp_path):
    spec = _final_report_spec(tmp_path)
    (tmp_path / ".theory-master").mkdir(exist_ok=True)
    (tmp_path / ".theory-master/task.json").write_text("{}")
    return spec


def test_task_report_dispatch_uses_independent_service_and_preserves_environment(tmp_path: Path, monkeypatch):
    from theory_master import final_report, worker_launch
    spec = _registered_report_spec(tmp_path)
    launched = []
    def launch(command, **kwargs):
        launched.append((command, kwargs))
        return "tm-final-report-1234"
    monkeypatch.setattr(worker_launch, "_launch_detached_service", launch)
    monkeypatch.setattr(worker_launch, "_task_scoped_process_env", lambda root: {
        "PYTHONPATH": "/synthetic/existing-import-root", "THEORY_MASTER_EMAIL_TERMINAL": "0"})
    monkeypatch.setattr(final_report, "_launcher_alive", lambda receipt: bool(receipt))
    result = final_report.launch_final_report_job(spec)
    assert result["launch_status"] == "started"
    command, options = launched[0]
    assert command[1:] == ["-m", "theory_master.final_report", "--task", str(tmp_path), "--generate"]
    assert options["kind"] == "final-report"
    source_root = str(Path(final_report.__file__).resolve().parent.parent)
    assert options["env"]["PYTHONPATH"] == source_root + final_report.os.pathsep + "/synthetic/existing-import-root"
    assert options["env"]["THEORY_MASTER_EMAIL_TERMINAL"] == "0"
    assert result["launcher"]["source_root"] == source_root
    assert result["launcher"]["unit"] == "tm-final-report-1234"
    assert final_report.launch_final_report_job(spec)["launch_status"] == "running"
    assert len(launched) == 1


def test_failed_report_dispatch_requires_explicit_retry_and_remains_idempotent(tmp_path: Path, monkeypatch):
    from theory_master import final_report, worker_launch
    spec = _registered_report_spec(tmp_path)
    job = final_report.ensure_final_report_job(spec)
    path = final_report.report_registry(spec) / "jobs" / f"{job['job_id']}.json"
    job.update(state="failed", attempt=1)
    final_report.atomic_json(path, job)
    launched = []
    monkeypatch.setattr(worker_launch, "_launch_detached_service",
                        lambda command, **kwargs: launched.append(command) or "tm-final-report-1234")
    monkeypatch.setattr(final_report, "_launcher_alive", lambda receipt: bool(receipt))
    assert final_report.launch_final_report_job(spec)["state"] == "failed"
    assert not launched
    assert final_report.launch_final_report_job(spec, retry_failed=True)["launch_status"] == "started"
    assert json.loads(path.read_text())["attempt"] == 1
    assert final_report.launch_final_report_job(spec, retry_failed=True)["launch_status"] == "running"
    assert len(launched) == 1


@pytest.mark.parametrize("state", ["running", "queued"])
def test_dead_consumer_is_retryable_and_read_projection_does_not_write(tmp_path, monkeypatch, state):
    from theory_master import final_report, worker_launch
    from theory_master.final_report_publication import final_report_descriptor
    spec = _registered_report_spec(tmp_path)
    job = final_report.ensure_final_report_job(spec)
    registry = final_report.report_registry(spec)
    path = registry / "jobs" / f"{job['job_id']}.json"
    job.update(state=state, attempt=1)
    final_report.atomic_json(path, job)
    final_report.atomic_json(registry / f"launch-{job['job_id']}.json", {"pid": 12345, "pid_start_ticks": 12})
    monkeypatch.setattr(final_report, "_launcher_alive", lambda receipt: False)
    before = path.read_bytes(), spec.graph_database.read_bytes()
    assert final_report_descriptor(tmp_path)["state"] == "failed"
    assert (path.read_bytes(), spec.graph_database.read_bytes()) == before
    assert final_report.launch_final_report_job(spec)["state"] == "failed"
    assert json.loads(path.read_text())["retryable"] is True
    monkeypatch.setattr(worker_launch, "_launch_detached_service", lambda *a, **kw: "tm-final-report-retry")
    assert final_report.launch_final_report_job(spec, retry_failed=True)["launch_status"] == "started"
    assert spec.graph_database.read_bytes() == before[1]


def test_direct_cli_generation_lock_prevents_false_orphan_and_second_launch(tmp_path, monkeypatch):
    from theory_master import final_report, worker_launch
    spec = _registered_report_spec(tmp_path)
    job = final_report.ensure_final_report_job(spec)
    registry = final_report.report_registry(spec)
    path = registry / "jobs" / f"{job['job_id']}.json"
    job.update(state="running", attempt=1)
    final_report.atomic_json(path, job)
    monkeypatch.setattr(worker_launch, "_launch_detached_service", lambda *a, **kw: pytest.fail("duplicate launch"))
    with final_report._lock(registry / f"{job['job_id']}.lock"):
        assert final_report.report_job_status(tmp_path, job)["state"] == "running"
        assert final_report.launch_final_report_job(spec, retry_failed=True)["launch_status"] == "running"
    assert json.loads(path.read_text()) == job
    with final_report._lock(registry / "dispatch.lock"):
        assert final_report.report_job_status(tmp_path, job)["state"] == "running"


def test_service_probe_recognizes_startup_and_failure_without_saved_pid(monkeypatch):
    from theory_master import final_report, runtime_manager
    monkeypatch.setattr(runtime_manager, "process_identity_alive", lambda *a: False)
    for active, expected in [("activating", True), ("active", True), ("failed", False),
                             ("inactive", False), ("", True)]:
        monkeypatch.setattr(final_report.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=active))
        assert final_report._launcher_alive({"unit": "tm-final-report-1234"}) is expected
    def unavailable(*a, **kw):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(final_report.subprocess, "run", unavailable)
    assert final_report._launcher_alive({"unit": "tm-final-report-1234"}) is True


def test_completion_read_only_repairs_queue_and_dispatch_failure_is_isolated(tmp_path: Path, monkeypatch):
    from theory_master.codex_backend import CodexBackend
    from theory_master.workflow_goal import GoalCompletionMixin
    from theory_master import final_report
    spec = _final_report_spec(tmp_path)
    class Completion(GoalCompletionMixin):
        def _graph_database_path(self): return spec.graph_database
        def _emit(self, *args): pass
        def _close_open_frontier_tips(self): return []
    owner = Completion()
    owner.backend = object.__new__(CodexBackend)
    owner.spec = spec
    owner.cycle_dir = tmp_path / "1"
    launched = []
    def fail(*args, **kwargs):
        launched.append(True)
        raise OSError("synthetic launcher unavailable")
    monkeypatch.setattr(final_report, "launch_final_report_job", fail)
    restored = owner._latest_goal_completion_state()
    assert restored["goal_completion"]["verdict"] == "passed"
    assert restored["final_report"]["state"] == "queued"
    assert launched == []
    state = {}
    with ScienceGraphStore(spec.graph_database, readonly=True) as store:
        seal = store.goal_terminal()
    result = {"verdict": "passed"}
    assert owner._apply_goal_completion(state, result, "passed", "Synthetic", seal) is result
    assert launched == [True]
    assert state["goal_status"] == "passed"
    assert state["resumable"] is False
    assert state["final_report"]["state"] == "unavailable"


def test_fake_or_missing_workflow_backend_never_launches_report_models(tmp_path: Path, monkeypatch):
    from theory_master.backend_fakes import FakeBackend
    from theory_master.workflow_goal import GoalCompletionMixin
    from theory_master import final_report
    spec = _final_report_spec(tmp_path)
    owner = GoalCompletionMixin()
    owner.spec = spec
    owner._graph_database_path = lambda: spec.graph_database
    def forbidden(*args, **kwargs):
        pytest.fail("offline scientific backends must never dispatch real report models")
    monkeypatch.setattr(final_report, "launch_final_report_job", forbidden)
    owner.backend = FakeBackend(reworks=0)
    state = {}
    owner._queue_final_report(state, launch=True)
    assert state["final_report"]["state"] == "queued"
    del owner.backend
    owner._queue_final_report(state, launch=True)
    assert state["final_report"]["state"] == "queued"


def test_final_report_models_use_task_routes_account_policy_and_existing_provider_config(tmp_path: Path, monkeypatch):
    from theory_master import backend_config, codex_accounts, codex_backend
    from theory_master.final_report import final_report_backend_options
    spec = _final_report_spec(tmp_path)
    task_env = tmp_path / ".theory-master/env"
    task_env.write_text("# Synthetic task route environment\n", encoding="utf-8")
    seen = []
    def resolve(**kwargs):
        assert kwargs == {"task_env_file": task_env, "task_route_override_root": tmp_path}
        return {"reporter": backend_config.RoleRoute("gpt-6-luna", "CodexHTTPS", reasoning_effort="low"),
                "judge": backend_config.RoleRoute("gpt-6.1-sol", "CodexHTTPS", reasoning_effort="high")}
    def config(routes):
        seen.append(routes)
        return ('model_providers.openai_https.requires_openai_auth=true',), {"PRIVATE_PROVIDER_TEST_TOKEN": "synthetic-secret"}
    monkeypatch.setattr(backend_config, "resolve_routes", resolve)
    monkeypatch.setattr(codex_accounts, "selected_account", lambda: "2")
    monkeypatch.delenv("THEORYMASTER_WORKER_PINNED_ACCOUNT", raising=False)
    monkeypatch.setattr(codex_backend, "_codex_home", lambda: tmp_path / "existing-auth-runtime")
    monkeypatch.setattr(codex_backend.CodexBackend, "_runtime_config", staticmethod(config))
    options = final_report_backend_options(spec)
    assert options["model_by_role"] == {"writer": "gpt-6-luna", "reviewer": "gpt-6-sol"}
    assert options["reasoning_effort_by_role"] == {"writer": "low", "reviewer": "high"}
    assert list(seen[0]) == ["reporter"] and list(seen[1]) == ["judge"]
    for role in ("writer", "reviewer"):
        assert options["config_overrides_by_role"][role]["model_provider"] == "openai_https"
        assert options["config_overrides_by_role"][role]["model_providers"]["openai_https"]["requires_openai_auth"] is True
        assert options["env_by_role"][role]["CODEX_HOME"] == str(tmp_path / "existing-auth-runtime")
        assert options["env_by_role"][role]["PRIVATE_PROVIDER_TEST_TOKEN"] == "synthetic-secret"
        assert "synthetic-secret" not in json.dumps(options["config_overrides_by_role"])
    # Account-1 retains the requested supported model under existing policy.
    monkeypatch.setenv("THEORYMASTER_WORKER_PINNED_ACCOUNT", "1")
    assert final_report_backend_options(spec)["model_by_role"]["reviewer"] == "gpt-6.1-sol"


def test_final_report_provider_ids_follow_existing_account_and_optional_mappings(tmp_path: Path, monkeypatch):
    from theory_master import backend_config, codex_accounts, codex_backend
    from theory_master.final_report import final_report_backend_options
    spec = _final_report_spec(tmp_path)
    monkeypatch.setattr(backend_config, "resolve_routes", lambda **kwargs: {
        "reporter": backend_config.RoleRoute("public-synthetic-model", "ConfiguredToyProvider", reasoning_effort="medium"),
        "judge": backend_config.RoleRoute("gpt-6-sol", "Codex", reasoning_effort=None)})
    monkeypatch.setattr(codex_accounts, "selected_account", lambda: "1")
    monkeypatch.delenv("THEORYMASTER_WORKER_PINNED_ACCOUNT", raising=False)
    monkeypatch.setattr(codex_backend, "_codex_home", lambda: tmp_path / "existing-auth-runtime")
    monkeypatch.setattr(codex_backend.CodexBackend, "_runtime_config", staticmethod(lambda routes: ((), {})))
    options = final_report_backend_options(spec)
    assert options["config_overrides_by_role"]["writer"] == {"model_provider": "ConfiguredToyProvider"}
    assert options["config_overrides_by_role"]["reviewer"] == {}
    assert options["reasoning_effort_by_role"]["reviewer"] == "medium"
