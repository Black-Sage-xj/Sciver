"""The full Outcome Markdown has no total character/byte cap."""
from pathlib import Path

from tm_observer.canvas_md import MAX_DOCUMENT_BYTES, read_document, document_paths


def test_unlimited_document_pagination(tmp_path: Path):
    huge = "# Proof details\n\n" + "公式与数学推导\n" * (MAX_DOCUMENT_BYTES // 14 + 500)
    page = tmp_path / "notes" / "outcomes" / "C7.md"
    page.parent.mkdir(parents=True)
    page.write_text(huge, encoding="utf-8")
    assert "notes/outcomes/C7.md" in document_paths(tmp_path)
    pieces = []
    offset = 0
    while True:
        result = read_document(tmp_path, "notes/outcomes/C7.md", offset)
        pieces.append(result["content"])
        if result["next_offset"] is None:
            break
        assert result["next_offset"] > offset
        offset = result["next_offset"]
    assert "".join(pieces) == huge


def test_pagination_handles_long_utf8_line(tmp_path: Path):
    huge = "χ" * (MAX_DOCUMENT_BYTES // 2 + 100)
    p = tmp_path / "notes" / "long.md"
    p.parent.mkdir()
    p.write_text(huge, encoding="utf-8")
    first = read_document(tmp_path, "notes/long.md")
    second = read_document(tmp_path, "notes/long.md", first["next_offset"])
    assert first["content"] + second["content"] == huge
