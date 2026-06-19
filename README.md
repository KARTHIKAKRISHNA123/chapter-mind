# universal-book-splitter

Deterministic, format-agnostic book → chapter splitter. Give it a `.docx` or
`.epub`, get one file per detected chapter — with formatting preserved.

The detection engine is **format-blind**: a layered system of TOC intelligence,
heading styles, and visual/text signals decides where chapters begin, while
format-specific adapters handle reading and writing. DOCX output is byte-loss-free;
EPUB output is faithfully reconstructed (see [fidelity contract](#supported-formats--fidelity)).

## Install

```bash
pip install universal-book-splitter           # DOCX only (core)
pip install "universal-book-splitter[epub]"   # + EPUB support
pip install "universal-book-splitter[all]"    # everything
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

## Supported formats & fidelity

| Format | Read | Write | Fidelity |
|---|---|---|---|
| DOCX | yes | yes | **Byte-loss-free** — clones the original ZIP and rewrites only the body, so every chapter keeps styles, media, headers/footers, fonts and numbering. |
| EPUB | yes | yes (experimental) | **Semantically faithful, not byte-identical** — each chapter is reconstructed as a valid EPUB with its referenced CSS/images/fonts carried over. |

Input is validated by content, never by extension: a renamed `.doc`, a
password-protected docx, a PDF, a truncated download, or a decompression bomb is
rejected with a precise, typed error before any parsing happens.

## How it works

See [ARCHITECTURE.md](ARCHITECTURE.md) for the pipeline and the format-blind-engine
seam. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT.
