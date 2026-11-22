"""Public synthetic reports exercise references, figures and fresh-review contracts."""

from copy import deepcopy
import hashlib

from theory_master.report_synthesis.prompts import (
    PROMPT_VERSION, LEGACY_REPORT_SCHEMA, REPORT_SCHEMA, REVIEW_CRITERIA, REVIEW_SCHEMA,
    WRITER_PROMPT_VERSION, REVIEWER_PROMPT_VERSION,
    prompt_metadata, reviewer_prompt, writer_prompt,
)
from theory_master.report_synthesis.review import canonicalize_report_references, validate_report, validate_review
from jsonschema import Draft202012Validator


def example():
    snapshot = {
        "fingerprint": "synthetic-snapshot",
        "original_question": {"source_ref": "research_plan", "content": "Compare two public toy settings."},
        "hypotheses": [{"id": "H-0001", "claim": "The toy response differs by setting."}],
        "outcomes": [{"id": "O-0001", "hypothesis_id": "H-0001", "verdict": "supported"}],
        "figures": [{"id": "figure:abc", "source_ref": "document:toy.png",
                     "path": "assets/toy.png", "status": "available"}],
        "source_index": {key: {} for key in (
            "research_plan", "H-0001", "O-0001", "document:toy.png")},
    }
    report = {
        "schema_version": "2", "title": "Toy comparison", "abstract": "The recorded toy settings differ.",
        "markdown": "# Toy comparison\n\nThe recorded toy settings differ. [O-0001]\n\n"
                    "![Recorded toy response](assets/toy.png)\n\nFigure 1. Toy settings; source document:toy.png.",
        "claims": [{"id": "C-1", "text": "The recorded toy settings differ.", "kind": "answer",
                    "hypothesis_ids": ["H-0001"], "outcome_ids": ["O-0001"],
                    "source_refs": ["O-0001"], "scope": "The two recorded toy settings."}],
        "selected_hypothesis_ids": ["H-0001"],
        "figures": [{"id": "figure:abc", "source_ref": "document:toy.png", "path": "assets/toy.png",
                     "caption": "Recorded toy response by setting.", "claim_ids": ["C-1"],
                     "alt_text": "A response comparison between two toy settings."}],
        "requirements": [{"requirement_id": "Q-1", "question": "Do the toy settings differ?",
                          "answer": "They differ in the recorded comparison.", "claim_ids": ["C-1"],
                          "status": "answered"}],
        "research_path": [{"hypothesis_id": "H-0001", "role": "main",
                           "explanation": "The decisive toy comparison answers the question.",
                           "outcome_ids": ["O-0001"], "source_refs": ["O-0001"]}],
        "research_story": [{"title": "Compare the toy settings", "action": "Compare the two recorded responses.",
                            "finding": "The toy settings differ.", "transition": "This answers the question for these settings.",
                            "hypothesis_ids": ["H-0001"], "outcome_ids": ["O-0001"], "source_refs": ["O-0001"]}],
        "limitations": [{"text": "The comparison covers two toy settings.", "source_refs": ["H-0001"]}],
    }
    review = {
        "schema_version": "2", "verdict": "pass", "summary": "The report matches the toy records.",
        "dimensions": [{"criterion": criterion, "status": "pass",
                        "reason": "Compared the relevant toy question and recorded evidence.",
                        "source_refs": ["research_plan", "O-0001"]}
                       for criterion in REVIEW_CRITERIA],
        "issues": [],
    }
    return snapshot, report, review


def test_public_synthetic_report_and_review_validate():
    snapshot, report, review = example()
    Draft202012Validator.check_schema(REPORT_SCHEMA)
    Draft202012Validator.check_schema(LEGACY_REPORT_SCHEMA)
    Draft202012Validator.check_schema(REVIEW_SCHEMA)
    assert validate_report(report, snapshot) == []
    assert validate_review(review, report, snapshot) == []


def test_legacy_review_remains_readable_and_cannot_satisfy_active_review():
    snapshot, report, review = example()
    review["schema_version"] = "1"
    review["dimensions"] = [dimension for dimension in review["dimensions"]
                            if dimension["criterion"] != "reader_clarity"]
    assert validate_review(review, report, snapshot) == []
    assert validate_review(review, report, snapshot, allow_legacy=False)


def test_reviewer_can_locate_an_actually_supplied_host_diagram():
    snapshot, report, review = example()
    review["issues"] = [{"severity": "minor", "criterion": "figures_and_captions",
        "report_location": "derived_visuals[0].svg:status label", "problem": "State label needs a scope",
        "source_refs": ["O-0001"], "system_prompt_recommendation": "Label round and whole-H states separately"}]
    assert any("outside this report" in error for error in validate_review(review, report, snapshot))
    assert validate_review(review, report, snapshot, derived_visuals=[{"svg": "synthetic"}]) == []


def test_review_location_allows_a_readable_annotation_after_a_checked_index():
    snapshot, report, review = example()
    review["issues"] = [{"severity": "minor", "criterion": "evidence_and_scope",
        "report_location": "claims[0]（C-1）", "problem": "Wording needs a scope clarification",
        "source_refs": ["O-0001"], "system_prompt_recommendation": "Keep narrative and claim scope consistent"}]
    assert validate_review(review, report, snapshot) == []
    review["issues"][0]["report_location"] = "claims[9]（C-unknown）"
    assert any("outside this report" in error for error in validate_review(review, report, snapshot))


def test_review_location_resolves_actual_report_item_ids():
    snapshot, report, review = example()
    review["issues"] = [{"severity": "minor", "criterion": "answer_coverage",
        "report_location": "requirements[Q-1].answer；claims[C-1]", "problem": "Clarify the answer",
        "source_refs": ["O-0001"], "system_prompt_recommendation": "Put the answer first"}]
    assert validate_review(review, report, snapshot) == []
    review["issues"][0]["report_location"] = "requirements[Q-unknown].answer"
    assert any("identifier is absent" in error for error in validate_review(review, report, snapshot))


def test_prompt_receipts_hash_effective_schema_and_prose():
    metadata = prompt_metadata()
    assert set(metadata) == {"writer", "reviewer"}
    assert PROMPT_VERSION == WRITER_PROMPT_VERSION == "2026-11-20.11"
    assert REVIEWER_PROMPT_VERSION == "2026-11-20.7"
    versions = {"writer": WRITER_PROMPT_VERSION, "reviewer": REVIEWER_PROMPT_VERSION}
    for role, prompt in (("writer", writer_prompt()), ("reviewer", reviewer_prompt())):
        assert metadata[role] == {
            "version": versions[role], "sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        assert prompt_metadata(role) == metadata[role]
    assert "全新的模型上下文" in reviewer_prompt()
    assert "不自行修改任何提示词" in reviewer_prompt()
    assert all(criterion in reviewer_prompt() for criterion in REVIEW_CRITERIA)
    assert "research_story" in writer_prompt() and "3–7" in writer_prompt()
    assert "主线相邻表示叙述推进" in writer_prompt()
    assert "无须展示所有 selected_hypothesis_ids" in reviewer_prompt()


def test_unknown_claim_source_and_outcome_are_rejected():
    snapshot, report, _ = example()
    report["claims"][0].update(source_refs=["fabricated-file"], outcome_ids=["O-fiction"])
    errors = validate_report(report, snapshot)
    assert any("source_refs" in error and "fabricated-file" in error for error in errors)
    assert any("outcome_ids" in error and "O-fiction" in error for error in errors)


def test_markdown_citations_must_resolve_outside_code_math_and_image_embeds():
    snapshot, report, _ = example()
    report["markdown"] += "\nReal [[O-0001|readable source]], typo [[missing-key]]. `[[code]]` $[[math]]$"
    errors = validate_report(report, snapshot)
    assert errors == ["report.markdown: unknown reference 'missing-key'"]


def test_short_material_references_expand_to_exact_archived_keys():
    snapshot, report, _ = example()
    ref = "file:root-0/round-report.md"
    snapshot["source_index"][ref] = {"kind": "document", "material_id": "D-0008"}
    report["claims"][0]["source_refs"] = ["D-0008", ref]
    report["markdown"] += "\n[[D-0008|Round report]] `[[D-0008]]` $[[D-0008]]$"
    canonical = canonicalize_report_references(report, snapshot)
    assert canonical["claims"][0]["source_refs"] == [ref]
    assert canonical["markdown"].endswith(f"[[{ref}|Round report]] `[[D-0008]]` $[[D-0008]]$")
    assert validate_report(canonical, snapshot) == []
    assert report["claims"][0]["source_refs"] == ["D-0008", ref]


def test_index_only_sources_need_visible_record_or_projection_support():
    snapshot, report, _ = example()
    snapshot["source_index"]["O-0001"]["visibility"] = "index_only"
    assert any("index_only material" in error for error in validate_report(report, snapshot))
    snapshot["source_index"]["visible-result"] = {"visibility": "structured_projection"}
    report["claims"][0]["source_refs"].append("visible-result")
    report["research_story"][0]["source_refs"].append("visible-result")
    assert validate_report(report, snapshot) == []
    snapshot["source_index"]["visible-result"]["visibility"] = "full"
    assert validate_report(report, snapshot) == []


def test_selected_path_and_requirement_must_map_to_real_records():
    snapshot, report, _ = example()
    report["requirements"][0]["claim_ids"] = []
    report["research_path"] = []
    errors = validate_report(report, snapshot)
    assert any("answered requirements" in error for error in errors)
    assert any("has no path entry" in error for error in errors)


def test_stored_v1_reports_remain_valid_without_research_story():
    snapshot, report, review = example()
    report["schema_version"] = "1"
    del report["research_story"]
    assert validate_report(report, snapshot) == []
    assert validate_review(review, report, snapshot) == []
    report["schema_version"] = "2"
    assert any("research_story" in error for error in validate_report(report, snapshot))


def test_research_story_requires_actual_records_and_selected_hypotheses():
    snapshot, report, _ = example()
    stage = report["research_story"][0]
    stage.update(hypothesis_ids=["H-fiction"], outcome_ids=["O-fiction"], source_refs=["file-fiction"])
    errors = validate_report(report, snapshot)
    assert any("research_story[0].hypothesis_ids" in error and "H-fiction" in error for error in errors)
    assert any("research_story[0].outcome_ids" in error and "O-fiction" in error for error in errors)
    assert any("research_story[0].source_refs" in error and "file-fiction" in error for error in errors)
    snapshot["hypotheses"].append({"id": "H-0002"})
    stage.update(hypothesis_ids=["H-0002"], outcome_ids=[], source_refs=["O-0001"])
    assert any("must be selected" in error for error in validate_report(report, snapshot))


def test_research_story_rejects_index_only_support_and_mismatched_outcome_ownership():
    snapshot, report, _ = example()
    snapshot["source_index"]["indexed-script"] = {"visibility": "index_only"}
    stage = report["research_story"][0]
    stage["source_refs"] = ["indexed-script"]
    assert any("sole support for a research stage" in error for error in validate_report(report, snapshot))
    stage["source_refs"].append("O-0001")
    assert validate_report(report, snapshot) == []
    snapshot["hypotheses"].append({"id": "H-0002"})
    snapshot["outcomes"].append({"id": "O-0002", "hypothesis_id": "H-0002"})
    stage["outcome_ids"] = ["O-0002"]
    assert any("belongs to hypothesis 'H-0002' outside this stage" in error for error in validate_report(report, snapshot))


def test_research_story_can_merge_stages_and_omit_nonessential_selected_branches():
    snapshot, report, _ = example()
    snapshot["hypotheses"].append({"id": "H-0002"})
    snapshot["outcomes"].append({"id": "O-0002", "hypothesis_id": "H-0002"})
    snapshot["source_index"].update({"H-0002": {}, "O-0002": {}})
    report["selected_hypothesis_ids"].append("H-0002")
    report["research_path"].append({"hypothesis_id": "H-0002", "role": "supporting",
        "explanation": "A local check supports the same bounded answer.",
        "outcome_ids": ["O-0002"], "source_refs": ["O-0002"]})
    assert validate_report(report, snapshot) == []
    stage = report["research_story"][0]
    stage["hypothesis_ids"].append("H-0002")
    stage["outcome_ids"].append("O-0002")
    stage["source_refs"].append("O-0002")
    assert validate_report(report, snapshot) == []
    assert len(report["research_story"]) < len(report["selected_hypothesis_ids"])


def test_research_story_can_revisit_a_hypothesis_with_a_different_recorded_outcome():
    snapshot, report, _ = example()
    snapshot["outcomes"].append({"id": "O-0002", "hypothesis_id": "H-0001"})
    snapshot["source_index"]["O-0002"] = {}
    second = deepcopy(report["research_story"][0])
    second.update(title="Check the held-out toy setting", outcome_ids=["O-0002"], source_refs=["O-0002"])
    report["research_story"].append(second)
    assert validate_report(report, snapshot) == []


def test_reviewer_can_locate_research_story_stages():
    snapshot, report, review = example()
    review["issues"] = [{"severity": "minor", "criterion": "research_trajectory",
        "report_location": "research_story[0].transition", "problem": "The final transition needs a scope.",
        "source_refs": ["O-0001"], "system_prompt_recommendation": "Keep the final stage tied to the supported scope."}]
    assert validate_review(review, report, snapshot) == []
    review["issues"][0]["report_location"] = "research_story[1].transition"
    assert any("outside this report" in error for error in validate_review(review, report, snapshot))


def test_duplicate_claims_and_unknown_requirement_claims_are_rejected():
    snapshot, report, _ = example()
    report["claims"].append(deepcopy(report["claims"][0]))
    report["requirements"][0]["claim_ids"] = ["invented-claim"]
    errors = validate_report(report, snapshot)
    assert any("duplicate id" in error for error in errors)
    assert any("invented-claim" in error for error in errors)


def test_figures_must_match_snapshot_and_be_embedded():
    snapshot, report, _ = example()
    report["figures"][0]["path"] = "assets/invented.png"
    errors = validate_report(report, snapshot)
    assert any("supplied original/frozen" in error for error in errors)
    assert any("not embedded" in error for error in errors)
    assert any("not an approved" in error for error in errors)


def test_unlisted_markdown_or_inline_generated_images_are_rejected():
    snapshot, report, _ = example()
    report["markdown"] += '\n![fiction](https://example.invalid/fiction.png)\n<svg><path/></svg>'
    errors = validate_report(report, snapshot)
    assert any("fiction.png" in error for error in errors)
    assert any("inline generated graphics" in error for error in errors)


def test_html_image_requires_approved_source_but_code_examples_are_literal():
    snapshot, report, _ = example()
    report["markdown"] += '\n```html\n<img src="example.png"><svg></svg>\n```'
    assert validate_report(report, snapshot) == []
    report["markdown"] += '\n<img src=https://example.invalid/invented.png>'
    assert any("invented.png" in error for error in validate_report(report, snapshot))


def test_missing_figure_source_or_unavailable_asset_is_rejected():
    snapshot, report, _ = example()
    snapshot["figures"][0]["status"] = "missing"
    report["figures"][0]["source_ref"] = "O-0001"
    errors = validate_report(report, snapshot)
    assert any("does not identify this figure" in error for error in errors)
    assert any("unavailable" in error for error in errors)


def test_no_images_is_valid_and_does_not_require_a_fabricated_figure():
    snapshot, report, review = example()
    report["markdown"] = "# Toy comparison\n\nThe recorded settings differ. [O-0001]"
    report["figures"] = []
    snapshot["figures"] = []
    next(d for d in review["dimensions"] if d["criterion"] == "figures_and_captions")["status"] = "not_applicable"
    assert validate_report(report, snapshot) == []
    assert validate_review(review, report, snapshot) == []


def test_all_review_dimensions_are_required_once():
    snapshot, report, review = example()
    review["dimensions"][1] = deepcopy(review["dimensions"][0])
    errors = validate_review(review, report, snapshot)
    assert any("duplicate review dimension" in error for error in errors)
    assert any("missing criterion" in error for error in errors)


def test_pass_cannot_hide_major_or_failed_dimension():
    snapshot, report, review = example()
    review["dimensions"][1]["status"] = "fail"
    review["issues"] = [{"severity": "major", "criterion": "evidence_and_scope",
                         "report_location": "claims[0]", "problem": "The toy scope is incomplete.",
                         "source_refs": ["H-0001"],
                         "system_prompt_recommendation": "Require every numeric comparison to retain the original test conditions."}]
    errors = validate_review(review, report, snapshot)
    assert any("pass conflicts" in error for error in errors)
    review["verdict"] = "revise"
    assert validate_review(review, report, snapshot) == []


def test_reviewer_cannot_skip_dimensions_or_give_unlocated_empty_revisions():
    snapshot, report, review = example()
    review["verdict"] = "revise"
    review["dimensions"][0]["status"] = "not_applicable"
    errors = validate_review(review, report, snapshot)
    assert any("not_applicable" in error for error in errors)
    assert any("requires at least one" in error for error in errors)


def test_review_issue_requires_real_sources_and_report_location():
    snapshot, report, review = example()
    review["issues"] = [{"severity": "minor", "criterion": "answer_coverage",
                         "report_location": "claims[99]", "problem": "A local toy sentence needs a reference.",
                         "source_refs": ["fabricated-source"],
                         "system_prompt_recommendation": "Keep source references adjacent to each substantive answer."}]
    errors = validate_review(review, report, snapshot)
    assert any("fabricated-source" in error for error in errors)
    assert any("outside this report" in error for error in errors)


def test_schema_failures_are_returned_without_crashing():
    snapshot, report, _ = example()
    assert validate_report(None, snapshot)
    assert validate_report(report, None)
    assert validate_report(report, {"source_index": []})
    assert validate_review({}, report, snapshot)
    assert validate_review(example()[2], {"title": "incomplete"}, snapshot)
