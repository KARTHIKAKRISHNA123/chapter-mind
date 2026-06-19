# Contributing

Thanks for helping improve universal-book-splitter.

## Dev setup

```bash
pip install -e ".[all,dev]"
```

## Run the checks

```bash
pytest                       # full suite
pytest -m regression         # golden chapter-count / title stability
pytest -m "not performance"  # what release.yml runs
ruff check .                 # lint
ruff format --check .        # format
mypy book_splitter           # types
```

## Adding a golden book (the safety net)

The golden corpus is what makes every other change safe: if a refactor or a
hardening tweak drifts detection, the regression suite fails loudly. Adding a
book takes **two files and zero code**:

1. Drop the book into `tests/golden_books/<name>.<format>` (e.g.
   `my_book.docx`). **Only public-domain or synthetic books** — never commit
   copyrighted texts.
2. Add `tests/golden_books/<name>.expected.json`:

   ```json
   { "format": "docx", "abstained": false, "chapter_count": 6, "titles": ["..."] }
   ```

   Generate the expected values by running detection once and locking whatever
   the engine currently produces — that becomes the regression baseline.

`tests/regression/test_golden_books.py` auto-discovers every `*.expected.json`.

## Invariants nobody may break

- **DOCX output is byte-identical.** The DOCX writer clones the original ZIP and
  rewrites only the body; it must never reconstruct the document. The
  byte-equality regression test guards this — keep it green.
- **The detection engine is format-blind.** Nothing under `detector.py`,
  `signals.py`, `scoring.py`, `hierarchy.py`, or `decision_engine.py` may read
  `Block.source` or import `lxml` / `ebooklib`. Format knowledge lives only in
  adapters.
- **Core stays CLI-free.** `detect`, `get_adapter`, and the adapters must import
  and run without `typer` / `rich` installed. Keep those imports inside `cli.py`.
- **Untrusted input is validated first.** All ZIP input flows through
  `ingestion.sniff_format` (content sniff) and `safety.assert_safe_archive`
  (bomb / zip-slip guard) before any adapter touches it.

## Versioning

Stay on `0.x` while the public surface (CLI flags and the
`detect` / `get_adapter` signatures) can still change. Cut `1.0.0` once that
surface is frozen. After 1.0: patch = fix, minor = back-compatible feature,
major = breaking change. Pushing a `vX.Y.Z` tag triggers `release.yml`.

## PR checklist

- [ ] Tests added/updated; `pytest` green (incl. `-m regression`).
- [ ] `ruff check .` and `mypy book_splitter` clean.
- [ ] DOCX byte-equality test still passes.
- [ ] Public-facing changes noted in the README if relevant.
