"""Operator controls and read-only inspection; never starts research or a model."""
from __future__ import annotations

import json
import uuid
from typing import Any


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("skills", help="control silent trajectory-derived skill learning")
    parser.add_argument("skill_action", choices=["status", "on", "off", "list", "proposals", "show", "archive", "rollback"])
    parser.add_argument("master_id", nargs="?")
    parser.add_argument("--proposal")
    parser.add_argument("--name")
    parser.add_argument("--role", choices=["investigator", "literature"], default="investigator")
    parser.add_argument("--version")
    parser.add_argument("--reason", default="Operator requested skill maintenance")


def run(args: Any) -> int:
    from ..cli_discovery import spec_from_task
    from ..operator_preferences import write, preference_lock
    from .runtime import enabled, flush_events, task_key, adopt_operator_change
    from .store import SkillError, SkillLibrary
    try:
        spec = spec_from_task(master_id=args.master_id)
        owner = task_key(spec.research_root)
        library = SkillLibrary()
        action = args.skill_action
        if action in {"on", "off"}:
            write(spec.research_root, kind="flag", key="skill_learning", value=action == "on")
            result = {"enabled": action == "on", "effective_at": "next_safe_invocation"}
        elif action == "status":
            result = {"enabled": enabled(spec.research_root), "extra_model_requests": 0,
                      "effect_tests": False, "library": str(library.home)}
        elif action == "list":
            result = library.catalogue(owner=owner, role=args.role, shared=False, limit=200)
        elif action in {"proposals", "show"}:
            result = library.proposals(owner)
            if action == "show":
                result = next((p for p in result if p["id"] == args.proposal), None)
                if result is None:
                    raise SkillError("unknown task skill proposal; provide --proposal SKP-...")
        else:
            current = library.owned(owner=owner, role=args.role, name=args.name)
            if current is None:
                raise SkillError("unknown task-owned skill; provide --name")
            with preference_lock(spec.research_root):
                result = library.maintain(
                    {"operation": action, "name": args.name, "base_version": current["version"],
                     "target_version": args.version, "reason": args.reason},
                    owner=owner, role=args.role,
                    source={"operator": "cli", "task": owner, "request": uuid.uuid4().hex},
                )
                if result["status"] == "applied":
                    adopt_operator_change(spec.research_root, args.role, result, library)
            flush_events(root=spec.research_root, library=library)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Sciver skills: {exc}")
        return 2
