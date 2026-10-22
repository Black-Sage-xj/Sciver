"""Authoritative Cycle note and report projection."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .display import direction_text
from .evidence import adjudicable_manifest_records
from .runtime import atomic, canvas_ts, write_json


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _cycle_scientific_state(
    scientific_synthesis: dict[str, Any],
    evidence_scope_digest: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Build the compact post-Cycle state from already-authorized scientific facts."""
    established = _unique_text(list(scientific_synthesis.get("conclusions") or []))
    unresolved = _unique_text(list(scientific_synthesis.get("open_questions") or []))
    boundary_items: list[str] = []
    for item in evidence_scope_digest:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "").strip()
        limitations = str(item.get("limitations") or "").strip()
        if scope:
            boundary_items.append(f"Scope: {scope}")
        if limitations:
            boundary_items.append(f"Limitation: {limitations}")
    boundary = _unique_text(boundary_items)
    return {
        key: value for key, value in {
            "established": established,
            "unresolved": unresolved,
            "boundary": boundary,
        }.items() if value
    }


class ReportingMixin:
    def _write_cycle_report(
        self, state: dict[str, Any], branch: dict[str, Any], judge: dict[str, Any],
        active_title: str, active_hypothesis: str, verdict: str,
    ) -> None:
        update_path = self.cycle_dir / "scientific_model_update.json"
        model_update = json.loads(update_path.read_text(encoding="utf-8")) if update_path.is_file() else {}
        model_out_path = self.cycle_dir / "scientific_model_out.json"
        scientific_model = (
            json.loads(model_out_path.read_text(encoding="utf-8"))
            if model_out_path.is_file() else (model_update.get("model") or {})
        )
        evidence_context = self._evidence_context()
        evidence_refs = [
            str(item["evidence_id"])
            for item in adjudicable_manifest_records(evidence_context.get("evidence_manifest"))
            if item.get("evidence_id")
        ]
        report = state["role_outputs"].get("reporter") or self._call(state, "reporter", {
            "branch": branch, "judge": judge,
            "scientific_synthesis": state.get("scientific_synthesis") or {},
            "scientific_model_update": model_update,
            "scientific_model": scientific_model,
            "scientific_model_version_in": state.get("scientific_model_version_in"),
            "scientific_model_version_out": state.get("scientific_model_version_out"),
            "evidence_refs": evidence_refs,
            # A terminal report closes this Cycle only.  The next Cycle owns
            # proposing and locking its own hypothesis.
            "evolution_candidates": [],
            "authorized_next_frontier_candidate_id": None,
            **evidence_context,
        })
        notes_dir = self.spec.notes_mirror_dir or self.notes_dir or (self.cycle_dir / "notes")
        notes_dir.mkdir(parents=True, exist_ok=True)
        # A vault directory is shared by a long-lived branch.  Prefix the
        # report name with its immutable Cycle ID so repeated titles cannot
        # silently overwrite an earlier scientific decision.
        cycle_ref = self.cycle_dir.name
        slug = re.sub(r"[^\w.-]+", "-", report.get("title", branch["title"]))[:80].strip("-") or "decision"
        note = notes_dir / f"{cycle_ref}--{slug}.md"
        if self.spec.notes_mirror_dir is not None:
            try:
                note.resolve().relative_to(self.spec.notes_mirror_dir.resolve())
            except ValueError as exc:
                raise ValueError("authoritative Cycle note must land in notes_mirror_dir") from exc
        repo_root = Path(self._path_anchors().get("repo_root") or Path.cwd()).resolve()
        try:
            cycle_rel = self.cycle_dir.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            cycle_rel = self.cycle_dir.resolve().as_posix()
        machine_index = [
            "## 机器审计与复现索引",
            "",
            "> 本节只提供机器权威记录的索引，不展开结构化对象。正文中的科学解释优先于这里的机器字段。",
            "",
            f"- Cycle：`{cycle_ref}`",
            f"- Branch verdict：`{judge.get('verdict') or ''}`",
            f"- Scientific Model：`v{state.get('scientific_model_version_in')} → v{state.get('scientific_model_version_out')}`",
        ]
        if evidence_refs:
            machine_index.append("- Evidence IDs：" + "、".join(f"`{ref}`" for ref in evidence_refs))
        machine_index.extend([
            "",
            "### 权威机器记录",
            "",
            f"- Scientific Model update：`{cycle_rel}/scientific_model_update.json`",
            f"- Scientific Model output：`{cycle_rel}/scientific_model_out.json`",
            f"- Research state：`{cycle_rel}/research_state.json`",
            f"- Evidence registry：`{cycle_rel}/evidence/registry.jsonl`",
            f"- Evidence manifest：`{cycle_rel}/workspace/evidence_manifest.json`",
            f"- Coverage matrix：`{cycle_rel}/workspace/coverage_matrix.json`",
            f"- Scientific events：`{cycle_rel}/events.jsonl`",
            f"- Runtime events：`{cycle_rel}/runtime_events.jsonl`",
            *([f"- Important events / anomalies：`{cycle_rel}/important_events.jsonl`"] if (self.cycle_dir / "important_events.jsonl").is_file() else []),
            *([f"- Historical anomalies：`{cycle_rel}/cycle_anomalies.jsonl`"] if (self.cycle_dir / "cycle_anomalies.jsonl").is_file() else []),
            "",
            "### 计算目录",
            "",
            f"- Workspace：`{cycle_rel}/workspace/`",
            f"- Evidence：`{cycle_rel}/evidence/`",
            "",
            "本笔记是该 Cycle 的人类可读科研叙事；机器权威状态保存在上述结构化文件中。",
        ])
        atomic(
            note,
            f"# {report.get('title', branch['title'])}\n\n{report.get('summary', '').strip()}\n\n"
            + "\n".join(machine_index)
            + "\n",
        )
        if self.branch_canvas is not None:
            from .hypothesis_dossier import append_hypothesis_cycle_report
            append_hypothesis_cycle_report(
                canvas_path=self.branch_canvas, notes_root=notes_dir,
                hypothesis_id=str(branch.get("hypothesis_ref") or ""),
                cycle_id=cycle_ref, note_path=note,
            )
        branch["note_path"] = str(note)
        branch["summary"] = str(report.get("summary") or "")
        branch["timestamp"] = canvas_ts()

        # The human report is the sole narrative source. Persist its Cycle-local
        # summary directly as a SQLite Outcome; no Canvas-writing Agent stage exists.
        source_id = str(branch.get("hypothesis_ref") or "").strip()
        graph_db = self._graph_database_path()
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph_db, readonly=True) as store:
            if not source_id or store.db.execute(
                "SELECT 1 FROM hypotheses WHERE id=?", (source_id,)
            ).fetchone() is None:
                raise ValueError(f"Reporter source H is missing from canonical SQLite graph: {source_id}")

        evidence_scope_digest = [
            {key: item.get(key) for key in (
                "evidence_id", "relation", "summary", "scope", "limitations", "provenance", "source_access"
            ) if item.get(key) not in (None, "", [], {})}
            for item in adjudicable_manifest_records(evidence_context.get("evidence_manifest"))
        ]
        cycle_scientific_state = _cycle_scientific_state(
            state.get("scientific_synthesis") if isinstance(state.get("scientific_synthesis"), dict) else {},
            evidence_scope_digest,
        )
        report_ref = ""
        if self.branch_canvas is not None:
            canvas_root = self.branch_canvas.resolve().parent
            try:
                report_ref = note.resolve().relative_to(canvas_root).as_posix()
            except ValueError:
                # The canonical Cycle workspace may live in the non-sync data
                # root outside Task Home. Keep the graph link resolvable from
                # the Canvas/Task Home instead of silently dropping it.
                report_ref = Path(os.path.relpath(note.resolve(), canvas_root)).as_posix()
        cycle_verdict = str(judge.get("verdict") or verdict or "open").strip()
        if cycle_verdict == "milestone_advanced":
            cycle_verdict = "open"
        if cycle_verdict not in {"open", "supported", "rejected"}:
            raise ValueError(f"Cannot persist unsupported Cycle Outcome verdict: {cycle_verdict}")
        decisive_test = str(
            branch.get("decisive_test")
            or (state.get("hypothesis_lock") or {}).get("decisive_test")
            or ""
        )
        state["cycle_outcome_record"] = {
            "id": f"cycle-outcome-{cycle_ref}",
            "title": str(report.get("title") or branch.get("title") or "Cycle Outcome").strip(),
            "hypothesis_id": source_id,
            "cycle_id": cycle_ref,
            "verdict": cycle_verdict,
            "summary": str(report.get("summary") or "").strip(),
            "decisive_test": decisive_test,
            "judge_rationale": str(judge.get("rationale") or "").strip(),
            "findings": list(cycle_scientific_state.get("established") or []),
            "open_questions": list(cycle_scientific_state.get("unresolved") or []),
            "boundary": list(cycle_scientific_state.get("boundary") or []),
            "evidence_refs": list(evidence_refs),
            "report_ref": report_ref,
            "record_only": True,
        }
        branch["summary"] = str(report.get("summary") or "")
        branch["timestamp"] = canvas_ts()
        state["next_start_node_id"] = None
        state.pop("frontier_state", None)
        state.update({
            "status": "over", "phase": "complete", "resumable": False,
            "execution_active": False,
            "current_state": state.get("scientific_model_current_state")
                or direction_text(active_title, active_hypothesis),
        })
        self._emit(state, "cycle.outcome_prepared", {
            "cycle_id": cycle_ref, "hypothesis_id": source_id,
            "verdict": cycle_verdict,
        })
        write_json(
            self.cycle_dir / "checkpoints" / f"{state['last_seq']:06d}-{cycle_verdict}.json",
            state,
        )
