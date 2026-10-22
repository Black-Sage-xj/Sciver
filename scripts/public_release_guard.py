#!/usr/bin/env python3
"""Fail closed when a public Git tree or Python artifact contains a private provider.

No file contents, credentials, or matching lines are echoed. This is a release
gate, not a sanitizer: clean the sources on a sanitized public branch first.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

# Split retired identifiers are detection rules, never runtime integrations.
PRIVATE_MARKERS = ("do_not_" + "publish", "yun" + "shu", "\u4e91\u67a2")
PRIVATE_PROFILE_PATH = re.compile(r"(^|/)model_profiles/(?!test\.toml$|codex\.toml$|advanced\.toml$)[^/]+\.toml$")
PRIVATE_PATHS = ("config/local/", ".secrets/", ".codex/", ".env", "auth.json",
                 "provider_connections.toml")


def _is_private_name(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if any(marker in normalized for marker in PRIVATE_MARKERS):
        return True
    if PRIVATE_PROFILE_PATH.search(normalized):
        return True
    return (any(normalized.startswith(prefix) for prefix in PRIVATE_PATHS)
            and normalized not in {".env.example", "config/.env.example"})


def inspect_public_artifact(path: Path) -> list[str]:
    """Inspect wheel/zip/sdist members, not just the source checkout."""
    issues: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = ((name, archive.read(name)) for name in archive.namelist()
                       if not name.endswith("/"))
            issues.extend(_inspect_members(entries))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            entries = ((member.name, archive.extractfile(member).read())
                       for member in archive.getmembers()
                       if member.isfile() and member.size <= 2_000_000)
            issues.extend(_inspect_members(entries))
    else:
        raise ValueError("artifact must be a wheel, zip or tar archive")
    return issues


def _inspect_members(entries) -> list[str]:
    issues = []
    for name, raw in entries:
        if _is_private_name(name):
            issues.append(name + ": private provider path")
        if (len(raw) <= 2_000_000 and
                Path(name).suffix.lower() in {".py", ".toml", ".md", ".txt", ".json",
                                               ".js", ".tsx", ".yml", ".yaml", ".html"}):
            text = raw.decode("utf-8", errors="replace").lower()
            if any(marker in text for marker in PRIVATE_MARKERS):
                issues.append(name + ": private provider content")
    return issues


def inspect_git_ref(root: Path, ref: str) -> list[str]:
    """Inspect the exact commit proposed for publication, not a dirty checkout."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]*", ref):
        raise ValueError("invalid Git ref")
    tree = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", ref],
        capture_output=True, check=True,
    ).stdout
    paths = [p.decode("utf-8", "replace") for p in tree.split(b"\0") if p]
    issues = [path + ": private provider path" for path in paths if _is_private_name(path)]
    # Git grep reads blobs directly from the selected ref. Never output matches.
    command = ["git", "-C", str(root), "grep", "-I", "-i", "-l"]
    for marker in PRIVATE_MARKERS:
        command += ["-e", marker]
    command += [ref, "--"]
    found = subprocess.run(command, capture_output=True)
    if found.returncode not in {0, 1}:
        raise RuntimeError("Git provider scan failed")
    for line in found.stdout.decode("utf-8", "replace").splitlines():
        _ref, sep, path = line.partition(":")
        if sep and path:
            issues.append(path + ": private provider content")
    return sorted(set(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a public release contains no private provider integration")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        findings = inspect_git_ref(args.repo, args.ref)
        for archive in args.artifact:
            findings.extend(archive.name + ": " + issue
                            for issue in inspect_public_artifact(archive))
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print("BLOCKED: release verification unavailable (" + type(exc).__name__ + ")", file=sys.stderr)
        return 2
    if findings:
        print("BLOCKED: private provider remains in proposed public release:")
        for finding in sorted(set(findings))[:80]:
            print("  " + finding)
        if len(findings) > 80:
            print("  ... additional findings omitted")
        return 1
    print("PASS: no private provider markers in Git ref or supplied artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
