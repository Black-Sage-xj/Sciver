"""Read-only CLI facade for TheoryMaster's canonical SQLite science graph.

This is a stable SDK/CLI tool surface for Agent-initiated detail reads. It is not
an MCP server; an MCP adapter can later wrap these same operations without
reimplementing graph semantics or adding another data store.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .science_graph_store import ScienceGraphStore


LEAD_MAINTENANCE_OPERATIONS = {
    "hypothesis_detail": "hypothesis-detail <H-ID>",
    "external_detail": "external-detail <E-ID>",
    "cycle_detail": "cycle-detail <Cycle-ID>",
    "global_view": "view [H-ID]",
}
LEAD_PROPOSAL_OPERATIONS = {
    "search": "search <query> [--offset N]",
    **LEAD_MAINTENANCE_OPERATIONS,
}
CHALLENGER_REVIEW_OPERATIONS = {
    "search": "search <query> [--offset N]",
    "hypothesis_detail": "hypothesis-detail <H-ID>",
    "external_detail": "external-detail <E-ID>",
    "cycle_detail": "cycle-detail <Cycle-ID>",
}


def tool_context(database: str | Path, operations: dict[str, str]) -> dict[str, Any]:
    """Stable read-only command block injected into an Agent stage context."""
    import shlex
    path = Path(database).expanduser().resolve()
    root = Path(__file__).resolve().parents[1]
    return {
        "database_path": str(path),
        "command_prefix": (
            f"PYTHONPATH={shlex.quote(str(root))}:${{PYTHONPATH:-}} "
            f"{shlex.quote(sys.executable)} -m theory_master.science_graph_tools "
            f"--database {shlex.quote(str(path))}"
        ),
        "operations": dict(operations),
        "access": "read_only",
        "history_navigation": "search locates H and matched Cycles; hypothesis-detail gives scientific records; cycle-detail gives original report/Workspace locators, including unadjudicated work. Read relevant originals when summaries/truncation cannot establish the old/new comparison. H IDs are not Cycle IDs; search is not Workspace full-text search.",
        "hypothesis_text_limit_chars": 2000,
        "external_content_limit_chars": 12000,
        "search_result_limit": 8,
        "search_pagination": "truncated/next_offset indicate more matches; pass --offset N to continue",
        "cycle_ref_root": "research_root from existing task Context; paths are relative and availability is unverified",
    }


def _bounded_json(value: Any, max_chars: int) -> Any:
    """Bound every free-text leaf and annotate fields containing truncation."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        truncated_fields: list[str] = []
        for key, item in value.items():
            bounded, truncated = _bounded_value(item, max_chars)
            result[key] = bounded
            if truncated:
                truncated_fields.append(key)
        if truncated_fields:
            result["truncated_fields"] = truncated_fields
        return result
    bounded, _ = _bounded_value(value, max_chars)
    return bounded


def _bounded_value(value: Any, max_chars: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        return (value[:max_chars], True) if len(value) > max_chars else (value, False)
    if isinstance(value, list):
        rows = []
        truncated = False
        for item in value:
            bounded, item_truncated = _bounded_value(item, max_chars)
            rows.append(bounded)
            truncated = truncated or item_truncated
        return rows, truncated
    if isinstance(value, dict):
        bounded = _bounded_json(value, max_chars)
        return bounded, bool(bounded.get("truncated_fields"))
    return value, False


def execute(database: str | Path, operation: str, identifier: str | None = None,
            *, recent_outcomes: int = 3, max_content_chars: int = 12_000,
            offset: int = 0) -> dict[str, Any]:
    """Run one bounded read against a canonical SQLite database."""
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "database_unavailable", "error": "SQLite database unavailable"}
    try:
        with ScienceGraphStore(path, readonly=True) as store:
            if operation == "view":
                result = store.agent_view(entry_hypothesis_id=identifier or None)
            elif operation == "hypothesis-detail":
                if not identifier:
                    return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "invalid_arguments", "error": "hypothesis-detail requires an H ID"}
                result = store.agent_hypothesis_detail(identifier, recent_outcomes=max(0, min(10, recent_outcomes)))
            elif operation == "external-detail":
                if not identifier:
                    return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "invalid_arguments", "error": "external-detail requires an External ID"}
                result = store.agent_external_detail(identifier, max_content_chars=max(0, min(24_000, max_content_chars)))
            elif operation == "cycle-detail":
                if not identifier:
                    return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "invalid_arguments", "error": "cycle-detail requires a Cycle ID"}
                result = store.agent_cycle_detail(identifier)
            elif operation == "search":
                result = store.agent_search(identifier or "", offset=offset)
            else:
                return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "invalid_arguments", "error": f"unknown operation: {operation}"}
    except KeyError as exc:
        return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "not_found", "error": str(exc), "id": identifier}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"schema": "theorymaster/science-graph-tool-result/v1", "status": "database_error", "error": "read-only SQLite query failed"}
    return _bounded_json(result, max_chars=2_000) if operation == "hypothesis-detail" else result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only tools for TheoryMaster's SQLite science graph")
    parser.add_argument("--database", required=True, help="Canonical science_graph.sqlite3 path")
    parser.add_argument("--recent-outcomes", type=int, default=3, help="Recent Cycle Outcomes for hypothesis-detail (0..10)")
    parser.add_argument("--max-content-chars", type=int, default=12_000, help="External source text cap (0..24000)")
    parser.add_argument("--offset", type=int, default=0, help="Search result pagination offset")
    parser.add_argument("operation", choices=("view", "hypothesis-detail", "external-detail", "cycle-detail", "search"))
    parser.add_argument("id", nargs="?", help="H ID, External ID, or search query; optional for view")
    args = parser.parse_args(argv)
    result = execute(args.database, args.operation, args.id,
                     recent_outcomes=args.recent_outcomes,
                     max_content_chars=args.max_content_chars, offset=args.offset)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") not in {"not_found", "database_unavailable", "database_error", "invalid_arguments"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
