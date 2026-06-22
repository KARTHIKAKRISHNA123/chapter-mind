"""
Golden regression harness — the safety net for all hardening work.

Data-driven: adding a book = drop two files into either:
  - ``tests/golden_books/``  (flat)
  - ``tests/golden/<language>/``  (language-organised)

File pair per book:
    <name>.<format>            the book itself (docx / epub)
    <name>.expected.json       {"format","chapter_count","abstained","titles"}

No new code needed per book. Once ``pytest -m regression`` is green in CI, every
other change (security guards, refactors, signal tweaks) is safe to make: if it
drifts the detected chapter structure, this fails loudly.

Use only public-domain or synthetic books here — never commit copyrighted texts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

GOLDEN_BOOKS = Path(__file__).parents[1] / "golden_books"
GOLDEN_LANG  = Path(__file__).parents[1] / "golden"


def _cases():
    # flat legacy directory
    for spec in sorted(GOLDEN_BOOKS.glob("*.expected.json")):
        meta = json.loads(spec.read_text(encoding="utf-8"))
        stem = spec.name[: -len(".expected.json")]
        book = spec.with_name(f"{stem}.{meta['format']}")
        yield pytest.param(book, meta, id=f"legacy/{book.name}")

    # language-organised subdirectories: tests/golden/<lang>/<name>.expected.json
    if GOLDEN_LANG.is_dir():
        for lang_dir in sorted(GOLDEN_LANG.iterdir()):
            if not lang_dir.is_dir():
                continue
            for spec in sorted(lang_dir.glob("*.expected.json")):
                meta = json.loads(spec.read_text(encoding="utf-8"))
                stem = spec.name[: -len(".expected.json")]
                book = spec.with_name(f"{stem}.{meta['format']}")
                yield pytest.param(book, meta, id=f"{lang_dir.name}/{book.name}")


_CASES = list(_cases())


@pytest.mark.regression
@pytest.mark.parametrize("book,expected", _CASES)
def test_chapter_count_is_stable(book, expected):
    if not book.exists():
        pytest.skip(f"golden book missing: {book.name}")
    adapter = get_adapter(str(book))
    plan = detect(adapter.load(str(book)), level_filter="auto")
    assert plan.abstained == expected.get("abstained", False), (
        f"{book.name}: abstained={plan.abstained}, "
        f"locked at {expected.get('abstained', False)}")
    assert len(plan.chapters) == expected["chapter_count"], (
        f"{book.name}: detected {len(plan.chapters)} chapters, "
        f"locked at {expected['chapter_count']}")


@pytest.mark.regression
@pytest.mark.parametrize("book,expected", _CASES)
def test_titles_are_stable(book, expected):
    if not book.exists():
        pytest.skip(f"golden book missing: {book.name}")
    if "titles" not in expected:
        pytest.skip(f"{book.name}: no locked titles")
    adapter = get_adapter(str(book))
    plan = detect(adapter.load(str(book)), level_filter="auto")
    assert [c.title for c in plan.chapters] == expected["titles"]
