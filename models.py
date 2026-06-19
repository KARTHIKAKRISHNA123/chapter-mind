"""
models.py
=========
FORMAT-NEUTRAL DATA MODELS — the seam between adapters and the detection engine.

Block          — a single document body element with its extracted features.
                 The `source` field is opaque: an lxml Element for DOCX, a BS4
                 Tag for EPUB. Nothing outside the adapter layer inspects it.
DocumentMeta   — thin metadata bag.
UnifiedDocument — everything the detection engine needs: blocks + structural
                 side-tables pre-computed by the adapter.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    """A single body child with its extracted, detection-ready features.

    All fields carry the same semantics regardless of source format:
      * style_level=0  means a top-level heading (Heading 1 / h1)
      * style_level=1  means sub-heading (Heading 2 / h2), etc.
      * source         is the opaque original element used by the writer for
                       lossless output (lxml Element for DOCX, BS4 Tag for EPUB).
                       Detection code MUST NOT inspect it.
    """
    index: int                        # position among body children
    source: Any = None                # opaque handle for the writer

    # block type
    tag: str = ""
    is_paragraph: bool = False
    is_table: bool = False

    # content
    text: str = ""
    is_empty: bool = True
    word_count: int = 0

    # style / structure
    style_id: str | None = None
    style_level: int | None = None    # 0=h1, 1=h2, ... None=body/unknown

    # run-level formatting
    bold: bool = False
    all_caps_prop: bool = False
    max_size: int | None = None       # largest run size in pt (None if unknown)
    alignment: str | None = None

    # positional / layout
    page_break_before: bool = False

    # ---- convenience predicates used by signals --------------------------

    @property
    def is_textual_all_caps(self) -> bool:
        letters = [c for c in self.text if c.isalpha()]
        return bool(letters) and all(c.isupper() for c in letters)

    def normalized_title(self) -> str:
        return " ".join(self.text.lower().replace(":", " ").split())


@dataclass
class DocumentMeta:
    source_format: str          # "docx" | "epub" | ...
    title: str = ""
    path: str = ""


@dataclass
class UnifiedDocument:
    """The engine's view of a document, produced by DocumentAdapter.load()."""
    blocks: list[Block]
    meta: DocumentMeta

    # Structural side-tables computed by the adapter (format-specific logic stays
    # in the adapter; the engine just reads these sets).
    page_break_after: set = field(default_factory=set)   # indices where a page starts
    section_break_at: set = field(default_factory=set)   # oddPage/evenPage section starts
    isolated: set = field(default_factory=set)           # short lines flanked by blanks

    # TOC entries pre-extracted from the source (EPUB nav/NCX).
    # None means "no pre-extracted TOC; engine will scan body blocks".
    toc_entries: list | None = None
