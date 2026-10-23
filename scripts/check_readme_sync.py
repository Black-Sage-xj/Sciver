#!/usr/bin/env python3
"""Check the parts of the Chinese/English README pair that must match exactly."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHINESE = ROOT / "README.zh-CN.md"
ENGLISH = ROOT / "README.md"


def structure(text: str) -> list[int]:
    return [len(marker) for marker in re.findall(r"^(#{2,3})\s+", text, re.M)]


def shell_examples(text: str) -> list[list[str]]:
    blocks = re.findall(r"^```bash\s*\n(.*?)^```", text, re.M | re.S)
    return [[line.split(" #", 1)[0].strip() for line in block.splitlines() if line.strip()]
            for block in blocks]


def destinations(text: str) -> list[str]:
    return (re.findall(r"\]\(([^)]+)\)", text)
            + re.findall(r'<a href="([^"]+)"', text))


def inline_commands(text: str) -> list[str]:
    return re.findall(r"`((?:sciver|python|tm-observer|export)\s+[^`]+)`", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-from", help="Git ref for requiring paired README edits")
    args = parser.parse_args()
    chinese, english = CHINESE.read_text(encoding="utf-8"), ENGLISH.read_text(encoding="utf-8")
    failures = []
    for label, extract in (("section levels/order", structure),
                           ("shell commands", shell_examples),
                           ("link destinations", destinations),
                           ("inline commands", inline_commands)):
        if extract(chinese) != extract(english):
            failures.append(f"{label} differ between README.zh-CN.md and README.md")
    if args.changed_from:
        changed = set(subprocess.check_output(
            ["git", "diff", "--name-only", args.changed_from, "HEAD", "--", "README.zh-CN.md", "README.md"],
            cwd=ROOT, text=True).splitlines())
        if len(changed) == 1:
            failures.append("a README changed without updating its translation in the same change")
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        return 1
    print("PASS: bilingual README sections, commands and links are aligned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
