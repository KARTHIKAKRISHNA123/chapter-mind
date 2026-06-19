"""
hierarchy.py
============
ADAPTIVE HIERARCHY DETECTION (RTCFR).

Infers a document-specific division hierarchy without assuming "part > chapter".
A canonical partial order over division *vocabularies* is provided; the engine
keeps only the ranks actually present in the book and compacts them to depths
0..k, so a play (act/scene), a treatise (part/chapter/section) and a multi-volume
work (volume/book/part) are all handled by the same code.

Deterministic. No ML. Pure keyword + style-level classification.
"""

from __future__ import annotations
import re

# Canonical partial order. Smaller rank = higher in the tree.
# Vocabularies that rarely co-occur (act vs part, scene vs section) share a rank.
DIVISION_RANKS = {
    "volume": 0,
    "book": 1,
    "part": 2, "act": 2,
    "chapter": 3, "canto": 3, "stave": 3,
    "section": 4, "scene": 4,
    "subsection": 5,
}

# Front/back-matter divisions: chapter-level boundaries, excluded from numbering.
MATTER = {
    "prologue", "epilogue", "interlude", "foreword", "afterword", "preface",
    "introduction", "appendix", "index", "contents", "glossary", "preamble",
    "acknowledgments", "acknowledgements", "dedication", "colophon", "notes",
}

_DIV_KW = re.compile(
    r"^\s*(volume|book|part|act|scene|section|subsection|chapter|canto|stave)\b",
    re.I)
_MATTER_KW = re.compile(r"^\s*(" + "|".join(sorted(MATTER)) + r")\b", re.I)

_ROMAN_MAP = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12}


def _roman_to_int(s: str):
    s = s.lower()
    if not s or any(c not in _ROMAN_MAP for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN_MAP[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _extract_number(rest: str):
    rest = rest.strip(" :.\u2013\u2014-")
    if not rest:
        return None
    tok = rest.split()[0].lower().strip(":.-")
    if tok.isdigit():
        return int(tok)
    r = _roman_to_int(tok)
    if r:
        return r
    return _NUM_WORDS.get(tok)


def classify(title: str, style_level=None):
    """Return (div_type, rank, number|None, is_matter)."""
    t = (title or "").strip()
    if _MATTER_KW.match(t):
        return ("chapter", DIVISION_RANKS["chapter"], None, True)
    m = _DIV_KW.match(t)
    if m:
        kw = m.group(1).lower()
        return (kw, DIVISION_RANKS[kw], _extract_number(t[m.end():]), False)
    if style_level == 0:
        return ("chapter", DIVISION_RANKS["chapter"], None, False)
    if style_level == 1:
        return ("section", DIVISION_RANKS["section"], None, False)
    return ("chapter", DIVISION_RANKS["chapter"], None, False)


def infer(candidates):
    """Annotate each candidate with div_type, _rank, number, is_matter, depth.
    Returns the ordered list of (rank, div_type) present = the inferred
    document hierarchy."""
    for c in candidates:
        dt, rank, num, matter = classify(c.title, getattr(c, "style_level", None))
        c.div_type, c._rank, c.number, c.is_matter = dt, rank, num, matter
    present = sorted({c._rank for c in candidates})
    remap = {r: i for i, r in enumerate(present)}
    for c in candidates:
        c.depth = remap[c._rank]
    return [dt for _, dt in sorted({(c._rank, c.div_type) for c in candidates})]