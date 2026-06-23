"""
adapters/epub_adapter.py
========================
EPUB side of the seam.

Dependencies (install separately): ebooklib, beautifulsoup4
    pip install ebooklib beautifulsoup4

Fidelity contract (weaker than DOCX by design):
  * Input fidelity: XHTML body structure + nav/NCX TOC are preserved in blocks.
  * Output fidelity: _EpubWriter reconstructs a valid single-chapter EPUB per
    detected chapter, carrying over CSS/images from the source.
    This differs from DOCX (ZIP clone) -- EPUB container/manifest model makes
    lossless slicing non-trivial, so reconstruction is the right trade-off.

Block mapping:
  * Each block-level element in each spine XHTML item -> one Block.
  * b.source = (item_id, element) -- writer uses item_id for CSS path
    resolution and element for HTML serialisation.
  * style_level derived from tag name: h1->0, h2->1, ..., h6->5.
  * page_break_after and section_break_at are empty (no element-level page
    breaks in EPUB); isolated is computed from block attributes.
"""

from __future__ import annotations
import os
import re
import uuid
from urllib.parse import unquote

from .base import DocumentAdapter, OutputWriter
from .epub_resources import collect_assets
from ..models import Block, DocumentMeta, UnifiedDocument
from ..naming import render_filename

# tag -> 0-based heading level (mirrors DOCX Heading 1 = level 0)
_HEADING_LEVEL: dict[str, int] = {
    "h1": 0, "h2": 1, "h3": 2, "h4": 3, "h5": 4, "h6": 5,
}

_PARA_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "dt", "dd", "blockquote", "pre",
})
_TABLE_TAGS = frozenset({"table"})

# F4: structural wrappers that GROUP block content rather than being content.
# We descend into these so a chapter wrapped in <div class="chapter"> exposes
# its heading/paragraphs as individual blocks instead of one giant block.
_CONTAINER_TAGS = frozenset({"div", "section", "article", "main"})


def _has_block_children(el) -> bool:
    """True if `el` directly contains block-level or further container children
    (so it is a wrapper to descend), vs. a leaf block whose text we keep whole."""
    return any(
        (c.name in _PARA_TAGS or c.name in _TABLE_TAGS or c.name in _CONTAINER_TAGS)
        for c in el.find_all(True, recursive=False)
    )


def _iter_block_elements(node):
    """Yield body block elements in reading order, descending into structural
    wrapper containers (F4) but never into paragraph/heading/table elements.

    A container with no block-level children (e.g. a <div> of inline text) is
    itself emitted as one block, so no text is ever dropped."""
    for c in node.find_all(True, recursive=False):
        if c.name in _CONTAINER_TAGS and _has_block_children(c):
            yield from _iter_block_elements(c)
        else:
            yield c


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------

def _epub_block(index: int, el, item_id: str) -> Block:
    """Build a format-neutral Block from one BS4 body-level element."""
    tag = el.name or ""
    is_p = tag in _PARA_TAGS
    is_t = tag in _TABLE_TAGS
    text = el.get_text(" ", strip=True) if (is_p or is_t) else ""
    return Block(
        index=index,
        source=(item_id, el),       # opaque to detection; used by writer
        tag=tag,
        is_paragraph=is_p,
        is_table=is_t,
        text=text,
        is_empty=(text == ""),
        word_count=len(text.split()),
        style_level=_HEADING_LEVEL.get(tag),
        bold=bool(el.find(["b", "strong"])),
        anchor_id=el.get("id") or None,   # F1: own HTML id for nav resolution
    )


def _element_ids(el) -> list[str]:
    """All anchor ids reachable from this block element: its own ``id`` plus any
    descendant ``id`` and legacy ``<a name=...>`` anchors. Lets nav hrefs that
    target an element *inside* a block (common when a chapter is wrapped in a
    div) still resolve to this block's index."""
    ids: list[str] = []
    own = el.get("id")
    if own:
        ids.append(own)
    for d in el.find_all(attrs={"id": True}):
        ids.append(d["id"])
    for a in el.find_all("a", attrs={"name": True}):
        ids.append(a["name"])
    return ids


def _compute_isolated(blocks: list[Block]) -> set:
    """Isolation heuristic -- identical to DOCX adapter; no XML access needed."""
    isolated: set = set()
    for i, b in enumerate(blocks):
        if not b.is_paragraph or b.is_empty or b.word_count > 12:
            continue
        prev_empty = i > 0 and blocks[i - 1].is_empty
        follows_body = any(
            blocks[j].is_paragraph and blocks[j].word_count > 16
            for j in range(i + 1, min(i + 4, len(blocks))))
        if prev_empty and follows_body:
            isolated.add(b.index)
    return isolated


def _extract_toc(book) -> list[dict] | None:
    """Flatten ebooklib book.toc hierarchy into a list of dicts.

    Each entry: {"title": str, "level": int (0-based), "href": str}.
    Returns None when empty so the engine scans body blocks instead.
    """
    def _walk(items, level):
        result = []
        for item in items:
            if hasattr(item, "title") and not isinstance(item, tuple):
                result.append({
                    "title": item.title or "",
                    "level": level,
                    "href": getattr(item, "href", "") or "",
                })
            elif isinstance(item, tuple) and len(item) == 2:
                section, children = item
                result.append({
                    "title": getattr(section, "title", "") or "",
                    "level": level,
                    "href": getattr(section, "href", "") or "",
                })
                result.extend(_walk(children, level + 1))
        return result

    entries = _walk(book.toc, 0)
    return entries if entries else None


def _resolve_toc(book, item_first_index, anchor_index, name_to_id):
    """F2 -- turn raw nav/NCX entries ({title, level, href}) into engine-ready
    entries ({title, level, index}) by resolving each ``href`` to a block index.

    Resolution order per entry:
      1. ``file#anchor`` -> exact block carrying that id  (anchor_index)
      2. ``file``        -> first block of that spine document (item_first_index)
      3. unresolvable    -> dropped (engine falls back to body scanning)

    Returns a de-duplicated, index-sorted list, or None when fewer than two
    entries resolve (not enough to be authoritative)."""
    raw = _extract_toc(book)
    if not raw:
        return None

    def _item_for(file_part: str):
        if not file_part:
            return None
        f = unquote(file_part)
        return (name_to_id.get(f)
                or name_to_id.get(f.rsplit("/", 1)[-1]))   # basename fallback

    resolved = []
    for e in raw:
        file_part, _, anchor = (e.get("href") or "").partition("#")
        item_id = _item_for(file_part)
        if item_id is None:
            continue
        idx = None
        if anchor:
            idx = anchor_index.get((item_id, unquote(anchor)))
        if idx is None:
            idx = item_first_index.get(item_id)
        if idx is None:
            continue
        resolved.append({"title": e.get("title", "") or "",
                         "level": e.get("level", 0), "index": idx})

    if len(resolved) < 2:
        return None

    # De-dupe by block index, keeping the shallowest (top-level) title.
    by_index: dict[int, dict] = {}
    for r in sorted(resolved, key=lambda r: (r["index"], r["level"])):
        by_index.setdefault(r["index"], r)
    out = sorted(by_index.values(), key=lambda r: r["index"])
    return out if len(out) >= 2 else None


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class _EpubWriter(OutputWriter):
    """Writes one EPUB file per chapter."""

    def __init__(self, book, original_path: str):
        self._book = book
        self._original_path = original_path
        self._blocks: list[Block] = []   # set by EpubAdapter.make_writer()

    def write(self, plan, out_dir: str, pattern: str | None = None) -> list[dict]:
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError(
                "ebooklib is required for EPUB output. "
                "Install it with: pip install ebooklib"
            )
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError(
                "beautifulsoup4 is required for EPUB output. "
                "Install it with: pip install beautifulsoup4"
            )

        os.makedirs(out_dir, exist_ok=True)

        item_map = {
            item.get_id(): item
            for item in self._book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if not isinstance(item, epub.EpubNav)   # EpubNav shares ITEM_DOCUMENT type
        }
        media_items = [
            item for item in self._book.get_items()
            if item.get_type() not in (
                ebooklib.ITEM_DOCUMENT,
                ebooklib.ITEM_NAVIGATION,   # EpubNcx.get_type() == ITEM_NAVIGATION == 4
                                            # (ebooklib has no ITEM_NCX constant)
            )
        ]
        lang_meta = self._book.get_metadata("DC", "language")
        lang = lang_meta[0][0] if lang_meta else "en"

        manifest = []

        for ch_idx, ch in enumerate(plan.chapters):
            ch_blocks = self._blocks[ch.start:ch.end]
            if not ch_blocks:
                continue

            html_parts: list[str] = []
            css_links: list[str] = []
            seen_item_ids: set = set()
            chapter_assets: dict = {}        # id -> source EpubItem (deduped)

            for b in ch_blocks:
                item_id, el = b.source
                if item_id not in seen_item_ids:
                    seen_item_ids.add(item_id)
                    orig = item_map.get(item_id)
                    if orig:
                        soup = BeautifulSoup(orig.get_content(), "html.parser")
                        for link in soup.find_all("link", rel="stylesheet"):
                            href = link.get("href", "")
                            if href and href not in css_links:
                                css_links.append(href)
                        # Precise, POSIX-safe, transitive asset resolution:
                        # follows external CSS to pull in @font-face/url() assets.
                        # Resolved relative to THIS source item's own href.
                        try:
                            for a in collect_assets(
                                self._book, orig.get_name(), orig.get_content()
                            ):
                                chapter_assets[a.get_id()] = a
                        except Exception:
                            pass            # fall back to all media below
                html_parts.append(str(el))

            css_block = "\n".join(
                f'<link rel="stylesheet" type="text/css" href="{h}"/>'
                for h in css_links
            )
            body_content = "\n".join(html_parts)
            xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"'
                ' "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"'
                f' xml:lang="{lang}">\n'
                "<head>\n"
                f"<title>{_escape(ch.title)}</title>\n"
                f"{css_block}\n"
                "</head>\n"
                "<body>\n"
                f"{body_content}\n"
                "</body>\n"
                "</html>\n"
            )

            slug = _slug(ch.title, f"chapter_{ch_idx:02d}")
            if pattern:
                fname = render_filename(
                    pattern, ordinal=ch_idx, title=ch.title, ext=".epub"
                )
            else:
                fname = f"{ch_idx:02d}_{slug}.epub"
            out_path = os.path.join(out_dir, fname)

            new_book = epub.EpubBook()
            new_book.set_identifier(str(uuid.uuid4()))
            new_book.set_title(ch.title)
            new_book.set_language(lang)
            # Prefer the precisely-referenced assets; fall back to every media
            # item if resolution found nothing (safety net — never drop assets).
            assets = list(chapter_assets.values()) or media_items
            for item in assets:
                new_book.add_item(item)
            ch_item = epub.EpubHtml(
                title=ch.title,
                file_name=f"{slug}.xhtml",
                lang=lang,
            )
            ch_item.content = xhtml.encode("utf-8")
            new_book.add_item(ch_item)
            new_book.toc = [epub.Link(ch_item.file_name, ch.title, slug)]
            new_book.add_item(epub.EpubNcx())
            new_book.add_item(epub.EpubNav())
            new_book.spine = ["nav", ch_item]
            epub.write_epub(out_path, new_book, {})

            manifest.append({
                "file": fname,
                "title": ch.title,
                "level": ch.level,
                "confidence": ch.confidence,
                "blocks": [ch.start, ch.end],
            })

        return manifest


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class EpubAdapter(DocumentAdapter):
    extensions = (".epub",)

    def load(self, path: str) -> UnifiedDocument:
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError:
            raise ImportError(
                "ebooklib is required for EPUB support. "
                "Install it with: pip install ebooklib"
            )
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError(
                "beautifulsoup4 is required for EPUB support. "
                "Install it with: pip install beautifulsoup4"
            )

        self._book = epub.read_epub(path, options={"ignore_ncx": False})
        self._path = path

        # Document title (used by segmentation to recognise the title page).
        _md = self._book.get_metadata("DC", "title")
        _title = (_md[0][0] if _md and _md[0] else "") or ""

        blocks: list[Block] = []
        idx = 0
        item_first_index: dict[str, int] = {}   # item_id -> first block index
        anchor_index: dict[tuple, int] = {}      # (item_id, anchor) -> block index
        spine_starts: set[int] = set()           # F3: first block of each spine doc
        name_to_id: dict[str, str] = {}          # href file -> item_id (+ basename)

        for item in self._book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if isinstance(item, epub.EpubNav):
                continue    # EpubNav.get_type()==ITEM_DOCUMENT; nav is not body content
            item_id = item.get_id()
            name = item.get_name() or ""
            name_to_id.setdefault(name, item_id)
            name_to_id.setdefault(name.rsplit("/", 1)[-1], item_id)

            soup = BeautifulSoup(item.get_content(), "html.parser")
            body = soup.find("body") or soup
            first_for_item: int | None = None
            for el in _iter_block_elements(body):        # F4: descend wrappers
                blocks.append(_epub_block(idx, el, item_id))
                if first_for_item is None:
                    first_for_item = idx
                for an in _element_ids(el):              # F1: index every anchor
                    anchor_index.setdefault((item_id, an), idx)
                idx += 1
            if first_for_item is not None:
                item_first_index[item_id] = first_for_item
                spine_starts.add(first_for_item)         # F3

        self._blocks = blocks

        return UnifiedDocument(
            blocks=blocks,
            meta=DocumentMeta(source_format="epub", path=path, title=_title),
            page_break_after=set(),
            section_break_at=set(),
            isolated=_compute_isolated(blocks),
            spine_starts=spine_starts,
            toc_entries=_resolve_toc(self._book, item_first_index,   # F2
                                     anchor_index, name_to_id),
        )

    def make_writer(self) -> OutputWriter:
        writer = _EpubWriter(self._book, self._path)
        writer._blocks = self._blocks
        return writer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Minimal XML/HTML escaping for attribute and element content."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _slug(title: str, fallback: str = "chapter") -> str:
    """Turn a chapter title into a safe filename stem."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:60] or fallback
