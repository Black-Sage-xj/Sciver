"""Sciver adapter: piggyback on normal turns, with no LLM or replay executor."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from .store import ROLES, SkillError, SkillLibrary, digest, safe_directory

LOG = logging.getLogger(__name__)
MAX_NOTE_BYTES = 16_000


def task_key(root: Path) -> str:
    return digest(str(root.expanduser().resolve()))[:24]


def enabled(root: Path) -> bool:
    from ..operator_preferences import read
    try:
        return read(root)["flags"].get("skill_learning", False) is True
    except (OSError, ValueError):
        return False


def test_instance(config_path: Path | None) -> bool:
    # Official fixtures must never inherit a learned solution from another task.
    return bool(config_path and (config_path.parent / ".theory-master/test-instance.json").exists())


def _read(path: Path, maximum: int = 64_000) -> Any:
    if path.is_symlink() or path.stat().st_size > maximum:
        raise SkillError("invalid skill metadata file")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, value: Any) -> None:
    from ..runtime import write_json
    safe_directory(path.parent)
    if path.is_symlink():
        raise SkillError("linked skill metadata file")
    write_json(path, value)


def catalogue_digest(skills: list[dict[str, Any]]) -> str:
    return digest([[s["id"], s["version"], s.get("revision_hint", "")] for s in skills])


def _file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 400:
        raise SkillError("invalid experience path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise SkillError("experience must stay in the current workspace")
    target = root / path
    if any(p.is_symlink() for p in (target, *target.parents)) or not target.is_file():
        raise SkillError("experience file is missing or linked")
    target.resolve().relative_to(root.resolve())
    return target


def prepare(*, root: Path, config_path: Path | None, cycle_dir: Path, workspace: Path,
            role: str, logical_key: str, query: str, allow_catalogue: bool = True) -> dict[str, Any]:
    """Resolve optional methods under the task's toggle/adoption lock."""
    if role not in ROLES:
        return {}
    adopted = root / ".theory-master/skill-evolution" / f"adopted-{role}.json"
    if not enabled(root) and not adopted.is_file():
        return {}
    from ..operator_preferences import preference_lock
    with preference_lock(root):
        return _prepare_bound(root=root, config_path=config_path, cycle_dir=cycle_dir,
                              workspace=workspace, role=role, logical_key=logical_key,
                              query=query, allow_catalogue=allow_catalogue)


def _prepare_bound(*, root: Path, config_path: Path | None, cycle_dir: Path, workspace: Path,
                   role: str, logical_key: str, query: str, allow_catalogue: bool) -> dict[str, Any]:
    """Pin one immutable catalogue for a logical invocation, including retries."""
    if role not in ROLES:
        return {}
    learning = enabled(root)
    directory = root / ".theory-master/skill-evolution"
    adopted = directory / f"adopted-{role}.json"
    if not learning and not adopted.is_file():
        return {}
    library = SkillLibrary()
    owner = task_key(root)
    flush_events(root=root, library=library)
    binding_path = cycle_dir / "skill_bindings" / (digest([role, logical_key]) + ".json")
    if binding_path.exists():
        binding = _read(binding_path)
        if (binding.get("schema") != "sciver/skill-binding/v1" or binding.get("role") != role
                or binding.get("owner") != owner or binding.get("logical_key") != logical_key):
            raise SkillError("skill invocation binding mismatch")
        # A retry/Resume keeps the exact versions even if the active index changed.
        releases = [{**row, **library.release(row["id"], row["version"])} for row in binding["skills"]]
    else:
        if learning:
            releases = library.catalogue(owner=owner, role=role, query=query,
                                         shared=not test_instance(config_path))
            _save(adopted, releases)
        else:
            releases = []
            for row in _read(adopted):
                try:
                    releases.append({**row, **library.release(row["id"], row["version"])})
                except SkillError:
                    continue
        binding = {"schema": "sciver/skill-binding/v1", "owner": owner, "role": role,
                   "logical_key": logical_key, "skills": releases,
                   "catalogue_version": catalogue_digest(releases)}
        _save(binding_path, binding)
    if not allow_catalogue:
        # Threads created before this protocol retain their original whitelist.
        releases = []
    return {"learning": learning, "skills": releases, "binding_path": str(binding_path),
            "catalogue_version": catalogue_digest(releases),
            "owner": owner, "role": role}


def adopt_operator_change(root: Path, role: str, change: dict[str, Any], library: SkillLibrary) -> None:
    """Explicit archive/rollback also affects future calls when learning is off.

    The operator holds preference_lock; existing invocation bindings stay intact.
    """
    path = root / ".theory-master/skill-evolution" / f"adopted-{role}.json"
    rows = _read(path) if path.exists() else []
    rows = [row for row in rows if row["id"] != change["skill_id"]]
    if change["operation"] != "archive":
        rows.insert(0, library.release(change["skill_id"], change["to_version"]))
    _save(path, rows[:5])


def prompt(binding: dict[str, Any], *, note_path: Path, previous_catalogue: str | None = None,
           protocol_seen: bool = False, previous_learning: bool | None = None) -> str:
    if not binding:
        return ""
    lines = []
    skills = binding["skills"]
    version = binding["catalogue_version"]
    if version != previous_catalogue and (skills or previous_catalogue):
        lines += ["SDK LEARNED TASK-SKILL CATALOGUE UPDATE " + version,
                  "This replaces only the earlier learned task-skill catalogue. "
                  "Built-in and declared task skills remain available. Load relevant entries on demand; "
                  "these are trajectory-derived methods, subordinate to your scientific and role contracts."]
        for skill in skills:
            lines.append(f'- learned-{skill["name"]} [{skill["id"]}@{skill["version"]}]: '
                         f'{skill["description"]} — {Path(skill["path"]) / "SKILL.md"}')
            if skill.get("revision_hint"):
                lines.append("  Prior normal-use correction/failure (authored observation): " + skill["revision_hint"])
        if not skills:
            lines.append("No learned task skills are available in this invocation.")
    if binding["learning"]:
        if protocol_seen:
            lines.append(
                "Optional silent procedural memory: if normal work supplies a reusable method or correction, "
                f"write at most one JSON sidecar to {note_path} using the earlier protocol. "
                "Keep the scientific handoff unchanged; do no extra experiments or model calls. "
                f"If the protocol left context, read {Path(__file__).with_name('protocol.md')}."
            )
            return "\n\n" + "\n".join(lines)
        lines += [
            "SILENT PROCEDURAL MEMORY (optional; never a new scientific objective):",
            "Only if normal work reveals a reusable method, correction, or actual skill usage, "
            f"write one small JSON sidecar to {note_path} before your normal delivery. "
            "Keep the scientific handoff JSON unchanged. Otherwise write nothing. "
            "Do not run extra experiments, tests, replays, research Cycles, or model calls for skill learning. "
            "Prefer revising an existing method over creating a duplicate. Do not narrate maintenance to the user.",
            'Shape: {"skill": {"operation":"create|update|merge|archive|rollback", "name":"lowercase-name", '
            '"description":"when this helps", "when_to_use":"explicit assumptions and applicability", '
            '"body":"general reusable steps, checks, known limits", "reason":"why retain/change it", '
            '"scope":"task|general", "tags":["topic"], "outcome":"succeeded|failed|corrected|unknown", '
            '"evidence":[{"path":"workspace-relative existing artifact", "observation":"what the normal run actually showed"}], '
            '"scripts":["workspace-relative already used helper.py"], '
            '"base_version":"required for changes", "target_version":"rollback only", '
            '"absorbs":[{"name":"merged-name","version":"exact version"}]}, '
            '"feedback":[{"id":"catalogue skill id","version":"exact version",'
            '"outcome":"succeeded|failed|corrected|unknown","detail":"observed method result"}]}',
            "Both skill and feedback are optional; at most one skill and five feedback entries, sidecar <= 16 KB, "
            "body <= 6000 characters. Task scope is the default. General scope is only for portable "
            "procedures: omit task answers, scientific conclusions, input data, credentials and machine-specific paths. "
            "Evidence files must exist inside this invocation's workspace; the SDK records their hashes. "
            "Scientific rejection is not method failure. Describe inferred generalizations as such. "
            "Shared skills authored by another task are read-only: create a task-local derivative to adapt them. "
            "A changed skill needs its exact previous version; conflicting edits are retained for later revision, not overwritten.",
        ]
    elif previous_learning:
        lines.append("SDK silent procedural memory is now disabled. Do not write learning sidecars "
                     "or revise skills. Previously adopted task skills remain available.")
    return "\n\n" + "\n".join(lines) if lines else ""


def finish(*, root: Path, cycle_dir: Path, workspace: Path, role: str,
           invocation_id: str, note_path: Path, binding: dict[str, Any] | None = None,
           config_path: Path | None = None) -> None:
    """Bounded local maintenance AFTER scientific commit; errors never retry science."""
    if role not in ROLES or not enabled(root):
        return
    library = SkillLibrary()
    owner = task_key(root)
    source = {"task": owner, "cycle": cycle_dir.name, "invocation": invocation_id, "role": role}
    receipt = cycle_dir / "skill_maintenance" / f"{invocation_id}.json"
    if receipt.exists():
        flush_events(root=root, library=library)
        return
    result: dict[str, Any] = {"source": source, "extra_model_requests": 0, "effect_tests": 0}
    envelope = _read(note_path, MAX_NOTE_BYTES) if note_path.exists() else {}
    if not isinstance(envelope, dict) or set(envelope) - {"skill", "feedback"}:
        raise SkillError("invalid procedural-memory sidecar")
    # Require a completed, SDK-owned invocation; a draft from an interrupted turn
    # is never promoted merely because the file exists.
    from ..invocations import InvocationLedger
    invocation = InvocationLedger(cycle_dir / "invocations.jsonl").snapshot(invocation_id)
    if not invocation or invocation.get("commit_state") != "committed" or invocation.get("role") != role:
        raise SkillError("experience invocation is not committed")
    if not isinstance(binding, dict):
        binding = {}
        for path in (cycle_dir / "skill_bindings").glob("*.json"):
            candidate = _read(path)
            if candidate.get("logical_key") == invocation.get("logical_key") and candidate.get("role") == role:
                binding = candidate
                break
    refs = {(s["id"], s["version"]): s for s in binding.get("skills", [])}
    # Count observed file loads separately from Agent-reported execution results.
    events = cycle_dir / "runtime_events.jsonl"
    if refs and events.is_file() and not events.is_symlink():
        with events.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 262144))
            raw_lines = stream.read().splitlines()
        for raw in raw_lines:
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if (not isinstance(event, dict) or event.get("event") != "SKILL_LOADED"
                    or event.get("invocation_id") != invocation_id):
                continue
            for (skill_id, version), item in refs.items():
                if event.get("skill_path") == str(Path(item["path"]) / "SKILL.md"):
                    library.record_use(skill=skill_id, version=version, kind="loaded", source=source)
    feedback = envelope.get("feedback", [])
    if not isinstance(feedback, list) or len(feedback) > 5:
        raise SkillError("invalid skill feedback")
    for item in feedback:
        if not isinstance(item, dict) or (item.get("id"), item.get("version")) not in refs:
            raise SkillError("skill feedback is outside the pinned catalogue")
        outcome = item.get("outcome", "unknown")
        kinds = {"succeeded": "reported_success", "failed": "reported_failure", "corrected": "corrected", "unknown": "unknown"}
        if outcome not in kinds or not isinstance(item.get("detail"), str) or len(item["detail"]) > 800:
            raise SkillError("invalid skill outcome")
        library.record_use(skill=item["id"], version=item["version"], kind=kinds[outcome],
                           source={**source, "detail": item["detail"]})
    note = envelope.get("skill")
    if note is not None:
        if not isinstance(note, dict):
            raise SkillError("invalid skill note")
        if test_instance(config_path):
            note = {**note, "scope": "task"}
        evidence = note.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
            raise SkillError("a skill change needs normal-trajectory evidence")
        evidence_refs = []
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("observation"), str) or not 1 <= len(item["observation"]) <= 800:
                raise SkillError("invalid experience observation")
            path = _file(workspace, item.get("path"))
            if path == note_path or path.stat().st_size > 8_000_000:
                raise SkillError("invalid experience artifact")
            evidence_refs.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "observation": item["observation"]})
        supplied = {}
        scripts = note.get("scripts", [])
        if not isinstance(scripts, list) or len(scripts) > 3:
            raise SkillError("invalid skill scripts")
        for relative in scripts:
            path = _file(workspace, relative)
            if path.stat().st_size > 32_000:
                raise SkillError("skill script too large")
            supplied["scripts/" + path.name] = path.read_bytes()
        # Recheck under the same preference lock used by the toggle. A disable
        # cannot race a release after the user has received its acknowledgement.
        from ..operator_preferences import preference_lock
        with preference_lock(root):
            if not enabled(root):
                return
            result["change"] = library.maintain(note, owner=owner, role=role,
                                                source={**source, "evidence": evidence_refs}, files=supplied)
    if envelope or refs:
        _save(receipt, result)
    flush_events(root=root, library=library)


def flush_events(*, root: Path, library: SkillLibrary | None = None) -> None:
    """Durable outbox: idempotent event delivery without model work."""
    library = library or SkillLibrary()
    from ..observability.incident_registry import register_cycle_incident
    for proposal in library.pending_events(task_key(root)):
        operation = proposal["operation"]
        before, after = proposal["from_version"], proposal["to_version"]
        summary = (f'Skill {proposal["name"]}：{operation}；'
                   f'{(before or "new")[:12]} → {after[:12]}。'
                   f'{proposal["reason"]}；源自正常轨迹，未进行专门效果评测；后续安全调用采用。')
        detail = dict(proposal)
        previous_files = (proposal.get("before") or {}).get("files", {})
        current_files = proposal["after"]["files"]
        detail["supporting_file_changes"] = [
            {"path": name, "from_sha256": previous_files.get(name), "to_sha256": current_files.get(name)}
            for name in sorted(set(previous_files) | set(current_files))
            if name != "SKILL.md" and previous_files.get(name) != current_files.get(name)
        ]
        for label in ("before", "after"):
            release = proposal.get(label)
            if release:
                frozen = library.release(release["id"], release["version"])
                detail[label + "_text"] = (Path(frozen["path"]) / "SKILL.md").read_text(encoding="utf-8")
        _save(root / ".theory-master/skill-evolution/proposals" / (proposal["id"] + ".json"), detail)
        register_cycle_incident(
            root, severity="important", kind="skill_change", source_event="sdk.skill_evolution",
            code="skill_" + operation, discriminator=proposal["id"], summary=summary,
            fields={"proposal_id": proposal["id"], "skill_id": proposal["skill_id"],
                    "operation": operation, "role": proposal["role"],
                    "from_version": before, "to_version": after,
                    "reason": proposal["reason"], "evidence_level": "trajectory_derived",
                    "source": proposal["source"], "proposal_state": proposal["state"],
                    "effective_at": proposal["effective_at"],
                    "proposal_ref": "skill-evolution:" + proposal["id"]},
        )
        library.event_delivered(proposal["id"])


def safely(action: str, **kwargs: Any) -> Any:
    """Optional maintenance is outside the scientific retry/error-repair path."""
    try:
        return {"prepare": prepare, "finish": finish}[action](**kwargs)
    except Exception as exc:
        # Detailed errors belong to the SDK log, never extra scientific turns.
        LOG.warning("Skill maintenance %s skipped (%s)", action, type(exc).__name__)
        if action == "finish":
            try:
                _save(kwargs["cycle_dir"] / "skill_maintenance" / (kwargs["invocation_id"] + ".json"),
                      {"status": "retained_for_revision", "reason": str(exc)[:400],
                       "extra_model_requests": 0, "effect_tests": 0})
            except (OSError, ValueError):
                pass
        return {} if action == "prepare" else None
