#!/usr/bin/env python3
"""CLI adapter for the explicitly enabled Sciver PDF conversion component."""
from __future__ import annotations

import argparse
from pathlib import Path

from theory_master.document_conversion import convert_pdf_to_markdown


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()
    print(convert_pdf_to_markdown(args.pdf, args.output_dir, max_pages=args.max_pages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
