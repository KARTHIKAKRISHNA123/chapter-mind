import ebooklib, os
from ebooklib import epub

base = os.path.join(os.path.dirname(__file__), "tests", "golden", "epub")
for name in ("pg521.epub", "pg2130.epub"):
    b = epub.read_epub(os.path.join(base, name), options={"ignore_ncx": False})
    docs = [it for it in b.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if not isinstance(it, epub.EpubNav)]
    print("====", name)
    print("spine length:", len(b.spine))
    print("content docs:", len(docs))
    print("doc names:", [d.get_name() for d in docs][:15])
    print("toc entries (top-level):", len(b.toc))

    def walk(items, lvl, out):
        for it in items:
            if isinstance(it, tuple) and len(it) == 2:
                sec, ch = it
                out.append((lvl, getattr(sec, "title", ""), getattr(sec, "href", "")))
                walk(ch, lvl + 1, out)
            else:
                out.append((lvl, getattr(it, "title", ""), getattr(it, "href", "")))
        return out

    rows = walk(b.toc, 0, [])
    print("nav rows:", len(rows))
    for r in rows[:20]:
        print("   nav:", r)
    print()
