"""Shared pytest fixtures for book_splitter tests."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Tiny synthetic .docx builder
# ---------------------------------------------------------------------------

_STYLES_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
</w:styles>
"""

_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""

_WORD_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>
"""


def _make_docx(body_xml: str) -> bytes:
    """Build a minimal valid .docx ZIP in memory."""
    doc_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{body_xml}<w:sectPr/></w:body>
</w:document>""".encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc_xml)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("word/_rels/document.xml.rels", _WORD_RELS)
    return buf.getvalue()


def _heading_para(text: str, level: int = 1) -> str:
    return (
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>'
        f'<w:r><w:t>{text}</w:t></w:r></w:p>'
    )


def _body_para(text: str) -> str:
    return (
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:r><w:t>{text}</w:t></w:r></w:p>'
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tiny_docx_path(tmp_path_factory) -> Path:
    """A tiny 3-chapter DOCX for unit/regression tests."""
    body = "".join([
        _heading_para("Introduction", 1),
        _body_para("This is the introduction. " * 8),
        _heading_para("Chapter One", 1),
        _body_para("Body text for chapter one. " * 10),
        _body_para("More body text for chapter one. " * 8),
        _heading_para("Chapter Two", 1),
        _body_para("Body text for chapter two. " * 10),
    ])
    tmp = tmp_path_factory.mktemp("fixtures")
    path = tmp / "tiny_3ch.docx"
    path.write_bytes(_make_docx(body))
    return path


@pytest.fixture(scope="session")
def epub_path() -> Path | None:
    """The French Revolution EPUB included in the repo (if present)."""
    p = Path(__file__).parent.parent / "French Revolution.epub"
    return p if p.exists() else None
