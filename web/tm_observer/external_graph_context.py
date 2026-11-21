"""Read-only presentation of External inputs and their explicit discussion context.

These links describe recorded discussion and delivery. They never modify the
canonical graph, scientific lineage, qualification or verdicts.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

_MARKER = re.compile(r"^<!-- sciver:human-conjecture:v1 (.+) -->$", re.M)
_H_ID = re.compile(r"(?<![A-Za-z0-9_-])H-\d{4,6}(?![A-Za-z0-9_-])", re.I)
_TARGET_PREFIX = re.compile(r"(?:沿|基于|围绕|针对|关于|延续|接续|承接|继续|依据|扩展)\s*(?:现有|已有|先前|原有)?\s*$")
_TARGET_SUFFIX = re.compile(r"\s*的?\s*(?:研究目标|研究方向|研究问题|后续研究|猜想|命题)")
_NEGATED = re.compile(r"(?:不|未|无需|无须|避免|禁止|排除|而非|并非|不能|不可|without|not\b)", re.I)
_DELIVERABLE_TITLE = re.compile(
    # Look ahead so a lexical occurrence such as “解析构造” cannot swallow a
    # later actual action (“设计一套…”). Each possible action is considered.
    r"(?=(?P<verb>设计|交付|提出|构造)(?P<count>一套|一份|一个)?(?:供审阅的|可审阅的)?(?P<title>[^：:。！？\n]+))"
)
_ACTION_BOUNDARY = re.compile(r"(?:[，,；;：:\s]|(?:可|先|将|再|并|拟|应|需)(?:以|先)?)$")


def _object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _plain(value: Any, limit: int) -> str:
    text = re.sub(r"<!--.*?-->", "", str(value or ""), flags=re.S)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|>\s*)", "", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _human_record(node: dict[str, Any]) -> dict[str, str] | None:
    if node.get("source") != "human" or node.get("content_type") != "ResearchFocus":
        return None
    matches = _MARKER.findall(str(node.get("content") or ""))
    if not matches:
        return None
    record = _object(matches[-1])
    fields = ("external_id", "hypothesis", "decisive_test", "reason", "source_session")
    if (record.get("external_id") != node.get("id")
            or any(not isinstance(record.get(field), str) for field in fields)):
        return None
    return {field: record[field] for field in fields}


def _human_title(hypothesis: str) -> str:
    """Extract the named deliverable verbatim, retaining its first defining clause."""
    first_sentence = re.split(r"[。！？\n]", hypothesis, maxsplit=1)[0]
    matches = [match for match in _DELIVERABLE_TITLE.finditer(first_sentence)
               if match["count"] or match.start() == 0
               or _ACTION_BOUNDARY.search(first_sentence[:match.start()])]
    match = max(matches, key=lambda item: (
        bool(item["count"]), item["verb"] in {"设计", "交付"}, item.start(),
    ), default=None)
    if match and (match.start() > 0 or match["count"]):
        title = match["title"].strip()
        # A generic name such as “对照方案” gains a useful, still-verbatim
        # definition; a specific named deliverable already explains itself.
        tail = first_sentence[match.end("title"):]
        if len(title) <= 8 and tail.startswith(("：", ":")):
            definition = re.split(r"[，,；;]", tail[1:], maxsplit=1)[0].strip()
            if definition:
                title += "：" + definition
        return _plain(title, 48)
    return _plain(first_sentence, 48)


def _doc_key(value: Any) -> str:
    """Match exact stored vault references, allowing only the optional .md suffix."""
    ref = str(value or "").split("#", 1)[0].strip()
    return ref[:-3] if ref.endswith(".md") else ref


def _rows(db: Any, sql: str) -> list[Any]:
    try:
        return db.execute(sql).fetchall()
    except sqlite3.Error:
        # Older compatible graphs may not yet contain delivery/owner tables.
        return []


def external_graph_context(store: Any, source: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    """Project stable input labels, original fields and evidenced H→E links.

    E numbers use the original SQLite rowid, independently of Cycle numbers,
    ID sorting and graph filtering. Proposal delivery needs an exact Cycle owner;
    consumed alone never means that a final H adopted the requested proposition.
    """
    db = store.db
    hypotheses = {n["id"]: n for n in source["nodes"] if n.get("type") == "Hypothesis"}
    sequences = {str(row["id"]): int(row["external_sequence"]) for row in _rows(
        db, "SELECT rowid AS external_sequence,id FROM external_records ORDER BY rowid")}
    cycle_owners: dict[str, set[str]] = {}
    for table in ("hypothesis_cycle_owners", "cycle_evaluations", "cycle_preservations"):
        for row in _rows(db, f"SELECT cycle_id,hypothesis_id FROM {table}"):
            if row["hypothesis_id"] in hypotheses:
                cycle_owners.setdefault(str(row["cycle_id"]), set()).add(row["hypothesis_id"])
    doc_owners: dict[str, set[str]] = {}
    for ident, node in hypotheses.items():
        refs = [node.get("dossier_ref"), f"notes/hypotheses/{ident}.md"]
        for outcome in node.get("cycle_outcomes") or []:
            refs.extend(outcome.get(field) for field in ("document_ref", "report_ref"))
        if isinstance(node.get("outcome"), dict):
            refs.extend(node["outcome"].get(field) for field in ("document_ref", "report_ref"))
        for ref in refs:
            if ref:
                doc_owners.setdefault(_doc_key(ref), set()).add(ident)
    events: dict[str, list[dict]] = {}
    for row in _rows(db, "SELECT seq,timestamp,action,subject,detail_json FROM graph_events "
                         "WHERE action LIKE 'external.human_conjecture.%' ORDER BY seq"):
        events.setdefault(str(row["subject"]), []).append({
            "seq": row["seq"], "timestamp": row["timestamp"], "action": row["action"],
            "status": str(row["action"]).rsplit(".", 1)[-1],
            "detail": _object(row["detail_json"]),
        })

    contexts: dict[str, dict] = {}
    edges: list[dict] = []
    for node in source["nodes"]:
        if node.get("type") != "External":
            continue
        ident = node["id"]
        human = _human_record(node)
        history = events.get(ident, [])
        title_source = human["hypothesis"] if human else node.get("title") or ident
        first_sentence = re.split(r"[。！？\n]", str(title_source), maxsplit=1)[0]
        sequence = sequences.get(ident)
        context = {
            "sequence": sequence, "display_id": f"E{sequence}" if sequence is not None else "E",
            "label": f"E{sequence}" if sequence is not None else "E",
            "title": (_human_title(human["hypothesis"]) if human else _plain(first_sentence, 64)) or ident,
            "summary": _plain(human["hypothesis"] if human else node.get("content"), 220),
            "created_at": node.get("created_at"),
            "delivery_status": history[-1]["status"] if history else (
                "reconciliation_required" if human else None),
            "event_history": history, "source_references": [],
            "source_session": human["source_session"] if human else None,
            "hypothesis": human["hypothesis"] if human else None,
            "decisive_test": human["decisive_test"] if human else None,
            "reason": human["reason"] if human else None,
            "proposal_cycle_id": None,
        }
        contexts[ident] = context
        candidates: dict[tuple[str, str], dict] = {}

        def add(target: str, relation: str, label: str, reason: str, provenance: dict) -> None:
            if target not in hypotheses:
                return
            key = (target, relation)
            if key in candidates:
                candidates[key]["provenance"]["additional_records"].append(provenance)
                return
            candidates[key] = {
                "id": "discussion-" + hashlib.sha256(f"{ident}|{target}|{relation}".encode()).hexdigest()[:20],
                "fromNode": target, "toNode": ident, "edge_type": "discussion_context",
                "relation": relation, "label": label, "reason": reason,
                "confidence": 1.0, "projection_only": True, "display_primary": False,
                "provenance": {**provenance, "additional_records": []},
            }

        snapshot_seen: set[str] = set()
        delivery_events = {
            str(event["detail"].get("cycle_id") or ""): event for event in history
            if event["status"] in {"claimed", "consumed"}
        }
        for event in history:
            refs = event["detail"].get("source_references")
            for ref in refs if isinstance(refs, list) else []:
                if not isinstance(ref, dict):
                    continue
                encoded = json.dumps(ref, sort_keys=True, ensure_ascii=False)
                if encoded in snapshot_seen:
                    continue
                snapshot_seen.add(encoded)
                context["source_references"].append(ref)
                owner = ref.get("hypothesis_id")
                if owner not in hypotheses:
                    owner = ref.get("id") if ref.get("kind") == "node" else None
                if owner not in hypotheses and ref.get("kind") == "report":
                    owners = cycle_owners.get(str(ref.get("cycle_id") or ref.get("id")), set())
                    owner = next(iter(owners)) if len(owners) == 1 else None
                if owner not in hypotheses and ref.get("kind") == "doc":
                    owners = doc_owners.get(_doc_key(ref.get("document_id") or ref.get("id")), set())
                    owner = next(iter(owners)) if len(owners) == 1 else None
                if owner in hypotheses:
                    add(owner, "source_snapshot", "讨论来源", "登记时选取的讨论来源明确指向此 H；保留的是当时的来源快照。", {
                        "source": "graph_events.source_references", "event_seq": event["seq"], "reference": ref,
                    })
            if event["status"] in {"claimed", "consumed"}:
                cycle = str(event["detail"].get("cycle_id") or "")
                context["proposal_cycle_id"] = cycle or context["proposal_cycle_id"]
                if event is not delivery_events[cycle]:
                    continue
                owners = cycle_owners.get(cycle, set())
                if len(owners) == 1:
                    consumed = event["status"] == "consumed"
                    label = "本轮讨论输入" if consumed else "本轮讨论输入（已认领）"
                    reason = (f"此外部方向已送入 Cycle {cycle} 的提案流程，该轮明确归属此 H；送达记录不代表最终 H 采纳了全部内容。"
                              if consumed else f"此外部方向已被 Cycle {cycle} 的提案流程认领，该轮明确归属此 H；尚无送达完成记录。")
                    add(next(iter(owners)), "proposal_delivery", label, reason, {
                            "source": "graph_events+cycle_ownership", "event_seq": event["seq"],
                            "event_action": event["action"], "cycle_id": cycle,
                            "meaning": "delivered_to_proposal_only" if consumed else "claimed_for_proposal",
                            "delivery_status": event["status"],
                        })
        # Only the structured proposition can supply a fallback discussion target.
        # Tests/reasons often name H objects solely to exclude rewriting or judging
        # them, so arbitrary body regex matches must never create links.
        if human:
            for clause in re.split(r"[。！？；;，,\n]", human["hypothesis"]):
                for match in _H_ID.finditer(clause):
                    before, after = clause[:match.start()], clause[match.end():]
                    if _NEGATED.search(before) or not (
                            _TARGET_PREFIX.search(before) or _TARGET_SUFFIX.match(after)):
                        continue
                    owner = match.group().upper()
                    if (owner, "source_snapshot") not in candidates:
                        add(owner, "body_mention", "讨论目标", "人工猜想正文明确将此 H 作为讨论目标。", {
                            "source": "external_records.content.human_conjecture.hypothesis",
                            "field": "hypothesis", "matched_id": owner, "excerpt": clause.strip(),
                        })
        ranked = sorted(candidates.values(), key=lambda edge: (
            {"proposal_delivery": 0, "source_snapshot": 1, "body_mention": 2}[edge["relation"]],
            edge["fromNode"],
        ))
        if ranked:
            ranked[0]["display_primary"] = True
        for edge in ranked:
            edge["provenance_json"] = json.dumps(edge["provenance"], ensure_ascii=False, sort_keys=True)
        edges.extend(ranked)
    return contexts, edges
