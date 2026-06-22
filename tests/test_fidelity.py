"""
test_fidelity.py
================
PRESERVATION GUARANTEE — wraps book_splitter.verify for pytest CI.

For every non-abstaining golden DOCX book: split it into chapters, then assert
that every output chapter is byte-identical on all non-document parts (styles,
images, fonts, theme, headers/footers) and has a well-formed document.xml with
a trailing sectPr (page-settings element).

This is the *split guarantee* test: if it passes the engine has never silently
corrupted a file. If it fails on a previously-passing book, a change in the
emitter or DocxPackage broke structural preservation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect
from book_splitter.verify import verify

GOLDEN      = Path(__file__).parent / "golden"
GOLDEN_BOOKS = Path(__file__).parent / "golden_books"

# Only DOCX books that do NOT abstain are fidelity-testable.
# EPUBs abstain and have no DOCX-specific preservation guarantee.
_FIDELITY_BOOKS = [
    GOLDEN       / "english"  / "sign_of_the_four.docx",
    GOLDEN       / "english"  / "quiver_dont_quake.docx",
    GOLDEN       / "assamese" / "village_reformation_assamese.docx",
    GOLDEN_BOOKS / "richest_man_babylon.docx",
]


def _id(p: Path) -> str:
    return f"{p.parent.name}/{p.name}"


@pytest.mark.fidelity
@pytest.mark.parametrize("book", [pytest.param(p, id=_id(p)) for p in _FIDELITY_BOOKS])
def test_split_preserves_all_parts(book: Path, tmp_path: Path) -> None:
    """Split *book* then verify every output chapter passes the three checks:
    1. same ZIP part set as the original,
    2. every non-document.xml part byte-identical to the original,
    3. document.xml well-formed and has a trailing sectPr.
    """
    if not book.exists():
        pytest.skip(f"golden book missing: {book}")

    adapter = get_adapter(str(book))
    plan    = detect(adapter.load(str(book)))

    assert not plan.abstained, (
        f"{book.name} abstained unexpectedly — run the golden regression suite "
        "to confirm detection is still working.")

    out_dir = str(tmp_path / book.stem)
    adapter.make_writer().write(plan, out_dir)

    ok = verify(str(book), out_dir)   # prints per-chapter status to stdout
    assert ok, (
        f"Preservation check failed for {book.name}. "
        "See the per-chapter output above for which part failed.")
