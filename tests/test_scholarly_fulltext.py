"""Public source acquisition regressions; all requests and conversion are local fakes."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys
import time
import subprocess
import threading

import pytest

from theory_master.skills import skills_root


def module(name):
    scripts = skills_root() / "tm-scholar" / "scripts"
    spec = importlib.util.spec_from_file_location("fulltext_fixture_" + name, scripts / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(result)
    finally:
        sys.path.pop(0)
    return result


def paper(**fields):
    return {"title": "Topology controls polymer fracture", "doi": "10.1234/topology", **fields}


def full_html(*, doi="10.1234/topology", title="Topology controls polymer fracture"):
    paragraph = ("The polymer network's chain topology changes the distribution of tensile stress. "
                 "We compare controlled linear and ring architectures at fixed strand density and temperature. ")
    return (f'<html><head><meta name="citation_doi" content="{doi}">'
            f'<meta name="citation_title" content="{title}"></head><body>'
            '<nav>Unrelated publisher navigation</nav><article>'
            f'<h1>{title}</h1><h2>Introduction</h2><p>{paragraph * 5}</p>'
            f'<p>{paragraph * 3}</p><h2>Results and discussion</h2><p>{paragraph * 5}</p>'
            f'<p>{paragraph * 3}</p><h2>References</h2><p>Sources</p>'
            '</article><footer>Publisher legal links</footer></body></html>').encode()


def test_normalization_retains_openalex_html_and_repository_locations():
    retrieve = module("lit_retrieve")
    record = retrieve.normalize({"display_name": "Topology controls polymer fracture", "doi": "10.1234/topology",
        "open_access": {"is_oa": True, "oa_url": "https://example.org/article"},
        "best_oa_location": {"landing_page_url": "https://example.org/article", "license": "cc-by"},
        "locations": [{"is_oa": True, "landing_page_url": "https://arxiv.org/abs/2401.12345"}]}, "openalex")
    assert record["is_open_access"]
    assert record["arxiv_id"] == "2401.12345"
    assert record["open_access_urls"] == ["https://example.org/article", "https://arxiv.org/abs/2401.12345"]


def test_crossref_tdm_link_requires_a_public_open_license():
    retrieve = module("lit_retrieve")
    raw = {"title": ["Topology controls polymer fracture"], "DOI": "10.1234/topology",
           "link": [{"URL": "https://example.org/full.pdf", "content-type": "application/pdf"}],
           "license": [{"URL": "https://publisher.example/tdm-policy"}]}
    assert not retrieve.normalize(raw, "crossref")["is_open_access"]
    raw["license"] = [{"URL": "https://creativecommons.org/licenses/by/4.0/"}]
    normalized = retrieve.normalize(raw, "crossref")
    assert normalized["is_open_access"] and normalized["open_access_url"].endswith("full.pdf")


def test_identity_merge_preserves_complementary_oa_locations():
    identity = module("scholar_identity")
    left = paper(source="openalex", sources=["openalex"], oa_locations=[{"url": "https://example.org/html"}])
    right = paper(source="semanticscholar", sources=["semanticscholar"], oa_locations=[{"url": "https://example.org/paper.pdf"}])
    assert len(identity.merge_paper(left, right)["oa_locations"]) == 2
    assert identity.arxiv_key("https://arxiv.org/html/2401.12345v2#S1") == "2401.12345"


def test_identity_merge_retains_exact_abstract_versions_and_provider_provenance():
    identity = module("scholar_identity")
    arxiv = paper(source="arxiv", sources=["arxiv"], url="https://arxiv.org/abs/cond-mat/0602462",
        abstract="Affine displacement is not imposed: their displacement is governed by force balances. Contribution is &#8532;.")
    journal = paper(source="openalex", sources=["openalex"], url="https://doi.org/10.1234/topology",
        abstract="Affine displacement is not imposed: Their displacement, as well as sliding, is governed by force balances. Contribution is a longer typeset fraction.")
    merged = identity.merge_paper(arxiv, journal)
    assert merged["abstract"] == journal["abstract"]
    variants = merged["abstract_variants"]
    assert {"text": arxiv["abstract"], "provider": "arxiv", "url": arxiv["url"]} in variants
    decoded = arxiv["abstract"].replace("&#8532;", "⅔")
    assert {"text": decoded, "provider": "arxiv", "url": arxiv["url"]} in variants
    assert {"text": journal["abstract"], "provider": "openalex", "url": journal["url"]} in variants
    assert not any("their displacement, as well as sliding" in item["text"] for item in variants)


def test_abstract_variants_are_bounded_and_legacy_merge_provenance_is_honest():
    identity = module("scholar_identity")
    legacy = paper(source="openalex", sources=["openalex", "arxiv"], abstract="A legacy merged abstract.")
    assert identity.source_abstract_variants(legacy)[0]["provider"] == "merged_metadata"
    many = paper(source="arxiv", sources=["arxiv"], abstract_variants=[
        {"text": f"Version {i} " + "a" * 20000, "provider": "arxiv", "url": "https://arxiv.org/abs/2401.12345"}
        for i in range(30)])
    variants = identity.source_abstract_variants(many)
    assert len(variants) == 12 and all(len(item["text"]) <= 16000 for item in variants)


def test_real_article_body_has_identity_raw_source_and_receipt(monkeypatch, tmp_path):
    reader = module("read_papers")
    raw = full_html()
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html; charset=utf-8"))
    text, method = reader.read_source("https://example.org/article", tmp_path, expected_paper=paper())
    assert method == "html/text" and "tensile stress" in text
    assert "publisher navigation" not in text and "Publisher legal" not in text
    receipt = json.loads((tmp_path / "acquisition.json").read_text())
    assert receipt["identity_check"] == "exact_article_doi"
    assert receipt["scientific_status"] == "materialized_unverified"
    assert (tmp_path / "source.html").read_bytes() == raw


@pytest.mark.parametrize("raw,reason", [
    (full_html(doi="10.1234/different"), "requested DOI"),
    (full_html(title="An unrelated clinical trial"), "requested title"),
    ((b'<html><article><h1>Topology controls polymer fracture</h1><h2>Abstract</h2><p>' +
      b'An abstract is not the source body. ' * 150 + b'</p></article></html>'), "article body"),
    (b'<html><h1>Access denied</h1><p>Please sign in.</p></html>', "identity"),
])
def test_wrong_identity_and_abstract_landing_pages_never_become_fulltext(monkeypatch, tmp_path, raw, reason):
    reader = module("read_papers")
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    with pytest.raises(ValueError, match=reason):
        reader.read_source("https://example.org/article", tmp_path, expected_paper=paper())
    assert not (tmp_path / "acquisition.json").exists()


def test_landing_page_alternative_is_followed_without_accepting_its_abstract(monkeypatch, tmp_path):
    reader = module("read_papers")
    landing = (b'<html><meta name="citation_doi" content="10.1234/topology">'
               b'<meta name="citation_fulltext_html_url" content="/body"><h2>Abstract</h2><p>Summary.</p></html>')
    attempts = []
    def download(url, limit):
        attempts.append(url)
        return (landing if url.endswith("landing") else full_html()), "text/html"
    monkeypatch.setattr(reader, "_download", download)
    path = tmp_path / "papers.json"
    path.write_text(json.dumps([paper(is_open_access=True, open_access_url="https://example.org/landing")]))
    output = tmp_path / "reading"
    monkeypatch.setattr(sys, "argv", ["read_papers", "--papers-json", str(path), "--output-dir", str(output)])
    assert reader.main() == 0
    row = next(csv.DictReader((output / "pdf_manifest.csv").open()))
    assert row["status"] == "read" and row["url"] == "https://example.org/body"
    assert attempts == ["https://example.org/landing", "https://example.org/body"]


def test_exact_doi_resolution_rejects_conflict_and_caches_valid_alternative(monkeypatch, tmp_path):
    reader = module("read_papers")
    calls = []
    def download(url, limit):
        calls.append(url)
        raw = ({"doi": "10.1234/conflict", "open_access": {"is_oa": True, "oa_url": "https://example.org/wrong"}}
               if "openalex" in url else {"title": paper()["title"], "externalIds": {"DOI": "10.1234/topology"},
               "openAccessPdf": {"url": "https://example.org/right.pdf"}})
        return json.dumps(raw).encode(), "application/json"
    monkeypatch.setattr(reader, "_download", download)
    target = paper(is_open_access=False, source_locked=True)
    urls, errors = reader.resolve_oa_locations(target, tmp_path, deadline=time.monotonic() + 10)
    assert urls == ["https://example.org/right.pdf"]
    assert len(calls) == 2 and "different DOI" in errors[0]
    assert reader.resolve_oa_locations(target, tmp_path, deadline=time.monotonic() + 10) == (urls, errors)
    assert len(calls) == 2


def test_arxiv_html_does_not_enable_or_call_a_pdf_converter(monkeypatch, tmp_path):
    reader = module("read_papers")
    monkeypatch.setattr(reader, "_download", lambda *args: (full_html(), "text/html"))
    def forbidden(*args, **kwargs):
        pytest.fail("HTML reading must not enable/invoke PDF conversion")
    monkeypatch.setattr(reader, "convert_pdf_to_markdown", forbidden)
    target = paper(arxiv_id="2401.12345", is_open_access=False)
    url = reader.candidate_urls(target)[0]
    assert url == "https://arxiv.org/html/2401.12345"
    text, _ = reader.read_source(url, tmp_path, expected_paper=target)
    assert "tensile stress" in text


def test_oa_resolution_continues_past_previously_failed_location(monkeypatch, tmp_path):
    reader = module("read_papers")
    def download(url, limit):
        raw = ({"doi": "10.1234/topology", "open_access": {"is_oa": True, "oa_url": "https://example.org/already-failed"}}
               if "openalex" in url else {"externalIds": {"DOI": "10.1234/topology"},
               "openAccessPdf": {"url": "https://example.org/new.pdf"}})
        return json.dumps(raw).encode(), "application/json"
    monkeypatch.setattr(reader, "_download", download)
    urls, _ = reader.resolve_oa_locations(paper(), tmp_path, deadline=time.monotonic() + 10,
        exclude={"https://example.org/already-failed"})
    assert urls == ["https://example.org/new.pdf"]


def test_substantial_article_with_custom_section_titles_is_readable(monkeypatch, tmp_path):
    reader = module("read_papers")
    paragraphs = "".join("<p>" + "Controlled chain deformation under external forcing. " * 30 + "</p>" for _ in range(10))
    raw = ('<article><h1>Topology controls polymer fracture</h1>'
           '<h2>Redistributing the strain</h2>' + paragraphs +
           '<h2>Spatial heterogeneity and damage localization</h2>' + paragraphs + '</article>').encode()
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    text, _ = reader.read_source("https://arxiv.org/html/2610.15962", tmp_path, expected_paper=paper())
    assert len(text) > 15000


def test_generic_title_substring_cannot_establish_article_identity(monkeypatch, tmp_path):
    reader = module("read_papers")
    raw = full_html(doi="", title="fracture")
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    with pytest.raises(ValueError, match="requested title"):
        reader.read_source("https://example.org/article", tmp_path, expected_paper=paper())


def test_doi_only_source_needs_matching_identity_in_the_actual_body(monkeypatch, tmp_path):
    reader = module("read_papers")
    raw = full_html(doi="", title="An unrelated clinical trial")
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    with pytest.raises(ValueError, match="identity"):
        reader.read_source("https://example.org/article", tmp_path, expected_paper={"doi": "10.1234/topology"})


def test_published_subtitle_can_match_a_substantial_unsectioned_preprint(monkeypatch, tmp_path):
    reader = module("read_papers")
    paragraphs = "".join("<p>" + "Entanglement constraints determine primitive path statistics. " * 30 + "</p>" for _ in range(10))
    raw = ('<article><h1>Topological Analysis of Linear Polymer Melts</h1>' + paragraphs +
           '<h2>References</h2><p>Prior work.</p></article>').encode()
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    text, _ = reader.read_source("https://arxiv.org/html/cond-mat/0602555", tmp_path,
        expected_paper={"title": "Topological Analysis of Linear Polymer Melts: A Statistical Approach", "doi": "10.1021/ma0607057"})
    assert len(text) > 10000


@pytest.mark.parametrize("reference_titles", [False, True])
def test_long_abstract_and_bibliography_are_not_an_unsectioned_article(monkeypatch, tmp_path, reference_titles):
    reader = module("read_papers")
    abstract = '<h2>Abstract</h2><p>' + 'An abstract alone is not the paper. ' * 150 + '</p>'
    references = ''.join(('<h3>Prior related publication</h3>' if reference_titles else '') +
                         '<p>' + 'Prior work in polymer fracture mechanics. ' * 50 + '</p>' for _ in range(12))
    raw = ('<article><h1>Topology controls polymer fracture</h1>' + abstract +
           '<h2>References</h2>' + references + '</article>').encode()
    monkeypatch.setattr(reader, "_download", lambda *args: (raw, "text/html"))
    with pytest.raises(ValueError, match="article body"):
        reader.read_source("https://example.org/landing", tmp_path, expected_paper=paper())


def test_same_pdf_does_not_repeat_a_failed_converter_at_another_url(monkeypatch, tmp_path):
    reader = module("read_papers")
    from theory_master.document_conversion import ConversionError
    calls = []
    monkeypatch.setattr(reader, "_download", lambda *args: (b"%PDF-1.4\nidentical bytes", "application/pdf"))
    def convert(*args, **kwargs):
        calls.append(args)
        raise ConversionError("Marker conversion timed out")
    monkeypatch.setattr(reader, "convert_pdf_to_markdown", convert)
    for url in ("https://example.org/a.pdf", "https://repository.example/b.pdf"):
        with pytest.raises(ConversionError):
            reader.read_source(url, tmp_path, expected_paper=paper())
    assert len(calls) == 1


def test_reader_outer_timeout_includes_the_configured_converter_budget(monkeypatch):
    from theory_master import document_conversion, scholarly_transport
    monkeypatch.setattr(document_conversion, "load_settings", lambda: {"enabled": True, "timeout_s": 240})
    assert scholarly_transport._reading_timeout(150) == 450
    monkeypatch.setattr(document_conversion, "load_settings", lambda: {"enabled": False, "timeout_s": 240})
    assert scholarly_transport._reading_timeout(150) == 180


@pytest.mark.parametrize("cancel", [False, True])
def test_reader_process_timeout_and_cancel_reap_converter_descendants(tmp_path, cancel):
    from theory_master.scholarly_transport import _run_scholarly_command
    pid_file = tmp_path / "converter.pid"
    code = ("import subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)")
    event = threading.Event()
    timer = threading.Timer(.4, event.set) if cancel else None
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError if cancel else subprocess.TimeoutExpired):
            _run_scholarly_command([sys.executable, "-c", code], timeout=5 if cancel else .4,
                                   cancel_event=event if cancel else None)
    finally:
        if timer:
            timer.cancel()
    assert time.monotonic() - started < 2
    assert pid_file.is_file()
    status = Path(f"/proc/{int(pid_file.read_text())}/stat")
    try:
        state = status.read_text().split()[2]
    except (FileNotFoundError, ProcessLookupError):
        state = None
    assert state in {None, "Z"}
