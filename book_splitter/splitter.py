"""
splitter.py
===========
THE EMITTER.

Given a DocxPackage and a ChapterPlan, write one DOCX per chapter by calling
DocxPackage.clone_with_blocks() on each chapter's slice of body children. Every
output is a full, valid package with all original parts intact (styles, media,
headers/footers, theme, fonts, numbering) -- only the body content differs.
"""

from __future__ import annotations
import os
import re


def _slug(title: str, fallback: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title).strip().lower()
    s = re.sub(r"[\s]+", "_", s)
    return (s[:48] or fallback)


def split(package, plan, out_dir: str) -> list[dict]:
    """Write chapter files into out_dir; return a manifest list."""
    os.makedirs(out_dir, exist_ok=True)
    children = package.block_children()
    manifest = []

    for i, ch in enumerate(plan.chapters):
        blocks = children[ch.start:ch.end]
        # Skip an empty slice (can happen if two boundaries are adjacent).
        if not any(b.tag.endswith("}p") or b.tag.endswith("}tbl") for b in blocks):
            if not blocks:
                continue
        data = package.clone_with_blocks(blocks)
        name = f"{i:02d}_{_slug(ch.title, f'chapter_{i}')}.docx"
        path = os.path.join(out_dir, name)
        package.write(path, data)
        manifest.append({
            "file": name,
            "title": ch.title,
            "level": ch.level,
            "confidence": ch.confidence,
            "blocks": [ch.start, ch.end],
            "signals": ch.fired,
        })
    return manifest