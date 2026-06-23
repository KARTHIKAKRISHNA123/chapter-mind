import os, sys, json, glob
sys.path.insert(0, os.path.dirname(__file__))
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

root = os.path.dirname(__file__)
specs = []
specs += glob.glob(os.path.join(root, "tests", "golden_books", "*.expected.json"))
specs += glob.glob(os.path.join(root, "tests", "golden", "*", "*.expected.json"))

passed = failed = skipped = 0
for spec in sorted(specs):
    meta = json.loads(open(spec, encoding="utf-8").read())
    stem = os.path.basename(spec)[: -len(".expected.json")]
    book = os.path.join(os.path.dirname(spec), f"{stem}.{meta['format']}")
    label = os.path.relpath(book, root)
    if not os.path.exists(book):
        print("SKIP", label, "(missing)"); skipped += 1; continue
    plan = detect(get_adapter(book).load(book), level_filter="auto")
    got_abs, got_n = plan.abstained, len(plan.chapters)
    exp_abs, exp_n = meta.get("abstained", False), meta["chapter_count"]
    ok = (got_abs == exp_abs) and (got_n == exp_n)
    if ok and "titles" in meta:
        ok = [c.title for c in plan.chapters] == meta["titles"]
    if ok:
        print("PASS", label, f"(n={got_n}, abstained={got_abs})"); passed += 1
    else:
        print("FAIL", label, f"got n={got_n} abstained={got_abs} | "
              f"expected n={exp_n} abstained={exp_abs}"); failed += 1

print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
