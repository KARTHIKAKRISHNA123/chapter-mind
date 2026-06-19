"""
structure.py
============
HIERARCHICAL STRUCTURE DISCOVERY ENGINE (deterministic, no ML).

Replaces the "every book has chapters" assumption with discovery:

  STEP 1  discover candidate hierarchy labels   (vocabulary.match_division)
  STEP 2  detect numbering systems              (vocabulary.parse_number)
  STEP 3  build a hierarchy graph               (canonical ranks + observation)
  STEP 4  determine the dominant split level     (frequency, spacing, numbering
                                                  progression, TOC, style)
  STEP 5  produce a StructurePlan

Output (StructurePlan):
  { "hierarchy": ["Volume","Chapter"], "split_level": "Chapter",
    "confidence": 0.94, "levels": {...}, "graph": {...}, "flags": [...] }
"""

from __future__ import annotations
from dataclasses import dataclass, field
from statistics import mean, pstdev

from . import vocabulary as V

# split-level scoring sub-weights (structural, deterministic; NOT score cuts)
W_FREQ, W_PROG, W_REG, W_TOC = 0.35, 0.30, 0.20, 0.15
CONF_FLOOR = 0.45          # below this the dominant level is "ambiguous"
AMBIG_MARGIN = 0.08        # top two levels closer than this => ambiguous


@dataclass
class StructurePlan:
    hierarchy: list                       # ordered top->leaf, Title-cased
    split_level: str
    confidence: float
    levels: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    rationale: str = ""


# ---- STEP 1+2: classify each candidate (label + number + rank) --------------
def classify(c):
    m = V.match_division(c.title)
    if m:
        if m["role"] == "bare":
            c.div_type, c._rank = "unit", 3
            c.number, c.system, c.is_matter = m["number"], m["system"], False
        else:
            c.div_type = m["type"]
            c._rank = V.RANKS.get(m["type"], 3)
            c.number, c.system = m["number"], m["system"]
            c.is_matter = (m["role"] == "matter") or (m["type"] in V.MATTER)
    else:
        sl = getattr(c, "style_level", None)
        c.div_type = "chapter" if sl in (0, None) else "section"
        c._rank = V.RANKS[c.div_type]
        c.number = c.system = None
        c.is_matter = False
    return c


# ---- STEP 4 helpers ---------------------------------------------------------
def _progression(numbers):
    """Fraction of consecutive pairs that increase by 1 or reset to 1.
    Named (number-less) levels return a neutral 0.5."""
    nums = [n for n in numbers if n is not None]
    if len(nums) < 2:
        return 0.5
    good = sum(1 for i in range(1, len(nums))
               if nums[i] == nums[i - 1] + 1 or nums[i] == 1)
    return good / (len(nums) - 1)


def _regularity(indices):
    """Spacing regularity = 1/(1+CV) of inter-instance distance."""
    if len(indices) < 3:
        return 0.5
    gaps = [indices[i + 1] - indices[i] for i in range(len(indices) - 1)]
    mu = mean(gaps)
    if mu == 0:
        return 0.0
    return 1.0 / (1.0 + (pstdev(gaps) / mu))


# ---- main -------------------------------------------------------------------
def discover(candidates, context) -> StructurePlan:
    for c in candidates:
        classify(c)

    # compact depths from the ranks actually present
    present = sorted({c._rank for c in candidates})
    remap = {r: i for i, r in enumerate(present)}
    for c in candidates:
        c.depth = remap[c._rank]

    # STEP 3: hierarchy graph (ordered unique (rank,type), top->leaf)
    rank_types = sorted({(c._rank, c.div_type) for c in candidates if not c.is_matter})
    hierarchy = [t.title() for _, t in rank_types]
    graph = {hierarchy[i]: hierarchy[i + 1] for i in range(len(hierarchy) - 1)}

    # per-level statistics
    toc_idx = context.get("toc_indices") or set()
    levels = {}
    for _, t in rank_types:
        items = sorted([c for c in candidates if c.div_type == t and not c.is_matter],
                       key=lambda c: c.index)
        idxs = [c.index for c in items]
        nums = [c.number for c in items]
        systems = {c.system for c in items if c.system}
        levels[t] = {
            "count": len(items),
            "progression": round(_progression(nums), 3),
            "regularity": round(_regularity(idxs), 3),
            "numbering": sorted(systems) or ["named"],
            "toc_aligned": round(sum(1 for c in items if c.index in toc_idx)
                                 / max(1, len(items)), 3),
        }

    # STEP 4: dominant split level
    flags = []
    if not levels:
        return StructurePlan(["(none)"], "(none)", 0.0, {}, {},
                             ["no_structure_discovered"],
                             "no division labels or heading structure discovered")

    max_count = max(v["count"] for v in levels.values())
    scored = {}
    for t, v in levels.items():
        score = (W_FREQ * (v["count"] / max_count)
                 + W_PROG * v["progression"]
                 + W_REG * v["regularity"]
                 + W_TOC * v["toc_aligned"])
        scored[t] = round(score, 4)

    # argmax, tie-break by deeper rank (finer = preferred split unit)
    rank_of = {t: r for r, t in rank_types}
    split_level = max(scored, key=lambda t: (scored[t], rank_of[t]))
    ordered = sorted(scored.values(), reverse=True)
    margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else ordered[0]

    confidence = round(min(1.0, 0.6 * scored[split_level]
                           + 0.4 * levels[split_level]["progression"]), 3)
    if len(scored) > 1 and margin < AMBIG_MARGIN:
        flags.append("ambiguous_split_level")
    if confidence < CONF_FLOOR:
        flags.append("low_split_confidence")
    if levels[split_level]["progression"] < 0.5 and not toc_idx:
        flags.append("broken_numbering_progression")

    rationale = (f"split level '{split_level}' chosen: score {scored[split_level]:.3f}, "
                 f"count {levels[split_level]['count']}, "
                 f"progression {levels[split_level]['progression']}, "
                 f"regularity {levels[split_level]['regularity']}, "
                 f"margin {margin:.3f}")

    return StructurePlan(hierarchy, split_level.title(), confidence,
                         levels, graph, flags, rationale)