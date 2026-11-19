"""Offline end-to-end checks for the shared Literature role service."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import threading

import pytest

from theory_master.literature_service import LiteratureServiceError, _locked, run_literature


PAPERS = [
    {"title": "Foundational mechanism", "doi": "10.1234/foundation", "abstract": "A bounded mechanism is proposed.",
     "open_access_url": "https://example.invalid/foundation.pdf", "is_open_access": True, "score": 999},
    {"title": "Discriminating counterexample", "doi": "10.1234/counterexample", "abstract": "A counterexample limits the mechanism.",
     "open_access_url": "https://example.invalid/counterexample.pdf", "is_open_access": True, "score": 1},
]


class Retriever:
    def __init__(self, *, full_text: bool = True):
        self.calls: list[dict] = []
        self.full_text = full_text

    def __call__(self, task: dict, workspace: Path) -> dict:
        self.calls.append(json.loads(json.dumps(task)))
        home = workspace / "literature" / "sdk_retrieval" / task["source_gap_id"]
        retrieval = home / "retrieval"
        retrieval.mkdir(parents=True, exist_ok=True)
        papers = PAPERS
        if task["task_type"] == "targeted_source":
            papers = [paper for paper in PAPERS if paper["doi"] == task["source_lock"]["doi"]]
        (retrieval / "papers.json").write_text(json.dumps(papers), encoding="utf-8")
        (retrieval / "evidence_index.md").write_text("# Fixture papers\n", encoding="utf-8")
        reading = {}
        if task["task_type"] == "targeted_source":
            directory = home / "reading"
            directory.mkdir(exist_ok=True)
            paper = papers[0]
            text = directory / "paper-01.md"
            if self.full_text:
                text.write_text("# " + paper["title"] + "\n\n" + paper["abstract"] + "\nFull source passage.", encoding="utf-8")
            manifest = directory / "pdf_manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["paper", "doi", "status", "markdown_path"])
                writer.writeheader()
                writer.writerow({"paper": paper["title"], "doi": paper["doi"],
                    "status": "read" if self.full_text else "unavailable", "markdown_path": str(text)})
            reading = {"pdf_manifest": str(manifest)}
        return {"status": "ready", "transport_ok": True, "output_dir": str(home),
                "papers_json": str(retrieval / "papers.json"), "evidence_index_md": str(retrieval / "evidence_index.md"),
                "reading": reading, "metadata_hit_count": len(papers), "fulltext_read_count": int(bool(reading) and self.full_text)}


class LiteratureBackend:
    def __init__(self, *, remaining: bool = False, false_fulltext: bool = False, fail_once: bool = False):
        self.calls: list[dict] = []
        self.remaining = remaining
        self.false_fulltext = false_fulltext
        self.fail_once = fail_once
        self.failed = False

    def ensure_thread(self, role: str, thread_id: str | None, model: str, provider: str) -> str:
        assert role == "literature"
        return thread_id or "fixture-literature-thread"

    def invoke(self, role: str, prompt: str, thread_id: str, model: str, provider: str, *, cancel_event=None):
        assert role == "literature"
        context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
        stage = prompt.split("Current task: ", 1)[1].split(" ", 1)[0]
        self.calls.append({"role": role, "context": context, "stage": stage, "thread_id": thread_id,
                           "model": model, "provider": provider, "prompt": prompt})
        if self.fail_once and not self.failed and stage == "targeted_source":
            self.failed = True
            raise RuntimeError("fixture transient model failure")
        synthesis = "Synthesize this job" in context.get("search_target", "")
        if stage == "targeted_source":
            source = context["source_lock"]
            output = {"evidence_records": [self.record(source)], "remaining_scientific_question": ""}
        else:
            output = {"evidence_records": [self.record(PAPERS[0])] if synthesis else [],
                "remaining_scientific_question": "Which conflicting assumptions require a complementary original source?" if self.remaining else "",
                "landscape": {"field_scope": "Mechanisms and counterexamples",
                    "major_routes": [{"name": "Mechanism", "focus": "Compare foundational assumptions with a counterexample.",
                        "representative_papers": [{"source_ref": PAPERS[0]["doi"], "relevance": "Foundational route", "source_access": "abstract"}]}],
                    "representative_papers": [{"source_ref": PAPERS[0]["doi"], "relevance": "Foundation", "source_access": "abstract"},
                        {"source_ref": PAPERS[1]["doi"], "relevance": "Decision-relevant counterexample", "source_access": "abstract"}],
                    "open_questions": ["Does the counterexample share the same assumptions?"],
                    "relation_to_current_research": "The sources disagree within different applicability limits.",
                    "recommended_reading_order": [PAPERS[1]["title"], PAPERS[0]["title"]]}}
        return thread_id, json.dumps(output)

    def record(self, paper: dict) -> dict:
        return {"source_ref": paper["doi"], "scientific_insight": "The source defines the mechanism's applicability.",
            "interpretation": "Assumptions differ across the selected original sources.", "limitations": "Fixture source only.",
            "source_access": "full_text" if self.false_fulltext else "abstract", "exact_quote": paper["abstract"],
            "locator": {"section": "Abstract"}}


def request(**extra):
    return {"query": "Compare the mechanism and its counterexample", "scope": "A bounded scientific question", **extra}


def test_real_role_assembly_bounded_retrieval_reading_synthesis_and_idempotency(tmp_path):
    backend = LiteratureBackend()
    retrieve = Retriever()
    events = []
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve,
                            model="fixture-model", reasoning_effort="high", on_event=events.append)
    assert result["status"] == "completed", result
    assert result["literature_agent_invoked"] and result["model_invoked"]
    assert result["agent_turns"] == 4
    assert not result["scientific_evidence_created"]
    assert result["thread_id"] == "fixture-literature-thread"
    assert [row["stage"] for row in backend.calls] == ["landscape", "targeted_source", "targeted_source", "landscape"]
    assert len({row["thread_id"] for row in backend.calls}) == 1
    # Model reading order deliberately opposes transport score order.
    assert retrieve.calls[1]["source_lock"]["doi"] == PAPERS[1]["doi"]
    assert retrieve.calls[1]["source_lock"]["open_access_url"] == PAPERS[1]["open_access_url"]
    assert result["source_access_counts"]["full_text"] == 2
    corpus = Path(result["manifest"]).parent / "corpus"
    assert PAPERS[0]["title"] in (corpus / "reading/reading_index.md").read_text()
    focus = json.loads((corpus / "retrieval/paper_focus.json").read_text())
    assert focus and focus[0]["doi"] == PAPERS[0]["doi"]
    state = json.loads(Path(result["manifest"]).read_text())
    assert state["route"] == {"model": "fixture-model", "provider": backend.calls[0]["provider"], "reasoning_effort": "high"}
    for row in state["invocations"]:
        assert row["skills"] == ["tm-scholar", "pdf-reading", "research-lit", "novelty-check"]
        assert "theory-master/role/literature/" in row["contract"]["contract_id"]
        developer = Path(row["developer_path"]).read_text()
        assert "# 身份：Literature" in developer
        assert "research-lit/SKILL.md" in developer
        assert "不作科学裁决" in developer
        assert "Current objective:" in Path(row["prompt_path"]).read_text()
    assert "Source-grounded observations" in Path(result["report"]).read_text()
    assert [event["event"] for event in events].count("literature.agent_started") == 4
    resumed = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve,
                             model="fixture-model", reasoning_effort="high")
    assert resumed == result
    assert len(backend.calls) == 4 and len(retrieve.calls) == 3
    assert not list((tmp_path / "job").rglob("science_graph.sqlite3"))
    assert not list((tmp_path / "job").rglob("research_plan.md"))
    assert not list((tmp_path / "job").rglob("state.json"))


def test_actual_unresolved_question_drives_complementary_search_then_budget_pause(tmp_path):
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    result = run_literature(tmp_path / "job", request(max_rounds=2, max_sources=6), backend=backend, retriever=retrieve)
    assert result["status"] == "paused"
    assert result["stop_reason"] == "round_budget_exhausted"
    landscapes = [row for row in retrieve.calls if row["task_type"] == "landscape"]
    assert len(landscapes) == 2
    assert landscapes[1]["search_target"] == "Which conflicting assumptions require a complementary original source?"
    assert landscapes[1]["new_search_lead"] == result["remaining_scientific_question"]
    targets = [row for row in retrieve.calls if row["task_type"] == "targeted_source"]
    assert len(targets) == 2  # Do not reacquire the same source in round two.


def test_source_budget_is_an_execution_limit_not_a_claim_of_complete_review(tmp_path):
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    result = run_literature(tmp_path / "job", request(max_sources=1), backend=backend, retriever=retrieve)
    assert result["status"] == "paused" and result["stop_reason"] == "source_budget_exhausted"
    assert result["source_acquisition_attempts"] == 1
    assert result["remaining_scientific_question"]
    assert "paused" in Path(result["report"]).read_text()


def test_unanswered_model_turn_holds_without_retrieving_or_calling_again(tmp_path):
    backend, retrieve = LiteratureBackend(fail_once=True), Retriever()
    failed = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert failed["status"] == "failed"
    assert "fixture transient model failure" in failed["error"]
    first_retrievals = len(retrieve.calls)
    held = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert held["status"] == "failed" and "no durable response" in held["error"]
    assert len(retrieve.calls) == first_retrievals
    assert len(backend.calls) == 2  # discovery plus the original unanswered source turn
    state = json.loads(Path(held["manifest"]).read_text())
    assert state["invocations"][1]["attempts"] == 1
    assert len({row["thread_id"] for row in state["invocations"]}) == 1


def test_cancellation_before_work_and_resume(tmp_path):
    cancel = threading.Event()
    cancel.set()
    backend, retrieve = LiteratureBackend(), Retriever()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve, cancel_event=cancel)
    assert result["status"] == "paused" and result["stop_reason"] == "cancelled"
    assert not result["model_invoked"] and not backend.calls and not retrieve.calls
    cancel.clear()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve, cancel_event=cancel)
    assert result["status"] == "completed"


def test_cancellation_between_agent_stages_retains_completed_delivery(tmp_path):
    cancel = threading.Event()
    backend, retrieve = LiteratureBackend(), Retriever()
    def event(value):
        if value["event"] == "literature.agent_completed":
            cancel.set()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve,
                            cancel_event=cancel, on_event=event)
    assert result["status"] == "paused" and result["agent_turns"] == 1
    cancel.clear()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve, cancel_event=cancel)
    assert result["status"] == "completed" and len(backend.calls) == 4


def test_metadata_or_abstract_cannot_be_promoted_to_full_text_by_agent(tmp_path):
    result = run_literature(tmp_path / "job", request(), backend=LiteratureBackend(false_fulltext=True),
                            retriever=Retriever(full_text=False))
    assert result["status"] == "paused" and result["stop_reason"] == "source_access_unverified"
    assert result["source_access_counts"]["full_text"] == 0
    record = result["output"]["evidence_records"][0]
    assert record["declared_source_access"] == "full_text"
    assert record["source_access"] == "unavailable"
    assert not record["access_assessment"]["quote_located"]
    assert "retain as unverified" in record["limitations"]


def test_fulltext_receipt_requires_real_nonempty_materialized_text_and_quote(tmp_path):
    result = run_literature(tmp_path / "job", request(), backend=LiteratureBackend(false_fulltext=True), retriever=Retriever())
    record = result["output"]["evidence_records"][0]
    assert record["source_access"] == "full_text"
    assert record["access_assessment"]["quote_located"]
    assert not record["access_assessment"]["locator_semantically_checked"]


def test_invented_quote_remains_unverified_even_when_pdf_text_exists(tmp_path):
    class UnquotedBackend(LiteratureBackend):
        def record(self, paper):
            row = super().record(paper)
            row["exact_quote"] = "This quotation does not occur in any materialized source."
            return row
    result = run_literature(tmp_path / "job", request(), backend=UnquotedBackend(false_fulltext=True), retriever=Retriever())
    assert result["output"]["evidence_records"][0]["source_access"] == "unavailable"


def test_contract_violation_is_failure_not_a_silent_success(tmp_path):
    class InvalidBackend(LiteratureBackend):
        def invoke(self, *args, **kwargs):
            thread, raw = super().invoke(*args, **kwargs)
            value = json.loads(raw)
            value["scientific_verdict"] = "supported"
            return thread, json.dumps(value)
    result = run_literature(tmp_path / "job", request(), backend=InvalidBackend(), retriever=Retriever())
    assert result["status"] == "failed" and "unsupported fields" in result["error"]
    assert result["agent_turns"] == 0


def test_missing_or_corrupted_saved_delivery_cannot_be_replayed_as_completed(tmp_path):
    backend, retrieve = LiteratureBackend(fail_once=True), Retriever()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    state = json.loads(Path(result["manifest"]).read_text())
    Path(state["invocations"][0]["output_path"]).unlink()
    resumed = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert resumed["status"] == "failed"
    assert "absent" in resumed["error"]
    assert len(backend.calls) == 2


def test_job_scope_binding_and_no_overwrite_of_scientific_task(tmp_path):
    root = tmp_path / "scientific-task"
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text("# Original science", encoding="utf-8")
    with pytest.raises(LiteratureServiceError, match="separate Literature job"):
        run_literature(root, request(), backend=LiteratureBackend(), retriever=Retriever())
    assert plan.read_text() == "# Original science"
    result = run_literature(tmp_path / "job", request(), backend=LiteratureBackend(remaining=True), retriever=Retriever())
    with pytest.raises(LiteratureServiceError, match="assigned question"):
        run_literature(tmp_path / "job", request(query="An unrelated question"), backend=LiteratureBackend(), retriever=Retriever())
    with pytest.raises(LiteratureServiceError, match="original model"):
        run_literature(tmp_path / "job", request(), backend=LiteratureBackend(), retriever=Retriever(), model="changed-model")
    assert Path(result["manifest"]).is_file()


def test_same_job_concurrent_execution_is_rejected(tmp_path):
    with _locked(tmp_path / "job"):
        with pytest.raises(LiteratureServiceError, match="already running"):
            run_literature(tmp_path / "job", request(), backend=LiteratureBackend(), retriever=Retriever())


def test_transport_cannot_smuggle_external_artifact_paths(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(PAPERS))
    def retrieve(task, root):
        return {"output_dir": str(root), "papers_json": str(outside)}
    backend = LiteratureBackend()
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert result["status"] == "failed" and "escapes" in result["error"]
    assert not backend.calls


def test_preparation_sdk_manifest_reuses_attested_candidates_without_repeat_discovery(tmp_path):
    preparation = tmp_path / "preparation"
    old_job = preparation / "literature" / "old-job"
    old_job.mkdir(parents=True)
    retrieve = Retriever()
    transport = retrieve({"source_gap_id": "old-discovery", "task_type": "landscape"}, old_job)
    artifacts = [{"path": str(path.relative_to(preparation)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in old_job.rglob("*") if path.is_file()]
    manifest = old_job / "manifest.json"
    manifest.write_text(json.dumps({"schema": "sciver/preparation-literature/v1", "transport": transport, "artifacts": artifacts}))
    new_retrieve = Retriever()
    result = run_literature(tmp_path / "new-job", request(retrieval_manifests=[str(manifest)]),
                            backend=LiteratureBackend(), retriever=new_retrieve)
    assert result["status"] == "completed", result
    assert len(new_retrieve.calls) == 2
    assert all(task["task_type"] == "targeted_source" for task in new_retrieve.calls)
    assert result["source_count"] == 2


@pytest.mark.parametrize("kwargs", [{"max_rounds": 0}, {"max_rounds": 5}, {"max_sources": True}, {"max_sources": 13}])
def test_resource_caps_are_checked_before_dispatch(tmp_path, kwargs):
    backend, retrieve = LiteratureBackend(), Retriever()
    with pytest.raises(LiteratureServiceError):
        run_literature(tmp_path / "job", request(**kwargs), backend=backend, retriever=retrieve)
    assert not backend.calls and not retrieve.calls


def test_corrupted_job_manifest_is_not_overwritten_as_new_job(tmp_path):
    root = tmp_path / "job"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text("{incomplete", encoding="utf-8")
    with pytest.raises(LiteratureServiceError, match="cannot be replaced"):
        run_literature(root, request(), backend=LiteratureBackend(), retriever=Retriever())
    assert manifest.read_text() == "{incomplete"


def test_repeated_complementary_query_is_not_retrieved_again(tmp_path):
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    result = run_literature(tmp_path / "job", request(max_rounds=4), backend=backend, retriever=retrieve)
    assert result["status"] == "paused"
    assert len([row for row in retrieve.calls if row["task_type"] == "landscape"]) == 2


def test_retrieval_failure_can_resume_without_resetting_job_or_agent_thread(tmp_path):
    backend, retrieve = LiteratureBackend(), Retriever()
    failed_once = False
    def transient(task, workspace):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("fixture source API temporarily unavailable")
        return retrieve(task, workspace)
    failed = run_literature(tmp_path / "job", request(), backend=backend, retriever=transient)
    assert failed["status"] == "failed" and not failed["model_invoked"]
    resumed = run_literature(tmp_path / "job", request(), backend=backend, retriever=transient)
    assert resumed["status"] == "completed"
    assert resumed["job_id"] == failed["job_id"]


def test_default_codex_backend_receives_job_root_so_role_cwd_is_not_double_nested(tmp_path, monkeypatch):
    import theory_master.codex_backend
    created = []
    class StubCodexBackend(LiteratureBackend):
        def __init__(self, cwd, routes):
            super().__init__()
            self.cwd = cwd
            created.append({"cwd": cwd, "routes": routes})
        def invoke(self, role, prompt, thread, model, provider, **kwargs):
            if "Current task: search_plan" in prompt:
                return thread, json.dumps({"queries": [{"query": "mechanism counterexample", "purpose": "Compare conditions"}],
                    "relevance_terms": ["mechanism"], "scope": "A bounded question"})
            return super().invoke(role, prompt, thread, model, provider, **kwargs)
    monkeypatch.setattr(theory_master.codex_backend, "CodexBackend", StubCodexBackend)
    result = run_literature(tmp_path / "job", request(), retriever=Retriever(), model="fixture-model")
    assert result["status"] == "completed"
    assert created[0]["cwd"] == tmp_path / "job"
    assert set(created[0]["routes"]) == {"literature"}
    assert created[0]["routes"]["literature"].model == "fixture-model"


def test_persisted_raw_response_recovers_without_a_duplicate_model_call(tmp_path, monkeypatch):
    import theory_master.literature_service as service
    backend, retrieve = LiteratureBackend(), Retriever()
    real_close = service.close_agent_output
    crashed = False
    def crash_after_response(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise KeyboardInterrupt("fixture process interruption after persisted response")
        return real_close(*args, **kwargs)
    monkeypatch.setattr(service, "close_agent_output", crash_after_response)
    with pytest.raises(KeyboardInterrupt):
        run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert len(backend.calls) == 1
    result = run_literature(tmp_path / "job", request(), backend=backend, retriever=retrieve)
    assert result["status"] == "completed"
    assert len(backend.calls) == 4


def test_same_title_distinct_doi_never_shares_a_materialized_source(tmp_path):
    from theory_master.literature_service import _catalog, _paper_ref
    root = tmp_path / "job"
    root.mkdir()
    first = {"title": "Shared title", "doi": "10.1234/first", "abstract": "first abstract"}
    second = {"title": "Shared title", "doi": "10.1234/different", "abstract": "second abstract"}
    papers = root / "papers.json"
    papers.write_text(json.dumps([first, second]))
    text = root / "first.md"
    text.write_text("Only the first source's actual body text.")
    manifest = root / "pdf_manifest.csv"
    with manifest.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["paper", "doi", "status", "text_path"])
        writer.writeheader()
        writer.writerow({"paper": "Shared title", "doi": first["doi"], "status": "read", "text_path": str(text)})
    corpus, _, sources = _catalog(root, [{"papers_json": str(papers), "output_dir": str(root),
                                         "reading": {"pdf_manifest": str(manifest)}}])
    assert len(corpus) == 2
    by_doi = {source["doi"]: source for source in sources}
    assert by_doi[first["doi"]]["reading_level"] == "full_text"
    assert by_doi[second["doi"]]["reading_level"] == "abstract"
    assert _paper_ref("Shared title", corpus) is None
    assert _paper_ref("https://doi.org/10.1234/first", corpus)["doi"] == first["doi"]


def test_legacy_title_only_reading_is_rejected_when_title_is_ambiguous(tmp_path):
    from theory_master.literature_service import _catalog
    root = tmp_path / "job"
    root.mkdir()
    papers = root / "papers.json"
    papers.write_text(json.dumps([{"title": "Shared title", "doi": "10.1234/first"},
                                 {"title": "Shared title", "doi": "10.1234/different"}]))
    (root / "paper-01.md").write_text("An ambiguous paper body.")
    manifest = root / "pdf_manifest.csv"
    manifest.write_text("paper,status\nShared title,read\n")
    _, _, sources = _catalog(root, [{"papers_json": str(papers), "output_dir": str(root),
                                    "reading": {"pdf_manifest": str(manifest)}}])
    assert len(sources) == 2
    assert all(source["reading_level"] == "metadata" for source in sources)


def test_pdf_capability_projection_and_page_scope_do_not_expose_api_key(tmp_path, monkeypatch):
    import theory_master.document_conversion as conversion
    from theory_master.literature_service import _catalog
    monkeypatch.setattr(conversion, "load_settings", lambda: {"enabled": True, "provider": "mineru", "available": True,
        "max_pages": 20, "extract_images": True, "api_key": "NEVER-IN-AGENT-CONTEXT", "online_base_url": "sensitive"})
    root = tmp_path / "job"
    root.mkdir()
    retrieve = Retriever()
    transport = retrieve({"source_gap_id": "fixture", "task_type": "targeted_source", "source_lock": PAPERS[0]}, root)
    manifest = Path(transport["reading"]["pdf_manifest"])
    text = manifest.parent / "paper-01.md"
    receipt = manifest.parent / "conversion.json"
    receipt.write_text("{}")
    with manifest.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["paper", "doi", "status", "text_path", "max_pages", "conversion_manifest"])
        writer.writeheader()
        writer.writerow({"paper": PAPERS[0]["title"], "doi": PAPERS[0]["doi"], "status": "read", "text_path": str(text),
                         "max_pages": 20, "conversion_manifest": str(receipt)})
    _, corpus, sources = _catalog(root, [transport])
    index = Path(corpus["evidence_index_md"]).read_text()
    assert "only the first 20 pages" in index
    assert str(receipt) in index
    assert "NEVER-IN-AGENT-CONTEXT" not in index
    assert "online_base_url" not in index
    assert sources[0]["materialization"]["record"]["max_pages"] == "20"


def test_existing_scientific_parent_records_are_unchanged(tmp_path):
    parent = tmp_path / "task"
    parent.mkdir()
    files = {"research_plan.md": b"# Current research plan", "science_graph.sqlite3": b"existing graph bytes",
             "scientific_model.json": b'{"version":4}', "cycle_state.json": b'{"cycle":8}'}
    for name, data in files.items():
        (parent / name).write_bytes(data)
    result = run_literature(parent / "chat-literature" / "job",
                            request(), backend=LiteratureBackend(), retriever=Retriever())
    assert result["status"] == "completed"
    assert {name: (parent / name).read_bytes() for name in files} == files


def test_planned_workflow_persists_queries_and_reuses_plan_on_resume(tmp_path):
    backend, retrieve, plans = LiteratureBackend(), Retriever(), []
    def planner(root, request, route, **kwargs):
        plans.append(request)
        return {"queries": [{"query": "mechanism counterexample", "purpose": "Compare assumptions"}],
                "relevance_terms": ["mechanism"], "scope": "Bounded review"}
    root = tmp_path / "job"
    result = run_literature(root, request(), backend=backend, retriever=retrieve, planner=planner)
    assert result["status"] == "completed"
    assert retrieve.calls[0]["search_queries"][0]["query"] == "mechanism counterexample"
    assert len(plans) == 1
    repeated = run_literature(root, request(), backend=backend, retriever=retrieve, planner=planner)
    assert repeated["status"] == "completed" and len(plans) == 1
    state = json.loads((root / "manifest.json").read_text())
    assert state["workflow_version"] == 2 and state["search_plan"]


def test_imported_metadata_cannot_inflate_report_with_a_webpage_title(tmp_path):
    from theory_master.literature_service import _catalog
    home = tmp_path / "retrieval"
    home.mkdir()
    raw = [{"title": "politics " * 20000, "doi": "10.1234/bad"}, PAPERS[0]]
    (home / "papers.json").write_text(json.dumps(raw))
    papers, corpus, sources = _catalog(tmp_path, [{"papers_json": str(home / "papers.json")}])
    assert len(papers) == len(sources) == 1
    assert papers[0]["doi"] == PAPERS[0]["doi"]
    rejected = json.loads((tmp_path / "corpus/rejected_metadata.json").read_text())
    assert len(rejected) == 1 and rejected[0]["reason"]
    assert len(Path(corpus["evidence_index_md"]).read_text()) < 5000
    assert json.loads((home / "papers.json").read_text()) == raw


def test_saved_selection_keeps_stable_identity_after_catalog_filtering():
    from theory_master.literature_service import _select
    papers = [{"title": "Correct source", "doi": "10.1234/correct"},
              {"title": "Different source", "doi": "10.1234/different"}]
    saved = {"landscape": {"representative_papers": [
        {"source_ref": "paper:1", "doi_or_url": "10.1234/correct"}]}}
    assert _select(saved, papers, set()) == [papers[0]]
    saved["landscape"]["representative_papers"][0]["doi_or_url"] = "10.1234/missing"
    assert _select(saved, papers, set()) == []


def test_new_planned_queries_are_not_reused_for_the_same_gap_text(tmp_path):
    retrieve = Retriever()
    rounds = []
    def planner(root, request, route, **kwargs):
        rounds.append(request)
        return {"queries": [{"query": f"distinct query {len(rounds)}", "purpose": "Complementary route"}],
                "relevance_terms": ["mechanism"], "scope": "Bounded review"}
    result = run_literature(tmp_path / "job", request(max_rounds=3),
        backend=LiteratureBackend(remaining=True), retriever=retrieve, planner=planner)
    assert result["status"] == "paused"
    landscapes = [row for row in retrieve.calls if row["task_type"] == "landscape"]
    assert len(landscapes) == 3
    assert landscapes[1]["search_target"] == landscapes[2]["search_target"]
    assert landscapes[1]["search_queries"] != landscapes[2]["search_queries"]


def test_doi_enrichment_preserves_review_and_prevents_repeat_acquisition():
    from theory_master.literature_service import _reconcile_source_bookkeeping, _select
    preprint = {"title": "A specific polymer network study", "arxiv_id": "cond-mat/0007270", "year": 2000}
    published = {**preprint, "doi": "10.1063/1.1290134"}
    review = {"evidence_records": [{"doi_or_url": published["doi"], "scientific_insight": "Original review"}]}
    state = {"selected_sources": [{"paper": preprint, "source_id": "arxiv:cond-mat/0007270"}],
             "source_attempts": ["arxiv:cond-mat/0007270"], "source_reviews": {"arxiv:cond-mat/0007270": review}}
    _reconcile_source_bookkeeping(state, [published])
    key = "doi:10.1063/1.1290134"
    assert state["source_attempts"] == [key]
    assert state["source_reviews"] == {key: review}
    assert state["source_aliases"] == {"arxiv:cond-mat/0007270": key}
    assert _select({"landscape": {"representative_papers": [{"doi_or_url": published["doi"]}]}},
                   [published], set(state["source_attempts"])) == []
    _reconcile_source_bookkeeping(state, [published])
    assert state["source_reviews"] == {key: review}


def test_report_expands_corpus_ids_into_readable_linked_papers():
    from theory_master.literature_service import _expand_report_references
    text = "Compare paper:0 and paper:1.\n1. paper:0 — Read this mechanism first."
    rendered = _expand_report_references(text, [PAPERS[0], {"title": "Unsafe source", "url": "javascript:alert(1)"}])
    assert "[Foundational mechanism](https://doi.org/10.1234/foundation)" in rendered
    assert "Unsafe source" in rendered and "javascript:" not in rendered
    assert "paper:" not in rendered and "Read this mechanism first" in rendered


def test_exact_abstract_quote_can_bind_to_retained_preprint_version():
    from theory_master.literature_service import _access
    paper = {**PAPERS[0], "abstract": "The journal version uses different wording.",
             "abstract_variants": [{"text": "The original preprint contains this exact statement.",
                 "provider": "arxiv", "url": "https://arxiv.org/abs/2000.12345"}]}
    raw = {"evidence_records": [{"source_ref": paper["doi"], "source_access": "abstract",
        "exact_quote": "original preprint contains this exact statement.", "limitations": "Abstract only."}]}
    closed = {"evidence_records": [{"doi_or_url": paper["doi"], "limitations": "Old SDK failure"}]}
    sources = [{"source_id": "doi:" + paper["doi"], "reading_level": "abstract"}]
    result = _access(closed, raw, [paper], sources)["evidence_records"][0]
    assert result["source_access"] == "abstract" and result["access_assessment"]["quote_located"]
    assert result["access_assessment"]["abstract_provenance"]["provider"] == "arxiv"
    assert result["limitations"] == "Abstract only."
    raw["evidence_records"][0]["exact_quote"] = "a paraphrase not actually present"
    assert _access(closed, raw, [paper], sources)["evidence_records"][0]["source_access"] == "unavailable"
