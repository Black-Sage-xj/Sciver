"""Deliver an explicit human conjecture through the existing qualification input."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_PREFIX = "<!-- sciver:human-conjecture:v1 "
_PATTERN = re.compile(r"^<!-- sciver:human-conjecture:v1 (.+) -->$", re.M)
_FIELDS = ("external_id", "hypothesis", "decisive_test", "reason", "source_session")


def _validated(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(_FIELDS):
        raise ValueError("Invalid human conjecture record")
    result = {}
    for field in _FIELDS:
        text = value[field]
        if not isinstance(text, str) or len(text) > 4000 or (field != "source_session" and not text.strip()):
            raise ValueError(f"Invalid human conjecture field: {field}")
        result[field] = text
    if not re.fullmatch(r"human-conjecture-[a-f0-9]{16,32}", result["external_id"]):
        raise ValueError("Invalid human conjecture source node")
    return result


def format_requested_conjecture(*, external_id: str, hypothesis: str,
                                decisive_test: str, reason: str,
                                source_session: str = "") -> str:
    """A single-line marker cannot introduce extra Research Plan H1 sections."""
    record = _validated(dict(external_id=external_id, hypothesis=hypothesis,
                             decisive_test=decisive_test, reason=reason,
                             source_session=source_session))
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    payload = (payload.replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return _PREFIX + payload + " -->"


def requested_conjecture(required_context: str) -> dict[str, str] | None:
    """Read the latest current-plan direction, with no secondary queue or state."""
    position = required_context.rfind(_PREFIX)
    if position < 0:
        return None
    line_end = required_context.find("\n", position)
    last_line = required_context[position:line_end if line_end >= 0 else None].rstrip("\r")
    match = _PATTERN.fullmatch(last_line)
    if match is None or (position > 0 and required_context[position - 1] != "\n"):
        raise ValueError("Malformed human conjecture in Required Context")
    try:
        return _validated(json.loads(match.group(1)))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid human conjecture in Required Context") from exc


def qualification_guidance(required_context: str, existing: str = "", *,
                           record: dict[str, Any] | None = None) -> str:
    record = record if record is not None else requested_conjecture(required_context)
    if record is None:
        return existing
    instruction = (
        "Current human-requested research direction from its canonical External record. "
        "This is a proposed conjecture, not evidence or a scientific verdict. "
        "Start the candidate proposal from this exact proposition and decisive test. "
        "Keep the Goal, Research Contract, Final Gate and Challenger qualification. "
        "If this direction cannot form an admissible candidate, report the conflict "
        "instead of silently replacing the requested subject. The SDK allocates the "
        "formal H only after qualification. Preserve this external source as human "
        "provenance; do not use an External ID as scientific H lineage.\n"
        + json.dumps(record, ensure_ascii=False)
    )
    return "\n\n".join(part for part in (instruction, existing) if part)


def _directions(store: Any, required_context: str = "") -> list[dict[str, Any]]:
    """Content and delivery state both belong to the canonical graph.

    Historical records stay visible as reconciliation_required. They must be
    explicitly re-registered, so a legacy marker cannot hide a FIFO entry.
    """
    requested_conjecture(required_context)
    result = []
    for row in store.db.execute(
        "SELECT id,content FROM external_records WHERE content_type='ResearchFocus' "
        "AND source='human' AND id LIKE 'human-conjecture-%' ORDER BY created_at,id"
    ):
        record = requested_conjecture(row["content"])
        if record is None or record["external_id"] != row["id"]:
            continue
        event = store.db.execute(
            "SELECT action,detail_json FROM graph_events WHERE subject=? "
            "AND action LIKE 'external.human_conjecture.%' ORDER BY seq DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if event:
            detail = json.loads(event["detail_json"])
            result.append({"record": record, "status": event["action"].rsplit(".", 1)[1],
                           "cycle_id": str(detail.get("cycle_id") or ""),
                           "source_references": detail.get("source_references") or []})
        else:
            # Historical partial registrations require explicit re-registration.
            # Never let a context-dependent hidden legacy item jump a visible FIFO.
            result.append({"record": record, "status": "reconciliation_required", "cycle_id": "",
                           "source_references": [], "legacy": True})
    return result


def direction_status(store: Any, external_id: str) -> dict[str, Any]:
    item = next((item for item in _directions(store)
                 if item["record"]["external_id"] == external_id), None)
    if item is None:
        raise ValueError("人工猜想不存在或尚未完成规范登记")
    return item


def list_conjectures(database: Path, *, status: str | None = "pending") -> list[dict[str, Any]]:
    from .science_graph_store import ScienceGraphStore

    with ScienceGraphStore(database, readonly=True) as store:
        return [item for item in _directions(store) if status is None or item["status"] == status]


def transition_pending(store: Any, external_id: str, *, status: str,
                       reason: str, replacement_id: str = "", source_session: str = "") -> None:
    """Caller owns an IMMEDIATE transaction; claimed science cannot be rewritten."""
    if status not in {"revoked", "superseded"}:
        raise ValueError("Invalid human conjecture transition")
    item = direction_status(store, external_id)
    if item["status"] != "pending":
        raise ValueError("人工猜想已领取或处理，不能撤销或取代；当前科研命题保持不变")
    store._event("external.human_conjecture." + status, external_id,
                 {"reason": reason, "replacement_id": replacement_id,
                  "source_session": source_session,
                  "source_references": item["source_references"]})


def _require_existing_graph(database: Path) -> Path:
    """Delivery may update a canonical graph, never create its replacement."""
    from .science_graph_store import ScienceGraphStore

    path = Path(database)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path != path.resolve():
        raise ValueError("Canonical science graph is missing or unsafe; no replacement was created")
    with ScienceGraphStore(path, readonly=True):
        pass
    return path


def claim_conjecture(database: Path, *, cycle_id: str,
                     required_context: str = "", research_root: Path | None = None) -> dict[str, Any] | None:
    """Atomically deliver the FIFO direction to one fresh proposal episode.

    The same Cycle owns its snapshot through retries and crashes. Consumed means
    delivered to that proposal, never qualified, executed, or scientifically true.
    """
    from .science_graph_store import ScienceGraphStore
    from .goal_terminal import science_commit_barrier, assert_goal_open
    from .multi_worker import canvas_write_lock

    if not cycle_id:
        raise ValueError("Human conjecture claim requires Cycle identity")
    root = Path(research_root) if research_root is not None else Path(database).parent
    with science_commit_barrier(root, "shared"), canvas_write_lock(root):
        database = _require_existing_graph(database)
        assert_goal_open(root, database)
        with ScienceGraphStore(database) as store:
            with store.transaction():
                items = _directions(store, required_context)
                owned = next((item for item in items if item["cycle_id"] == cycle_id
                              and item["status"] in {"claimed", "consumed"}), None)
                if owned is not None:
                    return owned
                item = next((item for item in items if item["status"] == "pending"), None)
                if item is None:
                    return None
                detail = {"cycle_id": cycle_id, "source_references": item["source_references"]}
                store._event("external.human_conjecture.claimed", item["record"]["external_id"], detail)
                return {**item, "status": "claimed", "cycle_id": cycle_id}


def consume_conjecture(database: Path, *, external_id: str, cycle_id: str,
                       research_root: Path | None = None) -> None:
    from .science_graph_store import ScienceGraphStore
    from .goal_terminal import science_commit_barrier, assert_goal_open
    from .multi_worker import canvas_write_lock

    root = Path(research_root) if research_root is not None else Path(database).parent
    with science_commit_barrier(root, "shared"), canvas_write_lock(root):
        database = _require_existing_graph(database)
        assert_goal_open(root, database)
        with ScienceGraphStore(database) as store:
            with store.transaction():
                item = direction_status(store, external_id)
                if item["cycle_id"] != cycle_id or item["status"] not in {"claimed", "consumed"}:
                    raise ValueError("Human conjecture claim ownership changed")
                if item["status"] == "claimed":
                    store._event("external.human_conjecture.consumed", external_id,
                                 {"cycle_id": cycle_id, "source_references": item["source_references"],
                                  "meaning": "delivered_to_proposal_only"})
