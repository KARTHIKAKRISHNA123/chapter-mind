# book_splitter/adapters/docx_toc.py
"""Language-agnostic TOC detection via Word's _Toc bookmark anchors.
A heading carries <w:bookmarkStart w:name="_Toc..."/>; the TOC entry
hyperlinks to it with w:anchor="_Toc...". Match by anchor, never by text.

Fallback (pandoc / converted DOCX): a TOC *field* with a cached result but NO
_Toc bookmarks. `resolve_field_toc` resolves those cached entry titles to body
heading indices by monotonic title match — see its docstring."""
from __future__ import annotations
import re
from difflib import SequenceMatcher

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
def q(t): return f"{{{W}}}{t}"

_PAGENUM_SUFFIX = re.compile(r"[\s\.\u2026]+\d{1,4}\s*$")   # "..... 42"
_CONTENTS_RE = re.compile(
    r"^\s*(table of contents|contents|table des mati|sommaire|"
    r"inhaltsverzeichnis|inhalt)\b", re.I)


def _norm_toc(s: str) -> str:
    s = _PAGENUM_SUFFIX.sub("", s or "")
    s = re.sub(r"[^\w\s]", " ", s.lower())   # drop punctuation, keep any script
    return " ".join(s.split())


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


def resolve_field_toc(field_titles, heading_pool):
    """Resolve a Word TOC *field* cached result to body heading indices.

    Pandoc and many DOCX converters emit a TOC field whose cached rendering
    lists the chapter titles, but DON'T write the `_Toc` bookmark anchors that
    `extract_toc` relies on. This recovers an authoritative, index-based TOC by
    matching each cached entry title to a body heading.

    Parameters
    ----------
    field_titles : ordered visible titles from the TOC field's cached result.
    heading_pool : ordered ``[(block_index, title_text, style_level)]`` for
                   every styled body heading (any heading level; the value is
                   not assumed to be 0 — pandoc's off-by-one outline levels are
                   tolerated).

    Resolution is MONOTONIC: each entry matches the first heading at or after
    the previous match, so repeated titles (e.g. a book title appearing on the
    title page AND as a chapter) disambiguate by order. Language-agnostic — the
    match is on normalized title text in whatever script. Returns
    ``[{index, title, level}]`` or None if fewer than two entries resolve.
    """
    pool = [(i, _norm_toc(t), lvl) for (i, t, lvl) in heading_pool
            if t and not _CONTENTS_RE.match(t)]
    if not pool:
        return None
    min_lvl = min(lvl for _, _, lvl in pool)

    entries, cursor = [], 0
    for raw in field_titles:
        title = _PAGENUM_SUFFIX.sub("", raw or "").strip()
        if not title or _CONTENTS_RE.match(title):
            continue
        norm = _norm_toc(title)
        if not norm:
            continue
        chosen = None
        for j in range(cursor, len(pool)):
            idx, hnorm, lvl = pool[j]
            if not hnorm:
                continue
            if (hnorm == norm or hnorm.startswith(norm) or norm.startswith(hnorm)
                    or SequenceMatcher(None, norm, hnorm).ratio() >= 0.9):
                chosen = (j, idx, lvl)
                break
        if chosen:
            j, idx, lvl = chosen
            entries.append({"index": idx, "title": title,
                            "level": 0 if lvl <= min_lvl else 1})
            cursor = j + 1

    return entries if len(entries) >= 2 else None