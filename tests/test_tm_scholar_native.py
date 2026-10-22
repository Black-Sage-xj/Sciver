"""Deterministic tests of the original TM Scholar CLI and SDK contract."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from theory_master.skills import skills_root

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = skills_root() / "tm-scholar" / "scripts"


def _module(name: str):
    path = SCRIPTS / (name + ".py")
    spec = importlib.util.spec_from_file_location("tm_native_" + name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_unique_packaged_scholar_skill_and_required_files():
    from theory_master.skills import role_skills

    names = [skill.directory for skill in role_skills("literature")]
    assert "tm-scholar" in names
    assert "scholarly-deep-research" not in names
    assert not (ROOT / "skills").exists()
    assert not (skills_root() / "scholarly-deep-research").exists()
    scholar = skills_root() / "tm-scholar"
    for relative in ("SKILL.md", "README.md", "references/standalone.md",
                     "scripts/lit_retrieve.py", "scripts/pdf_to_markdown.py",
                     "scripts/read_papers.py", "scripts/scholar_identity.py",
                     "scripts/trace_paper.py"):
        assert (scholar / relative).is_file(), relative


def test_runtime_scripts_resolve_packaged_resources():
    from theory_master.scholarly_transport import RETRIEVE_SCRIPT, READ_SCRIPT, TRACE_SCRIPT
    from theory_master.skills import skills_root
    for script in (RETRIEVE_SCRIPT, READ_SCRIPT, TRACE_SCRIPT):
        assert script.is_file()
        assert script.parent == skills_root() / "tm-scholar" / "scripts"


def test_retrieval_normalizes_ranks_and_deduplicates():

    mod = _module("lit_retrieve")
    a = mod.normalize({"title": "Fracture in polymer networks", "doi": "10.1000/ABC",
                       "publication_year": 2025, "cited_by_count": 10, "authorships": []}, "openalex")
    b = mod.normalize({"title": ["Fracture in polymer networks"], "DOI": "10.1000/abc",
                       "published": {"date-parts": [[2025]]}}, "crossref")
    papers = mod.rank_and_dedupe([a, b], "polymer network fracture", 2020, 2026)
    assert len(papers) == 1
    assert papers[0]["sources"] == ["crossref", "openalex"]
    assert papers[0]["doi"] == "10.1000/abc"


def test_retrieval_cli_writes_contract_and_reports_partial_failure(monkeypatch, tmp_path):
    mod = _module("lit_retrieve")
    def fake_search(source, query, limit):
        if source == "crossref":
            raise RuntimeError("temporary 429")
        return [mod.make("Polymer geometry", ["A. Scientist"], 2024, "10.1234/xyz",
                         "https://doi.org/10.1234/xyz", "Polymer evidence", 2, "", source)]
    monkeypatch.setattr(mod, "search", fake_search)
    monkeypatch.setattr(sys, "argv", ["lit_retrieve.py", "--query", "polymer", "--sources",
                                      "openalex,crossref", "--output-dir", str(tmp_path)])
    assert mod.main() == 0
    assert len(json.loads((tmp_path / "papers.json").read_text())) == 1
    for file in ("papers.csv", "papers.bib", "report.md", "topic_brief.md", "search_log.md", "errors.log"):
        assert (tmp_path / file).is_file()
    assert "crossref" in (tmp_path / "errors.log").read_text()


def test_retrieval_cli_all_sources_blocked(monkeypatch, tmp_path):
    mod = _module("lit_retrieve")
    def fail(source, query, limit):
        raise RuntimeError("offline")
    monkeypatch.setattr(mod, "search", fail)
    monkeypatch.setattr(sys, "argv", ["lit_retrieve.py", "--query", "nothing",
                                      "--sources", "openalex", "--output-dir", str(tmp_path)])
    assert mod.main() == 2
    assert json.loads((tmp_path / "papers.json").read_text()) == []
    assert (tmp_path / "errors.log").is_file()


def test_reader_distinguishes_metadata_from_verified_text(monkeypatch, tmp_path):
    mod = _module("read_papers")
    papers = tmp_path / "papers.json"
    papers.write_text(json.dumps([
        {"title": "Metadata only", "doi": "10.1/one", "is_open_access": False},
        {"title": "Full source", "doi": "10.1/two", "is_open_access": True,
         "open_access_url": "https://example.org/paper"}
    ]))
    monkeypatch.setattr(mod, "read_source", lambda url, out, **kwargs: ("Real primary text", "html/text"))
    out = tmp_path / "reading"
    monkeypatch.setattr(sys, "argv", ["read_papers.py", "--papers-json", str(papers),
                                      "--output-dir", str(out), "--reading-limit", "2"])
    assert mod.main() == 0
    import csv
    with (out / "pdf_manifest.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [row["status"] for row in rows] == ["unverified", "read"]
    assert "Real primary text" in (out / "paper-02.md").read_text()


def test_pubmed_metadata_via_faked_xml(monkeypatch):
    mod = _module("lit_retrieve")
    xml_search = b"<eSearchResult><IdList><Id>123</Id></IdList></eSearchResult>"
    xml_papers = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Polymer fracture</ArticleTitle><Journal><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal><Abstract><AbstractText>Evidence</AbstractText></Abstract><AuthorList><Author><ForeName>A</ForeName><LastName>B</LastName></Author></AuthorList></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""
    monkeypatch.setattr(mod, "fetch", lambda url: xml_search if "esearch.fcgi" in url else xml_papers)
    rows = mod.search("pubmed", "polymer", 1)
    assert len(rows) == 1
    assert rows[0]["title"] == "Polymer fracture"
    assert rows[0]["doi"] == "10.1/test"
    assert rows[0]["source"] == "pubmed"


def test_marker_arguments_are_bounded_and_local(monkeypatch, tmp_path):
    mod = _module("pdf_to_markdown")
    marker = tmp_path / "marker_single"
    marker.write_text("stub")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    def fake_run(cmd, **kwargs):
        assert "--page_range" in cmd
        assert cmd[cmd.index("--page_range") + 1] == "0-2"
        assert "markdown" in cmd
        dest = Path(cmd[cmd.index("--output_dir") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "paper.md").write_text("converted full text")
        return type("P", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod.convert_pdf_to_markdown(pdf, tmp_path / "converted", marker_bin=str(marker), max_pages=3)
    assert result.read_text() == "converted full text"


def test_reader_rejects_private_and_non_https_urls():
    mod = _module("read_papers")
    for url in ("file:///etc/passwd", "http://example.org/a", "https://127.0.0.1/a",
                "https://localhost/a", "https://192.168.1.2/a"):
        assert not mod.safe_source(url)
    assert mod.safe_source("https://arxiv.org/pdf/2401.00001")


def test_citation_trace_creates_metadata_labeled_artifacts(monkeypatch, tmp_path):
    mod = _module("trace_paper")
    def fake_api(url):
        if "cites%3AW123" in url or "cites%3AW123" in url.replace("%3a", "%3A"):
            return {"results": [{"id": "https://openalex.org/W999", "display_name": "Citing", "publication_year": 2026}]}
        if url.endswith("/W222"):
            return {"id": url, "display_name": "Referenced", "publication_year": 2020}
        return {"id": "https://openalex.org/W123", "display_name": "Paper",
                "doi": "https://doi.org/10.1000/abc",
                "referenced_works": ["https://api.openalex.org/works/W222"]}
    monkeypatch.setattr(mod, "api", fake_api)
    monkeypatch.setattr(sys, "argv", ["trace_paper.py", "--paper", "10.1000/abc",
                                      "--output-dir", str(tmp_path), "--openalex"])
    assert mod.main() == 0
    report = json.loads((tmp_path / "paper_trace.json").read_text())
    assert report["status"] == "metadata_only"
    assert len(report["references"]) == 1
    assert (tmp_path / "paper_trace_report.md").is_file()


def test_identity_reconciles_doi_arxiv_and_metadata_without_overmerging():
    from scholar_identity import doi_key, arxiv_key
    mod = _module("lit_retrieve")
    a = mod.make("Spatially weighted fracture", ["Jane Doe"], 2025, "https://doi.org/10.1234/ABC",
                 "https://openalex.org/W1", "spatially weighted fracture", 17, "", "openalex",
                 arxiv_id="2401.12345v2")
    b = mod.make("Spatially Weighted Fracture!", ["J. Doe"], 2025, "", "https://arxiv.org/abs/2401.12345",
                 "", 0, "https://arxiv.org/pdf/2401.12345", "arxiv", arxiv_id="2401.12345")
    c = mod.make("Spatially weighted fracture", ["John Doe"], 2025, "10.9999/different",
                 "https://doi.org/10.9999/different", "", 0, "", "crossref")
    merged = mod.rank_and_dedupe([a, b, c], "spatially weighted fracture", 2020, 2026)
    assert len(merged) == 2
    original = next(row for row in merged if row["doi"] == "10.1234/abc")
    assert original["sources"] == ["arxiv", "openalex"]
    assert original["arxiv_id"] == "2401.12345"
    assert original["is_open_access"]
    assert doi_key("doi:10.1234/ABC") == "10.1234/abc"
    assert arxiv_key("https://arxiv.org/pdf/2401.12345v3.pdf") == "2401.12345"


def test_same_title_different_authors_and_years_are_distinct():
    mod = _module("lit_retrieve")
    records = [
        mod.make("An analysis of networks", ["Jane Doe"], 2020, "", "", "", 1, "", "crossref"),
        mod.make("An analysis of networks", ["John Doe"], 2020, "", "", "", 1, "", "openalex"),
        mod.make("An analysis of networks", ["Jane Doe"], 2025, "", "", "", 1, "", "arxiv"),
    ]
    assert len(mod.rank_and_dedupe(records, "analysis networks", None, None)) == 3


def test_title_rank_prefers_relevance_over_high_citation_count():
    mod = _module("lit_retrieve")
    relevant = mod.make("Polymer network fracture", ["A. B"], 2024, "", "", "", 1, "", "arxiv")
    popular = mod.make("Economic growth and networks", ["C. D"], 2024, "", "", "", 9000, "", "crossref")
    assert mod.rank_and_dedupe([popular, relevant], "polymer network fracture", None, None)[0]["title"] == relevant["title"]


def test_retrieval_source_validation_and_stale_error_cleanup(monkeypatch, tmp_path):
    mod = _module("lit_retrieve")
    monkeypatch.setattr(mod, "search", lambda source, query, limit: [
        mod.make("Polymer fracture", ["Jane Doe"], 2025, "", "", "", 0, "", source)])
    out = tmp_path / "search"
    out.mkdir()
    (out / "errors.log").write_text("stale failure")
    monkeypatch.setattr(sys, "argv", ["lit_retrieve.py", "--query", "polymer",
                                      "--sources", "openalex", "--output-dir", str(out)])
    assert mod.main() == 0
    assert not (out / "errors.log").exists()


def test_retrieval_oversize_http_response_fails_explicitly(monkeypatch):
    mod = _module("lit_retrieve")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, count):
            return b"x" * count
    monkeypatch.setattr(mod, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(mod, "MAX_RESPONSE", 10)
    import pytest
    with pytest.raises(ValueError, match="size limit"):
        mod.fetch("https://api.openalex.org/works")


def test_trace_rejects_mismatched_doi_and_ambiguous_title(monkeypatch, tmp_path):
    mod = _module("trace_paper")
    monkeypatch.setattr(mod, "api", lambda url: {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/wrong",
        "display_name": "Paper", "publication_year": 2025})
    monkeypatch.setattr(sys, "argv", ["trace_paper.py", "--paper", "10.1000/expected",
                                      "--output-dir", str(tmp_path)])
    assert mod.main() == 2
    result = json.loads((tmp_path / "paper_trace.json").read_text())
    assert "does not match" in result["error"]
    local = tmp_path / "local.json"
    local.write_text(json.dumps([
        {"title": "Same Title", "doi": "10.1000/one", "authors": ["Jane Doe"]},
        {"title": "Same Title", "doi": "10.1000/two", "authors": ["John Doe"]},
    ]))
    import pytest
    with pytest.raises(ValueError, match="ambiguous"):
        mod.seed_from_local("Same Title", local)


def test_trace_partial_reference_failure_and_citation_dedup(monkeypatch, tmp_path):
    mod = _module("trace_paper")
    def fake_api(url):
        if url.endswith("/W2"):
            raise RuntimeError("rate limited")
        if "cites%3AW1" in url:
            return {"results": [
                {"id": "https://openalex.org/W3", "display_name": "Citing", "publication_year": 2026,
                 "referenced_works": ["https://openalex.org/W1"]},
                {"id": "https://openalex.org/W3", "display_name": "Citing duplicate"},
                {"id": "https://openalex.org/W4", "display_name": "Wrong edge",
                 "referenced_works": ["https://openalex.org/W9"]},
            ]}
        return {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/one",
                "display_name": "Seed", "referenced_works": ["https://openalex.org/W2"]}
    monkeypatch.setattr(mod, "api", fake_api)
    monkeypatch.setattr(sys, "argv", ["trace_paper.py", "--paper", "10.1000/one",
                                      "--output-dir", str(tmp_path)])
    assert mod.main() == 0
    payload = json.loads((tmp_path / "paper_trace.json").read_text())
    assert payload["status"] == "partial_metadata"
    assert payload["references"][0]["edge_status"] == "unresolved"
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["work_id"] == "W3"
    assert len(payload["errors"]) == 2


def test_reading_retries_transient_http_errors(monkeypatch):
    mod = _module("read_papers")
    from urllib.error import HTTPError
    attempts = []
    class Response:
        headers = {"Content-Type": "text/html"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def geturl(self):
            return "https://example.org/paper"
        def read(self, count):
            return b"<html><p>Scientific full text.</p></html>"
    def opener(url):
        attempts.append(url)
        if len(attempts) == 1:
            raise HTTPError(url, 429, "rate limited", {}, None)
        return Response()
    monkeypatch.setattr(mod, "_open_public", opener)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    data, mime = mod._download("https://example.org/paper", 1000)
    assert b"Scientific" in data and mime == "text/html"
    assert len(attempts) == 2


def test_reading_candidate_fallback_keeps_failed_pdf_unverified(monkeypatch, tmp_path):
    mod = _module("read_papers")
    papers = tmp_path / "papers.json"
    papers.write_text(json.dumps([{
        "title": "Full paper", "is_open_access": True,
        "open_access_url": "https://example.org/broken.pdf",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }]))
    attempts = []
    def fake_read(url, out, **kwargs):
        attempts.append(url)
        if "broken" in url:
            raise RuntimeError("Marker unavailable")
        return "Materialized primary text", "pdf/marker"
    monkeypatch.setattr(mod, "read_source", fake_read)
    monkeypatch.setattr(sys, "argv", ["read_papers.py", "--papers-json", str(papers),
                                      "--output-dir", str(tmp_path / "reading")])
    assert mod.main() == 0
    import csv
    with (tmp_path / "reading" / "pdf_manifest.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(attempts) == 2 and rows[0]["status"] == "read"
    assert rows[0]["url"] == "https://arxiv.org/pdf/2401.12345"


def test_pdf_converter_rejects_stale_markdown_after_failed_attempt(monkeypatch, tmp_path):
    mod = _module("pdf_to_markdown")
    marker = tmp_path / "marker_single"
    marker.write_text("stub")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "converted"
    dest.mkdir()
    (dest / "paper.md").write_text("stale previous output")
    monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kwargs:
                        type("P", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    import pytest
    with pytest.raises(RuntimeError, match="nonempty Markdown"):
        mod.convert_pdf_to_markdown(pdf, dest, marker_bin=str(marker))
    assert (dest / "paper.md").read_text() == "stale previous output"


def test_reader_rejects_private_ipv6_and_redirect(monkeypatch):
    mod = _module("read_papers")
    for url in ("https://[::1]/", "https://172.20.0.1/", "https://169.254.169.254/",
                "https://user:password@example.org/", "https://metadata.google.internal/"):
        assert not mod.safe_source(url)
    assert mod.safe_source("https://172.32.1.1/")  # outside the RFC1918 172.16/12 range
    import pytest
    with pytest.raises(ValueError, match="redirect"):
        mod._SafeRedirect().redirect_request(None, None, 302, "redirect", {}, "http://127.0.0.1/")


def test_metadata_json_cannot_masquerade_as_primary_article(monkeypatch, tmp_path):
    mod = _module("read_papers")
    monkeypatch.setattr(mod, "_download", lambda url, max_bytes:
                        (b'{"title":"Metadata, not article"}', "application/json"))
    import pytest
    with pytest.raises(ValueError, match="article text"):
        mod.read_source("https://example.org/metadata", tmp_path)


def test_marker_timeout_preserves_unverified_status(monkeypatch, tmp_path):
    mod = _module("pdf_to_markdown")
    marker = tmp_path / "marker_single"
    marker.write_text("stub")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    import subprocess
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 240)
    monkeypatch.setattr(mod.subprocess, "run", timeout)
    import pytest
    with pytest.raises(RuntimeError, match="timed out"):
        mod.convert_pdf_to_markdown(pdf, tmp_path / "converted", marker_bin=str(marker))
    assert not (tmp_path / "converted" / "paper.md").exists()


def test_trace_title_disambiguates_by_year_and_authors(monkeypatch):
    mod = _module("trace_paper")
    results = [
        {"id": "https://openalex.org/W1", "display_name": "Same title", "publication_year": 2020,
         "authorships": [{"author": {"display_name": "Jane Doe"}}]},
        {"id": "https://openalex.org/W2", "display_name": "Same title", "publication_year": 2025,
         "authorships": [{"author": {"display_name": "John Doe"}}]},
    ]
    monkeypatch.setattr(mod, "api", lambda url: {"results": results})
    local = {"title": "Same title", "year": 2020, "authors": ["Jane Doe"]}
    assert mod.resolve_seed("Same title", local)["id"].endswith("W1")
    import pytest
    with pytest.raises(ValueError, match="ambiguous"):
        mod.resolve_seed("Same title", {})


def test_orcid_conflict_prevents_weak_author_identity_merge():
    mod = _module("lit_retrieve")
    a = mod.make("Same title", ["Jane Doe"], 2025, "", "", "", 0, "", "crossref",
                 author_ids=["https://orcid.org/0000-0001-0000-0001"])
    b = mod.make("Same title", ["Jane Doe"], 2025, "", "", "", 0, "", "openalex",
                 author_ids=["https://orcid.org/0000-0001-0000-0002"])
    assert len(mod.rank_and_dedupe([a, b], "Same title", None, None)) == 2


def test_title_only_without_year_or_authors_is_not_identity_evidence():
    mod = _module("lit_retrieve")
    unknown = mod.make("Same title", [], None, "", "", "", 0, "", "arxiv")
    known = mod.make("Same title", ["Jane Doe"], 2025, "", "", "", 0, "", "crossref")
    assert len(mod.rank_and_dedupe([unknown, known], "Same title", None, None)) == 2


def test_marker_adapter_invokes_real_local_subprocess(tmp_path):
    import os
    import pytest
    if os.name != "posix":
        pytest.skip("POSIX executable integration smoke")
    mod = _module("pdf_to_markdown")
    marker = tmp_path / "marker_single"
    marker.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output_dir') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'paper.md').write_text('Real subprocess materialization')\n"
    )
    marker.chmod(0o755)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test fixture\n")
    converted = mod.convert_pdf_to_markdown(pdf, tmp_path / "converted", marker_bin=str(marker), max_pages=1)
    assert converted.read_text() == "Real subprocess materialization"
    assert converted.parent == tmp_path / "converted"


def test_search_excludes_peer_reviews_and_nonpaper_records(monkeypatch):
    mod = _module("lit_retrieve")
    def fake_json(url):
        if "crossref.org" in url:
            return {"message": {"items": [
                {"title": ["Review for an article"], "type": "peer-review"},
                {"title": ["Polymer network fracture"], "type": "journal-article",
                 "DOI": "10.1000/article"},
            ]}}
        return {"results": [
            {"display_name": "Dataset entry", "type": "dataset"},
            {"display_name": "Research article", "type": "article", "doi": "10.1000/oa"},
        ]}
    monkeypatch.setattr(mod, "get_json", fake_json)
    crossref = mod.search("crossref", "polymer network fracture", 10)
    openalex = mod.search("openalex", "polymer network fracture", 10)
    assert [row["title"] for row in crossref] == ["Polymer network fracture"]
    assert [row["title"] for row in openalex] == ["Research article"]


def test_sdk_source_lock_and_provider_doi_share_canonical_key():
    from theory_master.scholarly_selection import _paper_key
    assert _paper_key({"doi": "https://doi.org/10.1000/ABC"}) == _paper_key(
        {"doi": "10.1000/abc"})
    assert _paper_key({"doi": "doi:10.1000/ABC"}) == "doi:10.1000/abc"


def test_domain_aware_ranking_promotes_polymer_fracture_over_generic_bc():
    from theory_master.scholarly_selection import _domain_alignment, _targeted_relevance
    from theory_master.scholarly_transport import retrieve_scholarly  # noqa: F401
    titles = [
        "Predicting failure locations in model end-linked polymer networks",
        "A GPU-based solution for fast calculation of the betweenness centrality in large weighted networks",
        "Biological networks edge-betweenness centrality modularity algorithm",
    ]
    context = "polymer network fracture, weighted edge betweenness centrality, molecular dynamics"
    query = "Spatially Weighted Betweenness Centrality for Predicting Fracture in End-Linked Polymer Networks"
    results = [dict(title=t, abstract="", authors=[], score=0) for t in titles]
    score = lambda row: (_targeted_relevance(row, query, name_source=query+" "+context)
                         + _domain_alignment(row, context))
    assert score(results[0]) > score(results[1])
    assert score(results[0]) > score(results[2])


def test_openalex_multiple_open_access_candidates_are_preserved():
    mod = _module("lit_retrieve")
    row = mod.normalize({
        "display_name": "Polymer fracture", "doi": "10.1000/oa",
        "best_oa_location": {"is_oa": True, "pdf_url": "https://archive.org/a.pdf"},
        "locations": [
            {"is_oa": False, "pdf_url": "https://publisher.org/paywall.pdf"},
            {"is_oa": True, "pdf_url": "https://repository.edu/a.pdf"},
        ],
    }, "openalex")
    assert row["open_access_urls"] == ["https://archive.org/a.pdf", "https://repository.edu/a.pdf"]
    assert row["is_open_access"]
    other = mod.normalize({"title": ["Polymer fracture"], "DOI": "10.1000/oa"},
                          "crossref")
    merged = mod.rank_and_dedupe([other, row], "polymer fracture", None, None)
    assert merged[0]["open_access_urls"] == row["open_access_urls"]


def test_pubmed_pmcid_is_safe_declared_open_access(monkeypatch):
    mod = _module("lit_retrieve")
    search_xml = b"<eSearchResult><IdList><Id>123</Id></IdList></eSearchResult>"
    detail = b'''<PubmedArticleSet><PubmedArticle>
    <MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Polymer gel</ArticleTitle></Article></MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC12345</ArticleId></ArticleIdList></PubmedData>
    </PubmedArticle></PubmedArticleSet>'''
    monkeypatch.setattr(mod, "fetch", lambda url: search_xml if "esearch" in url else detail)
    result = mod.search("pubmed", "polymer", 2)
    assert result[0]["is_open_access"]
    assert result[0]["open_access_url"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/"


def test_provider_rate_limit_retries_and_preserves_deferred_warning(monkeypatch):
    from urllib.error import HTTPError
    mod = _module("lit_retrieve")
    attempts, delays = [], []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"data": []}'
    def fake_fetch(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise HTTPError(url, 429, "limited", {"Retry-After": "1"}, None)
        return Response()
    monkeypatch.setattr(mod, "urlopen", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", delays.append)
    assert mod.fetch("https://api.semanticscholar.org") == b'{"data": []}'
    assert len(attempts) == 2 and delays == [1.0]
    monkeypatch.setattr(mod, "urlopen", lambda url, **kw: (_ for _ in ()).throw(
        HTTPError(str(url), 429, "limited", {"Retry-After": "90"}, None)))
    import pytest
    with pytest.raises(RuntimeError, match="deferred"):
        mod.fetch("https://api.semanticscholar.org")


def test_citation_coverage_explicit_zero_and_bounded_paging(monkeypatch):
    mod = _module("trace_paper")
    source = {"id": "https://openalex.org/W123", "display_name": "Seed",
              "cited_by_count": 13, "referenced_works": [f"https://openalex.org/W{i}" for i in (4, 5, 6)]}
    def fake_api(url):
        if "filter=" in url:
            if "page=1" in url:
                return {"meta": {"count": 13}, "results": [
                    {"id": "https://openalex.org/W901"},
                    {"id": "https://openalex.org/W901"}]}
            return {"meta": {"count": 13}, "results": [
                {"id": "https://openalex.org/W902"}]}
        return {"id": url, "display_name": "Referenced"}
    monkeypatch.setattr(mod, "api", fake_api)
    report = mod.trace(source, 2)
    assert report["coverage"]["indexed_cited_by_count"] == 13
    assert report["coverage"]["reference_total"] == 3
    assert report["coverage"]["reference_truncated"] is True
    assert report["coverage"]["citation_truncated"] is True
    assert report["coverage"]["citation_query_total"] == 13
    assert report["coverage"]["citations_returned"] <= 2


def test_literature_context_index_is_bounded_and_stage_contract(tmp_path):
    from theory_master.scholarly_selection import _write_evidence_index
    from theory_master.prompt_stages import stage_context
    from theory_master.skills import identity_block
    retrieval = tmp_path / "retrieval"
    retrieval.mkdir()
    papers = [{"title": "Polymer Network Fracture " + str(i),
               "year": 2026, "doi": f"10.1000/{i}",
               "abstract": "LONG ABSTRACT " * 5000,
               "authors": ["Scientist"], "sources": ["openalex"],
               "citation_count": 2, "score": 0.1} for i in range(31)]
    path = _write_evidence_index(retrieval, papers, [{"connector_error_count": 1}])
    assert path.is_file()
    assert len((retrieval / "papers_top.json").read_text()) < 5000
    assert len(path.read_text()) < 4200
    assert "30" not in path.read_text().split("## Ranked candidates", 1)[-1]
    assert "NEVER cat" in path.read_text()
    handoff = stage_context("literature", "targeted_source", {
        "literature_task": {"search_target": "Polymer fracture", "source_lock": {"doi": "10.1000/1"}},
        "sdk_scholarly_retrieval": {"output_dir": str(tmp_path)}})
    assert handoff["scholarly_retrieval_root"] == str(tmp_path)
    assert set(handoff) == {"search_target", "source_lock", "scholarly_retrieval_root"}
    assert "evidence_index.md" in identity_block("literature")
    assert "禁止直接" in identity_block("literature")


def test_real_sdk_runner_writes_compact_index_without_paid_llm(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from theory_master import scholarly_transport as sdk
    commands = []
    def fake_runner(command, **kwargs):
        commands.append(command)
        out = Path(command[command.index("--output-dir")+1])
        out.mkdir(parents=True, exist_ok=True)
        if "lit_retrieve.py" in command[1]:
            paper = {"doi":"10.1000/abc", "title":"Polymer Network Fracture",
                     "authors":["Scientist"],"source":"openalex","sources":["openalex"],
                     "abstract":"Result abstract", "is_open_access":False,"score":0.9,"year":2025}
            (out/"papers.json").write_text(json.dumps([paper]))
            return SimpleNamespace(returncode=0,stdout="1 metadata hit",stderr="")
        if "read_papers.py" in command[1]:
            import csv
            with (out/"pdf_manifest.csv").open("w") as f:
                w=csv.DictWriter(f, fieldnames=["paper","status","url","method","reason"])
                w.writeheader();w.writerow({"paper":"Polymer Network Fracture","status":"unverified"})
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        (out/"paper_trace_report.md").write_text("metadata only")
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    record = sdk.retrieve_scholarly({
        "source_gap_id":"test-source", "task_type":"targeted_source",
        "search_target":"Polymer Network Fracture", "domain_context":"polymer network fracture",
        "source_lock":{"doi":"10.1000/abc", "title":"Polymer Network Fracture"}
    },tmp_path,runner=fake_runner)
    assert record["status"] == "ready" and record["paper_count"] == 1
    assert record["fulltext_read_count"] == 0
    assert len(commands) == 3
    assert Path(record["evidence_index_md"]).exists()
    assert "Polymer Network Fracture" in Path(record["evidence_index_md"]).read_text()
    assert (Path(record["retrieval_dir"])/"papers_top.json").exists()
    focus_file = Path(record["paper_focus_json"])
    assert focus_file.is_file()
    focus = json.loads(focus_file.read_text())
    assert focus["doi"] == "10.1000/abc"
    assert focus["abstract"] == "Result abstract"
    assert focus["evidence_level"] == "abstract"
    assert "<jats:" not in focus["abstract"]
    assert commands[-1][commands[-1].index("--limit")+1] == "40"


def test_source_lock_alone_is_not_counted_as_retrieved_evidence(tmp_path):
    from types import SimpleNamespace
    from theory_master.scholarly_transport import retrieve_scholarly
    def unavailable(command, **kwargs):
        return SimpleNamespace(returncode=2,stdout="",stderr="provider unreachable")
    result = retrieve_scholarly({
        "task_type":"targeted_source", "source_gap_id":"locked-offline",
        "source_lock":{"doi":"10.1000/only-lock", "title":"Only user-provided paper"},
        "search_target":"Only user-provided paper"
    }, tmp_path, runner=unavailable)
    assert result["status"] == "blocked"
    assert result["transport_ok"] is False
    assert result["metadata_hit_count"] == 0
    assert result["source_lock_only_count"] == 1








