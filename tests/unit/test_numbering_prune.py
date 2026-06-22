"""Unit tests for _prune_numbering_noise and _longest_increasing.

These are pure-function tests -- no file I/O, no detector, no adapters.
The synthetic candidates mirror a Bhojpuri book where a nested numbered
list (1. 2. 1. 3.) injected false chapter boundaries.
"""
from __future__ import annotations

import pytest

from book_splitter.decision_engine import (
    Candidate,
    _longest_increasing,
    _prune_numbering_noise,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _c(index: int, number: int, conf: float, rank: int = 3) -> Candidate:
    """Build a minimal Candidate that satisfies _prune_numbering_noise."""
    c = Candidate(
        index=index,
        title=f"{number}. Title {index}",
        norm=f"{number} title {index}",
        level="chapter",
        confidence=conf,
        fired={},
    )
    c.number = number
    c._rank = rank
    c.is_matter = False
    c.div_type = "unit"
    return c


def _matter(index: int) -> Candidate:
    """Front/back-matter candidate (no number)."""
    c = Candidate(index=index, title="Preface", norm="preface",
                  level="section", confidence=0.8, fired={})
    c.number = None
    c._rank = 3
    c.is_matter = True
    c.div_type = "section"
    return c


# ---------------------------------------------------------------------------
# _longest_increasing
# ---------------------------------------------------------------------------
class TestLongestIncreasing:
    def test_already_increasing(self):
        items = [_c(10, 1, 0.7), _c(20, 2, 0.7), _c(30, 3, 0.7)]
        result = _longest_increasing(items, key=lambda c: c.number)
        assert [c.number for c in result] == [1, 2, 3]

    def test_drops_regression(self):
        # 1, 2, 1, 3 -> LIS = 1, 2, 3
        items = [_c(10, 1, 0.7), _c(20, 2, 0.7),
                 _c(30, 1, 0.5), _c(40, 3, 0.7)]
        result = _longest_increasing(items, key=lambda c: c.number)
        assert [c.number for c in result] == [1, 2, 3]

    def test_single_item(self):
        items = [_c(5, 1, 0.9)]
        assert _longest_increasing(items, key=lambda c: c.number) == items

    def test_empty(self):
        assert _longest_increasing([], key=lambda c: c.number) == []


# ---------------------------------------------------------------------------
# _prune_numbering_noise
# ---------------------------------------------------------------------------
class TestPruneNumberingNoise:
    def test_drops_nested_list_items(self):
        """Real chapters (high conf) survive; list items (low conf, same number)
        are pruned. The kept sequence is strictly increasing: 1, 2, 3, 4."""
        # Chapter 1 (page-break evidence -> high conf) at index 38
        # False "1" (bare number in nested list) at index 50
        # False "2" from list at index 55
        # Chapter 2 (high conf) at index 101
        # False "1" from list at index 160
        # Chapter 3 at index 191
        # Chapter 4 at index 251
        cands = [
            _c(38, 1, 0.70),   # real chapter 1
            _c(50, 1, 0.45),   # list item falsely numbered 1
            _c(55, 2, 0.50),   # list item falsely numbered 2
            _c(101, 2, 0.70),  # real chapter 2
            _c(160, 1, 0.45),  # list item falsely numbered 1 again
            _c(191, 3, 0.70),  # real chapter 3
            _c(251, 4, 0.70),  # real chapter 4
        ]
        result = _prune_numbering_noise(cands, {})
        assert [c.index for c in result] == [38, 101, 191, 251]

    def test_clean_sequence_is_noop(self):
        """A perfectly increasing sequence is returned as-is (same object)."""
        cands = [_c(10, 1, 0.7), _c(20, 2, 0.7), _c(30, 3, 0.7)]
        result = _prune_numbering_noise(cands, {})
        assert result is cands

    def test_multilevel_not_pruned(self):
        """If chapter numbers reset under a new Part (_rank differs), the reset
        is legitimate and the function must not prune anything."""
        part = _c(10, 1, 0.8, rank=2)   # Part level
        ch1  = _c(40, 1, 0.7, rank=3)   # Chapter 1 under Part 1
        ch2  = _c(70, 2, 0.7, rank=3)   # Chapter 2 under Part 1
        ch3  = _c(110, 1, 0.7, rank=3)  # Chapter 1 under Part 2 (legitimate reset)
        cands = [part, ch1, ch2, ch3]
        result = _prune_numbering_noise(cands, {})
        assert result is cands

    def test_fewer_than_three_numbered_is_noop(self):
        """With only two numbered candidates the function cannot reliably prune."""
        cands = [_c(5, 1, 0.7), _c(10, 1, 0.5)]
        result = _prune_numbering_noise(cands, {})
        assert result is cands

    def test_matter_blocks_always_kept(self):
        """Front/back-matter (no number, is_matter=True) survive regardless."""
        pre = _matter(2)
        cands = [
            pre,
            _c(20, 1, 0.70),
            _c(30, 1, 0.45),  # noise
            _c(80, 2, 0.70),
            _c(140, 3, 0.70),
        ]
        diag: dict = {}
        result = _prune_numbering_noise(cands, diag)
        assert pre in result
        assert len(result) == 4   # preface + chapters 1, 2, 3
        assert "numbering_noise_dropped" in diag

    def test_diag_records_dropped_titles(self):
        diag: dict = {}
        cands = [
            _c(10, 1, 0.8),
            _c(20, 1, 0.4),  # will be dropped (weaker duplicate-1)
            _c(50, 2, 0.8),
            _c(90, 3, 0.8),
        ]
        _prune_numbering_noise(cands, diag)
        assert "numbering_noise_dropped" in diag
        assert any("1." in t for t in diag["numbering_noise_dropped"])
