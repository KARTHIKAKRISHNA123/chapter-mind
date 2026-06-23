# Production Readiness Review — `book_splitter`

> Grounded review of the **actual source tree** at `book_splitter/`, not the
> hypothetical state described in the two review prompts. Scope is limited to
> structure discovery, hierarchy inference, boundary detection, confidence,
> splitting, EPUB robustness, metrics, and architecture. Translation / LLM /
> localization / OCR are out of scope by request.

---

## 0. Headline — the prompts are reviewing an older codebase than the one on disk

Both prompt documents assume a pre-refactor architecture (a `chapter_detector.py`
God file, `signals`/`scoring`/`decision_engine` "merged", explainability only
"partially implemented", hierarchy "shallow"). **The code on disk has already
moved past that.** What the prompts ask for as future work is, in large part,
already shipped:

| Prompt asks for | Status in actual code | Evidence |
|---|---|---|
| Break up the detector God file | **Done.** `detector.py` is a 363-line orchestrator | delegates to `toc`, `scoring`, `decision_engine`, `signals`, `structure`, `blocks` |
| Separate `signals` / `scoring` / `decision_engine` | **Done.** Three clean modules with correct boundaries | `signals.py` = pure `[0,1]` predicates; `decision_engine.py` = thresholds/validation/abstain |
| Explainability (`reasons[]` → real evidence) | **~80% done.** Per-boundary `explanation`, `fired_signals`, `confidence_band`, structure `rationale` | `decision_engine._explain`, `Decision.explanations` |
| Hierarchy discovery without keywords | **Done as discovery, flat as output.** Ranks, progression, regularity, TOC alignment | `structure.discover` → `StructurePlan` |
| Adapter / writer separation, format-blind engine | **Done and enforced.** Engine imports no `lxml`/`ebooklib` | `ARCHITECTURE.md` seam, `models.UnifiedDocument` |
| Input safety / format sniffing | **Done well.** Magic-byte sniff, typed errors, zip-bomb/zip-slip guards | `ingestion.py`, `safety.py` |

So this review does **not** re-litigate those. It confirms them briefly, then
concentrates fire where the real gaps are: **EPUB structural extraction (several
concrete bugs)**, **flat hierarchy output**, **candidate-generation inlined in the
orchestrator**, and **metrics not gated in CI**.

---

## A. Detector Architecture Review

**Finding: `detector.py` is not a God file.** It is a 363-line orchestrator. The
"block models / document models / DOCX parsing / TOC extraction" the prompt lists
as living inside it actually live in `models.py`, `adapters/`, `toc.py`,
`structure.py`, `scoring.py`, `decision_engine.py`. The seam in `ARCHITECTURE.md`
("nothing in `detector.py`, `signals.py`, `scoring.py`, `hierarchy.py`, or
`decision_engine.py` imports `lxml` or `ebooklib`") holds in the code.

**The real smell is different: candidate *generation* is inlined as three
branches inside `detect()`.** Reading `detect()` top to bottom you find:

1. an **index-based TOC path** (`pre_toc` from `_Toc` anchors) — builds `Candidate`s,
2. a **text-region TOC path** (`detect_toc_region` → `match_to_body`) — builds `Candidate`s,
3. a **list-numbering path** (`_detect_list_chapters`, ~60 lines) that *returns a
   whole `ChapterPlan` early*, bypassing the decision engine entirely,
4. a **scored-merge path** (`_merge_candidates` → `score_block`) — builds `Candidate`s.

Plus the candidate **pre-filters** (`_is_raw_candidate`, `_structural_candidate`,
`_merge_candidates`, `_MergedView`, `compute_baselines`) all live in the
orchestrator file.

| | |
|---|---|
| **Problem** | Four evidence sources are interleaved as conditional branches in one function; one of them (`_detect_list_chapters`) short-circuits the decision engine, so list-numbered books skip validation, abstention, and explainability. |
| **Why it matters** | New evidence sources mean editing `detect()` again; the list path is a second, parallel decision policy that can drift from `decision_engine`. Testing a single generator in isolation is impossible today. |
| **Architectural impact** | Introduce a `CandidateGenerator` protocol (Section C). Each path becomes a unit-testable generator that *only emits candidates*; `detect()` shrinks to: run generators → merge candidate sets → hand to `decision_engine.decide`. The list path stops being a bypass and instead feeds candidates with a `LIST_NUMBERED` signal like everything else. |
| **Migration difficulty** | **Medium.** Mechanical extraction; behaviour-preserving if you keep the same gating order. The only behavioural change worth making deliberately is routing the list path *through* the decision engine. |
| **Rank** | **High Value** (maintainability + closes a validation-bypass), not Critical — current behaviour is correct on the golden corpus. |

---

## B. Responsibility Matrix

This confirms the existing boundaries (they are correct) and flags the few leaks.

| Module | Owns | Must never contain | Verdict |
|---|---|---|---|
| `signals.py` | Pure predicates `Block × ctx → [0,1]`, each with WHY/FP/FN docstring, registered in `SIGNALS` | Weights, thresholds, decisions, format types | ✅ Correct — exemplary |
| `scoring.py` | Weighted combination of fired signals → `{confidence, level, fired}` | Accept/reject thresholds, validation | ✅ Correct |
| `decision_engine.py` | Adaptive thresholds (gap analysis/IQR), boundary validators, abstention, failure-resistance, per-boundary explanation | `lxml`/`ebooklib`, raw signal logic, scoring weights, format strings | ✅ Correct |
| `structure.py` | Hierarchy discovery: classify → rank → graph → split-level | Score cuts, format access | ✅ Correct |
| `detector.py` | Orchestration only | Candidate *generation* (Section A) | ⚠️ Leak — generation inlined |
| `adapters/*` | Format → `UnifiedDocument`; own the opaque `source` | Detection policy | ✅ Correct |
| `writers/*` | `ChapterPlan` → files; only reader of `block.source` | Detection policy | ✅ Correct |

**Leaks to fix:**

- **Candidate generation in `detector.py`** — see Section A.
- **Language-specific patterns in `signals.py`.** `_HINDI_DATE` (a Devanagari
  month-name regex) lives in `signals.py` and is imported by `detector.py` for
  candidate gating. This is the one place the "language-agnostic" claim is
  literally violated in the signal layer. It works, but it is a precedent: the
  next language's date/honorific exception will land here too. **Recommendation:**
  move script-specific exclusion patterns into `utils/vocabulary.py` (or a new
  `utils/script_rules.py`) behind a neutral predicate like
  `is_probably_date(text)`, so `signals.py` stays language-blind. Rank: **Nice to
  Have.**
- **Regex constants shared by import.** `detector.py` imports `_ROMAN`,
  `_CHAPTER_KW`, `_PART_KW`, `_NUMERIC`, `_HINDI_DATE` from `signals.py` for its
  pre-filter. Pre-filters belong with the candidate generators; when you extract
  generators (Section C) these constants move with them. Rank: folded into A.

**What must never enter `decision_engine.py`:** format knowledge (DOCX/EPUB
specifics), document parsing, scoring weights, or any `import lxml/ebooklib`.
Currently clean — keep it that way; it is the load-bearing invariant.

---

## C. Candidate Generator Design

Make every evidence source a peer that emits candidates with provenance. The
decision engine stays the single arbiter.

```text
UnifiedDocument
      │
      ▼
┌─────────────────────────── generators (each: doc, ctx → list[Candidate]) ──────────────────────────┐
│  TOCCandidateGenerator        author-declared boundaries (DOCX _Toc anchors, EPUB nav/NCX→index)    │
│  SpineCandidateGenerator      EPUB only: first block of each spine document (Section F)              │
│  ListNumberingGenerator       Word numPr ilvl=0 pattern (today's _detect_list_chapters)             │
│  StyleVisualGenerator         merged headings scored by signals.py (today's _merge_candidates path)  │
└──────────────────────────────────────────┬─────────────────────────────────────────────────────────┘
                                            ▼
                              merge candidate sets (dedupe by index;
                              keep highest-confidence + union of fired signals)
                                            ▼
                              decision_engine.decide(candidates, context)
                                            ▼
                                       ChapterPlan
```

**Protocol**

```python
class CandidateGenerator(Protocol):
    name: str
    def generate(self, doc: UnifiedDocument, ctx: dict) -> list[Candidate]: ...
```

**Authority is expressed as data, not control flow.** Today TOC authority is a
branch (`if toc_authoritative: ... else: ...`) and the list path is an early
`return`. Replace both with: every generator emits candidates carrying a `fired`
signal (`TOC_MATCH`, `LIST_NUMBERED`, …); `decision_engine` already knows how to
let `toc_authoritative` bypass score-gating. The list path simply emits
candidates with `LIST_NUMBERED=1.0` and lets the engine validate them — closing
the bypass noted in Section A.

| | |
|---|---|
| **Problem** | Evidence sources are control-flow branches, not composable units. |
| **Why it matters** | Adding EPUB spine handling (Section F) means *adding a generator*, not editing `detect()`. Each generator gets its own unit test. |
| **Architectural impact** | `detect()` collapses to ~40 lines: build ctx → run generators → merge → decide → assemble ranges. |
| **Migration difficulty** | **Medium.** Extract four functions you already have into a `generators/` package; keep merge order identical to preserve golden results. |
| **Rank** | **High Value.** |

---

## D. Explainability Architecture

**Already built (confirm):** `decision_engine._explain` produces, per boundary,
`{title, index, level, div_type, depth, number, confidence, band, accepted_by,
fired_signals}`; `Decision.explanations` carries the list; `structure.rationale`
explains split-level choice; `Thresholds.rationale` explains the cut
(`"largest gap 0.31 between rank 11 and 12; cut at midpoint 0.62; separation
0.18"`). This is genuine auditability, not `reasons[]`.

**Gap: it is untyped dicts, and signal contributions are not weight-resolved.**
The prompt's target — `Boundary 210 / TOC +0.55 / STYLE +0.25 / NUMBER +0.15 /
Final 0.95` — needs the *weighted* contribution of each signal, but `fired`
stores raw signal strength, not `weight × strength`. Formalize three records:

```python
@dataclass(frozen=True)
class SignalEvidence:      # one fired signal's contribution
    name: str; strength: float; weight: float
    @property
    def contribution(self) -> float: return round(self.weight * self.strength, 3)

@dataclass(frozen=True)
class ConfidenceEvidence:  # how the final score was formed
    signals: list[SignalEvidence]; raw_sum: float; final: float; band: str

@dataclass(frozen=True)
class BoundaryEvidence:    # the full audit record for one boundary
    index: int; title: str; div_type: str; depth: int
    confidence: ConfidenceEvidence
    accepted_by: str        # "toc_authority" | "score>=0.62 via gap_analysis"
    validators_passed: list[str]   # min_distance, numbering_progression, ...
```

| | |
|---|---|
| **Problem** | Evidence is a dict; per-signal weighted contribution isn't surfaced; the only place weights live is `scoring.py`'s table. |
| **Why it matters** | A typed record gives you a stable `--explain` CLI/JSON output, a regression surface for calibration, and the exact `TOC +0.55` breakdown users ask for. |
| **Architectural impact** | `scoring.score_block` returns `ConfidenceEvidence` instead of `{confidence, fired}`; `_explain` wraps it. Backward-compatible if you keep a `.confidence` shim. |
| **Migration difficulty** | **Low–Medium.** Localized to `scoring.py` + `_explain`. |
| **Rank** | **High Value** (cheap, high user-visible payoff). |

---

## E. Hierarchy Discovery & Tree Output

**Already built (confirm):** `structure.discover` infers hierarchy without
keywords — it classifies each candidate to a canonical rank, builds a
`Book→Part→Chapter→Section` graph, scores a dominant **split level** from
frequency + numbering **progression** + spacing **regularity** + **TOC
alignment**, and flags `ambiguous_split_level` / `low_split_confidence` /
`broken_numbering_progression`. This is exactly the language-agnostic engine the
prompts request (TOC depth, heading levels, numbering depth, spacing) — it
exists.

**Gap: discovery is hierarchical, but the *output* is flat.** `ChapterPlan` is a
flat `list[Chapter]`; each `Chapter` carries `depth`/`div_type`/`number` but no
parent/child links. The prompt's desired `Book ├─ Part │ ├─ Chapter │ │ ├─
Section` tree is computable from data you already have but is never assembled.

**Recommendation: add a tree *view* without disturbing the flat plan the writer
needs.** Keep `ChapterPlan.chapters` (the writer splits at the leaf/split level);
add an optional assembled tree built from `depth`:

```python
@dataclass
class DivisionNode:
    title: str; div_type: str; depth: int; number: object
    start: int; end: int; confidence: float
    children: list["DivisionNode"] = field(default_factory=list)

def build_tree(chapters: list[Chapter]) -> list[DivisionNode]:
    """Stack-based nest by depth; O(n). Matter nodes attach at depth 0."""
```

| | |
|---|---|
| **Problem** | Hierarchy is known internally but thrown away at output. |
| **Why it matters** | Enables nested output (one folder per Part), TOC regeneration, and is the natural home for `hierarchy_accuracy` metrics (Section I). |
| **Architectural impact** | Additive. `ChapterPlan` gains `tree: list[DivisionNode] | None`. Writers keep consuming the flat list; only callers that *want* nesting read the tree. Resolve conflicting evidence (a Part with no Chapters under it) by the existing `split_level` rule — the tree just visualizes the decision already made. |
| **Migration difficulty** | **Low.** Pure post-processing of `decision.boundaries`. |
| **Rank** | **High Value.** |

---

## F. EPUB Architecture Review — the one genuinely under-built area

`epub_adapter.py` reads correctly for NAV-based, one-block-per-direct-child
EPUBs, and the writer reconstructs valid per-chapter EPUBs with transitive asset
resolution (`epub_resources.collect_assets`) — good. But the **structure
discovery** side has concrete bugs that make most non-trivial EPUBs under-detect.

### F1 — EPUB TOC never reaches the authoritative path *(Critical)*

`detector.detect` treats TOC as ground truth only when entries carry an
`"index"`:

```python
pre_toc = [e for e in (doc.toc_entries or []) if "index" in e]
```

But `epub_adapter._extract_toc` emits `{"title", "level", "href"}` — **no
`index`**. So EPUB TOC entries *never* satisfy `pre_toc`, fall through to the
text-region matcher (`detect_toc_region`), which scans *body* blocks — and the
EPUB's real TOC lives in `nav.xhtml`, a spine item the loader explicitly skips.
**Net effect: the EPUB's own NAV/NCX rarely drives boundaries at all.** This is
the single highest-impact EPUB bug.

**Fix:** resolve each TOC `href` to a block index at load time.
- Split `href` into `file` + `#anchor`.
- Capture, per block, which spine `item_id` and (Section F3) which element `id`
  it came from — you already store `item_id` in `block.source`.
- Map `file → first block index of that item`; map `#anchor → index of the block
  whose element `id` matches`. Emit `toc_entries` with a resolved `"index"`.

After this, EPUB rides the same authoritative TOC path as DOCX `_Toc` anchors.

### F2 — Spine boundaries are never emitted as candidates *(Critical)*

The loader records `item_id` on every block but never says "a new spine document
starts here." For a huge class of EPUBs — **Gutenberg books, NCX-only EPUB2,
broken-NAV EPUBs, and the common one-file-per-chapter layout** — the spine *is*
the chapter structure. Without it, those books fall to font/keyword heuristics
that XHTML often doesn't expose, and the engine abstains (single-file
passthrough).

**Fix:** add a `spine_starts: set[int]` side-table to `UnifiedDocument`
(analogous to DOCX `page_break_after`), populated with the first block index of
each spine document. Add a `SpineCandidateGenerator` that emits a candidate at
each spine start with a `SPINE_START` signal. This is cheap, language-agnostic,
and exactly the kind of structural cue the engine is built to soft-vote on.

### F3 — Single-file EPUB anchors unsupported *(High)*

`Block` has no element-`id` field, and `_epub_block` doesn't read `el.get("id")`.
So a single-XHTML book whose chapters are delimited by `<h2 id="chap03">` cannot
have its NAV `book.xhtml#chap03` hrefs resolved (F1 depends on this). **Fix:** add
`anchor_id: str | None` to `Block`; populate it in `_epub_block`; use it in the
F1 href resolver.

### F4 — Only direct `<body>` children become blocks *(High)*

```python
for el in body.find_all(True, recursive=False):
```

Publisher and Gutenberg EPUBs routinely wrap content in `<div class="chapter">`
or `<section>`. With `recursive=False`, the whole wrapper collapses into **one
block** with all text concatenated, so the `<h1>` inside is invisible to style
detection. **Fix:** when a direct child is a structural container
(`div`/`section`/`article` with block-level children), descend one level (or
flatten recursively while preserving order). Guard against descending into
inline/paragraph content.

### EPUB detection order & abstention (target design)

```text
1. NAV (EPUB3 nav[epub:type=toc])      → href→index (F1)   ── highest authority
2. NCX (EPUB2 toc.ncx navMap)          → src→index
3. Spine starts (F2)                   → structural candidates
4. In-body headings (F4-corrected)     → style/visual scoring
   merge candidate sets → decision_engine.decide
Abstain when: no NAV, no NCX, spine has <2 documents, AND <2 in-body headings.
```

| | |
|---|---|
| **Problem** | EPUB structural signals (NAV/NCX hrefs, spine, anchors, nested headings) are extracted weakly or not at all. |
| **Why it matters** | The "format-agnostic" promise is only half-true today: DOCX is strong, EPUB silently abstains on common real-world books. |
| **Architectural impact** | Lands entirely in `epub_adapter.py` + two new fields on `models.py` + one new generator. The engine and decision logic need **zero** changes — the seam pays off here. |
| **Migration difficulty** | **Medium** (F1+F3 together), **Low** (F2), **Medium** (F4). |
| **Rank** | **Critical** (F1, F2), **High** (F3, F4). |

---

## G. Lazy / Streaming Review

There is no `lazy_docx.py`; there is `ingestion.py` (sniffing), a streaming
*writer* behind `OutputWriter`, and a `tests/unit/test_streaming.py`.
`ARCHITECTURE.md` already argues — correctly — that **detection is a deliberate
low-memory full pass, not online**, because adaptive thresholds (median body
font, heading band, gap analysis) require seeing the whole document before any
cut can be made.

**Verdict: this is the right call; do not "fix" it.**

- *Is lazy processing isolated?* The expensive, format-specific parse stays in
  the adapter; the engine sees only lightweight `Block` records (ints/floats/short
  strings). That is the correct isolation.
- *Should detection depend on eager parsing?* It depends on a **single bounded
  pass over Blocks**, not on holding the raw DOCX/EPUB DOM. For real books this is
  megabytes. Fine.
- *Additional streaming opportunities?* Only the **EPUB writer** for very large
  multi-file books (reconstruct per chapter without holding all assets). Worth it
  only if a real >100 MB book appears.

| | |
|---|---|
| **Problem** | None that needs solving now. The risk is *over*-engineering an online detector. |
| **Why it matters** | An online pass would forfeit adaptive thresholds — the core of the calibration story. |
| **Architectural impact** | Keep the full-pass detector; keep the streaming-writer seam available. |
| **Migration difficulty** | n/a |
| **Rank** | Building an online detector: **Avoid.** Streaming EPUB writer: **Nice to Have.** |

---

## H. Corpus Strategy

A golden corpus exists: `tests/golden/{english,hindi,assamese,bhojpuri,epub}`,
`tests/golden_books/` (Richest Man in Babylon DOCX + `.expected.json`),
`lock_golden.py`, and `tests/regression/test_golden_books.py`. Good foundation.

**Make the expectation schema explicit and uniform per class.** Each fixture
should assert four things, so a regression localizes immediately:

```json
{
  "class": "epub3_nav_multifile",
  "expected_boundaries": [0, 5, 41, 88],
  "expected_hierarchy": ["Part", "Chapter"],
  "expected_confidence_band": "high",
  "expected_abstain": false
}
```

**Highest-value additions are the EPUB variants that directly exercise Section F**
(these are the regression net for the bugs above): `epub2_ncx_only`,
`epub3_nav_multifile`, `epub_single_file_anchors`, `epub_broken_nav`,
`gutenberg_plaintext_html`, `epub_wrapper_divs`. Add CJK/RTL/play/religious
fixtures **only in lockstep with claiming them** (Section K) — an untested
fixture class is worse than an absent one.

| | |
|---|---|
| **Problem** | Corpus exists but lacks the EPUB-variant coverage and a uniform expectation schema. |
| **Why it matters** | Without `epub_broken_nav` etc., the Section F fixes can silently regress. |
| **Architectural impact** | Add fixtures + one schema; feed Section I metrics. |
| **Migration difficulty** | **Low–Medium** (sourcing/trimming small fixtures). |
| **Rank** | **High Value.** |

---

## I. Metrics & CI

**CI today (confirm):** `ci.yml` gates on `ruff` + `mypy`, then a real test matrix
(`ubuntu/windows/macos × py3.10/3.11/3.12`), then trusted-publishing to PyPI on
`v*` tags via OIDC (no stored token). That is a genuinely solid pipeline.

**Two gaps:**

1. **Coverage is measured but not gated.** `pytest … --cov` runs, but there is no
   `--cov-fail-under=N`, so coverage can silently rot. Add `--cov-fail-under=80`
   (or your true floor).
2. **No detector-quality gate.** Golden tests assert exact expected output, which
   is binary; there is no *aggregate* metric that fails the build when, say,
   boundary precision drops from 0.98 to 0.91 across the corpus. The prompts ask
   for precision/recall/FPR/FNR/abstain-rate/boundary-accuracy/hierarchy-accuracy
   /calibration — none are computed as a gate yet.

**Define metrics with tolerance, then gate them:**

| Metric | Definition |
|---|---|
| Boundary precision/recall | Predicted vs expected indices, match within ±1 block |
| False-positive / false-negative rate | From the same matched set |
| Abstain rate | Fraction of corpus returning passthrough — gate **per class** (a single-essay class *should* abstain ~100%) |
| Boundary accuracy | Mean abs index error of matched boundaries |
| Hierarchy accuracy | Predicted vs expected `div_type`/depth sequence (needs Section E tree) |
| Calibration | Bin boundaries by `confidence_band`; check empirical correctness ≈ band — this is how you *validate* the hand-tuned 0.95/0.90/0.75 statistically |

**CI shape:** a `metrics` job runs `python -m book_splitter.metrics --corpus
tests/golden --json metrics.json` and fails if `precision < 0.95`,
`per_class_abstain` deviates from expected, or `calibration_error > 0.15`. Upload
`metrics.json` as an artifact for trend tracking.

| | |
|---|---|
| **Problem** | Quality is asserted pointwise, not measured in aggregate or gated; confidence numbers are unvalidated. |
| **Why it matters** | A metrics gate is what lets you refactor (Sections A/C/F) *safely* and lets you make a defensible "production-ready" claim. |
| **Architectural impact** | New `metrics.py` reading the Section H schema; new CI job; `--cov-fail-under`. |
| **Migration difficulty** | **Medium.** |
| **Rank** | **High Value** (calibration validation is the prompts' headline ask). |

---

## J. Refactored Project Structure

The current layout is **already close to the prompt's ideal** (`adapters/`,
`writers/`, `utils/`, split engine modules, `tests/{unit,regression,performance}`).
Do **not** do a big-bang reshuffle. Two light, behaviour-preserving moves:

```text
book_splitter/
├── adapters/            # unchanged (docx, epub, registry, base)
├── generators/          # NEW — extract candidate generators (Section A/C)
│   ├── base.py          #   CandidateGenerator protocol
│   ├── toc.py           #   (moves toc-path logic out of detector.py)
│   ├── spine.py         #   NEW — EPUB spine boundaries (Section F2)
│   ├── list_numbering.py#   (today's _detect_list_chapters, no longer a bypass)
│   └── style_visual.py  #   (today's _merge_candidates + pre-filters)
├── engine/              # OPTIONAL grouping: detector, scoring, decision_engine,
│                        #   signals, structure, hierarchy  (pure, format-blind)
├── models.py · ingestion.py · safety.py · naming.py · review.py · verify.py
├── writers/  · utils/   · cli.py · __main__.py
└── metrics.py           # NEW (Section I)
```

- **`generators/`** is the one structural change worth making — it is where
  Sections A, C, and F2 converge. Migration: **Medium**, mechanical, golden-test
  protected.
- **`engine/`** grouping is **optional / Nice-to-have**; it is pure cosmetics and
  touches every import. Only do it if the root is feeling crowded.
- **Note — `llm/` and `llm_fallback.py` exist in the tree.** Out of this review's
  scope, but one architectural rule applies: the deterministic engine (`detector`,
  `scoring`, `decision_engine`, `signals`, `structure`) **must not import the LLM
  path**. Keep any LLM use behind the CLI/orchestration layer so the
  format-blind, deterministic core stays testable and the calibration story stays
  honest. Verify nothing under the engine modules imports `llm_fallback`.

| | |
|---|---|
| **Problem** | Candidate generation has no home; rest of the tree is fine. |
| **Why it matters** | A `generators/` package is the structural prerequisite for A/C/F. |
| **Architectural impact** | Additive package; imports updated; no API change. |
| **Migration difficulty** | **Medium** (`generators/`), **Low/Avoid** (`engine/`). |
| **Rank** | `generators/`: **High Value.** `engine/` grouping: **Nice to Have.** |

---

## K. Production Readiness Assessment

**Honest maturity by path, not a single number:**

| Path | Maturity | Basis |
|---|---|---|
| DOCX detection + byte-fidelity writing | **~90% — production-grade** | Clean seam, adaptive thresholds, validated abstention, golden corpus across EN/Tamil/Hindi/Assamese/Bhojpuri |
| EPUB detection | **~55–65% — beta** | Reads NAV happy-path; F1–F4 cause silent under-detection on Gutenberg/NCX-only/single-file/wrapper-div books |
| EPUB reconstruction (writer) | **~80%** | Valid per-chapter EPUBs, transitive asset resolution |
| Explainability | **~80%** | Real per-boundary evidence; needs typed records + weighted contributions (D) |
| Hierarchy | **discovery ~85%, output 0%** | Discovered internally, emitted flat (E) |
| Metrics/CI | **~70%** | Excellent pipeline; coverage + quality not gated (I) |

### Universal-claim audit

The package is honestly named **`book-splitter`**, described as
**"format-agnostic"** — *not* "universal". Keep it that way; the code earns
"format-agnostic (DOCX strong, EPUB beta)", and it does **not** yet earn
"universal". **Validated:** DOCX in Latin + four Indic scripts. **Not validated:**
Arabic/Hebrew (RTL), CJK, plays, religious texts, multi-volume, government/manual
layouts, and most EPUB variants. To *earn* a broader claim: (1) land Section F;
(2) add the Section H fixture classes for each script/type you want to claim, with
expected boundaries + abstention; (3) gate Section I metrics per class. Claim only
what has a green fixture.

### Top 5, in order

1. **EPUB F1 + F3** — resolve NAV/NCX hrefs (and anchors) to block indices.
   *Critical; without it EPUB TOC is dead.*
2. **EPUB F2** — emit spine-start candidates. *Critical; unlocks Gutenberg/NCX-only.*
3. **Metrics gate + calibration validation (I)** — turns "feels good" into
   "measured", and is the safety net for everything below.
4. **`generators/` extraction (A/C) + route list-path through the engine** —
   removes the validation bypass, makes F2 a one-file add.
5. **Hierarchy tree output (E) + typed explainability (D)** — user-visible payoff,
   low risk, additive.

Defer: `engine/` regrouping, online streaming detector, untested language fixtures.

---

## Master recommendation table

| # | Recommendation | Section | Rank | Migration |
|---|---|---|---|---|
| 1 | EPUB NAV/NCX `href`→block-index resolution | F1 | **Critical** | Medium |
| 2 | Capture element `id` on EPUB blocks (`Block.anchor_id`) | F3 | **Critical** (enables F1) | Low |
| 3 | Emit spine-start candidates (`spine_starts` + generator) | F2 | **Critical** | Low |
| 4 | Metrics harness + CI gate + calibration validation | I | **High** | Medium |
| 5 | Descend into wrapper `div`/`section` for EPUB blocks | F4 | **High** | Medium |
| 6 | `generators/` package; route list-path through decision engine | A,C,J | **High** | Medium |
| 7 | Hierarchy tree output (`DivisionNode`, additive) | E | **High** | Low |
| 8 | Typed explainability records + weighted contributions | D | **High** | Low–Med |
| 9 | EPUB-variant golden fixtures + uniform expectation schema | H | **High** | Low–Med |
| 10 | `--cov-fail-under` coverage gate | I | **High** | Trivial |
| 11 | Move `_HINDI_DATE`/script rules out of `signals.py` | B | **Nice** | Low |
| 12 | Streaming EPUB writer for very large books | G | **Nice** | Medium |
| 13 | `engine/` subpackage regrouping | J | **Nice** | Low (churny) |
| 14 | Online/streaming detector | G | **Avoid** | — |
| 15 | Claim untested languages/types without fixtures | K | **Avoid** | — |

---

### Note on method

This review was written against the files actually on disk —
`ARCHITECTURE.md`, `detector.py`, `decision_engine.py`, `structure.py`,
`signals.py`, `models.py`, `adapters/epub_adapter.py`, `ingestion.py`,
`pyproject.toml`, and `.github/workflows/ci.yml`. Where it asserts a behaviour
(e.g. "EPUB TOC never reaches the authoritative path"), that follows from the
`pre_toc = [e … if "index" in e]` gate in `detector.detect` combined with
`_extract_toc` emitting no `index`. Modules not opened in full (`scoring.py`,
`hierarchy.py`, `toc.py`, `vocabulary.py`, `writers/*`, `epub_resources.py`,
`safety.py`) were inferred from their call sites; spot-check before acting on any
recommendation that names them.
