from __future__ import annotations

"""Deterministic offline backends used by tests and smoke runs."""

import json
import re
import uuid
from pathlib import Path
from typing import Any


def _prompt_stage(prompt: str) -> str:
    marker = "Current task: "
    if marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1]
    return tail.split(" ", 1)[0].strip()

def _fake_sqlite_lineage(context: dict[str, Any]) -> dict[str, Any]:
    """Offline Lead fixture derives H lineage only from the injected SQLite view."""
    graph = context.get("agent_graph") if isinstance(context.get("agent_graph"), dict) else {}
    frontiers = graph.get("current_frontiers") or []
    active = [row for row in frontiers if isinstance(row, dict)
              and "active" in (row.get("states") or []) and row.get("id")]
    if active:
        return {"lineage_anchor_node_ids": [str(active[-1]["id"])],
                "evolution_operator": "specialize"}
    hypotheses = graph.get("hypotheses") or []
    for row in reversed(hypotheses):
        if isinstance(row, dict):
            hypothesis_id = str(row.get("id") or row.get("hypothesis_id") or "").strip()
            if hypothesis_id:
                return {"lineage_anchor_node_ids": [hypothesis_id],
                        "evolution_operator": "alternative"}
        elif isinstance(row, list) and row:
            hypothesis_id = str(row[0] or "").strip()
            if hypothesis_id:
                return {"lineage_anchor_node_ids": [hypothesis_id],
                        "evolution_operator": "alternative"}
        elif isinstance(row, str) and row.strip():
            return {"lineage_anchor_node_ids": [row.strip()],
                    "evolution_operator": "alternative"}
    return {}


class FakeBackend:
    """Deterministic backend used by tests and the offline smoke."""
    def __init__(self, reworks: int = 1):
        self.reworks = reworks
        self.judges = 0
        self.collector: Any | None = None
    def ensure_thread(self, role: str, thread_id: str | None, model: str, provider: str) -> str:
        return thread_id or f"fake-{role}-{uuid.uuid4().hex[:8]}"
    def drop_thread(self, thread_id: str | None) -> None:
        return
    def invoke(self, role: str, prompt: str, thread_id: str | None, model: str, provider: str) -> tuple[str, str]:
        thread = self.ensure_thread(role, thread_id, model, provider)
        context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
        stage = _prompt_stage(prompt)
        if self.collector is not None:
            self.collector.on_codex(
                {"event": "LLM_TURN_STARTED", "agent": role, "role": role, "thread_id": thread, "turn_id": f"{thread}-1"},
                role=role, thread_id=thread,
            )
        if role == "research_lead":
            if stage == "hypothesis_display":
                value = {
                    "canvas_title": "冻结科学假说",
                    "canvas_claim": "冻结命题中的具体预测仍需检验",
                    "canvas_conditions": "仅限冻结的适用范围与决定性检验",
                }
            elif stage == "frontier_maintenance":
                value = {"updates": []}
            elif stage == "cycle_synthesis":
                access = context.get("scientific_access") if isinstance(context.get("scientific_access"), dict) else {}
                previous_model = {}
                model_path = str(access.get("scientific_model_path") or "")
                if model_path and Path(model_path).is_file():
                    previous_model = json.loads(Path(model_path).read_text(encoding="utf-8"))
                value = {
                    "scientific_synthesis": {
                        "conclusions": ["smoke path completed"],
                        "open_questions": [],
                    },
                    "scientific_model_update": {
                        "action": "replace",
                        "model": {
                            "summary": "Deterministic smoke fixture completed its controlled evidence path.",
                        },
                    },
                }
            elif context.get("repair_options"):
                options = context.get("repair_options") or []
                selected = str(options[0]["candidate_id"]) if options else "smoke-candidate"
                blockers = (options[0].get("blockers") or []) if options and isinstance(options[0], dict) else []
                value = {
                    "selected_parent_candidate_id": selected,
                    "outcome": "resolved",
                    "repair": {
                        "repaired_decisive_test": "register deterministic smoke evidence",
                        "blocker_closures": [
                            {"blocker_id": str(item.get("id")), "resolution": "deterministic fixture closes the frozen blocker"}
                            for item in blockers if isinstance(item, dict) and item.get("id")
                        ],
                    },
                }
            elif context.get("qualified_candidates"):
                candidates = context.get("qualified_candidates") or []
                selected = str(candidates[0]["id"]) if candidates else "smoke-candidate"
                value = {"selected_candidate_id": selected,
                         "selection_reason": "prefer the qualified deterministic smoke candidate"}
            else:
                value = {"candidates": [{"hypothesis": "smoke-h",
                                          "scope": "deterministic offline smoke scope",
                                          "why_now": "deterministic fixture is immediately executable",
                                          "goal_advance": "advance the deterministic fixture Goal from definition to registered evidence",
                                          "decisive_test": "register deterministic smoke evidence",
                                          **_fake_sqlite_lineage(context)}]}
        elif role == "challenger":
            value = {"reviews": [{"candidate_id": item["id"], "verdict": "qualified",
                                  "reason": "the smoke candidate is atomic, falsifiable, and executable in one Cycle"}
                                 for item in context.get("candidate", [])]}
        elif role == "investigator":
            # The offline fixture must obey the same Agent-facing boundary as a
            # real Investigator. It reasons from the frozen objective and must
            # not depend on SDK-only criterion objects or Evidence bookkeeping.
            objective = context.get("frozen_objective") if isinstance(context.get("frozen_objective"), dict) else {}
            target = str(
                objective.get("decisive_test")
                or objective.get("hypothesis")
                or "deterministic smoke objective"
            ).strip()
            findings = [
                {"statement": target + " — offline synthetic smoke derivation (fixture only)"},
                {"statement": target + " — offline synthetic smoke numerical observation (fixture only)"},
            ]
            for objection in context.get("judge_objections") or []:
                if not isinstance(objection, dict):
                    continue
                requested = str(objection.get("requested_resolution") or "").strip()
                reason = str(objection.get("reason") or "").strip()
                if requested or reason:
                    findings.append({
                        "statement": " — ".join(
                            part for part in (target, requested, reason, "smoke controlled check") if part
                        ),
                    })
            value = {"findings": findings}
        elif role == "literature":
            value = {
                "evidence_records": [{
                    "source_ref": "https://example.invalid/theorymaster-literature-fixture",
                    "scientific_insight": "deterministic external source fixture",
                    "interpretation": "test-only verified source",
                    "limitations": "offline deterministic fixture only",
                    "source_access": "full_text",
                    "locator": {"section": "fixture"},
                    "exact_quote": "deterministic external source fixture",
                }],
                "remaining_scientific_question": "",
            }
            if stage == "landscape":
                value["landscape"] = {
                    "field_scope": str(context.get("search_target") or "smoke field"),
                    "major_routes": [], "representative_papers": [], "open_questions": [],
                    "relation_to_current_research": "smoke-only external field map",
                    "recommended_reading_order": [],
                }
            elif stage == "novelty_check":
                value["positioning"] = {"assessment": "uncertain in smoke fixture", "closest_prior_work": [],
                                        "priority_risks": [], "required_citations": []}
        elif role == "judge":
            if stage == "goal_completion":
                value = {
                    "verdict": "not_passed",
                    "rationale": "the deterministic smoke trajectory does not yet satisfy the task Final Gate",
                }
            else:
                self.judges += 1; more = self.judges <= self.reworks
                value = {"verdict": None if more else "supported",
                         "objection": ({"requested_resolution": "run the controlled check",
                                         "needs_external_scholarship": False} if more else None),
                         "rationale": "one controlled check remains" if more else "deterministic smoke verdict"}
        elif role == "reporter":
            value = {"title": "TheoryMaster smoke report", "summary": "SDK-independent workflow smoke completed."}
        else: raise ValueError(role)
        if self.collector is not None:
            self.collector.on_codex(
                {"event": "LLM_TURN_COMPLETED", "agent": role, "role": role, "thread_id": thread, "turn_id": f"{thread}-1",
                 "tokens_in": 3, "tokens_out": 1, "tokens_total": 4},
                role=role, thread_id=thread,
            )
        return thread, json.dumps(value)


class CanonicalFakeBackend(FakeBackend):
    """Offline fixture that exercises the V2 Research Lead canonical path."""

    def invoke(self, role: str, prompt: str, thread_id: str | None, model: str, provider: str) -> tuple[str, str]:
        if role == "research_lead":
            context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
            stage = _prompt_stage(prompt)
            thread = self.ensure_thread(role, thread_id, model, provider)
            if context.get("qualified_candidates"):
                candidates = context.get("qualified_candidates") or []
                selected = str(candidates[0]["id"]) if candidates else "canonical-smoke"
                return thread, json.dumps({
                    "selected_candidate_id": selected,
                    "selection_reason": "prefer the Challenger-qualified canonical smoke candidate",
                })
            if stage == "frontier_proposal":
                return thread, json.dumps({
                    "candidates": [{
                        "hypothesis": "canonical smoke claim",
                        "scope": "deterministic canonical smoke scope",
                        "why_now": "the deterministic canonical fixture is immediately executable",
                        "goal_advance": "advance the canonical fixture Goal from definition to registered evidence",
                        "decisive_test": "register deterministic canonical smoke evidence",
                        **_fake_sqlite_lineage(context),
                    }],
                })
            if stage == "frontier_maintenance":
                return thread, json.dumps({"updates": []})
            if stage == "cycle_synthesis":
                access = context.get("scientific_access") if isinstance(context.get("scientific_access"), dict) else {}
                previous_model = {}
                model_path = str(access.get("scientific_model_path") or "")
                if model_path and Path(model_path).is_file():
                    previous_model = json.loads(Path(model_path).read_text(encoding="utf-8"))
                return thread, json.dumps({
                    "scientific_synthesis": {
                        "conclusions": ["canonical path exercised"],
                        "open_questions": [],
                    },
                    "scientific_model_update": {
                        "action": "replace",
                        "model": {
                            "summary": "Canonical smoke path completed.",
                        },
                    },
                })
        return super().invoke(role, prompt, thread_id, model, provider)
