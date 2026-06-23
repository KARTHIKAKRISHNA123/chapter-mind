"""
docx_package.py
===============
THE FORMAT-PRESERVATION CORE.

A .docx file is a ZIP archive of XML "parts" (document.xml, styles.xml,
numbering.xml, theme1.xml, headers/footers, media/*, fontTable.xml, etc.).

The #1 reason naive splitters destroy formatting is that they *rebuild* a new
document from scratch (e.g. with python-docx) and copy paragraphs across. That
reconstruction silently drops styles, theme fonts, numbering definitions,
embedded fonts, headers/footers, section page setup, and image relationships.

This module takes the opposite, loss-free approach:

    For every chapter we CLONE THE ENTIRE ORIGINAL PACKAGE byte-for-byte and
    only rewrite ONE part -- word/document.xml -- so that its <w:body> keeps
    just the block elements belonging to that chapter, PLUS the document's final
    <w:sectPr> (which carries page size, margins, and header/footer references).

Because every other part is copied verbatim:
    * styles.xml ............ styles preserved 100%
    * numbering.xml ......... list numbering definitions preserved
    * theme1.xml + fonts/ ... theme + embedded fonts preserved
    * media/* ............... every image preserved
    * header*.xml/footer*.xml headers & footers preserved
    * settings.xml .......... page/compat settings preserved
    * document.xml.rels ..... image/hyperlink relationships still resolve

The runs (<w:r>), run-properties (<w:rPr>), tables (<w:tbl>) and drawings
(<w:drawing>) inside the kept blocks are *the original elements*, deep-copied
without modification -- so their formatting is identical to the source.

No reconstruction. We only DELETE sibling blocks. That is what makes
preservation effectively 100%.

Phase 8 — lazy streaming
------------------------
``__init__`` parses word/document.xml ONCE to build ``_skeleton`` (the
empty-body template used by ``clone_with_blocks``), then immediately frees
the full lxml tree.  The adapter's streaming loader iterates body children
via ``iter_block_children()`` (an iterparse generator) and clears each
element after feature extraction, so peak in-RAM lxml data is bounded by
one body child at a time during detection.

``block_children()`` still works as a compatibility shim (full re-parse)
for callers that need a random-access list (e.g. the legacy splitter.py).
"""

from __future__ import annotations
import copy
import io
import zipfile
from lxml import etree

# OOXML WordprocessingML namespace (the "w:" prefix).
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# document.xml must be serialized with the same prolog Word uses.
_XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# Block-level element tags that can appear as direct children of <w:body>.
_BLOCK_TAGS = {W + "p", W + "tbl", W + "sdt"}


class DocxPackage:
    """In-memory representation of a .docx package with loss-free cloning.

    Memory contract (Phase 8)
    -------------------------
    After ``__init__`` returns:

    * ``_raw``      – all ZIP member bytes (needed for clone_with_blocks).
    * ``_skeleton`` – tiny empty-body tree (needed for clone_with_blocks).
    * ``_names``    – ordered ZIP member names.

    The full lxml element tree of ``word/document.xml`` is parsed briefly
    to build ``_skeleton``, then freed.  The adapter uses
    ``iter_block_children()`` to stream body children one at a time.
    """

    def __init__(self, path: str):
        self.path = path
        # Preserve the EXACT zip member order and raw bytes of every part.
        self._names: list[str] = []
        self._raw: dict[str, bytes] = {}
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                self._names.append(info.filename)
                self._raw[info.filename] = z.read(info.filename)

        # Parse document.xml once — only to build _skeleton.
        # The full tree is freed immediately afterwards; the adapter
        # uses iter_block_children() for streaming feature extraction.
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        _tmp_tree = etree.fromstring(self._raw["word/document.xml"], parser)
        _tmp_body = _tmp_tree.find(W + "body")
        if _tmp_body is None:
            raise ValueError("Malformed DOCX: <w:body> not found in document.xml")

        # Empty-body skeleton: doc root + body with only the trailing sectPr.
        # clone_with_blocks() deep-copies this tiny tree per chapter so that
        # all namespace declarations and document-level attributes are preserved.
        self._skeleton = copy.deepcopy(_tmp_tree)
        skel_body = self._skeleton.find(W + "body")
        kids = list(skel_body)
        keep_sect = kids[-1] if (kids and kids[-1].tag == W + "sectPr") else None
        for child in kids:
            if child is not keep_sect:
                skel_body.remove(child)

        # Free the full parse — only _skeleton and _raw are retained.
        del _tmp_tree, _tmp_body

    # ---- streaming access (preferred) ------------------------------------

    def iter_block_children(self):
        """Generator: yield each direct ``<w:body>`` child (except final
        ``<w:sectPr>``) in document order, parsed from the cached raw bytes.

        Depth-tracking iterparse ensures only ONE body-level element is fully
        built in RAM at a time.  The caller MUST call ``el.clear()`` on each
        yielded element after extracting all needed information; not doing so
        negates the memory benefit.

        Typical usage::

            for el in pkg.iter_block_children():
                block = extract_features(el)
                el.clear()          # ← required: free internal nodes
        """
        depth = 0
        body_depth: int | None = None

        for event, el in etree.iterparse(
            io.BytesIO(self._raw["word/document.xml"]),
            events=("start", "end"),
        ):
            if event == "start":
                depth += 1
                if el.tag == W + "body" and body_depth is None:
                    body_depth = depth
            else:  # "end"
                if body_depth is not None and depth == body_depth + 1:
                    if el.tag != W + "sectPr":
                        yield el
                    # caller clears the element; we only track depth
                depth -= 1

    # ---- compatibility shim (random-access) ------------------------------

    def block_children(self) -> list:
        """All direct body children EXCLUDING the final ``<w:sectPr>``.

        Re-parses ``word/document.xml`` from the cached raw bytes on every
        call; prefer ``iter_block_children()`` for large documents.

        Retained for backward compatibility with callers that need a
        random-access list (e.g. legacy ``splitter.py``).
        """
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        doc = etree.fromstring(self._raw["word/document.xml"], parser)
        body = doc.find(W + "body")
        kids = list(body) if body is not None else []
        if kids and kids[-1].tag == W + "sectPr":
            return kids[:-1]
        return kids

    def body_children(self) -> list:
        """All direct body children INCLUDING the trailing ``<w:sectPr>``."""
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        doc = etree.fromstring(self._raw["word/document.xml"], parser)
        body = doc.find(W + "body")
        return list(body) if body is not None else []

    # ---- the loss-free clone ---------------------------------------------

    def clone_with_blocks(self, blocks: list[etree._Element]) -> bytes:
        """Return .docx bytes identical to the original EXCEPT that <w:body>
        contains only `blocks` (deep-copied) followed by the preserved final
        <w:sectPr>. All other parts are byte-for-byte identical."""
        # 1. Clone the empty-body skeleton (tiny vs deep-copying the full doc).
        #    Skeleton already contains only the trailing sectPr, so page size,
        #    margins, and header/footer references are preserved automatically.
        doc = copy.deepcopy(self._skeleton)
        body = doc.find(W + "body")

        # 2. Insert chapter blocks before the preserved sectPr.
        kids = list(body)
        sect = kids[-1] if (kids and kids[-1].tag == W + "sectPr") else None
        pos = body.index(sect) if sect is not None else len(body)
        for blk in blocks:
            body.insert(pos, copy.deepcopy(blk))
            pos += 1

        new_document_xml = _XML_DECL + etree.tostring(
            doc, xml_declaration=False, encoding="UTF-8"
        )

        # 3. Re-zip: every member kept verbatim, only document.xml substituted.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in self._names:
                data = new_document_xml if name == "word/document.xml" else self._raw[name]
                z.writestr(name, data)
        return buf.getvalue()

    @staticmethod
    def write(path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)
