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
    )


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

        blocks: list[Block] = []
        idx = 0
        for item in self._book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if isinstance(item, epub.EpubNav):
                continue    # EpubNav.get_type()==ITEM_DOCUMENT; nav is not body content
            soup = BeautifulSoup(item.get_content(), "html.parser")
            body = soup.find("body") or soup
            for el in body.find_all(True, recursive=False):
                blocks.append(_epub_block(idx, el, item.get_id()))
                idx += 1

        self._blocks = blocks

        return UnifiedDocument(
            blocks=blocks,
            meta=DocumentMeta(source_format="epub", path=path),
            page_break_after=set(),
            section_break_at=set(),
            isolated=_compute_isolated(blocks),
            toc_entries=_extract_toc(self._book),
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
