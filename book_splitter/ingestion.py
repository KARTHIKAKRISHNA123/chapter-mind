"""
ingestion.py
============
INPUT VALIDATION / FORMAT SNIFFING — runs BEFORE any adapter touches the bytes.

Why this layer exists
---------------------
A ``.docx`` and a ``.epub`` are both ZIP containers; every valid one starts with
the bytes ``PK`` (``50 4B 03 04``). ``zipfile.ZipFile(path)`` reads that header,
sees something that is not a ZIP local-file signature, and raises
``zipfile.BadZipFile`` *before any of our code runs*. The old path selected an
adapter purely by file extension and immediately trusted the bytes — and
extensions lie (a renamed ``.doc``, a password-protected ``.docx``, a PDF saved
with the wrong name, a Git-LFS pointer, a truncated download).

So we never trust the extension. We sniff the magic bytes, then inspect ZIP
membership, and turn the raw ``BadZipFile`` into a precise, user-actionable
error. Adapter selection is then driven by ACTUAL content (see
``adapters/registry.get_adapter``).

This module has no heavy dependencies and imports nothing from the engine, so it
is safe to import very early.
"""

from __future__ import annotations

import zipfile
from enum import Enum
from pathlib import Path

from .safety import assert_safe_archive


class Format(str, Enum):
    """The true container format, decided by content (not extension)."""
    DOCX = "docx"
    EPUB = "epub"
    PDF = "pdf"          # recognized, but not yet a splittable format


# ---------------------------------------------------------------------------
# Typed errors — every failure mode is named and carries an actionable message
# ---------------------------------------------------------------------------

class IngestionError(Exception):
    """Base: the input cannot be processed. Message is human-actionable."""


class CorruptArchiveError(IngestionError):
    """Looks like a ZIP (right magic) but is truncated/damaged/empty."""


class UnsupportedFormatError(IngestionError):
    """A real, readable file — but not a format we split (legacy .doc, RTF,
    HTML, PDF, an unknown ZIP, ...)."""


class MisnamedFileError(IngestionError):
    """Extension claims one thing, the bytes say another. (Reserved for callers
    that want to distinguish a wrong extension from an unsupported format.)"""


# ZIP local-file / empty-archive / spanned-archive signatures.
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# OLE2 compound-file header: legacy .doc OR a password-protected OOXML file.
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _describe_non_zip(head: bytes) -> str:
    """Best-effort, friendly description of a file that is not a ZIP."""
    if head.startswith(_OLE2):
        return ("an OLE2 file — most likely a legacy .doc OR a password-protected "
                ".docx. Re-save it as a plain .docx without protection, or convert "
                ".doc -> .docx in Word.")
    if head.startswith(b"%PDF"):
        return "a PDF. PDF splitting isn't supported yet."
    if head.startswith(b"{\\rtf"):
        return "an RTF file. Re-save it as a .docx in Word."
    if head[:5] in (b"<?xml", b"<html", b"<!DOC"):
        return "an HTML/XML file saved with a document name. Re-export a real .docx/.epub."
    if not head.strip():
        return "empty or a Git-LFS pointer (the download is likely incomplete)."
    return f"not a recognized document container (starts with {head[:4]!r})."


def sniff_format(path) -> Format:
    """Identify the true :class:`Format` of *path*, or raise an
    :class:`IngestionError` explaining exactly why it cannot be processed.

    Cheap: reads an 8-byte header, then (for ZIPs) scans the central directory
    and CRC-checks members. Never loads document content.
    """
    path = Path(path)
    if not path.is_file():
        raise IngestionError(f"Not a file: {path}")
    if path.stat().st_size == 0:
        raise CorruptArchiveError(f"{path.name} is empty (0 bytes).")

    with path.open("rb") as fh:
        head = fh.read(8)

    if not any(head.startswith(m) for m in _ZIP_MAGICS):
        raise UnsupportedFormatError(f"{path.name} is {_describe_non_zip(head)}")

    # It claims to be a ZIP — open it and inspect membership.
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()                # None == all members CRC-valid
            if bad is not None:
                raise CorruptArchiveError(
                    f"{path.name} has a corrupt member ({bad!r})."
                )
            names = set(z.namelist())
    except zipfile.BadZipFile as exc:        # truncated/garbled central dir
        raise CorruptArchiveError(
            f"{path.name} looks like a ZIP but is damaged or truncated."
        ) from exc

    # Structural safety: reject decompression bombs and zip-slip paths before
    # any adapter decompresses a member. Cheap (central-directory scan only).
    assert_safe_archive(path)

    if "word/document.xml" in names:
        return Format.DOCX
    if "mimetype" in names and "META-INF/container.xml" in names:
        return Format.EPUB

    raise UnsupportedFormatError(
        f"{path.name} is a ZIP but neither DOCX nor EPUB "
        f"(no word/document.xml, and no EPUB mimetype + META-INF/container.xml)."
    )
