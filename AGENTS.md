# Agent instructions

- Use `uv run` for all Python. Dependencies are in `pyproject.toml`.
- `fetch_data.py` writes `data/`. `model.py` reads only `data/` and writes `output/` and
  `site/data.json`. Commit `site/data.json` after every model change. The Pages workflow does not build it.
- Rerun `model.py` after any change and check `output/results.md` and both charts.
- `site/` is a static page with no build step and no dependencies. Charts are plain SVG in
  `site/app.js`, with colors from CSS variables in `site/style.css`.
- Keep all prices in real `BASE_YEAR` dollars. Change `BASE_YEAR` in `model.py` when a new full
  year of Zillow data exists.
