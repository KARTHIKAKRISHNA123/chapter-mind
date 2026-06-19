"""
toc.py
======
THE TOC INTELLIGENCE MODULE.

When a book ships a Table of Contents, that TOC is the closest thing to
ground truth we have: the author has literally listed the chapters in order.
This module turns that list into authoritative split boundaries.

It handles four kinds of TOC:
  * Generated Word TOC field  (<w:instrText> containing "TOC", with fldChar)
  * Hyperlinked TOC           (entries are <w:hyperlink> with anchors)
  * Manual "Contents" page     (a "Contents" heading followed by entry lines)
  * TOC-styled entries         (paragraph styles TOC1..TOC9)

Pipeline:
  1. detect_toc_region()  -> locate the contiguous block range that IS the TOC.
  2. extract_entries()    -> ordered list of cleaned entry titles (+ level).
  3. match_to_body()      -> align each entry to the best body paragraph that
                             appears AFTER the TOC, using monotonic alignment.

Ambiguity / mismatch handling baked into match_to_body():
  * page-number + dot-leader suffixes are stripped before matching;
  * duplicate titles are disambiguated by MONOTONIC position (each match must
    come after the previous match), so "Introduction" appearing twice maps to
    the two occurrences in order;
  * fuzzy matching (difflib ratio) absorbs minor punctuation/whitespace and the
    "Interlude" vs "Interlude: Why we should quake" merge differences;
  * an entry that finds no confident body match is dropped (reported), never
    forced onto an unrelated paragraph.
"""

from __future__ import annotations
import re
from difflib import SequenceMatcher

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_TOC_STYLES = {f"TOC{i}" for i in range(1, 10)} | {f"toc {i}" for i in range(1, 10)}
_CONTENTS_RE = re.compile(
    r"^\s*(table of contents|contents|table des mati|sommaire|"
    r"inhaltsverzeichnis|inhalt)\b", re.I)
_PAGENUM_SUFFIX = re.compile(r"[\s\.\u2026]+\d{1,4}\s*$")  # dot leaders + page no.


def _norm(s: str) -> str:
    s = _PAGENUM_SUFFIX.sub("", s)          # strip "..... 42"
    s = re.sub(r"[^\w\s]", " ", s.lower())  # drop punctuation
    return " ".join(s.split())


def has_toc_field(document_xml: bytes) -> bool:
    raw = document_xml.decode("utf-8", "ignore")
    return "instrText" in raw and "TOC" in raw


def detect_toc_region(blocks) -> tuple[int, int] | None:
    """Return (start_idx, end_idx) of the TOC among body children, or None.

    Strategy: find a 'Contents' heading or the first TOC-styled paragraph, then
    consume the contiguous run of TOC-styled / short entry-like paragraphs."""
    start = None
    for b in blocks:
        if b.style_id in _TOC_STYLES:
            start = b.index
            break
        if b.is_paragraph and _CONTENTS_RE.match(b.text or ""):
            start = b.index
            break
    if start is None:
        return None

    end = start
    # If the document uses TOC paragraph styles, the region ends at the LAST
    # TOC-styled paragraph (plus trailing blanks). This prevents the region from
    # greedily swallowing the first real heading after the contents page.
    styled = [b.index for b in blocks
              if b.index > start and b.style_id in _TOC_STYLES]
    if styled:
        return (start, max(styled))

    # Manual contents page (no TOC styles): consume the run of short entry lines.
    blanks = 0
    for b in blocks[start + 1:]:
        if b.is_empty:
            blanks += 1
            if blanks > 4:
                break
        elif b.word_count <= 14 and not b.text.endswith("."):
            end = b.index
            blanks = 0
        else:
            break
    return (start, end)


def extract_entries(blocks, region: tuple[int, int]) -> list[dict]:
    """Ordered TOC entries with a level guess derived from the division lexicon
    (container labels -> level 0, serial/leaf labels -> level 1)."""
    from . import vocabulary as V
    start, end = region
    entries = []
    for b in blocks:
        if b.index < start or b.index > end:
            continue
        if b.is_empty or _CONTENTS_RE.match(b.text or ""):
            continue
        title = _PAGENUM_SUFFIX.sub("", b.text).strip()
        if not title:
            continue
        m = V.match_division(title)
        if m and m["type"] and not m["role"] == "matter":
            level = 0 if V.RANKS.get(m["type"], 3) <= 2 else 1
        else:
            level = 1
        entries.append({"title": title, "norm": _norm(title), "level": level})
    return entries


def match_to_body(entries: list[dict], blocks, toc_end: int,
                  merged_candidates: list[dict]) -> list[dict]:
    """Align TOC entries to candidate heading blocks that appear after the TOC.

    `merged_candidates` are heading candidates already merged across
    multi-paragraph titles (see detector.merge_runs). Each is
    {start_index, norm, text}. We walk entries and candidates with a monotonic
    pointer so duplicates resolve by order and alignment never goes backwards.
    """
    pool = [c for c in merged_candidates if c["start_index"] > toc_end]
    matched = []
    cursor = 0
    for e in entries:
        best, best_ratio, best_pos = None, 0.0, None
        for j in range(cursor, len(pool)):
            cand = pool[j]
            ratio = SequenceMatcher(None, e["norm"], cand["norm"]).ratio()
            # also reward prefix containment (Interlude vs Interlude ...).
            if e["norm"] and (e["norm"].startswith(cand["norm"])
                              or cand["norm"].startswith(e["norm"])):
                ratio = max(ratio, 0.92)
            if ratio > best_ratio:
                best, best_ratio, best_pos = cand, ratio, j
        if best is not None and best_ratio >= 0.72:
            matched.append({
                "title": e["title"],
                "start_index": best["start_index"],
                "level": e["level"],
                "ratio": round(best_ratio, 3),
            })
            cursor = best_pos + 1  # monotonic: next match must come later
    return matched