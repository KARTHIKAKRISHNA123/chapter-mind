"""
lock_golden.py
==============
Run once per book to generate (or regenerate) its regression baseline.

Usage
-----
    python lock_golden.py "The Richest Man in Babylon.docx" richest_man_babylon
    python lock_golden.py "Deep Work.docx" deep_work
    python lock_golden.py "Village Reformation (Assamese).docx" village_reformation_assamese
    python lock_golden.py "20260636.epub" railway_state_ownership

The book is copied to tests/golden_books/<stem>.<ext> and the detected plan
is written to tests/golden_books/<stem>.expected.json.

IMPORTANT: only commit PUBLIC-DOMAIN or synthetic books. Never commit
copyrighted content. The existing richest_man_babylon.docx is a synthetic
sample modelled on the public-domain text.

After locking, verify the printout looks correct, then run:
    pytest -m regression
"""

from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect


def main(src: str, stem: str) -> None:
    src_path = Path(src)
    if not src_path.exists():
        print(f"ERROR: source file not found: {src_path}", file=sys.stderr)
        sys.exit(1)

    fmt = src_path.suffix.lstrip(".").lower()
    if fmt not in ("docx", "epub"):
        print(f"ERROR: unsupported format '{fmt}' (must be docx or epub)",
              file=sys.stderr)
        sys.exit(1)

    dst_dir = Path("tests/golden_books")
    dst_dir.mkdir(parents=True, exist_ok=True)

    book = dst_dir / f"{stem}.{fmt}"
    shutil.copyfile(src_path, book)
    print(f"Copied  -> {book}")

    adapter = get_adapter(str(book))
    plan = detect(adapter.load(str(book)), level_filter="auto")

    expected = {
        "format": fmt,
        "source": "public-domain",
        "abstained": plan.abstained,
        "chapter_count": len(plan.chapters),
        "titles": [c.title for c in plan.chapters],
    }

    out = dst_dir / f"{stem}.expected.json"
    out.write_text(json.dumps(expected, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Locked  -> {out}")
    print()
    print(json.dumps(expected, ensure_ascii=False, indent=2))
    print(f"\n✓ {len(plan.chapters)} chapters detected"
          + (" (ABSTAINED)" if plan.abstained else ""))
    if plan.abstained:
        print(f"  reason: {plan.reason}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
