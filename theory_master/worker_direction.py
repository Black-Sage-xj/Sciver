"""Research direction wake conditions and operator guidance."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any


_INPUT_IDS = "verified_research_input_ids"


def _verified_input_ids(fingerprint: Any) -> frozenset[str] | None:
    if not isinstance(fingerprint, dict):
        return None
    identities = fingerprint.get(_INPUT_IDS)
    if not isinstance(identities, list) or not all(
            isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value) for value in identities):
        return None
    return frozenset(identities)


def _waiting_fingerprint(root: Path, previous: Any, wait_key: tuple, *, establish_from: Any = None) -> Any:
    """Bind the first verified inventory to a specific old wait without editing its Cycle."""
    if _direction_values(previous) is None or _verified_input_ids(previous) is not None:
        return previous
    from .invocations import stable_hash
    from .runtime import write_json

    owner = stable_hash([list(wait_key), previous])
    base = root / ".theory-master" / "research_input_wait_baselines"
    path = base / (owner + ".json")
    try:
        if any(part.is_symlink() for part in (path, base, base.parent)):
            return previous
        if path.is_file():
            with path.open("rb") as handle:
                raw = handle.read(65_537)
            if len(raw) > 65_536:
                return previous
            record = json.loads(raw)
            if (isinstance(record, dict) and record.get("wait_key") == owner
                    and _verified_input_ids(record) is not None):
                return {**previous, _INPUT_IDS: record[_INPUT_IDS]}
            return previous
        new_ids = _verified_input_ids(establish_from)
        if not path.exists() and new_ids is not None:
            write_json(path, {"wait_key": owner, _INPUT_IDS: sorted(new_ids)})
    except (OSError, ValueError):
        pass
    return previous


def qualification_wait_fingerprint(root: Path, state: dict, *, establish_from: Any = None) -> Any:
    """Read or establish the baseline for exactly one persisted whole-task review."""
    review = state.get("supervisor_qualification") or {}
    previous = review.get("direction_fingerprint")
    if not review.get("session_id") or not state.get("cycle_id"):
        return previous
    return _waiting_fingerprint(
        root, previous, ("supervisor", state["cycle_id"], review["session_id"]),
        establish_from=establish_from,
    )


def research_direction_fingerprint(spec: Any) -> dict[str, Any]:
    """G6 wake authorities, including currently available verified input identities."""
    from . import multi_worker, worker_state, worker_store
    from .research_inputs import qualification_input_ids
    import sqlite3
    root = Path(spec.research_root).resolve()
    ids: list[str] = []
    graph = worker_store._graph_path(spec)
    if graph.is_file():
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph, readonly=True) as store:
            try:
                ids = sorted(str(row[0]) for row in store.db.execute("SELECT id FROM cycle_evaluations"))
            except sqlite3.OperationalError:
                ids = []
    from .scientific_model import state_path
    model_state = state_path(root)
    version = multi_worker._read_json(model_state).get("version") if model_state.is_file() else None
    plan = Path(spec.document)
    input_ids = qualification_input_ids(spec)
    return {
        "schema": worker_state.DIRECTION_FINGERPRINT_SCHEMA,
        "committed_outcome_count": len(ids),
        "committed_outcome_ids_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "scientific_model_version": version if isinstance(version, int) else None,
        "research_plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest() if plan.is_file() else None,
        _INPUT_IDS: input_ids,
        "research_inputs_sha256": hashlib.sha256("\n".join(input_ids).encode()).hexdigest(),
    }


def _direction_values(fingerprint: Any) -> tuple[Any, ...] | None:
    from . import worker_state
    if not isinstance(fingerprint, dict) or not all(k in fingerprint for k in worker_state.DIRECTION_FINGERPRINT_FIELDS):
        return None
    values = tuple(fingerprint[k] for k in worker_state.DIRECTION_FINGERPRINT_FIELDS)
    count, outcomes, version, plan = values
    if (type(count) is not int or count < 0 or not isinstance(outcomes, str) or not outcomes
            or version is not None and (type(version) is not int or version < 0)
            or not isinstance(plan, str) or not plan):
        return None
    return values


def added_research_input_ids(previous: Any, current: Any) -> frozenset[str]:
    old_inputs, new_inputs = _verified_input_ids(previous), _verified_input_ids(current)
    return new_inputs - old_inputs if old_inputs is not None and new_inputs is not None else frozenset()


def omitted_qualification_input_ids(root: Path, state: dict[str, Any], current: Any, *,
                                    excluded_sha256: frozenset[str] = frozenset()) -> frozenset[str]:
    """Recover new source text skipped by the previous qualification projection."""
    from .public_submission import read_bounded_regular_file
    from .supervisor_intervention import SESSION_SCHEMA
    from .task_progression import local_replan_reason

    review = state.get("supervisor_qualification") or {}
    cycle = str(state.get("cycle_id") or "")
    source = str(review.get("source_cycle") or "")
    session_id = review.get("session_id")
    if (review.get("source_context_version") != 2 or review.get("status") != "waiting"
            or not session_id or not cycle.isascii() or not cycle.isdecimal()
            or not source.isascii() or not source.isdecimal() or not 0 < int(source) < int(cycle)
            or local_replan_reason(state) != "no_qualified_candidate"):
        return frozenset()
    try:
        previous = json.loads(read_bounded_regular_file(root / source / "research_state.json", 2_000_000))
        if (str(previous.get("cycle_id")) != source or previous.get("worker_id") != state.get("worker_id")
                or local_replan_reason(previous) != "no_qualified_candidate"):
            return frozenset()
        added = (added_research_input_ids(qualification_wait_fingerprint(root, previous),
                                         qualification_wait_fingerprint(root, state))
                 & (_verified_input_ids(current) or frozenset())) - excluded_sha256
        if not added:
            return frozenset()
        raw = read_bounded_regular_file(root / cycle / "supervisor" / "session.jsonl", 2_000_000)
        prompts = []
        for line in raw.splitlines():
            entry = json.loads(line)
            if (entry.get("schema") == SESSION_SCHEMA and entry.get("session_id") == session_id
                    and entry.get("profile") == "attention" and entry.get("code") == "no_qualified_candidate"
                    and entry.get("kind") == "prompt" and entry.get("role") == "attention_reviewer"):
                prompts.append(entry["content"])
        if not prompts:
            return frozenset()
        payload = json.loads(prompts[0].partition("\nTask evidence:\n")[2])
        sources = payload.get("verified_public_sources") or []
        eligible = {
            row["sha256"] for row in sources if row.get("sha256") in added
            and row.get("kind") in {"fixture_input", "supplemental_input"}
            and type(row.get("size")) is int and 0 < row["size"] <= 12_000
            and Path(str(row.get("path") or "")).suffix in {".json", ".md", ".py"}
        }
        inlined = {row["sha256"] for row in sources if row.get("sha256") in eligible and row.get("source_text")}
        return frozenset(eligible - inlined)
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return frozenset()


def direction_changed(previous: Any, current: Any) -> bool:
    """Missing new fields in an old fingerprint are not evidence of a change."""
    before, after = _direction_values(previous), _direction_values(current)
    if before is None or after is None:
        return False
    if before != after:
        return True
    return bool(added_research_input_ids(previous, current))


def _retry_direction_path(root: Path, worker_id: str) -> Path:
    from . import worker_store
    return worker_store.worker_dir(root, worker_id) / "retry_direction.json"


def _pending_retry_direction(root: Path, worker_id: str) -> dict[str, Any] | None:
    from . import multi_worker, worker_store
    request = multi_worker._read_json(_retry_direction_path(root, worker_id))
    if request.get("status") != "pending":
        return None
    if request.get("source") == "supervisor":
        from .supervisor_activation import dormant_qualification_cycle
        from .supervisor_intervention import supervisor_enabled
        worker = worker_store.read_worker(root, worker_id)
        cycle = dormant_qualification_cycle(root, worker) if supervisor_enabled(root) else None
        if (cycle is None or request.get("waiting_cycle") != cycle
                or request.get("wake_fingerprint_at_request") != worker.get("wake_fingerprint")):
            return None
    return request


def _waiting_cycle_state(root: Path, worker: dict[str, Any]) -> tuple[Path, dict] | None:
    from . import multi_worker
    cycle = str(worker.get("current_cycle") or "")
    if (worker.get("claim_id") or worker.get("epoch") is not None
            or not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1):
        return None
    directory, path = root / cycle, root / cycle / "research_state.json"
    try:
        if (directory.is_symlink() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > 1_048_576):
            return None
        state = multi_worker._read_json(path)
    except (OSError, ValueError):
        return None
    if (state.get("worker_id") != worker.get("worker_id")
            or str(state.get("cycle_id") or "") != cycle):
        return None
    return directory, state


def _waiting_local_replan_reason(root: Path, worker: dict[str, Any], *, spec: Any = None,
                                current_fingerprint: Any = None) -> str | None:
    """Classify a waiting attempt without reopening or editing its Cycle."""
    from .task_progression import local_replan_reason, qualification_replan_allowed

    waiting = _waiting_cycle_state(root, worker)
    if waiting is None:
        return None
    directory, state = waiting
    reason = local_replan_reason(state)
    if reason == "no_qualified_candidate" and (spec is None or not qualification_replan_allowed(
            spec, state, current_fingerprint=current_fingerprint)):
        return None
    if reason == "input_support_unavailable":
        from .supervisor_input_support import input_support_recovery

        review = input_support_recovery(directory, state, establish_baseline=True, spec=spec,
                                        worker_id=worker.get("worker_id"))
        if not review or review["route"] != "input_support":
            return "input_support_wait"
    return reason



def _worker_input_wake(spec: Any, worker: dict) -> dict | None:
    from .supervisor_input_support import input_support_recovery

    waiting = _waiting_cycle_state(Path(spec.research_root).resolve(), worker)
    if waiting is None:
        return None
    directory, state = waiting
    plan = input_support_recovery(directory, state, spec=spec, worker_id=worker["worker_id"])
    if not plan or plan["route"] != "input_support":
        return None
    return {"cycle_id": directory.name, "dependency_identity": plan["dependency_identity"],
            "wake_identity": plan["wake_identity"], "wake_fingerprint": worker.get("wake_fingerprint"),
            "waiting_since": worker.get("waiting_since")}

def _partition_waiting_workers(spec: Any, workers: list[dict[str, Any]]
                               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Wake waiting Workers for a new direction, explicit retry or verified local replan.

    An unknown fingerprint supplies no wake signal. A canonical completed local
    failure may independently return to Lead under the daemon's remaining quota.
    """
    from . import worker_state
    root = Path(spec.research_root).resolve()
    waiting = [w for w in workers if w.get("status") == worker_state.WAITING_RESEARCH_DIRECTION
               and not w.get("dismissed") and not w.get("process_alive")]
    if not waiting:
        return [], [], None
    try:
        current = research_direction_fingerprint(spec)
    except (OSError, ValueError):
        current = None
    woken, dormant = [], []
    for worker in waiting:
        retry = _pending_retry_direction(root, worker["worker_id"])
        local_replan = _waiting_local_replan_reason(root, worker, spec=spec,
                                                   current_fingerprint=current)
        previous = _waiting_fingerprint(
            root, worker.get("wake_fingerprint"),
            ("worker", worker["worker_id"], worker.get("current_cycle"), worker.get("waiting_since")),
            establish_from=current,
        )
        bound_input_wait = worker.get("input_support_wait_cycle") == str(worker.get("current_cycle") or "")
        input_wake = None
        if not retry and (bound_input_wait or local_replan in {"input_support_wait", "input_support_unavailable"}):
            input_wake = _worker_input_wake(spec, worker) if local_replan == "input_support_unavailable" else None
            if input_wake is None:
                dormant.append(worker)
                continue
        changed = direction_changed(previous, current)
        if retry or changed or local_replan:
            woken.append({**worker, "wake_reason": "retry_direction" if retry
                          else "local_replan:" + local_replan if input_wake
                          else "direction_fingerprint_changed" if changed
                          else "local_replan:" + local_replan,
                          **({"input_support_wake": input_wake} if input_wake else {})})
        else:
            dormant.append(worker)
    return woken, dormant, current


def _notify_all_waiting(root: Path, dormant: list[dict[str, Any]],
                        current: dict[str, Any] | None) -> dict[str, Any]:
    """One important event per all-waiting episode (deduplicated by registry key)."""
    import hashlib
    from .observability.incident_registry import register_cycle_incident
    ids = sorted(w["worker_id"] for w in dormant)
    episode = json.dumps({"workers": ids, "fingerprint": _direction_values(current),
                          "waiting": sorted(str(w.get("waiting_since") or "") for w in dormant)},
                         sort_keys=True, default=str)
    incident, created = register_cycle_incident(
        root, severity="important", kind="all_workers_waiting_research_direction",
        source_event="sdk.worker_runtime",
        summary=("All living Workers are waiting for a research direction: " + ", ".join(ids)
                 + ". Change the Research Plan, add operator guidance or request retry_direction."),
        code="all_workers_waiting_research_direction",
        discriminator=hashlib.sha256(episode.encode("utf-8")).hexdigest()[:20],
        fields={"worker_ids": ids, "event_class": "research_direction_notice"},
    )
    return {"incident_id": incident.get("incident_id"), "created": created, "worker_ids": ids}


def detect_critical_knowledge_change(spec: Any, *, plan_digest: str,
                                     hypothesis_id: str | None = None,
                                     cycle_id: str | None = None,
                                     entry_node_id: str | None = None) -> dict[str, Any] | None:
    """G10: only a Research Plan change, or this Cycle's OWN bound H deleted/closed.

    Parent H or other frontier changes are ordinary science (I-06). Closure by
    this Cycle's own committed Outcome or by the Goal seal is not a notice.
    """
    from . import worker_store
    import hashlib
    hypothesis_id = hypothesis_id or entry_node_id
    plan = Path(spec.document)
    if hashlib.sha256(plan.read_bytes()).hexdigest() != plan_digest:
        return {"reason": "research_plan_changed", "hypothesis_id": hypothesis_id}
    if not hypothesis_id:
        return None
    graph_path = worker_store._graph_path(spec).resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(f"Canonical SQLite graph is required: {graph_path}")
    from .science_graph_store import GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON, ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        if store.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone() is None:
            return {"reason": "own_hypothesis_deleted", "hypothesis_id": hypothesis_id}
        frontier = store.frontier_view().get(hypothesis_id)
        if not frontier or frontier["frontier_status"] != "closed":
            return None
        branch = frontier["branches"][0] if frontier["branches"] else {}
        if branch.get("reason") in {GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON}:
            return None
        if cycle_id and store.db.execute(
                "SELECT 1 FROM cycle_evaluations WHERE cycle_id=? AND hypothesis_id=?",
                (str(cycle_id), hypothesis_id)).fetchone() is not None:
            return None
    return {"reason": "own_hypothesis_closed", "hypothesis_id": hypothesis_id}


def _restore_cycle_operator_guidance(state: dict[str, Any], cycle_dir: Path) -> dict[str, Any]:
    """Inject pending human guidance when Worker resumes an existing Cycle."""
    from .resume_preparation import restore_operator_guidance
    return restore_operator_guidance(state, cycle_dir)
