"""Guard the public architecture docs against reintroducing a Canvas data layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_current_graph_docs_make_sqlite_authoritative_and_canvas_projection_only():
    canvas_doc = (ROOT / "design" / "Canvas设计文档.md").read_text(encoding="utf-8")
    graph_doc = (ROOT / "design" / "ResearchGraph设计文档.md").read_text(encoding="utf-8")
    sdk_doc = (ROOT / "design" / "SDK设计文档.md").read_text(encoding="utf-8")
    historical = (ROOT / "design" / "Canvas设计文档-历史V1.md").read_text(encoding="utf-8")
    assert "science_graph.sqlite3" in canvas_doc
    assert "可视化投影" in canvas_doc
    assert "Runtime data authority" in canvas_doc and "Runtime Agents do not read or write it" in canvas_doc
    assert "cycle_evaluations" in graph_doc
    assert "hypothesis_cycle_owners" in graph_doc
    assert "frontier_branches" in graph_doc
    assert "PHASE_CONTRACTS" in sdk_doc
    assert "运行时不提供历史合同查询" in sdk_doc or "历史合同查询" in sdk_doc
    assert "历史 V1 规则归档" in historical
    assert len(historical) > 20000


def test_agent_identities_and_packaged_skills_have_no_canvas_reader_or_writer_skill():
    name = "identities/_research_lead_reading.md"
    packaged = (ROOT / "theory_master" / "resources" / name).read_text(encoding="utf-8")
    authored = (ROOT / name).read_text(encoding="utf-8")
    assert packaged == authored, name
    assert (ROOT / "theory_master" / "resources" / "skills" / "report-writing" / "SKILL.md").is_file()
    assert not (ROOT / "skills").exists()
    identity_dir = ROOT / "identities"
    resource_identity_dir = ROOT / "theory_master" / "resources" / "identities"
    assert not (identity_dir / "_canvas_read.md").exists()
    assert not (identity_dir / "_canvas_renderer_policy.md").exists()
    assert not (resource_identity_dir / "_canvas_read.md").exists()
    assert not (resource_identity_dir / "_canvas_renderer_policy.md").exists()
    assert not (ROOT / "skills" / "canvas-maintenance" / "SKILL.md").exists()
    assert not (ROOT / "theory_master" / "resources" / "skills" / "canvas-maintenance" / "SKILL.md").exists()


def test_primary_entrypoints_link_the_current_sqlite_graph_model():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plan = (ROOT / "design" / "ResearchPlan设计文档.md").read_text(encoding="utf-8")
    research = (ROOT / "design" / "ResearchGraph设计文档.md").read_text(encoding="utf-8")
    sdk = (ROOT / "design" / "SDK设计文档.md").read_text(encoding="utf-8")
    assert "历史宽泛 H 可以关联多个" in readme
    assert "每个已裁决 Cycle 唯一 Outcome" in plan
    assert "Canvas设计文档-历史V1.md" in research
    assert "当前 Canvas 权威" in sdk or "SQLite 是唯一科研数据权威层" in sdk
