"""
verify.py
=========
PRESERVATION VERIFIER (and lightweight benchmark helper).

Proves the central guarantee of this engine: that every chapter file is the
original package with only word/document.xml trimmed. For each chapter it
checks that every non-document part is byte-for-byte identical to the source,
that document.xml is well-formed, and that the page-settings sectPr survived.

Usage:
    python -m book_splitter.verify original.docx chapters_dir/
"""

from __future__ import annotations
import sys
import os
import glob
import zipfile
import hashlib
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _parts(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        return names, {n: hashlib.sha1(z.read(n)).hexdigest() for n in names}


def verify(original: str, chapters_dir: str) -> bool:
    src_names, src_hashes = _parts(original)
    src_set = set(src_names)
    files = sorted(glob.glob(os.path.join(chapters_dir, "*.docx")))
    ok = True
    for f in files:
        names, hashes = _parts(f)
        nset = set(names)
        # 1. same set of parts
        same_parts = (nset == src_set)
        # 2. every non-document part byte-identical
        preserved = all(hashes[k] == src_hashes[k]
                        for k in nset if k != "word/document.xml")
        # 3. document.xml well-formed + has trailing sectPr (page settings)
        with zipfile.ZipFile(f) as z:
            doc = etree.fromstring(z.read("word/document.xml"))
        body = doc.find(W + "body")
        kids = list(body)
        has_sect = bool(kids) and kids[-1].tag == W + "sectPr"

        good = same_parts and preserved and has_sect
        ok = ok and good
        status = "OK " if good else "FAIL"
        print(f"[{status}] {os.path.basename(f):48} "
              f"parts={same_parts} bytes_preserved={preserved} sectPr={has_sect}")
    print(f"\n{'ALL CHAPTERS PRESERVED' if ok else 'PRESERVATION FAILURES DETECTED'}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m book_splitter.verify original.docx chapters_dir/")
        sys.exit(2)
    sys.exit(0 if verify(sys.argv[1], sys.argv[2]) else 1)