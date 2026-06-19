"""
naming.py
=========
Render safe output filenames from a user-supplied pattern.

No format coupling — works for any writer (DOCX, EPUB, future PDF). The CLI's
``--pattern`` flows through to the adapters' writers; when no pattern is given
the writers keep their original ``NN_slug`` naming so existing output stays
byte-identical.

Pattern placeholders:
  * ``{index}``  — the chapter ordinal (supports format specs, e.g. ``{index:03d}``)
  * ``{title}``  — the sanitized chapter title

Example:  ``"{index:02d}_{title}"`` -> ``"03_the_man_who_desired_gold.docx"``
"""
from __future__ import annotations

import re

# Characters illegal in Windows filenames (superset of POSIX-illegal), plus the
# separators we don't want inside a single name component.
_INVALID = '<>:"/\\|?*'
_CONTROL = re.compile(r"[\x00-\x1f]")


def sanitize(title: str, max_len: int = 60) -> str:
    """Turn an arbitrary title into a safe, lower-ish filename stem.

    Collapses runs of whitespace/underscores, strips illegal and control
    characters, trims to *max_len*, and never returns an empty string.
    """
    title = _CONTROL.sub("", title or "")
    for ch in _INVALID:
        title = title.replace(ch, "_")
    title = re.sub(r"[\s_]+", "_", title).strip("_.")
    title = title[:max_len].rstrip("_.")
    return title or "untitled"


def render_filename(pattern: str, *, ordinal: int, title: str | None, ext: str) -> str:
    """Render a filename from *pattern*.

    *ext* is the desired extension *with* leading dot (e.g. ``".docx"``); it is
    appended only if *pattern* did not already produce it.
    """
    name = pattern.format(
        index=ordinal,
        title=sanitize(title or f"chapter_{ordinal}"),
    )
    if not name.lower().endswith(ext.lower()):
        name += ext
    return name
