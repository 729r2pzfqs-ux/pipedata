# PipeData.org

Static site publishing pipe, flange and buttweld fitting specifications from the
ASME B16 and B36 standards. Imperial primary, metric alongside.

**Live:** https://pipedata.org · **Contact:** info@pipedata.org

## Coverage

| Standard | What | Pages |
| --- | --- | --- |
| ASME B36.10M | Pipe dimensions, NPS 1/8 – NPS 36, 14 schedules | 30 size + 14 schedule |
| ASME B16.5 | Flanges, 6 types × 7 classes, NPS 1/2 – NPS 24 | 6 type + 42 class |
| ASME B16.47 | Large flanges, Series A and B, NPS 26 – NPS 60 | 4 |
| ASME B16.9 | Buttweld fittings — elbows, tee, reducers, cap | 7 + index |
| Reference | NPS/DN, schedules, P-T ratings, materials, bolting, faces | 11 + index |

122 pages total.

### Data accuracy

`data/flanges_b1647.yaml` deliberately tabulates **Series A Class 150 only**. The
other B16.47 series/class combinations are described but not tabulated — large
flange data varies between series in ways that make a transcription error
expensive, and the site says so on the page rather than filling the gap. Do not
add tables there from a secondary source.

Derived figures (bore, weight, flow area, water volume) are calculated by
`generate.py` from the published dimensions, and the formula is stated on the
page wherever a value is derived rather than tabulated.

## Layout

```
data/       YAML spec tables — the only place to edit data
static/     CSS, JS, favicon source; copied verbatim into docs/
generate.py Builds docs/ from data/ + static/
make_assets.py  Rasterises static/favicon.svg into PNG/ICO/OG card
docs/       Build output. GitHub Pages serves from here. Never hand-edit.
```

## Build

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python generate.py
```

`generate.py` wipes and rewrites `docs/` on every run, so any hand-edit there is
lost. Change `data/` or the page functions in `generate.py` instead.

After editing `static/favicon.svg`, re-run `make_assets.py` to regenerate the
raster icons and the Open Graph card.

### Build gates

The build fails rather than shipping bad SEO:

- `audit_descriptions()` — every indexed page needs a unique meta description of
  120–160 characters. `fit_desc()` picks the richest tail that fits the band.
- `audit_titles()` — every indexed page needs a unique `<title>` of ≤60
  characters. `fit_title()` drops optional tails from the right until it fits.

Both run over everything `head()` emitted, so a new page type is covered
automatically without being registered anywhere.

## Analytics

`GA_ID` in `generate.py` is empty, so no analytics snippet is emitted at all. Set
it to a real `G-…` measurement ID to switch it on; the privacy page text changes
with it automatically. A placeholder ID is deliberately not shipped — it would
cost every visitor a request and collect nothing.

## Conventions

- Imperial is the primary unit everywhere. `dual()` renders inches with the
  millimetre value stacked beneath in grey; `inch_mm()` is the inline prose form.
- Every dimension page carries the reference-only disclaimer in the footer.
- Tables live inside `.table-scroll` so wide tables scroll themselves and the
  page body never scrolls horizontally.
- Print styles drop the chrome and un-scroll the tables — these pages get printed
  and taken into the field.
