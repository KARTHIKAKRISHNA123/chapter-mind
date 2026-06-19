"""
safety.py
=========
Structural safety guard for untrusted ZIP input.

Every ``.docx`` and ``.epub`` is an attacker-supplyable ZIP. Two classic attacks
matter before any member is read:

* **Decompression bomb** — a tiny file that expands to gigabytes (42.zip style).
* **Zip-slip / path traversal** — a member named ``../../etc/x`` or ``/abs/path``
  that, if ever extracted naively, writes outside the target directory.

This scan reads only the ZIP **central directory** (the per-member size/name
table) — it decompresses *nothing* — so it costs microseconds and runs right
after ingestion confirms the file is a valid ZIP.

Honest limitation
-----------------
Central-directory sizes are *declared by the archive*, so a maliciously crafted
ZIP could lie about them. For real-world bombs this guard is sufficient: a bomb
must declare its true (huge) expanded size to actually expand, so it trips the
caps here at zero runtime cost. A second guard that counts bytes *during*
decompression is deferred hardening — worth adding only if the threat model
becomes "hostile uploads from strangers" rather than "my own books".
"""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveLimits:
    max_total_uncompressed: int = 750 * 1024 * 1024   # 750 MB expanded, whole archive
    max_member_uncompressed: int = 200 * 1024 * 1024  # 200 MB, single member
    max_entries: int = 50_000                         # member-count cap
    max_ratio: int = 200                              # per-member ratio, members > 1 MB


class UnsafeArchiveError(Exception):
    """Decompression bomb or unsafe member path — reject before processing."""


def is_safe_member_name(name: str) -> bool:
    """True iff *name* is a safe, contained relative path.

    Rejects absolute paths, Windows drive letters, and any parent-directory
    escape (``..``) — the zip-slip vectors.
    """
    if not name or name.startswith(("/", "\\")):
        return False
    if ":" in name.split("/", 1)[0]:          # drive letter / scheme in first segment
        return False
    norm = posixpath.normpath(name.replace("\\", "/"))
    return norm != ".." and not norm.startswith("../")


def assert_safe_archive(path, limits: ArchiveLimits = ArchiveLimits()) -> None:
    """Cheap structural safety scan. Raise :class:`UnsafeArchiveError` if the
    archive looks like a bomb or contains an unsafe member path.

    Call immediately after the file is confirmed to be a valid ZIP.
    """
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()

        if len(infos) > limits.max_entries:
            raise UnsafeArchiveError(
                f"{len(infos)} entries exceeds cap of {limits.max_entries}."
            )

        total = 0
        for info in infos:
            if not is_safe_member_name(info.filename):
                raise UnsafeArchiveError(f"Unsafe member path: {info.filename!r}")

            total += info.file_size
            if info.file_size > limits.max_member_uncompressed:
                raise UnsafeArchiveError(
                    f"Member too large ({info.file_size >> 20} MB): {info.filename}"
                )
            # Per-member ratio check, only for members big enough to matter.
            if info.compress_size and info.file_size > 1_000_000:
                if info.file_size / info.compress_size > limits.max_ratio:
                    raise UnsafeArchiveError(
                        f"Suspicious compression ratio in {info.filename}"
                    )

        if total > limits.max_total_uncompressed:
            raise UnsafeArchiveError(
                f"Expanded size {total >> 20} MB exceeds cap "
                f"of {limits.max_total_uncompressed >> 20} MB."
            )
