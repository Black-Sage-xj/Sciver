from __future__ import annotations

import json
from pathlib import Path

from theory_master.hypothesis_dossier_migration import repair_hypothesis_dossiers
from theory_master.science_graph_store import ScienceGraphStore


def _add_legacy_text(store: ScienceGraphStore, hypothesis_id: str, text: str) -> None:
    store.db.execute(
        "INSERT INTO legacy_records(id,node_type,raw_json,diagnostic) VALUES (?,?,?,?)",
        (hypothesis_id, "text", json.dumps({"id": hypothesis_id, "type": "text", "text": text}, ensure_ascii=False), ""),
    )


def test_backfill_preserves_originals_adds_all_parents_and_is_idempotent(tmp_path: Path):
    database = tmp_path / "science_graph.sqlite3"
    notes = tmp_path / "notes"
    long_claim = "在固定制备协议与周期网络内，" + "原始科学命题" * 45
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title=long_claim, summary=long_claim + "\n\nRationale: original explanation")
        store.add_hypothesis("H-0002", title="多源谱系检验", summary="历史摘要过长" * 40)
        store.add_hypothesis("H-0003", title="第二上游", summary="历史摘要过长" * 40)
        store.add_evolution(edge_id="e1", source="H-0001", target="H-0002", operator="specialize")
        store.add_evolution(edge_id="e2", source="H-0003", target="H-0002", operator="derive")
        _add_legacy_text(store, "H-0001", "# 原始命题\n" + long_claim + "\nEvidence refs: E-1")
        _add_legacy_text(store, "H-0002", "# 多源谱系检验\n原始多父节点文本")
        _add_legacy_text(store, "H-0003", "# 第二上游\n原始父节点文本")
        store.db.execute(
            "UPDATE hypotheses SET dossier_ref=? WHERE id=?",
            ("notes/missing-H-0003", "H-0003"),
        )

    preview = repair_hypothesis_dossiers(database, notes, preview=True)
    assert preview["hypotheses"] == 3
    assert preview["missing_dossiers"] == 3
    assert preview["broken_refs"] == 1
    assert preview["multi_parent_dossiers"] == 1
    assert not notes.exists()

    result = repair_hypothesis_dossiers(database, notes, preview=False)
    assert result["dossiers_written"] == 3
    assert result["broken_refs"] == 1
    assert result["conflicts"] == []
    dossier = (notes / "hypotheses/H-0002.md").read_text(encoding="utf-8")
    assert "原始多父节点文本" in dossier
    assert "H-0001（specialize）" in dossier
    assert "H-0003（derive）" in dossier
    assert "未保存的冻结范围或决定性检验不作推断补写" in dossier

    with ScienceGraphStore(database, readonly=True) as store:
        rows = list(store.db.execute("SELECT id,title,summary,dossier_ref FROM hypotheses ORDER BY id"))
        assert all(row["dossier_ref"] for row in rows)
        assert all(len(row["title"]) <= 40 for row in rows)
        assert all(len(row["summary"]) <= 160 for row in rows)
        h1 = next(row for row in rows if row["id"] == "H-0001")
        assert "摘要暂不可用" in h1["summary"]
        assert long_claim not in h1["summary"]

    second = repair_hypothesis_dossiers(database, notes, preview=True)
    assert second["missing_dossiers"] == 0
    assert second["broken_refs"] == 0
    assert second["display_updates"] == 0
