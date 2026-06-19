"""Unit tests for confidence triage (pure, no I/O)."""
from __future__ import annotations

from book_splitter.detector import Chapter, ChapterPlan
from book_splitter.review import triage


def _plan(*confs):
    chapters = [
        Chapter(title=f"ch{i}", start=i, end=i + 1, confidence=c)
        for i, c in enumerate(confs)
    ]
    return ChapterPlan(chapters=chapters)


def test_triage_orders_weakest_first():
    r = triage(_plan(0.9, 0.2, 0.4), threshold=0.5)
    assert r.weak_count == 2
    assert [c.confidence for c in r.weak] == [0.2, 0.4]


def test_triage_clean_when_all_strong():
    assert triage(_plan(0.8, 0.95)).is_clean


def test_triage_total_counts_all_chapters():
    r = triage(_plan(0.1, 0.2, 0.9), threshold=0.5)
    assert r.total == 3
    assert r.weak_count == 2


def test_triage_empty_plan_is_clean():
    r = triage(_plan(), threshold=0.5)
    assert r.is_clean
    assert r.total == 0
