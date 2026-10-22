#!/usr/bin/env python3
"""Optional local Marker adapter; isolated conversion and explicit failure reporting."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def convert_pdf_to_markdown(pdf: Path, output: Path, *, marker_bin: str = "", max_pages: int = 0) -> Path:
    executable = marker_bin or os.getenv("SCHOLARLY_MARKER_BIN") or str(
        Path.home() / "miniconda3/envs/marker_env/bin/marker_single")
    if not Path(executable).is_file() and not shutil.which(executable):
        raise RuntimeError("Marker converter unavailable; PDF remains unverified")
    if not pdf.is_file():
        raise ValueError("PDF input file is missing")
    with pdf.open("rb") as source:
        if not source.read(5).startswith(b"%PDF-"):
            raise ValueError("input is not a valid PDF header")
    if pdf.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("PDF exceeds converter input size limit")
    output.mkdir(parents=True, exist_ok=True)
    # Every attempt gets a fresh private directory. Stale Markdown cannot be accepted.
    with tempfile.TemporaryDirectory(prefix="marker-run-", dir=output) as tmp:
        staging = Path(tmp)
        command = [executable, str(pdf), "--output_format", "markdown", "--output_dir", str(staging),
                   "--disable_image_extraction", "--disable_tqdm"]
        if max_pages > 0:
            command.extend(["--page_range", f"0-{min(max_pages, 100) - 1}"])
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=240)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Marker conversion timed out; PDF remains unverified") from exc
        if proc.returncode:
            raise RuntimeError("Marker conversion failed: " + (proc.stderr or proc.stdout)[-600:])
        candidates = sorted(path for path in staging.rglob("*.md") if path.is_file() and path.stat().st_size)
        if not candidates:
            raise RuntimeError("Marker produced no nonempty Markdown; PDF remains unverified")
        chosen = candidates[0]
        if not chosen.read_text(encoding="utf-8", errors="replace").strip():
            raise RuntimeError("Marker output contains no readable text")
        destination = output / "paper.md"
        shutil.copy2(chosen, output / "paper.md.tmp")
        (output / "paper.md.tmp").replace(destination)
        return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=0)
    args = parser.parse_args()
    print(convert_pdf_to_markdown(args.pdf, args.output_dir, max_pages=args.max_pages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
