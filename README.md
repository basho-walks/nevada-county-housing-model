# Nevada County housing model

Estimates how Nevada County, California, population growth changes typical home values in
Grass Valley and Nevada City, and projects 10-year population scenarios.

**Website:** https://basho-walks.github.io/nevada-county-housing-model/ (interactive scenario
explorer, history, county comparison, and method).

## Method

1. **County panel (stage 1).** Annual real home-price growth (FHFA county house price index,
   CPI-deflated) for 55 California counties, 2001-2024, regressed on population growth, last
   year's price growth, real per-capita income growth, and last year's permits per 1,000
   residents. County fixed effects remove fixed local differences. Year fixed effects remove
   statewide and national drivers such as mortgage rates. A single county has too few years to
   measure the population effect, so the panel supplies the elasticity.
2. **City pass-through (stage 2).** Each city's real Zillow Home Value Index growth is regressed
   on Nevada County real price growth, 2002-2025.
3. **Scenarios.** Constant population growth paths for 2026-2035. Results show the value change
   caused by population alone, against a flat-population path. They are not market forecasts.

## Limits

- Population and prices affect each other. The same-year model can overstate the effect. The
  lagged-population model gives roughly half the effect and is the conservative bound.
- The effect is assumed symmetric. Durable housing usually makes declines hit prices harder.
- Census has no 2010-2020 intercensal county file. `fetch_data.py` spreads the 2020 estimate
  gap linearly over 2011-2019.

## Run

```sh
uv run python fetch_data.py   # downloads to data/ (Zillow file is ~95 MB, streamed)
uv run python model.py        # writes output/ and site/data.json
python3 -m http.server -d site 8000   # preview the website at http://localhost:8000
```

Pushing changes under `site/` to `main` deploys the website through GitHub Actions.

Sources: FRED (FHFA HPI, BEA per-capita income, Census building permits, 30-year mortgage rate,
CPI-U), Census population estimates, Zillow ZHVI (mid-tier, all homes, smoothed, seasonally adjusted).
