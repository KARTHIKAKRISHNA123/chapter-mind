"""Build a synthetic SINGLE-FILE EPUB whose chapters are delimited only by
in-document anchors (<h2 id="chN">), with a nav that points to
content.xhtml#chN. This isolates F1 anchor resolution: if anchors did not
resolve, every nav entry would collapse to block 0 and the book would abstain.
Public-domain / synthetic content only.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
from ebooklib import epub
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

epub_dir = os.path.join(os.path.dirname(__file__), "tests", "golden", "epub")
out_epub = os.path.join(epub_dir, "single_file_anchors.epub")

para = ("This is ordinary body text that is deliberately written to be longer "
        "than sixteen words so the detector treats it as prose and not a heading.")

chapters = [("ch1", "Chapter 1. The Beginning"),
            ("ch2", "Chapter 2. The Middle"),
            ("ch3", "Chapter 3. The End")]

body = ['<h1 id="title">A Synthetic Single-File Book</h1>',
        f"<p>{para}</p>"]
for cid, title in chapters:
    body.append(f'<h2 id="{cid}">{title}</h2>')
    body += [f"<p>{para}</p>", f"<p>{para}</p>", f"<p>{para}</p>"]

xhtml = ('<?xml version="1.0" encoding="utf-8"?>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>'
         '</head><body>\n' + "\n".join(body) + "\n</body></html>")

book = epub.EpubBook()
book.set_identifier("single-file-anchors-001")
book.set_title("A Synthetic Single-File Book")
book.set_language("en")
item = epub.EpubHtml(title="Book", file_name="content.xhtml", lang="en")
item.content = xhtml.encode("utf-8")
book.add_item(item)
book.toc = tuple(epub.Link(f"content.xhtml#{cid}", title, cid)
                 for cid, title in chapters)
book.add_item(epub.EpubNcx())
book.add_item(epub.EpubNav())
book.spine = ["nav", item]
epub.write_epub(out_epub, book, {})
print("Built", os.path.relpath(out_epub, os.path.dirname(__file__)))

plan = detect(get_adapter(out_epub).load(out_epub), level_filter="auto")
print("abstained:", plan.abstained, "chapters:", len(plan.chapters))
for c in plan.chapters:
    print(f"   [{c.start}-{c.end}] {c.level} {c.title!r}")

expected = {
    "format": "epub",
    "source": "Synthetic single-file EPUB (anchor-delimited chapters) — F1 regression",
    "abstained": plan.abstained,
    "chapter_count": len(plan.chapters),
    "titles": [c.title for c in plan.chapters],
    "note": "Single XHTML doc; chapters resolved purely via nav href anchors "
            "(content.xhtml#chN -> block index). Proves F1 anchor resolution.",
}
with open(os.path.join(epub_dir, "single_file_anchors.expected.json"),
          "w", encoding="utf-8") as fh:
    json.dump(expected, fh, ensure_ascii=False, indent=2)
print("Locked single_file_anchors.expected.json")
