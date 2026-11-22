from __future__ import annotations

import copy
import hashlib
import json

import pytest

from theory_master.report_synthesis.retrieval import build_report_bundle


def document(reference, content, *, roles=None, suffix=None):
    return {"source_ref": "file:" + reference, "reference": reference, "source_path": "/synthetic/" + reference,
            "id": "file:" + reference, "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "size": len(content.encode()), "kind": "document", "status": "available",
            "roles": roles or ["referenced_material"], "content": content,
            "frozen_path": "files/frozen" + (suffix or "." + reference.rsplit(".", 1)[-1]),
            "frozen_sha256": hashlib.sha256(content.encode()).hexdigest()}


def snapshot(*documents):
    index = {"H-0001": {"kind": "hypothesis", "id": "H-0001"},
             "H-0002": {"kind": "hypothesis", "id": "H-0002"},
             "outcome-1": {"kind": "outcome", "id": "outcome-1"},
             "outcome-2": {"kind": "outcome", "id": "outcome-2"},
             "E-1": {"kind": "evidence", "id": "E-1"}}
    for row in documents:
        for ref in (row["source_ref"], row["reference"], row["source_path"]):
            index[ref] = {"kind": "document", "id": row["id"], "sha256": row["sha256"]}
    return {"schema": "graph-research/final-report-snapshot/v1", "fingerprint": "frozen-source-identity",
            "original_question": {"content": "Compare a synthetic pair of models", "source_ref": "research_plan"},
            "scientific_model": {"content": "A bounded explanation"}, "goal_terminal": {"source_cycle": "2", "verdict": "passed"},
            "hypotheses": [{"id": "H-0001", "summary": "First bounded hypothesis"},
                           {"id": "H-0002", "summary": "Revised scope", "research_transition": {"prior_gap": "Counterexample", "current_change": "Narrow scope"}}],
            "lineage": [{"id": "edge", "source_hypothesis": "H-0001", "target_hypothesis": "H-0002", "role": "main"}],
            "outcomes": [{"id": "outcome-1", "hypothesis_id": "H-0001", "cycle_id": "1", "verdict": "rejected", "summary": "Counterexample found",
                           "boundary": ["Synthetic scope"], "findings": ["A decisive negative result"], "evidence_refs": ["E-1"]},
                          {"id": "outcome-2", "hypothesis_id": "H-0002", "cycle_id": "2", "verdict": "supported", "summary": "Narrower relation holds"}],
            "preservations": [{"id": "preserved-3", "cycle_id": "3", "verdict": "unadjudicated"}],
            "evidence_records": [{"evidence_id": "E-1", "source_ref": "E-1", "scope": "Original scope", "adjudicable": True}],
            "external_sources": [], "documents": list(documents), "figures": [], "source_index": index, "warnings": []}


def test_preserves_all_scoped_scientific_records_negative_results_and_source_identity():
    source = snapshot(document("round-1.md", "# A negative result\nCounterexample found", roles=["round_report"]))
    before = copy.deepcopy(source)
    bundle = build_report_bundle(source)
    for key in ("hypotheses", "lineage", "outcomes", "preservations", "evidence_records", "original_question", "scientific_model", "goal_terminal"):
        assert bundle[key] == source[key]
    assert source == before
    assert bundle["fingerprint"] == source["fingerprint"]
    assert bundle["derived"] is True
    assert bundle["retrieval"]["coverage"]["negative_outcomes"] == 1
    assert bundle["source_index"]["E-1"]["visibility"] == "full"
    assert bundle["retrieval"]["serialized_chars"] == len(json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def test_indexes_raw_bootstrap_arrays_and_retains_recorded_estimates_intervals_and_small_tables():
    result = {"estimate": 2.5, "confidence_interval": [2.1, 2.9],
              "summary_table": [{"model": "A", "loss": 0.1}, {"model": "B", "loss": 0.2}],
              "bootstrap_samples": list(range(5000)), "predictions": [{"row": i, "value": i / 10} for i in range(200)]}
    bundle = build_report_bundle(snapshot(document("result.json", json.dumps(result))))
    row = bundle["documents"][0]
    parsed = json.loads(row["content"])
    for field in ("estimate", "confidence_interval", "summary_table"):
        assert parsed[field] == result[field]
    assert parsed["bootstrap_samples"]["length"] == 5000
    assert parsed["predictions"]["_retrieval"] == "index_only_array"
    assert row["visibility"] == "structured_projection"
    assert "mean" not in parsed["bootstrap_samples"]
    assert bundle["source_index"][row["source_ref"]]["visibility"] == "structured_projection"


def test_scripts_raw_csv_and_execution_records_are_explicitly_index_only():
    rows = [document("verify.py", "print('synthetic verifier')"),
            document("raw.csv", "observation,value\n" + "\n".join(f"{i},{i}" for i in range(200))),
            document("unit.json", json.dumps({"argv": ["python", "verify.py"], "cwd": "/synthetic", "inputs_sha256": {}})),
            document("summary.csv", "model,estimate\nA,1.2\nB,1.3")]
    bundle = build_report_bundle(snapshot(*rows))
    assert [row["visibility"] for row in bundle["documents"]] == ["index_only", "index_only", "index_only", "full"]
    assert all("content" not in row for row in bundle["documents"][:3])
    assert bundle["documents"][1]["retrieval_index"]["rows_including_header"] == 201
    assert bundle["documents"][3]["content"] == rows[3]["content"]
    assert len(bundle["retrieval"]["index_only_refs"]) == 3


def test_compact_row_aligned_result_table_remains_readable():
    table = "row,result\n" + "".join(f"{i},{i % 2}\n" for i in range(55))
    bundle = build_report_bundle(snapshot(document("delivered.csv", table)))
    assert bundle["documents"][0]["content"] == table
    assert bundle["documents"][0]["visibility"] == "full"


def test_wide_recorded_result_keeps_exact_row_keys_and_output_column():
    columns = [f"feature_{i}" for i in range(20)]
    header = ",".join(["row_index", *columns, "label"]) + "\n"
    table = header + "".join(",".join([str(i), *(["0.0"] * 20), str(i % 2)]) + "\n" for i in range(55))
    row = document("delivered.csv", table, roles=["recorded_evidence_artifact"])
    bundle = build_report_bundle(snapshot(row))
    projected = bundle["documents"][0]
    assert projected["visibility"] == "structured_projection"
    assert projected["retrieval_index"]["projected_column_indices"] == [0, 21]
    assert projected["content"] == "row_index,label\n" + "".join(f"{i},{i % 2}\n" for i in range(55))


def test_many_raw_detail_records_are_indexed_but_named_scientific_parameters_survive():
    raw = {"recovery_details": {f"unit-{i}": {"attempts": list(range(100)), "status": "complete"} for i in range(100)},
           "named_parameters": {f"parameter_{i}": {"estimate": i / 100, "scope": "Synthetic"} for i in range(70)}}
    bundle = build_report_bundle(snapshot(document("result.json", json.dumps(raw))))
    parsed = json.loads(bundle["documents"][0]["content"])
    assert parsed["recovery_details"]["record_count"] == 100
    assert parsed["named_parameters"] == raw["named_parameters"]


def test_exact_duplicate_text_has_a_visible_pointer_and_reproduction_metadata_is_indexed():
    text = "Scoped synthetic result. " * 100
    report = "# Scientific result\n" + text + "\n## 复现索引\n" + "/synthetic/machine-path\n" * 100
    source = snapshot(document("round.md", report, roles=["round_report"]), document("copy.md", text))
    source["outcomes"][0].update(summary=text, report_context={"body": text, "reproduction_index": "machine metadata"})
    bundle = build_report_bundle(source)
    assert "body" not in bundle["outcomes"][0]["report_context"]
    assert bundle["outcomes"][0]["report_context"]["body_visible_source"] == "outcomes[0].summary"
    assert bundle["outcomes"][0]["summary"] == text
    assert "machine-path" not in bundle["documents"][0]["content"]
    assert bundle["documents"][1]["content_visible_source"] == "outcomes[0].summary"


def test_file_alias_compaction_preserves_canonical_files_and_every_scientific_id():
    source = snapshot(document("result.json", '{"estimate":1.5}'))
    bundle = build_report_bundle(source)
    assert set(bundle["source_index"]) == {"H-0001", "H-0002", "outcome-1", "outcome-2", "E-1", "file:result.json"}
    assert bundle["source_index"]["file:result.json"]["material_id"] == bundle["documents"][0]["material_id"]
    assert bundle["documents"][0]["sha256"] == source["documents"][0]["sha256"]
    assert "frozen_path" not in bundle["documents"][0]
    assert any(t["action"] == "canonicalize_redundant_file_aliases" for t in bundle["retrieval"]["transformations"])


def test_over_budget_scientific_content_fails_without_truncation_or_source_mutation():
    source = snapshot()
    source["outcomes"][0]["summary"] = "An essential scoped scientific sentence. " * 1000
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="no silent truncation"):
        build_report_bundle(source, max_chars=10000)
    assert source == before


def test_budget_indexes_supplements_but_keeps_round_reports_and_visible_result_fields():
    kept = document("round.md", "The recorded counterexample changed the design.", roles=["round_report"])
    large = document("registered.json", json.dumps({"estimate": 2.5, "raw_details": "x" * 25_000}),
                     roles=["registered_evidence_artifact"])
    optional = document("supplement.md", "unrelated detail " * 2_000)
    source = snapshot(kept, large, optional)
    unchanged = copy.deepcopy(source)
    bundle = build_report_bundle(source, max_chars=6500)
    doc, projected, indexed = bundle["documents"]
    assert doc["content"] == kept["content"]
    assert json.loads(projected["content"]) == {"estimate": 2.5}
    assert projected["budget_projection"]["omitted_keys"] == ["raw_details"]
    assert bundle["source_index"][large["source_ref"]]["visibility"] == "structured_projection"
    assert bundle["source_index"][optional["source_ref"]]["visibility"] == "index_only"
    assert indexed["retrieval_index"]["reason"] == "supplemental_body_over_budget"
    assert bundle["outcomes"] == source["outcomes"]
    assert source == unchanged


def test_budget_prioritizes_terminal_cycle_result_over_earlier_supplements():
    other = document("1/workspace/earlier.txt", "Earlier comparison. " * 115,
                     roles=["registered_material"])
    closing = document("2/workspace/closing.txt", "Closing result. " * 165,
                       roles=["registered_material"])
    source = snapshot(other, closing)
    source["sources"] = {"research_root": "/synthetic"}
    bundle = build_report_bundle(source, max_chars=6500)
    assert [row["visibility"] for row in bundle["documents"]] == ["index_only", "full"]


def test_unavailable_figures_remain_index_only_and_paths_are_unchanged():
    source = snapshot()
    source["figures"] = [{"id": "figure-1", "source_ref": "file:curve.svg", "path": "sources/files/curve.svg", "status": "unsafe_or_invalid_figure"}]
    source["source_index"]["file:curve.svg"] = {"kind": "figure", "id": "figure-1"}
    bundle = build_report_bundle(source)
    assert bundle["figures"][0]["path"] == source["figures"][0]["path"]
    assert bundle["source_index"]["file:curve.svg"]["visibility"] == "index_only"


def test_long_scientific_text_lists_and_parameter_vectors_are_preserved():
    result = {"estimate": 1.5, "counterexamples": [f"Counterexample {i}" for i in range(80)],
              "bootstrap_failures": [f"Failure reason {i}" for i in range(50)],
              "limitations": [f"Bounded limitation {i}" for i in range(60)],
              "coefficients": list(range(70)), "diagnostic_notes": [f"Named diagnostic {i}" for i in range(70)],
              "bootstrap_samples": list(range(2000))}
    bundle = build_report_bundle(snapshot(document("result.json", json.dumps(result))))
    projection = json.loads(bundle["documents"][0]["content"])
    for field in result:
        if field != "bootstrap_samples":
            assert projection[field] == result[field]
    assert projection["bootstrap_samples"]["_retrieval"] == "index_only_array"


@pytest.mark.parametrize("status", ["source_changed", "missing", "source_changed_at_freeze", "source_unavailable_at_freeze"])
def test_unavailable_documents_cannot_remain_visible_support(status):
    row = document("changed.json", '{"estimate":9.9}')
    row["status"] = status
    source = snapshot(row)
    bundle = build_report_bundle(source)
    assert "content" not in bundle["documents"][0]
    assert bundle["documents"][0]["visibility"] == "index_only"
    assert bundle["source_index"][row["source_ref"]]["visibility"] == "index_only"
    assert bundle["source_index"][row["source_ref"]]["status"] == status
    assert bundle["source_index"]["outcome-1"]["status"] == "recorded"


def test_exact_shared_scientific_structure_remains_resolvable_in_visible_material():
    detail = {f"parameter_{i}": {"estimate": i / 100, "scope": "Synthetic parameter"} for i in range(30)}
    source = snapshot(document("first.json", json.dumps({"title": "First recorded source", "details": detail})),
                      document("second.json", json.dumps({"title": "Second recorded source", "details": detail})))
    bundle = build_report_bundle(source)
    first = json.loads(bundle["documents"][0]["content"])
    second = json.loads(bundle["documents"][1]["content"])
    assert first["details"] == detail
    assert second["details"]["_retrieval"] == "duplicate_visible_structure"
    assert second["details"]["visible_source"].startswith(bundle["documents"][0]["material_id"])
    assert bundle["documents"][1]["visibility"] == "structured_projection"
