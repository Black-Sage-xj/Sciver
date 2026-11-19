"""Cycle Literature claims require actual, same-source SDK material and quotes."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from types import SimpleNamespace

import pytest

from theory_master.contract_closure import close_agent_output
from theory_master.contracts import role_contract
from theory_master.evidence import EvidenceRegistry
from theory_master.literature_source_access import MAX_SOURCE_BYTES, assess_source_access, resolve_paper_reference

QUOTE = "A bounded source result was measured."
DOI = "10.1000/target"


def write_manifest(fixture, rows=None):
    with fixture.manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["key", "title", "doi", "arxiv_id", "year", "authors", "status", "markdown_path"])
        writer.writeheader()
        writer.writerows(rows if rows is not None else [{
            "key": "source-1", "title": "Target paper", "doi": DOI, "arxiv_id": "",
            "status": "read", "markdown_path": str(fixture.source),
        }])


@pytest.fixture
def corpus(tmp_path):
    cycle = tmp_path / "research" / "1"
    workspace = cycle / "workspace"
    sdk = workspace / "literature" / "sdk_retrieval" / "source-gap"
    sdk.mkdir(parents=True)
    fixture = SimpleNamespace(cycle=cycle, workspace=workspace, sdk=sdk,
        papers=sdk / "papers.json", manifest=sdk / "pdf_manifest.csv", source=sdk / "read.md")
    fixture.papers.write_text(json.dumps([{"doi": DOI, "title": "Target paper", "abstract": QUOTE}]), encoding="utf-8")
    fixture.source.write_text("# Original source\n\n" + QUOTE + "\n", encoding="utf-8")
    write_manifest(fixture)
    fixture.transport = {"status": "ready", "transport_ok": True,
        "papers_json": str(fixture.papers), "reading": {"pdf_manifest": str(fixture.manifest)}}
    return fixture


def register(fixture, *, declared="full_text", quote=QUOTE, source_ref="paper:0"):
    raw = {"evidence_records": [{"source_ref": source_ref, "source_access": declared,
        "exact_quote": quote, "locator": {"section": "Results"},
        "interpretation": "Synthetic source interpretation.", "scientific_insight": "Synthetic authored statement."}],
        "remaining_scientific_question": ""}
    context = {"literature_task": {"task_type": "targeted_source", "source_gap_id": "gap-1"},
               "sdk_scholarly_retrieval": fixture.transport, "cycle_workspace": str(fixture.workspace)}
    role_contract("literature", phase="targeted_source").validate(raw, context)
    state = {"scientific_verdict": "supported", "hypothesis_lock": {"hypothesis": "Retained H"}}
    before = copy.deepcopy(state)
    closed = close_agent_output("literature", "targeted_source", raw, context, state)
    registry = EvidenceRegistry(fixture.cycle, fixture.workspace)
    registry.register_role_output("literature", closed, epoch="post_lock_test",
        producer_invocation={"invocation_id": "inv-literature", "role": "literature"},
        source_transport=fixture.transport)
    record = registry.context()["evidence_manifest"]["payload"]["records"][0]
    assert state == before
    assert record.get("exact_quote", "") == quote
    assert record["source_access"]["declared"] == declared
    return registry, record


def assert_unverified(record):
    assert record["source_access"]["verified"] == "unavailable"
    assert record["source_access"]["verification_status"] == "unverified"
    assert record["source_access"]["quote_located"] is False
    assert record["source_access"]["reason"]
    assert record["adjudicable"] is False


def test_matching_fulltext_and_quote_bind_all_input_hashes(corpus):
    _, record = register(corpus)
    access = record["source_access"]
    assert access["verified"] == "full_text" and access["quote_located"] is True
    assert record["adjudicable"] is True
    assert access["reading_ref"]["markdown_path"] == str(corpus.source)
    assert access["artifact_hashes"] == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [corpus.papers, corpus.manifest, corpus.source]}


def test_missing_fulltext_cannot_be_verified_by_read_status(corpus):
    corpus.source.unlink()
    _, record = register(corpus)
    assert_unverified(record)


def test_unrelated_manifest_record_cannot_match_shared_empty_identity_fields(corpus):
    write_manifest(corpus, [{"key": "other-key", "title": "Unrelated source", "doi": "10.2000/other",
                            "status": "read", "markdown_path": str(corpus.source)}])
    _, record = register(corpus)
    assert_unverified(record)


def test_conflicting_doi_is_not_repaired_by_equal_title(corpus):
    write_manifest(corpus, [{"key": "other-key", "title": "Target paper", "doi": "10.2000/other",
                            "status": "read", "markdown_path": str(corpus.source)}])
    _, record = register(corpus)
    assert_unverified(record)


def test_conflicting_arxiv_is_not_repaired_by_equal_doi(corpus):
    corpus.papers.write_text(json.dumps([{"doi": DOI, "arxiv_id": "2401.12345v1",
        "title": "Target paper", "abstract": QUOTE}]), encoding="utf-8")
    write_manifest(corpus, [{"key": "source-1", "title": "Target paper", "doi": DOI,
        "arxiv_id": "2401.12346v2", "status": "read", "markdown_path": str(corpus.source)}])
    _, record = register(corpus)
    assert_unverified(record)


@pytest.mark.parametrize("extra_doi", [False, True])
def test_equivalent_arxiv_versions_and_newly_enriched_doi_bind_same_source(corpus, extra_doi):
    corpus.papers.write_text(json.dumps([{"arxiv_id": "2401.12345v1", "title": "Target paper", "abstract": QUOTE}]), encoding="utf-8")
    write_manifest(corpus, [{"key": "source-1", "title": "Target paper",
        "doi": DOI if extra_doi else "", "arxiv_id": "https://arxiv.org/pdf/2401.12345v2.pdf",
        "status": "read", "markdown_path": str(corpus.source)}])
    _, record = register(corpus, source_ref="https://arxiv.org/abs/2401.12345v3")
    assert record["source_access"]["verified"] == "full_text" and record["adjudicable"] is True


def test_canonical_doi_url_and_case_resolve_same_source(corpus):
    _, record = register(corpus, source_ref="https://doi.org/10.1000/TARGET")
    assert record["adjudicable"] is True


@pytest.mark.parametrize("metadata", [{"year": "1970"}, {"year": "2020", "authors": "Bob Other"}])
def test_same_title_does_not_override_contradictory_weak_metadata(corpus, metadata):
    corpus.papers.write_text(json.dumps([{"doi": DOI, "title": "Target paper", "abstract": QUOTE,
        "year": 2020, "authors": ["Alice Scientist"]}]), encoding="utf-8")
    write_manifest(corpus, [{"key": "legacy", "title": "Target paper", **metadata,
        "status": "read", "markdown_path": str(corpus.source)}])
    _, record = register(corpus)
    assert_unverified(record)


def test_unique_legacy_manifest_default_text_path_is_still_bounded_and_checked(corpus):
    default = corpus.manifest.parent / "paper-01.md"
    corpus.source.rename(default)
    write_manifest(corpus, [{"key": "source-1", "doi": DOI, "status": "read"}])
    _, record = register(corpus)
    assert record["adjudicable"] is True
    assert record["source_access"]["reading_ref"]["markdown_path"] == str(default)


def test_current_reader_paper_and_text_path_columns_establish_fulltext(corpus):
    with corpus.manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["paper", "doi", "arxiv_id", "text_path", "status"])
        writer.writeheader()
        writer.writerow({"paper": "Target paper", "doi": DOI, "text_path": str(corpus.source), "status": "read"})
    _, record = register(corpus)
    assert record["source_access"]["verified"] == "full_text" and record["adjudicable"] is True


def test_explicit_paper_index_remains_unambiguous_when_titles_collide(corpus):
    corpus.papers.write_text(json.dumps([{"doi": DOI, "title": "Target paper", "abstract": QUOTE},
        {"doi": "10.2000/other", "title": "Target paper", "abstract": QUOTE}]), encoding="utf-8")
    _, record = register(corpus, source_ref="paper:0")
    assert record["adjudicable"] is True


def test_empty_normalized_identity_never_selects_empty_metadata():
    assert resolve_paper_reference("...", [{"title": "", "doi": ""}]) == {}


@pytest.mark.parametrize("declared", ["full_text", "abstract", "metadata"])
def test_quote_absent_from_actual_material_is_retained_but_not_adjudicable(corpus, declared):
    _, record = register(corpus, declared=declared, quote="THIS PASSAGE DOES NOT EXIST IN THE SOURCE")
    assert_unverified(record)


@pytest.mark.parametrize("declared,quote", [("abstract", QUOTE), ("metadata", "Target paper")])
def test_access_level_uses_the_actual_corresponding_material(corpus, declared, quote):
    _, record = register(corpus, declared=declared, quote=quote)
    assert record["adjudicable"] is True
    assert record["source_access"]["verified"] == declared
    assert set(record["source_access"]["artifact_hashes"]) == {str(corpus.papers)}


def test_whitespace_normalized_quote_in_actual_abstract_variant_is_accepted(corpus):
    corpus.papers.write_text(json.dumps([{"doi": DOI, "title": "Target paper", "abstract": "A different abstract.",
        "abstract_variants": [{"text": "A bounded\n source result\twas measured.", "provider": "original-provider"}]}]), encoding="utf-8")
    _, record = register(corpus, declared="abstract")
    assert record["source_access"]["quote_located"] is True
    assert record["adjudicable"] is True


def test_ambiguous_source_title_does_not_select_the_first_paper(corpus):
    corpus.papers.write_text(json.dumps([{"doi": DOI, "title": "Target paper", "abstract": QUOTE},
        {"doi": "10.2000/other", "title": "Target paper", "abstract": QUOTE}]), encoding="utf-8")
    _, record = register(corpus, source_ref="Target paper")
    assert_unverified(record)


def test_duplicate_reading_records_require_an_unambiguous_binding(corpus):
    row = {"key": "source-1", "title": "Target paper", "doi": DOI, "status": "read", "markdown_path": str(corpus.source)}
    write_manifest(corpus, [row, {**row, "key": "duplicate"}])
    _, record = register(corpus)
    assert_unverified(record)


@pytest.mark.parametrize("which", ["papers", "manifest", "source"])
def test_symlinks_in_any_source_input_are_rejected(corpus, which):
    path = getattr(corpus, which)
    real = path.with_name(path.name + ".real")
    path.rename(real)
    path.symlink_to(real)
    _, record = register(corpus)
    assert_unverified(record)


def test_symlinked_source_parent_is_rejected(corpus):
    real = corpus.sdk / "real"
    real.mkdir()
    target = real / "read.md"
    target.write_text(QUOTE, encoding="utf-8")
    alias = corpus.sdk / "alias"
    alias.symlink_to(real, target_is_directory=True)
    corpus.source = alias / "read.md"
    write_manifest(corpus)
    _, record = register(corpus)
    assert_unverified(record)


@pytest.mark.parametrize("location", ["benchmark", "experiment", "parent_traversal"])
def test_files_outside_sdk_retrieval_do_not_establish_fulltext(corpus, location):
    if location == "benchmark":
        source = corpus.cycle.parent.parent / "benchmark" / "public.md"
    elif location == "experiment":
        source = corpus.workspace / "experiment.md"
    else:
        source = corpus.sdk / ".." / "outside.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(QUOTE, encoding="utf-8")
    corpus.source = source
    write_manifest(corpus)
    _, record = register(corpus)
    assert_unverified(record)
    assert source.read_text() == QUOTE


@pytest.mark.parametrize("contents", [b"", b"\xff\xfe\x80"])
def test_empty_or_non_text_source_is_not_verified(corpus, contents):
    corpus.source.write_bytes(contents)
    _, record = register(corpus)
    assert_unverified(record)


def test_oversized_source_is_rejected_before_unbounded_read(corpus):
    with corpus.source.open("wb") as stream:
        stream.seek(MAX_SOURCE_BYTES)
        stream.write(b"x")
    _, record = register(corpus)
    assert_unverified(record)


@pytest.mark.parametrize("which", ["papers", "manifest", "source"])
def test_source_integrity_drift_retracts_adjudicability_without_rewriting_original_record(corpus, which):
    registry, record = register(corpus)
    assert record["adjudicable"] is True
    original = registry.records_path.read_bytes()
    path = getattr(corpus, which)
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    after = registry.context()["evidence_manifest"]["payload"]["records"][0]
    assert after["adjudicable"] is False
    assert after["lifecycle_status"] == "integrity_drifted"
    assert str(path) in after["integrity_current"]["changed_paths"]
    assert registry.records_path.read_bytes() == original


def test_unavailable_declaration_remains_audit_only(corpus):
    _, record = register(corpus, declared="unavailable", quote="")
    assert record["source_access"]["verified"] == "unavailable"
    assert record["adjudicable"] is False


def test_helper_does_not_read_or_verify_an_unregistered_source_identity(corpus):
    access = assess_source_access("full_text", "10.9999/unknown", corpus.transport,
                                 {"exact_quote": QUOTE}, workspace=corpus.workspace)
    assert access["verified"] == "unavailable" and not access["quote_located"]


@pytest.mark.parametrize("kind", ["outside", "symlink", "oversized"])
def test_formal_cycle_metadata_enrichment_refuses_unsafe_index_before_read(corpus, monkeypatch, kind):
    if kind == "outside":
        target = corpus.cycle.parent.parent / "benchmark" / "papers.json"
        target.parent.mkdir()
        target.write_text(corpus.papers.read_text(), encoding="utf-8")
        corpus.transport["papers_json"] = str(target)
    elif kind == "symlink":
        target = corpus.papers
        real = target.with_suffix(".original")
        target.rename(real)
        target.symlink_to(real)
    else:
        target = corpus.papers
        with target.open("wb") as stream:
            stream.seek(MAX_SOURCE_BYTES)
            stream.write(b"x")
    from pathlib import Path
    original_open = Path.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if path == target and "r" in mode:
            pytest.fail("unsafe source index must be rejected before reading its contents")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    _, record = register(corpus)
    assert_unverified(record)
    assert record["literature_source"]["paper_title"] == "paper:0"


def test_legacy_metadata_enrichment_alone_does_not_grant_verified_access(corpus):
    legacy = corpus.workspace / "legacy-papers.json"
    legacy.write_text(corpus.papers.read_text(), encoding="utf-8")
    transport = {**corpus.transport, "papers_json": str(legacy)}
    closed = close_agent_output("literature", "targeted_source", {
        "evidence_records": [{"source_ref": "paper:0", "source_access": "abstract", "exact_quote": QUOTE}],
        "remaining_scientific_question": ""},
        {"literature_task": {"task_type": "targeted_source"}, "sdk_scholarly_retrieval": transport}, {})
    assert closed["evidence_records"][0]["doi_or_url"] == DOI
    access = assess_source_access("abstract", DOI, transport, {"exact_quote": QUOTE}, workspace=corpus.workspace)
    assert access["verified"] == "unavailable" and access["verification_status"] == "unverified"


@pytest.mark.parametrize("missing", ["protocol", "quote_located", "artifact_hashes"])
def test_legacy_access_label_without_current_proof_remains_audit_only(corpus, missing):
    registry, record = register(corpus)
    assert record["adjudicable"] is True
    rows = [json.loads(line) for line in registry.records_path.read_text().splitlines()]
    rows[0]["source_access"].pop(missing)
    registry.records_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    legacy_bytes = registry.records_path.read_bytes()
    projected = registry.context()["evidence_manifest"]["payload"]["records"][0]
    assert projected["source_access"]["verified"] == "full_text"
    assert projected["adjudicable"] is False
    assert registry.records_path.read_bytes() == legacy_bytes
