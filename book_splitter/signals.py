"""
signals.py
==========
THE SIGNAL LAYER of the weighted detection model.

Each signal is a small, independent predicate over a Block (in document
context). A signal returns a strength in [0,1] -- 0 means "did not fire".
No single signal decides anything; scoring.py combines them with weights.

The signals implement every cue requested:
  TOC match, heading style, outline level, custom 'chapter' style, font size,
  font weight (bold), alignment (centered), page break, section break,
  short text block, chapter keyword, part keyword, roman numeral, numeric
  chapter, all-caps title, and structural positioning (isolation).

Each signal docstring records WHY it matters and its false-pos / false-neg
risk -- the same content surfaced in the written scoring spec.
"""

from __future__ import annotations
import re

_ROMAN = re.compile(r"^\s*([IVXLCDM]{1,7})\s*[\.\:\)]?\s*$", re.I)
_CHAPTER_KW = re.compile(r"^\s*(chapter|chap\.?)\s+([0-9]+|[ivxlcdm]+)\b", re.I)
_PART_KW = re.compile(r"^\s*(part|book|section|volume)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_NUMERIC = re.compile(r"^\s*(\d{1,3})([\.\:\)]\s+\S|\s+\S)")  # "3. Title" / "3 Title"


def s_toc_match(block, ctx):
    """Block index is in the TOC-matched boundary set.
    WHY: a TOC is author-declared structure -- the strongest possible cue.
    FP risk: near-zero (TOC was already validated against body).
    FN risk: books with no TOC (then this simply never fires)."""
    return 1.0 if block.index in ctx["toc_indices"] else 0.0


def s_heading_style(block, ctx):
    """Resolved paragraph style is a heading/Title (level 0 or 1).
    WHY: styles encode semantic structure independent of appearance.
    FP risk: 'Title' reused for front-matter lines; mitigated by TOC + position.
    FN risk: manually formatted books that never apply heading styles (Babylon)."""
    if block.style_level is None:
        return 0.0
    return 1.0 if block.style_level == 0 else 0.55  # level1 is weaker (sub-section)


def s_outline_level(block, ctx):
    """Explicit paragraph outlineLvl present (set via Word outline view).
    WHY: the structural contract least affected by visual edits.
    FP risk: low. FN risk: absent in most casual documents."""
    # style_level already folds in explicit outlineLvl; reward level 0 lightly.
    return 0.6 if block.style_level == 0 else 0.0


def s_custom_chapter_style(block, ctx):
    """Style id/name literally contains 'chapter' (e.g. Quiver's 'chapter').
    WHY: a bespoke chapter style is an explicit authorial chapter marker.
    FP risk: low. FN risk: only present in some templates."""
    sid = (block.style_id or "").lower()
    return 1.0 if "chapter" in sid else 0.0


def s_font_size(block, ctx):
    """Run size sits in the document's heading size band (relative, not 16pt).
    WHY: large type is the classic chapter-title cue in manual layouts.
    FP risk: pull-quotes/drop-caps/title-page text -> mitigated by band bounds.
    FN risk: books whose titles are styled but not enlarged."""
    band = ctx["heading_band"]
    if not band or block.max_size is None:
        return 0.0
    lo, hi = band
    if lo <= block.max_size <= hi:
        return 1.0
    if block.max_size > hi:           # title-page sized: weak, not a chapter
        return 0.2
    return 0.0


def s_bold(block, ctx):
    """Paragraph is bold (run or style resolved).
    WHY: bold short lines are common manual chapter markers.
    FP risk: HIGH -- bold is overloaded (key terms, warnings). Low weight.
    FN risk: titles set in regular weight."""
    return 1.0 if block.bold else 0.0


def s_all_caps(block, ctx):
    """Title is ALL CAPS (textual or via w:caps).
    WHY: small-caps/all-caps titling is a real convention.
    FP risk: HIGH -- emphasis/promo text; demoted, and duplicates dropped later.
    FN risk: mixed-case titles."""
    return 0.7 if (block.is_textual_all_caps or block.all_caps_prop) else 0.0


def s_centered(block, ctx):
    """Paragraph is centered.
    WHY: chapter openers are frequently centered.
    FP risk: centered captions/quotes. Supporting cue only.
    FN risk: left/right-aligned titles (Babylon uses right-aligned too)."""
    return 0.6 if block.alignment == "center" else 0.0


def s_page_break_before(block, ctx):
    """Paragraph forces a new page (pageBreakBefore or a preceding page break).
    WHY: chapters typically start on a new page.
    FP risk: any deliberate page break. Supporting cue.
    FN risk: continuous-flow books."""
    return 0.8 if (block.page_break_before or block.index in ctx["page_break_after"]) else 0.0


def s_section_break(block, ctx):
    """A section break (sectPr/oddPage) coincides with this block.
    WHY: professional layouts start chapters on new sections/recto pages.
    FP risk: column/layout sections. Supporting cue.
    FN risk: single-section documents."""
    return 0.7 if block.index in ctx["section_break_at"] else 0.0


def s_short_text(block, ctx):
    """Short, title-length line (<= 12 words) with no terminal period.
    WHY: titles are short and unpunctuated; body sentences are long.
    FP risk: short body lines/list items. Supporting/gating cue.
    FN risk: long descriptive titles."""
    if not block.is_paragraph or block.is_empty:
        return 0.0
    if block.word_count <= 12 and not block.text.endswith((".", "?", "!")):
        return 0.5
    return 0.0


def s_chapter_keyword(block, ctx):
    """Text starts with 'Chapter N'.
    WHY: explicit and unambiguous chapter declaration.
    FP risk: cross-references ('see Chapter 3') -> mitigated by start-anchoring.
    FN risk: named chapters without the word 'Chapter'."""
    return 1.0 if _CHAPTER_KW.match(block.text or "") else 0.0


def s_part_keyword(block, ctx):
    """Text starts with 'Part/Book/Volume N'.
    WHY: marks the higher (part) level of the hierarchy.
    FP risk: 'part of...' -> mitigated by requiring a following number.
    FN risk: unnumbered parts."""
    return 1.0 if _PART_KW.match(block.text or "") else 0.0


def s_roman(block, ctx):
    """Whole line is a Roman numeral (I, II, III...).
    WHY: classic fiction chapter marker.
    FP risk: 'I' as the pronoun, names ('Henry VIII') -> isolation required.
    FN risk: arabic-numbered books."""
    return 0.9 if _ROMAN.match(block.text or "") else 0.0


def s_numeric_chapter(block, ctx):
    """Line begins with a bare number then a short title ('3 The Method').
    WHY: numbered chapter style.
    FP risk: numbered lists/figures. Supporting cue.
    FN risk: named chapters."""
    return 0.6 if _NUMERIC.match(block.text or "") else 0.0


def s_isolation(block, ctx):
    """Structural positioning: preceded by blank/break and followed by body.
    WHY: headings sit alone, with body text beneath -- a strong layout cue.
    FP risk: low. FN risk: dense layouts without spacing."""
    return 0.6 if block.index in ctx["isolated"] else 0.0


def s_division(block, ctx):
    """Text matches a known division label/numbering in EN/FR/DE
    (Chapter/Part/Book/Volume/Letter/Act/Scene/Story/Night/..., or a bare
    Roman/Arabic/Alpha number line). Generalises CHAPTER/PART keywords.
    WHY: explicit, language-independent structural declaration.
    FP risk: a body sentence starting 'Chapter 3 explains...' -> gated by the
    word-count limit in the candidate filter. FN risk: unlabeled named titles."""
    from .vocabulary import match_division
    m = match_division(block.text or "")
    if not m:
        return 0.0
    role = m["role"]
    if role in ("labeled", "ordinal_label"):
        return 1.0
    if role == "bare":
        return 0.6
    return 0.0      # 'named' / 'matter' rely on style/font corroboration


# Registry consumed by scoring.py (name -> function).
SIGNALS = {
    "TOC_MATCH": s_toc_match,
    "HEADING_STYLE": s_heading_style,
    "OUTLINE_LEVEL": s_outline_level,
    "CUSTOM_CHAPTER_STYLE": s_custom_chapter_style,
    "FONT_SIZE": s_font_size,
    "BOLD": s_bold,
    "ALL_CAPS": s_all_caps,
    "CENTERED": s_centered,
    "PAGE_BREAK_BEFORE": s_page_break_before,
    "SECTION_BREAK": s_section_break,
    "SHORT_TEXT": s_short_text,
    "CHAPTER_KEYWORD": s_chapter_keyword,
    "PART_KEYWORD": s_part_keyword,
    "DIVISION_LABEL": s_division,
    "ROMAN_NUMERAL": s_roman,
    "NUMERIC_CHAPTER": s_numeric_chapter,
    "ISOLATION": s_isolation,
}