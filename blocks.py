"""
blocks.py
=========
DOCX-SPECIFIC HELPERS (kept here so docx_adapter.py can import them).

After the Format Adapter refactor:
  * Block         -> moved to models.py (format-neutral dataclass)
  * build_blocks  -> moved to adapters/docx_adapter.py (_docx_block)
  * _text_of      -> stays here (used by docx_adapter and StyleResolver callers)
  * StyleResolver -> stays here (DOCX-only XML walking; imported by docx_adapter)
  * body_baseline_size -> stays here; works on models.Block via duck-typing

Two correctness details most splitters get wrong, handled by StyleResolver:

1. STYLE INHERITANCE. A paragraph's "bold" or "heading-ness" can come from its
   paragraph style, not from the run. `StyleResolver` walks the basedOn chain
   (with a cycle guard) so a custom style like "MyChapterTitle" based on
   "Heading 1" is correctly recognised as a level-0 heading, and a Title style
   that visually implies large/bold text is detected even when the runs carry
   no explicit <w:b>/<w:sz>.

2. RELATIVE FONT SIZE. We never hard-code "16pt = heading". We compute the body
   baseline (the most common run size) and a heading size band by clustering,
   so an 11pt-body book whose chapter titles are 20pt and a 12pt-body book whose
   titles are 14pt are both handled. (See detector.compute_baselines.)
"""

from __future__ import annotations
from collections import Counter
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t")).strip()


class StyleResolver:
    """Parses styles.xml and answers questions about resolved style meaning."""

    def __init__(self, styles_xml: bytes):
        self.by_id: dict[str, dict] = {}
        root = etree.fromstring(styles_xml)
        for st in root.iter(W + "style"):
            sid = st.get(W + "styleId")
            if not sid:
                continue
            name_el = st.find(W + "name")
            based_el = st.find(W + "basedOn")
            ol_el = st.find(f".//{W}outlineLvl")
            self.by_id[sid] = {
                "name": (name_el.get(W + "val") if name_el is not None else "") or "",
                "basedOn": based_el.get(W + "val") if based_el is not None else None,
                "outline": int(ol_el.get(W + "val")) if ol_el is not None else None,
                "bold": st.find(f".//{W}rPr/{W}b") is not None,
            }

    def heading_level(self, style_id: str | None) -> int | None:
        """Return a 0-based heading level for a style, walking basedOn.

        Recognises: built-in 'heading N', 'Title' (treated as level 0),
        'Subtitle' (level 1), and any custom style whose name/id contains
        'chapter'/'part' or whose chain leads to one of the above.
        Returns None for body styles."""
        seen = set()
        sid = style_id
        while sid and sid not in seen:
            seen.add(sid)
            meta = self.by_id.get(sid)
            if not meta:
                return None
            name = meta["name"].lower()
            if meta["outline"] is not None:
                return meta["outline"]
            if "heading" in name:
                digits = "".join(c for c in name if c.isdigit())
                return int(digits) - 1 if digits else 0
            if name in ("title",) or sid.lower() == "title":
                return 0
            if name in ("subtitle", "sub title") or sid.lower() == "subtitle":
                return 1
            if "chapter" in name or "chapter" in sid.lower():
                return 0
            if "part" in name and "particle" not in name:
                return 0
            sid = meta["basedOn"]
        return None


def body_baseline_size(blocks) -> int:
    """Most common run size across non-empty paragraphs = body text size."""
    c = Counter()
    for b in blocks:
        if b.is_paragraph and not b.is_empty and b.max_size:
            c[b.max_size] += 1
    return c.most_common(1)[0][0] if c else 11
