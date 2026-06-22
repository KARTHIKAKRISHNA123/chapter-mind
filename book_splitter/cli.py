"""
cli.py
======
COMMAND-LINE INTERFACE (argparse).

Examples
--------
    # See the detection plan without writing anything
    python -m book_splitter book.docx --dry-run

    # Split into per-chapter DOCX files
    python -m book_splitter book.docx --out out_dir

    # Force a specific split level, save report
    python -m book_splitter book.docx --out out_dir --level chapter \\
        --report out_dir/report.json
"""

from __future__ import annotations
import argparse
import glob as glob_mod
import json
import os
import sys
import logging

from .adapters.registry import get_adapter
from .detector import detect
from .ingestion import IngestionError
from .safety import UnsafeArchiveError
from .review import triage
from .scoring import confidence_band


def _build_parser():
    p = argparse.ArgumentParser(
        prog="book_splitter",
        description="Universal Book Chapter Splitter: split any DOCX/EPUB into "
                    "one file per chapter while preserving formatting.")
    p.add_argument("inputs", nargs="+",
                   help="Input .docx/.epub file(s) or glob pattern(s).")
    p.add_argument("--out", default="chapters_out",
                   help="Output directory (a sub-folder per book is created).")
    p.add_argument("--level", choices=["auto", "part", "chapter", "section"],
                   default="auto", help="Granularity of the split.")
    p.add_argument("--report", default=None,
                   help="Write a JSON detection+manifest report to this path.")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect and print the plan; do not write files.")
    p.add_argument("--pattern", default=None,
                   help="Filename template, e.g. '{index:02d}_{title}'. "
                        "Placeholders: {index}, {title}. Default keeps NN_slug.")
    p.add_argument("--min-confidence", type=float, default=0.5,
                   help="Warn (and, if interactive, confirm) before writing when "
                        "any boundary's confidence is below this value.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the low-confidence confirmation prompt.")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def _expand(patterns):
    files = []
    for pat in patterns:
        hits = glob_mod.glob(pat)
        files.extend(hits if hits else [pat])
    SUPPORTED = (".docx", ".epub")
    return [
        f for f in files
        if f.lower().endswith(SUPPORTED) and os.path.isfile(f)
    ]


def process_one(path, args, log) -> dict:
    log.info("Loading %s", path)

    try:
        adapter = get_adapter(path)
    except (IngestionError, UnsafeArchiveError, ValueError) as exc:
        # Typed ingestion failure (corrupt/mislabeled/unsupported) or legacy
        # ValueError — surface the reason, never a raw traceback.
        log.error("Cannot process this file: %s", exc)
        return {"book": os.path.splitext(os.path.basename(path))[0],
                "source": path, "error": str(exc)}

    doc = adapter.load(path)
    plan = detect(doc, level_filter=args.level)

    base = os.path.splitext(os.path.basename(path))[0]
    print(f"\n=== {base} ===")
    print(f"body_size={plan.diagnostics.get('body_size')}  "
          f"heading_band={plan.diagnostics.get('heading_band')}  "
          f"toc_matched={plan.diagnostics.get('toc_matched')}  "
          f"candidates={plan.diagnostics.get('merged_candidates')}")
    if plan.abstained:
        print(f"ABSTAINED: {plan.reason}")
    print(f"{'#':>2}  {'level':7} {'conf':>5}  {'band':9}  title")
    for i, ch in enumerate(plan.chapters):
        print(f"{i:>2}  {ch.level:7} {ch.confidence:>5.2f}  "
              f"{confidence_band(ch.confidence):9}  {ch.title[:50]}")

    manifest = []
    skipped = None
    if not args.dry_run and not plan.abstained:
        report = triage(plan, threshold=args.min_confidence)
        proceed = True
        if not report.is_clean:
            print(f"WARNING: {report.weak_count} of {report.total} boundaries are "
                  f"below confidence {report.threshold:.2f}:")
            for ch in report.weak:
                print(f"   start={ch.start:>5}  conf={ch.confidence:>4.2f}  "
                      f"{ch.title[:60]}")
            if args.yes:
                print("   --yes set; proceeding.")
            elif not sys.stdin.isatty():
                print("   Low-confidence split in a non-interactive shell; "
                      "pass --yes to force. Skipping.")
                proceed = False
                skipped = "low_confidence_non_interactive"
            else:
                reply = input("   Split anyway? [y/N] ").strip().lower()
                if reply not in ("y", "yes"):
                    proceed = False
                    skipped = "user_declined_low_confidence"

        if proceed:
            out_dir = os.path.join(args.out, base)
            manifest = adapter.make_writer().write(plan, out_dir, args.pattern)
            print(f"-> wrote {len(manifest)} files to {out_dir}")

    return {
        "book": base, "source": path,
        "abstained": plan.abstained, "reason": plan.reason,
        "skipped": skipped,
        "diagnostics": plan.diagnostics,
        "chapters": [{"title": c.title, "level": c.level,
                      "confidence": c.confidence, "blocks": [c.start, c.end]}
                     for c in plan.chapters],
        "manifest": manifest,
    }


def main(argv=None):
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")
    log = logging.getLogger("book_splitter")

    files = _expand(args.inputs)
    if not files:
        log.error("No supported inputs matched: %s", args.inputs)
        return 2

    reports = [process_one(f, args, log) for f in files]

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w") as fh:
            json.dump(reports, fh, indent=2)
        print(f"\nReport written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
