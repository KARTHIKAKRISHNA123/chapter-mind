"""
adapters/docx_adapter.py
========================
DOCX side of the seam. Input = existing DocxPackage + StyleResolver.
Output = existing clone_with_blocks, behind the OutputWriter interface.
NO detection logic lives here.

Guarantee: _DocxWriter.write() produces byte-identical output to the old
splitter.split() — it slices the same block_children() list by index and
hands the same lxml elements to the same clone_with_blocks().
"""
from __future__ import annotations
import os
import re

from lxml import etree

from .base import DocumentAdapter, OutputWriter
from ..models import Block, DocumentMeta, UnifiedDocument
from ..docx_package import DocxPackage
from ..naming import render_filename
from ..blocks import StyleResolver, _text_of    # StyleResolver & _text_of STAY in blocks.py

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------

def _docx_block(index: int, el, resolver: StyleResolver) -> Block:
    """Build a format-neutral Block from one lxml body child.

    Mirrors the old blocks.Block.__init__ + _extract_paragraph exactly, but
    returns a models.Block dataclass with source=el instead of self.el=el.
    Detection output is therefore byte-identical to the old pipeline.
    """
    tag = etree.QName(el).localname
    is_p = (el.tag == W + "p")
    is_t = (el.tag == W + "tbl")

    b = Block(
        index=index,
        source=el,              # opaque handle -- only the writer uses this
        tag=tag,
        is_paragraph=is_p,
        is_table=is_t,
    )

    if not is_p:
        return b                # tables / sdt / etc. -- defaults are fine

    # ---- paragraph feature extraction (lifted verbatim from Block._extract_paragraph) ----
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
        # explicit paragraph-level outline level overrides style
        ol = ppr.find(W + "outlineLvl")
        if ol is not None:
            b.style_level = int(ol.get(W + "val"))

    if b.style_level is None and b.style_id:
        b.style_level = resolver.heading_level(b.style_id)

    # Resolve run-level formatting (bold / caps / size).
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
        sz = rpr.find(W + "sz")
        if sz is not None:
            try:
                sizes.append(int(sz.get(W + "val")) // 2)  # half-points -> pt
            except (TypeError, ValueError):
                pass
    b.max_size = max(sizes) if sizes else None
    # Bold is true if any run is bold OR the paragraph style implies bold.
    style_bold = bool(b.style_id and resolver.by_id.get(b.style_id, {}).get("bold"))
    b.bold = run_bold or style_bold

    return b


# ---------------------------------------------------------------------------
# Structural side-tables (moved from detector._scan_structure)
# ---------------------------------------------------------------------------

def _scan_structure(blocks: list[Block]):
    """Extract page-break, section-break, and isolation sets from DOCX blocks.

    Uses b.source (the lxml element) for XML traversal -- this is the only
    DOCX-specific code path.  The returned sets are stored in UnifiedDocument
    and consumed by the detection engine without any format knowledge.
    """
    page_break_after: set = set()
    section_at: set = set()
    isolated: set = set()

    for b in blocks:
        el = b.source                   # lxml element (opaque to detection)
        # page breaks: a <w:br w:type="page"/> means the NEXT block opens a page
        for br in el.iter(W + "br"):
            if br.get(W + "type") == "page":
                page_break_after.add(b.index + 1)
        # only odd/even-page section breaks count (true chapter-start convention)
        for sect in el.iter(W + "sectPr"):
            t = sect.find(W + "type")
            if t is not None and t.get(W + "val") in ("oddPage", "evenPage"):
                section_at.add(b.index + 1)

    # isolation: short line, preceded by empty, followed soon by body-length text
    for i, b in enumerate(blocks):
        if not b.is_paragraph or b.is_empty or b.word_count > 12:
            continue
        prev_empty = i > 0 and blocks[i - 1].is_empty
        follows_body = any(
            blocks[j].is_paragraph and blocks[j].word_count > 16
            for j in range(i + 1, min(i + 4, len(blocks))))
        if prev_empty and follows_body:
            isolated.add(b.index)

    return page_break_after, section_at, isolated


# ---------------------------------------------------------------------------
# Writer (mirrors old splitter.split() exactly)
# ---------------------------------------------------------------------------

class _DocxWriter(OutputWriter):
    def __init__(self, package: DocxPackage):
        self._pkg = package

    def write(self, plan, out_dir: str, pattern: str | None = None) -> list[dict]:
        """Clone a slice of the original ZIP for every chapter.

        Uses clone_with_blocks() -- which rewrites only word/document.xml while
        keeping the rest of the ZIP intact -- so output is byte-identical to
        what the old splitter.split() produced.

        Filenames follow `pattern` when given; otherwise the original
        `NN_slug.docx` naming is kept unchanged (preserves byte-fidelity tests).
        """
        os.makedirs(out_dir, exist_ok=True)
        children = self._pkg.block_children()   # list of lxml elements, same order as blocks
        manifest = []
        for i, ch in enumerate(plan.chapters):
            els = children[ch.start:ch.end]
            if not els:
                continue
            data = self._pkg.clone_with_blocks(els)
            if pattern:
                name = render_filename(pattern, ordinal=i, title=ch.title, ext=".docx")
            else:
                name = f"{i:02d}_{_slug(ch.title, f'chapter_{i}')}.docx"
            self._pkg.write(os.path.join(out_dir, name), data)
            manifest.append({
                "file": name,
                "title": ch.title,
                "level": ch.level,
                "confidence": ch.confidence,
                "blocks": [ch.start, ch.end],
            })
        return manifest


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class DocxAdapter(DocumentAdapter):
    extensions = (".docx",)

    def load(self, path: str) -> UnifiedDocument:
        self._pkg = DocxPackage(path)
        resolver = StyleResolver(self._pkg._raw["word/styles.xml"])
        children = self._pkg.block_children()
        blocks = [_docx_block(i, el, resolver) for i, el in enumerate(children)]
        pba, sect, iso = _scan_structure(blocks)    # pass blocks, not raw elements
        return UnifiedDocument(
            blocks=blocks,
            meta=DocumentMeta(source_format="docx", path=path),
            page_break_after=pba,
            section_break_at=sect,
            isolated=iso,
            toc_entries=None,           # engine will scan body blocks
        )

    def make_writer(self) -> OutputWriter:
        return _DocxWriter(self._pkg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(title: str, fallback: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title).strip().lower()
    return re.sub(r"\s+", "_", s)[:48] or fallback
