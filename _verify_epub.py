import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

base = os.path.join(os.path.dirname(__file__), "tests", "golden", "epub")
for name in ("pg521.epub", "pg2130.epub"):
    path = os.path.join(base, name)
    doc = get_adapter(path).load(path)
    plan = detect(doc, level_filter="auto")
    print("====", name)
    print("blocks:", len(doc.blocks), "spine_starts:", len(doc.spine_starts),
          "toc_entries:", (len(doc.toc_entries) if doc.toc_entries else None))
    print("abstained:", plan.abstained, "chapters:", len(plan.chapters))
    print("method:", plan.diagnostics.get("decision", {}).get("structure", {}).get("split_level")
          if isinstance(plan.diagnostics.get("decision"), dict) else None,
          "| toc_authoritative:", plan.diagnostics.get("toc_authoritative"))
    for c in plan.chapters[:30]:
        print(f"   [{c.start:>4}-{c.end:>4}] {c.level:<8} conf={c.confidence}  {c.title[:60]}")
    print()
