"""One-time, source-preserving promotion of the completed legacy Cycle 133.

Run with the vault and Cycle directory as arguments. The original run files are
immutable evidence; only the canonical graph and generated reading files move.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from theory_master.hypothesis_dossier import write_hypothesis_dossier
from theory_master.science_canvas_export import migrate_original_canvas
from theory_master.science_graph_store import ScienceGraphStore


def promote(vault: Path, cycle: Path) -> str:
    vault, cycle = vault.resolve(), cycle.resolve()
    graph, canvas = vault / "science_graph.sqlite3", vault / "exploration.canvas"
    state_file, outcome_doc = cycle / "run_state.json", vault / "notes/outcomes/133.md"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    lock = state["hypothesis_lock"]
    if (state.get("status") != "OVER" or str(state.get("cycle_id")) != "133"
            or lock.get("status") != "locked" or lock.get("id") != "H-0089"):
        raise ValueError("Cycle 133 original lock or completion does not match")
    if not outcome_doc.is_file() or not canvas.is_file():
        raise ValueError("Canonical Cycle 133 reading files are missing")
    lead_records = [json.loads(path.read_text(encoding="utf-8")) for path in
                    (cycle / "semantic_closure").glob("inv-133-research_lead-*.json")]
    candidates = [candidate for record in lead_records
                  if record.get("stage") == "frontier_proposal"
                  for candidate in (record.get("closed_output") or {}).get("candidates", [])
                  if candidate.get("id") == lock.get("candidate_id")]
    if len(candidates) != 1:
        raise ValueError("Cycle 133 frozen candidate has no unique Lead source")
    lead = candidates[0]
    if any(lead.get(field) != lock.get(field) for field in
           ("hypothesis", "scope", "decisive_test")):
        raise ValueError("Lead proposal differs from the frozen scientific definition")
    backup = vault / ".backups" / "20261018-cycle133-new-h"
    if backup.exists():
        raise ValueError("Cycle 133 migration backup already exists; refusing to overwrite it")
    backup.mkdir(parents=True)
    with sqlite3.connect(str(graph)) as source, sqlite3.connect(str(backup / "science_graph.sqlite3")) as target:
        source.backup(target)
    for path in (canvas, outcome_doc):
        shutil.copy2(path, backup / path.name)
    with ScienceGraphStore(graph) as store:
        with store.transaction():
            prior = store.db.execute(
                "SELECT * FROM cycle_evaluations WHERE cycle_id='133'"
            ).fetchone()
            if not prior or prior["id"] != "133-decision" or prior["hypothesis_id"] != "H-0089":
                raise ValueError("Cycle 133 canonical evaluation already differs")
            if store.db.execute("SELECT 1 FROM hypothesis_cycle_owners WHERE cycle_id='133'").fetchone():
                raise ValueError("Cycle 133 already has a formal H owner")
            candidate = store.db.execute(
                "SELECT id FROM hypotheses WHERE id GLOB 'H-[0-9]*' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            h_id = f"H-{int(candidate['id'][2:]) + 1:04d}"
            evaluation = json.loads(prior["source_json"])
            if (evaluation.get("verdict") != "open" or evaluation.get("hypothesis_id") != "H-0089"
                    or evaluation.get("decisive_test") != lock.get("decisive_test")):
                raise ValueError("Cycle 133 evaluation does not match the frozen lock")
            title = "共同正幅值对冻结均匀慢场第6至12代的局部解释"
            summary = lock["hypothesis"] + "\n\n冻结范围：" + lock["scope"]
            store.add_hypothesis(h_id, title=title, summary=summary)
            store.register_cycle_hypothesis(cycle_id="133", hypothesis_id=h_id)
            store.add_evolution(edge_id=f"cycle-133-H-0089-to-{h_id}",
                                source="H-0089", target=h_id, operator="specialize",
                                outcome_id="132-decision")
            store.db.execute(
                "UPDATE scientific_edges SET provenance_json=? WHERE id=?",
                (json.dumps({
                    "source": "Cycle 133 Lead frontier_proposal + frozen lock",
                    "candidate_id": lead["id"], "original_anchor": "H-0089",
                    "operator_assignment": "post-cycle migration based on scoped test; not asserted as Lead-authored",
                }, ensure_ascii=False), f"cycle-133-H-0089-to-{h_id}"),
            )
            dossier_ref = write_hypothesis_dossier(
                canvas_path=canvas, notes_root=vault / "notes", hypothesis_id=h_id,
                cycle_id="133", hypothesis=lock["hypothesis"], scope=lock["scope"],
                why_now=lead["why_now"], goal_advance=lead["goal_advance"],
                decisive_test=lock["decisive_test"], display_title=title,
                display_summary="Cycle 133 冻结共同正幅值局部检验。",
                candidate_id=lock["candidate_id"], evolution_operator="specialize",
                lineage_anchor_node_id="H-0089",
            )
            dossier = vault / (dossier_ref + ".md")
            dossier_text = dossier.read_text(encoding="utf-8")
            dossier_text = dossier_text.replace(
                "Lead 的标题和摘要仅用于阅读，科学原文在冻结快照中。",
                "科学定义与 Why now / Goal advance 引自 Cycle 133 Lead 原始候选；展示标题和摘要由迁移工具整理。")
            dossier_text = dossier_text.replace("Canvas 展示摘要（Lead 文案）", "Canvas 展示摘要（迁移整理）")
            dossier.write_text(dossier_text, encoding="utf-8")
            store.db.execute("UPDATE hypotheses SET dossier_ref=?, scientific_status='open' WHERE id=?",
                             (dossier_ref, h_id))
            evaluation["hypothesis_id"] = h_id
            evaluation["identity_migration"] = {
                "original_hypothesis_id": "H-0089", "source": "completed Cycle 133 run_state.json",
                "run_state_sha256": hashlib.sha256(state_file.read_bytes()).hexdigest(),
                "reason": "user requested one formal H per scientific Cycle",
            }
            store.db.execute("UPDATE cycle_evaluations SET hypothesis_id=?,source_json=? WHERE cycle_id='133'",
                             (h_id, json.dumps(evaluation, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))
            store.db.execute(
                "UPDATE outcomes SET cycle_id='133',verdict='open',summary=?,decisive_test=?,"
                "judge_rationale=?,findings_json=?,open_questions_json=?,boundary_json=?,"
                "evidence_refs_json=?,report_ref=? WHERE hypothesis_id=?",
                (evaluation["summary"], evaluation["decisive_test"], evaluation["judge_rationale"],
                 json.dumps(evaluation["findings"], ensure_ascii=False),
                 json.dumps(evaluation["open_questions"], ensure_ascii=False),
                 json.dumps(evaluation["boundary"], ensure_ascii=False),
                 json.dumps(evaluation["evidence_refs"], ensure_ascii=False),
                 evaluation["report_ref"], h_id),
            )
            store._event("cycle.identity_migrated", "133", evaluation["identity_migration"] | {
                "new_hypothesis_id": h_id, "scientific_candidate_author": "research_lead",
                "candidate_id": lock["candidate_id"], "identity_writer": "SDK migration",
                "display_title_author": "migration summary", "outcome_copy_author": "reporter",
            })
    # The source document remains complete; its heading now names the formal
    # reading identity and records the original lock explicitly.
    text = outcome_doc.read_text(encoding="utf-8")
    source_digest = hashlib.sha256(json.dumps(
        evaluation, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    text = text.replace(text.splitlines()[0],
                        f"<!-- theory-master/cycle-outcome/source:{source_digest} -->", 1)
    text = text.replace("- Hypothesis ID: `H-0089`", f"- Hypothesis ID: `{h_id}`\n- Original locked H: `H-0089`", 1)
    outcome_doc.write_text(text, encoding="utf-8")
    migrate_original_canvas(canvas, graph)
    return h_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vault", type=Path)
    parser.add_argument("cycle", type=Path)
    args = parser.parse_args()
    print(promote(args.vault, args.cycle))
