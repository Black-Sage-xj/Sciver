"""Deterministic regression coverage for read-only human discussion overlays."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from theory_master.operator_direction import format_requested_conjecture
from theory_master.science_graph_store import ScienceGraphStore
from tm_observer.canvas_md import build_index, load_canvas
from tm_observer.external_graph_context import _human_title, external_graph_context


def _external(store, ident, hypothesis, *, test="不改写 H-0001，也不对它进行裁决。", refs=None, cycle=None):
    marker = format_requested_conjecture(
        external_id=ident, hypothesis=hypothesis, decisive_test=test,
        reason="提供可追溯的方法设计", source_session="discussion-session-1",
    )
    content = "## 用户提出的研究方向\n\n**猜想**\n\n> " + hypothesis + "\n\n" + marker
    store.db.execute(
        "INSERT INTO external_records(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
        (ident, "ResearchFocus", "human", "Human research conjecture", content, "2026-11-19T10:30:00+00:00"),
    )
    store._event("external.human_conjecture.pending", ident, {"source_references": refs or []})
    if cycle:
        store._event("external.human_conjecture.claimed", ident, {"cycle_id": cycle, "source_references": refs or []})
        store._event("external.human_conjecture.consumed", ident, {
            "cycle_id": cycle, "source_references": refs or [], "meaning": "delivered_to_proposal_only",
        })


def _database(root: Path):
    (root / "notes").mkdir(exist_ok=True)
    return ScienceGraphStore(root / "science_graph.sqlite3")


def test_human_inputs_have_stable_numbers_real_titles_and_full_fields(tmp_path):
    first, second = "human-conjecture-" + "f" * 20, "human-conjecture-" + "a" * 20
    proposition = "设计同一个周期信号的相位扫描方案。\n保持各通道输入及采样不变。"
    with _database(tmp_path) as store:
        store.add_hypothesis("H-0001")
        _external(store, first, proposition)
        _external(store, second, "后续方向")
    database = tmp_path / "science_graph.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    index = build_index(tmp_path)
    nodes = {n["id"]: n for n in index["nodes"]}
    ext = nodes[first]["semantic"]["external"]
    assert ext["display_id"] == "E1"
    assert nodes[second]["semantic"]["external"]["display_id"] == "E2"
    assert ext["title"] == "设计同一个周期信号的相位扫描方案"
    assert ext["hypothesis"] == proposition
    assert ext["decisive_test"] == "不改写 H-0001，也不对它进行裁决。"
    assert ext["source_session"] == "discussion-session-1"
    assert ext["delivery_status"] == "pending"
    assert nodes[first]["semantic"]["timestamp"] == ext["created_at"]
    assert nodes[first]["semantic"]["cycle"] is None
    assert "\n\n## 用户提出" not in nodes[first]["text"]
    assert not index["edges"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_human_titles_extract_the_named_deliverable_without_context_prefixes():
    assert _human_title("在当前基准上，设计一套供审阅的频率扫描对照：固定参数，仅改变频率。") == "频率扫描对照：固定参数"
    assert _human_title("可以仅凭可追溯原文、解析构造和算法成本推导，设计一套供审阅的频率扫描对照：固定参数，仅改变频率。") == "频率扫描对照：固定参数"
    assert _human_title("沿 H-0002 的研究目标，可先交付一份可审阅的对照方案：只调整同一个周期信号的相位，同时保持参数。") == "对照方案：只调整同一个周期信号的相位"
    assert len(_human_title("这里没有可抽取的交付物名称" * 10)) <= 48


def test_delivered_direction_and_explicit_target_are_distinct_context_links(tmp_path):
    first, second = "human-conjecture-" + "b" * 20, "human-conjecture-" + "d" * 20
    with _database(tmp_path) as store:
        for index in (1, 2, 3):
            store.add_hypothesis(f"H-{index:04d}")
        for cycle in (2, 3):
            store.db.execute("INSERT INTO hypothesis_cycle_owners VALUES (?,?,?)", (str(cycle), f"H-{cycle:04d}", "stamp"))
        _external(store, first, "交付可审阅的方法设计方案。", cycle="2")
        _external(store, second, "沿 H-0002 的研究目标，设计旋转对照方案。", cycle="3")
    graph = load_canvas(tmp_path)
    links = {(e["fromNode"], e["toNode"], e["relation"]): e for e in graph["edges"]}
    assert set(links) == {
        ("H-0002", first, "proposal_delivery"),
        ("H-0003", second, "proposal_delivery"),
        ("H-0002", second, "body_mention"),
    }
    delivered = links[("H-0003", second, "proposal_delivery")]
    assert delivered["label"] == "本轮讨论输入"
    assert delivered["projection_only"] is True
    assert delivered["display_primary"] is True
    assert delivered["provenance"]["meaning"] == "delivered_to_proposal_only"
    assert "不代表" in delivered["reason"]
    assert json.loads(delivered["provenance_json"]) == delivered["provenance"]
    assert links[("H-0002", second, "body_mention")]["display_primary"] is False
    with ScienceGraphStore(tmp_path / "science_graph.sqlite3", readonly=True) as store:
        assert not store.graph_view()["external_edges"]
        assert not store.graph_view()["edges"]


def test_exact_source_snapshot_and_document_ownership_take_precedence(tmp_path):
    ident = "human-conjecture-" + "a1" * 10
    ref = {"kind": "doc", "id": "notes/hypotheses/H-0001.md", "source_version": "a" * 64,
           "quote": "引用正文提到 H-0002，但来源本身属于 H-0001。"}
    with _database(tmp_path) as store:
        store.add_hypothesis("H-0001", dossier_ref="notes/hypotheses/H-0001")
        store.add_hypothesis("H-0002")
        _external(store, ident, "沿 H-0001 的研究目标，提供方案。", refs=[ref], cycle="99")
    graph = load_canvas(tmp_path)
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert (edge["fromNode"], edge["relation"]) == ("H-0001", "source_snapshot")
    assert edge["provenance"]["reference"] == ref
    node = next(n for n in graph["nodes"] if n["id"] == ident)
    assert node["external"]["source_references"] == [ref]
    assert node["external"]["delivery_status"] == "consumed"
    assert node["external"]["proposal_cycle_id"] == "99"


def test_negated_and_incidental_h_mentions_do_not_create_context(tmp_path):
    with _database(tmp_path) as store:
        store.add_hypothesis("H-0001")
        _external(store, "human-conjecture-" + "1" * 20, "不沿 H-0001 的研究目标开展研究。")
        _external(store, "human-conjecture-" + "2" * 20, "记录 H-0001 的编号供查阅。")
    assert load_canvas(tmp_path)["edges"] == []


def test_claimed_direction_remains_claimed_with_a_partially_older_schema(tmp_path):
    ident = "human-conjecture-" + "c" * 20
    with _database(tmp_path) as store:
        store.add_hypothesis("H-0001")
        store.db.execute("INSERT INTO hypothesis_cycle_owners VALUES (?,?,?)", ("7", "H-0001", "stamp"))
        _external(store, ident, "一个方法方案", cycle="7")
        store.db.execute("DELETE FROM graph_events WHERE action='external.human_conjecture.consumed'")
        source = store.graph_view()
        store.db.execute("DROP TABLE cycle_preservations")
        contexts, edges = external_graph_context(store, source)
    assert contexts[ident]["delivery_status"] == "claimed"
    assert len(edges) == 1
    assert edges[0]["provenance"]["meaning"] == "claimed_for_proposal"
    assert "已认领" in edges[0]["label"]
    assert "尚无送达完成记录" in edges[0]["reason"]


def test_external_sequence_survives_deletion_and_legacy_canvas_is_unchanged(tmp_path):
    with _database(tmp_path) as store:
        _external(store, "human-conjecture-" + "1" * 20, "第一个方向")
        _external(store, "human-conjecture-" + "2" * 20, "第二个方向")
        store.db.execute("DELETE FROM external_records WHERE rowid=1")
    node = load_canvas(tmp_path)["nodes"][0]
    assert node["external"]["display_id"] == "E2"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    payload = {"nodes": [{"id": "old", "text": "type: External\ncycle: 7\n\n# Old external"}], "edges": []}
    (legacy / "exploration.canvas").write_text(json.dumps(payload), encoding="utf-8")
    assert load_canvas(legacy) == payload
