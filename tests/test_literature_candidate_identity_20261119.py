"""Provider identity regressions use receipts only; no network or model calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.scholarly_selection import _identity_helpers
from theory_master.scholarly_transport import retrieve_scholarly


def article(**fields):
    return {
        "title": "An exact repeated title",
        "doi": "",
        "year": 2000,
        "authors": ["Alice Original"],
        "source": "openalex",
        "abstract": "Original paper",
        **fields,
    }


def retrieve(tmp_path, batches, *, lock=None, max_candidates=60):
    calls = []
    batch_index = 0

    def runner(command, **kwargs):
        nonlocal batch_index
        calls.append(command)
        name = Path(command[1]).name
        assert name in {"lit_retrieve.py", "read_papers.py", "trace_paper.py"}
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if name == "lit_retrieve.py":
            (output / "fetched.json").write_text(
                json.dumps(batches[batch_index]), encoding="utf-8"
            )
            batch_index += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    task = {
        "task_type": "targeted_source" if lock else "landscape",
        "search_queries": [f"query {index}" for index in range(len(batches))],
        "max_candidates": max_candidates,
    }
    if lock:
        assert len(batches) == 1
        task["source_lock"] = lock
    result = retrieve_scholarly(task, tmp_path, runner=runner)
    assert batch_index == len(batches)
    return result, json.loads(Path(result["papers_json"]).read_text()), calls


@pytest.mark.parametrize(
    "changes",
    [
        {"year": 2024},
        {"authors": ["Bob Different"]},
        {"year": 2024, "authors": ["Bob Different"]},
    ],
)
def test_same_title_conflicting_weak_identities_remain_separate(tmp_path, changes):
    original = article()
    other = article(**changes, source="crossref", abstract="Unrelated later work")
    assert not _identity_helpers().same_paper(original, other)
    result, rows, _ = retrieve(tmp_path, [[original], [other]])
    assert (
        len(rows)
        == result["metadata_hit_count"]
        == result["candidate_count_before_limit"]
        == 2
    )
    assert result["source_lock_only_count"] == 0
    assert {(row["year"], tuple(row["authors"]), row["abstract"]) for row in rows} == {
        (row["year"], tuple(row["authors"]), row["abstract"])
        for row in (original, other)
    }
    assert sorted(row["discovery_queries"] for row in rows) == [
        ["query 0"],
        ["query 1"],
    ]


def test_shared_doi_does_not_override_conflicting_strong_arxiv_ids(tmp_path):
    first = article(doi="10.1234/shared", arxiv_id="2001.00001")
    second = article(doi="10.1234/shared", arxiv_id="2001.00002", source="crossref")
    assert not _identity_helpers().same_paper(first, second)
    result, rows, _ = retrieve(tmp_path, [[first, second]])
    assert len(rows) == result["metadata_hit_count"] == 2
    assert {row["arxiv_id"] for row in rows} == {"2001.00001", "2001.00002"}


@pytest.mark.parametrize(
    "changes",
    [
        {"year": 2024},
        {"authors": ["Bob Different"]},
        {"year": 2024, "authors": ["Bob Different"]},
    ],
)
def test_title_collision_cannot_overwrite_or_bind_requested_seed(tmp_path, changes):
    lock = {key: article()[key] for key in ("title", "year", "authors")}
    other = article(
        **changes,
        source="crossref",
        abstract="Wrong identity",
        is_open_access=True,
        open_access_url="https://example.invalid/wrong",
    )
    result, rows, _ = retrieve(tmp_path, [[other]], lock=lock)
    assert len(rows) == 2
    seed = next(row for row in rows if row.get("source_locked"))
    assert seed["source"] == "source-lock"
    assert {key: seed[key] for key in lock} == lock
    assert seed["identity_resolution"] == "unresolved"
    assert not seed["abstract"] and not seed["open_access_url"]
    provider = next(row for row in rows if row is not seed)
    assert not provider.get("source_locked")
    assert provider["abstract"] == other["abstract"]
    assert result["metadata_hit_count"] == result["source_lock_only_count"] == 1


def test_title_only_request_keeps_distinct_provider_identities_ambiguous(tmp_path):
    result, rows, _ = retrieve(
        tmp_path,
        [[article(), article(year=2024, authors=["Bob Different"])]],
        lock={"title": article()["title"]},
    )
    assert len(rows) == 3
    seed = next(row for row in rows if row.get("source_locked"))
    assert (
        seed["source"] == "source-lock" and seed["identity_resolution"] == "ambiguous"
    )
    assert result["metadata_hit_count"] == 2 and result["source_lock_only_count"] == 1


def test_conflicting_arxiv_metadata_cannot_bind_exact_doi_lock(tmp_path):
    lock = {
        "doi": "10.1234/shared",
        "arxiv_id": "2001.00001",
        "title": article()["title"],
    }
    result, rows, _ = retrieve(
        tmp_path, [[article(doi=lock["doi"], arxiv_id="2001.00002")]], lock=lock
    )
    seed = next(row for row in rows if row.get("source_locked"))
    assert (
        seed["source"] == "source-lock" and seed["identity_resolution"] == "unresolved"
    )
    assert result["metadata_hit_count"] == result["source_lock_only_count"] == 1


def test_shared_doi_conflicting_arxiv_candidates_are_ambiguous_for_title_lock(tmp_path):
    result, rows, _ = retrieve(
        tmp_path,
        [
            [
                article(doi="10.1234/shared", arxiv_id="2001.00001"),
                article(doi="10.1234/shared", arxiv_id="2001.00002"),
            ]
        ],
        lock={"title": article()["title"]},
    )
    seed = next(row for row in rows if row.get("source_locked"))
    assert (
        seed["source"] == "source-lock" and seed["identity_resolution"] == "ambiguous"
    )
    assert len(rows) == 3 and result["metadata_hit_count"] == 2


def test_matching_weak_identity_keeps_complementary_query_and_oa_provenance(tmp_path):
    original = article()
    second = article(
        source="crossref",
        open_access_url="https://example.invalid/original",
        is_open_access=True,
        abstract="Complementary provider abstract",
    )
    result, rows, _ = retrieve(tmp_path, [[original], [second]])
    assert len(rows) == result["metadata_hit_count"] == 1
    assert rows[0]["sources"] == ["crossref", "openalex"]
    assert rows[0]["discovery_queries"] == ["query 0", "query 1"]
    assert rows[0]["open_access_url"] == second["open_access_url"]


def test_canonical_doi_aliases_resolve_to_same_provider_identity(tmp_path):
    first = article(doi="https://doi.org/10.1234/ABC")
    second = article(doi="doi:10.1234/abc.", source="crossref")
    result, rows, _ = retrieve(tmp_path, [[first], [second]])
    assert len(rows) == result["metadata_hit_count"] == 1
    assert rows[0]["discovery_queries"] == ["query 0", "query 1"]


def test_weak_record_cannot_bridge_two_doi_identities(tmp_path):
    result, rows, _ = retrieve(
        tmp_path,
        [
            [article(doi="10.1234/one"), article(doi="10.1234/two")],
            [article(source="crossref")],
        ],
    )
    assert len(rows) == result["metadata_hit_count"] == 3
    assert {row["doi"] for row in rows} == {"10.1234/one", "10.1234/two", ""}
    assert all(len(row["discovery_queries"]) == 1 for row in rows)


def test_unresolved_seed_does_not_become_provider_hit(tmp_path):
    result, rows, _ = retrieve(tmp_path, [[]], lock={"title": article()["title"]})
    assert result["status"] == "empty"
    assert result["metadata_hit_count"] == 0 and result["source_lock_only_count"] == 1
    assert rows[0]["identity_resolution"] == "unresolved"


def test_candidate_cap_counts_retained_identities_not_colliding_keys(tmp_path):
    result, rows, _ = retrieve(
        tmp_path, [[article(), article(year=2024)]], max_candidates=1
    )
    assert len(rows) == result["metadata_hit_count"] == 1
    assert result["candidate_count_before_limit"] == 2
    assert result["source_lock_only_count"] == 0


def test_title_alone_does_not_prove_two_provider_records_are_same_paper(tmp_path):
    first = article(year=None, authors=[])
    second = article(year=None, authors=[], source="crossref")
    assert not _identity_helpers().same_paper(first, second)
    result, rows, _ = retrieve(tmp_path, [[first], [second]])
    assert len(rows) == result["metadata_hit_count"] == 2


def test_compatible_title_lock_binds_provider_without_counting_seed_as_hit(tmp_path):
    lock = {key: article()[key] for key in ("title", "year", "authors")}
    result, rows, _ = retrieve(tmp_path, [[article()]], lock=lock)
    assert len(rows) == result["metadata_hit_count"] == 1
    assert result["source_lock_only_count"] == 0
    assert rows[0]["source"] == "openalex" and rows[0]["source_locked"]
    assert rows[0]["identity_resolution"] == "unique_exact_title_candidate"


def test_arxiv_lock_requires_matching_explicit_identifier(tmp_path):
    lock = {"title": article()["title"], "arxiv_id": "2001.00001v2"}
    result, rows, _ = retrieve(
        tmp_path,
        [
            [
                article(arxiv_id="2001.00002"),
                article(arxiv_id="https://arxiv.org/abs/2001.00001"),
            ]
        ],
        lock=lock,
    )
    assert result["metadata_hit_count"] == 2 and result["source_lock_only_count"] == 0
    locked = next(row for row in rows if row.get("source_locked"))
    assert locked["arxiv_id"].endswith("2001.00001")
    assert locked["identity_resolution"] == "exact_arxiv"
    assert locked["requested_source"]["arxiv_id"] == lock["arxiv_id"]


def test_provider_cannot_carry_an_unverified_source_lock_binding(tmp_path):
    row = article(
        source_locked=True,
        source_lock_status="locked",
        identity_resolution="exact_doi",
        requested_source={"doi": "10.1234/unrelated"},
    )
    result, rows, _ = retrieve(tmp_path, [[row]])
    assert result["metadata_hit_count"] == 1
    assert not any(
        field in rows[0]
        for field in (
            "source_locked",
            "source_lock_status",
            "identity_resolution",
            "requested_source",
        )
    )
