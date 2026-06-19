"""Unit tests for pure models and block properties."""
from __future__ import annotations

import pytest

from book_splitter.models import (
    Block,
    DocumentMeta,
    UnifiedDocument,
)
from book_splitter.detector import (
    ChapterPlan,
    Chapter,
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

    def test_normalized_title_strips_and_lowercases(self):
        # colon → space, split() collapses all whitespace runs, join with single space
        b = Block(index=0, text="  Chapter: One  ")
        assert b.normalized_title() == "chapter one"

    def test_normalized_title_colon_becomes_space(self):
        b = Block(index=0, text="Part:Two")
        assert b.normalized_title() == "part two"

    def test_is_a_dataclass(self):
        b = Block(index=0)
        assert hasattr(b, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# DocumentMeta
# ---------------------------------------------------------------------------

class TestDocumentMeta:
    def test_required_source_format(self):
        m = DocumentMeta(source_format="docx")
        assert m.source_format == "docx"

    def test_defaults(self):
        m = DocumentMeta(source_format="docx")
        assert m.title == ""
        assert m.path == ""

    def test_path_field(self):
        m = DocumentMeta(source_format="epub", path="book.epub")
        assert m.path == "book.epub"

    def test_mutable(self):
        # DocumentMeta is a plain dataclass, not frozen — mutation is allowed
        m = DocumentMeta(source_format="docx")
        m.title = "My Book"
        assert m.title == "My Book"


# ---------------------------------------------------------------------------
# UnifiedDocument
# ---------------------------------------------------------------------------

class TestUnifiedDocument:
    def _meta(self) -> DocumentMeta:
        return DocumentMeta(source_format="docx", path="x.docx")

    def test_blocks_field_present(self):
        doc = UnifiedDocument(blocks=[], meta=self._meta())
        assert doc.blocks == []

    def test_blocks_stores_elements(self):
        b = Block(index=0, text="Hello")
        doc = UnifiedDocument(blocks=[b], meta=self._meta())
        assert len(doc.blocks) == 1
        assert doc.blocks[0].text == "Hello"

    def test_default_sets_empty(self):
        doc = UnifiedDocument(blocks=[], meta=self._meta())
        assert doc.page_break_after == set()
        assert doc.section_break_at == set()
        assert doc.isolated == set()

    def test_toc_entries_default_none(self):
        doc = UnifiedDocument(blocks=[], meta=self._meta())
        assert doc.toc_entries is None


# ---------------------------------------------------------------------------
# Chapter & ChapterPlan
# ---------------------------------------------------------------------------

class TestChapterPlan:
    def test_chapter_initialization(self):
        ch = Chapter(title="Chapter 1", start=0, end=10, confidence=0.95)
        assert ch.title == "Chapter 1"
        assert ch.start == 0
        assert ch.end == 10
        assert ch.confidence == 0.95
        assert ch.level == "chapter" # Default value

    def test_chapter_plan_abstained(self):
        plan = ChapterPlan(chapters=[], abstained=True, reason="too few candidates")
        assert plan.abstained is True
        assert "too few" in plan.reason

    def test_chapter_plan_valid(self):
        ch1 = Chapter(title="Intro", start=0, end=5)
        ch2 = Chapter(title="Chapter 1", start=5, end=15)
        plan = ChapterPlan(chapters=[ch1, ch2], abstained=False)
        
        assert plan.abstained is False
        assert len(plan.chapters) == 2
        assert plan.chapters[1].title == "Chapter 1"