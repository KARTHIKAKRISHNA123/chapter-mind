"""
scoring.py
==========
THE CONFIDENCE MODEL: family-based noisy-OR.

Design
------
Signals are grouped into four independent *evidence families*:

  authority   -- TOC match, heading style, outline level, custom chapter style
  lexical     -- chapter/part keywords, division labels, numeric/roman patterns
  typographic -- font size, bold, all-caps, centered, short text
  layout      -- page break, section break, isolation

Within a family, signals are correlated (they all say the same kind of thing),
so we take the **MAX** -- extra signals in the same family add little new info.

Across families, signals are independent (style doesn't imply keyword, which
doesn't imply layout), so we combine with **noisy-OR**:

    P(heading) = 1 - ∏(1 - family_evidence_i)

This is exactly what rewards genuine multi-source agreement over a lucky single
signal. A heading that fires authority + lexical + layout (~0.9) reliably
outranks one that only fires typographic (0.6) or layout (0.5) alone.

The model is:
  * Bounded [0,1] with no normaliser needed.
  * Fully attributable: fired, families, and band ship on every scored block.
  * Calibration-safe: any weight/evidence change has a predictable, monotone
    effect. Run lock_golden.py on every golden book after any change, then
    confirm pytest -m regression is green.
"""

from __future__ import annotations
from .signals import SIGNALS

# ---------------------------------------------------------------------------
# Signal evidence table
# ---------------------------------------------------------------------------
# Each value = P(heading | only THIS signal fired).
# Higher = stronger standalone predictor.
EVIDENCE = {
    "TOC_MATCH":            0.97,   # author-declared structure
    "CHAPTER_KEYWORD":      0.95,   # explicit "Chapter N"
    "HEADING_STYLE":        0.90,   # resolved heading/Title style
    "CUSTOM_CHAPTER_STYLE": 0.90,   # bespoke chapter style
    "DIVISION_LABEL":       0.88,   # explicit multilingual division label
    "OUTLINE_LEVEL":        0.85,   # explicit outline contract
    "PART_KEYWORD":         0.80,   # explicit "Part N" (higher level)
    "FONT_SIZE":            0.62,   # in the heading size band (relative)
    "NUMERIC_CHAPTER":      0.55,   # "3 Title"
    "ROMAN_NUMERAL":        0.50,   # isolated roman numeral
    "PAGE_BREAK_BEFORE":    0.48,   # new-page start
    "SECTION_BREAK":        0.48,   # new-section / recto start
    "SPINE_START":          0.48,   # new EPUB spine document (one-file-per-chapter)
    "ISOLATION":            0.40,   # surrounded by blank, followed by body
    "CENTERED":             0.32,   # supporting layout cue
    "ALL_CAPS":             0.30,   # supporting, overloaded
    "BOLD":                 0.28,   # supporting, heavily overloaded
    "SHORT_TEXT":           0.20,   # gating/supporting
}

# ---------------------------------------------------------------------------
# Evidence family assignment
# ---------------------------------------------------------------------------
# Within-family: correlated -> MAX (extra signals add little new info).
# Across-family: independent -> noisy-OR (genuine agreement compounds).
FAMILY = {
    # Author-declared / structural authority
    "TOC_MATCH":            "authority",
    "HEADING_STYLE":        "authority",
    "OUTLINE_LEVEL":        "authority",
    "CUSTOM_CHAPTER_STYLE": "authority",
    # Textual / lexical cues
    "CHAPTER_KEYWORD":      "lexical",
    "PART_KEYWORD":         "lexical",
    "DIVISION_LABEL":       "lexical",
    "NUMERIC_CHAPTER":      "lexical",
    "ROMAN_NUMERAL":        "lexical",
    # Visual / typographic cues
    "FONT_SIZE":            "typographic",
    "BOLD":                 "typographic",
    "ALL_CAPS":             "typographic",
    "CENTERED":             "typographic",
    "SHORT_TEXT":           "typographic",
    # Layout / structural positioning
    "PAGE_BREAK_BEFORE":    "layout",
    "SECTION_BREAK":        "layout",
    "SPINE_START":          "layout",
    "ISOLATION":            "layout",
}

# ---------------------------------------------------------------------------
# Confidence bands
# ---------------------------------------------------------------------------
_BANDS = [
    (0.90, "very_high"),    # authority + at least one other family
    (0.75, "high"),         # two strong independent families
    (0.55, "medium"),       # one strong family or two weak ones
    (0.0,  "low"),          # single weak signal
]


def confidence_band(c: float) -> str:
    """Map a [0,1] confidence to a human-readable band name."""
    for lo, name in _BANDS:
        if c >= lo:
            return name
    return "low"


def _noisy_or(values) -> float:
    """Combine independent probabilities via 1 - ∏(1 - p_i)."""
    p = 1.0
    for v in values:
        p *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - p


def score_block(block, ctx) -> dict:
    """Return scoring result for one block.

    Returns
    -------
    dict with keys:
        confidence  float [0,1]
        level       "part" | "chapter" | "section"
        fired       {signal_name: effective_strength}   -- for attribution
        families    {family_name: max_evidence}          -- per-family rollup
        band        "very_high" | "high" | "medium" | "low"
    """
    fired: dict[str, float] = {}
    fam: dict[str, float] = {}

    for name, fn in SIGNALS.items():
        strength = fn(block, ctx)
        if strength <= 0:
            continue
        e = strength * EVIDENCE[name]
        fired[name] = round(e, 3)
        f = FAMILY[name]
        fam[f] = max(fam.get(f, 0.0), e)       # within-family: correlated -> MAX

    conf = _noisy_or(fam.values())              # across-family: independent -> OR

    # ---- body-likeness dampener (multiplicative) -------------------------
    # A bold full sentence should NOT become a chapter boundary.
    # Devanagari danda (।/॥) included so Hindi body sentences are penalised
    # exactly like English ones.
    if block.is_paragraph and not block.is_empty:
        if (block.text.endswith((".", "?", "!", "।", "॥"))
                and block.word_count > 8):
            conf *= 0.4
        if block.word_count > 16:
            conf *= 0.3

    conf = round(conf, 3)

    # ---- level inference ------------------------------------------------
    if "PART_KEYWORD" in fired:
        level = "part"
    elif fired.keys() & {"TOC_MATCH", "CHAPTER_KEYWORD", "CUSTOM_CHAPTER_STYLE"} \
            or block.style_level == 0 or "FONT_SIZE" in fired:
        level = "chapter"
    else:
        level = "section"

    return {
        "confidence": conf,
        "level": level,
        "fired": fired,
        "families": {k: round(v, 3) for k, v in fam.items()},
        "band": confidence_band(conf),
    }
