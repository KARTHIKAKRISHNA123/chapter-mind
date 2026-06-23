"""
adapters/docx_adapter.py
========================
DOCX side of the seam.  Input = DocxPackage + StyleResolver.
Output = per-chapter DOCX files via OutputWriter.
NO detection logic lives here.

Phase 8 — lazy streaming
------------------------
Detection pass
  ``DocxAdapter.load()`` streams body children one at a time via
  ``DocxPackage.iter_block_children()``.  Feature extraction runs on
  each element; the element is cleared immediately afterwards so only
  ONE body child is held as a full lxml tree at any given moment.
  ``Block.source`` is set to ``None`` — the detection engine never
  inspects it, and the writer re-reads elements from ``_pkg._raw``
  when it needs them.

Write pass
  ``_DocxWriter.iter_write()`` makes a SINGLE additional iterparse
  pass over the cached ``word/document.xml`` bytes.  It routes each
  body element to the chapter it belongs to and flushes (writes DOCX,
  clears buffer) when a chapter boundary is crossed.  Peak lxml RAM
  during writing = elements in the largest single chapter.

Guarantee: output is byte-identical to the old splitter.split().
"""
from __future__ import annotations
import copy
import io
import os
import re

from lxml import etree

from .base import DocumentAdapter, OutputWriter
from .docx_toc import toc_anchor, toc_region, _para_text, resolve_field_toc
from ..models import Block, DocumentMeta, UnifiedDocument
from ..docx_package import DocxPackage, W
from ..naming import render_filename
from ..blocks import StyleResolver, _text_of


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------

def _docx_block(index: int, el, resolver: StyleResolver) -> Block:
    """Build a format-neutral Block from one lxml body child.

    Mirrors the old Block.__init__ + _extract_paragraph exactly, but
    returns a models.Block dataclass with source=None (Phase 8: source
    elements are freed after extraction; the writer re-reads from raw).
    """
    tag = etree.QName(el).localname
    is_p = (el.tag == W + "p")
    is_t = (el.tag == W + "tbl")

    b = Block(
        index=index,
        source=None,            # not stored — freed after extraction
        tag=tag,
        is_paragraph=is_p,
        is_table=is_t,
    )

    if not is_p:
        return b                # tables / sdt / etc. — defaults are fine

    # ---- paragraph feature extraction -----------------------------------
    b.text = _text_of(el)
    b.is_empty = (b.text == "")
    b.word_count = len(b.text.split())

    ppr = el.find(W + "pPr")
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        b.style_id = ps.get(W + "val") if ps is not None else None
        jc = ppr.find(W + "jc")
        b.alignment = jc.get(W + "val") if jc is not None else None
        if ppr.find(W + "pageBreakBefore") is not None:
            b.page_break_before = True
        ol = ppr.find(W + "outlineLvl")
        if ol is not None:
            b.style_level = int(ol.get(W + "val"))
        np = ppr.find(W + "numPr")
        if np is not None:
            nid = np.find(W + "numId")
            ilvl = np.find(W + "ilvl")
            if nid is not None and ilvl is not None:
                try:
                    b.num = (int(nid.get(W + "val")), int(ilvl.get(W + "val")))
                except (TypeError, ValueError):
                    pass

    if b.style_level is None and b.style_id:
        b.style_level = resolver.heading_level(b.style_id)

    sizes = []
    run_bold = False
    for r in el.findall(W + "r"):
        rpr = r.find(W + "rPr")
        if rpr is None:
            continue
        if rpr.find(W + "b") is not None:
            run_bold = True
        if rpr.find(W + "caps") is not None:
            b.all_caps_prop = True
        for tag in (W + "sz", W + "szCs"):
            s = rpr.find(tag)
            if s is not None:
                try:
                    sizes.append(int(s.get(W + "val")) // 2)
                except (TypeError, ValueError):
                    pass
    b.max_size = max(sizes) if sizes else None
    style_bold = bool(b.style_id and resolver.by_id.get(b.style_id, {}).get("bold"))
    b.bold = run_bold or style_bold

    return b


# ---------------------------------------------------------------------------
# Streaming detection helpers (Phase 8)
# ---------------------------------------------------------------------------

def _load_blocks_streaming(
    doc_xml: bytes, resolver: StyleResolver
) -> tuple[list[Block], set[int], set[int], list[dict] | None]:
    """Single iterparse pass: build all Block objects while freeing each
    lxml element immediately after feature extraction.

    Returns
    -------
    blocks          list of Block (source=None)
    page_break_after  set of indices where a hard page break follows
    section_at        set of indices marking odd/even-page section starts
    toc_entries       [{index, title, level}] or None

    Memory: at most ONE body-level lxml element is live in RAM at a time.
    TOC extraction is interleaved: we accumulate the TOC-style region
    mapping and heading bookmarks in one pass, then resolve them at the end.
    """
    blocks: list[Block] = []
    page_break_after: set[int] = set()
    section_at: set[int] = set()

    # TOC interleaved accumulators
    toc_rgn: dict[str, dict] = {}       # _Toc anchor -> {title, level}
    heading_anchors: list[tuple[int, str]] = []  # (block_idx, anchor)

    # TOC-FIELD fallback (pandoc: cached TOC result, no _Toc bookmarks)
    toc_field_titles: list[str] = []              # cached entry titles, in order
    heading_pool: list[tuple[int, str, int]] = []  # (idx, text, style_level)
    _toc_field_active = False
    _toc_field_done = False

    depth = 0
    body_depth: int | None = None
    idx = 0

    for event, el in etree.iterparse(
        io.BytesIO(doc_xml), events=("start", "end")
    ):
        if event == "start":
            depth += 1
            if el.tag == W + "body" and body_depth is None:
                body_depth = depth
        else:  # "end"
            if body_depth is not None and depth == body_depth + 1:
                if el.tag != W + "sectPr":
                    # ---- page/section break scan (must happen before clear) ----
                    for br in el.iter(W + "br"):
                        if br.get(W + "type") == "page":
                            page_break_after.add(idx + 1)
                    for sect in el.iter(W + "sectPr"):
                        t = sect.find(W + "type")
                        if t is not None and t.get(W + "val") in (
                            "oddPage", "evenPage"
                        ):
                            section_at.add(idx + 1)

                    # ---- TOC interleaved scan ----
                    if el.tag == W + "p":
                        # Collect TOC-style entries (TOC1..TOC9 paragraphs)
                        ppr = el.find(W + "pPr")
                        sid = ""
                        if ppr is not None:
                            ps = ppr.find(W + "pStyle")
                            sid = (
                                (ps.get(W + "val") or "").lower()
                                if ps is not None
                                else ""
                            )
                        for hl in el.iter(W + "hyperlink"):
                            a = hl.get(W + "anchor") or ""
                            if a.startswith("_Toc"):
                                level = (
                                    int(sid[3:]) - 1
                                    if (sid.startswith("toc") and sid[3:].isdigit())
                                    else 0
                                )
                                toc_rgn[a] = {
                                    "title": _para_text(el),
                                    "level": max(level, 0),
                                }
                                break
                        # Collect bookmark anchors on heading paragraphs
                        anchor = toc_anchor(el)
                        if anchor is not None:
                            heading_anchors.append((idx, anchor))

                    # ---- build Block (extracts scalars, no lxml refs) ----
                    b = _docx_block(idx, el, resolver)
                    blocks.append(b)

                    # ---- TOC-FIELD fallback scan (el still alive) ----
                    # Body headings -> resolution pool (any heading level; the
                    # off-by-one outlineLvl some converters emit is tolerated).
                    if b.is_paragraph and b.style_level is not None and b.text:
                        heading_pool.append((idx, b.text, b.style_level))
                    # Cached TOC-field entries: collect visible text of every
                    # paragraph between the field's begin/separate and its end.
                    if not _toc_field_done and el.tag == W + "p":
                        _instr = "".join(t.text or "" for t in el.iter(W + "instrText"))
                        _ftypes = {f.get(W + "fldCharType")
                                   for f in el.iter(W + "fldChar")}
                        if (not _toc_field_active and "TOC" in _instr
                                and ("begin" in _ftypes or "separate" in _ftypes)):
                            _toc_field_active = True
                        if _toc_field_active:
                            if b.text:
                                toc_field_titles.append(b.text)
                            if "end" in _ftypes:
                                _toc_field_active = False
                                _toc_field_done = True

                    # ---- free element internals ----
                    el.clear()
                    idx += 1
            depth -= 1

    # Resolve TOC entries from accumulated maps
    toc_entries: list[dict] | None = None
    if heading_anchors and toc_rgn:
        toc_entries = []
        for bi, anchor in heading_anchors:
            meta = toc_rgn.get(anchor, {})
            toc_entries.append({
                "index": bi,
                "title": meta.get("title") or (blocks[bi].text if bi < len(blocks) else ""),
                "level": meta.get("level", 0),
            })
        if not toc_entries:
            toc_entries = None

    # Fallback: TOC field with a cached result but no _Toc bookmarks (pandoc).
    if toc_entries is None and toc_field_titles:
        toc_entries = resolve_field_toc(toc_field_titles, heading_pool)

    return blocks, page_break_after, section_at, toc_entries


def _compute_isolated(blocks: list[Block]) -> set[int]:
    """Pure-Python computation of 'isolated' block indices.

    An isolated block is a short non-empty paragraph preceded by an empty
    paragraph and followed (within 3 blocks) by body-length text.
    Identical logic to the old _scan_structure isolation check, but runs on
    lightweight Block objects rather than lxml elements.
    """
    isolated: set[int] = set()
    for i, b in enumerate(blocks):
        if not b.is_paragraph or b.is_empty or b.word_count > 12:
            continue
        prev_empty = i > 0 and blocks[i - 1].is_empty
        follows_body = any(
            blocks[j].is_paragraph and blocks[j].word_count > 16
            for j in range(i + 1, min(i + 4, len(blocks)))
        )
        if prev_empty and follows_body:
            isolated.add(b.index)
    return isolated


# ---------------------------------------------------------------------------
# Writer (streaming, Phase 8)
# ---------------------------------------------------------------------------

def _slug(title: str, fallback: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title).strip().lower()
    s = re.sub(r"[\s]+", "_", s)
    return s[:48] or fallback


class _DocxWriter(OutputWriter):
    def __init__(self, package: DocxPackage):
        self._pkg = package

    def write(self, plan, out_dir: str, pattern: str | None = None) -> list[dict]:
        """Write all chapters; return full manifest.

        Delegates to ``iter_write()`` which streams one chapter at a time.
        """
        return list(self.iter_write(plan, out_dir, pattern))

    def iter_write(self, plan, out_dir: str, pattern: str | None = None):
        """Lazy streaming write — single iterparse pass over document.xml.

        Only the elements belonging to the CURRENT chapter are held in RAM
        at any one time.  When a chapter boundary is crossed the buffer is
        serialised to a DOCX file and cleared before the next chapter begins.

        Peak lxml RAM = elements in the largest single chapter.
        Output files are byte-identical to the old clone_with_blocks path.
        """
        if not plan.chapters:
            return

        os.makedirs(out_dir, exist_ok=True)

        # Build sorted chapter list (document order is guaranteed, but be safe).
        chapters = sorted(enumerate(plan.chapters), key=lambda x: x[1].start)

        # Index from block position → (output_index, Chapter)
        # We'll walk chapters in order with a pointer.
        ch_iter = iter(chapters)
        out_i, current_ch = next(ch_iter, (None, None))
        current_buf: list[etree._Element] = []

        depth = 0
        body_depth: int | None = None
        block_idx = 0

        def _flush(out_index, ch, buf) -> dict | None:
            if not buf:
                return None
            data = self._pkg.clone_with_blocks(buf)
            if pattern:
                name = render_filename(
                    pattern, ordinal=out_index, title=ch.title, ext=".docx"
                )
            else:
                name = f"{out_index:02d}_{_slug(ch.title, f'chapter_{out_index}')}.docx"
            self._pkg.write(os.path.join(out_dir, name), data)
            return {
                "file": name,
                "title": ch.title,
                "level": ch.level,
                "confidence": ch.confidence,
                "blocks": [ch.start, ch.end],
            }

        for event, el in etree.iterparse(
            io.BytesIO(self._pkg._raw["word/document.xml"]),
            events=("start", "end"),
        ):
            if event == "start":
                depth += 1
                if el.tag == W + "body" and body_depth is None:
                    body_depth = depth
            else:  # "end"
                if body_depth is not None and depth == body_depth + 1:
                    if el.tag != W + "sectPr" and current_ch is not None:
                        # Advance past exhausted chapters
                        while current_ch is not None and block_idx >= current_ch.end:
                            entry = _flush(out_i, current_ch, current_buf)
                            current_buf = []
                            if entry:
                                yield entry
                            out_i, current_ch = next(ch_iter, (None, None))

                        # Collect element if inside current chapter range
                        if (
                            current_ch is not None
                            and current_ch.start <= block_idx < current_ch.end
                        ):
                            current_buf.append(copy.deepcopy(el))

                        el.clear()
                        block_idx += 1
                depth -= 1

        # Flush the last chapter (document ended before block_idx hit ch.end)
        if current_ch is not None:
            entry = _flush(out_i, current_ch, current_buf)
            current_buf = []
            if entry:
                yield entry
            # Flush any remaining chapters with empty buffers (edge case)
            for out_i, current_ch in ch_iter:
                entry = _flush(out_i, current_ch, [])
                if entry:
                    yield entry


# ---------------------------------------------------------------------------
# Structural scan (legacy path, kept for reference; streaming path inline)
# ---------------------------------------------------------------------------

def _scan_structure(blocks: list[Block]):
    """Extract page-break, section-break, and isolation sets from DOCX blocks.

    DEPRECATED: Phase 8 inlines this logic into ``_load_blocks_streaming``
    so that lxml elements are freed immediately.  Kept as a fallback for
    any caller that already has a populated blocks list with source elements.
    """
    page_break_after: set = set()
    section_at: set = set()
    isolated: set = set()

    for b in blocks:
        el = b.source
        if el is None:
            continue                        # streaming path: source already freed
        for br in el.iter(W + "br"):
            if br.get(W + "type") == "page":
                page_break_after.add(b.index + 1)
        for sect in el.iter(W + "sectPr"):
            t = sect.find(W + "type")
            if t is not None and t.get(W + "val") in ("oddPage", "evenPage"):
                section_at.add(b.index + 1)

    for i, b in enumerate(blocks):
        if not b.is_paragraph or b.is_empty or b.word_count > 12:
            continue
        prev_empty = i > 0 and blocks[i - 1].is_empty
        follows_body = any(
            blocks[j].is_paragraph and blocks[j].word_count > 16
            for j in range(i + 1, min(i + 4, len(blocks)))
        )
        if prev_empty and follows_body:
            isolated.add(b.index)

    return page_break_after, section_at, isolated


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class DocxAdapter(DocumentAdapter):
    extensions = (".docx",)

    def load(self, path: str) -> UnifiedDocument:
        """Load a DOCX and return a format-neutral UnifiedDocument.

        Phase 8 streaming:
        - ``iter_block_children()`` streams one body element at a time.
        - Features are extracted and the element cleared immediately.
        - ``Block.source`` is ``None`` throughout; no lxml elements are
          retained after this method returns.
        - Peak lxml RAM = one body child + _skeleton (tiny).
        """
        self._pkg = DocxPackage(path)
        resolver = StyleResolver(self._pkg._raw["word/styles.xml"])

        blocks, pba, sect, toc = _load_blocks_streaming(
            self._pkg._raw["word/document.xml"], resolver
        )
        isolated = _compute_isolated(blocks)

        return UnifiedDocument(
            blocks=blocks,
            meta=DocumentMeta(source_format="docx", path=path),
            page_break_after=pba,
            section_break_at=sect,
            isolated=isolated,
            toc_entries=toc,
        )

    def make_writer(self) -> OutputWriter:
        return _DocxWriter(self._pkg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan_structure_compat(blocks: list[Block], children: list):
    """Backward-compat wrapper: scan structure from a list of raw lxml elements.

    Useful for callers that already hold the full element list (e.g. tests
    that compare streaming vs non-streaming output).
    """
    page_break_after: set = set()
    section_at: set = set()

    for i, el in enumerate(children):
        for br in el.iter(W + "br"):
            if br.get(W + "type") == "page":
                page_break_after.add(i + 1)
        for sect in el.iter(W + "sectPr"):
            t = sect.find(W + "type")
            if t is not None and t.get(W + "val") in ("oddPage", "evenPage"):
                section_at.add(i + 1)

    isolated: set = set()
    for i, b in enumerate(blocks):
        if not b.is_paragraph or b.is_empty or b.word_count > 12:
            continue
        prev_empty = i > 0 and blocks[i - 1].is_empty
        follows_body = any(
            blocks[j].is_paragraph and blocks[j].word_count > 16
            for j in range(i + 1, min(i + 4, len(blocks)))
        )
        if prev_empty and follows_body:
            isolated.add(b.index)

    return page_break_after, section_at, isolated
