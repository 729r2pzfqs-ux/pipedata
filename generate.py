#!/usr/bin/env python3
"""
PipeData.org static site generator.

Reads the YAML spec tables in data/ and writes a complete static site to docs/
(GitHub Pages serves from /docs). Run: python generate.py

Every page flows through head(), which registers its title and meta description
so audit_titles() and audit_descriptions() can fail the build on a duplicate or
an out-of-band length before anything ships.
"""

import html
import json
import math
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import date
from fractions import Fraction

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
STATIC = os.path.join(ROOT, "static")
OUT = os.path.join(ROOT, "docs")

SITE = "https://pipedata.org"
SITE_NAME = "PipeData"
EMAIL = "info@pipedata.org"
TODAY = date.today().isoformat()

# Set to a real "G-..." measurement ID to switch analytics on. Left empty the
# snippet is omitted entirely rather than shipped dead: a placeholder ID still
# costs every visitor a googletagmanager request and collects nothing.
GA_ID = "G-YQ4MS7NNDS"
GA_ENABLED = bool(GA_ID) and GA_ID != "G-XXXXXXXXXX"

DESC_MIN = 120
DESC_MAX = 160
TITLE_MAX = 60

# Steel weight constant: w (lb/ft) = 10.6802 * t * (OD - t), both in inches.
STEEL_W = 10.6802
MM = 25.4
LBFT_TO_KGM = 1.48816


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s), quote=True)


def nps_slug(nps):
    """'1 1/4' -> '1-1-4', '1/2' -> '1-2', '4' -> '4'."""
    return str(nps).replace(" ", "-").replace("/", "-")


def nps_value(nps):
    """Numeric value of an NPS label, for sorting. '1 1/4' -> 1.25."""
    nps = str(nps).strip()
    if " " in nps:
        whole, frac = nps.split(" ", 1)
        return int(whole) + float(Fraction(frac))
    if "/" in nps:
        return float(Fraction(nps))
    return float(nps)


def sched_slug(s):
    return "schedule-" + str(s).lower()


def sched_label(s):
    """Display label for a schedule key."""
    s = str(s)
    if s == "STD":
        return "STD"
    if s == "XS":
        return "XS"
    if s == "XXS":
        return "XXS"
    return "Sch " + s


def sched_long(s):
    """Long-form schedule name used in prose and titles."""
    s = str(s)
    return {"STD": "Standard (STD)",
            "XS": "Extra Strong (XS)",
            "XXS": "Double Extra Strong (XXS)"}.get(s, "Schedule " + s)


def n(v, dp=3):
    """Fixed-decimal number with trailing zeros kept (spec tables want them)."""
    return f"{v:.{dp}f}"


def dual(v, dp=3, mm_dp=2):
    """Imperial primary with the metric equivalent stacked beneath it."""
    if v is None:
        return '<span class="na">—</span>'
    return f'{n(v, dp)}<span class="mm">{n(v * MM, mm_dp)} mm</span>'


def dual_w(lbft):
    """Weight cell: lb/ft primary, kg/m beneath."""
    return f'{n(lbft, 2)}<span class="mm">{n(lbft * LBFT_TO_KGM, 2)} kg/m</span>'


def inch_mm(v, dp=3, mm_dp=1):
    """Inline prose form: 4.500 in (114.3 mm)."""
    return f"{n(v, dp)} in ({n(v * MM, mm_dp)} mm)"


def psi_bar(p):
    return f"{p} psig ({n(p * 0.0689476, 1)} bar)"


def weight_lbft(od, t):
    return STEEL_W * t * (od - t)


def inside_dia(od, t):
    return od - 2 * t


def area_sqin(id_):
    return math.pi / 4 * id_ ** 2


def gal_per_ft(id_):
    """US gallons of water held per foot of pipe."""
    return area_sqin(id_) * 12 / 231.0


def comma_list(items):
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def ldjson(obj):
    return ('<script type="application/ld+json">'
            + json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            + "</script>")


def write(path, content):
    full = os.path.join(OUT, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def fit_title(core, *tails):
    """Append optional tails while they fit inside TITLE_MAX, richest first."""
    out = core
    for t in tails:
        if len(out) + len(t) > TITLE_MAX:
            break
        out += t
    return out


def fit_desc(head_part, tails):
    """Pick the richest tail that keeps the description inside the length band.

    `tails` runs longest/most-informative first, so a page with more data
    advertises more of it and a thin page degrades gracefully instead of
    getting truncated mid-word in the SERP.
    """
    cands = [head_part + t for t in tails]
    for c in cands:
        if DESC_MIN <= len(c) <= DESC_MAX:
            return c
    under = [c for c in cands if len(c) <= DESC_MAX]
    if under:
        return max(under, key=len)
    return min(cands, key=len)


# path -> {"desc", "title", "noindex"}, filled by head() for every page emitted.
DESC_REGISTRY = {}
# Entries for the client-side search index.
SEARCH = []


def index_entry(title, url, meta):
    SEARCH.append({"t": title, "u": url.lstrip("/"), "m": meta})


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def load_yaml(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def load():
    pipes = load_yaml("pipe_sizes.yaml")
    for s in pipes["sizes"]:
        s["nps"] = str(s["nps"])
        s["slug"] = nps_slug(s["nps"])
        s["url"] = f"/pipes/nps-{s['slug']}/"
        s["val"] = nps_value(s["nps"])
        # YAML parses bare 5/10/40 as ints and STD/XS/XXS as strings; normalise.
        s["walls"] = {str(k): float(v) for k, v in s["walls"].items()}
    pipes["sizes"].sort(key=lambda s: s["val"])
    pipes["schedule_order"] = [str(x) for x in pipes["schedule_order"]]

    b165 = load_yaml("flanges_b165.yaml")
    b165["raised_face"] = {str(k): float(v) for k, v in b165["raised_face"].items()}
    for cls, blk in b165["classes"].items():
        for r in blk["rows"]:
            r["nps"] = str(r["nps"])
            r["val"] = nps_value(r["nps"])
        blk["rows"].sort(key=lambda r: r["val"])

    ftypes = load_yaml("flange_types.yaml")["types"]
    b1647 = load_yaml("flanges_b1647.yaml")
    fittings = load_yaml("fittings_b169.yaml")["fittings"]
    for f in fittings:
        f["rows"] = {str(k): float(v) for k, v in f["rows"].items()}
    pt = load_yaml("pt_ratings.yaml")
    mats = load_yaml("materials.yaml")["categories"]

    ss = load_yaml("pipe_sizes_b3619.yaml")
    ss["schedules"] = [str(x) for x in ss["schedules"]]
    ss["sizes"] = {str(k): {str(kk): float(vv) for kk, vv in v.items()}
                   for k, v in ss["sizes"].items()}
    ss["counterparts"] = {str(k): str(v) for k, v in ss["counterparts"].items()}

    errors = []
    for s in pipes["sizes"]:
        for k, t in s["walls"].items():
            if t * 2 >= s["od"]:
                errors.append(f"NPS {s['nps']} sch {k}: wall {t} closes the bore")
    # Every B36.19M size must exist in B36.10M, since the OD comes from there.
    known = {s["nps"] for s in pipes["sizes"]}
    for nps in ss["sizes"]:
        if nps not in known:
            errors.append(f"B36.19 lists NPS {nps}, which B36.10 does not")
    return pipes, b165, ftypes, b1647, fittings, pt, mats, ss, errors


def s_schedule_comparison(ss, pipes):
    """Compare each S-schedule against its B36.10M counterpart, from the data.

    Returns {S-schedule: {"same": [...], "differs": [(nps, s_wall, b_wall)],
                          "only_s": [...], "counterpart": "40"}}.

    Computed rather than asserted: the divergences between B36.10M and B36.19M
    are the single most useful thing on the page and also the easiest thing to
    get wrong in prose, so the prose is generated from the numbers.
    """
    by_nps = {s["nps"]: s for s in pipes["sizes"]}
    out = {}
    for sch in ss["schedules"]:
        cp = ss["counterparts"][sch]
        same, differs, only_s = [], [], []
        for nps, walls in ss["sizes"].items():
            if sch not in walls:
                continue
            s_wall = walls[sch]
            b_wall = by_nps[nps]["walls"].get(cp)
            if b_wall is None:
                only_s.append(nps)
            elif abs(b_wall - s_wall) < 1e-9:
                same.append(nps)
            else:
                differs.append((nps, s_wall, b_wall))
        out[sch] = {"counterpart": cp, "same": same, "differs": differs,
                    "only_s": only_s}
    return out


# --------------------------------------------------------------------------
# page chrome
# --------------------------------------------------------------------------

BRAND_SVG = (
    '<svg viewBox="0 0 64 64" aria-hidden="true">'
    '<rect x="4" y="20" width="56" height="24" rx="3" fill="#fff" opacity=".92"/>'
    '<rect x="4" y="20" width="8" height="24" rx="2" fill="#D35400"/>'
    '<rect x="52" y="20" width="8" height="24" rx="2" fill="#D35400"/>'
    '<rect x="12" y="27" width="40" height="10" rx="2" fill="#2B4C7E"/>'
    "</svg>"
)


def head(title, desc, path, ld=None, og_type="website", noindex=False):
    canon = SITE + path
    DESC_REGISTRY[path] = {"desc": desc, "title": title, "noindex": noindex}
    ldblocks = "".join(ldjson(o) for o in (ld or []))
    analytics = (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>\n'
        "<script>window.dataLayer=window.dataLayer||[];"
        "function gtag(){dataLayer.push(arguments);}"
        f"gtag('js',new Date());gtag('config','{GA_ID}');</script>"
    ) if GA_ENABLED else ""
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{robots}<link rel="canonical" href="{esc(canon)}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canon)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:image" content="{SITE}/og-default.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#2B4C7E">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/css/style.css">
{ldblocks}
{analytics}
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="/">{BRAND_SVG}<span>Pipe<span class="dot">Data</span></span></a>
    <nav class="site-nav">
      <a href="/pipes/">Pipe</a>
      <a href="/flanges/">Flanges</a>
      <a href="/fittings/">Fittings</a>
      <a href="/reference/">Reference</a>
      <a href="/about/">About</a>
    </nav>
  </div>
</header>
<main>
"""


def foot():
    return f"""</main>
<footer class="site-footer">
  <div class="wrap">
    <div class="footer-cols">
      <div>
        <h3>Pipe</h3>
        <ul>
          <li><a href="/pipes/">All NPS sizes</a></li>
          <li><a href="/pipes/schedule-40/">Schedule 40</a></li>
          <li><a href="/pipes/schedule-80/">Schedule 80</a></li>
          <li><a href="/reference/pipe-weight-chart/">Weight chart</a></li>
        </ul>
      </div>
      <div>
        <h3>Flanges &amp; fittings</h3>
        <ul>
          <li><a href="/flanges/weld-neck/">Weld neck</a></li>
          <li><a href="/flanges/blind/">Blind</a></li>
          <li><a href="/flanges/large/">Large diameter (B16.47)</a></li>
          <li><a href="/fittings/90-degree-elbow/">90° LR elbow</a></li>
        </ul>
      </div>
      <div>
        <h3>Reference</h3>
        <ul>
          <li><a href="/reference/nps-dn-conversion/">NPS to DN</a></li>
          <li><a href="/reference/schedule-chart/">Schedule chart</a></li>
          <li><a href="/reference/pressure-temperature-ratings/">P-T ratings</a></li>
          <li><a href="/reference/material-grades/">Material grades</a></li>
        </ul>
      </div>
      <div>
        <h3>Site</h3>
        <ul>
          <li><a href="/about/">About</a></li>
          <li><a href="/privacy/">Privacy</a></li>
          <li><a href="/sitemap.xml">Sitemap</a></li>
          <li><a href="mailto:{EMAIL}">Contact</a></li>
        </ul>
      </div>
    </div>
    <div class="footer-legal">
      <p><strong>Reference only.</strong> PipeData reproduces dimensional and rating
      data from published industry standards for quick lookup. It is not a substitute
      for the standards themselves. Confirm every dimension against a current copy of
      the governing ASME, ASTM or API document before fabrication, procurement or
      design. PipeData is an independent project and is not affiliated with ASME,
      ASTM, API or MSS.</p>
      <p>© {date.today().year} PipeData.org · <a href="mailto:{EMAIL}">{EMAIL}</a></p>
    </div>
  </div>
</footer>
<script src="/js/search.js" defer></script>
</body>
</html>
"""


def page(path, title, desc, body, ld=None, og_type="website", noindex=False):
    out = os.path.join(path.strip("/"), "index.html") if path != "/" else "index.html"
    write(out, head(title, desc, path, ld, og_type, noindex) + body + foot())


def crumbs(items):
    """items: list of (label, url|None). Returns (html, BreadcrumbList schema)."""
    lis, elements = [], []
    for i, (label, url) in enumerate(items, 1):
        if url:
            lis.append(f'<li><a href="{esc(url)}">{esc(label)}</a></li>')
        else:
            lis.append(f'<li><span aria-current="page">{esc(label)}</span></li>')
        el = {"@type": "ListItem", "position": i, "name": label}
        if url:
            el["item"] = SITE + url
        elements.append(el)
    nav = ('<nav class="breadcrumbs" aria-label="Breadcrumb"><div class="wrap"><ol>'
           + "".join(lis) + "</ol></div></nav>")
    return nav, {"@context": "https://schema.org", "@type": "BreadcrumbList",
                 "itemListElement": elements}


def faq(pairs):
    """pairs: list of (question, answer_html). Returns (html, FAQPage schema)."""
    items = "".join(
        f"<details><summary>{esc(q)}</summary><div>{a}</div></details>"
        for q, a in pairs)
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer",
                                "text": re.sub(r"<[^>]+>", "", a).strip()}}
            for q, a in pairs
        ],
    }
    return f'<section class="faq"><h2>Common questions</h2>{items}</section>', schema


def item_list(entries, name):
    return {
        "@context": "https://schema.org", "@type": "ItemList", "name": name,
        "numberOfItems": len(entries),
        "itemListElement": [
            {"@type": "ListItem", "position": i, "url": SITE + u, "name": t}
            for i, (t, u) in enumerate(entries, 1)],
    }


def facts(pairs):
    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in pairs)
    return f'<dl class="facts">{cells}</dl>'


def table(headers, rows, caption=None, note=None, cls="specs"):
    thead = "".join(f"<th scope=\"col\">{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    cap = f"<caption>{caption}</caption>" if caption else ""
    out = (f'<div class="table-scroll"><table class="{cls}">{cap}'
           f"<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table></div>")
    if note:
        out += f'<p class="table-note">{note}</p>'
    return out


UNITS_NOTE = ('<p class="units-note">Dimensions are given in <strong>inches</strong>, '
              'with the millimetre equivalent beneath in grey.</p>')


# --------------------------------------------------------------------------
# pipe pages
# --------------------------------------------------------------------------

def pipe_page(s, pipes, sizes_by_slug):
    order = [k for k in pipes["schedule_order"] if k in s["walls"]]
    od = s["od"]
    rows = []
    for k in order:
        t = s["walls"][k]
        idd = inside_dia(od, t)
        w = weight_lbft(od, t)
        rows.append([
            f'<a href="/pipes/nps-{s["slug"]}/{sched_slug(k)}/">'
            f'<strong>{sched_label(k)}</strong></a>',
            dual(t),
            dual(idd),
            dual_w(w),
            n(area_sqin(idd), 2),
            n(gal_per_ft(idd), 3),
        ])

    sch40 = s["walls"].get("40") or s["walls"].get("STD")
    std = s["walls"]["STD"]
    xs = s["walls"].get("XS")
    numbered = [k for k in order if k.isdigit()]

    # STD/XS coincidence is the single most-confused thing about a size, so it
    # gets stated explicitly on every page rather than left to the reference page.
    same_as = [k for k in numbered if abs(s["walls"][k] - std) < 1e-9]
    std_note = (f"Standard weight equals Schedule {same_as[0]} in this size."
                if same_as else
                "Standard weight does not coincide with any numbered schedule "
                "in this size.")
    xs_same = [k for k in numbered if xs and abs(s["walls"][k] - xs) < 1e-9]
    xs_note = (f"Extra strong equals Schedule {xs_same[0]}." if xs_same else
               "Extra strong does not coincide with a numbered schedule here.")

    fact_rows = [
        ("Nominal size", f"NPS {esc(s['nps'])}"),
        ("Metric designator", f"DN {s['dn']}"),
        ("Outside diameter", inch_mm(od)),
        ("Schedules listed", str(len(order))),
        ("Standard weight wall", inch_mm(std)),
    ]
    if xs:
        fact_rows.append(("Extra strong wall", inch_mm(xs)))

    body_rows = [
        f"<h2>Every schedule in NPS {esc(s['nps'])}</h2>",
        UNITS_NOTE,
        table(
            ["Schedule", "Wall thickness", "Inside diameter", "Weight, empty",
             "Flow area (in²)", "Water (US gal/ft)"],
            rows,
            caption=(f"ASME B36.10M wall thicknesses for NPS {esc(s['nps'])} "
                     f"(DN {s['dn']}), outside diameter {inch_mm(od)}."),
            note=("Weight is calculated for carbon steel as "
                  "w = 10.6802 × t × (OD − t) lb/ft and is the plain-end weight, "
                  "excluding coatings, linings and fittings."),
        ),
        f"<p>{std_note} {xs_note}</p>",
    ]

    # neighbouring sizes
    idx = [x["slug"] for x in pipes["sizes"]].index(s["slug"])
    nav = []
    if idx > 0:
        p = pipes["sizes"][idx - 1]
        nav.append(f'<a class="card" href="{p["url"]}">'
                   f'<span class="card-title">← NPS {esc(p["nps"])}</span>'
                   f'<span class="card-meta">OD {inch_mm(p["od"])}</span></a>')
    if idx < len(pipes["sizes"]) - 1:
        nx = pipes["sizes"][idx + 1]
        nav.append(f'<a class="card" href="{nx["url"]}">'
                   f'<span class="card-title">NPS {esc(nx["nps"])} →</span>'
                   f'<span class="card-meta">OD {inch_mm(nx["od"])}</span></a>')

    sched_links = "".join(
        f'<a class="chip-link" href="/pipes/{sched_slug(k)}/">{sched_label(k)}</a>'
        for k in order)
    combo_links = "".join(
        f'<a class="chip-link" href="/pipes/nps-{s["slug"]}/{sched_slug(k)}/">'
        f'NPS {esc(s["nps"])} {sched_label(k)}</a>'
        for k in order)

    q = [
        (f"What is the outside diameter of NPS {s['nps']} pipe?",
         f"<p>NPS {esc(s['nps'])} pipe has an outside diameter of "
         f"{inch_mm(od)}. That outside diameter is fixed: it is identical in "
         f"every schedule, and only the wall thickness — and therefore the "
         f"bore — changes from one schedule to the next.</p>"),
        (f"How much does NPS {s['nps']} schedule 40 pipe weigh?",
         f"<p>{n(weight_lbft(od, sch40), 2)} lb/ft "
         f"({n(weight_lbft(od, sch40) * LBFT_TO_KGM, 2)} kg/m) as plain-end "
         f"carbon steel, on a wall thickness of {inch_mm(sch40)}.</p>"),
        (f"What is the metric equivalent of NPS {s['nps']}?",
         f"<p>DN {s['dn']}. DN is a dimensionless designator, not a measurement "
         f"— DN {s['dn']} pipe does not measure {s['dn']} mm anywhere. Its "
         f"actual outside diameter is {n(od * MM, 1)} mm.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Pipe", "/pipes/"),
                                   (f"NPS {s['nps']}", None)])

    title = fit_title(f"NPS {s['nps']} Pipe Dimensions & Weight Chart",
                      " | PipeData")
    desc = fit_desc(
        f"NPS {s['nps']} (DN {s['dn']}) pipe has an OD of {n(od, 3)} in "
        f"({n(od * MM, 1)} mm). ",
        [f"Wall thickness, bore, weight and flow area for all "
         f"{len(order)} ASME B36.10 schedules.",
         f"Wall thickness, bore and weight for all {len(order)} "
         f"ASME B36.10 schedules.",
         f"Full ASME B36.10 wall thickness and weight table for all "
         f"{len(order)} schedules.",
         "Wall thickness, bore and weight for every ASME B36.10 schedule."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>NPS {esc(s["nps"])} Pipe Dimensions</h1>'
            f'<p class="lede">Outside diameter {inch_mm(od)} — fixed across every '
            f'schedule. {len(order)} wall thicknesses from ASME B36.10M, with bore, '
            f'plain-end weight and flow area for each.</p></div>'
            + facts(fact_rows)
            + "".join(body_rows)
            + f'<h2>This size, schedule by schedule</h2>'
            f'<p>A page for each combination, with bore, weight, water '
            f'capacity and the matching flange and fitting data.</p>'
            f'<div class="chip-links">{combo_links}</div>'
            + f'<h2>This size in a schedule chart</h2>'
            f'<div class="chip-links">{sched_links}</div>'
            + (f'<p><a class="more" href="/flanges/nps-{s["slug"]}/">'
               f'NPS {esc(s["nps"])} flange dimensions →</a></p>'
               if s["nps"] in FLANGE_SIZES else "")
            + faq_html
            + (f'<h2>Adjacent sizes</h2><div class="grid">{"".join(nav)}</div>'
               if nav else "")
            + "</div>")

    page(s["url"], title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"NPS {s['nps']} pipe", s["url"],
                f"DN {s['dn']} · OD {n(od, 3)} in · {len(order)} schedules")


def pipes_index(pipes):
    rows = []
    for s in pipes["sizes"]:
        std = s["walls"]["STD"]
        rows.append([
            f'<a href="{s["url"]}"><strong>NPS {esc(s["nps"])}</strong></a>',
            f'DN {s["dn"]}',
            dual(s["od"]),
            dual(std),
            dual_w(weight_lbft(s["od"], std)),
            str(len([k for k in pipes["schedule_order"] if k in s["walls"]])),
        ])

    scheds = []
    for k in pipes["schedule_order"]:
        count = sum(1 for s in pipes["sizes"] if k in s["walls"])
        scheds.append(
            f'<a class="card" href="/pipes/{sched_slug(k)}/">'
            f'<span class="card-title">{sched_long(k)}</span>'
            f'<span class="card-meta">{count} sizes</span></a>')

    size_cards = "".join(
        f'<a class="card" href="{s["url"]}"><span class="card-title">'
        f'NPS {esc(s["nps"])}</span><span class="card-meta">DN {s["dn"]}</span>'
        f'<span class="card-spec">OD {n(s["od"], 3)} in</span></a>'
        for s in pipes["sizes"])

    q = [
        ("Does the outside diameter of pipe change with schedule?",
         "<p>No. For a given NPS the outside diameter is fixed by ASME B36.10M. "
         "Increasing the schedule thickens the wall inward, shrinking the bore "
         "while the OD stays put. That is what lets one set of flanges, fittings "
         "and pipe supports serve every schedule in a size.</p>"),
        ("Why does NPS stop matching the actual size above NPS 12?",
         "<p>From NPS 14 up, NPS equals the outside diameter in inches exactly — "
         "NPS 14 pipe is 14.000 in OD. Below NPS 14 the number is a historic "
         "designator inherited from iron pipe sizing and matches neither the OD "
         "nor the bore: NPS 2 pipe is 2.375 in OD.</p>"),
        ("What is the difference between schedule 40 and standard weight?",
         "<p>They are identical from NPS 1/8 through NPS 10. At NPS 12 they part "
         "company — standard weight stays at 0.375 in while schedule 40 goes to "
         "0.406 in — and above NPS 12 standard weight is 0.375 in in every size "
         "while schedule 40 keeps climbing.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Pipe", None)])

    body = (crumb_html + '<div class="wrap">'
            '<div class="page-head"><h1>Pipe Dimensions — ASME B36.10M</h1>'
            f'<p class="lede">Outside diameter, wall thickness, bore and weight for '
            f'{len(pipes["sizes"])} nominal pipe sizes from NPS 1/8 to NPS 36, across '
            f'every schedule the standard publishes.</p></div>'
            + UNITS_NOTE
            + table(["Size", "DN", "Outside diameter", "STD wall",
                     "STD weight", "Schedules"], rows,
                    caption="Summary of ASME B36.10M nominal pipe sizes. "
                            "Standard weight (STD) shown; open a size for every "
                            "schedule.")
            + '<h2>Browse by size</h2>'
            f'<div class="grid tight">{size_cards}</div>'
            + '<h2>Browse by schedule</h2>'
            f'<div class="grid">{"".join(scheds)}</div>'
            + faq_html + "</div>")

    title = "Pipe Dimensions Chart — ASME B36.10 | PipeData"
    desc = ("Outside diameter, wall thickness, bore and weight for every ASME "
            "B36.10 pipe size from NPS 1/8 to NPS 36, in all 14 schedules. "
            "Imperial with metric.")
    page("/pipes/", title, desc, body,
         ld=[crumb_ld, faq_ld,
             item_list([(f"NPS {s['nps']}", s["url"]) for s in pipes["sizes"]],
                       "ASME B36.10 pipe sizes")])
    index_entry("Pipe dimensions index", "/pipes/",
                "ASME B36.10 · NPS 1/8 to NPS 36")


def schedule_page(k, pipes):
    sizes = [s for s in pipes["sizes"] if k in s["walls"]]
    rows = []
    for s in sizes:
        t = s["walls"][k]
        idd = inside_dia(s["od"], t)
        rows.append([
            f'<a href="{s["url"]}"><strong>NPS {esc(s["nps"])}</strong></a>',
            f'DN {s["dn"]}',
            dual(s["od"]),
            dual(t),
            dual(idd),
            dual_w(weight_lbft(s["od"], t)),
            n(gal_per_ft(idd), 3),
            f'<a href="/pipes/nps-{s["slug"]}/{sched_slug(k)}/">detail →</a>',
        ])

    thin = min(sizes, key=lambda s: s["walls"][k])
    thick = max(sizes, key=lambda s: s["walls"][k])
    label = sched_long(k)

    # Where this schedule coincides with STD/XS/XXS, say so — it is the most
    # common source of a wrong material requisition.
    coincide = []
    if k.isdigit():
        for alias in ("STD", "XS", "XXS"):
            hits = [s for s in sizes
                    if alias in s["walls"]
                    and abs(s["walls"][alias] - s["walls"][k]) < 1e-9]
            if hits:
                rng = (f"NPS {hits[0]['nps']}" if len(hits) == 1
                       else f"NPS {hits[0]['nps']} through NPS {hits[-1]['nps']}")
                coincide.append(f"{alias} in {rng}")
    alias_para = (f"<p>In this schedule the wall thickness coincides with "
                  f"{comma_list(coincide)}. Outside those sizes it does not, so "
                  f"never substitute one designation for the other without "
                  f"checking the number.</p>" if coincide else "")

    fact_rows = [
        ("Schedule", label),
        ("Sizes published", f"{len(sizes)} (NPS {sizes[0]['nps']} to NPS {sizes[-1]['nps']})"),
        ("Thinnest wall", f"{inch_mm(thin['walls'][k])} at NPS {thin['nps']}"),
        ("Thickest wall", f"{inch_mm(thick['walls'][k])} at NPS {thick['nps']}"),
        ("Standard", "ASME B36.10M"),
    ]

    q = [
        (f"What sizes is {label.lower()} pipe made in?",
         f"<p>ASME B36.10M publishes {label.lower()} in {len(sizes)} sizes, from "
         f"NPS {esc(sizes[0]['nps'])} through NPS {esc(sizes[-1]['nps'])}. "
         f"Wall thickness runs from {inch_mm(thin['walls'][k])} at the small end "
         f"to {inch_mm(thick['walls'][k])} at the large end.</p>"),
        (f"Does {label.lower()} mean the same wall thickness in every size?",
         "<p>No. A schedule number is a series, not a thickness. It sets a wall "
         "that rises with the pipe size so the pressure rating stays roughly "
         "constant across the range — which is the whole point of the schedule "
         "system.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Pipe", "/pipes/"),
                                   (label, None)])

    title = fit_title(f"{label} Pipe Dimensions Chart", " | PipeData")
    desc = fit_desc(
        f"{label} pipe wall thickness runs {n(thin['walls'][k], 3)}–"
        f"{n(thick['walls'][k], 3)} in across {len(sizes)} sizes. ",
        [f"OD, bore and weight for NPS {sizes[0]['nps']} to NPS {sizes[-1]['nps']}, "
         f"per ASME B36.10.",
         f"OD, bore and weight per ASME B36.10M for every published size.",
         "OD, bore and weight for every size in ASME B36.10M."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(label)} Pipe Dimensions</h1>'
            f'<p class="lede">Wall thickness, bore and plain-end weight for all '
            f'{len(sizes)} sizes ASME B36.10M publishes in {label.lower()}, '
            f'NPS {esc(sizes[0]["nps"])} through NPS {esc(sizes[-1]["nps"])}.'
            f'</p></div>'
            + facts(fact_rows) + UNITS_NOTE
            + table(["Size", "DN", "Outside diameter", "Wall thickness",
                     "Inside diameter", "Weight, empty", "Water (US gal/ft)",
                     "This size"],
                    rows,
                    caption=f"ASME B36.10M {label.lower()} dimensions.",
                    note="Weight is plain-end carbon steel, "
                         "w = 10.6802 × t × (OD − t) lb/ft.")
            + alias_para + faq_html
            + '<p><a class="more" href="/reference/schedule-chart/">'
            'How pipe schedules work →</a></p></div>')

    page(f"/pipes/{sched_slug(k)}/", title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"{label} pipe", f"/pipes/{sched_slug(k)}/",
                f"{len(sizes)} sizes · ASME B36.10")


WATER_LB_GAL = 8.3454   # US gallon of fresh water at 60 °F
SQIN_TO_SQMM = 645.16


def combo_page(s, k, pipes, b165, fittings):
    """One NPS x schedule combination — the "4 inch schedule 40 pipe" page.

    This is the shape most lookups actually take: nobody searches for a
    schedule in the abstract, they search for a size in a schedule. Everything
    on the page is derived from the two numbers B36.10M publishes for the
    combination (OD and wall), so there is nothing to keep in sync by hand.
    """
    od, t = s["od"], s["walls"][k]
    idd = inside_dia(od, t)
    w = weight_lbft(od, t)
    gal = gal_per_ft(idd)
    water = gal * WATER_LB_GAL
    area = area_sqin(idd)
    order = [x for x in pipes["schedule_order"] if x in s["walls"]]
    twins = [x for x in order if x != k and abs(s["walls"][x] - t) < 1e-9]
    long_ = sched_long(k)
    url = f"/pipes/nps-{s['slug']}/{sched_slug(k)}/"

    fact_rows = [
        ("Nominal size", f"NPS {esc(s['nps'])} (DN {s['dn']})"),
        ("Schedule", esc(long_)),
        ("Outside diameter", inch_mm(od)),
        ("Wall thickness", inch_mm(t)),
        ("Inside diameter", inch_mm(idd)),
        ("Weight, empty", f"{n(w, 2)} lb/ft ({n(w * LBFT_TO_KGM, 2)} kg/m)"),
        ("Weight, water filled",
         f"{n(w + water, 2)} lb/ft ({n((w + water) * LBFT_TO_KGM, 2)} kg/m)"),
        ("Flow area", f"{n(area, 3)} in² ({n(area * SQIN_TO_SQMM, 0)} mm²)"),
    ]

    # The alias question ("is STD the same as sch 40 here?") is size-specific
    # and is the single most common way this combination gets mis-ordered.
    if twins:
        eq = (f"<p>In NPS {esc(s['nps'])} this wall thickness is also designated "
              f"{comma_list([sched_long(x) for x in twins])}. That is the same "
              f"pipe under another name, not a different product — but the "
              f"coincidence is specific to this size and does not hold across "
              f"the range.</p>")
    else:
        eq = (f"<p>No other designation in NPS {esc(s['nps'])} shares this wall "
              f"thickness, so {esc(long_)} is unambiguous in this size.</p>")

    ctx = []
    for x in order:
        tx = s["walls"][x]
        ix = inside_dia(od, tx)
        here = x == k
        cell = (f'<strong>{sched_label(x)}</strong>' if here else
                f'<a href="/pipes/nps-{s["slug"]}/{sched_slug(x)}/">'
                f'{sched_label(x)}</a>')
        ctx.append([
            cell + (' <span class="mm">this page</span>' if here else ""),
            dual(tx), dual(ix), dual_w(weight_lbft(od, tx)),
            n(gal_per_ft(ix), 3),
        ])

    fl_rows = []
    for c in ("150", "300", "400", "600", "900", "1500", "2500"):
        blk = b165["classes"].get(c)
        r = next((x for x in blk["rows"] if x["nps"] == s["nps"]), None) if blk else None
        if not r:
            continue
        fl_rows.append([
            f'<a href="/flanges/weld-neck/class-{c}/nps-{s["slug"]}/">'
            f'Class {c}</a>',
            dual(r["o"], 2), dual(r["tf"], 2), dual(r["bc"], 2),
            f'{r["bolts"]} × {esc(r["bolt"])} in',
        ])
    flange_block = ""
    if fl_rows:
        flange_block = (
            f'<h2>Flanges for NPS {esc(s["nps"])} pipe</h2>'
            f'<p>Flange dimensions are set by NPS and pressure class, not by '
            f'schedule, so every flange below fits this pipe. The exception is '
            f'the weld neck: its bore is machined to the pipe it is welded to, '
            f'which for {esc(long_)} means {inch_mm(idd)}. Order the flange to '
            f'the schedule or the bores will not line up at the weld.</p>'
            + table(["Class", "Flange OD", "Thickness", "Bolt circle", "Bolting"],
                    fl_rows,
                    caption=f"ASME B16.5 flange dimensions in NPS "
                            f"{esc(s['nps'])}, all published classes.")
            + f'<p><a class="more" href="/flanges/nps-{s["slug"]}/">'
              f'All NPS {esc(s["nps"])} flange data →</a></p>')

    fit_rows = []
    for f in fittings:
        v = f["rows"].get(s["nps"])
        if v is None:
            continue
        fit_rows.append([
            f'<a href="/fittings/{f["slug"]}/">{esc(f["name"])}</a>',
            esc(f["dim_label"]), dual(v, 2),
        ])
    fit_block = ""
    if fit_rows:
        fit_block = (
            f'<h2>Fittings for NPS {esc(s["nps"])} pipe</h2>'
            f'<p>ASME B16.9 dimensions come from NPS alone, so these figures '
            f'hold for {esc(long_)} exactly as they do for every other schedule '
            f'in the size. Only the wall thickness has to be ordered to match.</p>'
            + table(["Fitting", "Dimension", "NPS " + esc(s["nps"])], fit_rows,
                    caption=f"ASME B16.9 buttweld fitting dimensions in NPS "
                            f"{esc(s['nps'])}."))

    tons = 2000.0 / w
    q = [
        (f"What is the inside diameter of NPS {s['nps']} {long_.lower()} pipe?",
         f"<p>{inch_mm(idd)}. The outside diameter is fixed at {inch_mm(od)} by "
         f"ASME B36.10M, and the {inch_mm(t)} wall takes {n(2 * t, 3)} in off "
         f"it — {n(t, 3)} in from each side — leaving that bore.</p>"),
        (f"How much does NPS {s['nps']} {long_.lower()} pipe weigh?",
         f"<p>{n(w, 2)} lb/ft ({n(w * LBFT_TO_KGM, 2)} kg/m) empty, as plain-end "
         f"carbon steel. Full of water it comes to {n(w + water, 2)} lb/ft "
         f"({n((w + water) * LBFT_TO_KGM, 2)} kg/m), because the bore holds "
         f"{n(gal, 3)} US gallons per foot. One short ton of this pipe is about "
         f"{n(tons, 0)} ft.</p>"),
        (f"Does NPS {s['nps']} {long_.lower()} pipe use special flanges or "
         f"fittings?",
         f"<p>No. Flanges and buttweld fittings are dimensioned from NPS, so "
         f"NPS {esc(s['nps'])} hardware fits this pipe in any schedule. The one "
         f"thing that does follow the schedule is the bore of a weld neck "
         f"flange or a fitting end prep, which has to be {inch_mm(idd)} to "
         f"match.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    crumb_html, crumb_ld = crumbs([
        ("Home", "/"), ("Pipe", "/pipes/"),
        (f"NPS {s['nps']}", s["url"]), (sched_label(k), None)])

    title = fit_title(f"NPS {s['nps']} {long_} Pipe Dimensions", " | PipeData")
    desc = fit_desc(
        f"NPS {s['nps']} {long_.lower()} pipe: {n(od, 3)} in OD, {n(t, 3)} in "
        f"wall, {n(idd, 3)} in bore, {n(w, 2)} lb/ft. ",
        ["Flow area, water capacity and every schedule in this size.",
         "Flow area, water capacity and matching flange data.",
         "Flow area and water capacity per ASME B36.10.",
         "Full ASME B36.10 dimensions."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head">'
            f'<h1>NPS {esc(s["nps"])} {esc(long_)} Pipe</h1>'
            f'<p class="lede">{inch_mm(od)} outside diameter on a '
            f'{inch_mm(t)} wall, giving a {inch_mm(idd)} bore and '
            f'{n(w, 2)} lb/ft ({n(w * LBFT_TO_KGM, 2)} kg/m) empty. '
            f'ASME B36.10M.</p></div>'
            + facts(fact_rows) + eq
            + f'<h2>Every schedule in NPS {esc(s["nps"])}</h2>' + UNITS_NOTE
            + table(["Schedule", "Wall thickness", "Inside diameter",
                     "Weight, empty", "Water (US gal/ft)"], ctx,
                    caption=f"ASME B36.10M schedules published in NPS "
                            f"{esc(s['nps'])}, outside diameter {inch_mm(od)}.",
                    note="Weight is plain-end carbon steel, "
                         "w = 10.6802 × t × (OD − t) lb/ft.")
            + flange_block + fit_block + faq_html
            + '<h2>Related</h2><div class="chip-links">'
            f'<a class="chip-link" href="{s["url"]}">All NPS {esc(s["nps"])} '
            f'schedules</a>'
            f'<a class="chip-link" href="/pipes/{sched_slug(k)}/">'
            f'{esc(long_)} in every size</a>'
            f'<a class="chip-link" href="/reference/pipe-weight-chart/">'
            f'Weight chart</a>'
            f'<a class="chip-link" href="/reference/schedule-chart/">'
            f'Schedule chart</a></div></div>')

    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"NPS {s['nps']} {long_.lower()}", url,
                f"{n(t, 3)} in wall · {n(idd, 3)} in bore · {n(w, 2)} lb/ft")


# --------------------------------------------------------------------------
# flange pages
# --------------------------------------------------------------------------

def flange_class_page(ft, cls, blk, b165, ftypes):
    rows = []
    rf = b165["raised_face"]
    show_hub = any(r.get("y_wn") for r in blk["rows"]) and ft["slug"] != "blind"
    hub_key = "y_wn" if ft["slug"] == "weld-neck" else "y_so"

    headers = ["Size", "Flange OD", "Thickness", "Bolt circle", "Bolts",
               "Bolt dia.", "Raised face OD"]
    if show_hub:
        headers.append("Length thru hub")

    has_detail = ft["slug"] in ("weld-neck", "blind")
    for r in blk["rows"]:
        size_cell = f'<strong>NPS {esc(r["nps"])}</strong>'
        if has_detail:
            size_cell = (f'<a href="/flanges/{ft["slug"]}/class-{cls}/'
                         f'nps-{nps_slug(r["nps"])}/">{size_cell}</a>')
        cells = [
            size_cell,
            dual(r["o"], 2),
            dual(r["tf"], 2),
            dual(r["bc"], 2),
            str(r["bolts"]),
            esc(r["bolt"]) + " in",
            dual(rf[r["nps"]], 2) if r["nps"] in rf else '<span class="na">—</span>',
        ]
        if show_hub:
            v = r.get(hub_key)
            cells.append(dual(v, 2) if v else '<span class="na">—</span>')
        rows.append(cells)

    smallest, largest = blk["rows"][0], blk["rows"][-1]
    rating = None
    for g in PT_GROUPS:
        if g["slug"] == "1-1" and cls in g["ratings"]:
            rating = g["ratings"][cls][0]
            break

    fact_rows = [
        ("Standard", "ASME B16.5"),
        ("Flange type", ft["name"]),
        ("Pressure class", f"Class {cls}"),
        ("Size range", f"NPS {smallest['nps']} – NPS {largest['nps']}"),
        ("Raised face height", inch_mm(blk["rf_height"], 2)),
    ]
    if rating:
        fact_rows.append(("Rating at 100 °F (A105)", psi_bar(rating)))

    blind_note = ""
    if ft["slug"] == "blind":
        blind_note = ("<p>A blind flange has no bore and no hub, so the flange "
                      "outside diameter, thickness, bolt circle and bolting are "
                      "the whole of its dimensional definition. Thickness shown is "
                      "the minimum required by B16.5 — many mills supply "
                      "heavier.</p>")
    if ft["slug"] == "socket-weld" and nps_value(largest["nps"]) > 3:
        blind_note += ("<p>ASME B16.5 publishes socket weld flanges through "
                       "NPS 3 only. Rows above NPS 3 in the table are the "
                       "corresponding slip-on dimensions, shown so the bolt "
                       "pattern can still be looked up; a socket weld flange is "
                       "not available in those sizes.</p>")
    if blk.get("note"):
        blind_note += f"<p>{esc(blk['note'])}</p>"

    q = [
        (f"How many bolts does a Class {cls} {ft['short']} flange use?",
         "<p>" + "; ".join(
             f"NPS {esc(r['nps'])} takes {r['bolts']} × {esc(r['bolt'])} in"
             for r in blk["rows"][:5])
         + ". Bolt count rises with size — see the table above for the full "
           "range.</p>"),
        (f"What pressure is a Class {cls} flange rated for?",
         f"<p>Class is a designation, not a pressure. A Class {cls} flange in "
         f"A105 carbon steel is rated {psi_bar(rating)} at 100 °F, and that "
         f"rating falls as metal temperature rises. In stainless the same class "
         f"starts lower at ambient but holds up far better in the heat — see the "
         f"<a href='/reference/pressure-temperature-ratings/'>P-T rating "
         f"tables</a>.</p>" if rating else
         "<p>Class is a designation, not a pressure. See the P-T rating "
         "tables for the allowable pressure at your material and "
         "temperature.</p>"),
        (f"Will a Class {cls} flange bolt to a Class 150 flange?",
         "<p>No. Bolt circle diameter, bolt count and bolt size all change with "
         "class, so flanges of different classes in the same NPS will not mate. "
         "The only exceptions inside B16.5 are the deliberate ones the standard "
         "calls out, such as Class 400 sharing Class 600 dimensions in NPS 1/2 "
         "through 3 1/2.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    other_classes = "".join(
        f'<a class="chip-link" href="/flanges/{ft["slug"]}/class-{c}/">Class {c}</a>'
        for c in ft["classes"] if c != cls)
    other_types = "".join(
        f'<a class="chip-link" href="/flanges/{t["slug"]}/class-{cls}/">{t["short"].title()}</a>'
        for t in ftypes if t["slug"] != ft["slug"] and cls in t["classes"])

    crumb_html, crumb_ld = crumbs([
        ("Home", "/"), ("Flanges", "/flanges/"),
        (ft["name"], f"/flanges/{ft['slug']}/"), (f"Class {cls}", None)])

    short_title = ft["short"].title()
    title = fit_title(f"Class {cls} {short_title} Flange Dimensions",
                      " | PipeData")
    desc = fit_desc(
        f"Class {cls} {ft['short']} flange dimensions, NPS {smallest['nps']} to "
        f"NPS {largest['nps']}: ",
        ["outside diameter, thickness, bolt circle, bolt count and size per "
         "ASME B16.5.",
         "OD, thickness, bolt circle, bolt count and bolt size per ASME B16.5.",
         "OD, thickness, bolt circle and bolting per ASME B16.5.",
         "full ASME B16.5 dimension and bolting table."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>Class {cls} {esc(ft["name"])} Dimensions</h1>'
            f'<p class="lede">ASME B16.5 dimensions and bolting for the Class {cls} '
            f'{ft["short"]} flange, NPS {esc(smallest["nps"])} through '
            f'NPS {esc(largest["nps"])}.</p></div>'
            + facts(fact_rows) + UNITS_NOTE
            + table(headers, rows,
                    caption=f"ASME B16.5 Class {cls} {ft['short']} flange "
                            f"dimensions.",
                    note="Flange thickness excludes the raised face. Bolt "
                         "diameter is nominal; stud bolts are normally ASTM "
                         "A193 B7 with A194 2H nuts.")
            + blind_note
            + (f'<p>Each size above links to its own page, with '
               + ("the flange bore for every schedule of pipe it can be "
                  "welded to."
                  if ft["slug"] == "weld-neck" else
                  "an approximate weight and the end thrust at the rated "
                  "pressure.")
               + "</p>" if has_detail else "")
            + faq_html
            + f'<h2>Same flange, other classes</h2>'
            f'<div class="chip-links">{other_classes}</div>'
            + f'<h2>Same class, other flange types</h2>'
            f'<div class="chip-links">{other_types}</div>'
            + '<h2>Every class in one size</h2>'
            '<div class="chip-links">'
            + "".join(f'<a class="chip-link" href="/flanges/nps-'
                      f'{nps_slug(r["nps"])}/">NPS {esc(r["nps"])}</a>'
                      for r in blk["rows"])
            + "</div></div>")

    url = f"/flanges/{ft['slug']}/class-{cls}/"
    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"Class {cls} {ft['name']}", url,
                f"ASME B16.5 · NPS {smallest['nps']}–{largest['nps']}")


def flange_type_page(ft, b165, ftypes):
    cards = "".join(
        f'<a class="card" href="/flanges/{ft["slug"]}/class-{c}/">'
        f'<span class="card-title">Class {c}</span>'
        f'<span class="card-meta">NPS {b165["classes"][c]["rows"][0]["nps"]} – '
        f'{b165["classes"][c]["rows"][-1]["nps"]}</span>'
        f'<span class="card-spec">{len(b165["classes"][c]["rows"])} sizes</span></a>'
        for c in ft["classes"])

    pros = "".join(f"<li>{esc(p)}</li>" for p in ft["pros"])
    cons = "".join(f"<li>{esc(c)}</li>" for c in ft["cons"])

    # Class 150 dimensions as the at-a-glance table on the type page.
    c150 = b165["classes"]["150"]
    rf = b165["raised_face"]
    rows = [[f'<strong>NPS {esc(r["nps"])}</strong>', dual(r["o"], 2),
             dual(r["tf"], 2), dual(r["bc"], 2), str(r["bolts"]),
             esc(r["bolt"]) + " in", dual(rf[r["nps"]], 2)]
            for r in c150["rows"]]

    q = [
        (f"When should I use a {ft['short']} flange?",
         f"<p>{esc(ft['use'])}</p>"),
        (f"What pressure classes does a {ft['short']} flange come in?",
         f"<p>ASME B16.5 publishes the {ft['short']} flange in "
         f"{comma_list(['Class ' + c for c in ft['classes']])}, in sizes "
         f"NPS 1/2 through NPS 24. Above NPS 24 large-diameter flanges are "
         f"covered by <a href='/flanges/large/'>ASME B16.47</a> instead.</p>"),
        ("Does the flange face type change these dimensions?",
         "<p>The flange outside diameter, bolt circle and bolting are the same "
         "whatever the face. What changes is the facing itself and the flange "
         "thickness measurement: B16.5 thickness excludes the raised face, so a "
         "Class 300 raised-face flange is 0.06 in thicker overall than the "
         "tabulated figure. Ring-type joint flanges are grooved instead and are "
         "dimensioned separately.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Flanges", "/flanges/"),
                                   (ft["name"], None)])

    title = fit_title(f"{ft['name']} Dimensions — ASME B16.5", " | PipeData")
    desc = fit_desc(
        f"{ft['name']} ({ft['abbrev']}) dimensions for all "
        f"{len(ft['classes'])} ASME B16.5 pressure classes. ",
        ["Outside diameter, thickness, bolt circle and bolting, NPS 1/2 to 24.",
         "OD, thickness, bolt circle and bolting from NPS 1/2 to NPS 24.",
         "OD, thickness, bolt circle and bolting tables.",
         "Full dimension and bolting tables."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(ft["name"])} Dimensions</h1>'
            f'<p class="lede">{esc(ft["blurb"])}</p></div>'
            + facts([("Standard", "ASME B16.5"),
                     ("Abbreviation", esc(ft["abbrev"])),
                     ("Pressure classes", str(len(ft["classes"]))),
                     ("Size range", "NPS 1/2 – NPS 24"),
                     ("Typical service", esc(ft["use"]))])
            + '<h2>Dimensions by pressure class</h2>'
            f'<div class="grid">{cards}</div>'
            + '<div class="two-col"><div><h3>Strengths</h3><ul class="tick">'
            + pros + '</ul></div><div><h3>Limitations</h3><ul class="cross">'
            + cons + "</ul></div></div>"
            + f'<h2>Class 150 dimensions</h2>' + UNITS_NOTE
            + table(["Size", "Flange OD", "Thickness", "Bolt circle", "Bolts",
                     "Bolt dia.", "Raised face OD"], rows,
                    caption=f"ASME B16.5 Class 150 {ft['short']} flange "
                            f"dimensions — the most commonly specified class.",
                    note=f'Higher classes are on the '
                         f'<a href="/flanges/{ft["slug"]}/class-300/">Class 300</a> '
                         f'page and above.')
            + faq_html + "</div>")

    url = f"/flanges/{ft['slug']}/"
    page(url, title, desc, body,
         ld=[crumb_ld, faq_ld,
             item_list([(f"Class {c}", f"/flanges/{ft['slug']}/class-{c}/")
                        for c in ft["classes"]], f"{ft['name']} pressure classes")])
    index_entry(ft["name"], url, f"ASME B16.5 · {ft['abbrev']}")


CLASS_ORDER = ["150", "300", "400", "600", "900", "1500", "2500"]
STEEL_LB_IN3 = 0.2836   # carbon steel density, for the blind-flange estimate


def class_rating(cls):
    """Class rating at 100 °F for A105 carbon steel (material group 1.1)."""
    for g in PT_GROUPS:
        if g["slug"] == "1-1" and cls in g["ratings"]:
            return g["ratings"][cls][0]
    return None


def rows_for_nps(b165, nps):
    """[(class, row, block)] for every B16.5 class that publishes this size."""
    out = []
    for c in CLASS_ORDER:
        blk = b165["classes"].get(c)
        if not blk:
            continue
        r = next((x for x in blk["rows"] if x["nps"] == nps), None)
        if r:
            out.append((c, r, blk))
    return out


def flange_nps_page(nps, b165, ftypes, sizes_by_nps):
    """Every B16.5 class for one pipe size, on one page."""
    slug = nps_slug(nps)
    rf = b165["raised_face"]
    found = rows_for_nps(b165, nps)
    c150 = found[0][1]

    rows = []
    for c, r, blk in found:
        rating = class_rating(c)
        rows.append([
            f'<a href="/flanges/weld-neck/class-{c}/nps-{slug}/">'
            f'<strong>Class {c}</strong></a>',
            dual(r["o"], 2), dual(r["tf"], 2), dual(r["bc"], 2),
            f'{r["bolts"]} × {esc(r["bolt"])} in',
            dual(rf[nps], 2) if nps in rf else '<span class="na">—</span>',
            f"{rating} psig" if rating else '<span class="na">—</span>',
        ])

    hub_rows = []
    for c, r, blk in found:
        if not (r.get("y_wn") or r.get("y_so")):
            continue
        hub_rows.append([
            f"<strong>Class {c}</strong>",
            dual(r["y_wn"], 2) if r.get("y_wn") else '<span class="na">—</span>',
            dual(r["y_so"], 2) if r.get("y_so") else '<span class="na">—</span>',
            inch_mm(blk["rf_height"], 2),
        ])
    hub_block = ""
    if hub_rows:
        hub_block = (
            "<h2>Length through hub</h2>"
            "<p>How far the flange stands off the joint, which is what a spool "
            "drawing needs. B16.5 carries hub length for Classes 150 and 300 "
            "only; above that the hub follows the bore and varies with the "
            "schedule the flange is ordered to.</p>"
            + table(["Class", "Weld neck", "Slip-on / threaded",
                     "Raised face height"], hub_rows,
                    caption=f"ASME B16.5 length-through-hub, NPS {esc(nps)}."))

    pipe = sizes_by_nps.get(nps)
    pipe_block = ""
    if pipe:
        std = pipe["walls"]["STD"]
        pipe_block = (
            f'<h2>The pipe these flanges bolt to</h2>'
            f'<p>NPS {esc(nps)} pipe is {inch_mm(pipe["od"])} outside diameter '
            f'in every schedule, which is why one flange OD serves the whole '
            f'range. Standard weight is a {inch_mm(std)} wall, giving a '
            f'{inch_mm(inside_dia(pipe["od"], std))} bore — the figure a weld '
            f'neck flange in this size is normally bored to.</p>'
            f'<p><a class="more" href="{pipe["url"]}">NPS {esc(nps)} pipe '
            f'dimensions in every schedule →</a></p>')

    type_chips = "".join(
        f'<a class="chip-link" href="/flanges/{t["slug"]}/">{esc(t["name"])}</a>'
        for t in ftypes)

    biggest = found[-1]
    q = [
        (f"What is the bolt circle of an NPS {nps} flange?",
         "<p>" + "; ".join(f"Class {c} is {n(r['bc'], 2)} in with {r['bolts']} × "
                           f"{esc(r['bolt'])} in bolts" for c, r, _ in found[:4])
         + f". The bolt circle grows with the class, which is why an NPS "
           f"{esc(nps)} flange of one class will not bolt to another.</p>"),
        (f"Do all six flange types share these NPS {nps} dimensions?",
         "<p>Yes. Flange outside diameter, thickness, bolt circle, bolt count "
         "and bolt size are set by NPS and class alone — a weld neck, slip-on, "
         "threaded, lap joint, socket weld and blind of the same size and class "
         "are dimensionally interchangeable at the bolted joint. What differs "
         "is how each attaches to the pipe.</p>"),
        (f"How much bigger is a Class {biggest[0]} flange than a Class 150 in "
         f"NPS {nps}?",
         f"<p>{n(biggest[1]['o'], 2)} in outside diameter against "
         f"{n(c150['o'], 2)} in, and {n(biggest[1]['tf'], 2)} in thick against "
         f"{n(c150['tf'], 2)} in — {n(biggest[1]['tf'] / c150['tf'], 1)}× the "
         f"metal in the flange ring, plus heavier bolting on a wider bolt "
         f"circle.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Flanges", "/flanges/"),
                                   (f"NPS {nps}", None)])

    title = fit_title(f"NPS {nps} Flange Dimensions — ASME B16.5", " | PipeData")
    desc = fit_desc(
        f"NPS {nps} flange dimensions for all {len(found)} ASME B16.5 pressure "
        f"classes. Class 150: {n(c150['o'], 2)} in OD, {n(c150['tf'], 2)} in "
        f"thick, {c150['bolts']} × {c150['bolt']} in bolts. ",
        ["Bolt circle, raised face and hub data.",
         "Bolt circle and raised face OD.",
         "With bolt circle data."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>NPS {esc(nps)} Flange Dimensions</h1>'
            f'<p class="lede">Outside diameter, thickness, bolt circle and '
            f'bolting for every ASME B16.5 pressure class published in NPS '
            f'{esc(nps)} — Class {found[0][0]} through Class {found[-1][0]}.'
            f'</p></div>'
            + facts([("Standard", "ASME B16.5"),
                     ("Nominal size", f"NPS {esc(nps)}"),
                     ("Classes published", str(len(found))),
                     ("Class 150 flange OD", inch_mm(c150["o"], 2)),
                     ("Class 150 bolting",
                      f"{c150['bolts']} × {esc(c150['bolt'])} in")])
            + UNITS_NOTE
            + table(["Class", "Flange OD", "Thickness", "Bolt circle",
                     "Bolting", "Raised face OD", "Rating @ 100 °F"], rows,
                    caption=f"ASME B16.5 flange dimensions in NPS {esc(nps)}, "
                            f"all published pressure classes.",
                    note="Ratings are for A105 carbon steel at 100 °F and fall "
                         "with temperature. Thickness excludes the raised face.")
            + hub_block + pipe_block
            + '<h2>Flange types in this size</h2>'
            f'<div class="chip-links">{type_chips}</div>'
            + faq_html + "</div>")

    url = f"/flanges/nps-{slug}/"
    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"NPS {nps} flanges", url,
                f"ASME B16.5 · {len(found)} classes · Class 150 OD "
                f"{n(c150['o'], 2)} in")


def flange_detail_page(ft, cls, r, blk, b165, sizes_by_nps, ftypes):
    """One flange type x class x size. Only for types where the size-level
    detail is genuinely different: weld neck (bore follows the schedule) and
    blind (no bore, so weight and end thrust are the questions asked)."""
    nps = r["nps"]
    slug = nps_slug(nps)
    rf = b165["raised_face"].get(nps)
    rating = class_rating(cls)
    short_t = ft["short"].title()

    spacing = math.pi * r["bc"] / r["bolts"]
    bolt_dia = nps_value(r["bolt"])

    dim_rows = [
        ["Flange outside diameter", dual(r["o"], 2)],
        ["Flange thickness (min)", dual(r["tf"], 2)],
        ["Bolt circle diameter", dual(r["bc"], 2)],
        ["Number of bolts", str(r["bolts"])],
        ["Bolt diameter (nominal)", esc(r["bolt"]) + " in"],
        ["Bolt hole diameter", dual(bolt_dia + 0.125, 3)],
        ["Bolt hole spacing on the circle", dual(spacing, 2)],
        ["Raised face outside diameter",
         dual(rf, 2) if rf else '<span class="na">—</span>'],
        ["Raised face height", dual(blk["rf_height"], 2)],
    ]
    if r.get("y_wn") and ft["slug"] == "weld-neck":
        dim_rows.append(["Length through hub", dual(r["y_wn"], 2)])

    # ---- the type-specific block, which is why these pages exist ----
    extra = ""
    pipe = sizes_by_nps.get(nps)
    if ft["slug"] == "weld-neck" and pipe:
        order = [x for x in SCHEDULE_ORDER if x in pipe["walls"]]
        bore_rows = []
        for x in order:
            tx = pipe["walls"][x]
            bore_rows.append([
                f'<a href="/pipes/nps-{slug}/{sched_slug(x)}/">'
                f'<strong>{sched_label(x)}</strong></a>',
                dual(tx), dual(inside_dia(pipe["od"], tx)),
                dual_w(weight_lbft(pipe["od"], tx)),
            ])
        extra = (
            "<h2>Bore: order the flange to the schedule</h2>"
            f"<p>A weld neck flange is bored to match the pipe it is butt-welded "
            f"to, so NPS {esc(nps)} Class {cls} is not one part number but "
            f"{len(order)} — one for each schedule B36.10M publishes in this "
            f"size. Everything in the table above is common to all of them; the "
            f"bore below is not. Specifying &ldquo;NPS {esc(nps)} Class {cls} "
            f"weld neck&rdquo; without the schedule is an incomplete "
            f"requisition.</p>"
            + table(["Schedule", "Pipe wall", "Flange bore = pipe ID",
                     "Pipe weight"], bore_rows,
                    caption=f"Weld neck flange bore by schedule, NPS "
                            f"{esc(nps)}, from the matching ASME B36.10M pipe.",
                    note="A bore mismatch at a butt weld leaves a step in the "
                         "flow path and a stress raiser at the root — the "
                         "defect the weld neck exists to avoid.")
        )
    elif ft["slug"] == "blind":
        vol = math.pi / 4 * r["o"] ** 2 * r["tf"]
        wt = vol * STEEL_LB_IN3
        thrust = None
        if rating and rf:
            thrust = rating * math.pi / 4 * rf ** 2
        rows2 = [["Approximate weight",
                  f"{n(wt, 1)} lb ({n(wt * 0.453592, 1)} kg)"]]
        if thrust:
            rows2.append(["End thrust at rated pressure",
                          f"{n(thrust, 0)} lbf ({n(thrust * 0.004448, 1)} kN)"])
            rows2.append(["Load per bolt",
                          f"{n(thrust / r['bolts'], 0)} lbf"])
        extra = (
            "<h2>Weight and end load</h2>"
            f"<p>A blind flange has no bore, so its outside diameter and "
            f"thickness are the whole of its dimensional definition — and its "
            f"weight and the load it carries are the two things people actually "
            f"need. Both are calculated here from the B16.5 figures above.</p>"
            + table(["Quantity", "NPS " + esc(nps) + " Class " + cls], rows2,
                    caption=f"Derived figures for the NPS {esc(nps)} Class "
                            f"{cls} blind flange.",
                    note="Weight is a solid-disc estimate from the outside "
                         "diameter and the minimum thickness at "
                         "0.2836 lb/in³, and ignores the raised face, so it "
                         "runs slightly light against a real forging. End "
                         "thrust is the rated pressure acting on the raised "
                         "face area; it is not a bolt-load calculation, which "
                         "must also cover gasket seating.")
        )

    other_classes = "".join(
        f'<a class="chip-link" href="/flanges/{ft["slug"]}/class-{c}/nps-{slug}/">'
        f'Class {c}</a>'
        for c, _, _ in rows_for_nps(b165, nps) if c != cls)
    sibling = "blind" if ft["slug"] == "weld-neck" else "weld-neck"
    sibling_name = next(t["name"] for t in ftypes if t["slug"] == sibling)

    q = [
        (f"What are the bolt holes on an NPS {nps} Class {cls} flange?",
         f"<p>{r['bolts']} holes of {n(bolt_dia + 0.125, 3)} in diameter on a "
         f"{n(r['bc'], 2)} in bolt circle, taking {esc(r['bolt'])} in bolts. "
         f"B16.5 holes are 1/8 in larger than the nominal bolt. They straddle "
         f"the vertical and horizontal centrelines rather than sitting on them, "
         f"so hole spacing round the circle is {n(spacing, 2)} in.</p>"),
        (f"What pressure is an NPS {nps} Class {cls} flange good for?",
         (f"<p>{psi_bar(rating)} at 100 °F in A105 carbon steel, falling as "
          f"metal temperature rises. Class {cls} is a designation, not a "
          f"pressure — see the <a href='/reference/"
          f"pressure-temperature-ratings/'>P-T tables</a> for your material and "
          f"temperature.</p>") if rating else
         "<p>Class is a designation, not a pressure. See the P-T rating tables "
         "for the allowable pressure at your material and temperature.</p>"),
        (f"Is a Class {cls} {ft['short']} flange interchangeable with another "
         f"type?",
         f"<p>At the bolted joint, yes: every B16.5 type in NPS {esc(nps)} "
         f"Class {cls} shares the {n(r['o'], 2)} in outside diameter, "
         f"{n(r['bc'], 2)} in bolt circle and {r['bolts']} × "
         f"{esc(r['bolt'])} in bolting, so any two will mate. What is not "
         f"interchangeable is how they join the pipe, and therefore what "
         f"service they suit.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    crumb_html, crumb_ld = crumbs([
        ("Home", "/"), ("Flanges", "/flanges/"),
        (ft["name"], f"/flanges/{ft['slug']}/"),
        (f"Class {cls}", f"/flanges/{ft['slug']}/class-{cls}/"),
        (f"NPS {nps}", None)])

    title = fit_title(f"NPS {nps} Class {cls} {short_t} Flange Dimensions",
                      " | PipeData")
    tail = ("Bore by schedule, bolting and gasket data."
            if ft["slug"] == "weld-neck" else
            "Approximate weight, end thrust and bolting.")
    desc = fit_desc(
        f"NPS {nps} Class {cls} {ft['short']} flange: {n(r['o'], 2)} in OD, "
        f"{n(r['tf'], 2)} in thick, {n(r['bc'], 2)} in bolt circle, "
        f"{r['bolts']} × {r['bolt']} in bolts. ",
        [tail, "Bolting and gasket dimensions.", "Full ASME B16.5 data."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head">'
            f'<h1>NPS {esc(nps)} Class {cls} {esc(ft["name"])}</h1>'
            f'<p class="lede">ASME B16.5 dimensions for one size and class: '
            f'{inch_mm(r["o"], 2)} outside diameter, {inch_mm(r["tf"], 2)} '
            f'thick, on a {inch_mm(r["bc"], 2)} bolt circle with {r["bolts"]} × '
            f'{esc(r["bolt"])} in bolts.</p></div>'
            + UNITS_NOTE
            + table(["Dimension", f"NPS {esc(nps)} Class {cls}"], dim_rows,
                    caption=f"ASME B16.5 dimensions, NPS {esc(nps)} Class "
                            f"{cls} {ft['short']} flange.",
                    note="Thickness excludes the raised face. Bolt hole "
                         "diameter and spacing are derived from the B16.5 bolt "
                         "size and bolt circle.")
            + extra + faq_html
            + f'<h2>Same size, other classes</h2>'
            f'<div class="chip-links">{other_classes}</div>'
            + '<h2>Related</h2><div class="chip-links">'
            f'<a class="chip-link" href="/flanges/nps-{slug}/">All NPS '
            f'{esc(nps)} flanges</a>'
            f'<a class="chip-link" href="/flanges/{ft["slug"]}/class-{cls}/">'
            f'Class {cls} {esc(ft["short"])} in every size</a>'
            f'<a class="chip-link" href="/flanges/{sibling}/class-{cls}/'
            f'nps-{slug}/">{esc(sibling_name)}</a>'
            + (f'<a class="chip-link" href="/pipes/nps-{slug}/">NPS {esc(nps)} '
               f'pipe</a>' if pipe else "")
            + '<a class="chip-link" href="/reference/flange-bolt-chart/">'
            'Bolt chart</a></div></div>')

    url = f"/flanges/{ft['slug']}/class-{cls}/nps-{slug}/"
    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"NPS {nps} Class {cls} {ft['short']}", url,
                f"OD {n(r['o'], 2)} in · {r['bolts']} × {esc(r['bolt'])} in bolts")


def flanges_index(ftypes, b165):
    cards = "".join(
        f'<a class="card" href="/flanges/{t["slug"]}/">'
        f'<span class="card-title">{esc(t["name"])}</span>'
        f'<span class="card-meta">{esc(t["abbrev"])} · '
        f'{len(t["classes"])} classes</span>'
        f'<span class="card-spec">{esc(t["use"])}</span></a>'
        for t in ftypes)

    # class comparison at NPS 6, the size that shows the spread best
    comp_rows = []
    for c in ["150", "300", "400", "600", "900", "1500", "2500"]:
        r = next((x for x in b165["classes"][c]["rows"] if x["nps"] == "6"), None)
        if not r:
            continue
        rating = next(g["ratings"][c][0] for g in PT_GROUPS if g["slug"] == "1-1")
        comp_rows.append([
            f'<strong>Class {c}</strong>', dual(r["o"], 2), dual(r["tf"], 2),
            dual(r["bc"], 2), f'{r["bolts"]} × {esc(r["bolt"])} in',
            f'{rating} psig'])

    all_sizes = sorted({r["nps"] for blk in b165["classes"].values()
                        for r in blk["rows"]}, key=nps_value)
    size_cards = "".join(
        f'<a class="card" href="/flanges/nps-{nps_slug(x)}/">'
        f'<span class="card-title">NPS {esc(x)}</span>'
        f'<span class="card-meta">{len(rows_for_nps(b165, x))} classes</span>'
        f'</a>' for x in all_sizes)

    q = [
        ("What does the class number on a flange mean?",
         "<p>It is a dimensional and rating designation, not a pressure. A "
         "Class 300 flange is not rated for 300 psi — in A105 carbon steel it is "
         "rated 740 psig at 100 °F, and 570 psig at 600 °F. The number derives "
         "from the historic rating of the class in saturated steam service.</p>"),
        ("How do I know which flange type I need?",
         "<p>Work down from service severity. High pressure, cyclic loading or "
         "high temperature calls for a weld neck. Ordinary low-pressure utility "
         "service can take a slip-on. Small-bore high-pressure lines use socket "
         "weld. Frequent dismantling or expensive alloy favours lap joint. "
         "Non-weldable locations get threaded. Closures get a blind.</p>"),
        ("Where does B16.5 stop and B16.47 start?",
         "<p>ASME B16.5 covers NPS 1/2 through NPS 24. From NPS 26 up, flanges "
         "are covered by <a href='/flanges/large/'>ASME B16.47</a>, which has two "
         "non-interchangeable series — Series A and Series B.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Flanges", None)])

    body = (crumb_html + '<div class="wrap">'
            '<div class="page-head"><h1>Flange Dimensions — ASME B16.5</h1>'
            '<p class="lede">Six flange types across seven pressure classes, '
            'NPS 1/2 through NPS 24. Outside diameter, thickness, bolt circle, '
            'bolt count and bolt size for every combination.</p></div>'
            f'<div class="grid">{cards}</div>'
            + '<h2>What the pressure class buys you</h2>'
            '<p>The same NPS 6 flange across all seven classes, so the jump in '
            'metal and bolting is visible at a glance. Ratings are for A105 '
            'carbon steel at 100 °F.</p>'
            + UNITS_NOTE
            + table(["Class", "Flange OD", "Thickness", "Bolt circle",
                     "Bolting", "Rating @ 100 °F"], comp_rows,
                    caption="NPS 6 flange dimensions by pressure class, "
                            "ASME B16.5.")
            + '<h2>Browse by size</h2>'
            '<p>Every pressure class published in one nominal size, on one '
            'page — the quickest route if you already know the size and need '
            'to compare classes.</p>'
            f'<div class="grid tight">{size_cards}</div>'
            + '<h2>Larger than NPS 24</h2>'
            '<p>Flanges from NPS 26 to NPS 60 fall under ASME B16.47, in two '
            'series that will not bolt to each other. '
            '<a class="more" href="/flanges/large/">Large diameter flanges →</a></p>'
            + faq_html + "</div>")

    title = "Flange Dimensions Chart — ASME B16.5 | PipeData"
    desc = ("ASME B16.5 flange dimensions for all six types and seven pressure "
            "classes, NPS 1/2 to 24: outside diameter, thickness, bolt circle, "
            "bolt count and size.")
    page("/flanges/", title, desc, body,
         ld=[crumb_ld, faq_ld,
             item_list([(t["name"], f"/flanges/{t['slug']}/") for t in ftypes],
                       "ASME B16.5 flange types")])
    index_entry("Flange dimensions index", "/flanges/",
                "ASME B16.5 · 6 types · 7 classes")


def large_flange_pages(b1647):
    combos = b1647["combinations"]
    tabulated = [c for c in combos if c["tabulated"]]

    # ---- index ----
    rows = []
    for c in combos:
        s = b1647["series"][c["series"]]
        key = f'{c["series"]}-{c["class"]}'
        if c["tabulated"]:
            link = (f'<a href="/flanges/large/series-{c["series"]}-class-'
                    f'{c["class"]}/">Series {c["series"].upper()} Class '
                    f'{c["class"]}</a>')
            state = '<span class="yes">Full table</span>'
        else:
            link = f'Series {c["series"].upper()} Class {c["class"]}'
            state = '<span class="na">Not tabulated here</span>'
        rows.append([f"<strong>{link}</strong>", esc(s["origin"]),
                     esc(s["sizes"]), state])

    cards = "".join(
        f'<a class="card" href="/flanges/large/series-{k}/">'
        f'<span class="card-title">{esc(v["name"])}</span>'
        f'<span class="card-meta">from {esc(v["origin"])}</span>'
        f'<span class="card-spec">{len(v["classes"])} classes · '
        f'{esc(v["sizes"])}</span></a>'
        for k, v in b1647["series"].items())

    q = [
        ("Can a Series A flange bolt to a Series B flange?",
         "<p>No. At the same NPS and class the two series have different outside "
         "diameters, different bolt circles and different bolt counts. They are "
         "separate, non-interchangeable products that happen to live in the same "
         "standard. Always state the series on the requisition.</p>"),
        ("Which B16.47 series should I specify?",
         "<p>Series A is the heavier and the usual default for new North "
         "American refinery, power and pipeline work. Series B is lighter and "
         "cheaper but less rigid; it is normally chosen only to match existing "
         "API 605 flanges already in the plant.</p>"),
        ("What sizes does ASME B16.47 cover?",
         "<p>NPS 26 through NPS 60 in both series. Below NPS 26, flanges are "
         "covered by <a href='/flanges/'>ASME B16.5</a> instead — the two "
         "standards meet at NPS 24/26 with no overlap.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Flanges", "/flanges/"),
                                   ("Large diameter", None)])

    body = (crumb_html + '<div class="wrap">'
            '<div class="page-head"><h1>Large Diameter Flanges — ASME B16.47</h1>'
            '<p class="lede">NPS 26 through NPS 60 in two non-interchangeable '
            'series. Series A comes from MSS SP-44 and is the heavier; Series B '
            'comes from API 605 and is the lighter.</p></div>'
            f'<div class="grid">{cards}</div>'
            + '<div class="callout warn"><p><strong>The two series do not '
            'mate.</strong> A Series A and a Series B flange of the same NPS and '
            'class have different bolt circles and bolt counts. Specifying '
            '&ldquo;NPS 36 Class 150 B16.47&rdquo; without the series is an '
            'incomplete specification.</p></div>'
            + '<h2>Series and class coverage</h2>'
            + table(["Series and class", "Derived from", "Sizes",
                     "On PipeData"], rows,
                    caption="ASME B16.47 series and pressure classes.")
            + '<div class="callout"><p><strong>Coverage note.</strong> PipeData '
            'publishes the full dimensional table for Series A Class 150. The '
            'remaining series and class combinations are described here but not '
            'tabulated — read them from a current copy of ASME B16.47 rather '
            'than from a secondary source.</p></div>'
            + faq_html + "</div>")

    page("/flanges/large/",
         "Large Diameter Flanges — ASME B16.47 | PipeData",
         "ASME B16.47 covers flanges NPS 26 to NPS 60 in two non-interchangeable "
         "series: Series A from MSS SP-44 and Series B from API 605. Dimensions "
         "and bolting.",
         body, ld=[crumb_ld, faq_ld])
    index_entry("Large diameter flanges", "/flanges/large/",
                "ASME B16.47 · NPS 26–60")

    # ---- per-series ----
    for key, s in b1647["series"].items():
        my = [c for c in combos if c["series"] == key]
        other_key = "b" if key == "a" else "a"
        other = b1647["series"][other_key]
        rows = []
        for c in my:
            if c["tabulated"]:
                link = (f'<a href="/flanges/large/series-{key}-class-'
                        f'{c["class"]}/">Class {c["class"]}</a> — full table')
            else:
                link = f'Class {c["class"]} — see ASME B16.47'
            rows.append([f"<strong>{link}</strong>"])

        cls_links = "".join(
            f'<a class="chip-link" href="/flanges/large/series-{key}-class-'
            f'{c["class"]}/">Class {c["class"]}</a>'
            for c in my if c["tabulated"])

        q2 = [
            (f"What is ASME B16.47 {s['name']}?",
             f"<p>{esc(s['blurb'])}</p>"),
            (f"What classes does {s['name']} come in?",
             f"<p>{comma_list(['Class ' + c for c in s['classes']])}, in sizes "
             f"{s['sizes'].lower()}. {other['name']} carries "
             f"{comma_list(['Class ' + c for c in other['classes']])} instead, "
             f"which is one reason the two are easy to confuse on a "
             f"requisition.</p>"),
        ]
        faq2_html, faq2_ld = faq(q2)
        cr_html, cr_ld = crumbs([
            ("Home", "/"), ("Flanges", "/flanges/"),
            ("Large diameter", "/flanges/large/"), (s["name"], None)])

        b = (cr_html + '<div class="wrap">'
             f'<div class="page-head"><h1>ASME B16.47 {esc(s["name"])} Flanges</h1>'
             f'<p class="lede">{esc(s["blurb"])}</p></div>'
             + facts([("Standard", "ASME B16.47"), ("Series", esc(s["name"])),
                      ("Derived from", esc(s["origin"])),
                      ("Sizes", esc(s["sizes"])),
                      ("Classes", comma_list(s["classes"]))])
             + ('<h2>Dimension tables</h2>'
                f'<div class="chip-links">{cls_links}</div>' if cls_links else "")
             + '<div class="callout warn"><p><strong>Not interchangeable with '
               f'{esc(other["name"])}.</strong> The two series differ in outside '
               'diameter, bolt circle and bolt count at every size and class. '
               'Never mix them in a joint.</p></div>'
             + faq2_html
             + f'<p><a class="more" href="/flanges/large/series-{other_key}/">'
               f'Compare with {esc(other["name"])} →</a></p></div>')

        t = fit_title(f"ASME B16.47 {s['name']} Flange Dimensions", " | PipeData")
        d = fit_desc(
            f"ASME B16.47 {s['name']} large diameter flanges derive from "
            f"{s['origin']}, covering {s['sizes'].lower()} in ",
            [f"{comma_list(['Class ' + c for c in s['classes']])}. Not "
             f"interchangeable with {other['name']}.",
             f"{len(s['classes'])} pressure classes. Not interchangeable with "
             f"{other['name']}.",
             f"{len(s['classes'])} pressure classes."])
        page(f"/flanges/large/series-{key}/", t, d, b, ld=[cr_ld, faq2_ld])
        index_entry(f"B16.47 {s['name']}", f"/flanges/large/series-{key}/",
                    f"{s['origin']} · {s['sizes']}")

    # ---- tabulated combinations ----
    for c in tabulated:
        key = f'{c["series"]}-{c["class"]}'
        s = b1647["series"][c["series"]]
        tbl = b1647["tables"][key]
        rows = [[f'<strong>NPS {r["nps"]}</strong>', dual(r["o"], 2),
                 dual(r["tf"], 2), dual(r["bc"], 2), str(r["bolts"]),
                 esc(r["bolt"]) + " in"] for r in tbl["rows"]]
        first, last = tbl["rows"][0], tbl["rows"][-1]
        other_key = "b" if c["series"] == "a" else "a"

        q3 = [
            (f"How many bolts does an NPS 36 Series {c['series'].upper()} "
             f"Class {c['class']} flange take?",
             "<p>" + next(
                 f"{r['bolts']} bolts of {esc(r['bolt'])} in diameter, on a "
                 f"{n(r['bc'], 2)} in ({n(r['bc'] * MM, 1)} mm) bolt circle."
                 for r in tbl["rows"] if r["nps"] == 36) + "</p>"),
            ("Are these dimensions the same as Series "
             f"{other_key.upper()}?",
             "<p>No. Series A and Series B differ in outside diameter, bolt "
             "circle and bolt count at every size. The two will not bolt "
             "together, and a gasket for one will not suit the other.</p>"),
        ]
        f3_html, f3_ld = faq(q3)
        cr_html, cr_ld = crumbs([
            ("Home", "/"), ("Flanges", "/flanges/"),
            ("Large diameter", "/flanges/large/"),
            (s["name"], f"/flanges/large/series-{c['series']}/"),
            (f"Class {c['class']}", None)])

        b = (cr_html + '<div class="wrap">'
             f'<div class="page-head"><h1>B16.47 {esc(s["name"])} Class '
             f'{c["class"]} Dimensions</h1>'
             f'<p class="lede">Flange outside diameter, thickness, bolt circle '
             f'and bolting for NPS {first["nps"]} through NPS {last["nps"]}, '
             f'ASME B16.47 {s["name"]} Class {c["class"]}.</p></div>'
             + facts([("Standard", "ASME B16.47"), ("Series", esc(s["name"])),
                      ("Class", f"Class {c['class']}"),
                      ("Sizes", f"NPS {first['nps']} – NPS {last['nps']}"),
                      ("Largest flange OD", inch_mm(last["o"], 2))])
             + UNITS_NOTE
             + table(["Size", "Flange OD", "Thickness", "Bolt circle", "Bolts",
                      "Bolt dia."], rows,
                     caption=f"ASME B16.47 {s['name']} Class {c['class']} "
                             f"flange dimensions.",
                     note="Thickness is the minimum required by the standard "
                          "and excludes the raised face.")
             + f3_html + "</div>")

        t = fit_title(f"B16.47 Series {c['series'].upper()} Class "
                      f"{c['class']} Flange Dimensions", " | PipeData")
        d = fit_desc(
            f"ASME B16.47 {s['name']} Class {c['class']} flange dimensions for "
            f"NPS {first['nps']} to NPS {last['nps']}: ",
            ["outside diameter, thickness, bolt circle, bolt count and bolt "
             "size.",
             "OD, thickness, bolt circle, bolt count and bolt size.",
             "OD, thickness, bolt circle and bolting."])
        page(f"/flanges/large/series-{c['series']}-class-{c['class']}/",
             t, d, b, ld=[cr_ld, f3_ld])
        index_entry(f"B16.47 Series {c['series'].upper()} Class {c['class']}",
                    f"/flanges/large/series-{c['series']}-class-{c['class']}/",
                    f"NPS {first['nps']}–{last['nps']}")


# --------------------------------------------------------------------------
# fitting pages
# --------------------------------------------------------------------------

def fitting_page(f, fittings, pipes):
    od_by_nps = {s["nps"]: s["od"] for s in pipes["sizes"]}
    keys = sorted(f["rows"], key=nps_value)
    rows = []
    for k in keys:
        v = f["rows"][k]
        od = od_by_nps.get(k)
        rows.append([
            f'<strong>NPS {esc(k)}</strong>',
            f'<a href="/pipes/nps-{nps_slug(k)}/">{n(od, 3)} in</a>'
            if od else '<span class="na">—</span>',
            dual(v, 2),
        ])

    smallest, largest = keys[0], keys[-1]
    others = "".join(
        f'<a class="chip-link" href="/fittings/{o["slug"]}/">{esc(o["name"])}</a>'
        for o in fittings if o["slug"] != f["slug"])

    formula_row = [("Dimension formula", esc(f["formula"]))] if f["formula"] else []

    q = [
        (f"What is the {f['dim_label'].lower()} of an NPS 8 {f['short']}?",
         f"<p>{inch_mm(f['rows']['8'], 2)}"
         + (f", following {esc(f['formula'])}." if f["formula"] else ".")
         + " ASME B16.9 dimensions depend on NPS only, so this figure is the "
           "same in every schedule.</p>")
        if "8" in f["rows"] else
        (f"How is a {f['short']} dimensioned?",
         f"<p>By its {f['dim_label'].lower()}, in inches, as a function of NPS "
         f"alone.</p>"),
        (f"Does the {f['short']} dimension change with wall thickness?",
         "<p>No. ASME B16.9 sets fitting dimensions from NPS alone. A schedule "
         "40 and a schedule 160 fitting of the same size have identical "
         "centre-to-end dimensions — only the wall thickness and the bore "
         "differ, so the fitting still matches its pipe at the weld.</p>"),
        (f"What material are {f['short']}s made from?",
         "<p>Carbon steel fittings are normally ASTM A234 WPB, matched to A106 "
         "Gr B pipe. Low-temperature service uses A420 WPL6, stainless uses "
         "A403 WP304/WP316, and chrome-moly uses A234 WP11, WP22 or WP91. See "
         "the <a href='/reference/material-grades/'>material grade "
         "reference</a>.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Fittings", "/fittings/"),
                                   (f["name"], None)])

    title = fit_title(f"{f['name']} Dimensions — ASME B16.9", " | PipeData")
    desc = fit_desc(
        f"{f['name']} dimensions from ASME B16.9, NPS {smallest} to "
        f"NPS {largest}. ",
        [f"{f['dim_label']} for every published size, with the matching pipe "
         f"outside diameter alongside it.",
         f"{f['dim_label']} for every size, with matching pipe outside "
         f"diameters.",
         f"{f['dim_label']} for every published size.",
         "Centre-to-end dimensions for every published size."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(f["name"])} Dimensions</h1>'
            f'<p class="lede">{esc(f["blurb"])}</p></div>'
            + facts([("Standard", "ASME B16.9"),
                     ("Dimension", esc(f["dim_label"])),
                     ("Size range", f"NPS {esc(smallest)} – NPS {esc(largest)}"),
                     ("Sizes published", str(len(keys)))] + formula_row)
            + UNITS_NOTE
            + table(["Size", "Matching pipe OD", esc(f["dim_label"])], rows,
                    caption=f"ASME B16.9 {f['name'].lower()} dimensions.",
                    note="B16.9 dimensions are a function of NPS only and do "
                         "not change with schedule or wall thickness.")
            + '<div class="callout"><p><strong>Same size, any schedule.</strong> '
            'Because these dimensions depend on NPS alone, a spool drawing can '
            'be dimensioned before the schedule is fixed. The wall thickness '
            'still has to be ordered to match the pipe, or the weld prep will '
            'not line up.</p></div>'
            + (f'<div class="callout"><p><strong>Not tabulated here.</strong> '
               f'{esc(f["extra"])}</p></div>' if f.get("extra") else "")
            + faq_html
            + f'<h2>Other buttweld fittings</h2>'
            f'<div class="chip-links">{others}</div></div>')

    url = f"/fittings/{f['slug']}/"
    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f["name"], url, f"ASME B16.9 · NPS {smallest}–{largest}")


def fittings_index(fittings):
    cards = "".join(
        f'<a class="card" href="/fittings/{f["slug"]}/">'
        f'<span class="card-title">{esc(f["name"])}</span>'
        f'<span class="card-meta">{esc(f["dim_label"])}</span>'
        f'<span class="card-spec">{len(f["rows"])} sizes</span></a>'
        for f in fittings)

    # cross-fitting comparison at a few common sizes
    sample = ["2", "6", "12", "24"]
    rows = []
    for f in fittings:
        cells = [f'<a href="/fittings/{f["slug"]}/"><strong>{esc(f["name"])}'
                 f'</strong></a>']
        for s in sample:
            v = f["rows"].get(s)
            cells.append(dual(v, 2) if v else '<span class="na">—</span>')
        rows.append(cells)

    q = [
        ("Do buttweld fitting dimensions depend on the pipe schedule?",
         "<p>No. ASME B16.9 fixes every centre-to-end dimension from NPS alone. "
         "Schedule changes the wall thickness and the bore, not the geometry, so "
         "a schedule 10 and a schedule 160 elbow of the same size occupy exactly "
         "the same space.</p>"),
        ("What is the difference between a long radius and short radius elbow?",
         "<p>The centreline bend radius. A long radius elbow bends on 1.5 × NPS, "
         "a short radius on 1.0 × NPS. Long radius is the default: it costs less "
         "pressure drop and erodes more slowly. Short radius is a space-saving "
         "compromise.</p>"),
        ("Which way up does an eccentric reducer go?",
         "<p>Flat side up on pump suction, so vapour cannot collect at the high "
         "point and cavitate the pump. Flat side down on horizontal runs that "
         "must drain, so liquid cannot pool. Getting this backwards is one of "
         "the more common piping errors.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Fittings", None)])

    body = (crumb_html + '<div class="wrap">'
            '<div class="page-head"><h1>Buttweld Fitting Dimensions — B16.9</h1>'
            '<p class="lede">Centre-to-end dimensions for elbows, tees, reducers '
            'and caps, NPS 1/2 through NPS 24. Because ASME B16.9 dimensions '
            'depend on nominal size alone, one table covers every schedule.'
            '</p></div>'
            f'<div class="grid">{cards}</div>'
            + '<h2>Fittings side by side</h2>'
            '<p>The governing dimension of each fitting at four common sizes.</p>'
            + UNITS_NOTE
            + table(["Fitting", "NPS 2", "NPS 6", "NPS 12", "NPS 24"], rows,
                    caption="Governing ASME B16.9 dimension by fitting type. "
                            "Each column is the fitting's own dimension — "
                            "centre-to-end for elbows and tees, end-to-end for "
                            "reducers, length for caps.")
            + faq_html + "</div>")

    title = "Buttweld Fitting Dimensions — ASME B16.9 | PipeData"
    desc = ("ASME B16.9 buttweld fitting dimensions for elbows, tees, reducers "
            "and caps, NPS 1/2 to 24. Centre-to-end dimensions that hold for "
            "every schedule.")
    page("/fittings/", title, desc, body,
         ld=[crumb_ld, faq_ld,
             item_list([(f["name"], f"/fittings/{f['slug']}/") for f in fittings],
                       "ASME B16.9 buttweld fittings")])
    index_entry("Fitting dimensions index", "/fittings/",
                "ASME B16.9 · elbows, tees, reducers, caps")


# --------------------------------------------------------------------------
# reference pages
# --------------------------------------------------------------------------

REFERENCE_PAGES = []


def ref(slug, title, desc, h1, lede, body_html, ld_extra=None, faq_pairs=None,
        card_meta=""):
    """Emit a /reference/<slug>/ page and register it for the reference index."""
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Reference", "/reference/"),
                                   (h1, None)])
    ld = [crumb_ld] + (ld_extra or [])
    extra = ""
    if faq_pairs:
        fh, fl = faq(faq_pairs)
        extra = fh
        ld.append(fl)
    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(h1)}</h1>'
            f'<p class="lede">{lede}</p></div>'
            + body_html + extra + "</div>")
    url = f"/reference/{slug}/"
    page(url, title, desc, body, ld=ld)
    REFERENCE_PAGES.append((h1, url, card_meta))
    index_entry(h1, url, card_meta or "Reference")


def ref_nps_dn(pipes):
    rows = [[f'<strong>NPS {esc(s["nps"])}</strong>', f'DN {s["dn"]}',
             dual(s["od"], 3), n(s["od"] * MM, 1),
             f'<a href="{s["url"]}">dimensions →</a>']
            for s in pipes["sizes"]]

    body = (facts([("Sizes listed", str(len(pipes["sizes"]))),
                   ("Range", "NPS 1/8 – NPS 36"),
                   ("Metric range", "DN 6 – DN 900"),
                   ("Standard", "ASME B36.10M")])
            + "<p>NPS and DN are both <em>designators</em>: labels for a size, "
            "not measurements of it. Neither number is the outside diameter and "
            "neither is the bore. The only dimension that is real is the outside "
            "diameter in the third column, and that is what a tape measure will "
            "find on site.</p>"
            + UNITS_NOTE
            + table(["Nominal pipe size", "Metric designator",
                     "Outside diameter", "OD (mm)", "Full size data"], rows,
                    caption="NPS to DN conversion with true outside diameters, "
                            "ASME B36.10M.")
            + "<h2>The conversion is a lookup, not a calculation</h2>"
            "<p>DN is not NPS × 25.4. DN 50 pipe is NPS 2, and NPS 2 is "
            "2.375 in — which is 60.3 mm, not 50 mm. The DN series was chosen to "
            "be a convenient round-number set that maps one-to-one onto the "
            "existing inch sizes, so the only reliable way to convert is to read "
            "the table.</p>"
            "<h2>Where the numbers start telling the truth</h2>"
            "<p>From NPS 14 upward, NPS equals the outside diameter in inches "
            "exactly: NPS 14 is 14.000 in, NPS 36 is 36.000 in. Below NPS 14 the "
            "designator is inherited from nineteenth-century wrought iron pipe, "
            "where the number described the approximate bore of the thick-walled "
            "pipe of the day. Walls got thinner, bores got bigger, and the label "
            "stayed.</p>")

    q = [
        ("Is DN the same as the pipe diameter in millimetres?",
         "<p>No. DN is a dimensionless designator. DN 50 pipe has an outside "
         "diameter of 60.3 mm, not 50 mm. Treating DN as a measurement is one "
         "of the most common sizing mistakes.</p>"),
        ("How do I convert NPS to DN?",
         "<p>By table lookup only. There is no formula: the two series were "
         "aligned by agreement, not by arithmetic. NPS 2 is DN 50, NPS 4 is "
         "DN 100, NPS 10 is DN 250.</p>"),
        ("What does NPS actually measure?",
         "<p>Nothing, directly. Below NPS 14 it is a historic label that matches "
         "neither the OD nor the bore. From NPS 14 up it happens to equal the "
         "outside diameter in inches.</p>"),
    ]
    ref("nps-dn-conversion",
        "NPS to DN Conversion Chart with Pipe OD | PipeData",
        "NPS to DN conversion for all 30 pipe sizes from NPS 1/8 (DN 6) to "
        "NPS 36 (DN 900), with the true outside diameter in inches and "
        "millimetres for each.",
        "NPS to DN Conversion Chart",
        "Every nominal pipe size from NPS 1/8 to NPS 36 with its metric DN "
        "designator and its true outside diameter — because neither designator "
        "is a measurement of anything.",
        body, faq_pairs=q,
        card_meta=f"{len(pipes['sizes'])} sizes · NPS 1/8 to NPS 36")


def ref_schedule_chart(pipes):
    order = pipes["schedule_order"]
    common = [s for s in pipes["sizes"]
              if s["nps"] in ("1/2", "1", "2", "4", "6", "8", "10", "12",
                              "16", "20", "24")]
    headers = ["Size"] + [sched_label(k) for k in order]
    rows = []
    for s in common:
        cells = [f'<a href="{s["url"]}"><strong>NPS {esc(s["nps"])}</strong></a>']
        for k in order:
            t = s["walls"].get(k)
            cells.append(dual(t, 3) if t else '<span class="na">—</span>')
        rows.append(cells)

    sched_cards = "".join(
        f'<a class="card" href="/pipes/{sched_slug(k)}/">'
        f'<span class="card-title">{sched_long(k)}</span>'
        f'<span class="card-meta">'
        f'{sum(1 for s in pipes["sizes"] if k in s["walls"])} sizes</span></a>'
        for k in order)

    body = (facts([("Schedules in B36.10M", str(len(order))),
                   ("Numbered schedules", "5, 10, 20, 30, 40, 60, 80, 100, 120, 140, 160"),
                   ("Named weights", "STD, XS, XXS"),
                   ("Sizes covered", "NPS 1/8 – NPS 36")])
            + "<h2>What a schedule number means</h2>"
            "<p>A schedule is a wall-thickness series, not a thickness. Within "
            "one schedule the wall grows as the pipe grows, so the pressure the "
            "pipe can hold stays roughly constant across the size range. "
            "Schedule 40 at NPS 1 is 0.133 in; schedule 40 at NPS 24 is "
            "0.688 in. Same schedule, five times the metal.</p>"
            "<p>The original idea, from Barlow's formula, was that schedule "
            "number ≈ 1000 × P/S — working pressure over allowable stress. "
            "Modern schedules have drifted from that derivation and are simply "
            "tabulated values now, but the intent survives: a higher schedule "
            "number is a thicker wall and a higher pressure capability at the "
            "same nominal size.</p>"
            "<h2>Wall thickness across the schedules</h2>"
            + UNITS_NOTE
            + table(headers, rows,
                    caption="ASME B36.10M wall thickness in inches for common "
                            "sizes across every schedule. A dash means the "
                            "standard does not publish that combination.",
                    note='This chart shows 11 representative sizes. Open a '
                         '<a href="/pipes/">size page</a> for the complete list '
                         'of schedules in that NPS.')
            + "<h2>STD, XS and XXS</h2>"
            "<p>Standard weight, extra strong and double extra strong predate "
            "the numbered schedules and still appear on drawings and in "
            "warehouses. They coincide with numbered schedules only over part of "
            "the range:</p>"
            "<ul><li><strong>STD equals schedule 40</strong> from NPS 1/8 "
            "through NPS 10. At NPS 12 they diverge — STD is 0.375 in, schedule "
            "40 is 0.406 in — and above NPS 12, STD is 0.375 in in every "
            "size.</li>"
            "<li><strong>XS equals schedule 80</strong> from NPS 1/8 through "
            "NPS 8. From NPS 10 up, XS is 0.500 in in every size while schedule "
            "80 keeps climbing.</li>"
            "<li><strong>XXS has no numbered equivalent at all.</strong> It is "
            "its own series, published only through NPS 12, and it is not "
            "double the XS wall despite the name.</li></ul>"
            "<h2>The S schedules</h2>"
            "<p>Schedules written with an S suffix — 5S, 10S, 40S, 80S — belong "
            "to ASME B36.19M, the stainless steel pipe standard, not to "
            "B36.10M. The S does not mean a thinner wall: in most sizes an "
            "S-schedule carries exactly the same wall as the numbered schedule "
            "it shares a number with. What differs is the size range B36.19M "
            "publishes, and two specific wall thicknesses — 40S at NPS 12, and "
            "80S at NPS 10 and 12. "
            '<a class="more" href="/reference/stainless-pipe-schedules/">'
            "Full comparison →</a></p>"
            f'<h2>Every schedule</h2><div class="grid">{sched_cards}</div>')

    q = [
        ("Is schedule 40 the same as standard weight?",
         "<p>Only up to NPS 10. At NPS 12 standard weight is 0.375 in and "
         "schedule 40 is 0.406 in, and above NPS 12 standard weight stays at "
         "0.375 in while schedule 40 continues to increase.</p>"),
        ("Is XXS twice the wall thickness of XS?",
         "<p>No, despite the name. XXS is its own tabulated series. At NPS 4, XS "
         "is 0.337 in and XXS is 0.674 in — exactly double there — but at NPS 8, "
         "XS is 0.500 in and XXS is 0.875 in, which is not.</p>"),
        ("What is the difference between schedule 40 and 40S?",
         "<p>Schedule 40 is carbon steel pipe from ASME B36.10M; 40S is "
         "stainless pipe from ASME B36.19M. In the smaller sizes the wall "
         "thicknesses are identical — they part company only at NPS 12, where "
         "40S is 0.375 in and Schedule 40 is 0.406 in — but the S designation "
         "always signals stainless, and B36.19M stops publishing 40S above "
         "NPS 12.</p>"),
        ("Does a higher schedule number mean a smaller bore?",
         "<p>Yes. Outside diameter is fixed for a given NPS, so a thicker wall "
         "can only grow inward. Schedule 160 NPS 4 pipe has a bore of 3.438 in "
         "against schedule 40's 4.026 in — a 27% loss of flow area.</p>"),
    ]
    ref("schedule-chart",
        "Pipe Schedule Chart — Wall Thickness by Schedule | PipeData",
        "Pipe schedule chart with ASME B36.10 wall thickness for every schedule "
        "from 5 to 160, plus STD, XS and XXS, and where each named weight stops "
        "matching a number.",
        "Pipe Schedule Chart",
        "What a schedule number actually means, wall thickness for every "
        "schedule across the common sizes, and exactly where STD, XS and XXS "
        "stop coinciding with the numbered schedules.",
        body, faq_pairs=q, card_meta="14 schedules · wall thickness")


def ref_pt_ratings(pt):
    temps = pt["temperatures"]
    cards = "".join(
        f'<a class="card" href="/reference/pressure-temperature-ratings/'
        f'group-{g["slug"]}/"><span class="card-title">{esc(g["name"])}</span>'
        f'<span class="card-meta">{esc(g["materials"][:60])}…</span>'
        f'<span class="card-spec">{len(g["ratings"])} classes</span></a>'
        for g in pt["groups"])

    g11 = next(g for g in pt["groups"] if g["slug"] == "1-1")
    headers = ["Class"] + [f"{t} °F" for t in temps]
    rows = [[f"<strong>Class {c}</strong>"] + [str(v) for v in g11["ratings"][c]]
            for c in ["150", "300", "400", "600", "900", "1500", "2500"]]

    body = (facts([("Standard", "ASME B16.5"),
                   ("Material groups", str(len(pt["groups"]))),
                   ("Pressure classes", "7"),
                   ("Temperature range", "-20 °F to 1000 °F"),
                   ("Units", "psig")])
            + "<h2>A class is not a pressure</h2>"
            "<p>The single most persistent misconception in flange "
            "specification: a Class 150 flange is not rated for 150 psi. In A105 "
            "carbon steel it is rated 285 psig at 100 °F, and 140 psig at "
            "600 °F. The class number is a designation carried over from the "
            "historic saturated-steam rating of the flange, and the real "
            "allowable pressure comes from the table for your material group at "
            "your metal temperature.</p>"
            "<h2>Carbon steel — Group 1.1 (A105)</h2>"
            "<p>The most-used table on this page: standard carbon steel flanges "
            "at every class and temperature. Pressures are psig.</p>"
            + table(headers, rows,
                    caption="ASME B16.5 pressure-temperature ratings for Group "
                            "1.1 materials (A105, A216 WCB), in psig.",
                    note="Interpolation between listed temperatures is "
                         "permitted. Extrapolation beyond the table is not.")
            + "<h2>Why stainless crosses over carbon steel</h2>"
            "<p>Carbon steel starts higher and falls off a cliff. Austenitic "
            "stainless starts lower — a Class 300 F316 flange is 720 psig at "
            "ambient against carbon steel's 740 — but at 900 °F the stainless is "
            "still holding 395 psig while the carbon steel has dropped to 170. "
            "Above roughly 650 °F the stainless is the stronger flange, which is "
            "why high-temperature service specifies it even where corrosion is "
            "not the concern.</p>"
            f'<h2>All material groups</h2><div class="grid">{cards}</div>')

    q = [
        ("What pressure is a Class 150 flange rated for?",
         "<p>285 psig at 100 °F in A105 carbon steel, falling to 140 psig at "
         "600 °F and 20 psig at 1000 °F. In 316 stainless it is 275 psig at "
         "100 °F. The class number itself is not a pressure.</p>"),
        ("Can I interpolate between temperatures in the table?",
         "<p>Yes. ASME B16.5 explicitly permits linear interpolation between "
         "listed temperatures. It does not permit extrapolation beyond the ends "
         "of the table.</p>"),
        ("Which temperature do I use — the fluid or the metal?",
         "<p>The metal temperature of the pressure-retaining part. For "
         "uninsulated pipe carrying a hot fluid these are close enough that the "
         "fluid temperature is used, but where they differ meaningfully the "
         "flange is rated on its own metal.</p>"),
        ("Do these ratings apply to the pipe as well as the flange?",
         "<p>No. B16.5 rates flanges and flanged fittings. Pipe pressure "
         "capability is calculated from wall thickness and allowable stress "
         "under the applicable code, usually ASME B31.3 or B31.1, and is often "
         "the higher of the two — leaving the flange as the limiting "
         "component.</p>"),
    ]
    ref("pressure-temperature-ratings",
        "ASME B16.5 Pressure-Temperature Rating Chart | PipeData",
        "ASME B16.5 pressure-temperature ratings in psig for all seven flange "
        "classes across four material groups, -20 °F to 1000 °F. A Class 150 "
        "flange is not 150 psi.",
        "Pressure-Temperature Ratings",
        "What a flange class is actually rated for, at your material and your "
        "metal temperature. Four ASME B16.5 material groups, seven pressure "
        "classes, -20 °F to 1000 °F.",
        body, faq_pairs=q, card_meta="4 groups · 7 classes · psig")

    # per-group pages
    for g in pt["groups"]:
        headers = ["Class"] + [f"{t} °F" for t in temps]
        rows = [[f"<strong>Class {c}</strong>"]
                + [str(v) for v in g["ratings"][c]]
                for c in ["150", "300", "400", "600", "900", "1500", "2500"]
                if c in g["ratings"]]
        r150 = g["ratings"]["150"]
        others = "".join(
            f'<a class="chip-link" href="/reference/pressure-temperature-ratings/'
            f'group-{o["slug"]}/">{esc(o["name"])}</a>'
            for o in pt["groups"] if o["slug"] != g["slug"])

        crumb_html, crumb_ld = crumbs([
            ("Home", "/"), ("Reference", "/reference/"),
            ("P-T ratings", "/reference/pressure-temperature-ratings/"),
            (g["name"], None)])

        qg = [
            (f"Which materials are in ASME B16.5 {g['name']}?",
             f"<p>{esc(g['materials'])}.</p>"),
            (f"What is a Class 300 {g['name']} flange rated at 600 °F?",
             f"<p>{psi_bar(g['ratings']['300'][5])}. At 100 °F the same flange "
             f"is rated {psi_bar(g['ratings']['300'][0])}.</p>"),
        ]
        fh, fl = faq(qg)

        b = (crumb_html + '<div class="wrap">'
             f'<div class="page-head"><h1>{esc(g["name"])} Pressure-Temperature '
             f'Ratings</h1><p class="lede">{esc(g["blurb"])}</p></div>'
             + facts([("Standard", "ASME B16.5"), ("Group", esc(g["name"])),
                      ("Materials", esc(g["materials"])),
                      ("Class 150 at 100 °F", psi_bar(r150[0])),
                      ("Class 150 at 600 °F", psi_bar(r150[5]))])
             + table(headers, rows,
                     caption=f"ASME B16.5 pressure-temperature ratings for "
                             f"{g['name']} materials, in psig.",
                     note="Interpolation between listed temperatures is "
                          "permitted; extrapolation is not.")
             + fh
             + f'<h2>Other material groups</h2>'
               f'<div class="chip-links">{others}</div></div>')

        t = fit_title(f"ASME B16.5 {g['name']} P-T Ratings", " | PipeData")
        d = fit_desc(
            f"ASME B16.5 {g['name']} pressure-temperature ratings: Class 150 is "
            f"{r150[0]} psig at 100 °F and {r150[5]} psig at 600 °F. ",
            ["Full psig table for all seven classes, -20 to 1000 °F.",
             "Full table for all seven pressure classes.",
             "Ratings for all seven classes."])
        url = f"/reference/pressure-temperature-ratings/group-{g['slug']}/"
        page(url, t, d, b, ld=[crumb_ld, fl])
        index_entry(f"{g['name']} P-T ratings", url, esc(g["materials"][:52]))


def ref_materials(mats):
    sections = []
    for cat in mats:
        rows = []
        for gr in cat["grades"]:
            rows.append([
                f'<strong>{esc(gr["spec"])}</strong>',
                esc(gr["form"]),
                f'{gr["tensile"]} ksi' if gr["tensile"] else '<span class="na">—</span>',
                f'{gr["yield"]} ksi' if gr["yield"] else '<span class="na">—</span>',
                esc(gr["temp"]),
                esc(gr["note"]),
            ])
        sections.append(
            f'<h2>{esc(cat["name"])}</h2>'
            + table(["Specification", "Form", "Tensile, min", "Yield, min",
                     "Usual temperature range", "Notes"], rows,
                    caption=f"{cat['name']} — common ASTM specifications.",
                    cls="specs wide"))

    total = sum(len(c["grades"]) for c in mats)
    body = (facts([("Specifications listed", str(total)),
                   ("Categories", str(len(mats))),
                   ("Strength units", "ksi (1 ksi = 6.895 MPa)"),
                   ("Bolting standard", "ASTM A193 / A194 / A320")])
            + "<p>Strengths are the specified minimums, not typical mill values. "
            "Temperature ranges are the usual service limits in ordinary "
            "practice, not code limits — the governing design code, normally "
            "ASME B31.3 or B31.1, sets the allowable stress at temperature and "
            "has the final word.</p>"
            "<h2>Matching pipe, fittings and flanges</h2>"
            "<p>Components in a line should be metallurgically compatible so the "
            "weld and the post-weld heat treatment behave predictably. The usual "
            "sets are A106 Gr B pipe with A234 WPB fittings and A105 flanges; "
            "A333 Gr 6 pipe with A420 WPL6 fittings and A350 LF2 flanges; A312 "
            "TP316L pipe with A403 WP316L fittings and A182 F316L flanges.</p>"
            + "".join(sections))

    q = [
        ("What is the difference between A53 and A106 pipe?",
         "<p>A106 is seamless only and is intended for high-temperature service; "
         "A53 may be seamless or ERW and is a general utility pipe. A106 Gr B "
         "and A53 Gr B share the same minimum strengths, but A106 has tighter "
         "chemistry and is the one specified for process service.</p>"),
        ("Which flange material goes with A106 Gr B pipe?",
         "<p>ASTM A105 forged carbon steel, which is B16.5 material Group 1.1. "
         "For low-temperature service, A333 Gr 6 pipe pairs with A350 LF2 "
         "flanges instead.</p>"),
        ("What does the L in 316L mean?",
         "<p>Low carbon — 0.03% maximum against 0.08% for standard 316. The "
         "lower carbon stops chromium carbides forming at grain boundaries "
         "during welding, which would otherwise leave the heat-affected zone "
         "vulnerable to intergranular corrosion. The cost is a slightly lower "
         "allowable stress.</p>"),
        ("What bolting goes with a standard carbon steel flange?",
         "<p>ASTM A193 Gr B7 stud bolts with ASTM A194 Gr 2H heavy hex nuts. For "
         "low-temperature service, A320 Gr L7 studs; for sour service, the "
         "hardness-controlled B7M.</p>"),
    ]
    ref("material-grades",
        "Pipe & Flange Material Grades — ASTM Chart | PipeData",
        "ASTM specifications for pipe, flanges, fittings and bolting with "
        "minimum tensile and yield strength and temperature range. A106, A105, "
        "A312, A234 and more.",
        "Material Grades",
        "The ASTM specifications you will meet on a piping line list — pipe, "
        "flange forgings, buttweld fittings and bolting — with minimum "
        "strengths, temperature ranges and what each one is actually for.",
        body, faq_pairs=q, card_meta=f"{total} ASTM specifications")


def ref_bolt_chart(b165, ftypes):
    classes = ["150", "300", "400", "600", "900", "1500", "2500"]
    all_nps = sorted({r["nps"] for c in classes for r in b165["classes"][c]["rows"]},
                     key=nps_value)
    headers = ["Size"] + [f"Class {c}" for c in classes]
    rows = []
    for nps in all_nps:
        cells = [f"<strong>NPS {esc(nps)}</strong>"]
        for c in classes:
            r = next((x for x in b165["classes"][c]["rows"] if x["nps"] == nps),
                     None)
            cells.append(f'{r["bolts"]} × {esc(r["bolt"])}"'
                         if r else '<span class="na">—</span>')
        rows.append(cells)

    bc_rows = []
    for nps in all_nps:
        cells = [f"<strong>NPS {esc(nps)}</strong>"]
        for c in classes:
            r = next((x for x in b165["classes"][c]["rows"] if x["nps"] == nps),
                     None)
            cells.append(dual(r["bc"], 2) if r else '<span class="na">—</span>')
        bc_rows.append(cells)

    body = (facts([("Standard", "ASME B16.5"), ("Classes", "7"),
                   ("Sizes", "NPS 1/2 – NPS 24"),
                   ("Usual stud material", "ASTM A193 B7"),
                   ("Usual nut material", "ASTM A194 2H")])
            + "<p>Bolt count and diameter are set by the pressure class and the "
            "size — not by the flange type. A Class 300 NPS 6 weld neck, "
            "slip-on, blind and lap joint flange all take the same 12 bolts of "
            "3/4 in diameter on the same 10.62 in bolt circle. That is what "
            "makes the types interchangeable in a joint.</p>"
            "<h2>Bolt count and diameter</h2>"
            + table(headers, rows,
                    caption="ASME B16.5 bolt count and nominal bolt diameter by "
                            "size and pressure class.",
                    note="Bolt holes are always a multiple of four and are "
                         "straddled about the flange centrelines, so no bolt "
                         "sits on the vertical centreline.")
            + "<h2>Bolt circle diameter</h2>"
            + UNITS_NOTE
            + table(headers, bc_rows,
                    caption="ASME B16.5 bolt circle diameter by size and "
                            "pressure class.")
            + "<h2>Bolt length is not in B16.5</h2>"
            "<p>The standard fixes the hole pattern, not the stud length. Length "
            "depends on the two flange thicknesses, the gasket thickness and the "
            "nut height, and is normally rounded up to the next quarter inch "
            "with one to three threads showing beyond each nut. Compute it per "
            "joint rather than reading it off a chart.</p>"
            "<h2>Tightening</h2>"
            "<p>Bolts are tightened in a cross pattern in several passes, not in "
            "sequence around the circle, so the gasket is compressed evenly and "
            "the flange faces stay parallel. ASME PCC-1 gives the assembly "
            "procedure and the recommended pass sequence.</p>")

    q = [
        ("Does the flange type change the bolt pattern?",
         "<p>No. Bolt count, bolt diameter and bolt circle depend only on the "
         "size and the pressure class. A weld neck, slip-on, blind and threaded "
         "flange of the same NPS and class share an identical bolt pattern.</p>"),
        ("Why are flange bolt holes always a multiple of four?",
         "<p>So the pattern straddles both centrelines and no bolt sits at top "
         "dead centre. That keeps the vertical and horizontal centrelines clear "
         "for orientation on drawings and makes flanges easier to align in the "
         "field.</p>"),
        ("How long should the stud bolts be?",
         "<p>B16.5 does not specify it. Add both flange thicknesses, the "
         "compressed gasket thickness and two nut heights, then round up to the "
         "next quarter inch. Aim for one to three threads visible past each "
         "nut.</p>"),
    ]
    ref("flange-bolt-chart",
        "Flange Bolt Chart — Count, Size & Bolt Circle | PipeData",
        "Flange bolt chart for ASME B16.5: bolt count, nominal bolt diameter and "
        "bolt circle diameter for every size from NPS 1/2 to 24 in all seven "
        "pressure classes.",
        "Flange Bolt Chart",
        "Bolt count, bolt diameter and bolt circle for every ASME B16.5 size and "
        "pressure class — the numbers that are set by class and size alone, and "
        "never by the flange type.",
        body, faq_pairs=q, card_meta="7 classes · bolt count and circle")


def ref_weight_chart(pipes):
    keys = ["10", "40", "STD", "80", "XS", "160", "XXS"]
    headers = ["Size", "OD"] + [sched_label(k) for k in keys]
    rows = []
    for s in pipes["sizes"]:
        cells = [f'<a href="{s["url"]}"><strong>NPS {esc(s["nps"])}</strong></a>',
                 dual(s["od"], 3)]
        for k in keys:
            t = s["walls"].get(k)
            cells.append(dual_w(weight_lbft(s["od"], t)) if t
                         else '<span class="na">—</span>')
        rows.append(cells)

    body = (facts([("Formula", "w = 10.6802 × t × (OD − t)"),
                   ("Units", "lb/ft, with kg/m beneath"),
                   ("Material", "Carbon steel, 0.2836 lb/in³"),
                   ("Basis", "Plain end, no coating or lining")])
            + "<h2>The formula</h2>"
            "<p>Plain-end steel pipe weight follows directly from the annular "
            "cross-section and the density of steel:</p>"
            '<p class="formula">w (lb/ft) = 10.6802 × t × (OD − t)</p>'
            "<p>with wall thickness <em>t</em> and outside diameter <em>OD</em> "
            "both in inches. The constant folds in the density of carbon steel, "
            "0.2836 lb/in³, and the conversion from inches to feet. For "
            "stainless multiply by about 1.015, and for the metric equivalent "
            "multiply lb/ft by 1.48816 to get kg/m.</p>"
            "<h2>What the number does not include</h2>"
            "<p>This is bare pipe. A real line weighs more: contents, "
            "insulation, cladding, coating, lining, flanges, valves and the "
            "water left in it after hydrotest. Pipe support and structural "
            "calculations need all of those, and the hydrotest case is often the "
            "governing load — a water-filled NPS 24 line weighs more than twice "
            "its empty weight.</p>"
            "<h2>Weight per foot by schedule</h2>"
            + UNITS_NOTE
            + table(headers, rows,
                    caption="Plain-end carbon steel pipe weight in lb/ft, with "
                            "kg/m beneath, for the most-used schedules.",
                    note='A dash means ASME B36.10M does not publish that '
                         'combination. Open a <a href="/pipes/">size page</a> '
                         'for every schedule in that NPS.'))

    q = [
        ("How do I calculate pipe weight per foot?",
         "<p>w = 10.6802 × t × (OD − t), with wall thickness and outside "
         "diameter in inches, giving pounds per foot of plain-end carbon steel. "
         "Multiply by 1.48816 for kg/m.</p>"),
        ("Does the weight include the contents?",
         "<p>No — these are empty, plain-end weights. Water adds roughly "
         "0.34 × ID² lb/ft, which for a large line can exceed the weight of the "
         "steel itself and usually governs the hydrotest support case.</p>"),
        ("Is stainless steel pipe heavier than carbon steel?",
         "<p>Slightly. Austenitic stainless is about 0.29 lb/in³ against carbon "
         "steel's 0.2836, so multiply the carbon steel weight by about 1.015. "
         "The bigger difference in practice is that stainless lines are often "
         "run in thinner schedules.</p>"),
    ]
    ref("pipe-weight-chart",
        "Steel Pipe Weight Chart — lb/ft and kg/m | PipeData",
        "Steel pipe weight per foot for every size from NPS 1/8 to 36 in the "
        "common schedules, in lb/ft and kg/m, with the formula it is calculated "
        "from.",
        "Pipe Weight Chart",
        "Plain-end carbon steel pipe weight for every ASME B36.10 size in the "
        "seven most-used schedules, in pounds per foot and kilograms per metre, "
        "plus the formula behind it.",
        body, faq_pairs=q, card_meta="lb/ft and kg/m · all sizes")


def s_schedule_page(sch, ss, pipes, cmp_):
    """A /pipes/schedule-10s/ style page for one B36.19M stainless schedule."""
    by_nps = {s["nps"]: s for s in pipes["sizes"]}
    npss = sorted([k for k, v in ss["sizes"].items() if sch in v], key=nps_value)
    info = cmp_[sch]
    cp = info["counterpart"]

    rows = []
    for nps in npss:
        s = by_nps[nps]
        t = ss["sizes"][nps][sch]
        idd = inside_dia(s["od"], t)
        b_wall = s["walls"].get(cp)
        if b_wall is None:
            note = f'<span class="na">no Sch {cp}</span>'
        elif abs(b_wall - t) < 1e-9:
            note = f"same as Sch {cp}"
        else:
            note = f'<span class="yes">Sch {cp} is {n(b_wall, 3)}</span>'
        rows.append([
            f'<a href="{s["url"]}"><strong>NPS {esc(nps)}</strong></a>',
            f'DN {s["dn"]}', dual(s["od"]), dual(t), dual(idd),
            dual_w(weight_lbft(s["od"], t) * 1.015), note])

    thin = min(npss, key=lambda k: ss["sizes"][k][sch])
    thick = max(npss, key=lambda k: ss["sizes"][k][sch])

    if info["differs"]:
        diff_txt = comma_list(
            [f"NPS {d[0]} ({n(d[1], 3)} in against {n(d[2], 3)} in)"
             for d in info["differs"]])
        diverge = (f"<p><strong>{sch} is not the same as Schedule {cp} "
                   f"everywhere.</strong> The two agree in "
                   f"{len(info['same'])} of the {len(npss)} sizes published, and "
                   f"disagree at {diff_txt}. Substituting one for the other in "
                   f"those sizes puts the wrong wall in the line.</p>")
    else:
        diverge = (f"<p>Across every size B36.19M publishes in {sch}, the wall "
                   f"thickness is identical to B36.10M Schedule {cp}. The "
                   f"difference between them is the material, the permitted "
                   f"tolerances and the size range — not the wall.</p>")

    fact_rows = [
        ("Schedule", sch), ("Standard", "ASME B36.19M"),
        ("Material", "Stainless steel"),
        ("Sizes published", f"{len(npss)} (NPS {npss[0]} to NPS {npss[-1]})"),
        ("Thinnest wall", f"{inch_mm(ss['sizes'][thin][sch])} at NPS {thin}"),
        ("Thickest wall", f"{inch_mm(ss['sizes'][thick][sch])} at NPS {thick}"),
    ]

    q = [
        (f"Is {sch} the same as Schedule {cp}?",
         f"<p>{'Not in every size. ' if info['differs'] else 'In wall thickness, yes. '}"
         + (f"They agree in {len(info['same'])} sizes and disagree at "
            f"{comma_list(['NPS ' + d[0] for d in info['differs']])}."
            if info["differs"] else
            f"Every size B36.19M publishes in {sch} carries the same wall as "
            f"Schedule {cp}.")
         + f" The S suffix always signals stainless pipe to ASME B36.19M, "
           f"which also stops at NPS {npss[-1]} in this schedule where "
           f"B36.10M continues.</p>"),
        (f"What sizes does {sch} come in?",
         f"<p>NPS {npss[0]} through NPS {npss[-1]} — {len(npss)} sizes. "
         f"B36.19M is a shorter standard than B36.10M: it publishes 40S and 80S "
         f"only through NPS 12, and 5S and 10S only through NPS 30. Beyond "
         f"that, stainless pipe is ordered to a B36.10M schedule.</p>"),
        (f"Why is {sch} pipe used?",
         "<p>Stainless costs several times what carbon steel does, so stainless "
         "lines are run as thin as the pressure allows. The S-schedules exist to "
         "give that thin end of the range a designation — 10S in particular is "
         "the workhorse wall for low-pressure stainless process and sanitary "
         "piping.</p>"),
    ]
    faq_html, faq_ld = faq(q)
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Pipe", "/pipes/"),
                                   (f"Schedule {sch}", None)])

    others = "".join(
        f'<a class="chip-link" href="/pipes/schedule-{o.lower()}/">Sch {o}</a>'
        for o in ss["schedules"] if o != sch)

    title = fit_title(f"Schedule {sch} Stainless Pipe Dimensions", " | PipeData")
    desc = fit_desc(
        f"Schedule {sch} stainless pipe wall runs "
        f"{n(ss['sizes'][thin][sch], 3)}–{n(ss['sizes'][thick][sch], 3)} in "
        f"across {len(npss)} sizes. ",
        [f"ASME B36.19M OD, bore and weight, with how {sch} differs from "
         f"Schedule {cp}.",
         f"ASME B36.19M OD, bore and weight, compared against Schedule {cp}.",
         "ASME B36.19M outside diameter, bore and weight for every size."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>Schedule {sch} Pipe Dimensions</h1>'
            f'<p class="lede">ASME B36.19M stainless steel pipe in {sch}, '
            f'NPS {esc(npss[0])} through NPS {esc(npss[-1])} — with the '
            f'B36.10M Schedule {cp} wall alongside, because the two are '
            f'routinely swapped by mistake.</p></div>'
            + facts(fact_rows) + UNITS_NOTE
            + table(["Size", "DN", "Outside diameter", f"{sch} wall",
                     "Inside diameter", "Weight, empty",
                     f"vs Schedule {cp}"], rows,
                    caption=f"ASME B36.19M schedule {sch} stainless pipe "
                            f"dimensions, with the B36.10M Schedule {cp} "
                            f"comparison.",
                    note="Weight is plain-end austenitic stainless, taken as "
                         "1.5% heavier than the carbon steel formula "
                         "w = 10.6802 × t × (OD − t) lb/ft.")
            + diverge + faq_html
            + f'<h2>Other stainless schedules</h2>'
            f'<div class="chip-links">{others}</div>'
            + '<p><a class="more" href="/reference/stainless-pipe-schedules/">'
            'How B36.19M compares with B36.10M →</a></p></div>')

    url = f"/pipes/schedule-{sch.lower()}/"
    page(url, title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"Schedule {sch} stainless pipe", url,
                f"ASME B36.19M · {len(npss)} sizes")


def ref_stainless(ss, pipes, cmp_):
    by_nps = {s["nps"]: s for s in pipes["sizes"]}
    npss = sorted(ss["sizes"], key=nps_value)
    headers = ["Size", "OD"] + [f"Sch {s}" for s in ss["schedules"]]
    rows = []
    for nps in npss:
        s = by_nps[nps]
        cells = [f'<strong>NPS {esc(nps)}</strong>', dual(s["od"])]
        for sch in ss["schedules"]:
            t = ss["sizes"][nps].get(sch)
            cells.append(dual(t) if t else '<span class="na">—</span>')
        rows.append(cells)

    # Divergence table, computed — never asserted.
    div_rows = []
    for sch in ss["schedules"]:
        info = cmp_[sch]
        cp = info["counterpart"]
        if info["differs"]:
            where = comma_list([f"NPS {d[0]}" for d in info["differs"]])
            detail = comma_list(
                [f"{n(d[1], 3)} in against Schedule {cp}'s {n(d[2], 3)} in"
                 for d in info["differs"]])
            verdict = f'<span class="yes">Differs at {where}</span>'
        else:
            detail = f"identical wall in all {len(info['same'])} sizes"
            verdict = f"Matches Schedule {cp}"
        div_rows.append([f"<strong>Sch {sch}</strong>", f"Schedule {cp}",
                         verdict, detail])

    cards = "".join(
        f'<a class="card" href="/pipes/schedule-{s.lower()}/">'
        f'<span class="card-title">Schedule {s}</span>'
        f'<span class="card-meta">'
        f'{len([k for k, v in ss["sizes"].items() if s in v])} sizes</span></a>'
        for s in ss["schedules"])

    body = (facts([("Standard", "ASME B36.19M"),
                   ("Schedules", comma_list(ss["schedules"])),
                   ("40S and 80S published to", "NPS 12"),
                   ("5S and 10S published to", "NPS 30")])
            + "<h2>The S means stainless, not thinner</h2>"
            "<p>The most common belief about the S-schedules — that they are a "
            "thin-wall series with no carbon steel equivalent — is wrong. In "
            "most sizes an S-schedule carries exactly the same wall as the "
            "B36.10M schedule of the same number. What the S actually "
            "designates is the standard the pipe is made to: ASME B36.19M, the "
            "stainless steel pipe standard, with its own material scope, its own "
            "tolerances and a shorter size range.</p>"
            "<h2>Where the two standards actually disagree</h2>"
            "<p>Computed from the two tables rather than asserted — these are "
            "the only wall thickness disagreements between the S-schedules and "
            "their B36.10M counterparts:</p>"
            + table(["Stainless", "Carbon steel counterpart", "Verdict",
                     "Detail"], div_rows,
                    caption="B36.19M S-schedules against their B36.10M "
                            "counterparts, over every size both standards "
                            "publish.",
                    note="Where a schedule &ldquo;matches&rdquo;, it matches "
                         "only across the sizes B36.19M publishes — which is a "
                         "far shorter list than B36.10M's.",
                    cls="specs wide")
            + "<h2>The size range is the bigger difference</h2>"
            "<p>B36.19M publishes 40S and 80S only through NPS 12, and 5S and "
            "10S only through NPS 30. B36.10M runs to NPS 36 and beyond in the "
            "numbered schedules. A drawing calling for &ldquo;NPS 16, 40S&rdquo; "
            "is asking for something the standard does not define — the "
            "intention is almost always NPS 16 Schedule 40 in a stainless "
            "grade, ordered to B36.10M dimensions.</p>"
            "<h2>Stainless S-schedule dimensions</h2>"
            + UNITS_NOTE
            + table(headers, rows,
                    caption="ASME B36.19M wall thickness by size and "
                            "S-schedule. A dash means the standard does not "
                            "publish that combination.")
            + f'<h2>Each schedule in full</h2><div class="grid">{cards}</div>')

    q = [
        ("Is 10S the same as Schedule 10?",
         "<p>In wall thickness, yes, in every size B36.19M publishes. The "
         "difference is the standard: 10S is stainless pipe to ASME B36.19M, "
         "which stops at NPS 30, while Schedule 10 is B36.10M and continues "
         "further.</p>"),
        ("Is 40S the same as Schedule 40?",
         "<p>Up to NPS 10, yes. At NPS 12 they part company — 40S is 0.375 in "
         "while Schedule 40 is 0.406 in — and B36.19M does not publish 40S above "
         "NPS 12 at all.</p>"),
        ("Is 80S the same as Schedule 80?",
         "<p>Up to NPS 8, yes. At NPS 10 and NPS 12, 80S holds at 0.500 in while "
         "Schedule 80 climbs to 0.594 in and 0.688 in. Above NPS 12 there is no "
         "80S.</p>"),
        ("Can I order stainless pipe in a plain numbered schedule?",
         "<p>Yes, and above the S-schedule size limits you have to. Stainless "
         "pipe to ASTM A312 is routinely supplied to B36.10M schedules; the S "
         "designation is a convenience for the thin end of the range, not a "
         "requirement for stainless.</p>"),
    ]
    ref("stainless-pipe-schedules",
        "Stainless Pipe Schedules — 5S, 10S, 40S, 80S | PipeData",
        "ASME B36.19M stainless pipe schedules 5S, 10S, 40S and 80S, with wall "
        "thickness for every size and exactly where each stops matching its "
        "B36.10M counterpart.",
        "Stainless Pipe Schedules",
        "What the S in 10S actually means, wall thickness for every B36.19M "
        "size, and the two places where an S-schedule stops matching the "
        "carbon steel schedule of the same number.",
        body, faq_pairs=q, card_meta="B36.19M · 5S, 10S, 40S, 80S")


def ref_face_types():
    faces = [
        ("Raised Face (RF)", "The default in ASME B16.5 and by far the most "
         "common. A circular raised area carries the gasket, concentrating bolt "
         "load onto a smaller area than a flat face. Height is 1/16 in for "
         "Classes 150 and 300, and 1/4 in for Class 400 and above."),
        ("Flat Face (FF)", "The gasket surface is the full flange face. Used "
         "against cast iron equipment flanges and other brittle castings, where "
         "a raised face would bend the mating flange and crack it. Needs a "
         "full-face gasket."),
        ("Ring Type Joint (RTJ)", "A machined groove takes a solid metal ring "
         "that seals by plastic deformation as the bolts are tightened. The "
         "flange faces stand apart and never touch the gasket seating area. "
         "Standard for high-pressure and high-temperature service, especially "
         "Class 900 and above."),
        ("Tongue and Groove (T&amp;G)", "A projecting tongue on one flange mates "
         "with a matching groove on the other, trapping the gasket so it cannot "
         "blow out or be over-compressed. Supplied as a matched pair — a tongue "
         "flange is useless without its groove."),
        ("Male and Female (M&amp;F)", "Like tongue and groove but with a deeper "
         "male projection that locates in a female recess. Also a matched pair. "
         "Uncommon in new work; found in older plant and in some heat exchanger "
         "designs."),
        ("Lap Joint (LJ)", "Not a face in its own right — the stub end provides "
         "the gasket surface and the backing flange only supplies bolt load. The "
         "stub end face may itself be flat or serrated."),
    ]
    cards = "".join(
        f'<div class="face-card"><h3>{name}</h3><p>{desc}</p></div>'
        for name, desc in faces)

    body = (facts([("Faces in ASME B16.5", "6"),
                   ("Most common", "Raised face (RF)"),
                   ("RF height, Class 150/300", "1/16 in (1.6 mm)"),
                   ("RF height, Class 400+", "1/4 in (6.4 mm)")])
            + "<p>The face is the part of the flange that actually seals. It "
            "does not change the flange outside diameter, bolt circle or bolt "
            "pattern — a Class 300 NPS 6 flange has the same bolting whatever "
            "its face — but it does dictate the gasket, and two flanges with "
            "different faces cannot be bolted together.</p>"
            f'<div class="face-grid">{cards}</div>'
            + "<h2>Surface finish matters as much as the face</h2>"
            "<p>ASME B16.5 specifies a serrated finish on raised and flat faces, "
            "either concentric or spiral, at 125 to 500 microinches Ra. The "
            "serrations bite into a soft gasket and hold it in place. A face "
            "machined smooth — which looks better — seals worse, because there "
            "is nothing for the gasket to key into.</p>"
            "<h2>Flange thickness and the raised face</h2>"
            "<p>The thickness tabulated in B16.5 excludes the raised face. A "
            "Class 300 NPS 6 flange is listed at 1.44 in and measures 1.50 in "
            "overall with its 1/16 in raised face. Class 400 and above add "
            "1/4 in. This matters when calculating stud bolt length.</p>")

    q = [
        ("Can I bolt a raised face flange to a flat face flange?",
         "<p>Not properly. The raised face concentrates load on a narrow band "
         "and will bend the flat face flange — which is usually cast iron, and "
         "will crack. The correct fix is to machine the raised face off the "
         "steel flange and use a full-face gasket.</p>"),
        ("What is the raised face height?",
         "<p>1/16 in (1.6 mm) for Classes 150 and 300, and 1/4 in (6.4 mm) for "
         "Class 400 and above. The tabulated flange thickness in B16.5 excludes "
         "it, so the overall thickness is the table figure plus the face.</p>"),
        ("When do I need a ring type joint flange?",
         "<p>High pressure and high temperature, typically Class 900 and above, "
         "and wherever a metal-to-metal seal is required — hydrogen service, "
         "wellhead equipment and high-temperature hydrocarbon lines. RTJ flanges "
         "only mate with other RTJ flanges of the same ring number.</p>"),
    ]
    ref("flange-face-types",
        "Flange Face Types — RF, FF, RTJ Explained | PipeData",
        "The six ASME B16.5 flange face types explained: raised face, flat "
        "face, ring type joint, tongue and groove, male and female and lap "
        "joint, and when to use each.",
        "Flange Face Types",
        "Raised face, flat face, ring type joint and the rest — what each face "
        "does, which gasket it needs, and why two flanges with different faces "
        "will never seal against each other.",
        body, faq_pairs=q, card_meta="RF · FF · RTJ · T&G · M&F")


A13_SCHEME = [
    ("Flammable and oxidizing fluids", "#000000", "#FFD100", "Black", "Yellow"),
    ("Combustible fluids", "#FFFFFF", "#6B4226", "White", "Brown"),
    ("Toxic and corrosive fluids", "#000000", "#F07300", "Black", "Orange"),
    ("Fire-quenching fluids", "#FFFFFF", "#C8102E", "White", "Red"),
    ("Water — potable, cooling, boiler feed and other", "#FFFFFF", "#00843D",
     "White", "Green"),
    ("Compressed air", "#FFFFFF", "#0057B8", "White", "Blue"),
]

A13_USER = [
    ("User defined", "#FFFFFF", "#5B2D8E", "White", "Purple"),
    ("User defined", "#FFFFFF", "#000000", "White", "Black"),
    ("User defined", "#000000", "#FFFFFF", "Black", "White"),
    ("User defined", "#000000", "#9AA0A6", "Black", "Gray"),
]

# ASME A13.1 Table 1. Stated by the standard against the outside diameter of
# the pipe *or its covering*; applied here by NPS, which is how it is used in
# the field. A lagged line moves up a band — see the note on the page.
A13_SIZES = [
    (0.75, 1.25, 8, 0.5),
    (1.5, 2.0, 8, 0.75),
    (2.5, 6.0, 12, 1.25),
    (8.0, 10.0, 24, 2.5),
    (10.0, None, 32, 3.5),
]


def a13_band(nps):
    v = nps_value(nps)
    for lo, hi, length, letter in A13_SIZES:
        if hi is None:
            if v > lo:
                return length, letter
        elif lo <= v <= hi:
            return length, letter
    # Below NPS 3/4 the standard gives no field size; A13.1 directs the user to
    # a permanently attached tag instead of a wrapped marker.
    return None, None


def ref_color_coding(pipes):
    def swatch(fg, bg, fg_name, bg_name):
        return (f'<span style="display:inline-block;padding:.25em .7em;'
                f'border-radius:3px;border:1px solid rgba(0,0,0,.25);'
                f'background:{bg};color:{fg};font-weight:600;font-size:.9em">'
                f'{esc(bg_name)}</span> <span class="mm">{esc(fg_name)} legend'
                f'</span>')

    scheme_rows = [[esc(what), swatch(fg, bg, fgn, bgn), esc(fgn), esc(bgn)]
                   for what, fg, bg, fgn, bgn in A13_SCHEME]
    user_rows = [[esc(what), swatch(fg, bg, fgn, bgn), esc(fgn), esc(bgn)]
                 for what, fg, bg, fgn, bgn in A13_USER]

    band_rows = []
    for lo, hi, length, letter in A13_SIZES:
        rng = (f"Over {n(lo, 0)} in" if hi is None else
               f"{n(lo, 2).rstrip('0').rstrip('.')} – "
               f"{n(hi, 2).rstrip('0').rstrip('.')} in")
        band_rows.append([rng, f"{length} in", f"{n(letter, 2)} in"])

    size_rows = []
    for s in pipes["sizes"]:
        length, letter = a13_band(s["nps"])
        size_rows.append([
            f'<a href="{s["url"]}"><strong>NPS {esc(s["nps"])}</strong></a>',
            dual(s["od"], 3),
            f"{length} in" if length else '<span class="na">tag instead</span>',
            f"{n(letter, 2)} in" if letter else '<span class="na">—</span>',
        ])

    q = [
        ("What do the ASME A13.1 pipe colours mean?",
         "<p>Six colour combinations are fixed by hazard, not by contents. "
         "Yellow with black lettering is flammable or oxidizing; brown with "
         "white is combustible; orange with black is toxic or corrosive; red "
         "with white is fire-quenching; green with white is water; blue with "
         "white is compressed air. Four further combinations — purple, black, "
         "white and gray fields — are left for the user to define.</p>"),
        ("Is the colour enough on its own?",
         "<p>No. A13.1 requires the legend — the name of the contents in "
         "words — plus an arrow showing flow direction. The colour field is "
         "there to make the hazard class readable from a distance; the words "
         "are what identify the line. A pipe marked only by colour does not "
         "comply.</p>"),
        ("How often do markers have to be repeated?",
         "<p>A13.1 asks for markers wherever the line is read: at valves and "
         "flanges, at changes of direction, on both sides of a wall, floor or "
         "ceiling penetration, at entry and exit points, and at intervals on "
         "long straight runs — commonly taken as every 25 to 50 ft. Place them "
         "so a marker is visible from the normal approach to the pipe.</p>"),
        ("Does A13.1 apply outside the United States?",
         "<p>No. A13.1 is a US voluntary standard, adopted by reference in many "
         "specifications and by OSHA in practice rather than by rule. The UK "
         "and much of Europe use BS 1710 and its EN companions, which have a "
         "different and incompatible colour scheme — green for water there too, "
         "but the rest does not map across. Marine and shipbuilding practice "
         "differs again.</p>"),
    ]

    body = (
        "<h2>The six fixed colour combinations</h2>"
        "<p>ASME A13.1 classifies by <em>hazard</em>, not by fluid. What "
        "determines the colour is what the contents would do if they escaped, "
        "which is why steam, hot oil and natural gas do not each get a colour "
        "of their own.</p>"
        + table(["Contents", "Marker", "Legend colour", "Field colour"],
                scheme_rows,
                caption="ASME A13.1 pipe marker colour scheme.",
                note="Colours shown are indicative on screen only. Specify "
                     "marker stock against the standard, not against this "
                     "page.")
        + "<h2>The four user-defined combinations</h2>"
        "<p>These carry no assigned meaning. If a site uses them it must "
        "document what they mean and post the key where the pipes are — an "
        "undocumented purple line tells a stranger nothing.</p>"
        + table(["Contents", "Marker", "Legend colour", "Field colour"],
                user_rows, caption="ASME A13.1 user-defined combinations.")
        + "<h2>Marker size</h2>"
        "<p>The size of the colour field and the letter height both follow the "
        "pipe diameter, so a marker legible on a 2 in line is not compliant on "
        "a 12 in header.</p>"
        + table(["Pipe outside diameter", "Length of colour field",
                 "Letter height"], band_rows,
                caption="ASME A13.1 Table 1 — marker and legend size.",
                note="The standard states these against the outside diameter "
                     "of the pipe <em>or its covering</em>. An insulated line "
                     "is sized on the lagging, not the pipe, so it often moves "
                     "up a band.")
        + "<h2>Marker size by pipe size</h2>"
        "<p>The same table applied to every ASME B36.10M size, with the "
        "outside diameter alongside so an insulated line can be checked "
        "against the band above.</p>"
        + UNITS_NOTE
        + table(["Size", "Outside diameter", "Colour field length",
                 "Letter height"], size_rows,
                caption="ASME A13.1 marker size for each B36.10M pipe size, "
                        "bare pipe.",
                note="Below NPS 3/4 A13.1 gives no field size: the standard "
                     "calls for a permanently attached tag rather than a "
                     "wrapped marker.")
        + '<div class="callout"><p><strong>Colour is not the '
        "identification.</strong> Every A13.1 marker needs the legend in words "
        "and an arrow for flow direction. Colour classifies the hazard; the "
        "words identify the line. Bidirectional lines get arrows both ways."
        "</p></div>"
    )

    ref("pipe-color-coding",
        "Pipe Color Coding Chart — ASME A13.1 | PipeData",
        "ASME A13.1 pipe marker colours: black on yellow for flammable, black "
        "on orange for toxic, white on green for water. Marker and letter size "
        "for every pipe size.",
        "Pipe Color Coding — ASME A13.1",
        "The six fixed colour combinations, the four user-defined ones, and "
        "the legend and letter size required at every pipe diameter.",
        body, faq_pairs=q,
        card_meta="ASME A13.1 · 10 colour combinations")


def reference_index():
    cards = "".join(
        f'<a class="card" href="{url}"><span class="card-title">{esc(name)}</span>'
        f'<span class="card-meta">{meta}</span></a>'
        for name, url, meta in REFERENCE_PAGES)

    body = (crumbs([("Home", "/"), ("Reference", None)])[0]
            + '<div class="wrap">'
            '<div class="page-head"><h1>Piping Reference Tables</h1>'
            '<p class="lede">Conversion charts, schedule explanations, material '
            'specifications and pressure-temperature ratings — the lookups that '
            'sit behind the dimension tables.</p></div>'
            f'<div class="grid">{cards}</div>'
            + '<div class="callout"><p><strong>Every figure here comes from a '
            'published standard.</strong> PipeData reproduces them for quick '
            'lookup, not as a substitute for the standards. Confirm against a '
            'current copy of the governing ASME, ASTM or API document before '
            'you fabricate, procure or design from it.</p></div></div>')

    _, crumb_ld = crumbs([("Home", "/"), ("Reference", None)])
    page("/reference/",
         "Piping Reference Tables & Conversion Charts | PipeData",
         "Piping reference tables: NPS to DN conversion, pipe schedule chart, "
         "flange bolt chart, pressure-temperature ratings, material grades and "
         "flange face types.",
         body, ld=[crumb_ld,
                   item_list([(nm, u) for nm, u, _ in REFERENCE_PAGES],
                             "Piping reference tables")])
    index_entry("Reference index", "/reference/", "Charts and conversions")


# --------------------------------------------------------------------------
# top-level pages
# --------------------------------------------------------------------------

def homepage(pipes, ftypes, fittings, b165):
    size_chips = "".join(
        f'<a class="chip-link" href="{s["url"]}">NPS {esc(s["nps"])}</a>'
        for s in pipes["sizes"] if s["nps"] in
        ("1/2", "3/4", "1", "1 1/2", "2", "3", "4", "6", "8", "10", "12",
         "14", "16", "18", "20", "24", "30", "36"))

    type_cards = "".join(
        f'<a class="card" href="/flanges/{t["slug"]}/">'
        f'<span class="card-title">{esc(t["name"])}</span>'
        f'<span class="card-meta">{esc(t["abbrev"])}</span></a>'
        for t in ftypes)

    fit_cards = "".join(
        f'<a class="card" href="/fittings/{f["slug"]}/">'
        f'<span class="card-title">{esc(f["name"])}</span>'
        f'<span class="card-meta">{esc(f["dim_label"])}</span></a>'
        for f in fittings)

    ref_cards = "".join(
        f'<a class="card" href="{u}"><span class="card-title">{esc(nm)}</span>'
        f'<span class="card-meta">{meta}</span></a>'
        for nm, u, meta in REFERENCE_PAGES)

    n_pipe = len(pipes["sizes"])
    n_flange = sum(len(t["classes"]) for t in ftypes)

    q = [
        ("Does pipe outside diameter change with schedule?",
         "<p>No. Outside diameter is fixed for a given NPS. A higher schedule "
         "thickens the wall inward and shrinks the bore, which is what allows "
         "one set of flanges and fittings to serve every schedule in a "
         "size.</p>"),
        ("Is a Class 150 flange rated for 150 psi?",
         "<p>No. In A105 carbon steel a Class 150 flange is rated 285 psig at "
         "100 °F, falling to 140 psig at 600 °F. The class number is a "
         "designation, not a pressure.</p>"),
        ("Do buttweld fitting dimensions change with wall thickness?",
         "<p>No. ASME B16.9 sets fitting dimensions from nominal size alone, so "
         "a schedule 10 and a schedule 160 elbow of the same NPS occupy exactly "
         "the same space.</p>"),
        ("What units does PipeData use?",
         "<p>Imperial is primary throughout — inches, pounds per foot, psig — "
         "with the metric equivalent shown alongside in millimetres, kilograms "
         "per metre and bar.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    website_ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": SITE + "/",
        "description": ("Dimensional and rating data for steel pipe, flanges "
                        "and buttweld fittings from the ASME B16 and B36 "
                        "standards."),
        "publisher": {"@type": "Organization", "name": SITE_NAME,
                      "url": SITE + "/",
                      "email": EMAIL},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": SITE + "/?q={search_term_string}"},
            "query-input": "required name=search_term_string",
        },
    }

    body = f"""
<section class="hero">
  <div class="wrap">
    <h1>Pipe, flange and fitting specifications</h1>
    <p class="tagline">ASME B36.10 · B16.5 · B16.47 · B16.9</p>
    <div class="search">
      <input id="pd-search" type="search" autocomplete="off"
             placeholder="Search a size, class or fitting — try &ldquo;NPS 6&rdquo; or &ldquo;class 300 weld neck&rdquo;"
             aria-label="Search PipeData" data-base="/">
      <div id="pd-results" class="search-results" role="listbox"></div>
    </div>
    <p class="counter"><strong>{n_pipe}</strong> pipe sizes ·
      <strong>{n_flange}</strong> flange class tables ·
      <strong>{len(fittings)}</strong> fitting types ·
      imperial primary, metric alongside</p>
  </div>
</section>
<div class="wrap">
  <h2>Pipe dimensions</h2>
  <p>Outside diameter, wall thickness, bore, weight and flow area for every
     ASME B36.10M size from NPS 1/8 to NPS 36, across all fourteen schedules.</p>
  <div class="chip-links">{size_chips}
    <a class="chip-link more-chip" href="/pipes/">All sizes →</a></div>

  <h2>Flanges</h2>
  <p>Six ASME B16.5 flange types across seven pressure classes — outside
     diameter, thickness, bolt circle, bolt count and bolt size — plus the
     large-diameter B16.47 series above NPS 24.</p>
  <div class="grid">{type_cards}</div>
  <p><a class="more" href="/flanges/large/">Large diameter flanges, NPS 26–60 →</a></p>

  <h2>Buttweld fittings</h2>
  <p>Centre-to-end dimensions from ASME B16.9. These depend on nominal size
     alone, so a single table covers every schedule.</p>
  <div class="grid">{fit_cards}</div>

  <h2>Reference tables</h2>
  <div class="grid">{ref_cards}</div>

  {faq_html}

  <div class="callout">
    <p><strong>Reference only.</strong> PipeData reproduces published standard
    data for quick lookup. Confirm every dimension against a current copy of the
    governing ASME, ASTM or API standard before fabrication, procurement or
    design.</p>
  </div>
</div>
"""
    page("/", "PipeData — Pipe, Flange & Fitting Specifications",
         "Pipe, flange and fitting dimensions from ASME B36.10, B16.5, B16.47 "
         "and B16.9. Wall thickness, weight, bolt circles and pressure ratings, "
         "imperial with metric.",
         body, ld=[website_ld, faq_ld])


def about_page(pipes, ftypes, fittings):
    body = (crumbs([("Home", "/"), ("About", None)])[0]
            + '<div class="wrap narrow">'
            '<div class="page-head"><h1>About PipeData</h1>'
            '<p class="lede">A fast, free lookup for the dimensional and rating '
            'data that piping work depends on — with the caveats stated plainly '
            'rather than buried.</p></div>'
            "<h2>What this is</h2>"
            "<p>PipeData reproduces the dimension and rating tables from the "
            "ASME B16 and B36 series in a form you can read on a phone at a "
            "fabrication shop. Every page answers one question directly: the "
            "wall thickness of a size, the bolting of a class, the centre-to-end "
            "of a fitting.</p>"
            "<h2>What it covers</h2>"
            "<ul>"
            f"<li><strong>ASME B36.10M</strong> — {len(pipes['sizes'])} pipe "
            "sizes from NPS 1/8 to NPS 36, all fourteen schedules, with bore, "
            "weight and flow area calculated for each.</li>"
            f"<li><strong>ASME B16.5</strong> — {len(ftypes)} flange types "
            "across seven pressure classes, NPS 1/2 to NPS 24.</li>"
            "<li><strong>ASME B16.47</strong> — large-diameter flanges NPS 26 "
            "to NPS 60, both series.</li>"
            f"<li><strong>ASME B16.9</strong> — {len(fittings)} buttweld fitting "
            "types with their governing dimensions.</li>"
            "<li>Reference tables for NPS/DN conversion, schedules, materials, "
            "bolting, flange faces and pressure-temperature ratings.</li>"
            "</ul>"
            "<h2>Units</h2>"
            "<p>Imperial is primary — inches, pounds per foot, psig — because "
            "that is how these standards are written and how the material is "
            "ordered in North America. The metric equivalent appears alongside "
            "in every table: millimetres beneath inches, kilograms per metre "
            "beneath pounds per foot, bar beside psig.</p>"
            "<h2>Where the numbers come from</h2>"
            "<p>Dimensional and rating values are the published values from the "
            "ASME, ASTM, API and MSS standards named on each page. Derived "
            "figures — bore, weight, flow area, water volume — are calculated "
            "here from those published dimensions, and the formula is stated "
            "wherever a figure is derived rather than tabulated.</p>"
            '<div class="callout warn">'
            "<p><strong>This is not a substitute for the standards.</strong> "
            "Standards are revised, values change between editions, and a "
            "secondary source can carry a transcription error. Every figure on "
            "this site should be confirmed against a current copy of the "
            "governing document before it is used for fabrication, procurement "
            "or design. Where PipeData does not have data it trusts, it says so "
            "on the page rather than filling the gap.</p></div>"
            "<h2>Coverage gaps we admit to</h2>"
            "<p>ASME B16.47 is published here with the full table for Series A "
            "Class 150 only. The other series and class combinations are "
            "described but deliberately not tabulated. Large-flange data varies "
            "between the two series in ways that make a transcription error "
            "expensive, and an incomplete page is better than a confidently "
            "wrong one.</p>"
            "<h2>Corrections</h2>"
            "<p>If a figure here disagrees with your copy of the standard, the "
            "standard is right and we want to know. Email "
            f'<a href="mailto:{EMAIL}">{EMAIL}</a> with the page and the '
            "clause.</p>"
            "<h2>Independence</h2>"
            "<p>PipeData is an independent reference project. It is not "
            "affiliated with, endorsed by or sponsored by ASME, ASTM, API, MSS "
            "or any manufacturer or distributor.</p>"
            "</div>")
    page("/about/", "About PipeData — Piping Specification Reference",
         "PipeData is an independent reference for ASME pipe, flange and "
         "fitting dimensions. What it covers, where the numbers come from, and "
         "the gaps it admits to.",
         body, ld=[crumbs([("Home", "/"), ("About", None)])[1]])
    index_entry("About PipeData", "/about/", "Scope, sources and caveats")


def privacy_page():
    ga_para = (
        "<p>This site uses Google Analytics 4 to count visits and see which "
        "pages get used. It sets cookies and sends your IP address to Google, "
        "who process it as our data processor. We do not use it to build "
        "profiles or to advertise to you.</p>"
        if GA_ENABLED else
        "<p>This site runs no analytics, no advertising and no third-party "
        "tracking scripts. Nothing on these pages sets a cookie, and no "
        "profile of you is built or bought.</p>")

    fonts = ("<p>Typefaces are loaded from Google Fonts, which means your "
             "browser makes a request to <code>fonts.googleapis.com</code> and "
             "<code>fonts.gstatic.com</code> when a page loads. That request "
             "carries your IP address and user agent to Google. It is the only "
             "third-party request the site makes.</p>")

    body = (crumbs([("Home", "/"), ("Privacy", None)])[0]
            + '<div class="wrap narrow">'
            '<div class="page-head"><h1>Privacy</h1>'
            '<p class="lede">What this site does and does not collect, stated '
            'in full.</p></div>'
            "<h2>Analytics</h2>" + ga_para
            + "<h2>Fonts</h2>" + fonts
            + "<h2>What we never collect</h2>"
            "<p>There are no accounts, no logins and no forms on this site. We "
            "do not ask for your name, your email address or your employer, and "
            "there is nowhere on the site to give them to us. Nothing you type "
            "into the search box leaves your browser — the search index is a "
            "static file your browser downloads once and queries locally.</p>"
            "<h2>Hosting</h2>"
            "<p>PipeData is served as static files by GitHub Pages. GitHub "
            "receives the request as part of delivering the page and keeps its "
            "own server logs, which we do not have access to. Their practices "
            "are covered by the GitHub Privacy Statement.</p>"
            "<h2>Cookies</h2>"
            + ("<p>Google Analytics sets its own cookies. The site itself sets "
               "none.</p>" if GA_ENABLED else
               "<p>This site sets no cookies at all.</p>")
            + "<h2>Changes</h2>"
            "<p>If this ever changes — if analytics is added, or a third-party "
            "service introduced — this page will be updated to say so before or "
            "at the same time as the change goes live.</p>"
            "<h2>Contact</h2>"
            f'<p>Questions about any of this: <a href="mailto:{EMAIL}">{EMAIL}</a>.'
            f'</p><p class="muted">Last updated {TODAY}.</p></div>')
    page("/privacy/", "Privacy Policy | PipeData",
         "PipeData collects no personal data, has no accounts and no forms. "
         "What the site loads, what your browser sends to third parties, and "
         "who to contact about it.",
         body, ld=[crumbs([("Home", "/"), ("Privacy", None)])[1]])


def not_found():
    body = ('<div class="wrap narrow">'
            '<div class="page-head"><h1>Page not found</h1>'
            '<p class="lede">That URL does not exist on PipeData. It may have '
            'moved, or it may never have been here.</p></div>'
            '<div class="search">'
            '<input id="pd-search" type="search" autocomplete="off" '
            'placeholder="Search for a size, class or fitting" '
            'aria-label="Search PipeData" data-base="/">'
            '<div id="pd-results" class="search-results" role="listbox"></div>'
            "</div>"
            '<h2>Try one of these</h2>'
            '<div class="grid">'
            '<a class="card" href="/pipes/"><span class="card-title">'
            'Pipe dimensions</span><span class="card-meta">ASME B36.10 · '
            'NPS 1/8 to 36</span></a>'
            '<a class="card" href="/flanges/"><span class="card-title">'
            'Flange dimensions</span><span class="card-meta">ASME B16.5 · '
            '6 types</span></a>'
            '<a class="card" href="/fittings/"><span class="card-title">'
            'Fitting dimensions</span><span class="card-meta">ASME B16.9 · '
            'buttweld</span></a>'
            '<a class="card" href="/reference/"><span class="card-title">'
            'Reference tables</span><span class="card-meta">Conversions and '
            'charts</span></a></div></div>')
    write("404.html",
          head("Page not found | PipeData",
               "That page does not exist on PipeData. Search, or jump to pipe "
               "dimensions, flange dimensions, fitting dimensions or the "
               "reference tables from here.",
               "/404.html", noindex=True) + body + foot())


# --------------------------------------------------------------------------
# site plumbing
# --------------------------------------------------------------------------

def sitemap(urls):
    entries = "".join(
        f"<url><loc>{esc(SITE + u)}</loc><lastmod>{TODAY}</lastmod>"
        f"<changefreq>monthly</changefreq><priority>{p}</priority></url>"
        for u, p in urls)
    write("sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          + entries + "</urlset>\n")


def robots():
    write("robots.txt",
          f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")


def webmanifest():
    write("site.webmanifest", json.dumps({
        "name": "PipeData — Pipe, Flange & Fitting Specifications",
        "short_name": "PipeData",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#F7F8FA",
        "theme_color": "#2B4C7E",
        "icons": [
            {"src": "/favicon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/favicon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, indent=2))


def search_index():
    write("search-index.json",
          json.dumps(SEARCH, ensure_ascii=False, separators=(",", ":")))


def copy_static():
    for name in os.listdir(STATIC):
        src = os.path.join(STATIC, name)
        dst = os.path.join(OUT, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    write(".nojekyll", "")
    write("CNAME", "pipedata.org\n")


# --------------------------------------------------------------------------
# build gates
# --------------------------------------------------------------------------

def audit_descriptions():
    """Fail the build on a duplicate, empty or out-of-band meta description.

    Runs over everything head() emitted, so a new page type is covered
    automatically instead of needing to be registered here.
    """
    indexed = {p: v["desc"] for p, v in DESC_REGISTRY.items() if not v["noindex"]}

    by_text = defaultdict(list)
    for path, desc in indexed.items():
        by_text[desc].append(path)
    dupes = {d: ps for d, ps in by_text.items() if len(ps) > 1}
    long_ = {p: d for p, d in indexed.items() if len(d) > DESC_MAX}
    short_ = {p: d for p, d in indexed.items() if len(d) < DESC_MIN}
    missing = {p: d for p, d in indexed.items() if not (d or "").strip()}

    print(f"  meta descriptions: {len(indexed)} indexed pages, "
          f"{len(by_text)} unique")

    ok = True
    if dupes:
        ok = False
        print("  DUPLICATE descriptions:", file=sys.stderr)
        for d, ps in dupes.items():
            print(f"    {len(ps)}x {d[:70]!r}", file=sys.stderr)
            for p in ps:
                print(f"        {p}", file=sys.stderr)
    if missing:
        ok = False
        print("  EMPTY descriptions:", file=sys.stderr)
        for p in missing:
            print(f"    {p}", file=sys.stderr)
    for name, bucket in (("OVER", long_), ("UNDER", short_)):
        if bucket:
            ok = False
            limit = DESC_MAX if name == "OVER" else DESC_MIN
            print(f"  {name} length ({limit}) — {len(bucket)} pages:",
                  file=sys.stderr)
            for p, d in sorted(bucket.items(), key=lambda kv: -len(kv[1])):
                print(f"    {len(d):>3} {p}\n        {d}", file=sys.stderr)

    if not ok:
        print("\nMeta description audit failed.", file=sys.stderr)
        sys.exit(1)
    print(f"  meta description audit passed (all unique, "
          f"{DESC_MIN}-{DESC_MAX} chars)")


def audit_titles():
    """Fail the build on a duplicate or over-long <title>."""
    indexed = {p: v["title"] for p, v in DESC_REGISTRY.items() if not v["noindex"]}

    by_text = defaultdict(list)
    for path, t in indexed.items():
        by_text[t].append(path)
    dupes = {t: ps for t, ps in by_text.items() if len(ps) > 1}
    long_ = {p: t for p, t in indexed.items() if len(t) > TITLE_MAX}

    print(f"  titles: {len(indexed)} indexed pages, {len(by_text)} unique")

    ok = True
    if dupes:
        ok = False
        print("  DUPLICATE titles:", file=sys.stderr)
        for t, ps in dupes.items():
            print(f"    {len(ps)}x {t[:70]!r}", file=sys.stderr)
            for p in ps:
                print(f"        {p}", file=sys.stderr)
    if long_:
        ok = False
        print(f"  OVER length ({TITLE_MAX}) — {len(long_)} pages:", file=sys.stderr)
        for p, t in sorted(long_.items(), key=lambda kv: -len(kv[1])):
            print(f"    {len(t):>3} {p}\n        {t}", file=sys.stderr)

    if not ok:
        print("\nTitle audit failed.", file=sys.stderr)
        sys.exit(1)
    print(f"  title audit passed (all unique, <={TITLE_MAX} chars)")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

PT_GROUPS = []
SCHEDULE_ORDER = []
# NPS labels B16.5 publishes a flange in, so pages for sizes outside that set
# (NPS 4 1/2, 7, 9, 11, and everything above 24) do not link to a flange page
# that was never generated.
FLANGE_SIZES = set()


def main():
    global PT_GROUPS, SCHEDULE_ORDER, FLANGE_SIZES
    pipes, b165, ftypes, b1647, fittings, pt, mats, ss, errors = load()
    PT_GROUPS = pt["groups"]
    SCHEDULE_ORDER = pipes["schedule_order"]
    FLANGE_SIZES = {r["nps"] for blk in b165["classes"].values()
                    for r in blk["rows"]}
    ss_cmp = s_schedule_comparison(ss, pipes)

    if errors:
        print("Data problems found:", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        sys.exit(1)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    sizes_by_slug = {s["slug"]: s for s in pipes["sizes"]}
    sizes_by_nps = {s["nps"]: s for s in pipes["sizes"]}

    # ---- pipe ----
    for s in pipes["sizes"]:
        pipe_page(s, pipes, sizes_by_slug)
        for k in pipes["schedule_order"]:
            if k in s["walls"]:
                combo_page(s, k, pipes, b165, fittings)
    for k in pipes["schedule_order"]:
        schedule_page(k, pipes)
    for sch in ss["schedules"]:
        s_schedule_page(sch, ss, pipes, ss_cmp)
    pipes_index(pipes)

    # ---- flanges ----
    # Size-level detail pages are emitted for weld neck and blind only. Those
    # are the two types where the size adds something the class table does not
    # already say: the weld neck bore follows the schedule, and a blind's
    # weight and end load follow its diameter. The other four types would be
    # the same seven numbers under a different heading.
    detail_types = [t for t in ftypes if t["slug"] in ("weld-neck", "blind")]
    for ft in ftypes:
        for cls in ft["classes"]:
            blk = b165["classes"][cls]
            flange_class_page(ft, cls, blk, b165, ftypes)
            if ft in detail_types:
                for r in blk["rows"]:
                    flange_detail_page(ft, cls, r, blk, b165, sizes_by_nps,
                                       ftypes)
        flange_type_page(ft, b165, ftypes)
    flange_nps_list = sorted(
        {r["nps"] for blk in b165["classes"].values() for r in blk["rows"]},
        key=nps_value)
    for nps in flange_nps_list:
        flange_nps_page(nps, b165, ftypes, sizes_by_nps)
    flanges_index(ftypes, b165)
    large_flange_pages(b1647)

    # ---- fittings ----
    for f in fittings:
        fitting_page(f, fittings, pipes)
    fittings_index(fittings)

    # ---- reference (order here is the order on the index) ----
    ref_nps_dn(pipes)
    ref_schedule_chart(pipes)
    ref_stainless(ss, pipes, ss_cmp)
    ref_pt_ratings(pt)
    ref_bolt_chart(b165, ftypes)
    ref_materials(mats)
    ref_weight_chart(pipes)
    ref_face_types()
    ref_color_coding(pipes)
    reference_index()

    # ---- top level ----
    homepage(pipes, ftypes, fittings, b165)
    about_page(pipes, ftypes, fittings)
    privacy_page()
    not_found()

    audit_descriptions()
    audit_titles()

    search_index()
    copy_static()
    robots()
    webmanifest()

    urls = [("/", "1.0"), ("/pipes/", "0.9"), ("/flanges/", "0.9"),
            ("/fittings/", "0.9"), ("/reference/", "0.8"),
            ("/flanges/large/", "0.7"),
            ("/about/", "0.4"), ("/privacy/", "0.2")]
    urls += [(s["url"], "0.8") for s in pipes["sizes"]]
    urls += [(f"/pipes/nps-{s['slug']}/{sched_slug(k)}/", "0.7")
             for s in pipes["sizes"] for k in pipes["schedule_order"]
             if k in s["walls"]]
    urls += [(f"/pipes/{sched_slug(k)}/", "0.7") for k in pipes["schedule_order"]]
    urls += [(f"/pipes/schedule-{s.lower()}/", "0.7") for s in ss["schedules"]]
    for ft in ftypes:
        urls.append((f"/flanges/{ft['slug']}/", "0.8"))
        urls += [(f"/flanges/{ft['slug']}/class-{c}/", "0.7")
                 for c in ft["classes"]]
        if ft["slug"] in ("weld-neck", "blind"):
            urls += [(f"/flanges/{ft['slug']}/class-{c}/nps-{nps_slug(r['nps'])}/",
                      "0.6")
                     for c in ft["classes"]
                     for r in b165["classes"][c]["rows"]]
    urls += [(f"/flanges/nps-{nps_slug(nps)}/", "0.7")
             for nps in sorted({r["nps"] for blk in b165["classes"].values()
                                for r in blk["rows"]}, key=nps_value)]
    for key in b1647["series"]:
        urls.append((f"/flanges/large/series-{key}/", "0.6"))
    urls += [(f"/flanges/large/series-{c['series']}-class-{c['class']}/", "0.6")
             for c in b1647["combinations"] if c["tabulated"]]
    urls += [(f"/fittings/{f['slug']}/", "0.8") for f in fittings]
    urls += [(u, "0.7") for _, u, _ in REFERENCE_PAGES]
    urls += [(f"/reference/pressure-temperature-ratings/group-{g['slug']}/", "0.6")
             for g in pt["groups"]]

    seen = set()
    deduped = []
    for u, p in urls:
        if u not in seen:
            seen.add(u)
            deduped.append((u, p))
    sitemap(deduped)

    print(f"Built {len(DESC_REGISTRY)} pages, {len(deduped)} sitemap URLs, "
          f"{len(SEARCH)} search entries → {OUT}")


if __name__ == "__main__":
    main()
