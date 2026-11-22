"""Linear report narratives preserve authored order, pivots and provenance."""
from copy import deepcopy
from html import unescape
import json
import re
import xml.etree.ElementTree as ET

from theory_master.report_synthesis.render import render_report
from theory_master.report_synthesis.story import STORY_WIDTH, _wrap, research_story, story_svg


def stage(title="Question", **updates):
    return {"title": title, "action": "Compare recorded measurements", "finding": "The comparison answers the scoped question",
            "transition": "Use the result in the final answer", "hypothesis_ids": ["H-a"], "outcome_ids": ["O-a"],
            "source_refs": ["O-a"], **updates}


def test_explicit_stages_can_merge_nodes_and_keep_authored_narrative_order():
    stages = [stage("Locate the discrepancy", hypothesis_ids=["H-z", "H-b"], outcome_ids=["O-z", "O-b"], source_refs=["O-z", "O-b"]),
              stage("Bound the answer", hypothesis_ids=["H-a"])]
    report = {"research_story": stages}
    snapshot = {"hypotheses": [{"id": "H-a"}, {"id": "H-b"}, {"id": "H-z"}], "lineage": []}
    before = deepcopy(report), deepcopy(snapshot)
    assert research_story(report, snapshot) == stages
    root = ET.fromstring(story_svg(report, snapshot))
    assert root.attrib["width"] == str(STORY_WIDTH)
    assert [group.attrib["data-story-stage"] for group in root.findall("{*}g")] == ["1", "2"]
    assert "Locate the discrepancy" in "".join(root.itertext())
    assert before == (report, snapshot)


def test_legacy_story_keeps_pivotal_failed_ancestor_and_omits_exploratory_branches():
    ids = ["H-start", "H-dead", "H-model", "H-extra", "H-future", "H-rejected", "H-answer"]
    roles = ["main", "negative_result", "turning_point", "supporting", "turning_point", "negative_result", "main"]
    report = {"research_path": [{"hypothesis_id": ident, "role": role, "explanation": "Recorded explanation for " + ident,
                                  "outcome_ids": ["O-" + ident], "source_refs": ["O-" + ident]}
                                 for ident, role in zip(ids, roles)]}
    snapshot = {"hypotheses": [{"id": ident, "title": "Title " + ident} for ident in reversed(ids)], "lineage": [
        {"source_hypothesis": "H-start", "target_hypothesis": "H-dead", "role": "main"},
        {"source_hypothesis": "H-start", "target_hypothesis": "H-model", "role": "main"},
        {"source_hypothesis": "H-model", "target_hypothesis": "H-extra", "role": "main"},
        {"source_hypothesis": "H-model", "target_hypothesis": "H-future", "role": "main"},
        {"source_hypothesis": "H-extra", "target_hypothesis": "H-future", "role": "other"},
        {"source_hypothesis": "H-start", "target_hypothesis": "H-rejected", "role": "main"},
        {"source_hypothesis": "H-rejected", "target_hypothesis": "H-answer", "role": "main"}]}
    actual = research_story(report, snapshot)
    assert [row["hypothesis_ids"][0] for row in actual] == ["H-start", "H-model", "H-future", "H-rejected", "H-answer"]
    assert actual[-2]["finding"] == "Recorded explanation for H-rejected"
    assert actual[-2]["title"].startswith("检验：")
    assert actual[-2]["source_refs"] == ["O-H-rejected"]
    assert all(row["action"] == row["transition"] == "" for row in actual)


def test_legacy_main_ancestor_cycles_terminate_without_synthesizing_missing_stages():
    report = {"research_path": [{"hypothesis_id": "H-final", "role": "main", "explanation": "Final"},
                               {"hypothesis_id": "H-pivot", "role": "negative_result", "explanation": "Pivot"}]}
    snapshot = {"lineage": [
        {"source_hypothesis": "H-pivot", "target_hypothesis": "H-final", "role": "main"},
        {"source_hypothesis": "H-final", "target_hypothesis": "H-pivot", "role": "main"},
        {"source_hypothesis": "H-not-authored", "target_hypothesis": "H-pivot", "role": "main"}]}
    assert [row["hypothesis_ids"][0] for row in research_story(report, snapshot)] == ["H-final", "H-pivot"]


def test_without_core_roles_keep_the_authors_existing_order_and_never_guess_from_ids():
    report = {"research_path": [{"hypothesis_id": "H-z", "role": "negative_result", "explanation": "First"},
                               {"hypothesis_id": "H-a", "role": "supporting", "explanation": "Second"}]}
    assert [row["hypothesis_ids"][0] for row in research_story(report, {})] == ["H-z", "H-a"]
    assert story_svg({"selected_hypothesis_ids": ["H-a"]}, {"hypotheses": [{"id": "H-a"}]}) == ""
    assert story_svg({"research_story": [], **report}, {}) == ""


def test_full_chinese_and_escaped_text_fit_the_column_without_truncation():
    title = "条件变化与模型比较" * 8
    finding = "已记录的关键转向需要保留完整的解释。" * 12 + ' <script>&"e\u0301'
    report = {"research_story": [stage(title, finding=finding, source_refs=["file:public/toy<&>.json"])]}
    svg = story_svg(report, {})
    root = ET.fromstring(svg)
    text = "".join(element.text or "" for element in root.findall(".//{*}text"))
    assert title in text and finding in text
    assert not root.findall(".//{*}script")
    assert "file:public/toy<&>.json" in "".join(root.itertext())
    assert "…" not in svg
    assert "".join(_wrap(finding, 46)) == finding
    for item in root.findall(".//{*}text"):
        assert float(item.attrib["x"]) == 34
    group = root.find("{*}g")
    box = group.find("{*}rect")
    bottom = float(box.attrib["y"]) + float(box.attrib["height"])
    assert max(float(item.attrib["y"]) for item in group.findall("{*}text")) < bottom


def test_svg_has_only_linear_narrative_arrows_regardless_of_graph_reference_edges():
    report = {"research_story": [stage("First"), stage("Second"), stage("Answer")]}
    snapshot = {"lineage": [{"source_hypothesis": "H-a", "target_hypothesis": "H-b", "role": "other"}] * 5}
    root = ET.fromstring(story_svg(report, snapshot))
    connectors = root.findall("{*}path")
    assert len(connectors) == 2
    assert {item.attrib["data-kind"] for item in connectors} == {"narrative-step"}
    assert "箭头表示叙述推进" in "".join(root.itertext())
    assert "stroke-dasharray" not in story_svg(report, snapshot)


def test_visible_story_sources_are_cycles_while_granular_provenance_stays_metadata():
    report = {"research_story": [stage("Bound the toy answer", hypothesis_ids=["H-a"],
                outcome_ids=["O-nine"], source_refs=["O-nine", "document:toy-analysis"])]}
    snapshot = {"outcomes": [
        {"id": "O-first", "source_ref": "O-first", "cycle_id": "1", "hypothesis_id": "H-a"},
        {"id": "O-nine", "source_ref": "O-nine", "cycle_id": "9", "hypothesis_id": "H-a"}],
        "source_index": {"H-a": {"kind": "hypothesis", "id": "H-a"},
                         "O-first": {"kind": "outcome", "id": "O-first"},
                         "O-nine": {"kind": "outcome", "id": "O-nine"}}}
    before = deepcopy(report), deepcopy(snapshot)
    root = ET.fromstring(story_svg(report, snapshot))
    visible = "".join(item.text or "" for item in root.findall(".//{*}text"))
    assert "Cycle 1" in visible and "Cycle 9" in visible
    assert "O-nine" not in visible and "H-a" not in visible and "document:toy-analysis" not in visible
    assert "document:toy-analysis" in "".join(root.find("{*}g").find("{*}metadata").itertext())
    assert before == (report, snapshot)


def test_offline_reading_lists_each_cycle_once_and_preserves_original_documents(tmp_path):
    report = {"title": "Toy comparison", "markdown": "# Toy comparison\n\nA scoped result. [[O-one]][[file:helper]]\n\nA later check. [[O-nine]]",
              "research_story": [stage("Answer the toy question", outcome_ids=["O-one"], source_refs=["O-one"])]}
    snapshot = {"fingerprint": "public-toy", "sources": {"research_root": "/public/research"},
        "hypotheses": [{"id": "H-a"}],
        "outcomes": [
            {"id": "O-one", "source_ref": "O-one", "cycle_id": "1", "hypothesis_id": "H-a", "report_ref": "notes/outcomes/1.md"},
            {"id": "O-nine", "source_ref": "O-nine", "cycle_id": "9", "hypothesis_id": "H-a", "report_ref": "notes/outcomes/9.md"}],
        "documents": [
            {"id": "file:round-one", "source_ref": "file:round-one", "reference": "notes/outcomes/1.md", "frozen_path": "files/round-one.md"},
            {"id": "file:round-nine", "source_ref": "file:round-nine", "reference": "notes/outcomes/9.md", "frozen_path": "files/round-nine.md"},
            {"id": "file:helper", "source_ref": "file:helper", "source_path": "/public/research/1/workspace/helper.json"}],
        "source_index": {"O-one": {"kind": "outcome", "id": "O-one"},
            "O-nine": {"kind": "outcome", "id": "O-nine"},
            "notes/outcomes/1.md": {"kind": "document", "id": "file:round-one"},
            "notes/outcomes/9.md": {"kind": "document", "id": "file:round-nine"},
            **{f"file:helper-alias-{i}": {"kind": "document", "id": "file:helper"} for i in range(80)},
            "file:helper": {"kind": "document", "id": "file:helper"}}}
    (tmp_path / "report.md").write_text(report["markdown"])
    (tmp_path / "report.json").write_text(json.dumps(report))
    original = (tmp_path / "report.md").read_bytes(), (tmp_path / "report.json").read_bytes()
    inputs = deepcopy(report), deepcopy(snapshot)
    rendered = render_report(report, snapshot, tmp_path, {"state": "ready"}).read_text()
    data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', rendered, re.S).group(1))
    assert set(data["sources"]) == {"cycle:1", "cycle:9"}
    assert data["source_labels"] == {"cycle:1": "Cycle 1", "cycle:9": "Cycle 9"}
    assert data["markdown"].count("cycle:1") == 1
    assert data["markdown"].count("cycle:9") == 1
    assert rendered.count('<li id="src-') == 2
    assert '<summary>研究轮次</summary>' in rendered
    assert 'href="sources/files/round-one.md">Cycle 1</a>' in rendered
    assert 'href="sources/files/round-nine.md">Cycle 9</a>' in rendered
    assert "source_numbers" not in rendered
    assert "冻结来源指纹" not in rendered and "完整来源快照" not in rendered
    assert (tmp_path / "report.md").read_bytes() == original[0]
    assert (tmp_path / "report.json").read_bytes() == original[1]
    assert inputs == (report, snapshot)


def test_mixed_language_wrapping_preserves_chemical_names_and_record_ids():
    original = "关键结果来自Ethanol与H-0004的比较"
    lines = _wrap(original, 16)
    assert "".join(lines) == original
    assert any("Ethanol" in line for line in lines)
    assert any("H-0004" in line for line in lines)
    assert "".join(_wrap("x" * 80, 16)) == "x" * 80


def test_offline_cycle_without_frozen_document_includes_its_preservation_record(tmp_path):
    preservation = {"id": "preservation:4", "source_ref": "preservation:4", "cycle_id": "4",
                    "reason": "The measured boundary requires a narrower follow-up."}
    report = {"title": "Toy preserved boundary", "markdown": "A retained boundary. [[preservation:4]]"}
    snapshot = {"preservations": [preservation], "outcomes": [
        {"id": "O-other", "cycle_id": "9", "reason": "A separate round result."}]}
    original = deepcopy(report), deepcopy(snapshot)
    rendered = render_report(report, snapshot, tmp_path, {"state": "ready"}).read_text()
    match = re.search(r'<details><summary>Cycle 4</summary><pre>(.*?)</pre></details>', rendered, re.S)
    assert match is not None
    assert json.loads(unescape(match.group(1))) == [preservation]
    assert original == (report, snapshot)


def test_offline_story_refresh_preserves_a_previous_frozen_trajectory(tmp_path):
    original = b"frozen historical trajectory"
    (tmp_path / "trajectory.svg").write_bytes(original)
    report = {"title": "Toy report", "markdown": "# Toy report\n\nA scoped recorded answer.",
              "research_story": [stage("Answer the toy question")]}
    path = render_report(report, {"fingerprint": "public-toy", "source_index": {}}, tmp_path, {"state": "ready"})
    assert (tmp_path / "trajectory.svg").read_bytes() == original
    assert (tmp_path / "research-story.svg").is_file()
    assert "研究主线概览" in path.read_text() and 'src="research-story.svg"' in path.read_text()
