"""
adapters/epub_resources.py
==========================
Collect every asset a chapter's XHTML transitively references, so an extracted
chapter becomes a self-contained EPUB.

Three correctness rules that naive implementations get wrong:

1. **Follow external CSS.** Fonts live in ``@font-face`` rules inside ``.css``
   files, not in the chapter HTML. We follow each ``<link>``'d stylesheet and
   scan it (transitively, including ``@import``) so fonts/background images come
   along.
2. **POSIX paths only.** EPUB internal paths are always POSIX. We resolve with
   ``posixpath`` — never ``os.path`` — so ``../images/a.png`` does not turn into
   a backslash mess on Windows.
3. **Preserve ``<head>``.** When extracting a fragment we carry the ``<head>``
   ``<link>`` / ``<meta>`` / ``<title>`` forward, otherwise the chapter loses
   its stylesheet reference.

``lxml.html`` is imported lazily inside the functions that need it so importing
this module never forces an EPUB-only dependency.
"""
from __future__ import annotations

import copy
import posixpath
import re

from ..safety import is_safe_member_name

_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.IGNORECASE)
_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?['"]([^'")]+)['"]""", re.IGNORECASE)
_XLINK = "{http://www.w3.org/1999/xlink}href"


def _resolve(base_href: str, ref: str) -> str:
    """Resolve *ref* against the directory of *base_href* into a normalized
    POSIX path. Returns ``""`` for external / inline refs (nothing to copy).

    Pure and dependency-free — this is the function most worth unit-testing, and
    the guard against anyone swapping in ``os.path``.
    """
    ref = (ref or "").split("#", 1)[0].split("?", 1)[0].strip()
    if not ref or ref.startswith(("http://", "https://", "data:", "//")):
        return ""
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_href), ref))


def collect_assets(book, chapter_href: str, chapter_html: bytes) -> list:
    """Return a deduped list of source EpubItems referenced (transitively) by
    this chapter's HTML.

    *book* is the source ``ebooklib`` ``EpubBook``; ``book.get_item_with_href``
    is used to map resolved POSIX paths back to items. Unresolvable / external
    refs are silently skipped.
    """
    from lxml import html as lxml_html

    found: dict[str, object] = {}
    seen_css: set[str] = set()

    def add(path: str) -> None:
        # Drop unsafe (zip-slip) hrefs before resolving them to an item.
        if path and is_safe_member_name(path) and path not in found:
            item = book.get_item_with_href(path)
            if item is not None:
                found[path] = item

    try:
        tree = lxml_html.fromstring(chapter_html)
    except Exception:
        return []

    css_stack: list[str] = []

    for link in tree.xpath('//link[@rel="stylesheet"][@href]'):
        p = _resolve(chapter_href, link.get("href"))
        if p:
            add(p)
            css_stack.append(p)
    for img in tree.xpath("//img[@src]"):
        add(_resolve(chapter_href, img.get("src")))
    for image in tree.xpath('//*[local-name()="image"]'):          # SVG <image>
        add(_resolve(chapter_href, image.get(_XLINK) or image.get("href") or ""))
    for style in tree.xpath("//style"):                            # inline CSS
        for ref in _URL_RE.findall(style.text_content() or ""):
            add(_resolve(chapter_href, ref))

    # Follow external CSS for fonts / images / @import (the commonly-missed gap).
    while css_stack:
        css_path = css_stack.pop()
        if css_path in seen_css:
            continue
        seen_css.add(css_path)
        css_item = book.get_item_with_href(css_path)
        if css_item is None:
            continue
        css_text = css_item.get_content().decode("utf-8", "ignore")
        for ref in _URL_RE.findall(css_text):       # @font-face src, bg images
            add(_resolve(css_path, ref))            # base = the CSS file, not HTML
        for imp in _IMPORT_RE.findall(css_text):
            p = _resolve(css_path, imp)
            if p:
                add(p)
                css_stack.append(p)

    return list(found.values())


def extract_fragment(html_bytes: bytes, html_id: str) -> bytes:
    """Pull the element with ``id=html_id`` into a standalone XHTML document,
    preserving the source ``<head>`` links/meta/title.

    Used for the multiple-chapters-per-file case. Returns the original bytes
    unchanged if the id is not found.
    """
    from lxml import html as lxml_html

    src = lxml_html.fromstring(html_bytes)
    matches = src.xpath("//*[@id=$i]", i=html_id)
    if not matches:
        return html_bytes

    doc = lxml_html.fromstring("<html><head></head><body></body></html>")
    head, body = doc.find(".//head"), doc.find(".//body")

    src_head = src.find(".//head")
    if src_head is not None:
        for tag in src_head.xpath(".//link | .//meta | .//title"):
            head.append(copy.deepcopy(tag))

    body.append(copy.deepcopy(matches[0]))
    return lxml_html.tostring(doc, encoding="utf-8")
