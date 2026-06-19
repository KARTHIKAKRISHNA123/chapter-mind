"""
adapters/registry.py
====================
Content -> adapter mapping.

``get_adapter(path)`` first validates and *identifies* the file by its actual
bytes (``ingestion.sniff_format``), then returns a fresh adapter instance for
that format. EpubAdapter is imported lazily so missing ebooklib/bs4 only fails
at EPUB time.

Selection is driven by content, never by the file extension — a renamed ``.doc``
or a truncated download is rejected here with a precise, typed
:class:`~book_splitter.ingestion.IngestionError` instead of crashing later with a
bare ``zipfile.BadZipFile``.
"""
from __future__ import annotations

from ..ingestion import (
    Format,
    UnsupportedFormatError,
    sniff_format,
)
from .docx_adapter import DocxAdapter


def get_adapter(path: str):
    """Return a fresh adapter instance appropriate for *path*'s real format.

    Raises an :class:`~book_splitter.ingestion.IngestionError` subclass when the
    file is missing, empty, corrupt, mislabeled, or a recognized-but-unsupported
    format (e.g. PDF). The caller still invokes the returned adapter's
    ``.load(path)`` afterwards.
    """
    fmt = sniff_format(path)            # validates + identifies, BY CONTENT

    if fmt is Format.DOCX:
        return DocxAdapter()

    if fmt is Format.EPUB:
        from .epub_adapter import EpubAdapter   # lazy: ebooklib/bs4 optional
        return EpubAdapter()

    # Recognized container we don't split yet (e.g. PDF).
    raise UnsupportedFormatError(
        f"{fmt.value!r} is recognized but not a splittable format yet."
    )
