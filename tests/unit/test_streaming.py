"""
tests/unit/test_streaming.py
============================
Unit tests for Phase 8 lazy-streaming guarantees:

  1. Block.source is always None after DocxAdapter.load().
  2. DocxPackage has no _doc_tree / _body attributes after __init__.
  3. iter_block_children() yields the same count as block_children().
  4. _load_blocks_streaming() produces the same block count as the
     element-list path.
  5. _DocxWriter.iter_write() yields one manifest entry per chapter
     and chapter files are well-formed ZIPs.
"""
from __future__ import annotations
import pathlib
import zipfile

import pytest

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
LEGACY = pathlib.Path(__file__).parent.parent / "golden_books"

# Pick one DOCX for unit-level streaming tests.
_SAMPLE = GOLDEN / "english" / "sign_of_the_four.docx"


def _skip_if_missing(p: pathlib.Path):
    return pytest.mark.skipif(not p.exists(), reason=f"book not on disk: {p.name}")


@_skip_if_missing(_SAMPLE)
def test_block_source_is_none_after_load():
    """All Block.source fields must be None after streaming load."""
    from book_splitter.adapters.docx_adapter import DocxAdapter
    adapter = DocxAdapter()
    doc = adapter.load(str(_SAMPLE))
    assert all(b.source is None for b in doc.blocks), \
        "Block.source must be None in streaming mode"


@_skip_if_missing(_SAMPLE)
def test_docx_package_has_no_full_tree():
    """DocxPackage should not keep _doc_tree or _body as permanent attrs."""
    from book_splitter.docx_package import DocxPackage
    pkg = DocxPackage(str(_SAMPLE))
    assert not hasattr(pkg, "_doc_tree") or pkg.__dict__.get("_doc_tree") is None, \
        "_doc_tree should be freed after __init__"
    assert not hasattr(pkg, "_body") or pkg.__dict__.get("_body") is None, \
        "_body should be freed after __init__"


@_skip_if_missing(_SAMPLE)
def test_iter_block_children_count_matches_block_children():
    """iter_block_children() must yield the same count as block_children()."""
    from book_splitter.docx_package import DocxPackage
    pkg = DocxPackage(str(_SAMPLE))

    streaming_count = sum(1 for el in pkg.iter_block_children())
    list_count = len(pkg.block_children())
    assert streaming_count == list_count, (
        f"iter_block_children yielded {streaming_count} items; "
        f"block_children returned {list_count}"
    )


@_skip_if_missing(_SAMPLE)
def test_streaming_block_count_matches_element_count():
    """_load_blocks_streaming() must produce the same block count as
    the element-list path."""
    from book_splitter.docx_package import DocxPackage
    from book_splitter.blocks import StyleResolver
    from book_splitter.adapters.docx_adapter import _load_blocks_streaming

    pkg = DocxPackage(str(_SAMPLE))
    resolver = StyleResolver(pkg._raw["word/styles.xml"])

    stream_blocks, _, _, _ = _load_blocks_streaming(
        pkg._raw["word/document.xml"], resolver
    )
    list_blocks = pkg.block_children()

    assert len(stream_blocks) == len(list_blocks), (
        f"Streaming produced {len(stream_blocks)} blocks; "
        f"list path produced {len(list_blocks)}"
    )


@_skip_if_missing(_SAMPLE)
def test_iter_write_yields_valid_docx_files(tmp_path):
    """iter_write() must yield one entry per chapter; each file must be
    a valid ZIP (i.e. a well-formed DOCX container)."""
    from book_splitter.adapters.registry import get_adapter
    from book_splitter.detector import detect

    adapter = get_adapter(str(_SAMPLE))
    doc = adapter.load(str(_SAMPLE))
    plan = detect(doc)

    if plan.abstained:
        pytest.skip("Detector abstained on sample book")

    writer = adapter.make_writer()
    entries = list(writer.iter_write(plan, str(tmp_path)))

    assert len(entries) == len(plan.chapters), (
        f"iter_write yielded {len(entries)} entries for "
        f"{len(plan.chapters)} chapters"
    )
    for entry in entries:
        out_path = tmp_path / entry["file"]
        assert out_path.exists(), f"Output file missing: {entry['file']}"
        # Must be a valid ZIP
        assert zipfile.is_zipfile(str(out_path)), \
            f"Output is not a valid ZIP: {entry['file']}"
