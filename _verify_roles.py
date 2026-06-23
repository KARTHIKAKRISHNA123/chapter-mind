import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

base = os.path.join(os.path.dirname(__file__), "tests", "golden", "epub")
for name in ("pg521.epub", "pg2130.epub", "single_file_anchors.epub"):
    path = os.path.join(base, name)
    plan = detect(get_adapter(path).load(path), level_filter="auto")
    print("====", name, "| total:", len(plan.chapters),
          "| body:", len(plan.body_chapters),
          "| front:", len(plan.front_matter),
          "| back:", len(plan.back_matter))
    for c in plan.chapters:
        print(f"   {c.role:<13} {c.div_type or c.level:<10} {c.title[:55]}")
    print()
