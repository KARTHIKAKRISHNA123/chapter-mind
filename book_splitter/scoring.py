"""
scoring.py
==========
THE WEIGHTED CONFIDENCE MODEL.

Each signal carries a weight reflecting its reliability. A block's raw score is
the weighted sum of fired signals; we then divide by a soft normaliser and clamp
to [0,1] so the number reads like a confidence.

Design rules embodied here:
  * Semantic/structural signals (TOC, heading style, keyword) dominate.
  * Visual signals (bold, all-caps, centered) are SUPPORTING -- individually too
    weak to declare a chapter, collectively enough when they agree.
  * PENALTIES subtract confidence for body-like blocks (long text, terminal
    punctuation), so a bold full sentence does not become a false chapter.

The weights are not magic constants pulled from the air for one book: they rank
signals by how often each is *right* across book classes (the same ordering used
in the written scoring spec). They can be overridden per document class.
"""

from __future__ import annotations
from .signals import SIGNALS

# Signal -> weight. Higher = more trusted. (Sum is intentionally > 1; the
# normaliser below keeps scores in range while letting strong agreement win.)
WEIGHTS = {
    "TOC_MATCH":            1.00,   # author-declared structure
    "DIVISION_LABEL":       0.92,   # explicit multilingual division label
    "CHAPTER_KEYWORD":      0.95,   # explicit "Chapter N"
    "CUSTOM_CHAPTER_STYLE": 0.90,   # bespoke chapter style
    "HEADING_STYLE":        0.85,   # resolved heading/Title style
    "PART_KEYWORD":         0.80,   # explicit "Part N" (higher level)
    "OUTLINE_LEVEL":        0.75,   # explicit outline contract
    "FONT_SIZE":            0.70,   # in the heading size band (relative)
    "PAGE_BREAK_BEFORE":    0.45,   # new-page start
    "SECTION_BREAK":        0.45,   # new-section / recto start
    "ROMAN_NUMERAL":        0.45,   # isolated roman numeral
    "ISOLATION":            0.40,   # surrounded by blank, followed by body
    "CENTERED":             0.30,   # supporting layout cue
    "NUMERIC_CHAPTER":      0.30,   # "3 Title"
    "ALL_CAPS":             0.30,   # supporting, overloaded
    "BOLD":                 0.25,   # supporting, heavily overloaded
    "SHORT_TEXT":           0.20,   # gating/supporting
}

# Soft normaliser: the weight mass that represents "clearly a chapter".
# Reaching this much agreement yields ~1.0 confidence.
_NORMALISER = 1.6

# NOTE (RTCFR): fixed ACCEPT/REVIEW thresholds were removed. score_block now
# only produces a confidence; the accept/review cut is computed per-document by
# decision_engine.compute_adaptive_thresholds() from the score distribution.


def score_block(block, ctx) -> dict:
    """Return {confidence, level, fired:{signal:strength*weight}} for a block."""
    fired = {}
    raw = 0.0
    for name, fn in SIGNALS.items():
        strength = fn(block, ctx)
        if strength > 0:
            contribution = strength * WEIGHTS[name]
            fired[name] = round(contribution, 3)
            raw += contribution

    # ---- penalties: push body-like paragraphs back down -----------------
    penalty = 0.0
    if block.is_paragraph and not block.is_empty:
        if block.text.endswith((".", "?", "!")) and block.word_count > 8:
            penalty += 0.5                      # looks like a real sentence
        if block.word_count > 16:
            penalty += 0.6                      # too long to be a title
    raw = max(0.0, raw - penalty)

    confidence = min(1.0, raw / _NORMALISER)

    # ---- level inference -------------------------------------------------
    # part-level if a part keyword fired or style level 0 + 'part' text;
    # otherwise chapter level for level-0 cues, section level for weaker ones.
    if "PART_KEYWORD" in fired:
        level = "part"
    elif fired.keys() & {"TOC_MATCH", "CHAPTER_KEYWORD", "CUSTOM_CHAPTER_STYLE"} \
            or block.style_level == 0 or "FONT_SIZE" in fired:
        level = "chapter"
    else:
        level = "section"

    return {"confidence": round(confidence, 3), "level": level,
            "fired": fired, "penalty": round(penalty, 3)}