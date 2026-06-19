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


def extract_toc(children) -> list[dict] | None:
    """Return [{index, title, level}] for body headings carrying _Toc bookmarks,
    in body order — or None if the document has no Word-generated TOC.

    `children` must be the same list the adapter enumerates for Block.index
    (DocxPackage.block_children()), so indices line up exactly.

    Matching by ANCHOR (not text) is language-agnostic: works identically for
    English, Tamil, Hindi, Assamese, Bhojpuri, CJK — the script never appears
    in a bookmark id.
    """
    region = toc_region(children)
    entries: list[dict] = []
    for i, el in enumerate(children):
        anchor = toc_anchor(el)
        if anchor is None:
            continue
        meta = region.get(anchor, {})
        entries.append({
            "index": i,
            "title": meta.get("title") or _para_text(el),
            "level": meta.get("level", 0),
        })
    return entries or None