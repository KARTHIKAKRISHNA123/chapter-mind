"""Unit tests for the ingestion / format-sniffing layer."""
from __future__ import annotations

import zipfile

import pytest

from book_splitter.ingestion import (
    CorruptArchiveError,
    Format,
    UnsupportedFormatError,
    sniff_format,
)


def _make_zip(tmp_path, members: dict, name="x.docx"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return p


def test_real_docx(tmp_path):
    p = _make_zip(tmp_path, {
        "word/document.xml": b"<w:document/>",
        "[Content_Types].xml": b"",
    })
    assert sniff_format(p) is Format.DOCX


def test_real_epub(tmp_path):
    p = _make_zip(tmp_path, {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"<container/>",
        "OEBPS/ch1.xhtml": b"<html/>",
    }, name="book.epub")
    assert sniff_format(p) is Format.EPUB


def test_ole2_doc_renamed(tmp_path):
    """The real-world crash case: a legacy .doc / protected .docx named .docx."""
    p = tmp_path / "book.docx"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(UnsupportedFormatError, match="OLE2"):
        sniff_format(p)


def test_pdf_renamed(tmp_path):
    p = tmp_path / "book.docx"
    p.write_bytes(b"%PDF-1.7\n" + b"\x00" * 32)
    with pytest.raises(UnsupportedFormatError, match="PDF"):
        sniff_format(p)


def test_truncated_zip(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 8)  # zip magic, no central dir
    with pytest.raises(CorruptArchiveError):
        sniff_format(p)


def test_empty_file(tmp_path):
    p = tmp_path / "empty.docx"
    p.write_bytes(b"")
    with pytest.raises(CorruptArchiveError):
        sniff_format(p)


def test_zip_but_neither_format(tmp_path):
    p = _make_zip(tmp_path, {"random/file.txt": b"hello"})
    with pytest.raises(UnsupportedFormatError, match="neither DOCX nor EPUB"):
        sniff_format(p)


def test_missing_file(tmp_path):
    from book_splitter.ingestion import IngestionError
    with pytest.raises(IngestionError, match="Not a file"):
        sniff_format(tmp_path / "does_not_exist.docx")
