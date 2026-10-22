#!/usr/bin/env python3
"""Upgrade Codex without orphaning the daemon or racing account switching.

Usage: codex-safe-upgrade --check | --sync-daemon | --upgrade
Close active Codex CLI/Desktop sessions before running an upgrade.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from tm_observer import codex_usage as usage


def _daemon_version() -> dict:
    result = subprocess.run(
        [str(usage.DEFAULT_CODEX_BIN), "app-server", "daemon", "version"],
        env={**os.environ, "CODEX_HOME": str(usage.SHARED_RUNTIME_HOMES[0])},
        capture_output=True, text=True, timeout=15, check=True,
    )
    info = json.loads(result.stdout)
    if info.get("status") != "running":
        raise RuntimeError("Codex daemon is not running")
    return info


def _assert_version_aligned(info: dict) -> None:
    versions = {info.get(name) for name in
                ("cliVersion", "managedCodexVersion", "appServerVersion")}
    if len(versions) != 1 or None in versions:
        raise RuntimeError("CLI, daemon and managed app-server versions differ")


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, timeout=300)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--sync-daemon", action="store_true")
    modes.add_argument("--upgrade", action="store_true")
    args = parser.parse_args(argv)
    try:
        with usage._account_switch_process_lock(usage.ACCOUNT_SELECTOR):
            usage._assert_no_active_codex_sessions()
            usage._assert_managed_app_server()
            before = _daemon_version()
            if args.upgrade:
                _run(["npm", "install", "-g", "@openai/codex@latest"])
            if args.upgrade or args.sync_daemon:
                _run([str(usage.DEFAULT_CODEX_BIN), "app-server", "daemon",
                      "update", "--from-cli", "--yes"])
            usage._assert_managed_app_server()
            after = _daemon_version()
            _assert_version_aligned(after)
            print("Codex daemon healthy; CLI, managed daemon and app-server versions match: "
                  + str(after["cliVersion"]))
            if before.get("managedCodexVersion") != after.get("managedCodexVersion"):
                print("Daemon package upgraded and managed app-server restarted")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Codex managed upgrade blocked or failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
