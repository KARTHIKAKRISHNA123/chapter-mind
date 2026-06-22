"""
detector.py
===========
THE DETECTION ORCHESTRATOR (the "layered detection system").

Layers, highest authority first:
  L1  TOC Intelligence ....... author-declared boundaries (toc.py)
  L2  Style / outline level ... semantic heading styles (blocks.StyleResolver)
  L3  Visual + text fallback .. relative font band, bold, keywords, roman, caps

It also performs the two operations that make real books work:
  * MERGE consecutive heading paragraphs into one boundary, so a title split
    across lines ("The Man Who" / "Desired Gold") yields a single chapter.
  * ABSTAIN: if it cannot find at least two confident boundaries it returns a
    single-file passthrough plan instead of shredding the book.

Output: a ChapterPlan = ordered list of Chapter(title, start, end, confidence).
"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field

from .blocks import body_baseline_size
from .models import UnifiedDocument
from .scoring import score_block
from . import toc as toc_mod
from . import decision_engine as de
from . import vocabulary as V
from .signals import _ROMAN, _CHAPTER_KW, _PART_KW, _NUMERIC

@dataclass
class Chapter:
    title: str
    start: int                 # inclusive block-child index
    end: int                   # exclusive
    level: str = "chapter"
    confidence: float = 1.0
    fired: dict = field(default_factory=dict)
    depth: int = None          # RTCFR: adaptive hierarchy depth
    div_type: str = None       # RTCFR: volume/book/part/act/chapter/section/scene
    number: object = None      # RTCFR: parsed division number (progression checks)
    explanation: dict = None   # RTCFR: per-boundary explainability record


@dataclass
class ChapterPlan:
    chapters: list
    abstained: bool = False
    reason: str = ""
    diagnostics: dict = field(default_factory=dict)


class _MergedView:
    """A merged multi-paragraph heading presented to the scorer as one block."""
    is_paragraph = True
    is_table = False
    is_empty = False

    def __init__(self, members):
        first = members[0]
        self.index = first.index
        self.members = members
        self.text = " ".join(m.text for m in members if m.text).strip()
        self.word_count = len(self.text.split())
        self.style_id = next((m.style_id for m in members if m.style_id), None)
        self.style_level = min(
            [m.style_level for m in members if m.style_level is not None],
            default=None)
        self.bold = any(m.bold for m in members)
        self.all_caps_prop = any(m.all_caps_prop for m in members)
        self.max_size = max([m.max_size for m in members if m.max_size], default=None)
        self.alignment = next((m.alignment for m in members if m.alignment), None)
        self.page_break_before = any(m.page_break_before for m in members)

    @property
    def is_textual_all_caps(self):
        letters = [c for c in self.text if c.isalpha()]
        return bool(letters) and all(c.isupper() for c in letters)

    def normalized_title(self):
        return " ".join(self.text.lower().replace(":", " ").split())


def compute_baselines(blocks):
    """Body font size + heading size band (relative, never hard-coded)."""
    body = body_baseline_size(blocks)
    counter = Counter()
    for b in blocks:
        if b.is_paragraph and not b.is_empty and b.max_size:
            counter[b.max_size] += 1
    # heading band = most frequent size meaningfully above body, excluding the
    # extreme title-page sizes (capped at ~2.4x body).
    cand = {s: c for s, c in counter.items() if body * 1.25 < s <= body * 2.4}
    band = None
    if cand:
        s_h = max(cand, key=cand.get)
        band = (s_h - 1, s_h + 2)
    return body, band


def _is_raw_candidate(b, band):
    """Cheap pre-filter: could this paragraph be a heading at all?"""
    if not b.is_paragraph or b.is_empty:
        return False
    if b.style_level == 0:
        return True
    if b.style_id and "chapter" in b.style_id.lower():
        return True
    if _CHAPTER_KW.match(b.text) or _PART_KW.match(b.text) or _ROMAN.match(b.text):
        return True
    if b.word_count <= 12 and _NUMERIC.match(b.text or ""):   # "N. Title" — any script
        return True
    if b.word_count <= 12:
        m = V.match_division(b.text)
        if m and m["role"] in ("labeled", "ordinal_label"):
            return True
        if m and m["role"] == "bare" and b.word_count <= 3:
            return True
    if band and b.max_size and band[0] <= b.max_size <= band[1] and b.word_count <= 12:
        return True
    return False


def _structural_candidate(b, page_starts, isolated):
    """Language-agnostic fallback: a short, non-body line marked structurally
    (opens a page/section, or is bold / centered / isolated).

    Lets manually-formatted books in any script surface candidates for the
    scorer to judge.  The fallback runs ONLY when the precise gate finds
    fewer than 2 candidates, so English/styled/keyword books are never
    affected.
    """
    if not b.is_paragraph or b.is_empty or b.word_count > 12:
        return False
    # Sentence-ending punctuation -> body text, not a heading
    if b.text.endswith((".", "?", "!", "।", "॥")):   # Latin + Devanagari danda
        return False
    return (b.page_break_before
            or b.index in page_starts
            or b.bold
            or b.alignment == "center"
            or b.index in isolated)


def _merge_candidates(blocks, band, page_starts=frozenset(), isolated=frozenset()):
    """Group consecutive raw candidates (gap <= 2) into merged headings.

    When the precise pass (_is_raw_candidate) finds fewer than 2 hits, falls
    back to _structural_candidate so that non-Latin, manually-formatted books
    can still surface headings via page-break / bold / centered / isolated
    signals.
    """
    raw = [b for b in blocks if _is_raw_candidate(b, band)]
    if len(raw) < 2:        # nothing precise fired -- try structural signals
        raw = [b for b in blocks if _structural_candidate(b, page_starts, isolated)]
    groups, cur, prev = [], [], None
    for b in raw:
        if prev is not None and b.index - prev <= 2:
            cur.append(b)
        else:
            if cur:
                groups.append(_MergedView(cur))
            cur = [b]
        prev = b.index
    if cur:
        groups.append(_MergedView(cur))
    return groups


def detect(doc: UnifiedDocument, level_filter="auto") -> ChapterPlan:
    blocks = doc.blocks                       # already built by the adapter
    body_size, band = compute_baselines(blocks)
    page_break_after = doc.page_break_after   # adapter-computed
    section_at       = doc.section_break_at
    isolated         = doc.isolated

    merged = _merge_candidates(blocks, band,
                               page_break_after | section_at, isolated)

    # ---- L1: TOC intelligence -------------------------------------------
    # Prefer adapter-extracted, INDEX-BASED TOC (DOCX _Toc anchors): language-
    # agnostic ground truth, no text matching needed. Fall back to the
    # text-based region matcher when no such TOC exists (hand-typed TOCs,
    # EPUBs, etc.).
    pre_toc = [e for e in (doc.toc_entries or []) if "index" in e]
    if pre_toc:
        region = None
        toc_matched = [{"start_index": e["index"], "title": e["title"] or "(untitled)",
                        "level": e["level"], "ratio": 1.0}
                       for e in sorted(pre_toc, key=lambda e: e["index"])]
        toc_indices = {m["start_index"] for m in toc_matched}
        n_entries = len(toc_matched)
        toc_authoritative = len(toc_matched) >= 2      # _Toc anchors are ground truth
    else:
        toc_indices = set()
        toc_matched = []
        region = toc_mod.detect_toc_region(blocks)
        if region is not None:
            entries = toc_mod.extract_entries(blocks, region)
            cand_dicts = [{"start_index": m.index, "norm": m.normalized_title(),
                           "text": m.text} for m in merged]
            toc_matched = toc_mod.match_to_body(entries, blocks, region[1], cand_dicts)
            toc_indices = {m["start_index"] for m in toc_matched}
        n_entries = len(toc_mod.extract_entries(blocks, region)) if region else 0
        toc_authoritative = bool(toc_matched) and len(toc_matched) >= max(5, 0.5 * n_entries)

    # Context dict consumed by every signal function in signals.py.
    # All five keys must be present; signals guard against None/empty themselves.
    ctx = {
        "toc_indices":      toc_indices,
        "heading_band":     band,
        "page_break_after": page_break_after,
        "section_break_at": section_at,
        "isolated":         isolated,
    }

    # ---- build candidates for the Decision Engine ------------------------
    cand_list = []
    if toc_authoritative:
        for m in toc_matched:
            cand_list.append(de.Candidate(
                index=m["start_index"], title=m["title"],
                norm=" ".join(m["title"].lower().replace(":", " ").split()),
                level=("part" if m["level"] == 0 else "chapter"),
                confidence=round(0.6 + 0.4 * m["ratio"], 3),
                fired={"TOC_MATCH": 1.0},
                style_level=0 if m["level"] == 0 else None))
    else:
        toc_lo, toc_hi = (region if region else (-1, -1))
        for m in merged:
            if toc_lo <= m.index <= toc_hi:
                continue
            res = score_block(m, ctx)
            if res["confidence"] <= 0:
                continue
            cand_list.append(de.Candidate(
                index=m.index, title=m.text or "(untitled)",
                norm=m.normalized_title(), level=res["level"],
                confidence=res["confidence"], fired=res["fired"],
                style_level=m.style_level))

    # ---- Decision Engine: thresholds -> validation -> abstention ---------
    decision = de.decide(cand_list, {
        "toc_authoritative": toc_authoritative,
        "toc_indices": toc_indices, "toc_region": region,
        "toc_entries": n_entries, "total_blocks": len(blocks),
    })
    boundaries = decision.boundaries

    # optional level filtering (auto keeps everything)
    if level_filter in ("part", "chapter", "section"):
        boundaries = [b for b in boundaries if b.level == level_filter] or boundaries
    boundaries.sort(key=lambda c: c.index)

    diagnostics = {
        "body_size": body_size, "heading_band": band,
        "toc_region": region, "toc_entries": n_entries,
        "toc_matched": len(toc_matched), "toc_authoritative": toc_authoritative,
        "merged_candidates": len(merged), "accepted_boundaries": len(boundaries),
        "thresholds": vars(decision.thresholds),
        "abstain_reasons": decision.abstain_reasons,
        "hierarchy": decision.hierarchy,
        "structure": decision.diagnostics.get("structure"),
        "decision": decision.diagnostics,
    }

    # ---- abstention (Decision Engine framework) --------------------------
    if decision.abstain or len(boundaries) < 2:
        return ChapterPlan(
            chapters=[Chapter("(whole document)", 0, len(blocks),
                              confidence=0.0)],
            abstained=True,
            reason="; ".join(decision.abstain_reasons)
                   or "insufficient validated structure",
            diagnostics=diagnostics)

    # ---- assemble contiguous chapter ranges -----------------------------
    expl = {e["index"]: e for e in decision.explanations}
    chapters = []
    if boundaries[0].index > 0:
        chapters.append(Chapter("Front Matter", 0, boundaries[0].index,
                                level="front", confidence=1.0))
    for i, b in enumerate(boundaries):
        end = boundaries[i + 1].index if i + 1 < len(boundaries) else len(blocks)
        chapters.append(Chapter(
            b.title, b.index, end, (b.div_type or b.level), b.confidence, b.fired,
            depth=b.depth, div_type=b.div_type, number=b.number,
            explanation=expl.get(b.index)))

    return ChapterPlan(chapters=chapters, abstained=False, diagnostics=diagnostics)
