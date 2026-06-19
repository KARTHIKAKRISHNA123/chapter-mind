# book_splitter/adapters/docx_toc.py
"""Language-agnostic TOC detection via Word's _Toc bookmark anchors.
A heading carries <w:bookmarkStart w:name="_Toc..."/>; the TOC entry
hyperlinks to it with w:anchor="_Toc...". Match by anchor, never by text."""
from __future__ import annotations
from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
def q(t): return f"{{{W}}}{t}"


def toc_anchor(el) -> str | None:
    """Return the _Toc bookmark a body paragraph carries (it's a heading), else None."""
    if el.tag != q("p"):
        return None
    for bm in el.iter(q("bookmarkStart")):
        name = bm.get(q("name")) or ""
        if name.startswith("_Toc"):
            return name
    return None


def _para_text(el) -> str:
    return "".join(t.text or "" for t in el.iter(q("t"))).strip()


def toc_region(body) -> dict[str, dict]:
    """Map _Toc anchor -> {'title','level'} from the TOC LISTING paragraphs
    (TOC1..TOC9 styles and/or hyperlinks to _Toc). Language-agnostic: level
    comes from the style id number, title from the entry text."""
    region: dict[str, dict] = {}
    for el in body:
        if el.tag != q("p"):
            continue
        sid = ""
        pPr = el.find(q("pPr"))
        if pPr is not None:
            ps = pPr.find(q("pStyle"))
            sid = (ps.get(q("val")) or "").lower() if ps is not None else ""
        for hl in el.iter(q("hyperlink")):
            a = hl.get(q("anchor")) or ""
            if a.startswith("_Toc"):
                level = int(sid[3:]) - 1 if (sid.startswith("toc") and sid[3:].isdigit()) else 0
                region[a] = {"title": _para_text(el), "level": max(level, 0)}
                break
    return region