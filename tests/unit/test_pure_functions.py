"""Unit tests for pure v2 models and block properties."""
from __future__ import annotations

import pytest

from book_splitter.book_splitter_v2.models import (
    Block,
    ChapterBoundary,
    ChapterPlan,
    DocumentMeta,
    UnifiedDocument,
)


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------

class TestBlock:
    def test_default_source_is_none(self):
        b = Block(index=0)
        assert b.source is None

    def test_is_textual_all_caps_true(self):
        b = Block(index=0, text="CHAPTER ONE")
        assert b.is_textual_all_caps is True

    def test_is_textual_all_caps_false(self):
        b = Block(index=0, text="Chapter One")
        assert b.is_textual_all_caps is False

    def test_is_textual_all_caps_empty(self):
        b = Block(index=0, text="")
        assert b.is_textual_all_caps is False

    def test_is_textual_all_caps_numbers_only(self):
        b = Block(index=0, text="123 456")
        assert b.is_textual_all_caps is False

    def test_normalized_title(self):
        b = Block(index=0, text="  Chapter: One  ")
        assert b.normalized_title() == "chapter  one"

    def test_slots_no_dict(self):
        b = Block(index=0)
        assert not hasattr(b, "__dict__")


# ---------------------------------------------------------------------------
# DocumentMeta
# ---------------------------------------------------------------------------

class TestDocumentMeta:
    def test_frozen(self):
        m = DocumentMeta(source_path="x.docx", fmt="docx")
        with pytest.raises(Exception):
            m.source_path = "y.docx"  # type: ignore[misc]

    def test_defaults(self):
        m = DocumentMeta(source_path="x.docx", fmt="docx")
        assert m.title is None
        assert m.language is None
        assert m.block_count is None


# ---------------------------------------------------------------------------
# ChapterPlan
# ---------------------------------------------------------------------------

class TestChapterPlan:
    def _meta(self) -> DocumentMeta:
        return DocumentMeta(source_path="x.docx", fmt="docx")

    def test_chapter_count(self):
        plan = ChapterPlan(
            meta=self._meta(),
            boundaries=[
                ChapterBoundary(start_index=0),
                ChapterBoundary(start_index=10),
            ],
        )
        assert plan.chapter_count == 2

    def test_empty_plan(self):
        plan = ChapterPlan(meta=self._meta())
        assert plan.chapter_count == 0
        assert not plan.abstained

    def test_abstained(self):
        plan = ChapterPlan(
            meta=self._meta(),
            abstained=True,
            abstain_reason="too few candidates",
        )
        assert plan.abstained
        assert "too few" in plan.abstain_reason


# ---------------------------------------------------------------------------
# UnifiedDocument
# ---------------------------------------------------------------------------

class TestUnifiedDocument:
    def _meta(self) -> DocumentMeta:
        return DocumentMeta(source_path="x.docx", fmt="docx")

    def test_no_block_list(self):
        doc = UnifiedDocument(meta=self._meta())
        assert not hasattr(doc, "blocks")

    def test_default_sets_empty(self):
        doc = UnifiedDocument(meta=self._meta())
        assert doc.page_break_after == set()
        assert doc.section_break_at == set()
        assert doc.isolated == set()
