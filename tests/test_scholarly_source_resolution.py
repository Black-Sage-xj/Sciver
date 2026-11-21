"""Source identity and optional conversion regressions; no network or models."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from theory_master.skills import skills_root
from theory_master.scholarly_selection import _query_variants, _reconcile_locked_source, _source_lock_record


def module(name):
    scripts = skills_root() / "tm-scholar" / "scripts"
    spec = importlib.util.spec_from_file_location("source_resolution_" + name, scripts / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(result)
    finally:
        sys.path.pop(0)
    return result


@pytest.mark.parametrize("source,raw", [
    ("openalex", {"display_name": "Brownian motion", "doi": "https://doi.org/10.1234/abc"}),
    ("crossref", {"message": {"title": ["Brownian motion"], "DOI": "10.1234/abc"}}),
    ("semanticscholar", {"title": "Brownian motion", "externalIds": {"DOI": "10.1234/abc"}}),
])
def test_doi_uses_exact_provider_endpoint(monkeypatch, source, raw):
    mod = module("lit_retrieve")
    urls = []
    monkeypatch.setattr(mod, "get_json", lambda url: urls.append(url) or raw)
    rows = mod.search(source, "https://doi.org/10.1234/ABC", 20)
    assert rows[0]["doi"] == "10.1234/abc"
    assert len(urls) == 1 and "search" not in urls[0] and "query.bibliographic" not in urls[0]
    monkeypatch.setattr(mod, "get_json", lambda url: {})
    with pytest.raises(ValueError, match="requested DOI"):
        mod.search(source, "10.1234/abc", 20)


@pytest.mark.parametrize("hint", [
    "DOI 10.1234/ABC",
    "See https://doi.org/10.1234/ABC.",
    "Primary paper (10.1234/ABC); verify its model definition.",
])
def test_targeted_source_keeps_exact_doi_hint_with_domain_context(hint):
    queries = _query_variants("targeted_source", "lead-source-1",
        "Verify the source Hamiltonian and localization observable", hint,
        "Three-dimensional chiral topological Anderson transition")
    assert queries[0] == "10.1234/abc"
    assert len(queries) == len(set(queries)) <= 4
    assert any("Anderson" in query for query in queries[1:])


@pytest.mark.parametrize("hint,identifier", [
    ("arXiv:2211.09999v2", "2211.09999"),
    ("https://arxiv.org/html/2211.09999v2", "2211.09999"),
    ("original arxiv cond-mat/0601234v2", "cond-mat/0601234"),
])
def test_targeted_arxiv_hint_survives_prose_compaction(hint, identifier):
    assert _query_variants("targeted_source", "gap", "Verify model", hint,
        "Anderson localization")[0] == identifier


def test_explicit_source_hint_precedes_planned_queries_without_affecting_landscape():
    planned = ["Anderson model definitions", "Anderson boundary conditions"]
    assert _query_variants("targeted_source", "gap", "Verify model", "DOI 10.1234/ABC",
        "Anderson localization", planned) == ["10.1234/abc", *planned]
    assert _query_variants("landscape", "gap", "Survey models", "DOI 10.1234/ABC",
        "Anderson localization", planned) == planned


def test_targeted_title_hint_is_not_lost_when_domain_context_is_present():
    queries = _query_variants("targeted_source", "gap", "Verify Hamiltonian",
        "Anisotropic Topological Anderson Transitions in Chiral Symmetry Classes",
        "Anderson localization")
    assert any("Anisotropic Topological" in query for query in queries)


def test_lead_shaped_source_request_delivers_exact_doi_to_host_transport(tmp_path):
    from types import SimpleNamespace
    from theory_master.scholarly_transport import retrieve_scholarly
    queries = []

    def runner(command, **kwargs):
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if Path(command[1]).name == "lit_retrieve.py":
            query = command[command.index("--query") + 1]
            queries.append(query)
            rows = [paper()] if query == "10.1234/abc" else []
            (output / "papers.json").write_text(json.dumps(rows))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = retrieve_scholarly({
        "source_gap_id": "lead-source-1", "task_type": "targeted_source",
        "search_target": "Verify the source model and its observable",
        "new_search_lead": "DOI 10.1234/ABC", "domain_context": "Anderson localization",
    }, tmp_path, runner=runner)
    assert queries[0] == "10.1234/abc"
    assert result["request_identity"]["request"]["search_queries"] == queries
    assert json.loads(Path(result["papers_json"]).read_text())[0]["doi"] == "10.1234/abc"


def paper(doi="10.1234/abc", **fields):
    return {"title": "Brownian motion", "doi": doi, "year": 2020,
            "authors": ["Alice Scientist"], "source": "openalex", "is_open_access": True,
            "open_access_url": "https://example.org/original.pdf", **fields}


def test_title_lock_resolves_unique_provider_and_retains_fulltext():
    lock = {"title": "Brownian motion"}
    rows = _reconcile_locked_source([_source_lock_record(lock), paper()], lock)
    assert len(rows) == 1
    assert rows[0]["source_locked"] and rows[0]["doi"] == "10.1234/abc"
    assert rows[0]["is_open_access"] and rows[0]["open_access_url"]
    assert rows[0]["identity_resolution"] == "unique_exact_title_candidate"


def test_same_title_distinct_dois_remain_ambiguous():
    lock = {"title": "Brownian motion"}
    rows = _reconcile_locked_source([_source_lock_record(lock), paper(), paper("10.1234/other")], lock)
    assert rows[0]["identity_resolution"] == "ambiguous"
    assert sum(bool(row.get("source_locked")) for row in rows) == 1


@pytest.mark.parametrize("extra", [{"year": 1970}, {"authors": ["Bob Different"]}])
def test_title_lock_rejects_conflicting_known_metadata(extra):
    lock = {"title": "Brownian motion", **extra}
    rows = _reconcile_locked_source([_source_lock_record(lock), paper()], lock)
    assert rows[0]["identity_resolution"] == "unresolved"


def test_explicit_closed_access_is_preserved():
    lock = {"doi": "10.1234/abc", "is_open_access": False}
    rows = _reconcile_locked_source([_source_lock_record(lock), paper()], lock)
    assert rows[0]["identity_resolution"] == "exact_doi"
    assert rows[0]["is_open_access"] is False


@pytest.mark.parametrize("lock", [{"title": "Brownian motion"}, {"title": "Brownian motion", "doi": "10.1234/abc"}])
def test_sdk_reads_resolved_record_and_obeys_configured_page_limit(tmp_path, lock):
    from types import SimpleNamespace
    from theory_master.scholarly_transport import retrieve_scholarly
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if "lit_retrieve.py" == Path(command[1]).name:
            assert command[command.index("--query") + 1] == (lock.get("doi") or lock["title"])
            (output / "papers.json").write_text(json.dumps([paper(score=0)]))
        elif "read_papers.py" == Path(command[1]).name:
            assert command[command.index("--paper-ids") + 1] == "10.1234/abc"
            records = json.loads(Path(command[command.index("--papers-json") + 1]).read_text())
            assert len(records) == 1 and records[0]["open_access_url"].endswith("original.pdf")
            assert records[0]["identity_resolution"] in {"unique_exact_title_candidate", "exact_doi"}
            assert "--max-pages" not in command
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    retrieve_scholarly({"task_type": "targeted_source", "source_lock": lock, "search_target": "check claim"},
                       tmp_path, runner=runner)
    assert any(Path(command[1]).name == "read_papers.py" for command in calls)


def run_reader(monkeypatch, tmp_path, rows, requested, read_source):
    mod = module("read_papers")
    path = tmp_path / "papers.json"
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(mod, "read_source", read_source)
    monkeypatch.setattr(sys, "argv", ["read_papers", "--papers-json", str(path), "--output-dir",
        str(tmp_path / "reading"), "--reading-selection", "manual", "--paper-ids", requested])
    assert mod.main() == 0
    with (tmp_path / "reading/pdf_manifest.csv").open() as stream:
        return list(csv.DictReader(stream))


def test_reader_matches_title_even_if_provider_has_doi(monkeypatch, tmp_path):
    rows = run_reader(monkeypatch, tmp_path, [paper()], "Brownian motion",
                      lambda *args, **kwargs: ("Primary source text", "html/text"))
    assert rows[0]["status"] == "read" and rows[0]["doi"] == "10.1234/abc"


def test_reader_does_not_choose_arbitrary_ambiguous_title(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("An ambiguous title must not select the first paper")
    assert run_reader(monkeypatch, tmp_path, [paper(), paper("10.1234/other")], "Brownian motion", fail) == []


def test_disabled_pdf_can_fall_back_to_available_html(monkeypatch, tmp_path):
    from theory_master.document_conversion import ConversionDisabled
    def read(url, *args, **kwargs):
        if url.endswith(".pdf"):
            raise ConversionDisabled("PDF conversion is disabled")
        return "HTML primary source", "html/text"
    rows = run_reader(monkeypatch, tmp_path, [paper(open_access_urls=["https://example.org/article"])],
                      "10.1234/abc", read)
    assert rows[0]["status"] == "read" and rows[0]["url"].endswith("/article")


def test_disabled_pdf_retains_failure_with_stable_identifier(monkeypatch, tmp_path):
    from theory_master.document_conversion import ConversionDisabled
    def read(*args, **kwargs):
        raise ConversionDisabled("PDF conversion is disabled")
    rows = run_reader(monkeypatch, tmp_path, [paper(title="")], "10.1234/abc", read)
    assert rows[0]["status"] == "conversion_disabled" and rows[0]["doi"] == "10.1234/abc"
