"""Unit tests for EPUB asset path resolution.

`_resolve` is pure and dependency-free, so these run without ebooklib. They are
also the Windows guard: if anyone swaps in os.path, '../images/a.png' turns into
a backslash mess and these fail on every OS.
"""
from __future__ import annotations

from book_splitter.adapters.epub_resources import _resolve


def test_resolve_normalizes_parent_dirs():
    assert _resolve("OEBPS/text/ch1.xhtml", "../images/a.png") == "OEBPS/images/a.png"


def test_resolve_same_dir():
    assert _resolve("OEBPS/text/ch.xhtml", "style.css") == "OEBPS/text/style.css"


def test_resolve_strips_fragment_and_query():
    assert _resolve("OEBPS/text/ch.xhtml", "css/x.css#f") == "OEBPS/text/css/x.css"
    assert _resolve("OEBPS/text/ch.xhtml", "css/x.css?v=2") == "OEBPS/text/css/x.css"


def test_resolve_skips_external_and_inline():
    assert _resolve("OEBPS/ch.xhtml", "https://x.com/a.png") == ""
    assert _resolve("OEBPS/ch.xhtml", "http://x.com/a.png") == ""
    assert _resolve("OEBPS/ch.xhtml", "data:image/png;base64,AA") == ""
    assert _resolve("OEBPS/ch.xhtml", "//cdn.x.com/a.png") == ""


def test_resolve_empty_ref():
    assert _resolve("OEBPS/ch.xhtml", "") == ""
    assert _resolve("OEBPS/ch.xhtml", "   ") == ""
