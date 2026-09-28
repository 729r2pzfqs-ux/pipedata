# PipeData.org

Static site publishing pipe, flange and buttweld fitting specifications from the
ASME B16 and B36 standards. Imperial primary, metric alongside.

**Live:** https://pipedata.org · **Contact:** info@pipedata.org

## Coverage

| Standard | What | Pages |
| --- | --- | --- |
| ASME B36.10M | Pipe dimensions, NPS 1/8 – NPS 36, 14 schedules | 34 size + 14 schedule + 286 size×schedule |
| ASME B36.19M | Stainless S-schedules — 5S, 10S, 40S, 80S | 4 + reference |
| ASME B16.5 | Flanges, 6 types × 7 classes, NPS 1/2 – NPS 24 | 6 type + 42 class + 20 size + 264 detail |
| ASME B16.47 | Large flanges, Series A and B, NPS 26 – NPS 60 | 4 |
| ASME B16.9 | Buttweld fittings — elbows, tees, reducers, cap | 8 + index |
| ASME A13.1 | Pipe marker colour scheme and legend sizing | 1 |
| Reference | NPS/DN, schedules, P-T ratings, materials, bolting, faces | 12 + index |
| Compare | Schedule, flange type, flange class, material and fitting pairs | 24 + index |
| Guides | Sizing, wall thickness, test pressure, torque, material choice | 10 + index |

745 pages are built. 174 are indexed and in the sitemap; the other 571 are
single-row extracts kept live as `noindex` (see below).

### Page granularity: what is indexed and what is not

Google AdSense rejected the site for "low value content" in September 2026. The
cause was the page mix: several hundred pages that were one row of a table with
the same paragraphs round it. The data now lives on the parent pages, and the
per-row pages are kept only so that existing links do not break.

| Indexed parent | Holds | `noindex` children |
| --- | --- | --- |
| `/pipes/nps-4/` | every schedule, flow and pressure capacity, the flanges and fittings for the size | 286 `/pipes/nps-4/schedule-40/` pages |
| `/flanges/weld-neck/class-300/` | every size, bolting geometry, weld neck bore by schedule or blind weight and end load | 264 `/flanges/<type>/class-<n>/nps-<x>/` pages |
| `/pipes/nps-6/#flanges` | every class in one size | 20 `/flanges/nps-6/` pages |

Rules that keep it that way:

- A `noindex` page passes `noindex=True` to `page()`, opens with
  `folded_into()` pointing at its parent, and carries no FAQ markup.
- `sitemap()` is filtered against what `head()` registered, so a `noindex` page
  cannot get into the sitemap by being listed in `main()`.
- Indexed pages link to the parent anchor (`/pipes/nps-4/#schedule-40`,
  `/flanges/blind/class-150/#nps-6`), never to a `noindex` child. The rows carry
  the matching `id`.
- Do not add a new page type that is one row of an existing table. Add a column
  or a section to the parent.

### Prose on data pages

Each indexed data page explains its table. Statements of practice ("where this
size is used") are hand-written in `SIZE_NOTES`, `SCHEDULE_NOTES`, `CLASS_NOTES`,
`TYPE_BAND_NOTES` and `GROUP_NOTES`, and are worded as practice, not as
requirements of a standard. Every number in the surrounding text is computed
from `data/`. `READING` maps each guide and comparison to a one-line reason to
read it, and `reading()` puts the relevant ones on each data page.

NPS 4 1/2, 7, 9 and 11 are carried even though they are effectively obsolete —
B36.10M publishes them, and B16.5 and B16.9 do not, so their pages omit the
flange and fitting cross-references rather than inventing them.

### Generated claims, not asserted ones

`s_schedule_comparison()` computes where each B36.19M S-schedule agrees and
disagrees with its B36.10M counterpart by walking both data files, and the
stainless pages render that result as prose. An earlier hand-written version of
that claim was wrong (it said 5S and 10S are thinner than anything in B36.10M —
they are not; they match Schedule 5 and Schedule 10 in every published size).
Prefer computing this kind of cross-table claim over writing it out, so a data
edit cannot leave stale prose behind.

The comparison and guide pages follow the same rule, and building them turned up
four hand-written claims that the data contradicted:

- A pipe sizing example asserted the answer was NPS 6. Computed against the flow
  areas, the smallest Schedule 40 size meeting the duty is NPS 5. The example now
  selects the size from the data and notes when it lands on one many specs omit.
- The 45° elbow page claimed the tabulated centre-to-end ratio is tan 22.5° in
  every size. B16.9 publishes rounded figures, so it is nearer 0.417 from NPS 4
  up and reaches 0.587 at NPS 1. The page now derives both and says the table
  governs, not the formula.
- Schedule 40's NPS 24 wall was written as 0.687 in; B36.10M publishes 0.688.
- The Schedule 10 vs 40 page ran the comparison in one direction and described it
  in the other, calling Schedule 10 "89% lighter" when 89% is how much heavier
  Schedule 40 is. `sched_spread()` now returns both directions (`wt_avg` /
  `wt_rev`) so the framing and the arithmetic cannot disagree.

Note the shape of these: every one of them was a number a reasonable engineer
would accept on sight. The audits catch duplicate and over-long metadata, not
wrong arithmetic — so a numeric claim in prose should be computed from `data/`
wherever it possibly can be, and spot-checked against the built HTML when it
cannot.

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
diagrams.py Inline SVG figures, imported by generate.py
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

### Diagrams

`diagrams.py` draws every figure as inline SVG from coordinates; nothing is
traced or imported, and there are no image files to keep in step.

- **Schematic** figures (flange sections, fittings, the labelled pipe section)
  show which dimension a table column measures. They are not to scale and their
  captions say so.
- **To-scale** figures are drawn from `data/`: the pipe walls on the size and
  schedule pages, and the flange face on the class pages. They differ on every
  page and cannot disagree with the table beside them.

Figures are styled by the `.dg-*` classes in `static/css/style.css`, not by
inline colours. The stylesheet and script links carry a content hash
(`?v=...`), because a page drawn with a cached, older stylesheet shows the
figures as solid black shapes. Figures go on indexed pages only.

`noindex` pages also load no AdSense script: `page()` passes `ads=not noindex`.

### Dates and authorship

`CONTENT_PUBLISHED` and `CONTENT_UPDATED` are set by hand and feed the visible
byline, the `Article` markup on guides and comparisons, the privacy and About
pages, and every sitemap `lastmod`. Bump `CONTENT_UPDATED` in the same commit as
a change to prose or data; a rebuild that changes nothing must not claim to be
newer. The author in the markup is the organisation (`EDITORIAL`). The site
names no individual and claims no engineering credentials, and the About page
says so. `STANDARDS` lists the latest edition known of each standard; the site
does not claim to be transcribed from any one edition.

AdSense is omitted from `404.html` (`ads=False`), since an ad may not be shown
on a page with no content.

Neither gate checks arithmetic or links. After a build that adds pages, it is
worth walking `docs/` for `href="/..."` targets that do not exist and for
`FAQPage` / `BreadcrumbList` blocks that failed to render — both are a few lines
of Python over the output and both have caught real problems.

### Adding a comparison or guide

`compare()` and `guide()` wrap `_section_page()`, which emits the page, appends
it to `COMPARE_PAGES` / `GUIDE_PAGES` for the section index, and registers it for
search. A new page needs a call in `main()` and nothing else — the index, the
sitemap and both audits pick it up from the registry. Keep the call in the order
you want it to appear on the index.

## Analytics

`GA_ID` in `generate.py` is set to `G-YQ4MS7NNDS`, so the GA4 snippet ships on
every page and the privacy page states that analytics is active — both follow
`GA_ENABLED`, so clearing `GA_ID` removes the snippet and reverts the privacy
text in one edit. A placeholder ID is deliberately not shipped: it would cost
every visitor a request and collect nothing.

## Conventions

- Imperial is the primary unit everywhere. `dual()` renders inches with the
  millimetre value stacked beneath in grey; `inch_mm()` is the inline prose form.
- Every dimension page carries the reference-only disclaimer in the footer.
- Tables live inside `.table-scroll` so wide tables scroll themselves and the
  page body never scrolls horizontally.
- Print styles drop the chrome and un-scroll the tables — these pages get printed
  and taken into the field.
