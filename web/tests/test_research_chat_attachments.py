from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tm_observer.research_chat_attachments import (
    AttachmentError, MAX_UPLOAD_BYTES, attachment_content, attachment_context,
    capture_artifacts, get_attachment, image_paths, list_attachments,
    store_upload, validate_attachments,
)


class ResearchChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        (self.state / "research-chat").mkdir(parents=True)
        self.sid = "a" * 32
        self.other_sid = "b" * 32
        for sid in (self.sid, self.other_sid):
            (self.state / "research-chat" / f"{sid}.json").write_text(json.dumps({"id": sid}))

    def image(self, format="PNG"):
        output = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(output, format=format)
        return output.getvalue()

    def docx(self, xml: bytes | None = None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            archive.writestr("word/document.xml", xml or b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Document evidence</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        return output.getvalue()

    def pdf(self, texts: tuple[str, ...], *, password: str | None = None) -> bytes:
        """Build small PDF fixtures using only the production BSD PDF library."""
        with PdfWriter() as writer:
            for text in texts:
                page = writer.add_blank_page(width=240, height=180)
                if text:
                    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
                    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                        DictionaryObject({NameObject("/F1"): font})})
                    content = DecodedStreamObject()
                    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                    content.set_data(f"BT /F1 12 Tf 30 40 Td ({escaped}) Tj ET".encode("ascii"))
                    page.replace_contents(content)
            if password is not None:
                writer.encrypt(password)
            output = io.BytesIO()
            writer.write(output)
        return output.getvalue()

    def test_image_formats_roundtrip_and_deduplicate_with_model_paths(self):
        for extension, format, media in (("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp"), ("gif", "GIF", "image/gif")):
            with self.subTest(extension=extension):
                data = self.image(format)
                first = store_upload(self.state, self.sid, f"photo.{extension}", media, data)
                second = store_upload(self.state, self.sid, f"copy.{extension}", media, data)
                self.assertEqual(first["id"], second["id"])
                self.assertEqual((first["kind"], first["status"]), ("image", "ready"))
                self.assertNotIn("storage_name", first)
                self.assertEqual(attachment_content(self.state, self.sid, first["id"]), (data, media, first["name"]))
                paths = image_paths(self.state, self.sid, [first])
                self.assertEqual(len(paths), 1)
                self.assertEqual(Path(paths[0]).read_bytes(), data)
                self.assertIn("visual content is available only", attachment_context(self.state, self.sid, [first]))
        self.assertEqual(len(list_attachments(self.state, self.sid)), 4)

    def test_rejects_mime_spoof_binary_and_unsupported_content(self):
        for name, media, data in (("fake.png", "image/png", b"not an image"), ("fake.jpg", "image/jpeg", self.image()), ("image.png", "application/pdf", self.image()), ("fake.pdf", "application/pdf", b"not a PDF"), ("fake.docx", "", b"not a zip"), ("binary.txt", "text/plain", b"abc\x00xyz"), ("legacy.doc", "application/msword", b"legacy word"), ("payload.svg", "image/svg+xml", b"<svg/>")):
            with self.subTest(name=name), self.assertRaises(AttachmentError):
                store_upload(self.state, self.sid, name, media, data)
        with self.assertRaisesRegex(AttachmentError, "20 MB"):
            store_upload(self.state, self.sid, "large.txt", "", b"x" * (MAX_UPLOAD_BYTES + 1))
        with self.assertRaises(AttachmentError):
            store_upload(self.state, self.sid, "empty.txt", "", b"")

    def test_text_csv_and_docx_become_bounded_untrusted_source_context(self):
        text = store_upload(self.state, self.sid, "notes.md", "text/markdown", "# 标题\n证据文字".encode())
        csv = store_upload(self.state, self.sid, "data.csv", "text/csv", b"name,value\nx,2\n")
        docx = store_upload(self.state, self.sid, "report.docx", "", self.docx())
        self.assertEqual(docx["status"], "ready")
        context = attachment_context(self.state, self.sid, [text, csv, docx])
        for expected in ("quoted source material", "证据文字", "name,value", "Document evidence", "Table cell"):
            self.assertIn(expected, context)
        self.assertEqual(image_paths(self.state, self.sid, [docx]), [])
        self.assertNotIn(str(self.state), context)

    def test_pdf_extracts_page_markers_and_scans_report_actionable_failure(self):
        data = self.pdf(("First page evidence", "Second page evidence"))
        meta = store_upload(self.state, self.sid, "paper.pdf", "application/pdf", data)
        self.assertEqual(meta["page_count"], 2)
        context = attachment_context(self.state, self.sid, [meta])
        self.assertIn("[Page 1]", context)
        self.assertIn("[Page 2]", context)
        self.assertIn("Second page evidence", context)
        scan_data = self.pdf(("",))
        failed = store_upload(self.state, self.sid, "scan.pdf", "", scan_data)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("扫描件", failed["error"])
        with self.assertRaises(AttachmentError):
            validate_attachments(self.state, self.sid, [failed["id"]])
        self.assertEqual(attachment_content(self.state, self.sid, failed["id"])[0], scan_data)
        encrypted_data = self.pdf(("Protected evidence",), password="fixture-password")
        encrypted = store_upload(self.state, self.sid, "protected.pdf", "", encrypted_data)
        self.assertEqual(encrypted["status"], "failed")
        self.assertIn("已加密", encrypted["error"])
        with self.assertRaises(AttachmentError):
            validate_attachments(self.state, self.sid, [encrypted["id"]])
        self.assertEqual(attachment_content(self.state, self.sid, encrypted["id"])[0], encrypted_data)

    def test_scope_id_validation_and_content_integrity(self):
        meta = store_upload(self.state, self.sid, "notes.txt", "", b"evidence")
        with self.assertRaises(AttachmentError):
            get_attachment(self.state, self.other_sid, meta["id"])
        with self.assertRaises(AttachmentError):
            get_attachment(self.state, "../outside", meta["id"])
        with self.assertRaises(AttachmentError):
            validate_attachments(self.state, self.sid, [meta["id"]] * 11)
        self.assertEqual(len(validate_attachments(self.state, self.sid, [meta["id"], meta["id"]])), 1)
        folder = self.state / "research-chat" / "attachments" / self.sid
        content = next(folder.glob("*.txt"))
        content.write_bytes(b"tampered")
        with self.assertRaisesRegex(AttachmentError, "已改变"):
            validate_attachments(self.state, self.sid, [meta["id"]])

    def test_storage_and_content_symlinks_are_rejected(self):
        meta = store_upload(self.state, self.sid, "notes.txt", "", b"evidence")
        content = next((self.state / "research-chat" / "attachments" / self.sid).glob("*.txt"))
        outside = self.root / "private.txt"
        outside.write_bytes(b"private")
        content.unlink()
        content.symlink_to(outside)
        with self.assertRaises(AttachmentError):
            attachment_content(self.state, self.sid, meta["id"])
        root = self.state / "research-chat" / "attachments" / self.other_sid
        root.symlink_to(self.root)
        with self.assertRaises(AttachmentError):
            list_attachments(self.state, self.other_sid)

    def test_docx_archive_type_and_entity_rejection(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("other.txt", "not a word file")
        with self.assertRaisesRegex(AttachmentError, "DOCX"):
            store_upload(self.state, self.sid, "wrong.docx", "", output.getvalue())
        with self.assertRaisesRegex(AttachmentError, "XML"):
            store_upload(self.state, self.sid, "entity.docx", "", self.docx(b'<!DOCTYPE x [<!ENTITY x "expanded">]><document>&x;</document>'))

    def test_capture_artifacts_copies_allowed_links_and_rejects_escape_and_symlinks(self):
        task = self.root / "task"
        task.mkdir()
        output = task / "report.txt"
        output.write_text("research artifact", encoding="utf-8")
        outside = self.root / "private.txt"
        outside.write_text("private data", encoding="utf-8")
        (task / "link.txt").symlink_to(outside)
        (task / "linked-dir").symlink_to(self.root, target_is_directory=True)
        (task / ".env").write_text("SECRET=value")
        (task / "auth.json").write_text("secret")
        links = [f"[report]({output})", f"[duplicate]({output}:1)", f"[outside]({outside})", "[escape](../private.txt)", f"[symlink]({task / 'link.txt'})", f"[dir-link]({task / 'linked-dir/private.txt'})", f"[secret]({task / '.env'})", f"[auth]({task / 'auth.json'})", "[web](https://example.com/a.pdf)"]
        found = capture_artifacts(self.state, self.sid, "\n".join(links), [task])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "artifact")
        self.assertNotIn("source_path", found[0])
        output.unlink()
        self.assertEqual(attachment_content(self.state, self.sid, found[0]["id"])[0], b"research artifact")

    def test_unknown_artifact_is_download_only_not_executable_preview(self):
        task = self.root / "task"
        task.mkdir()
        artifact = task / "interactive.html"
        artifact.write_text("<script>alert(1)</script>")
        meta = capture_artifacts(self.state, self.sid, f"[result]({artifact})", [task])[0]
        self.assertEqual(meta["media_type"], "application/octet-stream")
        self.assertEqual(meta["kind"], "file")
        self.assertIsNone(meta["preview_url"])

    def test_concurrent_uploads_deduplicate_and_history_context_exceeds_single_message_limit(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: store_upload(self.state, self.sid, "same.txt", "", b"same content"), range(8)))
        self.assertEqual(len({meta["id"] for meta in results}), 1)
        other = store_upload(self.state, self.other_sid, "same.txt", "", b"same content")
        self.assertNotEqual(other["id"], results[0]["id"])
        history = [store_upload(self.state, self.sid, f"history-{index}.txt", "", f"historical content {index}".encode()) for index in range(12)]
        context = attachment_context(self.state, self.sid, history)
        self.assertIn("historical content 0", context)
        self.assertIn("historical content 11", context)

    def test_long_text_has_bounded_model_context_and_explicit_truncation(self):
        meta = store_upload(self.state, self.sid, "long.txt", "", b"evidence\n" * 40_000)
        context = attachment_context(self.state, self.sid, [meta])
        self.assertLess(len(context), 25_000)
        self.assertIn("Attachment text truncated", context)
        self.assertTrue(meta["text_truncated"])


if __name__ == "__main__":
    unittest.main()
