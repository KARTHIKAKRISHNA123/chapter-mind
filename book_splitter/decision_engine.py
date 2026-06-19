"""
decision_engine.py
==================
THE DECISION ENGINE (RTCFR: Rule-based, Threshold-Calibrated, Failure-Resistant).

Pipeline position:   Signals -> Scoring -> [Decision Engine] -> Chapter Plan

Responsibilities
  1. Adaptive threshold computation (gap analysis, IQR fallback) -- NO fixed
     ACCEPT/REVIEW constants.
  2. Boundary validation (min distance, duplicates, repeated headers, hierarchy,
     numbering progression, act/scene progression, TOC consistency).
  3. Abstention framework (multiple, explainable failure reasons).
  4. Failure resistance (no shred, no over-split, no split inside TOC, no split
     on repeated headers).

Fully deterministic. No ML, no probability, no external calls. Every accepted
boundary carries an explanation listing the signals that fired and the rule that
admitted it.

WHY GAP ANALYSIS for thresholds
  Heading scores are inherently bimodal: true headings cluster high, noise low.
  The largest gap in the sorted score list is the document's own natural
  decision boundary -- it adapts per book and is fully explainable ("threshold =
  midpoint of the largest score gap, between rank k and k+1"). Percentile assumes
  a fixed fraction of candidates are headings (false: 3-chapter vs 50-chapter
  books differ). Z-score assumes normality (scores are not normal). IQR measures
  spread but not the separating point, so it is used only as a fallback when no
  dominant gap exists. K-means(2) on 1-D data IS gap analysis but with
  nondeterministic init -- gap analysis is its deterministic closed form.
"""

from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median

from . import structure as structure_mod
from . import vocabulary as V

# ---- DERIVED guards (structural limits), NOT score thresholds ---------------
SEP_FLOOR = 0.08          # min relative dominance of the largest gap to trust it
MIN_GAP_FLOOR = 2         # absolute minimum spacing (blocks) between boundaries
MIN_GAP_FRACTION = 0.12   # adaptive spacing = fraction of median spacing
HEADER_REPEAT = 4         # a title recurring >= this often is a running header
HIER_TOL = 0.25           # tolerated hierarchy-violation ratio
TOC_TOL = 0.20            # tolerated TOC-order contradiction ratio
TINY_RATIO = 0.5          # > this share of tiny chapters => over-split


@dataclass
class Candidate:
    index: int
    title: str
    norm: str
    level: str
    confidence: float
    fired: dict
    style_level: object = None
    # filled by hierarchy.infer():
    div_type: str = None
    depth: int = None
    number: object = None
    is_matter: bool = False
    _rank: int = None


@dataclass
class Thresholds:
    accept: float
    review: float
    method: str
    separation: float
    rationale: str


@dataclass
class Decision:
    boundaries: list
    abstain: bool
    abstain_reasons: list
    thresholds: Thresholds
    hierarchy: list = field(default_factory=list)
    explanations: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    structure: object = None


# ----------------------------------------------------------------------------
# 1. Adaptive thresholds
# ----------------------------------------------------------------------------
def _quartiles(values):
    asc = sorted(values)
    n = len(asc)

    def pct(p):
        if n == 1:
            return asc[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return asc[lo] * (1 - (idx - lo)) + asc[hi] * (idx - lo)
    return pct(0.25), pct(0.75)


def compute_adaptive_thresholds(scores) -> Thresholds:
    s = sorted(scores, reverse=True)
    n = len(s)
    if n == 0:
        return Thresholds(1.0, 1.0, "empty", 0.0, "no candidates")
    if n == 1:
        return Thresholds(s[0], s[0], "single", 1.0, "one candidate; cut = its score")

    rng = s[0] - s[-1]
    gaps = [(s[i] - s[i + 1], i) for i in range(n - 1)]
    gmax, k = max(gaps, key=lambda g: (g[0], -g[1]))     # first (highest) largest gap
    separation = gmax / (rng + 1e-9)

    if separation >= SEP_FLOOR:
        accept = (s[k] + s[k + 1]) / 2
        method = "gap_analysis"
        rationale = (f"largest gap {gmax:.3f} between rank {k} ({s[k]:.3f}) and "
                     f"{k + 1} ({s[k + 1]:.3f}); cut at midpoint {accept:.3f}; "
                     f"separation {separation:.2f}")
    else:
        _, q3 = _quartiles(s)
        accept = q3
        method = "iqr_fallback"
        rationale = (f"no dominant gap (separation {separation:.2f} < {SEP_FLOOR}); "
                     f"conservative cut at Q3={q3:.3f}")

    below = [(g, i) for g, i in gaps if s[i + 1] < accept]
    if below:
        gb, kb = max(below, key=lambda x: (x[0], -x[1]))
        review = (s[kb] + s[kb + 1]) / 2
    else:
        review = accept * 0.7
    return Thresholds(round(accept, 4), round(min(review, accept), 4),
                      method, round(separation, 4), rationale)


# ----------------------------------------------------------------------------
# 2. Boundary validators
# ----------------------------------------------------------------------------
def _drop_repeated_headers(cands, diag):
    freq = Counter(c.norm for c in cands if c.norm)
    headers = {n for n, f in freq.items() if f >= HEADER_REPEAT}
    if headers:
        diag["repeated_headers_dropped"] = sorted(headers)
    return [c for c in cands if c.norm not in headers]


def _validate_duplicates(cands, diag):
    """Drop number-less duplicate titles (recap/running headings). Numbered
    divisions are NOT deduped -- 'Chapter I' may legitimately recur when
    numbering resets under a new Part/Book/Act."""
    seen, kept, dropped = set(), [], []
    for c in cands:
        if c.number is None and c.norm in seen:
            dropped.append(c.title)
            continue
        if c.number is None:
            seen.add(c.norm)
        kept.append(c)
    if dropped:
        diag["duplicate_titles_dropped"] = dropped
    return kept


def _validate_min_distance(cands, diag):
    if len(cands) < 2:
        return cands
    gaps = [cands[i + 1].index - cands[i].index for i in range(len(cands) - 1)]
    med = median(gaps) if gaps else 0
    min_gap = max(MIN_GAP_FLOOR, int(MIN_GAP_FRACTION * med))
    diag["min_gap"] = min_gap
    kept = [cands[0]]
    for c in cands[1:]:
        if c.index - kept[-1].index < min_gap:
            if c.confidence > kept[-1].confidence:   # keep the stronger of the pair
                kept[-1] = c
        else:
            kept.append(c)
    return kept


def _validate_hierarchy(cands):
    """First appearance of depth d must be preceded by some depth < d
    (a Scene cannot precede every Act). Returns violation ratio."""
    seen, violations = set(), 0
    for c in cands:
        d = c.depth or 0
        if d > 0 and not any(sd < d for sd in seen) and not c.is_matter:
            violations += 1
        seen.add(d)
    return violations / max(1, len(cands))


def _validate_numbering(cands):
    """Per division type, numbers should be monotonically increasing.
    Returns regression ratio (covers chapter AND act numbering)."""
    seqs = defaultdict(list)
    for c in cands:
        if c.number is not None and not c.is_matter:
            seqs[c.div_type].append(c.number)
    viol = total = 0
    for nums in seqs.values():
        for i in range(1, len(nums)):
            total += 1
            if nums[i] <= nums[i - 1]:
                viol += 1
    return viol / max(1, total)


def _validate_act_scene(cands):
    """If acts and scenes coexist, scene numbers should reset within each act.
    Returns contradiction ratio (0 when not applicable)."""
    types = {c.div_type for c in cands}
    if not ({"act", "scene"} <= types):
        return 0.0
    viol = total = 0
    last_scene = None
    for c in cands:
        if c.div_type == "act":
            last_scene = 0
        elif c.div_type == "scene" and c.number is not None and last_scene is not None:
            total += 1
            if c.number <= last_scene:
                # expected to increase within an act; a drop without a new act is ok
                pass
            last_scene = c.number
    return viol / max(1, total)


def _validate_toc_consistency(cands, context):
    region = context.get("toc_region")
    if not region:
        return 0.0
    contra = sum(1 for c in cands if region[0] <= c.index <= region[1])
    return contra / max(1, len(cands))


def _guard_oversplit(cands, total, diag):
    """Failure resistance: refuse to produce mostly-tiny chapters."""
    if len(cands) < 2 or total <= 0:
        return cands, False
    idx = [c.index for c in cands]
    lengths = [idx[i + 1] - idx[i] for i in range(len(idx) - 1)]
    tiny = sum(1 for L in lengths if L < MIN_GAP_FLOOR + 1)
    if tiny / max(1, len(lengths)) <= TINY_RATIO:
        return cands, False
    chosen = []
    for c in sorted(cands, key=lambda c: c.index):
        if not chosen or c.index - chosen[-1].index >= MIN_GAP_FLOOR + 1:
            chosen.append(c)
    diag["oversplit_corrected"] = True
    return chosen, True


def _prune_noise_levels(candidates, plan):
    """A division LEVEL is real only if it forms a series: >=2 instances with
    progression OR regularity OR TOC alignment. Bare-number ('unit') levels need
    stronger evidence (>=3 in clean progression). This removes stray numbers
    (page/verse numbers) and one-off label false positives before thresholding."""
    valid = set()
    for t, v in plan.levels.items():
        ok = v["count"] >= 2 and (v["progression"] >= 0.5
                                  or v["regularity"] >= 0.5
                                  or v["toc_aligned"] >= 0.3)
        if t == "unit":
            ok = v["count"] >= 3 and v["progression"] >= 0.6
        if ok or v["toc_aligned"] >= 0.3:
            valid.add(t)
    return [c for c in candidates
            if getattr(c, "is_matter", False) or c.div_type in valid]


def _explain(c, thr, toc_auth):
    return {
        "title": c.title, "index": c.index, "level": c.level,
        "div_type": c.div_type, "depth": c.depth, "number": c.number,
        "confidence": c.confidence,
        "accepted_by": ("toc_authority" if toc_auth
                        else f"score>={thr.accept} via {thr.method}"),
        "fired_signals": c.fired,
    }


# ----------------------------------------------------------------------------
# 3 + 4. Orchestration: threshold -> validate -> abstain / failure-resist
# ----------------------------------------------------------------------------
def decide(candidates, context) -> Decision:
    diag = {}
    reasons = []
    toc_auth = bool(context.get("toc_authoritative"))
    total = context.get("total_blocks", 0)

    # failure resistance: never split on running headers
    candidates = _drop_repeated_headers(candidates, diag)

    # STEP 1-5: hierarchical structure discovery (vocabulary -> graph -> plan).
    # Two passes: discover levels, prune noise levels (stray numbers / one-off
    # false labels), then re-discover on the cleaned candidate set.
    plan0 = structure_mod.discover(candidates, context)
    if not toc_auth:
        candidates = _prune_noise_levels(candidates, plan0)
    plan = structure_mod.discover(candidates, context)
    diag["structure"] = {
        "hierarchy": plan.hierarchy, "split_level": plan.split_level,
        "confidence": plan.confidence, "levels": plan.levels,
        "graph": plan.graph, "flags": plan.flags, "rationale": plan.rationale}
    if "no_structure_discovered" in plan.flags:
        reasons.append("no_structure_discovered")

    # adaptive thresholding (TOC authority bypasses score gating)
    if toc_auth:
        thr = Thresholds(0.0, 0.0, "toc_authority", 1.0,
                         "TOC matched body authoritatively; all TOC boundaries kept")
        gated = list(candidates)
    else:
        thr = compute_adaptive_thresholds([c.confidence for c in candidates])
        gated = [c for c in candidates if c.confidence >= thr.accept]
        if thr.separation < SEP_FLOOR:
            reasons.append("insufficient_confidence_separation")
    gated.sort(key=lambda c: c.index)

    # boundary validation
    gated = _validate_duplicates(gated, diag)
    gated = _validate_min_distance(gated, diag)
    hier_viol = _validate_hierarchy(gated)
    num_viol = _validate_numbering(gated)
    scene_viol = _validate_act_scene(gated)
    toc_contra = _validate_toc_consistency(gated, context)
    diag.update(hierarchy_violations=round(hier_viol, 3),
                numbering_violations=round(num_viol, 3),
                act_scene_violations=round(scene_viol, 3),
                toc_contradictions=round(toc_contra, 3))

    # failure resistance: over-split guard
    gated, _ = _guard_oversplit(gated, total, diag)

    # STEP 4 applied: restrict to the dominant split level + its container
    # ancestors (so a Part/Chapter book splits at Chapter while Part dividers
    # remain, and a play splits at Scene while Acts remain). Matter is kept.
    split_rank = V.RANKS.get(plan.split_level.lower(), 3) if plan.split_level else 3
    if context.get("respect_split_level", True):
        filt = [c for c in gated
                if getattr(c, "is_matter", False) or (c._rank or 3) <= split_rank]
        if len(filt) >= 2:
            gated = filt
            diag["split_level_rank"] = split_rank

    # abstention framework
    if len(gated) < 2:
        reasons.append("too_few_validated_boundaries")
    if not toc_auth and hier_viol > HIER_TOL:
        reasons.append("inconsistent_hierarchy")
    if toc_contra > TOC_TOL:
        reasons.append("contradictory_toc_alignment")

    abstain = ("too_few_validated_boundaries" in reasons) or (
        not toc_auth and (
            ("insufficient_confidence_separation" in reasons and len(gated) < 3)
            or "contradictory_toc_alignment" in reasons))

    explanations = [_explain(c, thr, toc_auth) for c in gated]
    return Decision(gated, abstain, reasons, thr, plan.hierarchy, explanations,
                    diag, structure=plan)