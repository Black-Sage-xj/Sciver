"""Cycle-level reading projections preserve frozen scientific provenance."""
from copy import deepcopy

from theory_master.report_synthesis.reading import cycle_reading


def snapshot():
    return {
        "sources": {"research_root": "/public-synthetic/research"},
        "hypotheses": [{"id": "H-9000"}, {"id": "H-0002"}],
        "outcomes": [
            {"id": "O-two", "source_ref": "O-two", "cycle_id": "2", "hypothesis_id": "H-9000", "report_ref": "notes/two.md"},
            {"id": "O-nine", "source_ref": "O-nine", "cycle_id": "9", "hypothesis_id": "H-9000", "report_ref": "notes/nine.md"},
        ],
        "preservations": [{"id": "kept-four", "cycle_id": "4", "hypothesis_id": "H-preserved"}],
        "evidence_records": [{"evidence_id": "E-7-misleading-number", "source_ref": "E-7-misleading-number", "cycle_id": "2",
                              "manifest_source_ref": "file:two-manifest"}],
        "documents": [
            {"id": "file:round-two", "source_ref": "file:round-two", "reference": "notes/two", "source_path": "/public-vault/notes/two.md",
             "frozen_path": "files/two.md", "kind": "document", "status": "available"},
            {"id": "file:round-nine", "source_ref": "file:round-nine", "reference": "notes/nine.md", "source_path": "/public-vault/notes/nine.md",
             "frozen_path": "files/nine.md", "kind": "document", "status": "available"},
            {"id": "file:result-two", "source_ref": "file:result-two", "reference": "result-two.json",
             "source_path": "/public-synthetic/research/2/workspace/result.json", "frozen_path": "files/result-two.json", "kind": "document"},
        ],
        "source_index": {
            "notes/two.md": {"kind": "document", "id": "file:round-two"},
            "notes/two": {"kind": "document", "id": "file:round-two"},
            "file:round-two": {"kind": "document", "id": "file:round-two"},
            "notes/nine.md": {"kind": "document", "id": "file:round-nine"},
            "file:round-nine": {"kind": "document", "id": "file:round-nine"},
            "file:result-two": {"kind": "document", "id": "file:result-two"},
            "research_plan": {"kind": "research_plan", "id": "research_plan"},
        },
    }


def test_cycle_entries_use_actual_round_records_and_frozen_round_reports():
    data = snapshot()
    report = {"markdown": "Recorded result. [[O-two]][[E-7-misleading-number]][[file:result-two]][[notes/two]]"}
    before = deepcopy(report), deepcopy(data)
    reading = cycle_reading(report, data)
    assert list(reading["cycles"]) == ["cycle:2", "cycle:4", "cycle:9"]
    assert reading["markdown"] == "Recorded result. 〔[[cycle:2|Cycle 2]]〕"
    assert reading["cycles"]["cycle:2"] == {
        "kind": "cycle", "id": "2", "cycle_id": "2", "title": "Cycle 2",
        "source_ref": "file:round-two", "outcome_ids": ["O-two"],
    }
    assert before == (report, data)


def test_document_aliases_inherit_round_membership_in_both_directions():
    reading = cycle_reading({}, snapshot())
    for reference in ("notes/two.md", "notes/two", "file:round-two", "/public-vault/notes/two.md"):
        assert reading["ref_cycles"][reference] == ["2"]


def test_hypotheses_join_recorded_outcomes_and_never_supply_cycle_numbers():
    reading = cycle_reading({"markdown": "A comparison. [[H-9000]][[H-0002]][[E-7-misleading-number]]"}, snapshot())
    assert reading["ref_cycles"]["H-9000"] == ["2", "9"]
    assert reading["ref_cycles"]["E-7-misleading-number"] == ["2"]
    assert "H-0002" not in reading["ref_cycles"]
    assert reading["markdown"] == "A comparison. 〔[[cycle:2|Cycle 2]]、[[cycle:9|9]]〕"
    assert "H-0002" in reading["omitted_refs"]


def test_ambiguous_file_aliases_do_not_merge_otherwise_distinct_cycles():
    data = snapshot()
    data["documents"][-1]["reference"] = "result.json"
    data["documents"].append({"id": "file:result-nine", "source_ref": "file:result-nine", "reference": "result.json",
        "source_path": "/public-synthetic/research/9/workspace/result.json", "frozen_path": "files/result-nine.json"})
    data["source_index"]["result.json"] = {"kind": "document", "id": "file:result-two"}
    reading = cycle_reading({"markdown": "A result. [[result.json]]"}, data)
    assert "result.json" not in reading["ref_cycles"]
    assert reading["ref_cycles"]["file:result-two"] == ["2"]
    assert reading["ref_cycles"]["file:result-nine"] == ["9"]
    assert reading["markdown"] == "A result. "


def test_only_known_bound_cycle_paths_or_exact_recorded_references_assign_files():
    data = snapshot()
    data["documents"].extend([
        {"id": "file:unknown-cycle", "source_path": "/public-synthetic/research/77/workspace/result.json"},
        {"id": "file:outside-root", "source_path": "/other-task/2/workspace/result.json"},
        {"id": "file:captured-workspace", "source_path": "/captured/public-workspace/analysis/result.json"},
        {"id": "file:captured-manifest", "source_path": "/captured/public-workspace/manifest.json"},
    ])
    data["evidence_records"].append({"evidence_id": "E-public", "cycle_id": "9",
        "execution_materials": {"workspace": "/captured/public-workspace", "manifest_path": "/captured/public-workspace/manifest.json"}})
    reading = cycle_reading({}, data)
    assert "file:unknown-cycle" not in reading["ref_cycles"]
    assert "file:outside-root" not in reading["ref_cycles"]
    assert "file:captured-workspace" not in reading["ref_cycles"]
    assert reading["ref_cycles"]["file:captured-manifest"] == ["9"]
    assert "cycle:77" not in reading["cycles"]


def test_preserved_rounds_have_their_own_cycle_without_becoming_outcomes():
    reading = cycle_reading({"markdown": "A retained record. [[kept-four]]"}, snapshot())
    assert reading["markdown"] == "A retained record. 〔[[cycle:4|Cycle 4]]〕"
    assert reading["cycles"]["cycle:4"]["outcome_ids"] == []
    assert reading["cycles"]["cycle:4"]["source_ref"] is None


def test_goal_sources_link_to_their_recorded_rounds():
    data = snapshot()
    data["goal_terminal"] = {"source_ref": "goal:terminal", "source_cycle": "9"}
    data["goal_gate_history"] = [{"source_ref": "goal:assessment:1", "cycle_id": "2"}]
    reading = cycle_reading({"markdown": "Earlier assessment [[goal:assessment:1]]. Goal passed [[goal:terminal]]."}, data)
    assert reading["ref_cycles"]["goal:terminal"] == ["9"]
    assert reading["ref_cycles"]["goal:assessment:1"] == ["2"]


def test_code_math_and_images_are_literal_while_internal_citations_are_compact():
    markdown = '''A result is $x=2$. [[O-two]][[file:result-two]]

`[[O-two]]` and `![[figure.png|200]]` are literal examples.

```python
value = "[[O-two]]"
```

$$ y = [[not-a-citation]] + x_1 $$

![Recorded curve](images/curve.png)
![[images/curve.svg|200]]

The original question remains described in prose. [[research_plan]]
'''
    reading = cycle_reading({"markdown": markdown}, snapshot())
    assert "A result is $x=2$. 〔[[cycle:2|Cycle 2]]〕" in reading["markdown"]
    assert '`[[O-two]]`' in reading["markdown"]
    assert 'value = "[[O-two]]"' in reading["markdown"]
    assert "$$ y = [[not-a-citation]] + x_1 $$" in reading["markdown"]
    assert "![Recorded curve](images/curve.png)" in reading["markdown"]
    assert "![[images/curve.svg|200]]" in reading["markdown"]
    assert "[[research_plan]]" not in reading["markdown"]
    assert reading["omitted_refs"] == ["research_plan"]


def test_only_a_strict_document_catalog_is_removed_and_scientific_boundaries_remain():
    markdown = '''## Execution and sources

The accepted technical recovery exceeded the original cost budget. [[O-nine]]

复现入口按研究问题组织如下；本报告综合已记录材料：

- Recorded comparison material. [[file:result-two]]
- Later round material. [[file:round-nine]]

The discarded analysis and identity boundary still matter. [[O-nine]]
'''
    reading = cycle_reading({"markdown": markdown}, snapshot())
    assert reading["removed_catalog_blocks"] == 1
    assert "Recorded comparison material" not in reading["markdown"]
    assert "Execution and sources" in reading["markdown"]
    assert "exceeded the original cost budget" in reading["markdown"]
    assert "discarded analysis and identity boundary" in reading["markdown"]


def test_code_catalog_examples_and_non_catalog_scientific_lists_are_preserved():
    markdown = '''```markdown
复现入口按研究问题组织如下：
- Example file. [[file:result-two]]
```

- A decisive rejected comparison. [[O-nine]]

复现入口按研究问题组织如下：
- A record without a file citation. [[O-two]]
'''
    reading = cycle_reading({"markdown": markdown}, snapshot())
    assert reading["removed_catalog_blocks"] == 0
    assert "- Example file. [[file:result-two]]" in reading["markdown"]
    assert "- A decisive rejected comparison." in reading["markdown"]
    assert "- A record without a file citation." in reading["markdown"]


def test_multiple_registered_round_reports_do_not_choose_an_arbitrary_document():
    data = snapshot()
    data["outcomes"].append({"id": "O-two-again", "cycle_id": "2", "hypothesis_id": "H-9000", "report_ref": "notes/other-two.md"})
    data["documents"].append({"id": "file:other-two", "reference": "notes/other-two.md", "frozen_path": "files/other-two.md"})
    reading = cycle_reading({}, data)
    assert reading["cycles"]["cycle:2"]["source_ref"] is None
    assert reading["cycles"]["cycle:2"]["outcome_ids"] == ["O-two", "O-two-again"]


def test_missing_markdown_or_cycle_records_never_infer_a_cycle():
    assert cycle_reading({"research_story": []}, {})["markdown"] == ""
    reading = cycle_reading({"markdown": "Scoped answer. [[H-0001]]"}, {"hypotheses": [{"id": "H-0001"}],
        "outcomes": [{"id": "O-one", "hypothesis_id": "H-0001"}]})
    assert reading["cycles"] == {}
    assert reading["markdown"] == "Scoped answer. "


def test_already_compact_cycle_citations_are_idempotent():
    data = snapshot()
    once = cycle_reading({"markdown": "A joint result. [[O-two]][[O-nine]]"}, data)["markdown"]
    assert cycle_reading({"markdown": once}, data)["markdown"] == once


def test_repeated_cycles_are_deduplicated_only_within_the_same_paragraph():
    markdown = ("First result. [[O-two]] Same Cycle again. [[file:result-two]] "
                "Another comparison. [[O-two]][[O-nine]]\n\n"
                "A separate scientific paragraph. [[O-two]]")
    reading = cycle_reading({"markdown": markdown}, snapshot())["markdown"]
    assert reading.count("[[cycle:2|Cycle 2]]") == 2
    assert reading.count("[[cycle:9|Cycle 9]]") == 1
    assert "[[cycle:9|9]]" not in reading
    assert "First result." in reading and "Same Cycle again." in reading and "Another comparison." in reading


def test_list_items_and_table_rows_keep_independent_cycle_citations():
    markdown = ("- First result. [[O-two]]\n- Second result. [[O-two]]\n\n"
                "| Setting | Result |\n| --- | --- |\n| A | One. [[O-two]] |\n| B | Two. [[O-two]] |")
    reading = cycle_reading({"markdown": markdown}, snapshot())["markdown"]
    assert reading.count("[[cycle:2|Cycle 2]]") == 4


def test_reproduction_sections_fold_without_dropping_scientific_or_execution_content():
    markdown = '''## Main result

A supported scoped result. [[O-two]]

## 执行修正、复现材料与来源

The accepted recovery exceeded the original cost budget. [[O-nine]]

The discarded analysis and identity limitations remain recorded. [[O-nine]]

## Discussion

This ordinary section remains expanded. [[O-two]]
'''
    reading = cycle_reading({"markdown": markdown}, snapshot())
    assert reading["folded_material_sections"] == 1
    text = reading["markdown"]
    assert text.count('<details class="report-materials">') == 1
    assert text.index("## 执行修正、复现材料与来源") < text.index('<details class="report-materials">')
    assert text.index("exceeded the original cost budget") < text.index("</details>")
    assert text.index("discarded analysis and identity limitations") < text.index("</details>")
    assert text.index("</details>") < text.index("## Discussion")
    assert cycle_reading({"markdown": text}, snapshot())["markdown"] == text


def test_reproduction_headings_inside_code_are_not_folded():
    markdown = '''```markdown
## 复现材料与来源
This is only a code example. [[O-two]]
```

## Final answer

The actual answer remains visible. [[O-two]]
'''
    reading = cycle_reading({"markdown": markdown}, snapshot())
    assert reading["folded_material_sections"] == 0
    assert '<details class="report-materials">' not in reading["markdown"]
    assert "This is only a code example. [[O-two]]" in reading["markdown"]
