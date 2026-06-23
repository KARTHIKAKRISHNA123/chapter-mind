"""PS3: segmentation role tests — front_matter / body / back_matter.

Splitting is lossless: ``plan.chapters`` holds everything, and an explicit
``role`` lets callers take a content-only view via ``plan.body_chapters``
without dropping front/back matter. These assert that policy on the
public-domain EPUB fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

EPUB = Path(__file__).parents[1] / "golden" / "epub"


def _plan(name: str):
    book = EPUB / name
    if not book.exists():
        pytest.skip(f"fixture missing: {name}")
    return detect(get_adapter(str(book)).load(str(book)), level_filter="auto")


def test_robinson_crusoe_body_is_twenty_chapters():
    plan = _plan("pg521.epub")
    assert len(plan.body_chapters) == 20
    assert all(c.title.upper().startswith("CHAPTER") for c in plan.body_chapters)


def test_robinson_crusoe_front_and_back_excluded_from_body():
    plan = _plan("pg521.epub")
    body_titles = {c.title for c in plan.body_chapters}
    assert "Contents" not in body_titles
    assert not any("LICENSE" in c.title.upper() for c in plan.body_chapters)
    assert any(c.role == "back_matter" and "LICENSE" in c.title.upper()
               for c in plan.chapters)
    assert any(c.role == "front_matter" and c.title == "Contents"
               for c in plan.chapters)


def test_lossless_total_equals_sum_of_roles():
    plan = _plan("pg521.epub")
    assert (len(plan.front_matter) + len(plan.body_chapters)
            + len(plan.back_matter)) == len(plan.chapters)


def test_utopia_body_excludes_title_page_and_matter():
    plan = _plan("pg2130.epub")
    assert len(plan.body_chapters) == 9
    assert "Utopia" not in {c.title for c in plan.body_chapters}   # title page
    assert "Contents" not in {c.title for c in plan.body_chapters}


def test_single_file_anchor_chapters_are_all_body():
    plan = _plan("single_file_anchors.epub")
    assert len(plan.body_chapters) == 3
    assert [c.title for c in plan.body_chapters] == [
        "Chapter 1. The Beginning",
        "Chapter 2. The Middle",
        "Chapter 3. The End",
    ]
