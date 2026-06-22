# book-splitter

> **Status: v0.9.0 — validated on public-domain books; API stable, not yet production-hardened.**

Deterministic, format-agnostic book → chapter splitter. Give it a `.docx` or
`.epub`, get one file per detected chapter — with formatting preserved.

The detection engine is **format-blind**: a layered system of TOC intelligence,
heading styles, and visual/text signals decides where chapters begin, while
format-specific adapters handle reading and writing.

## Install

```bash
pip install book-splitter
```

## Usage

```bash
# Split a book into one file per chapter
book-splitter book.docx --out out_dir

# Inspect the detected plan without writing anything
book-splitter book.docx --dry-run

# Custom output filenames (placeholders: {index}, {title})
book-splitter book.docx --out out_dir --pattern "{index:02d}_{title}"

# Force a split level and save a JSON report
book-splitter book.docx --out out_dir --level chapter --report out_dir/report.json
```

Equivalent module form: `python -m book_splitter book.docx --out out_dir`.

Useful flags:

- `--level {auto,part,chapter,section}` — granularity of the split (default `auto`).
- `--pattern` — filename template; omit it to keep the default `NN_slug` names.
- `--min-confidence` / `--yes` — warn (and, when interactive, confirm) before
  writing if any boundary is low-confidence; `--yes` skips the prompt.
- `--dry-run` — detect and print the plan only.

## Validated formats

Each entry in the table below is covered by a golden-corpus regression test and a
preservation-fidelity test. "Validated" means the split was confirmed correct on
at least one public-domain book; "Experimental" means the code runs but has no
locked golden baseline yet.

| Format | Read | Write | Fidelity | Languages validated |
|--------|------|-------|----------|---------------------|
| DOCX | ✅ | ✅ | **Byte-loss-free** — clones the original ZIP, rewrites only the body; styles, media, headers/footers, fonts and numbering are preserved bit-for-bit. | English, Assamese |
| EPUB | ✅ | ⚠ Experimental | **Semantically faithful, not byte-identical** — each chapter is reconstructed as a valid EPUB with its referenced CSS/images/fonts carried over. | — (abstention tested) |

Input is validated by content, never by extension: a renamed `.doc`, a
password-protected file, a PDF, a truncated download, or a decompression bomb is
rejected with a precise, typed error before any parsing happens.

### What "byte-loss-free" means in practice

Every output chapter passes three automated checks (run in CI via `pytest -m fidelity`):

1. **Same ZIP parts** — no parts added or dropped relative to the original.
2. **Non-body parts byte-identical** — styles, images, theme, fonts, headers, footers, numbering unchanged.
3. **Well-formed body** — `word/document.xml` parses as valid XML and ends with a `<w:sectPr>` (page-layout element).

### Abstention

When the engine cannot confidently locate chapter boundaries it writes nothing and
returns `abstained=True`. This is intentional safe-fail behaviour — a partial split
is worse than no split. You can inspect the reason with `--dry-run`.

## How it works

The pipeline:

```
get_adapter(path)  →  adapter.load(path)  →  detect(doc)  →  adapter.make_writer().write(plan, out_dir)
```

Detection is a two-phase process:

1. **Signal extraction** — TOC anchors, heading styles, paragraph text, numbering, visual rules.
2. **Adaptive thresholding** — gap analysis over scored boundary candidates; uniform-quality cluster guard prevents false abstention when all candidates score similarly high.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full pipeline detail.

## Running tests

```bash
# All tests
pytest

# Golden-corpus regression only (fast, no disk I/O)
pytest -m regression

# Preservation-fidelity tests (require golden DOCX files)
pytest -m fidelity
```

## License

MIT.
