"""Unit tests for the ZIP safety guard (zip bombs + zip-slip)."""
from __future__ import annotations

import zipfile

import pytest

from book_splitter.safety import (
    ArchiveLimits,
    UnsafeArchiveError,
    assert_safe_archive,
    is_safe_member_name,
)


# ---- is_safe_member_name ---------------------------------------------------

@pytest.mark.parametrize("name", [
    "word/document.xml",
    "OEBPS/text/ch1.xhtml",
    "a/b/c.png",
])
def test_safe_names(name):
    assert is_safe_member_name(name) is True


@pytest.mark.parametrize("name", [
    "../../etc/passwd",
    "../escape.xml",
    "/abs/path.xml",
    "\\windows\\path",
    "C:/Windows/x",
    "",
])
def test_unsafe_names(name):
    assert is_safe_member_name(name) is False


# ---- assert_safe_archive ---------------------------------------------------

def _zip(tmp_path, members, name="x.zip"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return p


def test_normal_archive_passes(tmp_path):
    p = _zip(tmp_path, {"word/document.xml": b"<w:document/>", "a.png": b"x" * 100})
    assert_safe_archive(p)  # no raise


def test_too_many_entries(tmp_path):
    p = _zip(tmp_path, {f"f{i}.txt": b"" for i in range(20)})
    with pytest.raises(UnsafeArchiveError, match="entries exceeds"):
        assert_safe_archive(p, ArchiveLimits(max_entries=5))


def test_member_too_large(tmp_path):
    p = _zip(tmp_path, {"big.bin": b"A" * 5000})
    with pytest.raises(UnsafeArchiveError, match="too large"):
        assert_safe_archive(p, ArchiveLimits(max_member_uncompressed=1000))


def test_total_too_large(tmp_path):
    p = _zip(tmp_path, {"a.bin": b"A" * 800, "b.bin": b"B" * 800})
    with pytest.raises(UnsafeArchiveError, match="Expanded size"):
        assert_safe_archive(p, ArchiveLimits(max_total_uncompressed=1000))


def test_zip_slip_member_rejected(tmp_path):
    # Build a zip whose central directory declares an unsafe member name.
    p = tmp_path / "slip.zip"
    with zipfile.ZipFile(p, "w") as z:
        info = zipfile.ZipInfo("../../evil.xml")
        z.writestr(info, b"pwned")
    with pytest.raises(UnsafeArchiveError, match="Unsafe member path"):
        assert_safe_archive(p)


def test_compression_bomb_ratio(tmp_path):
    # A >1 MB member of highly compressible data trips the ratio guard.
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        assert_safe_archive(p, ArchiveLimits(
            max_member_uncompressed=50 * 1024 * 1024,
            max_total_uncompressed=50 * 1024 * 1024,
            max_ratio=50,
        ))
