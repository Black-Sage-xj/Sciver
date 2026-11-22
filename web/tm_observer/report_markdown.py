"""Embed the same local Markdown reader used by exported Sciver reports."""
from importlib.resources import files

def markdown_script() -> str:
    return files("theory_master.report_synthesis").joinpath("assets/markdown_reader.js").read_text(encoding="utf-8")
