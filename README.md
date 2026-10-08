# OpenAI Math manuscript index

A static, searchable index of the [openai/math](https://github.com/openai/math)
manuscript collection, sorted by subject. `openai/math` is included as a
submodule at `math/`.

No paper is read or classified by hand or by a model: `scripts/build.py` parses
the files upstream already ships.

| Source | Provides |
|---|---|
| `overview.tex` | the discipline (17 total) of each result family |
| `CONTENTS.md` | family summaries, paper titles, PDF paths, abstracts |
| `lean/formalization.yaml` | which papers have a formalized main result |
| `README.md` | which families have reasoning summaries |
| `preprints/*/README.md` | BibTeX |
| `preprints/*/*.pdf` | page counts |

Page counts come from a small stdlib PDF parser. The PDFs total ~400 MB, so
counts are cached in `data/pdf-pages.json` keyed by git blob hash; the build
reads only PDFs missing from the cache (in CI, `git cat-file` lazily fetches
just those blobs). Commit the updated cache after bumping the submodule.

## Build locally

```sh
git submodule update --init      # or see the workflow for a 4 MB sparse fetch
python3 scripts/build.py         # writes _site/
python3 -m http.server -d _site
```

## Layout

- `scripts/build.py`: parser and HTML generator (Python stdlib only)
- `site/`: static assets copied into the output (`style.css`, `app.js`, favicon)
- `.github/workflows/pages.yml`: builds and deploys to GitHub Pages on push

Output: `index.html` (subjects), one page per subject (papers grouped by
family), `all.html` (search/filter everything), and `papers.json`.

## Updating

```sh
git submodule update --remote math
python3 scripts/build.py          # refreshes data/pdf-pages.json
git add math data && git commit -m "Bump openai/math"
git push
```
