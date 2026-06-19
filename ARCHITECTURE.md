# Architecture

## Pipeline

```
            ┌─────────────────────────── ingestion + safety ───────────────────────────┐
input file ─▶ sniff_format (magic bytes + ZIP membership)  ─▶  assert_safe_archive       │
            │   • rejects renamed .doc / PDF / RTF / HTML            (zip-bomb, zip-slip) │
            │   • rejects corrupt / truncated / empty                                    │
            └───────────────────────────────────┬───────────────────────────────────────┘
                                                 ▼
                                  registry.get_adapter (by content)
                                                 │
                       ┌─────────────────────────┴─────────────────────────┐
                       ▼                                                     ▼
                 DocxAdapter.load                                     EpubAdapter.load
                       │                                                     │
                       └──────────────► UnifiedDocument ◄────────────────────┘
                              (blocks + structural side-tables, format-neutral)
                                                 │
                                                 ▼
                              detect()  ── the format-blind engine ──
                       TOC intelligence ▶ style/outline ▶ visual+text signals
                          → scoring → hierarchy → decision (merge / abstain)
                                                 │
                                                 ▼
                                          ChapterPlan
                                     (Chapter: title, start, end,
                                      level, confidence)
                                                 │
                            review.triage (low-confidence warn/confirm)
                                                 │
                                                 ▼
                        adapter.make_writer().write(plan, out_dir, pattern)
                       DOCX: clone ZIP, rewrite body   EPUB: reconstruct + assets
                                                 │
                                                 ▼
                                    one file per chapter on disk
```

## The format-blind-engine seam

The single most important design rule: the **detection engine never knows what
format it is looking at.** Adapters convert any source into a list of
`Block`s (a slotted dataclass with extracted features — text, style level, font
size, bold, etc.) plus pre-computed structural side-tables (page breaks, section
breaks, isolation, optional TOC).

Each `Block` carries a `source` field — the opaque original element (an `lxml`
element for DOCX, a `(item_id, BS4 tag)` pair for EPUB). **Only the writer ever
reads `source`.** The engine carries it but must never inspect it. This is what
keeps DOCX byte-fidelity possible: the writer hands the original elements back to
the cloner untouched.

Consequently, nothing in `detector.py`, `signals.py`, `scoring.py`,
`hierarchy.py`, or `decision_engine.py` imports `lxml` or `ebooklib`.

## Why detection is a low-memory full pass, not online

Detection uses **adaptive thresholds** — the median body font size and the
heading band are computed across the whole document. You cannot know the global
body baseline until you have seen the body, so a purely online (single forward
emit) pass is not achievable without a look-ahead compromise. The engine
therefore makes one cheap full pass over lightweight `Block` records (ints,
floats, short strings — megabytes, not gigabytes), which is fast and bounded for
real books. A lazy/streaming writer exists behind the same `OutputWriter`
interface and can be adopted later, with no API change, if a genuinely large
book ever proves the need.

## Layers of detection (highest authority first)

1. **TOC intelligence** — author-declared boundaries (EPUB nav/NCX, DOCX body TOC scan).
2. **Style / outline level** — semantic heading styles, resolving the `basedOn`
   inheritance chain so custom styles based on `Heading 1` still count.
3. **Visual + text fallback** — relative font band, bold, "Chapter N" vocabulary,
   roman numerals, ALL-CAPS. The English/Latin-centric signals (caps, prefix
   words) are deliberately low-weight so non-Latin books (Tamil, Arabic, CJK),
   where `styleId`/`outlineLvl` remain language-independent, are not under-detected.

The engine **merges** consecutive heading paragraphs into one boundary and
**abstains** (returns a single-file passthrough) rather than shred a book when it
cannot find at least two confident boundaries.

## Extension points

- **New input/output format** = implement `DocumentAdapter` (`load` →
  `UnifiedDocument`, `make_writer` → `OutputWriter`) and register it in
  `adapters/registry.py`. The engine needs no changes.
- **New detection signal** = add it in `signals.py` with a weight in the scoring
  table; it composes by soft-voting with the others (no new "mode" plumbing).
