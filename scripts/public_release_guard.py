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
from pathlib import Path, PurePosixPath

# Split retired identifiers are detection rules, never runtime integrations.
PRIVATE_MARKERS = ("do_not_" + "publish", "yun" + "shu", "\u4e91\u67a2")
PRIVATE_PROFILE_PATH = re.compile(r"(^|/)model_profiles/(?!test\.toml$|codex\.toml$|advanced\.toml$)[^/]+\.toml$")
PRIVATE_PATHS = ("config/local/", ".secrets/", ".codex/", ".env", "auth.json",
                 "provider_connections.toml")


def _is_private_name(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = PurePosixPath(normalized).parts
    if normalized.startswith("/") or ".." in parts:
        return True
    if any(marker in normalized for marker in PRIVATE_MARKERS):
        return True
    if PRIVATE_PROFILE_PATH.search(normalized):
        return True
    # Archive members carry a distribution/package prefix. Check each suffix
    # so sciver-X/config/local/foo is held to the same rule as a Git path.
    return any(
        any(suffix.startswith(prefix) for prefix in PRIVATE_PATHS)
        and suffix not in {".env.example", "config/.env.example"}
        for suffix in ("/".join(parts[index:]) for index in range(len(parts)))
    )


def inspect_public_artifact(path: Path) -> list[str]:
    """Inspect wheel/zip/sdist members, not just the source checkout."""
    issues: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            issues.extend(info.filename + ": archive link"
                          for info in archive.infolist()
                          if (info.external_attr >> 16) & 0o170000 == 0o120000)
            entries = ((name, archive.read(name)) for name in archive.namelist()
                       if not name.endswith("/"))
            issues.extend(_inspect_members(entries))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            issues.extend(member.name + ": archive link"
                          for member in archive.getmembers()
                          if member.issym() or member.islnk())
            entries = ((member.name, archive.extractfile(member).read())
                       for member in archive.getmembers()
                       if member.isfile())
            issues.extend(_inspect_members(entries))
    else:
        raise ValueError("artifact must be a wheel, zip or tar archive")
    return issues


def _inspect_members(entries) -> list[str]:
    issues = []
    for name, raw in entries:
        if _is_private_name(name):
            issues.append(name + ": private provider path")
        if Path(name).suffix.lower() in {".py", ".toml", ".md", ".txt", ".json",
                                        ".js", ".tsx", ".yml", ".yaml", ".html"}:
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


def inspect_git_history(root: Path, base: str, ref: str) -> list[str]:
    """Inspect every new tree and commit metadata, including deleted content."""
    # Resolve object IDs before passing a user-supplied range to rev-list.
    def resolve(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]*", value):
            raise ValueError("invalid Git ref")
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--verify", value + "^{commit}"],
            text=True,
        ).strip()

    base_id, ref_id = resolve(base), resolve(ref)
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base_id, ref_id],
        capture_output=True,
    )
    if ancestor.returncode != 0:
        return ["history: public base is not an ancestor of release"]
    commits = subprocess.check_output(
        ["git", "-C", str(root), "rev-list", "--reverse", base_id + ".." + ref_id],
        text=True,
    ).splitlines()
    issues = []
    for commit in commits:
        metadata = subprocess.check_output(
            ["git", "-C", str(root), "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit],
            text=True,
        ).lower()
        if any(marker in metadata for marker in PRIVATE_MARKERS):
            issues.append(commit[:12] + ": private content in commit metadata")
        parents = subprocess.check_output(
            ["git", "-C", str(root), "show", "-s", "--format=%P", commit], text=True,
        ).split()
        if len(parents) > 1:
            issues.append(commit[:12] + ": release history contains a merge")
        issues.extend(commit[:12] + ": " + item for item in inspect_git_ref(root, commit))
    return sorted(set(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a public release contains no private provider integration")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--base", help="review all new commits after this public base")
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        findings = inspect_git_ref(args.repo, args.ref)
        if args.base:
            findings.extend(inspect_git_history(args.repo, args.base, args.ref))
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
    print("PASS: public tree, requested history and supplied artifacts contain no private provider markers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
