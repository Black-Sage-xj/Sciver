"""Observe registered Docker identities; no task controls or model calls."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .external_execution import EVIDENCE_SCHEMA, REGISTRY_SCHEMA, process_identity, read_document


def observe(registry: dict, command: list[str]) -> dict:
    containers = {}
    ids = sorted({row.get("container_id", "") for row in registry.get("tasks", {}).values()})
    ids = [value for value in ids if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)][:128]
    errors = []
    if ids:
        try:
            proc = subprocess.run([*command, "inspect", "--type", "container", *ids], capture_output=True,
                                  text=True, timeout=8, check=False)
            for info in json.loads(proc.stdout or "[]"):
                cid = info["Id"]
                processes = []
                if info.get("State", {}).get("Running"):
                    try:
                        top = subprocess.run([*command, "top", cid, "-eo", "pid"], capture_output=True,
                                             text=True, timeout=5, check=True)
                        for line in top.stdout.splitlines()[1:]:
                            if line.strip().isdigit():
                                identity = process_identity(int(line.strip()))
                                if identity:
                                    processes.append(identity)
                    except (OSError, subprocess.SubprocessError) as exc:
                        errors.append(f"{cid[:12]}:{type(exc).__name__}")
                containers[cid] = {"id": cid, "image_id": info.get("Image"),
                                   "running": info.get("State", {}).get("Running") is True,
                                   "mounts": [{"Source": m.get("Source"), "Destination": m.get("Destination")}
                                              for m in info.get("Mounts", [])], "processes": processes}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            errors.append(type(exc).__name__)
    return {"schema": EVIDENCE_SCHEMA, "observed_at": datetime.now(timezone.utc).isoformat(),
            "containers": containers, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-command", default='["docker"]')
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    command = json.loads(args.docker_command)
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        parser.error("docker-command must be a nonempty JSON argv list")
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        settings = read_document(args.registry)
        if settings.get("schema") != REGISTRY_SCHEMA:
            raise ValueError("invalid execution registry")
        value = observe(settings, command)
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(value) + '\n'); temporary.chmod(0o600); temporary.replace(args.output)
        if args.once: break
        time.sleep(max(1, args.interval))


if __name__ == '__main__': main()
