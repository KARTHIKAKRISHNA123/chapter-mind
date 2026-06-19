"""
adapters/base.py
================
The two abstractions that decouple format from engine.

Adapter  : path  -> UnifiedDocument   (INPUT  side)
Writer   : plan  -> files on disk     (OUTPUT side)

Why two interfaces and not one: input and output are asymmetric. DOCX reads via
zip+lxml but writes via the loss-free clone; EPUB reads via ebooklib but writes
by rebuilding a spine. Forcing both into one class would leak each format's
write strategy into its read path.
"""
from __future__ import annotations
from abc import ABC, abstractmethod

from ..models import UnifiedDocument


class OutputWriter(ABC):
    @abstractmethod
    def write(self, plan, out_dir: str, pattern: str | None = None) -> list[dict]:
        """Materialise `plan.chapters` into files; return a manifest list.

        `pattern` is an optional filename template (see `book_splitter.naming`).
        When None, writers keep their original `NN_slug` naming so existing
        output stays byte-identical.
        """
        ...


class DocumentAdapter(ABC):
    #: file extensions this adapter claims, e.g. (".docx",)
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def load(self, path: str) -> UnifiedDocument:
        ...

    @abstractmethod
    def make_writer(self) -> OutputWriter:
        """A writer bound to this loaded source (it may need the original
        package — e.g. DOCX needs it for clone_with_blocks)."""
        ...