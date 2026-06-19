"""Unit tests for filename pattern rendering + sanitization."""
from __future__ import annotations

from book_splitter.naming import render_filename, sanitize


def test_sanitize_strips_illegal_chars():
    assert sanitize('a/b:c*d?e') == "a_b_c_d_e"


def test_sanitize_collapses_whitespace_and_underscores():
    assert sanitize("The   Man __ Who") == "The_Man_Who"


def test_sanitize_truncates_and_trims():
    out = sanitize("x" * 200, max_len=10)
    assert len(out) == 10


def test_sanitize_never_empty():
    assert sanitize("///") == "untitled"
    assert sanitize("") == "untitled"


def test_render_filename_basic():
    out = render_filename("{index:02d}_{title}", ordinal=3,
                          title="The Man Who", ext=".docx")
    assert out == "03_The_Man_Who.docx"


def test_render_filename_appends_ext_only_if_missing():
    out = render_filename("{index}_{title}.docx", ordinal=1,
                          title="X", ext=".docx")
    assert out == "1_X.docx"          # not doubled


def test_render_filename_handles_missing_title():
    out = render_filename("{index:03d}_{title}", ordinal=5,
                          title=None, ext=".epub")
    assert out == "005_chapter_5.epub"
