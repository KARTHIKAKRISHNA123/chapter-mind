# Segmentation Policy — Splitable vs Navigational Content

> Formal definition of how `book_splitter` treats front matter, body content,
> and back matter. Resolves PS3 ("Splitable content vs navigational content is
> undefined").

## Decision

**Lossless split + explicit role.** Every detected division is kept in the
output (`ChapterPlan.chapters`) — the splitter never silently drops content —
and each chapter carries an explicit `role`:

| role           | meaning                                            | examples |
|----------------|----------------------------------------------------|----------|
| `front_matter` | precedes the body; navigational or prelim. matter  | Front Matter, title page, Contents, Preface, Foreword, Introduction, Prologue |
| `body`         | actual content divisions to split on               | Chapter, Part, Section, Story, Letter, Discourse, named chapters |
| `back_matter`  | follows the body; appendices and boilerplate       | Appendix, Afterword, Conclusion, Epilogue, License, Colophon, Index |

This is **Option A (keep everything) plus labels**, rather than Option B (drop
navigational entries). Dropping was rejected because (a) it risks data loss on
misclassification, and (b) downstream consumers differ on whether they want the
TOC/contents page. Labels make the distinction unambiguous without losing data.

## Consuming the policy

```python
plan = detect(adapter.load(path))

plan.chapters        # everything, in reading order (lossless)
plan.body_chapters   # content-only view  -> use this for a chapter split
plan.front_matter    # title page, contents, preface, ...
plan.back_matter     # license, colophon, appendix, ...
plan.diagnostics["roles"]   # {"front_matter": 3, "body": 20, "back_matter": 1}
```

A translation/export pipeline that wants "real chapters only" iterates
`plan.body_chapters`. A faithful full-book exporter iterates `plan.chapters`.

## How role is assigned (deterministic, language-agnostic)

Precedence, first match wins, per chapter:

1. The synthetic leading **"Front Matter"** span → `front_matter`.
2. Title contains a **back-matter marker** (`license`/`licence`/`copyright`/
   `colophon`/`gutenberg`/`transcriber`/`errata`/`end of the project`) →
   `back_matter`.
3. `div_type` ∈ {`appendix`, `afterword`, `conclusion`, `epilogue`} →
   `back_matter`.
4. `div_type` ∈ {`contents`, `preface`, `foreword`, `introduction`, `prologue`}
   → `front_matter`. *(These `div_type`s come from the multilingual division
   vocabulary, so this step is language-agnostic across EN/FR/DE + Indic.)*
5. Title equals the **document title** and no body division has appeared yet →
   `front_matter` (the **title page**, identified via the book's own metadata —
   works in any language).
6. Otherwise → `body`.

### Worked examples (public-domain fixtures)

```
Robinson Crusoe (pg521): 24 total -> 3 front | 20 body | 1 back
  front_matter : Front Matter, "The Life and Adventures of Robinson Crusoe", Contents
  body         : CHAPTER I … CHAPTER XX
  back_matter  : THE FULL PROJECT GUTENBERG™ LICENSE

Utopia (pg2130): 13 total -> 3 front | 9 body | 1 back
  front_matter : Front Matter, "Utopia", Contents
  body         : the nine discourses ("OF THEIR …")
  back_matter  : THE FULL PROJECT GUTENBERG™ LICENSE
```

## Known limitations

- Back-matter markers are English-centric (Project Gutenberg framing). Non-English
  license/colophon pages may remain labeled `body` until their markers are added.
- A named back-matter page with no marker and no recognized `div_type` (and not
  matching a vocabulary type) stays `body`. Position-based refinement (trailing
  non-progression divisions → `back_matter`) is a candidate future enhancement.
- The title-page rule requires the source to expose a document title
  (EPUB `dc:title`; DOCX core properties). Without it, a title page is treated
  as `body`.

## Tests

`tests/unit/test_segmentation_roles.py` locks the policy: body counts, exclusion
of Contents/License from `body_chapters`, lossless `front+body+back == total`,
and title-page exclusion. The golden harness continues to assert total
`chapter_count` and `titles` (roles are additive, so existing locks are stable).
