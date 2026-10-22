#!/usr/bin/env python3
"""Run reproducible Sciver core test profiles.

Profiles:
  default: run the core SDK tests except exact, documented known failures.
  full:    run every core SDK test, including known failures.
  list:    print the current known-failure quarantine without running tests.

The quarantine is narrow and explicit: no module-wide exclusions or pytest xfail.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = ROOT / "tests" / "known_failures.json"


def load_quarantine() -> list[dict[str, str]]:
    payload = json.loads(QUARANTINE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise SystemExit(f"Unsupported quarantine schema in {QUARANTINE}")
    tests = payload.get("tests")
    if not isinstance(tests, list):
        raise SystemExit(f"Missing tests list in {QUARANTINE}")
    seen: set[str] = set()
    for item in tests:
        if not isinstance(item, dict) or not item.get("nodeid") or not item.get("reason"):
            raise SystemExit(f"Malformed known-failure entry in {QUARANTINE}: {item!r}")
        if item["nodeid"] in seen:
            raise SystemExit(f"Duplicate quarantined node ID: {item['nodeid']}")
        seen.add(item["nodeid"])
        if item.get("status") != "open":
            raise SystemExit(f"Known-failure status must be reviewed explicitly: {item!r}")
    return tests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("default", "full", "list"), nargs="?", default="default")
    args = parser.parse_args()
    failures = load_quarantine()

    if args.profile == "list":
        print(f"Known failing tests ({len(failures)}):")
        for item in failures:
            print(f"- {item['nodeid']}\n  status: {item['status']} | category: {item['category']}\n  reason: {item['reason']}")
        return 0

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "web"))
    import pytest

    command = ["-q", "tests"]
    if args.profile == "default":
        print(f"Default profile: explicitly deselecting {len(failures)} documented known failures.", flush=True)
        for item in failures:
            command.extend(["--deselect", item["nodeid"]])
    else:
        print("Full profile: running all core tests, including known failures.", flush=True)
    return int(pytest.main(command))


if __name__ == "__main__":
    raise SystemExit(main())
