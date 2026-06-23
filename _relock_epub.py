import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

root = os.path.dirname(__file__)
epub_dir = os.path.join(root, "tests", "golden", "epub")

NOTE = ("Multi-file Project Gutenberg EPUB: nav hrefs resolve to block indices "
        "(F1/F2), spine starts seed candidates (F3), and chapter-wrapper divs "
        "are descended (F4). Authoritative TOC drives the split.")

for stem in ("pg521", "pg2130"):
    book = os.path.join(epub_dir, f"{stem}.epub")
    plan = detect(get_adapter(book).load(book), level_filter="auto")
    expected = {
        "format": "epub",
        "source": f"Project Gutenberg EPUB ({stem}) — multi-file nav + spine",
        "abstained": plan.abstained,
        "chapter_count": len(plan.chapters),
        "titles": [c.title for c in plan.chapters],
        "note": NOTE,
    }
    out = os.path.join(epub_dir, f"{stem}.expected.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(expected, fh, ensure_ascii=False, indent=2)
    print(f"Locked {stem}: n={len(plan.chapters)} abstained={plan.abstained}")
