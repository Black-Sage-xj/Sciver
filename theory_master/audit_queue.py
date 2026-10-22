"""Small, independent register for non-TMCR TheoryMaster audit follow-ups.

Formal TMCRs are governed by change_requests/待审查清单.md instead.
This queue records *audit findings*, never grants approval of a scientific
verdict or creates a formal change request.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

SCHEMA = "theory-master/audit-review-queue/v1"
DEFAULT_STORE = Path(__file__).resolve().parents[1] / "audit" / "pending_reviews.json"
PRIORITIES = ("P0", "P1", "P2")
CHECK_RESULTS = ("pending", "pass", "fail", "blocked", "na")


class QueueError(ValueError):
    """Invalid request or unsafe audit-register state."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _nonempty(value: str | None, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise QueueError(f"{name} cannot be empty")
    return result


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"schema": SCHEMA, "next_sequence": 1, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QueueError(f"unable to read audit registry {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise QueueError("unsupported or malformed audit queue schema; refusing to overwrite")
    if (not isinstance(data.get("next_sequence"), int)
            or data["next_sequence"] < 1
            or not isinstance(data.get("items"), list)):
        raise QueueError("audit queue has invalid sequence/items; refusing to overwrite")
    ids = [item.get("id") for item in data["items"] if isinstance(item, dict)]
    if len(ids) != len(data["items"]) or len(ids) != len(set(ids)):
        raise QueueError("audit queue contains corrupt or duplicate entries")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        name = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


@contextmanager
def _edit(path: Path) -> Iterator[dict]:
    # Stable, cross-process lock independent of the checked-in audit directory.
    digest = hashlib.sha256(str(path.expanduser().resolve()).encode()).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"tm-audit-queue-{digest}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = _read(path)
            yield data
            _atomic_write(path, data)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _item(data: dict, identifier: str) -> dict:
    for record in data["items"]:
        if record["id"] == identifier:
            return record
    raise QueueError(f"unknown audit item: {identifier}")


def _event(item: dict, action: str, **detail: object) -> None:
    timestamp = _now()
    item["updated_at"] = timestamp
    item.setdefault("history", []).append({"at": timestamp, "action": action, **detail})


def _open_status(checks: list[dict]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "failed"
    if any(check["status"] == "blocked" for check in checks):
        return "blocked"
    return "pending"


def _add(args: argparse.Namespace, data: dict) -> dict:
    checks = [_nonempty(text, "check") for text in args.check]
    title = _nonempty(args.title, "title")
    source = _nonempty(args.source, "source")
    duplicate = next((item for item in data["items"]
                      if item["title"].casefold() == title.casefold()
                      and item["source"] == source), None)
    if duplicate is not None:
        raise QueueError(f"duplicate audit item {duplicate['id']}; use show or reopen instead")
    sequence = data["next_sequence"]
    identifier = f"TMA-{datetime.now().astimezone():%Y%m%d}-{sequence:04d}"
    data["next_sequence"] = sequence + 1
    now = _now()
    item = {
        "id": identifier,
        "title": title,
        "priority": args.priority,
        "owner": _nonempty(args.owner, "owner"),
        "stage": _nonempty(args.stage, "stage"),
        "description": _nonempty(args.description, "description"),
        "source": source,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "checks": [
            {"id": f"C{i:02d}", "requirement": text, "status": "pending",
             "evidence": "", "note": "", "reviewer": "", "reviewed_at": None}
            for i, text in enumerate(checks, 1)
        ],
        "closed_at": None,
        "closed_by": "",
        "closing_summary": "",
        "history": [{"at": now, "action": "add", "owner": args.owner}],
    }
    data["items"].append(item)
    return item


def _review(args: argparse.Namespace, data: dict) -> dict:
    item = _item(data, args.id)
    if item["status"] in {"passed", "transferred"}:
        raise QueueError("closed/transferred item is immutable")
    check = next((x for x in item["checks"] if x["id"] == args.check), None)
    if check is None:
        raise QueueError(f"unknown check {args.check} for {args.id}")
    result = args.result
    evidence = (args.evidence or "").strip()
    note = (args.note or "").strip()
    reviewer = (args.reviewer or "").strip()
    if result in {"pass", "na"}:
        _nonempty(evidence, "evidence for a pass/not-applicable result")
        _nonempty(reviewer, "reviewer for a pass/not-applicable result")
    if result in {"fail", "blocked", "na"}:
        _nonempty(note, "note explaining failure, blocker or exemption")
    previous = copy.deepcopy(check)
    check.update(status=result, evidence=evidence, note=note,
                 reviewer=reviewer, reviewed_at=_now())
    item["status"] = _open_status(item["checks"])
    _event(item, "check", check_id=check["id"], previous=previous,
           result=result, evidence=evidence, note=note, reviewer=reviewer)
    return item


def _revise(args: argparse.Namespace, data: dict) -> dict:
    """Version a still-open acceptance contract; never erase the old criterion."""
    item = _item(data, args.id)
    if item["status"] in {"passed", "transferred"}:
        raise QueueError("closed/transferred item is immutable")
    reason = _nonempty(args.reason, "revision reason")
    description = None
    if args.description is not None:
        description = _nonempty(args.description, "description")

    specs: dict[str, str] = {}
    for raw in args.check or []:
        check_id, separator, requirement = str(raw).partition("=")
        check_id = check_id.strip()
        requirement = requirement.strip()
        if not separator or not check_id or not requirement:
            raise QueueError("each --check must use CHECK_ID=NEW_REQUIREMENT")
        if check_id in specs:
            raise QueueError(f"duplicate check revision: {check_id}")
        specs[check_id] = requirement

    previous_description = item.get("description", "")
    description_changed = description is not None and description != previous_description
    previous_checks = []
    for check_id, requirement in specs.items():
        check = next((row for row in item["checks"] if row.get("id") == check_id), None)
        if check is None:
            raise QueueError(f"unknown check {check_id} for {args.id}")
        if check.get("status") in {"pass", "na"}:
            raise QueueError(f"verified check {check_id} is immutable; add a separate check instead")
        if check.get("requirement") == requirement:
            continue
        previous_checks.append(copy.deepcopy(check))

    if not description_changed and not previous_checks:
        raise QueueError("revision contains no changed description or check requirement")

    for old in previous_checks:
        check = next(row for row in item["checks"] if row["id"] == old["id"])
        requirement = specs[old["id"]]
        check["requirement"] = requirement
        # Evidence collected against the prior contract cannot pass the new one.
        check.update(status="pending", evidence="", note="", reviewer="", reviewed_at=None)
    if description_changed:
        item["description"] = description
    item["status"] = _open_status(item["checks"])
    _event(
        item, "revise", reason=reason,
        previous_description=previous_description if description_changed else None,
        new_description=description if description_changed else None,
        previous_checks=previous_checks,
        new_requirements={old["id"]: specs[old["id"]] for old in previous_checks},
    )
    return item


def _close(args: argparse.Namespace, data: dict) -> dict:
    item = _item(data, args.id)
    if item["status"] in {"passed", "transferred"}:
        raise QueueError("audit item is already closed/transferred")
    incomplete = [
        check["id"] for check in item["checks"]
        if check["status"] not in {"pass", "na"}
        or not check.get("reviewer") or not check.get("evidence")
        or (check["status"] == "na" and not check.get("note"))
    ]
    if incomplete:
        raise QueueError("cannot close: checks still unaccepted or lack evidence: " + ", ".join(incomplete))
    reviewer = _nonempty(args.reviewer, "closing reviewer")
    summary = _nonempty(args.summary, "closing summary")
    item.update(status="passed", closed_at=_now(),
                closed_by=reviewer, closing_summary=summary)
    _event(item, "close", reviewer=reviewer, summary=summary)
    return item


def _transfer(args: argparse.Namespace, data: dict) -> dict:
    """Remove non-Cycle verification from the active queue without falsely passing checks."""
    item = _item(data, args.id)
    if item["status"] in {"passed", "transferred"}:
        raise QueueError("item is already closed/transferred")
    reference = _nonempty(args.change_record, "change record reference")
    if not reference.startswith("TMC-"):
        raise QueueError("transfer requires a TMC change record reference")
    from .change_register import DEFAULT_STORE as CHANGE_STORE, _read as read_changes
    registered = read_changes(args.change_store or CHANGE_STORE)["items"]
    if not any(record["id"] == reference and record.get("source_tma") == item["id"]
               for record in registered):
        raise QueueError("transfer requires a matching persisted TMC record linked to the original TMA")
    reason = _nonempty(args.reason, "transfer reason")
    item["status"] = "transferred"
    item["transferred_to"] = reference
    item["transfer_reason"] = reason
    _event(item, "transfer", reference=reference, reason=reason,
           previous_checks_status=[(c["id"], c["status"]) for c in item["checks"]])
    return item


def _reopen(args: argparse.Namespace, data: dict) -> dict:
    item = _item(data, args.id)
    if item["status"] != "passed":
        raise QueueError("only a passed/archived item may be reopened")
    previous = copy.deepcopy(item["checks"])
    for check in item["checks"]:
        check.update(status="pending", evidence="", note="", reviewer="", reviewed_at=None)
    item.update(status="pending", closed_at=None, closed_by="", closing_summary="")
    _event(item, "reopen", reviewer=_nonempty(args.reviewer, "reviewer"),
           reason=_nonempty(args.reason, "reopen reason"), previous_checks=previous)
    return item


def _display(item: dict) -> None:
    print(f"{item['id']}  {item['priority']}  {item['status']}  {item['title']}")
    print(f"owner: {item['owner']} | stage: {item['stage']}")
    print(f"source: {item['source']}")
    print(f"description: {item['description']}")
    for check in item["checks"]:
        print(f"  {check['id']} [{check['status']}] {check['requirement']}")
        if check["evidence"]:
            print(f"      evidence: {check['evidence']}")
        if check["note"]:
            print(f"      note: {check['note']}")
    if item["closed_at"]:
        print(f"closed: {item['closed_at']} by {item['closed_by']} — {item['closing_summary']}")
    if item.get("transferred_to"):
        print(f"transferred to: {item['transferred_to']} — {item.get('transfer_reason', '')}")
    print(f"history entries: {len(item['history'])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tm-audit",
        description="Independent audit follow-up register (NOT the formal TMCR approval list).",
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE,
                        help="audit register path (default: tool/theory_master/audit/pending_reviews.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list open/failed/blocked items (or history)")
    listing.add_argument("--all", action="store_true", help="include passed items")
    listing.add_argument("--json", action="store_true", help="structured output")
    show = sub.add_parser("show", help="inspect checks, evidence and history")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    transfer = sub.add_parser("transfer", help="archive a non-Cycle item into the change register without audit pass")
    transfer.add_argument("id")
    transfer.add_argument("--change-record", required=True)
    transfer.add_argument("--change-store", type=Path, help="alternate TMC store (tests or migration)")
    transfer.add_argument("--reason", required=True)
    add = sub.add_parser("add", help="register a new pending audit item")
    add.add_argument("--title", required=True)
    add.add_argument("--priority", choices=PRIORITIES, default="P1")
    add.add_argument("--owner", required=True)
    add.add_argument("--stage", required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--check", action="append", required=True,
                     help="required verification, repeat for C01/C02/...")
    review = sub.add_parser("check", help="record a check result and evidence")
    review.add_argument("id")
    review.add_argument("check", help="stable check identifier, e.g. C01")
    review.add_argument("--result", required=True,
                        choices=("pass", "fail", "blocked", "na", "pending"))
    review.add_argument("--evidence", default="", help="evidence path/command/result")
    review.add_argument("--note", default="", help="failure, blocker or exemption reason")
    review.add_argument("--reviewer", default="")
    revise = sub.add_parser("revise", help="revise open acceptance criteria with an immutable history entry")
    revise.add_argument("id")
    revise.add_argument("--reason", required=True)
    revise.add_argument("--description", help="new scope description")
    revise.add_argument("--check", action="append", default=[],
                        help="revise an unpassed check as CHECK_ID=NEW_REQUIREMENT; repeat as needed")
    close = sub.add_parser("close", help="pass item only after all checks pass or are approved NA")
    close.add_argument("id")
    close.add_argument("--reviewer", required=True)
    close.add_argument("--summary", required=True)
    reopen = sub.add_parser("reopen", help="re-audit a previously passed item")
    reopen.add_argument("id")
    reopen.add_argument("--reviewer", required=True)
    reopen.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"list", "show"}:
            data = _read(args.store)
            if args.command == "show":
                found = _item(data, args.id)
                if args.json:
                    print(json.dumps(found, ensure_ascii=False, indent=2))
                else:
                    _display(found)
                    for event in found["history"]:
                        print(f"  {event['at']} {event['action']}")
            else:
                items = [item for item in data["items"] if args.all or item["status"] not in {"passed", "transferred"}]
                if args.json:
                    print(json.dumps(items, ensure_ascii=False, indent=2))
                else:
                    for item in items:
                        done = sum(check["status"] in {"pass", "na"} for check in item["checks"])
                        print(f"{item['id']}  {item['priority']}  {item['status']:<8} "
                              f"{done}/{len(item['checks'])}  {item['title']}")
                    print(f"{len(items)} item(s)")
        else:
            with _edit(args.store) as data:
                if args.command == "add":
                    item = _add(args, data)
                elif args.command == "check":
                    item = _review(args, data)
                elif args.command == "revise":
                    item = _revise(args, data)
                elif args.command == "close":
                    item = _close(args, data)
                elif args.command == "transfer":
                    item = _transfer(args, data)
                else:
                    item = _reopen(args, data)
            print(f"{item['id']}  {item['status']}  {item['title']}")
        return 0
    except QueueError as exc:
        print(f"tm-audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
