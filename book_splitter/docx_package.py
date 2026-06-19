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
"""

from __future__ import annotations
import copy
import zipfile
from lxml import etree

# OOXML WordprocessingML namespace (the "w:" prefix).
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# document.xml must be serialized with the same prolog Word uses.
_XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# Block-level element tags that can appear as direct children of <w:body>.
_BLOCK_TAGS = {W + "p", W + "tbl", W + "sdt"}


class DocxPackage:
    """In-memory representation of a .docx package with loss-free cloning."""

    def __init__(self, path: str):
        self.path = path
        # Preserve the EXACT zip member order and raw bytes of every part.
        self._names: list[str] = []
        self._raw: dict[str, bytes] = {}
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                self._names.append(info.filename)
                self._raw[info.filename] = z.read(info.filename)

        # Parse only the main document part; everything else stays as raw bytes.
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        self._doc_tree = etree.fromstring(self._raw["word/document.xml"], parser)
        self._body = self._doc_tree.find(W + "body")
        if self._body is None:
            raise ValueError("Malformed DOCX: <w:body> not found in document.xml")

        # Empty-body skeleton built ONCE: clone_with_blocks copies this (tiny)
        # per chapter instead of the whole document. Output stays byte-identical.
        self._skeleton = copy.deepcopy(self._doc_tree)
        skel_body = self._skeleton.find(W + "body")
        kids = list(skel_body)
        keep_sect = kids[-1] if (kids and kids[-1].tag == W + "sectPr") else None
        for child in kids:
            if child is not keep_sect:
                skel_body.remove(child)

    # ---- read-only structural access -------------------------------------

    @property
    def body(self):
        return self._body

    def body_children(self) -> list:
        """All direct children of <w:body> in document order (paragraphs,
        tables, sdt blocks, and the trailing sectPr)."""
        return list(self._body)

    def block_children(self) -> list:
        """Body children EXCLUDING the final body-level <w:sectPr>.

        These are the units a chapter is made of. Tables and content controls
        travel with the surrounding paragraphs automatically because they are
        siblings in this same ordered list."""
        kids = self.body_children()
        if kids and kids[-1].tag == W + "sectPr":
            return kids[:-1]
        return kids

    def final_sectpr(self):
        """The body-level <w:sectPr>: page size, margins, header/footer refs.
        A deep copy of this is appended to EVERY chapter so each output file has
        identical page setup and header/footer wiring."""
        kids = self.body_children()
        if kids and kids[-1].tag == W + "sectPr":
            return kids[-1]
        return None

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
        import io
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
