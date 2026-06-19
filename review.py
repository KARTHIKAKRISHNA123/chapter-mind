"""
review.py
=========
Confidence triage — pure, no I/O, fully unit-testable.

The engine already *computes* a confidence per chapter and *abstains* when it
cannot find enough confident boundaries at all. This module covers the gap in
between: a plan that splits, but where some individual boundaries are shaky. The
CLI uses :func:`triage` to warn and (optionally) ask before writing.

Kept deliberately free of ``typer``/``click`` so it can be tested without a TTY
and reused by any front-end. Works against the engine's ``ChapterPlan`` /
``Chapter`` (duck-typed: a chapter only needs ``.confidence``, ``.title``,
``.start``; a plan only needs ``.chapters``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_THRESHOLD = 0.5


@dataclass(frozen=True)
class ConfidenceReport:
    threshold: float
    total: int
    weak: list[Any]            # chapters below threshold, weakest first

    @property
    def weak_count(self) -> int:
        return len(self.weak)

    @property
    def is_clean(self) -> bool:
        return not self.weak


def triage(plan, threshold: float = DEFAULT_THRESHOLD) -> ConfidenceReport:
    """Return a :class:`ConfidenceReport` for *plan*.

    ``weak`` holds the chapters whose confidence is below *threshold*, sorted
    weakest first so the worst offenders surface at the top of any warning.
    """
    chapters = list(getattr(plan, "chapters", []) or [])
    weak = sorted(
        (c for c in chapters if getattr(c, "confidence", 1.0) < threshold),
        key=lambda c: getattr(c, "confidence", 1.0),
    )
    return ConfidenceReport(threshold=threshold, total=len(chapters), weak=weak)
