#!/usr/bin/env python3
"""
PipeData.org static site generator.

Reads the YAML spec tables in data/ and writes a complete static site to docs/
(GitHub Pages serves from /docs). Run: python generate.py

Every page flows through head(), which registers its title and meta description
so audit_titles() and audit_descriptions() can fail the build on a duplicate or
an out-of-band length before anything ships.
"""

import hashlib
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

import diagrams as dg

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
STATIC = os.path.join(ROOT, "static")
OUT = os.path.join(ROOT, "docs")

SITE = "https://pipedata.org"
SITE_NAME = "PipeData"
EMAIL = "info@pipedata.org"
# Cloudflare rewrites any literal address it finds into a
# /cdn-cgi/l/email-protection link, which 404s for crawlers that do not run
# JavaScript — every page then reads as "links to a broken page". Entity-
# encoding the @ and the . hides the address from that scanner; browsers
# decode the entities in both text and the mailto href, so the link still
# works. The email_off comments around each link are Cloudflare's own
# documented opt-out and are inert HTML comments everywhere else.
EMAIL_HTML = "info&#64;pipedata&#46;org"
TODAY = date.today().isoformat()

# Dates shown on the page and carried in Article markup and the sitemap. These
# are set by hand, not taken from the clock: a rebuild that changes nothing must
# not claim the content is newer than it is. Bump CONTENT_UPDATED in the same
# commit as any change to the prose or the data.
CONTENT_PUBLISHED = "2026-07-22"
CONTENT_UPDATED = "2026-09-28"
EDITORIAL = "PipeData Editorial"


def _asset_version(*parts):
    """Short content hash, appended to an asset URL so that a changed file is
    fetched again instead of being served from a visitor's cache. Without it a
    page built against new CSS can be drawn with the old stylesheet, which for
    the inline diagrams means solid black shapes."""
    with open(os.path.join(STATIC, *parts), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


CSS_V = _asset_version("css", "style.css")
JS_V = _asset_version("js", "search.js")

# Set to a real "G-..." measurement ID to switch analytics on. Left empty the
# snippet is omitted entirely rather than shipped dead: a placeholder ID still
# costs every visitor a googletagmanager request and collects nothing.
GA_ID = "G-YQ4MS7NNDS"
GA_ENABLED = bool(GA_ID) and GA_ID != "G-XXXXXXXXXX"

# Ahrefs Web Analytics. Same rule as GA_ID: blank the key and the tag is
# dropped rather than shipped with a dead data-key.
AHREFS_KEY = "VClhZ0gJ5Zmb8aN8g5YR1Q"

# Google AdSense publisher ID. Placement is left to Auto ads, so the head tag
# is the only markup; ads.txt is written from the same ID. Blank it to drop
# both.
ADSENSE_CLIENT = "ca-pub-5861928596436289"

# Regions that get denied-by-default Consent Mode storage: the EEA (EU 27 plus
# Iceland, Liechtenstein, Norway), the UK and Switzerland. Everywhere else
# falls through to the granted default. Consent Mode matches these against
# ISO 3166-1 alpha-2, so Greece is GR. Do not write EL here: that is the
# Eurostat spelling, it matches no country, and Greek visitors would silently
# fall through to the granted fallback.
CONSENT_DENIED_REGIONS = [
    "BE", "BG", "CZ", "DK", "DE", "EE", "IE", "GR", "ES", "FR", "HR",
    "IT", "CY", "LV", "LT", "LU", "HU", "MT", "NL", "AT", "PL", "PT", "RO",
    "SI", "SK", "FI", "SE", "GB", "CH", "IS", "LI", "NO",
]

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
    for t in ftypes:
        t["classes"] = [str(c) for c in t["classes"]]
        t["max_nps"] = {str(k): str(v)
                        for k, v in (t.get("max_nps") or {}).items()}
        t["unpublished"] = [str(c) for c in t.get("unpublished") or []]
        t["unchecked"] = [str(c) for c in t.get("unchecked") or []]
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


def head(title, desc, path, ld=None, og_type="website", noindex=False,
         ads=True):
    canon = SITE + path
    DESC_REGISTRY[path] = {"desc": desc, "title": title, "noindex": noindex}
    ldblocks = "".join(ldjson(o) for o in (ld or []))
    # Head order is load-bearing for GDPR, so it is assembled in one place.
    # 1. Consent Mode defaults, before anything that reads them, in two calls:
    #    denied for the EEA/UK/CH, then a granted fallback for the rest of the
    #    world. Region-scoped defaults win over the unscoped one wherever they
    #    match, so the second call cannot loosen the first. The dataLayer and
    #    gtag() shim live here and nowhere else, because a second declaration
    #    further down would replace the queue this block filled.
    #    wait_for_update gives the CMP 500 ms to answer before tags give up;
    #    the granted fallback has nothing to wait for and so omits it.
    # 2. AdSense, which is what actually delivers the consent dialog at
    #    runtime, so it has to be the first network script on the page.
    # 3. The gtag loader and config, which now start out denied and upgrade
    #    themselves when the CMP reports a grant.
    # 4. Ahrefs last: it sets no cookies and has nothing to wait for.
    # ads=False drops the AdSense loader only. It is for pages with no content
    # of their own (the 404), where AdSense policy does not allow an ad, and
    # page() passes it for every noindex page: an ad belongs on the parent
    # page that carries the content, not on a single-row extract of it.
    parts = []
    if GA_ENABLED or ADSENSE_CLIENT:
        regions = ",".join("'%s'" % r for r in CONSENT_DENIED_REGIONS)
        parts.append(
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('consent','default',{"
            "'analytics_storage':'denied',"
            "'ad_storage':'denied',"
            "'ad_user_data':'denied',"
            "'ad_personalization':'denied',"
            "'wait_for_update':500,"
            f"'region':[{regions}]}});"
            "gtag('consent','default',{"
            "'analytics_storage':'granted',"
            "'ad_storage':'granted',"
            "'ad_user_data':'granted',"
            "'ad_personalization':'granted'});</script>"
        )
    if ADSENSE_CLIENT and ads:
        parts.append(
            '<script async src="https://pagead2.googlesyndication.com/'
            f'pagead/js/adsbygoogle.js?client={ADSENSE_CLIENT}" '
            'crossorigin="anonymous"></script>'
        )
    if GA_ENABLED:
        parts.append(
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>\n'
            "<script>gtag('js',new Date());"
            f"gtag('config','{GA_ID}');</script>"
        )
    if AHREFS_KEY:
        parts.append(
            '<script src="https://analytics.ahrefs.com/analytics.js" '
            f'data-key="{AHREFS_KEY}" async></script>'
        )
    analytics = "\n".join(parts)
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
<link rel="stylesheet" href="/css/style.css?v={CSS_V}">
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
      <a href="/compare/">Compare</a>
      <a href="/guides/">Guides</a>
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
        <h3>Compare</h3>
        <ul>
          <li><a href="/compare/">All comparisons</a></li>
          <li><a href="/compare/schedule-40-vs-schedule-80/">Sch 40 vs Sch 80</a></li>
          <li><a href="/compare/class-150-vs-class-300/">Class 150 vs 300</a></li>
          <li><a href="/compare/weld-neck-vs-slip-on-flange/">Weld neck vs slip-on</a></li>
        </ul>
      </div>
      <div>
        <h3>Guides</h3>
        <ul>
          <li><a href="/guides/">All guides</a></li>
          <li><a href="/guides/pipe-sizing/">Pipe sizing</a></li>
          <li><a href="/guides/pipe-wall-thickness-calculation/">Wall thickness</a></li>
          <li><a href="/guides/flange-bolt-torque/">Bolt torque</a></li>
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
          <li><a href="/contact/">Contact</a></li>
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
      <p>© {date.today().year} PipeData.org · <!--email_off--><a href="mailto:{EMAIL_HTML}">{EMAIL_HTML}</a><!--/email_off--></p>
    </div>
  </div>
</footer>
<script src="/js/search.js?v={JS_V}" defer></script>
</body>
</html>
"""


def page(path, title, desc, body, ld=None, og_type="website", noindex=False):
    out = os.path.join(path.strip("/"), "index.html") if path != "/" else "index.html"
    write(out, ad_free_tables(head(title, desc, path, ld, og_type, noindex,
                                   ads=not noindex)
                              + body + foot()))


# Google Auto ads pick their own insertion points, and a spec table is the one
# place on this site where an injected block breaks the thing the visitor came
# for. Every table ships inside .data-table-zone and the stylesheet hides ad
# containers within it; the first zone on a page also carries the id, since the
# id form is what AdSense's own documentation matches on. Applied to the
# finished page so the id lands once per document rather than once per table.
ZONE_DIV = '<div class="table-scroll data-table-zone">'


def ad_free_tables(html):
    return html.replace(
        ZONE_DIV, '<div id="data-table-zone" class="table-scroll data-table-zone">', 1)


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
    out = (f'{ZONE_DIV}<table class="{cls}">{cap}'
           f"<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table></div>")
    if note:
        out += f'<p class="table-note">{note}</p>'
    return out


UNITS_NOTE = ('<p class="units-note">Dimensions are given in <strong>inches</strong>, '
              'with the millimetre equivalent beneath in grey.</p>')

# --------------------------------------------------------------------------
# consolidation, cross-links and article markup
# --------------------------------------------------------------------------

def folded_into(parent_url, parent_label, what):
    """Banner for a page whose content now lives on a parent page.

    The size x schedule, flange size and flange type x class x size pages are
    kept live so that no existing link breaks, but they are noindex and out of
    the sitemap: each one is a single row of its parent's table, and several
    hundred near-identical pages is what a "low value content" review sees.
    The banner sends the reader to the page that carries the whole picture.
    """
    return ('<div class="callout folded"><p><strong>This is a single-row '
            f'extract.</strong> {what} is on '
            f'<a href="{esc(parent_url)}">{esc(parent_label)}</a>, which is '
            'the page to bookmark, link to and cite.</p></div>')


# Every comparison and guide, keyed by path, with the one line that says why a
# reader on a data page would want it. Data pages are built before the
# comparison and guide pages, so this cannot be read off COMPARE_PAGES and
# GUIDE_PAGES; audit_links() fails the build if an entry here stops resolving.
READING = {
    "/guides/pipe-sizing/": (
        "Pipe sizing by flow rate and velocity",
        "Turn a flow rate into a line size using velocity limits."),
    "/guides/pipe-wall-thickness-calculation/": (
        "Pipe wall thickness calculation",
        "Barlow and ASME B31.3, corrosion allowance and mill tolerance."),
    "/guides/hydrostatic-test-pressure/": (
        "Hydrostatic test pressure",
        "What a line and its flanges are tested to, and why."),
    "/guides/pressure-temperature-derating/": (
        "Pressure-temperature derating",
        "Reading a P-T table and interpolating between temperatures."),
    "/guides/flange-bolt-torque/": (
        "Flange bolt torque",
        "Target torque from bolt stress, for every B16.5 bolt size."),
    "/guides/flange-face-types/": (
        "Choosing a flange face type",
        "Raised face, flat face or ring-type joint, by service."),
    "/guides/pipe-material-selection/": (
        "Pipe material selection",
        "A53, A106, A312 and A335: which grade suits which duty."),
    "/guides/pipe-schedule-explained/": (
        "What a pipe schedule actually is",
        "Why a schedule is a wall series and not a thickness."),
    "/guides/nps-vs-dn-explained/": (
        "NPS vs DN explained",
        "Two designators for one pipe, and why neither is a measurement."),
    "/guides/pipe-end-connections/": (
        "Pipe end connections",
        "Bevelled, plain and threaded ends, and where each is used."),
    "/compare/schedule-40-vs-schedule-80/": (
        "Schedule 40 vs Schedule 80",
        "Wall, bore, weight and pressure capacity, size by size."),
    "/compare/schedule-10-vs-schedule-40/": (
        "Schedule 10 vs Schedule 40",
        "What the lighter wall saves and what it gives up."),
    "/compare/schedule-40-vs-schedule-160/": (
        "Schedule 40 vs Schedule 160",
        "How far the bore closes up on the heavy wall."),
    "/compare/schedule-5s-vs-schedule-10s/": (
        "Schedule 5S vs Schedule 10S",
        "The two light stainless walls side by side."),
    "/compare/std-vs-xs/": (
        "STD vs XS",
        "Where the weight classes match the numbered schedules."),
    "/compare/xs-vs-xxs/": (
        "XS vs XXS",
        "Extra strong against double extra strong."),
    "/compare/weld-neck-vs-slip-on-flange/": (
        "Weld neck vs slip-on flange",
        "One butt weld against two fillet welds."),
    "/compare/weld-neck-vs-blind-flange/": (
        "Weld neck vs blind flange",
        "A flange that joins pipe against one that closes it."),
    "/compare/slip-on-vs-threaded-flange/": (
        "Slip-on vs threaded flange",
        "Welded or screwed attachment for low-pressure service."),
    "/compare/socket-weld-vs-threaded-flange/": (
        "Socket weld vs threaded flange",
        "The two small-bore options compared."),
    "/compare/lap-joint-vs-slip-on-flange/": (
        "Lap joint vs slip-on flange",
        "A loose backing flange against a welded one."),
    "/compare/raised-face-vs-ring-type-joint/": (
        "Raised face vs ring-type joint",
        "Gasket seating at low and high pressure class."),
    "/compare/class-150-vs-class-300/": (
        "Class 150 vs Class 300",
        "Dimensions, bolting and ratings of the two commonest classes."),
    "/compare/class-300-vs-class-600/": (
        "Class 300 vs Class 600",
        "What doubling the class does to the flange."),
    "/compare/class-600-vs-class-900/": (
        "Class 600 vs Class 900",
        "The step into high-pressure bolting."),
    "/compare/class-900-vs-class-1500/": (
        "Class 900 vs Class 1500",
        "Where the two classes share dimensions and where they split."),
    "/compare/class-1500-vs-class-2500/": (
        "Class 1500 vs Class 2500",
        "The heaviest two classes B16.5 publishes."),
    "/compare/seamless-vs-welded-pipe/": (
        "Seamless vs welded pipe",
        "Joint factor, size range and cost."),
    "/compare/carbon-steel-vs-stainless-steel-pipe/": (
        "Carbon steel vs stainless steel pipe",
        "Strength, temperature and corrosion trade-offs."),
    "/compare/a106-vs-a53-pipe/": (
        "A106 vs A53 pipe",
        "Two carbon steel pipe specifications that are not the same."),
    "/compare/304-vs-316-stainless-steel-pipe/": (
        "304 vs 316 stainless steel pipe",
        "What the molybdenum in 316 buys."),
    "/compare/long-radius-vs-short-radius-elbow/": (
        "Long radius vs short radius elbow",
        "Space saved against pressure drop added."),
    "/compare/concentric-vs-eccentric-reducer/": (
        "Concentric vs eccentric reducer",
        "Which reducer goes where, and which way up."),
    "/compare/90-degree-vs-45-degree-elbow/": (
        "90° vs 45° elbow",
        "Centre-to-end dimensions and when two 45s beat one 90."),
}


def reading(paths, heading="Guides and comparisons for this page"):
    """Card block linking a data page to the articles that explain it."""
    cards = "".join(
        f'<a class="card" href="{p}"><span class="card-title">'
        f'{esc(READING[p][0])}</span><span class="card-meta">'
        f'{esc(READING[p][1])}</span></a>' for p in paths)
    return f'<h2>{esc(heading)}</h2><div class="grid">{cards}</div>'


def before_first_h2(body_html, block):
    """Put a figure after a page's opening summary and before its first
    section, which is where a reader needs the picture."""
    i = body_html.find("<h2")
    if i < 0:
        return block + body_html
    return body_html[:i] + block + body_html[i:]


def fig_pipe_section():
    return dg.figure(
        [dg.pipe_section_labelled()],
        "<strong>The three dimensions of a pipe.</strong> The outside "
        "diameter is fixed by the nominal size. The schedule sets the wall, "
        "and the inside diameter is what is left. Not to scale.")


def fig_flange_pair(slugs):
    names = [dg.FLANGE_TITLES[x] for x in slugs]
    cells = [dg.flange_section(x)
             + f'<span class="diagram-name">{esc(dg.FLANGE_TITLES[x])}</span>'
             for x in slugs]
    return dg.figure(
        cells,
        f"<strong>{esc(names[0])} and {esc(names[1].lower())} in "
        "section.</strong> Both share the same flange outside diameter, bolt "
        "circle and bolting in a given size and class. They differ in how "
        "they join the pipe. Welds are shown in orange. Not to scale.")


def fig_fitting_pair(slugs, fittings):
    by = {f["slug"]: f for f in fittings}
    cells = [dg.fitting(x)
             + f'<span class="diagram-name">{esc(by[x]["name"])}</span>'
             for x in slugs]
    return dg.figure(
        cells,
        "<strong>" + esc(by[slugs[0]]["name"]) + " and "
        + esc(by[slugs[1]]["name"].lower()) + ".</strong> Each letter is "
        "the dimension ASME B16.9 tabulates for that fitting. Not to scale.")


def fig_flange_face(b165, cls, nps="6", bore=None):
    """Flange face to scale from the data, for one size in one class."""
    blk = b165["classes"][cls]
    r = next((x for x in blk["rows"] if x["nps"] == nps), blk["rows"][-1])
    rf = b165["raised_face"].get(r["nps"])
    hole = nps_value(r["bolt"]) + 0.125
    svg_ = dg.flange_face(r["o"], r["bc"], rf, r["bolts"], hole, bore=bore,
                          title=f"NPS {r['nps']} Class {cls} flange, front view",
                          note=f"NPS {r['nps']} Class {cls}, to scale")
    return svg_, r, rf, hole


def long_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {date(y, m, d).strftime('%B')} {y}"


def byline():
    return ('<p class="byline">By ' + esc(EDITORIAL)
            + f' · Published <time datetime="{CONTENT_PUBLISHED}">'
            f'{long_date(CONTENT_PUBLISHED)}</time>'
            f' · Updated <time datetime="{CONTENT_UPDATED}">'
            f'{long_date(CONTENT_UPDATED)}</time>'
            ' · <a href="/about/#method">How the figures are checked</a></p>')


PUBLISHER_LD = {
    "@type": "Organization", "name": SITE_NAME, "url": SITE + "/",
    "email": EMAIL,
    "logo": {"@type": "ImageObject",
             "url": SITE + "/logos/logo-icon-512x512.png",
             "width": 512, "height": 512},
}


def article_ld(url, headline, desc, section):
    """Article markup for a guide or comparison.

    The author is the publication, as an Organization: the site names no
    individual, and the markup must not claim one that the page does not show.
    """
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "description": desc,
        "articleSection": section,
        "inLanguage": "en",
        "mainEntityOfPage": {"@type": "WebPage", "@id": SITE + url},
        "url": SITE + url,
        "image": SITE + "/og-default.png",
        "datePublished": CONTENT_PUBLISHED,
        "dateModified": CONTENT_UPDATED,
        "author": {"@type": "Organization", "name": EDITORIAL,
                   "url": SITE + "/about/"},
        "publisher": PUBLISHER_LD,
    }



# --------------------------------------------------------------------------
# pipe pages
# --------------------------------------------------------------------------

# Where each size actually gets used. These are statements of common practice,
# not of any standard, and are worded that way on the page. Anything numeric
# about a size is computed from data/ in pipe_page() instead of written here.
SIZE_NOTES = {
    "1/8": "This is the smallest size ASME B36.10M publishes. It turns up on "
           "gauge and instrument connections, lubrication lines and sample "
           "points rather than on process lines, and it is normally threaded: "
           "there is too little wall to butt-weld comfortably.",
    "1/4": "A size for instrument and gauge connections, lube oil and seal "
           "lines, and small air or sample tubing runs where pipe is preferred "
           "to tube. It is normally threaded or socket-welded.",
    "3/8": "The least used of the three sizes below NPS 1/2. Many piping "
           "specifications leave it off their size list altogether and step "
           "from NPS 1/4 straight to NPS 1/2, so check that fittings and "
           "valves are available before designing around it.",
    "1/2": "The smallest size for which ASME B16.5 publishes a flange and "
           "B16.9 a buttweld fitting. In process plants it is the usual size "
           "for instrument take-offs, steam tracing and small vents and "
           "drains, most often socket-welded or threaded.",
    "3/4": "Many plant piping specifications set NPS 3/4 as the minimum size "
           "for vents, drains and branch connections off a process line, "
           "because anything smaller is too easily bent or broken off. It is "
           "normally socket-welded or threaded.",
    "1": "A common size for utility drops, steam tracing supply and return, "
         "pump seal flush lines and sample lines. It is also the smallest "
         "size in which B16.9 publishes a short radius 90° elbow.",
    "1 1/4": "A standard size in building services, fire sprinkler and "
             "plumbing work, but one that many process piping specifications "
             "omit, stepping from NPS 1 to NPS 1 1/2 to hold down the number "
             "of sizes kept in stock.",
    "1 1/2": "A common size for utility headers, small pump suction and "
             "discharge lines and relief valve inlets. It sits near the top "
             "of the range that is normally socket-welded.",
    "2": "NPS 2 is where many piping specifications change construction: "
         "socket-welded or threaded at this size and below, butt-welded "
         "above. It is a common size for small process lines, utility "
         "headers and control valve stations.",
    "2 1/2": "Common in building services and fire protection, where it is "
             "the usual hose connection size, and uncommon in process plants, "
             "many of which step from NPS 2 to NPS 3.",
    "3": "A common size for small process lines and utility headers, and "
         "in many plants the smallest size that is butt-welded throughout.",
    "3 1/2": "Seldom specified in new work. B36.10M, B16.5 and B16.9 all "
             "still publish it, but most size lists step from NPS 3 to NPS 4 "
             "and stock is correspondingly thin.",
    "4": "One of the most widely used sizes in process, utility and fire "
         "water service, and one of the best stocked in every schedule.",
    "4 1/2": "Effectively obsolete. B36.10M still carries the size, but B16.5 "
             "publishes no flange for it and B16.9 no fitting, so it appears "
             "here for completeness and for anyone identifying old pipe.",
    "5": "Used in some building services and fire protection systems, but "
         "left off many process piping size lists, which step from NPS 4 to "
         "NPS 6. B16.5 and B16.9 both publish it.",
    "6": "A workhorse size for process lines, utility headers and fire water "
         "mains, stocked in every common schedule.",
    "7": "Effectively obsolete. B36.10M still carries the size, but B16.5 "
         "publishes no flange for it and B16.9 no fitting.",
    "8": "A common header and transfer line size, and the smallest size in "
         "which B36.10M publishes the full run of numbered schedules from "
         "Schedule 20 through Schedule 160.",
    "9": "Effectively obsolete. B36.10M still carries the size, but B16.5 "
         "publishes no flange for it and B16.9 no fitting.",
    "10": "A common size for main process lines, cooling water and flare "
          "sub-headers.",
    "11": "Effectively obsolete. B36.10M still carries the size, but B16.5 "
          "publishes no flange for it and B16.9 no fitting.",
    "12": "A common main header size, and the largest size in which B16.5 "
          "publishes a Class 2500 flange.",
    "14": "The first size in which the NPS number and the outside diameter "
          "in inches are the same figure. Used for main headers, cooling "
          "water and flare lines.",
    "16": "A main header size for cooling water, flare and large transfer "
          "lines.",
    "18": "A main header and large transfer line size.",
    "20": "A size for large headers, cooling water mains and tank farm "
          "transfer lines.",
    "22": "Less commonly stocked than the sizes either side of it; many "
          "piping size lists step from NPS 20 to NPS 24. B36.10M publishes "
          "no Schedule 40 wall for it.",
    "24": "The largest size covered by ASME B16.5 flanges and by the B16.9 "
          "tables on this site. Used for cooling water mains, flare headers "
          "and large transfer lines.",
    "26": "The first size beyond ASME B16.5. Flanges in this size come from "
          "ASME B16.47, and the pipe itself is typically made from rolled "
          "and welded plate rather than as seamless pipe.",
    "28": "A large-diameter size, typically supplied as welded pipe, with "
          "flanges to ASME B16.47.",
    "30": "A large-diameter size for cooling water, flare and pipeline "
          "service, typically supplied as welded pipe, with flanges to "
          "ASME B16.47.",
    "32": "A large-diameter size, typically supplied as welded pipe, with "
          "flanges to ASME B16.47.",
    "34": "A large-diameter size, typically supplied as welded pipe, with "
          "flanges to ASME B16.47.",
    "36": "The largest size tabulated on this site. B36.10M continues well "
          "beyond it. It is typically supplied as welded pipe, with flanges "
          "to ASME B16.47.",
}

# Basis for the pressure column on the size pages. Same figures as the wall
# thickness guide, so the two pages cannot disagree.
P_BASIS_S = 20000.0
P_BASIS_Y = 0.4
MILL_TOL = 0.875


def b313_pressure(od, t):
    """Internal pressure from ASME B31.3 eq. (3a) rearranged, E = W = 1.

    Returns None where t >= D/6: the equation does not apply to a wall that
    thick, and B31.3 asks for special consideration instead.
    """
    if t >= od / 6.0:
        return None
    return 2 * P_BASIS_S * t / (od - 2 * P_BASIS_Y * t)


def pipe_page(s, pipes, sizes_by_slug, b165, fittings):
    order = [k for k in pipes["schedule_order"] if k in s["walls"]]
    od = s["od"]
    nps = s["nps"]
    e_nps = esc(nps)
    rows, cap_rows = [], []
    thick_rows = 0
    for k in order:
        t = s["walls"][k]
        idd = inside_dia(od, t)
        w = weight_lbft(od, t)
        area = area_sqin(idd)
        water = gal_per_ft(idd) * WATER_LB_GAL
        rows.append([
            f'<strong id="{sched_slug(k)}">{sched_label(k)}</strong>',
            dual(t),
            dual(idd),
            dual_w(w),
            dual_w(w + water),
            n(area, 2),
            n(gal_per_ft(idd), 3),
        ])
        p_nom = b313_pressure(od, t)
        p_min = b313_pressure(od, t * MILL_TOL) if p_nom is not None else None
        if p_nom is None:
            thick_rows += 1
        cap_rows.append([
            f"<strong>{sched_label(k)}</strong>",
            f"{gpm(area, 3):,.1f}", f"{gpm(area, 6):,.1f}",
            f"{gpm(area, 10):,.1f}",
            f"{p_nom:,.0f} psig" if p_nom is not None
            else '<span class="na">—</span>',
            f"{p_min:,.0f} psig" if p_min is not None
            else '<span class="na">—</span>',
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
        ("Nominal size", f"NPS {e_nps}"),
        ("Metric designator", f"DN {s['dn']}"),
        ("Outside diameter", inch_mm(od)),
        ("Schedules listed", str(len(order))),
        ("Standard weight wall", inch_mm(std)),
    ]
    if xs:
        fact_rows.append(("Extra strong wall", inch_mm(xs)))

    # ---- what the size is ----
    if s["val"] >= 14:
        od_para = (
            f"NPS {e_nps} pipe measures {inch_mm(od)} across the outside, so "
            f"here the size designation and the outside diameter in inches "
            f"are the same number. That holds from NPS 14 upward and for no "
            f"size below it.")
    else:
        diff = od - s["val"]
        od_para = (
            f"NPS {e_nps} pipe measures {inch_mm(od)} across the outside, "
            f"which is {n(diff, 3)} in more than the {e_nps} in its name "
            f"suggests. Below NPS 14 the designation is a label inherited "
            f"from iron pipe sizing and matches neither the outside diameter "
            f"nor the bore of any schedule exactly.")
    od_para += (
        f" The metric designator is DN {s['dn']}, which is likewise a label "
        f"and not a measurement in millimetres. Whatever the schedule, the "
        f"outside diameter stays at {inch_mm(od)}: a heavier wall grows "
        f"inward and takes the difference out of the bore.")

    # ---- what the spread of schedules does, computed from both ends ----
    thin_k = min(order, key=lambda k: s["walls"][k])
    thick_k = max(order, key=lambda k: s["walls"][k])
    t_thin, t_thick = s["walls"][thin_k], s["walls"][thick_k]
    id_thin, id_thick = inside_dia(od, t_thin), inside_dia(od, t_thick)
    a_thin, a_thick = area_sqin(id_thin), area_sqin(id_thick)
    w_thin, w_thick = weight_lbft(od, t_thin), weight_lbft(od, t_thick)
    distinct = sorted({round(s["walls"][k], 4) for k in order})
    spread_para = (
        f"B36.10M lists {len(order)} designations in NPS {e_nps}, which "
        f"between them give {len(distinct)} different wall thicknesses. The "
        f"lightest is {sched_long(thin_k)} at {inch_mm(t_thin)} and the "
        f"heaviest is {sched_long(thick_k)} at {inch_mm(t_thick)}. Going from "
        f"one to the other takes the bore from {inch_mm(id_thin)} down to "
        f"{inch_mm(id_thick)}, which cuts the flow area by "
        f"{n((1 - a_thick / a_thin) * 100, 0)}% and raises the weight from "
        f"{n(w_thin, 2)} to {n(w_thick, 2)} lb/ft, a factor of "
        f"{n(w_thick / w_thin, 1)}.")

    std_id = inside_dia(od, std)
    std_w = weight_lbft(od, std)
    std_water = gal_per_ft(std_id) * WATER_LB_GAL
    handling_para = (
        f"For handling and support loads, a 20 ft length of standard weight "
        f"NPS {e_nps} pipe weighs about {n(std_w * 20, 0)} lb "
        f"({n(std_w * 20 * 0.453592, 0)} kg) empty and "
        f"{n((std_w + std_water) * 20, 0)} lb "
        f"({n((std_w + std_water) * 20 * 0.453592, 0)} kg) full of water. "
        f"The water-filled figure is the one that governs pipe support "
        f"spacing and the load during a hydrostatic test, including on lines "
        f"that will carry gas or vapour in service.")

    # The same pipe in its lightest, standard and heaviest wall, to scale.
    picks = []
    for k in (thin_k, "STD", thick_k):
        if all(abs(s["walls"][k] - s["walls"][p_]) > 1e-9 for p_ in picks):
            picks.append(k)
    wall_fig = dg.figure(
        [dg.pipe_walls(
            [(sched_label(k), od, s["walls"][k],
              f"{n(s['walls'][k], 3)} in wall · "
              f"{n(inside_dia(od, s['walls'][k]), 3)} in bore")
             for k in picks], od_label=f"NPS {nps}")],
        f"<strong>NPS {e_nps} pipe drawn to scale.</strong> Every section is "
        f"{inch_mm(od)} across the outside. Only the wall changes, and the "
        f"bore takes up the difference.", cls="wide")

    body_rows = [
        f"<h2>What NPS {e_nps} is</h2>",
        f"<p>{od_para}</p>",
        f"<p>{SIZE_NOTES[nps]}</p>",
        wall_fig,
        f'<h2 id="schedules">Every schedule in NPS {e_nps}</h2>',
        UNITS_NOTE,
        table(
            ["Schedule", "Wall thickness", "Inside diameter", "Weight, empty",
             "Weight, water filled", "Flow area (in²)", "Water (US gal/ft)"],
            rows,
            caption=(f"ASME B36.10M wall thicknesses for NPS {e_nps} "
                     f"(DN {s['dn']}), outside diameter {inch_mm(od)}."),
            note=("Inside diameter is OD − 2t. Weight is calculated for "
                  "carbon steel as w = 10.6802 × t × (OD − t) lb/ft and is the "
                  "plain-end weight, excluding coatings, linings and "
                  "fittings. Water-filled weight adds fresh water at "
                  "8.3454 lb per US gallon."),
        ),
        "<h2>Reading the table</h2>",
        f"<p>{spread_para}</p>",
        f"<p>{std_note} {xs_note} Where two designations share a wall they "
        f"are the same pipe under two names, and either may appear on a mill "
        f"certificate. The match is specific to the size, so it is the wall "
        f"thickness in inches that should go on a requisition, not the "
        f"designation alone.</p>",
        f"<p>{handling_para}</p>",
        f'<h2 id="capacity">Flow and pressure capacity by schedule</h2>',
        "<p>Two questions decide a schedule: how much the bore will carry, "
        "and how much pressure the wall will hold. The flow columns give the "
        "water flow through each bore at three velocities that bracket "
        "ordinary liquid service, from a pump suction line at the low end to "
        "a pump discharge line at the high end. The pressure columns are the "
        "ASME B31.3 pressure design equation solved for pressure, first on "
        "the nominal wall and then on the wall after the 12.5% mill "
        "under-tolerance that seamless pipe is allowed.</p>",
        table(
            ["Schedule", "Flow at 3 ft/s (US gpm)", "Flow at 6 ft/s (US gpm)",
             "Flow at 10 ft/s (US gpm)", "Pressure, nominal wall",
             "Pressure, wall less 12.5%"],
            cap_rows,
            caption=(f"Water flow and internal pressure capacity of NPS "
                     f"{e_nps} pipe by schedule."),
            note=("Flow is velocity × flow area. Pressure is "
                  "P = 2·S·t / (D − 2·Y·t) with S = 20,000 psi, E = W = 1.0 "
                  "and Y = 0.4: seamless carbon steel such as A106 Gr B at "
                  "moderate temperature, with no corrosion allowance and no "
                  "threading allowance. It is an illustration of the "
                  "arithmetic, not a design rating."
                  + (" A dash marks a wall of D/6 or more, where that "
                     "equation does not apply." if thick_rows else "")),
        ),
        '<p>The method behind both halves of that table is set out in the '
        '<a href="/guides/pipe-sizing/">pipe sizing guide</a> and the '
        '<a href="/guides/pipe-wall-thickness-calculation/">wall thickness '
        'guide</a>, each with a worked example.</p>',
    ]

    # ---- flanges in this size (folded in from the flange size pages) ----
    found = rows_for_nps(b165, nps)
    rf = b165["raised_face"]
    flange_block = ""
    if found:
        fl_rows = []
        for c, r, blk in found:
            rating = class_rating(c)
            fl_rows.append([
                f'<a href="/flanges/weld-neck/class-{c}/">'
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
        lo, hi = found[0], found[-1]
        flange_block = (
            f'<h2 id="flanges">Flanges for NPS {e_nps} pipe</h2>'
            f"<p>ASME B16.5 sizes a flange from the NPS and the pressure "
            f"class, never from the schedule, so every flange below fits "
            f"every wall in the table above. In NPS {e_nps} a Class {lo[0]} "
            f"flange is {inch_mm(lo[1]['o'], 2)} across and "
            f"{inch_mm(lo[1]['tf'], 2)} thick, held by {lo[1]['bolts']} bolts "
            f"of {esc(lo[1]['bolt'])} in. A Class {hi[0]} flange for the same "
            f"pipe is {inch_mm(hi[1]['o'], 2)} across and "
            f"{inch_mm(hi[1]['tf'], 2)} thick, on {hi[1]['bolts']} bolts of "
            f"{esc(hi[1]['bolt'])} in. Because the bolt circle moves with the "
            f"class, flanges of two different classes will not bolt together "
            f"even though both fit the pipe.</p>"
            + table(["Class", "Flange OD", "Thickness", "Bolt circle",
                     "Bolting", "Raised face OD", "Rating @ 100 °F"], fl_rows,
                    caption=f"ASME B16.5 flange dimensions in NPS {e_nps}, "
                            f"all published pressure classes.",
                    note="Ratings are for A105 carbon steel at 100 °F and "
                         "fall with temperature. Thickness excludes the "
                         "raised face. All six flange types share these "
                         "dimensions.")
            + "<p>The one flange dimension that does follow the schedule is "
              "the bore of a weld neck flange, which is machined to the "
              "inside diameter of the pipe it is welded to. A weld neck "
              f"flange for standard weight NPS {e_nps} pipe is bored "
              f"{inch_mm(std_id)}; ordered for {sched_long(thick_k)} it is "
              f"bored {inch_mm(id_thick)}. The schedule therefore belongs on "
              "every weld neck flange requisition.</p>"
            + (table(["Class", "Weld neck", "Slip-on / threaded",
                      "Raised face height"], hub_rows,
                     caption=f"ASME B16.5 length through hub, NPS {e_nps}.",
                     note="Length through hub is how far the flange stands "
                          "off the joint, which is what a spool drawing "
                          "needs. It is measured from the flange face, "
                          "without the raised face, and is tabulated here "
                          "for Classes 150 and 300. The Class 300 figures "
                          "are only partly checked.")
               if hub_rows else "")
            + '<div class="chip-links">'
            + "".join(f'<a class="chip-link" href="/flanges/{slug_}/">'
                      f'{label}</a>' for slug_, label in (
                          ("weld-neck", "Weld neck flanges"),
                          ("slip-on", "Slip-on flanges"),
                          ("blind", "Blind flanges")))
            + '<a class="chip-link" href="/reference/flange-bolt-chart/">'
              'Flange bolt chart</a>'
              '<a class="chip-link" href="/reference/'
              'pressure-temperature-ratings/">P-T ratings</a></div>')
    elif s["val"] > 24:
        flange_block = (
            f'<h2 id="flanges">Flanges for NPS {e_nps} pipe</h2>'
            f"<p>ASME B16.5 stops at NPS 24, so there is no B16.5 flange in "
            f"this size. Flanges for NPS {e_nps} pipe come from ASME B16.47, "
            f"which publishes two series, A and B, that do not bolt to each "
            f"other. The series has to be settled before anything else about "
            f"the flange is. See "
            f'<a href="/flanges/large/">large diameter flanges</a>.</p>')
    else:
        flange_block = (
            f'<h2 id="flanges">Flanges for NPS {e_nps} pipe</h2>'
            f"<p>ASME B16.5 publishes no flange in NPS {e_nps} and ASME B16.9 "
            f"no buttweld fitting. That is the practical meaning of an "
            f"obsolete size: the pipe dimensions are still in B36.10M, but "
            f"nothing standard is made to connect to it. New work uses the "
            f"next size up or down.</p>")

    # ---- fittings in this size ----
    fit_rows = []
    for f in fittings:
        v = f["rows"].get(nps)
        if v is None:
            continue
        fit_rows.append([
            f'<a href="/fittings/{f["slug"]}/">{esc(f["name"])}</a>',
            esc(f["dim_label"]), dual(v, 2),
        ])
    fit_block = ""
    if fit_rows:
        lr = next((f for f in fittings if f["slug"] == "90-degree-elbow"), None)
        lr_v = lr["rows"].get(nps) if lr else None
        fit_block = (
            f'<h2 id="fittings">Buttweld fittings for NPS {e_nps} pipe</h2>'
            f"<p>ASME B16.9 sets the dimensions of a buttweld fitting from "
            f"the NPS alone, so the figures below hold for every schedule in "
            f"the size. "
            + (f"A long radius 90° elbow in NPS {e_nps} measures "
               f"{inch_mm(lr_v, 2)} from its centre to each end, which is the "
               f"figure a piping layout adds for every change of direction. "
               if lr_v else "")
            + "The fitting is still ordered to a schedule, because its wall "
              "has to match the pipe at the weld.</p>"
            + table(["Fitting", "Dimension", "NPS " + e_nps], fit_rows,
                    caption=f"ASME B16.9 buttweld fitting dimensions in NPS "
                            f"{e_nps}."))

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

    sch40_id = inside_dia(od, sch40)
    sch40_name = ("schedule 40" if "40" in s["walls"] else "standard weight")
    q = [
        (f"What is the outside diameter of NPS {nps} pipe?",
         f"<p>NPS {e_nps} pipe has an outside diameter of "
         f"{inch_mm(od)}. That outside diameter is fixed: it is identical in "
         f"every schedule, and only the wall thickness — and therefore the "
         f"bore — changes from one schedule to the next.</p>"),
        (f"What is the inside diameter of NPS {nps} {sch40_name} pipe?",
         f"<p>{inch_mm(sch40_id)}. The {inch_mm(sch40)} wall comes off the "
         f"{inch_mm(od)} outside diameter twice, once on each side, which "
         f"leaves that bore and a flow area of "
         f"{n(area_sqin(sch40_id), 2)} in².</p>"),
        (f"How much does NPS {nps} {sch40_name} pipe weigh?",
         f"<p>{n(weight_lbft(od, sch40), 2)} lb/ft "
         f"({n(weight_lbft(od, sch40) * LBFT_TO_KGM, 2)} kg/m) as plain-end "
         f"carbon steel, on a wall thickness of {inch_mm(sch40)}. It holds "
         f"{n(gal_per_ft(sch40_id), 3)} US gallons of water per foot.</p>"),
        (f"What is the metric equivalent of NPS {nps}?",
         f"<p>DN {s['dn']}. DN is a dimensionless designator, not a measurement "
         f"— DN {s['dn']} pipe does not measure {s['dn']} mm anywhere. Its "
         f"actual outside diameter is {n(od * MM, 1)} mm.</p>"),
    ]
    faq_html, faq_ld = faq(q)

    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Pipe", "/pipes/"),
                                   (f"NPS {nps}", None)])

    # The reading list follows what the size page is actually used to decide.
    read = ["/guides/pipe-schedule-explained/", "/compare/schedule-40-vs-schedule-80/"]
    read.append("/compare/schedule-10-vs-schedule-40/" if "10" in s["walls"]
                and "40" in s["walls"] else "/compare/std-vs-xs/")
    if "160" in s["walls"] and "40" in s["walls"]:
        read.append("/compare/schedule-40-vs-schedule-160/")
    if "XXS" in s["walls"]:
        read.append("/compare/xs-vs-xxs/")
    read += ["/guides/nps-vs-dn-explained/",
             "/compare/seamless-vs-welded-pipe/",
             "/guides/pipe-material-selection/"]
    if s["val"] <= 2:
        read.append("/guides/pipe-end-connections/")

    title = fit_title(f"NPS {nps} Pipe Dimensions & Weight Chart",
                      " | PipeData")
    desc = fit_desc(
        f"NPS {nps} (DN {s['dn']}) pipe has an OD of {n(od, 3)} in "
        f"({n(od * MM, 1)} mm). ",
        [f"Wall thickness, bore, weight and flow area for all "
         f"{len(order)} ASME B36.10 schedules.",
         f"Wall thickness, bore and weight for all {len(order)} "
         f"ASME B36.10 schedules.",
         f"Full ASME B36.10 wall thickness and weight table for all "
         f"{len(order)} schedules.",
         "Wall thickness, bore and weight for every ASME B36.10 schedule."])

    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>NPS {e_nps} Pipe Dimensions</h1>'
            f'<p class="lede">Outside diameter {inch_mm(od)} — fixed across every '
            f'schedule. {len(order)} wall thicknesses from ASME B36.10M, with bore, '
            f'weight, flow and pressure capacity for each, and the flanges '
            f'and fittings that go with the size.</p></div>'
            + facts(fact_rows)
            + "".join(body_rows)
            + flange_block + fit_block
            + reading(read)
            + f'<h2>NPS {e_nps} in each schedule chart</h2>'
            f'<div class="chip-links">{sched_links}</div>'
            + faq_html
            + (f'<h2>Adjacent sizes</h2><div class="grid">{"".join(nav)}</div>'
               if nav else "")
            + "</div>")

    page(s["url"], title, desc, body, ld=[crumb_ld, faq_ld])
    index_entry(f"NPS {nps} pipe", s["url"],
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
            + fig_pipe_section()
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
            + reading(["/guides/pipe-schedule-explained/",
                       "/guides/nps-vs-dn-explained/",
                       "/guides/pipe-sizing/",
                       "/guides/pipe-wall-thickness-calculation/",
                       "/compare/schedule-40-vs-schedule-80/",
                       "/compare/seamless-vs-welded-pipe/"],
                      heading="Guides and comparisons for pipe")
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


# Where each wall series is used. Common practice, worded as such; the
# numbers beside these notes on the page are computed from data/.
SCHEDULE_NOTES = {
    "5": "Schedule 5 is the lightest wall B36.10M publishes. It is used far "
         "more in stainless steel, as Schedule 5S, than in carbon steel, "
         "where a wall this thin leaves almost nothing for corrosion. It is "
         "too thin to thread and is joined by welding or by mechanical "
         "couplings.",
    "10": "Schedule 10 is a light wall used for low-pressure service. Its "
          "best known use in carbon steel is fire sprinkler piping joined "
          "with roll-grooved couplings; in stainless, as Schedule 10S, it "
          "is a common choice for process and food-grade lines. It is too "
          "thin to thread.",
    "20": "Schedule 20 exists only in the larger sizes. It is a light wall "
          "for low-pressure, large-diameter duty such as cooling water, "
          "drainage and low-pressure gas.",
    "30": "Schedule 30 exists only in the larger sizes and sits between "
          "Schedule 20 and Schedule 40. It is used for large-diameter, "
          "low-pressure lines where Schedule 20 leaves too little margin.",
    "40": "Schedule 40 is the default wall for carbon steel pipe in general "
          "service, and the one most valves, fittings and pipe supports are "
          "stocked to suit. In small sizes it is thick enough to thread.",
    "60": "Schedule 60 is an intermediate wall between Schedule 40 and "
          "Schedule 80, published only in the larger sizes. It is seldom "
          "held in stock and is usually specified only when a calculation "
          "lands between the two.",
    "80": "Schedule 80 is the usual heavy wall. It is chosen for higher "
          "pressure, for a larger corrosion allowance, and very commonly "
          "for small-bore lines, where the extra wall gives threaded and "
          "socket-welded pipe the mechanical strength to survive being "
          "stood on or knocked.",
    "100": "Schedule 100 is an intermediate heavy wall published only in "
           "the larger sizes. It is mostly found in high-pressure steam "
           "and feed water piping and is normally made to order.",
    "120": "Schedule 120 is a heavy wall between Schedule 80 and Schedule "
           "160. It is used in high-pressure steam and process piping and "
           "is less widely stocked than either of its neighbours.",
    "140": "Schedule 140 is a heavy wall published only in the larger "
           "sizes, used in high-pressure steam and feed water piping and "
           "normally made to order.",
    "160": "Schedule 160 is the heaviest numbered schedule. It is used for "
           "high-pressure process and power piping, and in small sizes for "
           "the vents, drains and instrument connections on high-pressure "
           "lines.",
    "STD": "Standard weight is one of the three weight classes that are "
           "older than the schedule numbers. It is still the commonest way "
           "to call up ordinary carbon steel pipe, above all in the large "
           "sizes, where it means a single fixed wall.",
    "XS": "Extra strong is the heavy one of the three old weight classes. "
          "It is used much as Schedule 80 is, and in the large sizes it "
          "means a single fixed wall.",
    "XXS": "Double extra strong is the heaviest wall in B36.10M in the "
           "sizes where it exists. It is a specialised item for very high "
           "pressure in small and medium bore, and the bore that is left "
           "is small enough that flow capacity has to be checked as "
           "carefully as pressure.",
}

SCHEDULE_READING = {
    "5": ["/compare/schedule-5s-vs-schedule-10s/"],
    "10": ["/compare/schedule-10-vs-schedule-40/",
           "/compare/schedule-5s-vs-schedule-10s/"],
    "40": ["/compare/schedule-40-vs-schedule-80/",
           "/compare/schedule-10-vs-schedule-40/",
           "/compare/schedule-40-vs-schedule-160/"],
    "80": ["/compare/schedule-40-vs-schedule-80/"],
    "160": ["/compare/schedule-40-vs-schedule-160/"],
    "STD": ["/compare/std-vs-xs/"],
    "XS": ["/compare/std-vs-xs/", "/compare/xs-vs-xxs/"],
    "XXS": ["/compare/xs-vs-xxs/"],
}


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
            f'<a href="{s["url"]}#{sched_slug(k)}">all schedules →</a>',
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

    # How the wall and the pressure it holds move across the range. Computed,
    # because "a schedule keeps pressure constant" is widely repeated and the
    # data does not bear it out.
    first, last = sizes[0], sizes[-1]
    ratio_first = first["walls"][k] / first["od"]
    ratio_last = last["walls"][k] / last["od"]
    rated = [(s, b313_pressure(s["od"], s["walls"][k])) for s in sizes]
    rated = [(s, p_) for s, p_ in rated if p_ is not None]
    trend_para = (
        f"The wall in {label.lower()} is {inch_mm(first['walls'][k])} at NPS "
        f"{esc(first['nps'])} and {inch_mm(last['walls'][k])} at NPS "
        f"{esc(last['nps'])}. Measured against the pipe it belongs to, that "
        f"is {n(ratio_first * 100, 1)}% of the outside diameter at the small "
        f"end and {n(ratio_last * 100, 1)}% at the large end, so the wall "
        f"does not keep pace with the diameter.")
    if len(rated) >= 2:
        hi = max(rated, key=lambda x: x[1])
        lo = min(rated, key=lambda x: x[1])
        trend_para += (
            f" The pressure the pipe will hold moves the same way. On the "
            f"basis used throughout this site (ASME B31.3, S = 20,000 psi, "
            f"nominal wall, no corrosion allowance) {label.lower()} holds "
            f"about {hi[1]:,.0f} psig at NPS {esc(hi[0]['nps'])} and about "
            f"{lo[1]:,.0f} psig at NPS {esc(lo[0]['nps'])}. A schedule is "
            f"therefore chosen size by size, from a calculation, and not "
            f"once for a whole plant.")
    # Runs of sizes that share one wall: the weight classes do this above a
    # certain size, and it is the reason they are still in use.
    flat = [s for s in sizes if abs(s["walls"][k] - last["walls"][k]) < 1e-9]
    flat_para = ""
    if len(flat) >= 4 and flat[0]["nps"] != first["nps"]:
        flat_para = (
            f"<p>From NPS {esc(flat[0]['nps'])} up to NPS "
            f"{esc(last['nps'])} the wall is the same in every size, "
            f"{inch_mm(last['walls'][k])}. Over that range "
            f"{label.lower()} is a fixed thickness and no longer a series "
            f"that climbs with the size.</p>")

    shown = []
    for s_ in (first, sizes[len(sizes) // 2], last):
        if s_ not in shown:
            shown.append(s_)
    sched_fig = dg.figure(
        [dg.pipe_walls(
            [(f"NPS {s_['nps']}", s_["od"], s_["walls"][k],
              f"{n(s_['walls'][k], 3)} in wall on {n(s_['od'], 3)} in OD")
             for s_ in shown], od_label=label)],
        f"<strong>{esc(label)} in three sizes, each drawn to its own "
        f"scale.</strong> The sections are drawn at the same width so that "
        f"the wall can be compared with the diameter it belongs to. The wall "
        f"gets thicker as the size goes up, but it takes up a smaller share "
        f"of the pipe.", cls="wide")

    read = (SCHEDULE_READING.get(k, [])
            + ["/guides/pipe-schedule-explained/",
               "/guides/pipe-wall-thickness-calculation/",
               "/guides/pipe-sizing/"])

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
         f"<p>No. It is a series, not a thickness. The wall is "
         f"{inch_mm(first['walls'][k])} at NPS {esc(first['nps'])} and "
         f"{inch_mm(last['walls'][k])} at NPS {esc(last['nps'])}, so the "
         f"thickness always has to be read off the table for the size in "
         f"hand.</p>"),
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
            + facts(fact_rows)
            + f"<h2>Where {esc(label.lower())} is used</h2>"
            + f"<p>{SCHEDULE_NOTES[k]}</p>"
            + sched_fig
            + f"<h2>{esc(label)} in every size</h2>"
            + UNITS_NOTE
            + table(["Size", "DN", "Outside diameter", "Wall thickness",
                     "Inside diameter", "Weight, empty", "Water (US gal/ft)",
                     "This size"],
                    rows,
                    caption=f"ASME B36.10M {label.lower()} dimensions.",
                    note="Weight is plain-end carbon steel, "
                         "w = 10.6802 × t × (OD − t) lb/ft.")
            + "<h2>How the wall changes with size</h2>"
            + f"<p>{trend_para}</p>" + flat_para
            + alias_para + reading(read) + faq_html
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
            f'<a href="/flanges/weld-neck/class-{c}/">Class {c}</a>',
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
            + f'<p><a class="more" href="{s["url"]}#flanges">'
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
            + folded_into(
                s["url"] + "#" + sched_slug(k),
                f"the NPS {s['nps']} pipe dimensions page",
                f"{long_} alongside every other schedule in NPS {s['nps']}, "
                f"with flow and pressure capacity, flanges and fittings,")
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

    page(url, title, desc, body, ld=[crumb_ld], noindex=True)
    index_entry(f"NPS {s['nps']} {long_.lower()}", url,
                f"{n(t, 3)} in wall · {n(idd, 3)} in bore · {n(w, 2)} lb/ft")


# --------------------------------------------------------------------------
# flange pages
# --------------------------------------------------------------------------

# What each pressure class is for. Common practice, worded as such on the
# page; every rating quoted beside these is read from data/pt_ratings.yaml.
CLASS_NOTES = {
    "150": "Class 150 is the lightest class in ASME B16.5 and by a wide "
           "margin the most used. It covers cooling water, utility, fire "
           "water and low-pressure process service, and it is the class "
           "most pumps, vessels and valves in general service are nozzled "
           "to.",
    "300": "Class 300 is the usual step up when Class 150 runs out of "
           "pressure or temperature. It is common on hydrocarbon process "
           "lines, steam and hot oil, and it is often chosen over Class 150 "
           "for a hazardous fluid at modest pressure simply for the heavier "
           "joint.",
    "400": "Class 400 is published but seldom specified. Most piping "
           "specifications step from Class 300 straight to Class 600, so "
           "Class 400 flanges are rarely held in stock and usually have to "
           "be made to order.",
    "600": "Class 600 is the first of the high-pressure classes in everyday "
           "use, common on high-pressure steam, boiler feed water and "
           "hydrocarbon process lines.",
    "900": "Class 900 belongs to high-pressure process, injection and "
           "power piping. From this class upward the ring-type joint "
           "facing becomes a common alternative to the raised face.",
    "1500": "Class 1500 is used on high-pressure injection, wellhead and "
            "power plant feed water and steam lines. Flanges in this class "
            "are heavy enough that joint make-up is normally done with "
            "controlled tightening rather than by feel.",
    "2500": "Class 2500 is the heaviest class in ASME B16.5. It is used "
            "where pressure leaves no alternative, and at this class many "
            "designs avoid flanged joints altogether in favour of welded "
            "ones.",
}

CLASS_BAND = {"150": "low", "300": "low", "400": "mid", "600": "mid",
              "900": "high", "1500": "high", "2500": "high"}

# How each flange type sits in each band of classes. Again practice, not code.
TYPE_BAND_NOTES = {
    ("weld-neck", "low"):
        "At this class a weld neck is a choice rather than a necessity. It "
        "is picked over a slip-on where the line sees thermal or pressure "
        "cycling, where the fluid is hazardous, or where the butt weld has "
        "to be radiographed.",
    ("weld-neck", "mid"):
        "At this class the weld neck is the normal flange for welded pipe. "
        "Its tapered hub carries bending and thermal load into the pipe "
        "wall gradually, which is what the heavier service asks for.",
    ("weld-neck", "high"):
        "At this class the weld neck is, for practical purposes, the only "
        "flange used to join welded pipe. The full-penetration butt weld "
        "and the tapered hub are what let the joint match the strength of "
        "the pipe.",
    ("slip-on", "low"):
        "This is the class range the slip-on is meant for. It costs less "
        "than a weld neck, needs less accuracy when the pipe is cut to "
        "length, and is adequate for steady, low-pressure service.",
    ("slip-on", "mid"):
        "Many piping specifications stop using slip-on flanges at about "
        "this class, because the two fillet welds are harder to inspect "
        "and fatigue sooner than a butt weld. Check the project "
        "specification before choosing one here.",
    ("slip-on", "high"):
        "A slip-on is an unusual choice at this class. ASME B16.5 publishes "
        "it through NPS 24 in Class 900, through NPS 2 1/2 only in Class "
        "1500, and not at all in Class 2500. Most high-pressure "
        "specifications call for a weld neck instead.",
    ("socket-weld", "low"):
        "Socket weld flanges are a small-bore item. At this class they are "
        "used on utility and instrument lines where the pipe itself is "
        "socket-welded.",
    ("socket-weld", "mid"):
        "Socket weld flanges are a small-bore item. At this class they are "
        "typically found on small high-pressure lines such as hydraulic, "
        "chemical injection and steam tracing piping.",
    ("socket-weld", "high"):
        "Socket weld flanges are a small-bore item. In Class 1500 ASME "
        "B16.5 publishes them through NPS 2 1/2 only, and it publishes none "
        "in Class 2500. Above small bore the weld neck takes over.",
    ("lap-joint", "low"):
        "A lap joint flange is loose on the pipe and bears against a stub "
        "end, so it can be turned to line up the bolt holes. At this class "
        "it is mostly used to save money on alloy lines, since only the "
        "stub end needs to be in the expensive material.",
    ("lap-joint", "mid"):
        "A lap joint flange is loose on the pipe and bears against a stub "
        "end. It is less common at this class, because the joint has "
        "little resistance to bending; where it is used, it is for alloy "
        "lines that are dismantled often.",
    ("lap-joint", "high"):
        "A lap joint flange is loose on the pipe and bears against a stub "
        "end. It is rarely chosen at this class, where the joint is "
        "expected to carry bending as well as pressure.",
    ("threaded", "low"):
        "A threaded flange screws onto the pipe and needs no welding, which "
        "suits small utility lines and places where hot work is not "
        "allowed. It is normally kept to small sizes and steady service.",
    ("threaded", "mid"):
        "A threaded flange needs no welding. At this class it is used on "
        "small-bore lines, and the thread is sometimes seal-welded, which "
        "gives up the advantage of a weld-free joint in exchange for "
        "leak tightness.",
    ("threaded", "high"):
        "A threaded flange at this class is a specialised item for small "
        "sizes, since a pipe thread is a poor seal against high pressure "
        "and is weakened by cycling. Most specifications restrict it to "
        "places where welding is not possible.",
    ("blind", "low"):
        "A blind flange closes the end of a line or a nozzle. At this class "
        "it is the usual closure for spare nozzles, manways and the ends "
        "of headers left for future extension.",
    ("blind", "mid"):
        "A blind flange closes the end of a line or a nozzle. It is loaded "
        "in bending across its full face, which is why at this class it is "
        "already among the heaviest flanges in a given size.",
    ("blind", "high"):
        "A blind flange closes the end of a line or a nozzle. At this class "
        "the pressure load on the plate is large, and both the weight of "
        "the blind and the load on its bolts need to be allowed for in "
        "handling and in the joint design.",
}

TYPE_READING = {
    "weld-neck": ["/compare/weld-neck-vs-slip-on-flange/",
                  "/compare/weld-neck-vs-blind-flange/"],
    "slip-on": ["/compare/weld-neck-vs-slip-on-flange/",
                "/compare/slip-on-vs-threaded-flange/",
                "/compare/lap-joint-vs-slip-on-flange/"],
    "socket-weld": ["/compare/socket-weld-vs-threaded-flange/",
                    "/guides/pipe-end-connections/"],
    "lap-joint": ["/compare/lap-joint-vs-slip-on-flange/"],
    "threaded": ["/compare/slip-on-vs-threaded-flange/",
                 "/compare/socket-weld-vs-threaded-flange/"],
    "blind": ["/compare/weld-neck-vs-blind-flange/",
              "/guides/hydrostatic-test-pressure/"],
}

CLASS_READING = {
    "150": ["/compare/class-150-vs-class-300/"],
    "300": ["/compare/class-150-vs-class-300/",
            "/compare/class-300-vs-class-600/"],
    "400": ["/compare/class-300-vs-class-600/"],
    "600": ["/compare/class-300-vs-class-600/",
            "/compare/class-600-vs-class-900/"],
    "900": ["/compare/class-600-vs-class-900/",
            "/compare/class-900-vs-class-1500/"],
    "1500": ["/compare/class-900-vs-class-1500/",
             "/compare/class-1500-vs-class-2500/"],
    "2500": ["/compare/class-1500-vs-class-2500/"],
}


def type_rows(ft, cls, blk):
    """The rows of a class table that this flange type is published in."""
    top = ft["max_nps"].get(cls)
    if not top:
        return blk["rows"]
    return [r for r in blk["rows"] if r["val"] <= nps_value(top)]


def flange_unpublished_page(ft, cls, ftypes):
    """Notice kept at the address of a page for a flange ASME B16.5 does not
    publish. The site once carried a dimension table here; taking the page
    away would break links to it, and leaving the table would be wrong."""
    url = f"/flanges/{ft['slug']}/class-{cls}/"
    crumb_html, crumb_ld = crumbs([
        ("Home", "/"), ("Flanges", "/flanges/"),
        (ft["name"], f"/flanges/{ft['slug']}/"), (f"Class {cls}", None)])
    others = "".join(
        f'<a class="chip-link" href="/flanges/{t["slug"]}/class-{cls}/">'
        f'Class {cls} {esc(t["short"])}</a>'
        for t in ftypes if cls in t["classes"])
    same = "".join(
        f'<a class="chip-link" href="/flanges/{ft["slug"]}/class-{c}/">'
        f'Class {c} {esc(ft["short"])}</a>' for c in ft["classes"])
    body = (crumb_html + '<div class="wrap narrow">'
            f'<div class="page-head"><h1>Class {cls} {esc(ft["name"])}</h1>'
            f'<p class="lede">ASME B16.5 does not publish a '
            f'{esc(ft["short"])} flange in Class {cls}.</p></div>'
            '<div class="callout warn"><p><strong>Correction.</strong> This '
            'page used to show a dimension table. The figures in it were '
            f'those of other Class {cls} flange types, and they did not '
            f'describe a {esc(ft["short"])} flange that the standard '
            'publishes. The table has been withdrawn.</p></div>'
            f"<p>The {esc(ft['short'])} flange is published in "
            f"{comma_list(['Class ' + c for c in ft['classes']])}. In Class "
            f"{cls} the flange types ASME B16.5 does publish are listed "
            f"below.</p>"
            f'<h2>Flange types published in Class {cls}</h2>'
            f'<div class="chip-links">{others}</div>'
            f'<h2>{esc(ft["name"])} in other classes</h2>'
            f'<div class="chip-links">{same}</div></div>')
    page(url, f"Class {cls} {ft['short'].title()} Flange — Not Published",
         f"ASME B16.5 does not publish a {ft['short']} flange in Class {cls}. "
         f"The classes it is published in, and the flange types available "
         f"in Class {cls}.", body, ld=[crumb_ld], noindex=True)


def group_rating(slug, cls, idx):
    for g in PT_GROUPS:
        if g["slug"] == slug and cls in g["ratings"]:
            vals = g["ratings"][cls]
            return vals[idx] if idx < len(vals) else None
    return None


def flange_class_page(ft, cls, blk, b165, ftypes, sizes_by_nps):
    full_rows = blk["rows"]
    blk = dict(blk, rows=type_rows(ft, cls, blk))
    rows = []
    rf = b165["raised_face"]
    # Lap joint hub lengths are tabulated separately in B16.5 and are not in
    # data/, so the slip-on figure must not be shown under a lap joint heading.
    show_hub = (any(r.get("y_wn") for r in blk["rows"])
                and ft["slug"] not in ("blind", "lap-joint"))
    hub_key = "y_wn" if ft["slug"] == "weld-neck" else "y_so"

    headers = ["Size", "Flange OD", "Thickness", "Bolt circle", "Bolts",
               "Bolt dia.", "Raised face OD"]
    if show_hub:
        headers.append("Length thru hub")

    bolt_rows = []
    for r in blk["rows"]:
        cells = [
            f'<strong id="nps-{nps_slug(r["nps"])}">NPS {esc(r["nps"])}</strong>',
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

        bd = nps_value(r["bolt"])
        bolt_rows.append([
            f'<strong>NPS {esc(r["nps"])}</strong>',
            f'{r["bolts"]} × {esc(r["bolt"])} in',
            dual(bd + 0.125, 3),
            dual(math.pi * r["bc"] / r["bolts"], 2),
            f"{bolt_torque(bd):,.0f} lb-ft",
        ])

    smallest, largest = blk["rows"][0], blk["rows"][-1]
    rating = class_rating(cls)

    fact_rows = [
        ("Standard", "ASME B16.5"),
        ("Flange type", ft["name"]),
        ("Pressure class", f"Class {cls}"),
        ("Size range", f"NPS {smallest['nps']} – NPS {largest['nps']}"),
        ("Raised face height", inch_mm(blk["rf_height"], 2)),
    ]
    if rating:
        fact_rows.append(("Rating at 100 °F (A105)", psi_bar(rating)))

    # ---- what the class means, with the ratings read from the P-T data ----
    cs400, cs600, cs800 = (group_rating("1-1", cls, i) for i in (3, 5, 9))
    ss100, ss800 = (group_rating("2-1", cls, i) for i in (0, 9))
    rating_para = ""
    if rating and cs400 and cs600 and cs800:
        rating_para = (
            f"The number {cls} is a designation and not a pressure. A Class "
            f"{cls} flange in A105 carbon steel is rated {psi_bar(rating)} at "
            f"100 °F, {cs400} psig at 400 °F, {cs600} psig at 600 °F and "
            f"{cs800} psig at 800 °F.")
        if ss100 and ss800:
            rating_para += (
                f" In Type 304 stainless, Group 2.1, it starts at {ss100} "
                f"psig and still holds {ss800} psig at 800 °F, so the "
                f"material and the metal temperature have to be fixed before "
                f"the class can be.")
    # how the flange grows across the size range
    steps = []
    last = None
    for r in blk["rows"]:
        if r["bolts"] != last:
            steps.append((r["bolts"], r["nps"]))
            last = r["bolts"]
    step_txt = comma_list([f"{b} from NPS {esc(x)}" for b, x in steps])
    big_bolt = max(blk["rows"], key=lambda r: nps_value(r["bolt"]))
    growth_para = (
        f"Across the {len(blk['rows'])} sizes published in Class {cls}, the "
        f"flange grows from {inch_mm(smallest['o'], 2)} across and "
        f"{inch_mm(smallest['tf'], 2)} thick at NPS {esc(smallest['nps'])} to "
        f"{inch_mm(largest['o'], 2)} across and {inch_mm(largest['tf'], 2)} "
        f"thick at NPS {esc(largest['nps'])}. The bolt count goes "
        f"{step_txt}, always in multiples of four so that the holes can "
        f"straddle the centrelines. The largest bolt in the class is "
        f"{esc(big_bolt['bolt'])} in, first needed at NPS "
        f"{esc(next(r['nps'] for r in blk['rows'] if r['bolt'] == big_bolt['bolt']))}.")
    c150 = b165["classes"]["150"]
    six_here = next((r for r in blk["rows"] if r["nps"] == "6"), None)
    six_150 = next((r for r in c150["rows"] if r["nps"] == "6"), None)
    if cls != "150" and six_here and six_150:
        growth_para += (
            f" Against Class 150, an NPS 6 flange in Class {cls} is "
            f"{n(six_here['o'] - six_150['o'], 2)} in larger in diameter and "
            f"{n(six_here['tf'] / six_150['tf'], 1)} times as thick.")

    type_note = ""
    if ft["slug"] == "blind":
        type_note = ("<p>A blind flange has no bore and no hub, so the flange "
                     "outside diameter, thickness, bolt circle and bolting are "
                     "the whole of its dimensional definition. Thickness shown is "
                     "the minimum required by B16.5 — many mills supply "
                     "heavier.</p>")
    if len(blk["rows"]) < len(full_rows):
        type_note += (f"<p>ASME B16.5 publishes the {esc(ft['short'])} flange "
                      f"in Class {cls} up to NPS {esc(largest['nps'])} only. "
                      f"The Class {cls} table runs on to NPS "
                      f"{esc(full_rows[-1]['nps'])} for other flange types, "
                      f"but there is no {esc(ft['short'])} flange in those "
                      f"sizes, so they are not listed here.</p>")
    if ft["slug"] == "lap-joint":
        type_note += ("<p>Length through hub is not shown for the lap joint "
                      "flange. ASME B16.5 tabulates it separately from the "
                      "slip-on hub, and from NPS 14 upward the lap joint hub "
                      "is the longer of the two. Those figures are not yet "
                      "on this site.</p>")
    if show_hub and cls == "300":
        type_note += ("<p>Class 300 hub lengths on this page have been "
                      "checked against a second source in three sizes only. "
                      "Confirm the hub length against the standard where a "
                      "spool dimension depends on it.</p>")
    if cls in ft["unchecked"]:
        type_note += ('<div class="callout warn"><p><strong>Size range not '
                      f'confirmed.</strong> The sources we checked disagree on '
                      f'how far up the size range ASME B16.5 publishes a '
                      f'{esc(ft["short"])} flange in Class {cls}. The table '
                      f'above gives the Class {cls} flange dimensions for '
                      f'every size; confirm in the standard that the size you '
                      f'need is published as a {esc(ft["short"])} flange '
                      f'before specifying one.</p></div>')
    if blk.get("note"):
        type_note += f"<p>{esc(blk['note'])}</p>"

    # ---- the type-specific table, folded in from the per-size pages ----
    extra = ""
    if ft["slug"] == "weld-neck":
        cols = ["STD", "40", "XS", "80", "160", "XXS"]
        bore_rows = []
        for r in blk["rows"]:
            pipe = sizes_by_nps.get(r["nps"])
            if not pipe:
                continue
            bore_rows.append(
                [f'<a href="{pipe["url"]}"><strong>NPS {esc(r["nps"])}'
                 f'</strong></a>', dual(pipe["od"], 3)]
                + [dual(inside_dia(pipe["od"], pipe["walls"][c]), 3)
                   if c in pipe["walls"] else '<span class="na">—</span>'
                   for c in cols])
        extra = (
            '<h2 id="bore">Bore by pipe schedule</h2>'
            "<p>A weld neck flange is bored to the inside diameter of the "
            "pipe it is butt-welded to. Everything in the table above is "
            f"the same for every Class {cls} weld neck of a given size; the "
            "bore is not. It follows the schedule of the pipe, so one size "
            "and class covers several different part numbers, and a "
            "requisition that leaves the schedule off is incomplete. A "
            "bore that does not match leaves a step inside the joint, which "
            "disturbs the flow and concentrates stress at the root of the "
            "weld.</p>"
            + table(["Size", "Pipe OD"] + [sched_label(c) + " bore"
                                           for c in cols], bore_rows,
                    caption=f"Class {cls} weld neck flange bore for the "
                            f"common pipe schedules, from the matching ASME "
                            f"B36.10M pipe.",
                    note="Bore is the pipe inside diameter, OD − 2t. A dash "
                         "means B36.10M publishes no such schedule in that "
                         "size. Each size links to its pipe page, which "
                         "lists every schedule.",
                    cls="specs wide"))
    elif ft["slug"] == "blind":
        wt_rows = []
        for r in blk["rows"]:
            wt = math.pi / 4 * r["o"] ** 2 * r["tf"] * STEEL_LB_IN3
            face = rf.get(r["nps"])
            thrust = rating * math.pi / 4 * face ** 2 if rating and face else None
            wt_rows.append([
                f'<strong>NPS {esc(r["nps"])}</strong>',
                f"{n(wt, 1)} lb<span class=\"mm\">{n(wt * 0.453592, 1)} kg</span>",
                (f"{thrust:,.0f} lbf<span class=\"mm\">"
                 f"{n(thrust * 0.004448, 1)} kN</span>") if thrust
                else '<span class="na">—</span>',
                f"{thrust / r['bolts']:,.0f} lbf" if thrust
                else '<span class="na">—</span>',
            ])
        extra = (
            '<h2 id="weight">Weight and end load</h2>'
            "<p>With a blind flange the two figures most often wanted are "
            "not in B16.5 at all: what the flange weighs, for lifting and "
            "for support loads, and how much force the contained pressure "
            "puts on it. Both are calculated here from the dimensions "
            "above. The end load is what the bolts must resist in addition "
            "to the load needed to seat the gasket.</p>"
            + table(["Size", "Approximate weight",
                     "End thrust at rated pressure", "Thrust per bolt"],
                    wt_rows,
                    caption=f"Derived figures for Class {cls} blind flanges.",
                    note="Weight is a solid-disc estimate from the outside "
                         "diameter and the minimum thickness at "
                         "0.2836 lb/in³, and ignores the raised face, so it "
                         "runs slightly light against a real forging. End "
                         "thrust is the 100 °F rating for A105 acting on the "
                         "raised face area; it is not a bolt-load "
                         "calculation, which must also cover gasket "
                         "seating."))

    bolting = (
        '<h2 id="bolting">Bolt holes, spacing and torque</h2>'
        "<p>Bolt holes in ASME B16.5 flanges are drilled 1/8 in larger than "
        "the nominal bolt and are set to straddle the vertical and "
        "horizontal centrelines, never to sit on them. Spacing is the "
        "distance between neighbouring holes measured round the bolt "
        "circle, which shows how much room there is for a wrench or a "
        "tensioner. The torque column is a starting figure for a "
        "lubricated stud, not a joint-specific value.</p>"
        + table(["Size", "Bolting", "Bolt hole dia.", "Hole spacing",
                 "Torque for 50 ksi bolt stress"], bolt_rows,
                caption=f"Bolting geometry for Class {cls} flanges, derived "
                        f"from the ASME B16.5 bolt size and bolt circle.",
                note="Torque is T = K·F·d with nut factor K = 0.16 and a "
                     "bolt stress of 50,000 psi on the tensile stress area. "
                     'The <a href="/guides/flange-bolt-torque/">bolt torque '
                     "guide</a> gives the method and other nut factors."))

    # NPS 6 where the type is published that large, else its largest size.
    face_nps = ("6" if any(r["nps"] == "6" for r in blk["rows"])
                else largest["nps"])
    six_pipe = sizes_by_nps.get(face_nps)
    face_svg, fr, frf, fhole = fig_flange_face(
        b165, cls, face_nps,
        bore=(None if ft["slug"] == "blind" or not six_pipe else
              inside_dia(six_pipe["od"], six_pipe["walls"]["STD"])
              if ft["slug"] == "weld-neck" else six_pipe["od"]))
    class_fig = dg.figure(
        [dg.flange_section(ft["slug"]), face_svg],
        f"<strong>Left: {esc(ft['name'].lower())} in section, showing what "
        f"each column of the table measures.</strong> Welds are orange; the "
        f"section is not to scale. <strong>Right: an NPS {esc(fr['nps'])} "
        f"Class {cls} flange from the front, to scale.</strong> It is "
        f"{inch_mm(fr['o'], 2)} across, with {fr['bolts']} holes of "
        f"{n(fhole, 3)} in for {esc(fr['bolt'])} in bolts on a "
        f"{inch_mm(fr['bc'], 2)} bolt circle. The holes straddle the "
        f"centrelines.")

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
         "through 3 1/2.</p>")
        if cls != "150" else
        ("Will a Class 150 flange bolt to a Class 300 flange?",
         "<p>No. Bolt circle diameter, bolt count and bolt size all change with "
         "class, so flanges of different classes in the same NPS will not "
         "mate, even though both fit the same pipe.</p>"),
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

    read = []
    for p_ in (TYPE_READING[ft["slug"]] + CLASS_READING[cls]
               + ["/guides/pressure-temperature-derating/",
                  "/guides/flange-bolt-torque/",
                  "/guides/flange-face-types/"]
               + (["/compare/raised-face-vs-ring-type-joint/"]
                  if CLASS_BAND[cls] != "low" else [])):
        if p_ not in read:
            read.append(p_)

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
            f'NPS {esc(largest["nps"])}, with what the class is rated for '
            f'and where this flange type is used in it.</p></div>'
            + facts(fact_rows)
            + f'<h2>Where a Class {cls} {esc(ft["short"])} flange is used</h2>'
            + f"<p>{CLASS_NOTES[cls]}</p>"
            + f"<p>{TYPE_BAND_NOTES[(ft['slug'], CLASS_BAND[cls])]}</p>"
            + (f"<p>{rating_para}</p>" if rating_para else "")
            + class_fig
            + f'<h2 id="dimensions">Dimensions in every size</h2>'
            + UNITS_NOTE
            + table(headers, rows,
                    caption=f"ASME B16.5 Class {cls} {ft['short']} flange "
                            f"dimensions.",
                    note="Flange thickness excludes the raised face. Bolt "
                         "diameter is nominal; stud bolts are normally ASTM "
                         "A193 B7 with A194 2H nuts.")
            + f"<p>{growth_para}</p>"
            + type_note
            + extra
            + bolting
            + reading(read)
            + faq_html
            + f'<h2>Same flange, other classes</h2>'
            f'<div class="chip-links">{other_classes}</div>'
            + f'<h2>Same class, other flange types</h2>'
            f'<div class="chip-links">{other_types}</div>'
            + '<h2>Every class in one size</h2>'
            '<div class="chip-links">'
            + "".join(f'<a class="chip-link" href="/pipes/nps-'
                      f'{nps_slug(r["nps"])}/#flanges">NPS {esc(r["nps"])}</a>'
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
        f'<span class="card-meta">NPS '
        f'{type_rows(ft, c, b165["classes"][c])[0]["nps"]} – '
        f'{type_rows(ft, c, b165["classes"][c])[-1]["nps"]}</span>'
        f'<span class="card-spec">'
        f'{len(type_rows(ft, c, b165["classes"][c]))} sizes</span></a>'
        for c in ft["classes"])

    pros = "".join(f"<li>{esc(p)}</li>" for p in ft["pros"])
    cons = "".join(f"<li>{esc(c)}</li>" for c in ft["cons"])

    # Class 150 dimensions as the at-a-glance table on the type page.
    c150 = b165["classes"]["150"]
    rf = b165["raised_face"]
    rows = [[f'<strong>NPS {esc(r["nps"])}</strong>', dual(r["o"], 2),
             dual(r["tf"], 2), dual(r["bc"], 2), str(r["bolts"]),
             esc(r["bolt"]) + " in", dual(rf[r["nps"]], 2)]
            for r in type_rows(ft, "150", c150)]

    by_limit = defaultdict(list)
    for k, v in ft["max_nps"].items():
        by_limit[v].append(k)
    limits = [f"NPS {v} in "
              + ("Class " if len(ks) == 1 else "Classes ") + comma_list(ks)
              for v, ks in by_limit.items()]
    range_txt = ("NPS 1/2 through NPS 24, and through NPS 12 in Class 2500"
                 if not ft["max_nps"] and "2500" in ft["classes"] else
                 "NPS 1/2 through " + comma_list(limits)
                 + (", and through NPS 24 in the other classes"
                    if len(ft["max_nps"]) < len(ft["classes"]) else ""))
    not_pub = (f" It does not publish a {ft['short']} flange in "
               f"Class {comma_list(ft['unpublished']).replace(' and ', ' or ')}."
               if ft["unpublished"] else "")

    q = [
        (f"When should I use a {ft['short']} flange?",
         f"<p>{esc(ft['use'])}</p>"),
        (f"What pressure classes does a {ft['short']} flange come in?",
         f"<p>ASME B16.5 publishes the {ft['short']} flange in "
         f"{comma_list(['Class ' + c for c in ft['classes']])}, in sizes "
         f"{range_txt}.{not_pub} Above NPS 24 large-diameter flanges are "
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
                     ("Size range",
                      "NPS 1/2 – NPS "
                      + max((type_rows(ft, c, b165["classes"][c])[-1]
                             for c in ft["classes"]),
                            key=lambda r: r["val"])["nps"]),
                     ("Typical service", esc(ft["use"]))])
            + dg.figure(
                [dg.flange_section(ft["slug"]),
                 fig_flange_face(b165, "150", "6")[0]],
                f"<strong>{esc(ft['name'])} in section, and a Class 150 "
                f"flange from the front.</strong> The section shows what "
                f"each column of the dimension tables measures, with welds "
                f"in orange; it is not to scale. The front view is an NPS 6 "
                f"Class 150 flange drawn to scale, with its bolt holes "
                f"straddling the centrelines.")
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
            + reading(TYPE_READING[ft["slug"]]
                      + ["/guides/flange-face-types/",
                         "/guides/flange-bolt-torque/",
                         "/guides/pressure-temperature-derating/"])
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
            f'<a href="/flanges/weld-neck/class-{c}/">'
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
            + (folded_into(
                pipe["url"] + "#flanges",
                f"the NPS {nps} pipe dimensions page",
                f"This flange table, together with the pipe, schedule and "
                f"fitting data for NPS {nps},") if pipe else "")
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
    page(url, title, desc, body, ld=[crumb_ld], noindex=True)
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
            + folded_into(
                f"/flanges/{ft['slug']}/class-{cls}/#nps-{slug}",
                f"the Class {cls} {ft['short']} flange page",
                f"NPS {nps} alongside every other size in Class {cls}, with "
                + ("the bore for each pipe schedule"
                   if ft["slug"] == "weld-neck" else
                   "weight and end load for each size") + ",")
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
            f'<a class="chip-link" href="/pipes/nps-{slug}/#flanges">All NPS '
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
    page(url, title, desc, body, ld=[crumb_ld], noindex=True)
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
        f'<a class="card" href="/pipes/nps-{nps_slug(x)}/#flanges">'
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
            + dg.figure(
                [dg.flange_section(t["slug"], labels=False)
                 + f'<span class="diagram-name">{esc(t["name"])}</span>'
                 for t in ftypes],
                "<strong>The six ASME B16.5 flange types in section.</strong> "
                "Flange metal is hatched, the pipe is grey and welds are "
                "orange. In one size and class all six have the same outside "
                "diameter, bolt circle and bolting; they differ in how they "
                "are fixed to the pipe. Not to scale.", cls="small")
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
            '<p>Every pressure class published in one nominal size, shown '
            'on the page for that pipe size alongside its schedules and '
            'fittings — the quickest route if you already know the size and '
            'need to compare classes.</p>'
            f'<div class="grid tight">{size_cards}</div>'
            + '<h2>Larger than NPS 24</h2>'
            '<p>Flanges from NPS 26 to NPS 60 fall under ASME B16.47, in two '
            'series that will not bolt to each other. '
            '<a class="more" href="/flanges/large/">Large diameter flanges →</a></p>'
            + reading(["/guides/pressure-temperature-derating/",
                       "/guides/flange-face-types/",
                       "/guides/flange-bolt-torque/",
                       "/compare/weld-neck-vs-slip-on-flange/",
                       "/compare/class-150-vs-class-300/",
                       "/compare/raised-face-vs-ring-type-joint/"],
                      heading="Guides and comparisons for flanges")
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

FITTING_READING = {
    "90-degree-elbow": ["/compare/long-radius-vs-short-radius-elbow/",
                        "/compare/90-degree-vs-45-degree-elbow/"],
    "90-degree-elbow-short-radius": [
        "/compare/long-radius-vs-short-radius-elbow/"],
    "45-degree-elbow": ["/compare/90-degree-vs-45-degree-elbow/"],
    "180-degree-return-bend": ["/compare/long-radius-vs-short-radius-elbow/"],
    "concentric-reducer": ["/compare/concentric-vs-eccentric-reducer/"],
    "eccentric-reducer": ["/compare/concentric-vs-eccentric-reducer/"],
    "straight-tee": ["/guides/pipe-sizing/"],
    "reducing-tee": ["/guides/pipe-sizing/"],
    "straight-cross": ["/guides/pipe-sizing/"],
    "cap": ["/guides/hydrostatic-test-pressure/"],
}


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
            + dg.figure(
                [dg.fitting(f["slug"])],
                f"<strong>{esc(f['name'])}.</strong> The table below gives "
                f"{esc(f['dim_label'][0].lower() + f['dim_label'][1:])} for "
                f"each size. Not to scale.")
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
            + reading(FITTING_READING.get(f["slug"], [])
                      + ["/guides/pipe-end-connections/",
                         "/guides/pipe-material-selection/"])
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
            '<p class="lede">Centre-to-end dimensions for elbows, returns, tees, '
            'crosses, reducers and caps, NPS 1/2 through NPS 24. Because ASME '
            'B16.9 dimensions depend on nominal size alone, one table covers '
            'every schedule.</p></div>'
            f'<div class="grid">{cards}</div>'
            + dg.figure(
                [dg.fitting(f["slug"])
                 + f'<span class="diagram-name">{esc(f["name"])}</span>'
                 for f in fittings],
                "<strong>The ten buttweld fittings on this site.</strong> "
                "Each letter is the dimension ASME B16.9 tabulates for that "
                "fitting: A and B for elbows, O and K for return bends, C "
                "and M for tees and crosses, H for reducers and E for caps. "
                "Not to scale.", cls="small")
            + '<h2>Fittings side by side</h2>'
            '<p>The governing dimension of each fitting at four common sizes.</p>'
            + UNITS_NOTE
            + table(["Fitting", "NPS 2", "NPS 6", "NPS 12", "NPS 24"], rows,
                    caption="Governing ASME B16.9 dimension by fitting type. "
                            "Each column is the fitting's own dimension — "
                            "centre-to-end for elbows, tees and crosses, "
                            "centre-to-centre for 180° returns, end-to-end for "
                            "reducers, length for caps.")
            + reading(["/compare/long-radius-vs-short-radius-elbow/",
                       "/compare/90-degree-vs-45-degree-elbow/",
                       "/compare/concentric-vs-eccentric-reducer/",
                       "/guides/pipe-end-connections/"],
                      heading="Guides and comparisons for fittings")
            + faq_html + "</div>")

    title = "Buttweld Fitting Dimensions — ASME B16.9 | PipeData"
    desc = ("ASME B16.9 buttweld fitting dimensions for elbows, returns, tees, "
            "crosses, reducers and caps, NPS 1/2 to 24, for every schedule.")
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


REF_READING = {
    "nps-dn-conversion": ["/guides/nps-vs-dn-explained/",
                          "/guides/pipe-schedule-explained/"],
    "schedule-chart": ["/guides/pipe-schedule-explained/",
                       "/compare/schedule-40-vs-schedule-80/",
                       "/compare/std-vs-xs/",
                       "/guides/pipe-wall-thickness-calculation/"],
    "stainless-pipe-schedules": ["/compare/schedule-5s-vs-schedule-10s/",
                                 "/compare/304-vs-316-stainless-steel-pipe/",
                                 "/compare/carbon-steel-vs-stainless-steel-pipe/"],
    "pressure-temperature-ratings": ["/guides/pressure-temperature-derating/",
                                     "/guides/hydrostatic-test-pressure/",
                                     "/compare/class-150-vs-class-300/",
                                     "/compare/class-300-vs-class-600/"],
    "flange-bolt-chart": ["/guides/flange-bolt-torque/",
                          "/compare/class-150-vs-class-300/",
                          "/compare/weld-neck-vs-slip-on-flange/"],
    "material-grades": ["/guides/pipe-material-selection/",
                        "/compare/a106-vs-a53-pipe/",
                        "/compare/seamless-vs-welded-pipe/",
                        "/compare/304-vs-316-stainless-steel-pipe/"],
    "pipe-weight-chart": ["/compare/schedule-40-vs-schedule-80/",
                          "/compare/schedule-10-vs-schedule-40/",
                          "/guides/pipe-sizing/"],
    "flange-face-types": ["/guides/flange-face-types/",
                          "/compare/raised-face-vs-ring-type-joint/",
                          "/guides/flange-bolt-torque/"],
    "pipe-color-coding": ["/guides/pipe-material-selection/",
                          "/guides/nps-vs-dn-explained/"],
}


REF_FIGS = {
    "nps-dn-conversion": fig_pipe_section,
    "schedule-chart": fig_pipe_section,
    "pipe-weight-chart": fig_pipe_section,
    "stainless-pipe-schedules": fig_pipe_section,
}


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
            + (before_first_h2(body_html, REF_FIGS[slug]())
               if slug in REF_FIGS else body_html)
            + (reading(REF_READING[slug]) if slug in REF_READING else "")
            + extra + "</div>")
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


# What each material group is chosen for. Common practice; the comparison
# against carbon steel printed beside it is computed from the rating tables.
GROUP_NOTES = {
    "1-1": "This is the ordinary carbon steel group, and the table most "
           "flange ratings are quoted from. It suits water, steam, air and "
           "hydrocarbon service at moderate temperature. Carbon steel is "
           "not normally used for long periods above about 800 °F, where "
           "the carbide in the steel can slowly turn to graphite and the "
           "metal loses strength.",
    "1-2": "These are carbon, carbon-manganese and nickel steels, most of "
           "them castings and impact-tested grades. They are chosen mainly "
           "for their toughness at low temperature, so the group often "
           "appears on cold service where ordinary A105 is not permitted.",
    "2-1": "This is Type 304, the most widely used austenitic stainless "
           "steel. It is chosen for corrosion resistance, for cleanliness, "
           "for very low temperatures, and for temperatures beyond the "
           "useful range of carbon steel.",
    "2-2": "This is Type 316 and Type 317, the austenitic stainless steels "
           "that contain molybdenum. They are chosen over Type 304 where "
           "chlorides or aggressive process chemistry would pit it, and "
           "they carry a slightly higher rating than Type 304 when hot.",
    "2-3": "These are the low-carbon L grades of austenitic stainless "
           "steel. The lower carbon protects the weld zone from "
           "intergranular corrosion, and it costs some strength, so the "
           "ratings sit below those of the standard grades and the table "
           "stops at a lower temperature.",
    "1-9": "This is the 1¼Cr-½Mo chrome-moly group. The chromium and "
           "molybdenum give the steel creep strength, which is why it is "
           "used for steam and hot hydrocarbon service at temperatures "
           "where carbon steel has faded.",
    "1-10": "This is the 2¼Cr-1Mo chrome-moly group. It is used for "
            "high-temperature steam and for hot hydrogen service in "
            "refineries, where the alloy content resists both creep and "
            "hydrogen attack.",
    "1-15": "This is the 9Cr-1Mo-V group, commonly called Grade 91. It is "
            "the strongest of the chrome-moly steels on this site and is "
            "used for main steam and hot reheat lines in power plants. It "
            "depends on correct heat treatment after welding to reach "
            "its strength.",
}


def ref_pt_ratings(pt):
    temps = pt["temperatures"]
    cards = "".join(
        f'<a class="card" href="/reference/pressure-temperature-ratings/'
        f'group-{g["slug"]}/"><span class="card-title">{esc(g["name"])}</span>'
        f'<span class="card-meta">{esc(g["materials"][:60])}…</span>'
        f'<span class="card-spec">{len(g["ratings"])} classes</span></a>'
        for g in pt["groups"])

    g11 = next(g for g in pt["groups"] if g["slug"] == "1-1")
    g22 = next(g for g in pt["groups"] if g["slug"] == "2-2")
    i900 = temps.index(900)
    cross = next(t for i, t in enumerate(temps)
                 if g22["ratings"]["300"][i] > g11["ratings"]["300"][i])
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
            "<p>Carbon steel starts higher and falls away steeply once it "
            "is hot. Austenitic stainless starts lower — a Class 300 flange "
            f"in Type 316 is {g22['ratings']['300'][0]} psig at ambient "
            f"against carbon steel's {g11['ratings']['300'][0]} — but at "
            f"900 °F the stainless is still holding {g22['ratings']['300'][i900]} "
            f"psig while the carbon steel has dropped to "
            f"{g11['ratings']['300'][i900]}. From {cross} °F upward the "
            "stainless is the stronger flange, which is why high-temperature "
            "service specifies it even where corrosion is not the "
            "concern.</p>"
            f'<h2>All material groups</h2><div class="grid">{cards}</div>')

    q = [
        ("What pressure is a Class 150 flange rated for?",
         f"<p>{g11['ratings']['150'][0]} psig at 100 °F in A105 carbon "
         f"steel, falling to {g11['ratings']['150'][temps.index(600)]} psig "
         f"at 600 °F and {g11['ratings']['150'][-1]} psig at 1000 °F. In "
         f"Type 316 stainless it is {g22['ratings']['150'][0]} psig at "
         f"100 °F. The class number itself is not a pressure.</p>"),
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
    n_groups = len(pt["groups"])
    ref("pressure-temperature-ratings",
        "ASME B16.5 Pressure-Temperature Rating Chart | PipeData",
        f"ASME B16.5 pressure-temperature ratings in psig for all seven flange "
        f"classes across {n_groups} material groups, -20 °F to 1000 °F. A Class "
        f"150 flange is not 150 psi.",
        "Pressure-Temperature Ratings",
        "What a flange class is actually rated for, at your material and your "
        f"metal temperature. {n_groups} ASME B16.5 material groups, seven "
        "pressure classes, -20 °F to 1000 °F.",
        body, faq_pairs=q, card_meta=f"{n_groups} groups · 7 classes · psig")

    # per-group pages
    for g in pt["groups"]:
        r150 = g["ratings"]["150"]
        n_temps = len(r150)
        g_temps = temps[:n_temps]
        max_t = g_temps[-1]
        headers = ["Class"] + [f"{t} °F" for t in g_temps]
        rows = [[f"<strong>Class {c}</strong>"]
                + [str(v) for v in g["ratings"][c]]
                for c in ["150", "300", "400", "600", "900", "1500", "2500"]
                if c in g["ratings"]]
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

        # How the group behaves, read from its own Class 300 row and set
        # against carbon steel at the same temperatures.
        r300 = g["ratings"]["300"]
        base = next(x for x in pt["groups"] if x["slug"] == "1-1")["ratings"]["300"]
        held = r300[n_temps - 1] / r300[0] * 100
        behave = (
            f"A Class 300 flange in {g['name']} is rated {r300[0]} psig at "
            f"100 °F, {r300[5]} psig at 600 °F and {r300[n_temps - 1]} psig "
            f"at {max_t} °F, which is {n(held, 0)}% of its cold rating. "
            f"Every other class in the group follows the same curve, scaled "
            f"by the class.")
        if g["slug"] != "1-1":
            cross = next((g_temps[i] for i in range(n_temps)
                          if r300[i] > base[i]), None)
            always = all(r300[i] >= base[i] for i in range(n_temps))
            if always:
                behave += (
                    f" It is at or above Group 1.1 carbon steel at every "
                    f"temperature in the table: at {max_t} °F carbon steel "
                    f"is down to {base[n_temps - 1]} psig in the same "
                    f"class.")
            elif cross:
                behave += (
                    f" Group 1.1 carbon steel is rated {base[0]} psig at "
                    f"100 °F, so this group starts "
                    + ("lower" if r300[0] < base[0] else "level")
                    + f". It passes carbon steel at {cross} °F, where it "
                    f"is rated {r300[g_temps.index(cross)]} psig against "
                    f"{base[g_temps.index(cross)]} psig.")
            else:
                behave += (
                    f" Group 1.1 carbon steel is rated higher at every "
                    f"temperature in this table; at {max_t} °F it is "
                    f"{base[n_temps - 1]} psig in the same class.")
        use_html = (
            f"<h2>What {esc(g['name'])} is used for</h2>"
            f"<p>{GROUP_NOTES[g['slug']]}</p>"
            f"<h2>Reading the ratings</h2>"
            f"<p>{behave}</p>"
            "<p>Each figure is the highest gauge pressure the flange may "
            "see at that metal temperature. To use the table, fix the "
            "material group and the design temperature first, read down to "
            "find the lowest class whose rating is at or above the design "
            "pressure, and interpolate between columns if the temperature "
            "falls between them. The rating of the flange is often what "
            "limits a line, since the pipe wall is usually good for "
            "more.</p>")
        g_read = reading(
            ["/guides/pressure-temperature-derating/",
             "/guides/hydrostatic-test-pressure/",
             "/guides/pipe-material-selection/",
             ("/compare/304-vs-316-stainless-steel-pipe/"
              if g["slug"].startswith("2-") else
              "/compare/carbon-steel-vs-stainless-steel-pipe/"),
             "/compare/class-150-vs-class-300/"])

        b = (crumb_html + '<div class="wrap">'
             f'<div class="page-head"><h1>{esc(g["name"])} Pressure-Temperature '
             f'Ratings</h1><p class="lede">{esc(g["blurb"])}</p></div>'
             + facts([("Standard", "ASME B16.5"), ("Group", esc(g["name"])),
                      ("Materials", esc(g["materials"]))]
                     + ([("Pipe normally used with it", esc(g["pipe"]))]
                        if g.get("pipe") else [])
                     + [
                      ("Class 150 at 100 °F", psi_bar(r150[0])),
                      ("Class 150 at 600 °F", psi_bar(r150[5]))])
             + table(headers, rows,
                     caption=f"ASME B16.5 pressure-temperature ratings for "
                             f"{g['name']} materials, in psig.",
                     note="Interpolation between listed temperatures is "
                          "permitted; extrapolation is not."
                          + ("" if max_t >= 1000 else
                             f" ASME B16.5 does not publish a rating for this "
                             f"group above {max_t} °F."))
             + (f'<div class="callout warn"><p><strong>How far this table '
                f'has been checked.</strong> {esc(g["caveat"])} None of the '
                f'values on this site has been checked against the standard '
                f'itself; see <a href="/about/#method">how the figures are '
                f'checked</a>.</p></div>' if g.get("caveat") else "")
             + use_html + g_read
             + fh
             + f'<h2>Other material groups</h2>'
               f'<div class="chip-links">{others}</div></div>')

        t = fit_title(f"ASME B16.5 {g['name']} P-T Ratings", " | PipeData")
        d = fit_desc(
            f"ASME B16.5 {g['name']} pressure-temperature ratings: Class 150 is "
            f"{r150[0]} psig at 100 °F and {r150[5]} psig at 600 °F. ",
            [f"Full psig table for all seven classes, -20 to {max_t} °F.",
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
            + diverge
            + reading(["/compare/schedule-5s-vs-schedule-10s/",
                       "/compare/carbon-steel-vs-stainless-steel-pipe/",
                       "/compare/304-vs-316-stainless-steel-pipe/",
                       "/guides/pipe-schedule-explained/"])
            + faq_html
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


def ref_face_types(b165):
    # The worked figure is read from the data, so it follows a correction.
    blk300 = b165["classes"]["300"]
    six = next(r for r in blk300["rows"] if r["nps"] == "6")
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
            f"Class 300 NPS 6 flange is listed at {n(six['tf'], 2)} in and "
            f"measures {n(six['tf'] + blk300['rf_height'], 2)} in overall "
            "with its 1/16 in raised face. Class 400 and above add 1/4 in. "
            "This matters when calculating stud bolt length. Older "
            "catalogues and editions before 2003 print the Class 150 and "
            "300 thickness with the raised face included, so a figure "
            "1/16 in larger than the one here is the same flange measured "
            "the other way.</p>")

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
            "<h2>What is in this section</h2>"
            "<p>The pipe, flange and fitting pages each answer a question "
            "about one size or one class. The tables here answer questions "
            "that run across the whole range: every size against its metric "
            "designator, every schedule against every size, every pressure "
            "class against temperature. They are the charts that used to "
            "be pinned to the wall of a drawing office.</p>"
            f'<div class="grid">{cards}</div>'
            "<h2>Which table answers which question</h2>"
            "<ul>"
            '<li><strong>A drawing gives a DN and you need the inch '
            'size.</strong> Use the <a href="/reference/nps-dn-conversion/">'
            'NPS to DN conversion chart</a>. Both are labels, so the chart '
            'also gives the true outside diameter.</li>'
            '<li><strong>You need a wall thickness and know the size and '
            'schedule.</strong> Use the <a href="/reference/schedule-chart/">'
            'pipe schedule chart</a>, or for stainless the '
            '<a href="/reference/stainless-pipe-schedules/">stainless '
            'schedule table</a>, which shows where the S schedules part '
            'from the carbon steel ones.</li>'
            '<li><strong>You are estimating a lift or a support '
            'load.</strong> Use the <a href="/reference/pipe-weight-chart/">'
            'pipe weight chart</a>, and remember to add the contents.</li>'
            '<li><strong>You need to know what a flange class will '
            'hold.</strong> Use the '
            '<a href="/reference/pressure-temperature-ratings/">'
            'pressure-temperature ratings</a>, choosing the material group '
            'first and the temperature second.</li>'
            '<li><strong>You are making up a flanged joint.</strong> The '
            '<a href="/reference/flange-bolt-chart/">flange bolt chart</a> '
            'gives the number and size of bolts, and the '
            '<a href="/reference/flange-face-types/">flange face '
            'reference</a> the facing and gasket.</li>'
            '<li><strong>You have an ASTM grade on a certificate.</strong> '
            'Use the <a href="/reference/material-grades/">material '
            'grades</a> table to see what product form and steel it '
            'is.</li>'
            '<li><strong>You are labelling finished pipework.</strong> Use '
            'the <a href="/reference/pipe-color-coding/">pipe colour '
            'coding</a> page for marker colours and letter heights.</li>'
            "</ul>"
            "<h2>Where the method is explained</h2>"
            "<p>A chart gives the figure and leaves the reasoning out. The "
            '<a href="/guides/">guides</a> supply the reasoning, with a '
            'worked example for each, and the <a href="/compare/">'
            "comparisons</a> set two choices side by side when the "
            "question is which of them to use.</p>"
            + reading(["/guides/pipe-schedule-explained/",
                       "/guides/nps-vs-dn-explained/",
                       "/guides/pressure-temperature-derating/",
                       "/guides/pipe-material-selection/"],
                      heading="Guides that go with these tables")
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
# comparison and guide pages
# --------------------------------------------------------------------------

COMPARE_PAGES = []
GUIDE_PAGES = []


# Figures for guides and comparisons, keyed by path. Filled in main() once the
# data is loaded, since the fitting figures take their names from it.
ARTICLE_FIGS = {}


def _section_page(section_slug, section_name, registry, slug, title, desc, h1,
                  lede, body_html, ld_extra=None, faq_pairs=None, card_meta=""):
    """Emit a /<section>/<slug>/ page and register it for that section index.

    Comparisons and guides differ only in which index they land on, so they
    share one emitter rather than two that drift apart.
    """
    crumb_html, crumb_ld = crumbs([("Home", "/"),
                                   (section_name, f"/{section_slug}/"),
                                   (h1, None)])
    url = f"/{section_slug}/{slug}/"
    ld = ([crumb_ld, article_ld(url, h1, desc, section_name)]
          + (ld_extra or []))
    extra = ""
    if faq_pairs:
        fh, fl = faq(faq_pairs)
        extra = fh
        ld.append(fl)
    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(h1)}</h1>'
            f'<p class="lede">{lede}</p>' + byline() + '</div>'
            + (before_first_h2(body_html, ARTICLE_FIGS[url]())
               if url in ARTICLE_FIGS else body_html)
            + extra + "</div>")
    page(url, title, desc, body, ld=ld, og_type="article")
    registry.append((h1, url, card_meta))
    index_entry(h1, url, card_meta or section_name)


def compare(slug, title, desc, h1, lede, body_html, **kw):
    _section_page("compare", "Compare", COMPARE_PAGES, slug, title, desc, h1,
                  lede, body_html, **kw)


def guide(slug, title, desc, h1, lede, body_html, **kw):
    _section_page("guides", "Guides", GUIDE_PAGES, slug, title, desc, h1,
                  lede, body_html, **kw)


def verdict(text):
    """Answer-first summary box. Comparison queries want the answer above the
    fold, and the same sentence is what a featured snippet lifts."""
    return f'<div class="callout"><p><strong>Short answer.</strong> {text}</p></div>'


def vs_columns(a_title, a_points, b_title, b_points):
    """Two stacked bullet lists — the qualitative half of a comparison."""
    def col(t, pts):
        lis = "".join(f"<li>{p}</li>" for p in pts)
        return f"<div><h3>{esc(t)}</h3><ul>{lis}</ul></div>"
    return (f'<div class="two-col">{col(a_title, a_points)}'
            f"{col(b_title, b_points)}</div>")


def pct(new, old):
    """Signed percentage change, rendered for a table cell."""
    if old == 0:
        return '<span class="na">—</span>'
    d = (new - old) / old * 100
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.0f}%"


# --------------------------------------------------------------------------
# schedule-vs-schedule comparisons
# --------------------------------------------------------------------------

def wall_pairs(pipes, a, b):
    """Every NPS that publishes both schedules, with the derived figures.

    Both sides are computed from the same two published numbers (OD and wall),
    so the weight and bore columns cannot disagree with the schedule pages.
    """
    out = []
    for s in pipes["sizes"]:
        if a in s["walls"] and b in s["walls"]:
            ta, tb = s["walls"][a], s["walls"][b]
            out.append({
                "s": s, "ta": ta, "tb": tb,
                "ida": inside_dia(s["od"], ta), "idb": inside_dia(s["od"], tb),
                "wa": weight_lbft(s["od"], ta), "wb": weight_lbft(s["od"], tb),
            })
    return out


def sched_compare_table(rows, a, b, link=True):
    la, lb = sched_label(a), sched_label(b)
    trs = []
    for r in rows:
        s = r["s"]
        cell = (f'<a href="/pipes/nps-{s["slug"]}/">NPS {esc(s["nps"])}</a>'
                if link else f'NPS {esc(s["nps"])}')
        trs.append([
            f"<strong>{cell}</strong>",
            dual(s["od"], 3),
            dual(r["ta"], 3), dual(r["tb"], 3),
            dual(r["ida"], 3), dual(r["idb"], 3),
            dual_w(r["wa"]), dual_w(r["wb"]),
            pct(r["wb"], r["wa"]),
            pct(area_sqin(r["idb"]), area_sqin(r["ida"])),
        ])
    return table(
        ["NPS", "OD", f"{la} wall", f"{lb} wall", f"{la} bore", f"{lb} bore",
         f"{la} weight", f"{lb} weight", "Weight change", "Flow area change"],
        trs,
        caption=f"{sched_long(a)} against {sched_long(b)}, every size that "
                f"publishes both — ASME B36.10M.",
        note="Bore, weight and flow area are derived from the published outside "
             "diameter and wall thickness. Weight is bare steel pipe: "
             "w = 10.6802 × t × (OD − t).")


def sched_spread(rows):
    """Summary statistics that the prose quotes, computed from the rows.

    `wt_avg` and `fa_avg` describe b relative to a — the penalty of moving up
    to the heavier schedule. `wt_rev` and `fa_rev` describe a relative to b,
    for a page that frames the comparison the other way round: a wall that is
    89% lighter is not the same claim as one 89% heavier, and using the wrong
    one inverts the sentence.
    """
    wt = [(r["wb"] - r["wa"]) / r["wa"] * 100 for r in rows if r["wa"]]
    fa = [(area_sqin(r["idb"]) - area_sqin(r["ida"])) / area_sqin(r["ida"]) * 100
          for r in rows]
    wt_r = [(r["wa"] - r["wb"]) / r["wb"] * 100 for r in rows if r["wb"]]
    fa_r = [(area_sqin(r["ida"]) - area_sqin(r["idb"])) / area_sqin(r["idb"]) * 100
            for r in rows]
    return {"n": len(rows),
            "wt_lo": min(wt), "wt_hi": max(wt), "wt_avg": sum(wt) / len(wt),
            "fa_lo": min(fa), "fa_hi": max(fa), "fa_avg": sum(fa) / len(fa),
            "wt_rev": sum(wt_r) / len(wt_r), "fa_rev": sum(fa_r) / len(fa_r),
            "first": rows[0]["s"]["nps"], "last": rows[-1]["s"]["nps"]}


def cmp_sched_40_80(pipes):
    a, b = "40", "80"
    rows = wall_pairs(pipes, a, b)
    st = sched_spread(rows)
    four = next(r for r in rows if r["s"]["nps"] == "4")

    body = (facts([("Sizes with both", f"{st['n']} (NPS {st['first']} – NPS {st['last']})"),
                   ("Typical weight penalty", f"+{st['wt_avg']:.0f}% for Sch 80"),
                   ("Typical bore loss", f"{st['fa_avg']:.0f}% flow area"),
                   ("Same OD?", "Yes — always")])
            + verdict(
                "Schedule 80 is the thicker wall. Both schedules share the same "
                "outside diameter, so Schedule 80 buys pressure capacity by "
                "eating into the bore: across the "
                f"{st['n']} sizes that publish both, it adds "
                f"{st['wt_avg']:.0f}% weight on average and gives up "
                f"{abs(st['fa_avg']):.0f}% of the flow area. Use Schedule 40 for "
                "ordinary service and Schedule 80 where pressure, corrosion "
                "allowance or mechanical abuse demands the metal.")
            + "<h2>The outside diameter never changes</h2>"
            "<p>This is the single fact that makes schedules work. "
            f"NPS 4 pipe is {inch_mm(four['s']['od'])} on the outside in every "
            "schedule from 5 to 160. Schedule is a wall thickness series, so "
            "moving up a schedule pushes the wall inward: the fittings, flanges "
            "and supports that clamp the outside are unaffected, and the bore "
            "shrinks.</p>"
            f"<p>At NPS 4 the wall goes from {inch_mm(four['ta'])} to "
            f"{inch_mm(four['tb'])} — {(four['tb'] / four['ta'] - 1) * 100:.0f}% "
            f"more metal — and the bore falls from {inch_mm(four['ida'])} to "
            f"{inch_mm(four['idb'])}. Flow area drops from "
            f"{n(area_sqin(four['ida']), 2)} in² to "
            f"{n(area_sqin(four['idb']), 2)} in², a loss of "
            f"{abs((area_sqin(four['idb']) - area_sqin(four['ida'])) / area_sqin(four['ida']) * 100):.0f}%. "
            "Pumping the same volume through it costs more head.</p>"
            + UNITS_NOTE
            + sched_compare_table(rows, a, b)
            + "<h2>Pressure capacity, roughly</h2>"
            "<p>Barlow's formula puts the burst-limited pressure in proportion "
            "to wall thickness over diameter, so at the same allowable stress "
            "Schedule 80 carries close to the wall-thickness ratio more "
            f"pressure. At NPS 4 that ratio is {four['tb'] / four['ta']:.2f}, so "
            "the Schedule 80 pipe is good for roughly "
            f"{(four['tb'] / four['ta'] - 1) * 100:.0f}% more internal pressure "
            "than the Schedule 40 pipe in the same material. The exact number "
            "needs the design code — see the "
            '<a href="/guides/pipe-wall-thickness-calculation/">wall thickness '
            "calculation guide</a>.</p>"
            + vs_columns(
                "Choose Schedule 40 when",
                ["Pressure is modest and the line is not cyclic",
                 "Flow area matters — pumping cost follows the bore",
                 "The line is above ground, protected and inspectable",
                 "Cost and weight drive the design; Sch 40 is the stocked default",
                 "Threading is not required above NPS 2"],
                "Choose Schedule 80 when",
                ["Design pressure exceeds what Schedule 40 allows",
                 "The service is corrosive or erosive and needs a corrosion allowance",
                 "The pipe will be threaded — threads cut into the wall, and "
                 "many codes will not permit threading Schedule 40 in small bore",
                 "The line is buried, in a rack subject to impact, or in a "
                 "vibration-prone service",
                 "Steam, compressed gas or any stored-energy service"])
            + "<h2>Where STD and XS come in</h2>"
            "<p>Standard weight and Extra Strong are the older designations. "
            "Through NPS 10, STD and Schedule 40 are the same wall; through "
            "NPS 8, XS and Schedule 80 are the same wall. Above those sizes the "
            "two series split — STD freezes at 0.375 in and XS at 0.500 in while "
            "the numbered schedules keep climbing. The "
            '<a href="/compare/std-vs-xs/">STD vs XS comparison</a> works '
            "through where each pairing breaks.</p>"
            + '<h2>Both schedules, size by size</h2><div class="chip-links">'
            + "".join(
                f'<a class="chip-link" href="/pipes/nps-{r["s"]["slug"]}/#schedule-40">'
                f'NPS {esc(r["s"]["nps"])} Sch 40</a>' for r in rows[:14])
            + '</div><div class="chip-links">'
            + "".join(
                f'<a class="chip-link" href="/pipes/nps-{r["s"]["slug"]}/#schedule-80">'
                f'NPS {esc(r["s"]["nps"])} Sch 80</a>' for r in rows[:14])
            + "</div>"
            + '<div class="callout"><p>Full tables: '
            '<a href="/pipes/schedule-40/">Schedule 40 dimensions</a> · '
            '<a href="/pipes/schedule-80/">Schedule 80 dimensions</a> · '
            '<a href="/reference/schedule-chart/">all 14 schedules</a> · '
            '<a href="/guides/pipe-schedule-explained/">what a schedule '
            "actually is</a>.</p></div>")

    q = [
        ("Is Schedule 80 pipe bigger than Schedule 40?",
         "<p>No — the outside diameter is identical. Schedule 80 has a thicker "
         "wall, so it is heavier and has a <em>smaller</em> bore. NPS 4 is "
         "4.500 in on the outside in both.</p>"),
        ("Can Schedule 40 and Schedule 80 pipe be welded together?",
         "<p>Yes, but the bore mismatch must be handled. ASME B31.3 requires "
         "the inside of the thicker component to be tapered no steeper than "
         "30° to the thinner bore when the mismatch exceeds the allowable, so "
         "the joint is counterbored and back-bevelled rather than butted "
         "square.</p>"),
        ("How much heavier is Schedule 80 than Schedule 40?",
         f"<p>Between {st['wt_lo']:.0f}% and {st['wt_hi']:.0f}% depending on "
         f"size, averaging about {st['wt_avg']:.0f}% across the "
         f"{st['n']} sizes that publish both. The penalty is largest in small "
         "bore, where the wall is a bigger fraction of the diameter.</p>"),
        ("Does Schedule 80 hold twice the pressure of Schedule 40?",
         "<p>No. Pressure capacity scales roughly with wall thickness, not with "
         "the schedule number. Schedule 80 is typically 40–50% thicker than "
         "Schedule 40, so it carries roughly 40–50% more pressure in the same "
         "material — not double.</p>"),
        ("Which schedule is standard for water piping?",
         "<p>Schedule 40 in nearly all cases. Schedule 80 appears in water "
         "service mainly where the pipe is threaded, buried under load, or "
         "exposed to impact.</p>"),
    ]

    compare("schedule-40-vs-schedule-80",
            "Schedule 40 vs Schedule 80 Pipe: Full Comparison",
            "Schedule 80 has the same outside diameter as Schedule 40 but a "
            "thicker wall, smaller bore and more weight. Full size-by-size "
            "table of wall, bore and weight.",
            "Schedule 40 vs Schedule 80 Pipe",
            "Same outside diameter, thicker wall, smaller bore. Every size that "
            "publishes both schedules, with the wall, bore, weight and flow "
            "area penalty computed for each.",
            body, faq_pairs=q,
            card_meta=f"{st['n']} sizes · +{st['wt_avg']:.0f}% weight")


def cmp_sched_10_40(pipes):
    a, b = "10", "40"
    rows = wall_pairs(pipes, a, b)
    st = sched_spread(rows)
    six = next((r for r in rows if r["s"]["nps"] == "6"), rows[len(rows) // 2])

    body = (facts([("Sizes with both", f"{st['n']}"),
                   ("Weight saved by Sch 10",
                    f"{abs(st['wt_rev']):.0f}% on average"),
                   ("Flow area gained", f"+{st['fa_rev']:.0f}% on average"),
                   ("Common use", "Sch 10 — low-pressure and stainless")])
            + verdict(
                "Schedule 10 is a thin-wall pipe: same outside diameter as "
                f"Schedule 40, about {abs(st['wt_rev']):.0f}% lighter, and a "
                "larger bore. It is the standard choice for low-pressure "
                "stainless process lines and for large-diameter water service "
                "where Schedule 40 is far heavier than the pressure requires. "
                "Schedule 40 remains the default for carbon steel utility "
                "piping and anything threaded.")
            + "<h2>Thin wall, same envelope</h2>"
            f"<p>At NPS {six['s']['nps']} the wall drops from "
            f"{inch_mm(six['tb'])} in Schedule 40 to {inch_mm(six['ta'])} in "
            f"Schedule 10 — {abs((six['ta'] / six['tb'] - 1) * 100):.0f}% less "
            f"metal. Weight falls from {n(six['wb'], 2)} lb/ft to "
            f"{n(six['wa'], 2)} lb/ft, which changes what the pipe can be hung "
            "from and how many people it takes to place a spool.</p>"
            "<p>The gain is not only weight. The bore grows from "
            f"{inch_mm(six['idb'])} to {inch_mm(six['ida'])}, so flow area rises "
            f"{(area_sqin(six['ida']) - area_sqin(six['idb'])) / area_sqin(six['idb']) * 100:.0f}% "
            "and the same duty runs at lower velocity and lower pressure "
            "drop.</p>"
            + UNITS_NOTE
            + sched_compare_table(rows, a, b)
            + "<h2>What thin wall costs you</h2>"
            "<p>Schedule 10 cannot be threaded — there is not enough wall to cut "
            "a thread into and keep a pressure boundary. It has effectively no "
            "corrosion allowance, which is why it is specified in stainless and "
            "rarely in bare carbon steel. It dents and buckles under support "
            "point loads that Schedule 40 shrugs off, so support spacing and "
            "shoe design have to be checked rather than taken from a standard "
            "table.</p>"
            + vs_columns(
                "Schedule 10 suits",
                ["Stainless process lines where the material, not the wall, "
                 "provides the corrosion resistance",
                 "Large-diameter low-pressure water, air and vent service",
                 "Roll-grooved and clamp-coupled systems",
                 "Weight-critical work: modules, racks, offshore",
                 "Service where cost of alloy is dominated by wall thickness"],
                "Schedule 40 suits",
                ["Carbon steel utility service needing a corrosion allowance",
                 "Anything threaded",
                 "Ordinary process pressures in NPS 2 and below",
                 "Buried, impacted or vibrating lines",
                 "Work where the stocked, standard item beats the optimal one"])
            + '<div class="callout"><p>Full tables: '
            '<a href="/pipes/schedule-10/">Schedule 10 dimensions</a> · '
            '<a href="/pipes/schedule-40/">Schedule 40 dimensions</a> · '
            '<a href="/compare/schedule-5s-vs-schedule-10s/">the stainless '
            "S-schedules</a> · "
            '<a href="/reference/stainless-pipe-schedules/">B36.19M '
            "stainless</a>.</p></div>")

    q = [
        ("Is Schedule 10 strong enough for compressed air?",
         "<p>Often yes at shop pressures, but it must be calculated rather than "
         "assumed. Compressed gas stores energy, so a thin-wall failure is "
         "energetic — many owners specify Schedule 40 minimum for air "
         "regardless of the calculation.</p>"),
        ("Can Schedule 10 pipe be threaded?",
         "<p>No. The wall is too thin to carry a thread and still hold "
         "pressure. Schedule 10 is joined by welding, roll-grooved couplings or "
         "flanges.</p>"),
        ("Why is Schedule 10 so common in stainless?",
         "<p>Because stainless does not need a corrosion allowance in most "
         "services, and stainless is expensive by weight. Removing wall that "
         "exists only for corrosion removes cost. B36.19M formalises this with "
         "the 10S schedule.</p>"),
        ("Is Schedule 10 the same as 10S?",
         "<p>In every size where B36.19M publishes 10S, the wall matches "
         "Schedule 10 — but B36.19M stops at NPS 30, and 10S carries the "
         "stainless material and tolerance requirements that Schedule 10 alone "
         "does not.</p>"),
    ]

    compare("schedule-10-vs-schedule-40",
            "Schedule 10 vs Schedule 40 Pipe Compared",
            "Schedule 10 has the same outside diameter as Schedule 40 with a "
            "thinner wall, bigger bore and far less weight. Size-by-size wall, "
            "bore and weight table.",
            "Schedule 10 vs Schedule 40 Pipe",
            "Thin wall against the default wall: what Schedule 10 saves in "
            "weight, what it gains in bore, and the corrosion allowance and "
            "threading it gives up.",
            body, faq_pairs=q,
            card_meta=f"{st['n']} sizes · {abs(st['wt_rev']):.0f}% lighter")


def cmp_sched_40_160(pipes):
    a, b = "40", "160"
    rows = wall_pairs(pipes, a, b)
    st = sched_spread(rows)
    two = next((r for r in rows if r["s"]["nps"] == "2"), rows[0])

    body = (facts([("Sizes with both", f"{st['n']}"),
                   ("Weight penalty", f"+{st['wt_avg']:.0f}% on average"),
                   ("Flow area lost", f"{st['fa_avg']:.0f}% on average"),
                   ("Schedule 160 starts at", "NPS 1/2")])
            + verdict(
                "Schedule 160 is the heaviest numbered schedule B36.10M "
                "publishes. Against Schedule 40 it roughly doubles the wall in "
                f"small bore — averaging +{st['wt_avg']:.0f}% weight and "
                f"{abs(st['fa_avg']):.0f}% less flow area across the "
                f"{st['n']} sizes that publish both. It is a high-pressure and "
                "severe-service wall, not a general upgrade.")
            + "<h2>Two schedules at opposite ends of the series</h2>"
            f"<p>At NPS {two['s']['nps']} the Schedule 40 wall is "
            f"{inch_mm(two['ta'])} and the Schedule 160 wall is "
            f"{inch_mm(two['tb'])} — {two['tb'] / two['ta']:.1f} times the "
            f"metal. The bore closes from {inch_mm(two['ida'])} to "
            f"{inch_mm(two['idb'])}, so a line sized on Schedule 40 flow will "
            "not deliver the same duty in Schedule 160 without moving up in "
            "NPS.</p>"
            "<p>That is the practical trap: swapping schedule for pressure "
            "capacity is also a hydraulic change. On long runs the extra "
            "pressure drop can exceed the margin the heavier wall was bought "
            "for.</p>"
            + UNITS_NOTE
            + sched_compare_table(rows, a, b)
            + "<h2>Where Schedule 160 actually appears</h2>"
            "<p>Small-bore high-pressure process lines, boiler feedwater and "
            "blowdown, steam drum connections, hydraulic and lube-oil piping, "
            "and vent and drain nipples off high-pressure headers — where the "
            "pipe is short, the pressure is high, and the wall is bought for "
            "mechanical robustness as much as for hoop stress. Above NPS 12, "
            "Schedule 160 is rare in new work: at those diameters the required "
            "wall is usually specified directly rather than by schedule.</p>"
            + '<div class="callout"><p>Full tables: '
            '<a href="/pipes/schedule-40/">Schedule 40</a> · '
            '<a href="/pipes/schedule-160/">Schedule 160</a> · '
            '<a href="/compare/xs-vs-xxs/">XS vs XXS</a> · '
            '<a href="/guides/pipe-wall-thickness-calculation/">wall thickness '
            "from pressure</a>.</p></div>")

    q = [
        ("How much stronger is Schedule 160 than Schedule 40?",
         "<p>Roughly in proportion to wall thickness — commonly 1.8 to 2.3 "
         "times in small bore. Pressure capacity is close to linear in wall "
         "thickness for thin-walled pipe, so a wall that is twice as thick "
         "carries close to twice the pressure in the same material.</p>"),
        ("Is Schedule 160 available in every size?",
         "<p>No. B36.10M publishes Schedule 160 from NPS 1/2 upward, and it is "
         "not listed for the smallest three sizes or for several of the largest "
         "ones. Check the size before specifying it.</p>"),
        ("Should I use Schedule 160 or XXS?",
         "<p>They are different walls and neither is uniformly heavier. In some "
         "sizes XXS is thicker than Schedule 160, in others the reverse. Compare "
         "the actual walls for the size rather than assuming a ranking.</p>"),
        ("Does Schedule 160 need special fittings?",
         "<p>The buttweld fittings are the same B16.9 dimensions — those depend "
         "on NPS only — but they must be ordered in the matching wall, and the "
         "end preparation for a heavy wall differs from the standard bevel.</p>"),
    ]

    compare("schedule-40-vs-schedule-160",
            "Schedule 40 vs Schedule 160 Pipe Compared",
            "Schedule 160 roughly doubles the Schedule 40 wall at the same "
            "outside diameter, halving flow area in small bore. Full wall, bore "
            "and weight comparison.",
            "Schedule 40 vs Schedule 160 Pipe",
            "The default wall against the heaviest numbered schedule — how much "
            "metal Schedule 160 adds, how much bore it takes away, and where "
            "that trade is worth making.",
            body, faq_pairs=q,
            card_meta=f"{st['n']} sizes · +{st['wt_avg']:.0f}% weight")


def cmp_std_xs(pipes):
    a, b = "STD", "XS"
    rows = wall_pairs(pipes, a, b)
    st = sched_spread(rows)
    # Where STD and XS stop tracking the numbered schedules, computed not asserted.
    std_break = [s["nps"] for s in pipes["sizes"]
                 if "STD" in s["walls"] and "40" in s["walls"]
                 and s["walls"]["STD"] != s["walls"]["40"]]
    xs_break = [s["nps"] for s in pipes["sizes"]
                if "XS" in s["walls"] and "80" in s["walls"]
                and s["walls"]["XS"] != s["walls"]["80"]]
    std_same = [s["nps"] for s in pipes["sizes"]
                if "STD" in s["walls"] and "40" in s["walls"]
                and s["walls"]["STD"] == s["walls"]["40"]]
    xs_same = [s["nps"] for s in pipes["sizes"]
               if "XS" in s["walls"] and "80" in s["walls"]
               and s["walls"]["XS"] == s["walls"]["80"]]

    body = (facts([("Sizes with both", f"{st['n']}"),
                   ("XS weight penalty", f"+{st['wt_avg']:.0f}% on average"),
                   ("STD wall above NPS 10", "0.375 in, constant"),
                   ("XS wall above NPS 8", "0.500 in, constant")])
            + verdict(
                "Standard weight and Extra Strong are the pre-schedule wall "
                "designations that survived into modern practice. STD tracks "
                f"Schedule 40 up to NPS {std_same[-1] if std_same else '10'} and "
                f"XS tracks Schedule 80 up to NPS {xs_same[-1] if xs_same else '8'}; "
                "above those sizes both freeze at a constant wall while the "
                "numbered schedules keep climbing. XS is the heavier of the "
                "two in every size.")
            + "<h2>Two series that agree, then stop agreeing</h2>"
            "<p>STD and Schedule 40 are the same wall in every size from "
            f"NPS {std_same[0] if std_same else '1/8'} through "
            f"NPS {std_same[-1] if std_same else '10'}. From "
            f"NPS {std_break[0] if std_break else '12'} upward they diverge: "
            "STD holds at 0.375 in for every larger size, while Schedule 40 "
            "keeps thickening with diameter.</p>"
            "<p>XS and Schedule 80 behave the same way one size earlier. They "
            f"match through NPS {xs_same[-1] if xs_same else '8'}, and from "
            f"NPS {xs_break[0] if xs_break else '10'} up XS holds at 0.500 in "
            "while Schedule 80 climbs. This is why a large-bore line called out "
            "as “XS” and one called out as “Schedule 80” are "
            "not the same pipe, and why the two must never be treated as "
            "interchangeable on a large-diameter isometric.</p>"
            f"<p class=\"formula\">STD = Sch 40 through NPS "
            f"{std_same[-1] if std_same else '10'} · XS = Sch 80 through NPS "
            f"{xs_same[-1] if xs_same else '8'}</p>"
            + UNITS_NOTE
            + sched_compare_table(rows, a, b)
            + "<h2>Why the old names persist</h2>"
            "<p>Because they are what the mills roll and what the warehouse "
            "stocks. In large diameter, STD and XS are the two walls most likely "
            "to be on the rack, so a designer who can live with 0.375 in gets "
            "material off the shelf instead of a mill run. The numbered "
            "schedules matter when the calculation demands a specific wall; the "
            "named ones matter when availability does.</p>"
            + '<div class="callout"><p>Full tables: '
            '<a href="/pipes/schedule-std/">STD dimensions</a> · '
            '<a href="/pipes/schedule-xs/">XS dimensions</a> · '
            '<a href="/compare/xs-vs-xxs/">XS vs XXS</a> · '
            '<a href="/compare/schedule-40-vs-schedule-80/">Sch 40 vs Sch 80</a>.'
            "</p></div>")

    q = [
        ("Is STD pipe the same as Schedule 40?",
         f"<p>Through NPS {std_same[-1] if std_same else '10'}, yes — identical "
         f"walls. From NPS {std_break[0] if std_break else '12'} upward STD "
         "stays at 0.375 in while Schedule 40 continues to thicken, so the two "
         "are different pipe in large bore.</p>"),
        ("Is XS the same as Schedule 80?",
         f"<p>Through NPS {xs_same[-1] if xs_same else '8'}, yes. From "
         f"NPS {xs_break[0] if xs_break else '10'} up, XS holds at 0.500 in "
         "and Schedule 80 keeps increasing, so they diverge one size earlier "
         "than STD and Schedule 40 do.</p>"),
        ("What does Extra Strong mean?",
         "<p>It is a wall designation, not a material or a grade. Extra Strong "
         "pipe is ordinary pipe with a heavier wall than Standard weight — the "
         "steel is whatever the material specification says it is.</p>"),
        ("Which is heavier, STD or XS?",
         f"<p>XS, in every size — on average about {st['wt_avg']:.0f}% heavier "
         "than STD across the sizes that publish both.</p>"),
    ]

    compare("std-vs-xs",
            "STD vs XS Pipe: Wall, Weight and Where They Split",
            "STD matches Schedule 40 and XS matches Schedule 80 only up to a "
            "point — above NPS 10 and NPS 8 they freeze. Full wall and weight "
            "comparison table.",
            "STD vs XS Pipe",
            "Standard weight against Extra Strong: identical to Schedules 40 "
            "and 80 in small bore, and deliberately not identical once the pipe "
            "gets large.",
            body, faq_pairs=q,
            card_meta=f"{st['n']} sizes · +{st['wt_avg']:.0f}% weight")


def cmp_xs_xxs(pipes):
    a, b = "XS", "XXS"
    rows = wall_pairs(pipes, a, b)
    st = sched_spread(rows)
    thin = min(rows, key=lambda r: area_sqin(r["idb"]) / area_sqin(r["ida"]))

    body = (facts([("Sizes with both", f"{st['n']} (NPS {st['first']} – NPS {st['last']})"),
                   ("XXS weight penalty", f"+{st['wt_avg']:.0f}% on average"),
                   ("Flow area lost", f"{st['fa_avg']:.0f}% on average"),
                   ("XXS wall", "Roughly twice XS in small bore")])
            + verdict(
                "Double Extra Strong is the heaviest wall in the traditional "
                "series — close to twice the XS wall in small bore. Across the "
                f"{st['n']} sizes that publish both, XXS adds "
                f"{st['wt_avg']:.0f}% weight and gives up "
                f"{abs(st['fa_avg']):.0f}% of the flow area. It is specified for "
                "mechanical severity — erosion, impact, vibration — at least as "
                "often as for pressure.")
            + "<h2>The bore closes fast</h2>"
            f"<p>At NPS {thin['s']['nps']} the XXS wall of "
            f"{inch_mm(thin['tb'])} leaves a bore of {inch_mm(thin['idb'])} "
            f"against the XS bore of {inch_mm(thin['ida'])} — a flow area of "
            f"{n(area_sqin(thin['idb']), 3)} in² where XS gives "
            f"{n(area_sqin(thin['ida']), 3)} in². In the smallest sizes XXS is "
            "closer to heavy bar with a hole in it than to pipe, and it is used "
            "accordingly: as sample connections, as nipples on vibrating "
            "machinery, and as sacrificial wall in slurry and catalyst "
            "service.</p>"
            + UNITS_NOTE
            + sched_compare_table(rows, a, b)
            + "<h2>XXS is not a schedule number</h2>"
            "<p>There is no Schedule 200. XXS is a named wall carried over from "
            "the wrought-iron era, and it does not line up neatly with the "
            "numbered series: in some sizes it is thicker than Schedule 160, in "
            "others thinner. Specify it by name and check the actual wall in "
            "the table rather than assuming it sits at the top of the "
            "schedule ladder.</p>"
            + '<div class="callout"><p>Full tables: '
            '<a href="/pipes/schedule-xs/">XS dimensions</a> · '
            '<a href="/pipes/schedule-xxs/">XXS dimensions</a> · '
            '<a href="/compare/schedule-40-vs-schedule-160/">Sch 40 vs '
            "Sch 160</a> · "
            '<a href="/reference/schedule-chart/">all schedules</a>.</p></div>')

    q = [
        ("Is XXS thicker than Schedule 160?",
         "<p>Not always. The two series were developed separately, so in some "
         "sizes XXS is the heavier wall and in others Schedule 160 is. Compare "
         "the published walls for the specific NPS.</p>"),
        ("What is XXS pipe used for?",
         "<p>Erosive and abrasive service, vibrating small-bore connections, "
         "high-pressure hydraulic lines, and anywhere a thick wall is wanted "
         "for mechanical robustness rather than hoop stress alone.</p>"),
        ("Is there anything heavier than XXS?",
         "<p>Not in the named series. Beyond XXS the wall is specified directly "
         "as a dimension, and the pipe is ordered to that thickness rather than "
         "to a schedule or a name.</p>"),
        ("Can XXS pipe be threaded?",
         "<p>Yes, and it is one of the reasons XXS nipples exist — there is "
         "ample wall left under the thread root, which is exactly what a "
         "thinner wall lacks.</p>"),
    ]

    compare("xs-vs-xxs",
            "XS vs XXS Pipe: Wall Thickness and Weight",
            "XXS is close to twice the XS wall in small bore, at the same "
            "outside diameter. Full comparison of wall, bore, weight and flow "
            "area for every size.",
            "XS vs XXS Pipe",
            "Extra Strong against Double Extra Strong — the two heaviest named "
            "walls, and how much bore the second one costs you.",
            body, faq_pairs=q,
            card_meta=f"{st['n']} sizes · +{st['wt_avg']:.0f}% weight")


def cmp_5s_10s(ss, pipes, cmp_):
    od_by_nps = {s["nps"]: s["od"] for s in pipes["sizes"]}
    slug_by_nps = {s["nps"]: s["slug"] for s in pipes["sizes"]}
    both = [nps for nps, w in ss["sizes"].items() if "5S" in w and "10S" in w]
    both.sort(key=nps_value)

    trs, wt_delta = [], []
    for nps in both:
        od = od_by_nps[nps]
        t5, t10 = ss["sizes"][nps]["5S"], ss["sizes"][nps]["10S"]
        i5, i10 = inside_dia(od, t5), inside_dia(od, t10)
        w5, w10 = weight_lbft(od, t5), weight_lbft(od, t10)
        wt_delta.append((w10 - w5) / w5 * 100)
        trs.append([
            f'<strong><a href="/pipes/nps-{slug_by_nps[nps]}/">NPS {esc(nps)}</a></strong>',
            dual(od, 3), dual(t5, 3), dual(t10, 3),
            dual(i5, 3), dual(i10, 3),
            dual_w(w5), dual_w(w10), pct(w10, w5),
        ])
    avg = sum(wt_delta) / len(wt_delta)
    same5 = len(cmp_.get("5S", {}).get("same", []))
    same10 = len(cmp_.get("10S", {}).get("same", []))

    body = (facts([("Sizes with both", str(len(both))),
                   ("10S weight penalty", f"+{avg:.0f}% on average"),
                   ("Standard", "ASME B36.19M"),
                   ("Largest size published", "NPS 30")])
            + verdict(
                "5S is the thinnest wall B36.19M publishes and 10S is the next "
                f"step up — about {avg:.0f}% heavier on average, at the same "
                "outside diameter. Both are welded stainless walls with no "
                "corrosion allowance. 5S suits low-pressure vents, drains and "
                "sanitary work; 10S is the general-purpose stainless process "
                "wall and the one most warehouses stock.")
            + "<h2>Same outside diameter, same standard, different wall</h2>"
            "<p>B36.19M does not publish its own outside diameters — it uses "
            "B36.10M's. The S-schedules are wall thickness series layered onto "
            "the same dimensional envelope, which is why stainless pipe takes "
            "the same B16.9 fittings and B16.5 flanges as carbon steel of the "
            "same NPS.</p>"
            f"<p>Where B36.19M and B36.10M overlap, the walls largely coincide: "
            f"5S matches its B36.10M counterpart in {same5} sizes and 10S in "
            f"{same10}. The value of the S designation is not a different "
            "number — it is the stainless material, tolerance and finish "
            "requirements that come with it. See "
            '<a href="/reference/stainless-pipe-schedules/">the B36.19M '
            "reference</a> for the size-by-size divergences.</p>"
            + UNITS_NOTE
            + table(["NPS", "OD", "5S wall", "10S wall", "5S bore", "10S bore",
                     "5S weight", "10S weight", "Weight change"], trs,
                    caption="Schedule 5S against Schedule 10S, every size "
                            "publishing both — ASME B36.19M.",
                    note="Bore and weight are derived from the B36.10M outside "
                         "diameter and the B36.19M wall.")
            + vs_columns(
                "5S is specified for",
                ["Low-pressure vents, drains and overflow",
                 "Sanitary and high-purity lines where surface finish, not "
                 "wall, governs",
                 "Large-diameter ducting and tank connections",
                 "Weight-critical stainless work",
                 "Service where the pipe is fully supported and protected"],
                "10S is specified for",
                ["General stainless process piping — the default S-schedule",
                 "Lines needing a modest pressure rating with no corrosion "
                 "allowance",
                 "Anywhere the extra wall buys handling robustness on site",
                 "Systems joined by orbital or manual TIG welding",
                 "Stock availability: 10S is far more widely warehoused"])
            + '<div class="callout"><p>See also: '
            '<a href="/pipes/schedule-5s/">Schedule 5S dimensions</a> · '
            '<a href="/pipes/schedule-10s/">Schedule 10S dimensions</a> · '
            '<a href="/compare/304-vs-316-stainless-steel-pipe/">304 vs 316</a> · '
            '<a href="/guides/pipe-material-selection/">material selection</a>.'
            "</p></div>")

    q = [
        ("What is the difference between Schedule 5S and 10S?",
         f"<p>Wall thickness only. 10S is roughly {avg:.0f}% heavier than 5S at "
         "the same outside diameter, giving a higher pressure rating and a "
         "smaller bore. Both are stainless walls published by ASME "
         "B36.19M.</p>"),
        ("Is 5S the same as Schedule 5?",
         "<p>In the sizes where both are published the walls generally match, "
         "but 5S carries B36.19M's stainless material and tolerance "
         "requirements. B36.19M also stops at NPS 30, so above that a thin "
         "stainless wall is specified by B36.10M schedule instead.</p>"),
        ("Can 5S pipe be threaded?",
         "<p>No. Neither 5S nor 10S has enough wall to thread. Stainless "
         "S-schedule pipe is welded, or joined with flanges or clamp "
         "fittings.</p>"),
        ("Which is more commonly stocked?",
         "<p>10S, by a wide margin. It is the general-purpose stainless process "
         "wall, so it is the one distributors carry across the full size "
         "range.</p>"),
    ]

    compare("schedule-5s-vs-schedule-10s",
            "Schedule 5S vs 10S Stainless Pipe Compared",
            "Schedule 10S is about 40% heavier than 5S at the same outside "
            "diameter. Full ASME B36.19M wall, bore and weight comparison for "
            "every stainless size.",
            "Schedule 5S vs Schedule 10S Pipe",
            "The two thin-wall stainless schedules of ASME B36.19M — what "
            "separates them dimensionally, and which one belongs in a process "
            "line.",
            body, faq_pairs=q,
            card_meta=f"{len(both)} sizes · ASME B36.19M")

# --------------------------------------------------------------------------
# flange class comparisons
# --------------------------------------------------------------------------

def class_pairs(b165, ca, cb):
    """Rows present in both classes, keyed by NPS and sorted by size."""
    ra = {r["nps"]: r for r in b165["classes"][ca]["rows"]}
    rb = {r["nps"]: r for r in b165["classes"][cb]["rows"]}
    shared = sorted(set(ra) & set(rb), key=nps_value)
    return [(nps, ra[nps], rb[nps]) for nps in shared]


def class_compare_table(pairs, ca, cb):
    trs = []
    for nps, a, b in pairs:
        trs.append([
            f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(a["o"], 2), dual(b["o"], 2),
            dual(a["tf"], 2), dual(b["tf"], 2),
            dual(a["bc"], 2), dual(b["bc"], 2),
            f'{a["bolts"]} × {esc(a["bolt"])}"',
            f'{b["bolts"]} × {esc(b["bolt"])}"',
        ])
    return table(
        ["NPS", f"Cl {ca} OD", f"Cl {cb} OD", f"Cl {ca} thk", f"Cl {cb} thk",
         f"Cl {ca} BC", f"Cl {cb} BC", f"Cl {ca} bolting", f"Cl {cb} bolting"],
        trs,
        caption=f"Class {ca} against Class {cb} flange dimensions, ASME B16.5.",
        note="Thickness excludes the raised face. Bolting is quantity × "
             "nominal diameter in inches.")


def rating_row(pt, cls, gslug="1-1"):
    g = next(g for g in pt["groups"] if g["slug"] == gslug)
    return g["ratings"][cls]


def cmp_flange_class(cfg, b165, pt, ftypes):
    ca, cb = cfg["a"], cfg["b"]
    pairs = class_pairs(b165, ca, cb)
    temps = pt["temperatures"]
    ra, rb = rating_row(pt, ca), rating_row(pt, cb)
    sa, sb = rating_row(pt, ca, "2-1"), rating_row(pt, cb, "2-1")

    # Computed, not asserted: how much bigger and heavier the step actually is.
    od_growth = sum(b["o"] / a["o"] for _, a, b in pairs) / len(pairs)
    tf_growth = sum(b["tf"] / a["tf"] for _, a, b in pairs) / len(pairs)
    more_bolts = sum(1 for _, a, b in pairs if b["bolts"] > a["bolts"])
    bigger_bolts = sum(1 for _, a, b in pairs
                       if nps_value(b["bolt"]) > nps_value(a["bolt"]))
    ratio = rb[0] / ra[0]

    rt_rows = [[f"<strong>{t} °F</strong>",
                f"{ra[i]} psig", f"{rb[i]} psig", pct(rb[i], ra[i]),
                f"{sa[i]} psig", f"{sb[i]} psig"]
               for i, t in enumerate(temps)]

    body = (facts([("Sizes in both classes", str(len(pairs))),
                   (f"Class {ca} at 100 °F", f"{ra[0]} psig"),
                   (f"Class {cb} at 100 °F", f"{rb[0]} psig"),
                   ("Pressure gain", f"×{ratio:.2f}")])
            + verdict(cfg["verdict"].format(
                ca=ca, cb=cb, ra=ra[0], rb=rb[0], ratio=f"{ratio:.2f}",
                od=f"{(od_growth - 1) * 100:.0f}", tf=f"{(tf_growth - 1) * 100:.0f}"))
            + "<h2>What the class number means</h2>"
            "<p>A flange class is not a pressure. It is a rating series: the "
            "pressure a flange in that class may hold depends on the material "
            "group and the metal temperature, and it is read out of ASME B16.5 "
            "Table 2. The class number happens to equal the approximate working "
            "pressure in psig for Group 1.1 carbon steel at about 500 °F, which "
            "is where the numbers came from historically — but treating "
            f"Class {ca} as “{ca} psi” is wrong at every other "
            "temperature.</p>"
            f"<p>At ambient, Group 1.1 carbon steel is rated {ra[0]} psig in "
            f"Class {ca} and {rb[0]} psig in Class {cb} — a factor of "
            f"{ratio:.2f}. That ratio holds approximately across the "
            "temperature range, because every class in a group is derived from "
            "the same allowable stress curve.</p>"
            + table(["Temperature", f"Cl {ca} · Gp 1.1", f"Cl {cb} · Gp 1.1",
                     "Gain", f"Cl {ca} · Gp 2.1", f"Cl {cb} · Gp 2.1"],
                    rt_rows,
                    caption=f"Pressure-temperature ratings, Class {ca} against "
                            f"Class {cb}, ASME B16.5.",
                    note="Group 1.1 is A105 carbon steel; Group 2.1 is Type "
                         "304 stainless. Ratings are maximum allowable working "
                         "gauge pressure at the metal temperature.")
            + "<h2>The flange gets bigger, thicker and more heavily bolted</h2>"
            f"<p>Moving from Class {ca} to Class {cb} grows the flange outside "
            f"diameter by about {(od_growth - 1) * 100:.0f}% and the thickness "
            f"by about {(tf_growth - 1) * 100:.0f}% on average across the "
            f"{len(pairs)} shared sizes. The bolt count rises in "
            f"{more_bolts} of those sizes and the bolt diameter in "
            f"{bigger_bolts}. None of that is free: the flange costs more, the "
            "gasket costs more, the bolting costs more, and the extra bolt load "
            "has to be delivered with a torque wrench that can reach it.</p>"
            + UNITS_NOTE
            + class_compare_table(pairs, ca, cb)
            + cfg.get("extra", "")
            + "<h2>Practical selection</h2>"
            + vs_columns(
                f"Class {ca} is enough when",
                cfg["a_when"],
                f"Class {cb} is worth it when",
                cfg["b_when"])
            + '<div class="callout"><p>Dimensions in full: '
            f'<a href="/flanges/weld-neck/class-{ca}/">Class {ca} weld neck</a> · '
            f'<a href="/flanges/weld-neck/class-{cb}/">Class {cb} weld neck</a> · '
            f'<a href="/flanges/blind/class-{ca}/">Class {ca} blind</a> · '
            f'<a href="/flanges/blind/class-{cb}/">Class {cb} blind</a> · '
            '<a href="/reference/pressure-temperature-ratings/">all P-T '
            "ratings</a> · "
            '<a href="/guides/pressure-temperature-derating/">how derating '
            "works</a>.</p></div>")

    compare(cfg["slug"], cfg["title"], cfg["desc"], cfg["h1"], cfg["lede"],
            body, faq_pairs=cfg["faq"],
            card_meta=f"{ra[0]} vs {rb[0]} psig at 100 °F")


def flange_class_comparisons(b165, pt, ftypes):
    cfgs = [
        {
            "a": "150", "b": "300", "slug": "class-150-vs-class-300",
            "title": "Class 150 vs Class 300 Flange: Ratings & Dims",
            "desc": "Class 300 flanges hold 740 psig at 100 °F against Class "
                    "150's 285 psig, with a thicker, larger flange and heavier "
                    "bolting. Full comparison tables.",
            "h1": "Class 150 vs Class 300 Flanges",
            "lede": "The most common step in the B16.5 ladder — what the extra "
                    "class buys in pressure, and what it costs in flange size, "
                    "bolting and money.",
            "verdict": "Class {cb} holds {rb} psig at 100 °F where Class {ca} "
                       "holds {ra} — a factor of {ratio}. The flange grows about "
                       "{od}% in outside diameter and {tf}% in thickness, and "
                       "the bolting steps up with it. Class {ca} is the default "
                       "for utility and low-pressure process; Class {cb} is the "
                       "first step for steam, hydrocarbons and anything that "
                       "derates steeply with temperature.",
            "extra": "<h2>The temperature trap in Class 150</h2>"
                     "<p>Class 150 derates far more sharply than the higher "
                     "classes. Group 1.1 carbon steel loses more than half its "
                     "Class 150 rating by 600 °F — from 285 psig to 140 psig — "
                     "while Class 300 falls from 740 to 570 psig over the same "
                     "span. A line that is comfortable in Class 150 cold can be "
                     "under-rated hot, which is why steam service starts at "
                     "Class 300 far more often than the ambient pressure "
                     "alone would suggest.</p>",
            "a_when": [
                "Water, air, and low-pressure utility service",
                "Ambient or near-ambient temperature",
                "Design pressure comfortably under 285 psig at temperature",
                "Cost and weight matter and the duty is undemanding",
                "Mating to equipment nozzles that are themselves Class 150",
            ],
            "b_when": [
                "Steam service of any pressure worth the name",
                "Hydrocarbons, or anything where a leak is a safety event",
                "Elevated temperature, where Class 150 derates below the duty",
                "Cyclic or vibrating service needing a stiffer joint",
                "Where the piping spec already calls Class 300 for consistency",
            ],
            "faq": [
                ("Can a Class 150 flange bolt to a Class 300 flange?",
                 "<p>No. The bolt circles and bolt patterns differ in every "
                 "size, so the holes do not line up. Joining the two needs a "
                 "transition spool with one flange of each class.</p>"),
                ("Is a Class 150 flange rated for 150 psi?",
                 "<p>Not exactly. A Class 150 carbon steel flange is rated 285 "
                 "psig at 100 °F and falls below 150 psig only above about "
                 "550 °F. The class number is a series label, not the working "
                 "pressure.</p>"),
                ("How much more does Class 300 cost?",
                 "<p>Typically 1.5 to 2.5 times the flange cost, plus larger "
                 "gaskets and more or bigger bolts. The installed difference is "
                 "usually larger than the material difference because the "
                 "bolting labour rises too.</p>"),
                ("Do Class 150 and Class 300 use the same gasket?",
                 "<p>No. The raised face outside diameter is common to Classes "
                 "150 through 600 in a given size, but the gasket inside "
                 "diameter, thickness and style are selected for the class and "
                 "the bolt load available.</p>"),
            ],
        },
        {
            "a": "300", "b": "600", "slug": "class-300-vs-class-600",
            "title": "Class 300 vs Class 600 Flange Comparison",
            "desc": "Class 600 doubles the Class 300 pressure rating — 1480 "
                    "psig against 740 psig at 100 °F — with a much thicker "
                    "flange. Full dimension and rating tables.",
            "h1": "Class 300 vs Class 600 Flanges",
            "lede": "A clean doubling of pressure rating, bought almost "
                    "entirely with flange thickness rather than diameter.",
            "verdict": "Class {cb} is rated {rb} psig at 100 °F against Class "
                       "{ca}'s {ra} — exactly double. Notably, the flange "
                       "outside diameter and bolt circle barely change: the "
                       "extra capacity comes from about {tf}% more thickness "
                       "and heavier bolting, not from a bigger flange.",
            "extra": "<h2>Same footprint, thicker flange</h2>"
                     "<p>Class 600 shares its outside diameter and bolt circle "
                     "with Class 400 in most sizes, and stays close to Class "
                     "300. What changes is thickness and bolt size. That makes "
                     "the step attractive when space is tight: the flange "
                     "occupies nearly the same envelope but carries twice the "
                     "pressure. It also means the raised face jumps from 1/16 "
                     "in to 1/4 in at Class 400 and above, so the gasket and "
                     "the bolt length change even where the bolt circle does "
                     "not.</p>",
            "a_when": [
                "General process service under 740 psig at temperature",
                "Refinery and chemical plant lines at moderate pressure",
                "Low and medium pressure steam",
                "The spec break sits above the operating pressure with margin",
            ],
            "b_when": [
                "High-pressure steam and boiler feedwater",
                "Hydroprocessing, gas compression and pump discharge headers",
                "Where relief valve set pressure pushes the design above Class 300",
                "Where the 1/4 in raised face and heavier bolting are wanted "
                "for joint stiffness",
            ],
            "faq": [
                ("Why is Class 600 exactly twice Class 300?",
                 "<p>Because the classes in a group are derived from the same "
                 "allowable stress by a fixed set of multipliers. The 300/600 "
                 "pair falls at a clean factor of two; other pairs do not.</p>"),
                ("Is Class 400 used much?",
                 "<p>Rarely. Most piping specs jump 300 to 600 because Class "
                 "400 shares Class 600's outside diameter and bolt circle while "
                 "offering only two-thirds the rating, so it saves little and "
                 "complicates stocking.</p>"),
                ("Does the raised face change between Class 300 and 600?",
                 "<p>Yes. Classes 150 and 300 use a 1/16 in raised face; Class "
                 "400 and above use 1/4 in. The gasket and the bolt length must "
                 "both account for it.</p>"),
                ("Can Class 600 flanges use ring type joints?",
                 "<p>Yes. RTJ is available across the classes and becomes the "
                 "common choice at Class 600 and above for high-temperature "
                 "and hydrocarbon service.</p>"),
            ],
        },
        {
            "a": "600", "b": "900", "slug": "class-600-vs-class-900",
            "title": "Class 600 vs Class 900 Flange Comparison",
            "desc": "Class 900 raises the Class 600 rating by half — 2220 psig "
                    "against 1480 psig at 100 °F — with a substantially larger "
                    "flange. Full comparison tables.",
            "h1": "Class 600 vs Class 900 Flanges",
            "lede": "The point where flanges stop being incidental hardware: "
                    "Class 900 changes the flange size, the gasket type and the "
                    "way the joint is assembled.",
            "verdict": "Class {cb} holds {rb} psig at 100 °F against Class "
                       "{ca}'s {ra} — a factor of {ratio}. Unlike the 300-to-600 "
                       "step, this one grows the flange itself: about {od}% "
                       "more outside diameter and {tf}% more thickness, with "
                       "ring type joints becoming the normal gasket choice.",
            "extra": "<h2>Where RTJ takes over</h2>"
                     "<p>Raised face flanges are permitted at Class 900, but "
                     "ring type joints dominate in practice from here upward. "
                     "An RTJ seals by plastically deforming a solid metal ring "
                     "into a machined groove, which tolerates thermal cycling "
                     "and high temperature far better than a compressed sheet "
                     "or spiral wound gasket. The trade is that the groove must "
                     "be machined true, the ring is a consumable, and the "
                     "flange faces never touch — so bolt elongation, not face "
                     "contact, is what tells you the joint is made up. See "
                     "<a href=\"/compare/raised-face-vs-ring-type-joint/\">RF "
                     "vs RTJ</a>.</p>",
            "a_when": [
                "High-pressure process within 1480 psig at temperature",
                "Most refinery high-pressure service",
                "Where raised face joints and spiral wound gaskets are preferred",
                "Where flange weight and handling are already a constraint",
            ],
            "b_when": [
                "Gas compression discharge and high-pressure separation",
                "Boiler and high-energy steam service above Class 600",
                "Wellhead-adjacent and upstream production piping",
                "Where thermal cycling favours a metal ring seal",
            ],
            "faq": [
                ("Is Class 900 the same as API 5000?",
                 "<p>No, though they are often adjacent in service. API 6A "
                 "designations are a separate rating system for wellhead "
                 "equipment; B16.5 Class 900 is a piping flange rating. They "
                 "must not be mixed without a documented transition.</p>"),
                ("Do Class 900 flanges have to be RTJ?",
                 "<p>No. B16.5 publishes raised face Class 900 flanges. RTJ is "
                 "the common specification choice rather than a code "
                 "requirement.</p>"),
                ("Why does Class 900 share dimensions with Class 1500 in small "
                 "sizes?",
                 "<p>Through NPS 2 1/2, B16.5 makes Class 900 identical to "
                 "Class 1500 — there was no practical reason to publish a "
                 "separate lighter flange at those diameters, so the standard "
                 "does not.</p>"),
                ("How much heavier is a Class 900 flange?",
                 "<p>Substantially — commonly 1.6 to 2.2 times a Class 600 "
                 "flange in the same size, which is enough to change lifting "
                 "and support design.</p>"),
            ],
        },
        {
            "a": "900", "b": "1500", "slug": "class-900-vs-class-1500",
            "title": "Class 900 vs Class 1500 Flange Comparison",
            "desc": "Class 1500 holds 3705 psig at 100 °F against Class 900's "
                    "2220 psig, and the two share dimensions below NPS 3. Full "
                    "comparison tables.",
            "h1": "Class 900 vs Class 1500 Flanges",
            "lede": "Two high-pressure classes that are dimensionally identical "
                    "in small bore and diverge sharply above it.",
            "verdict": "Class {cb} is rated {rb} psig at 100 °F against Class "
                       "{ca}'s {ra} — a factor of {ratio}. Through NPS 2 1/2 the "
                       "two classes share the same flange, so the choice only "
                       "becomes real at NPS 3 and above, where Class 1500 adds "
                       "about {od}% in diameter and {tf}% in thickness.",
            "extra": "<h2>Identical below NPS 3</h2>"
                     "<p>ASME B16.5 publishes the same outside diameter, "
                     "thickness, bolt circle and bolting for Class 900 and "
                     "Class 1500 in NPS 1/2 through NPS 2 1/2. In those sizes a "
                     "Class 900 flange <em>is</em> a Class 1500 flange, and "
                     "specifying the lower class buys nothing. Piping specs "
                     "often go straight to Class 1500 in small bore for that "
                     "reason — one fewer item to stock, at no extra "
                     "material.</p>",
            "a_when": [
                "Pressures within 2220 psig at temperature, NPS 3 and above",
                "Where flange weight and bolt tensioning access are limiting",
                "Existing plant already standardised on Class 900",
            ],
            "b_when": [
                "Small bore, where Class 1500 costs the same as Class 900",
                "High-pressure gas, injection and hydroprocessing service",
                "Where relief or upset conditions exceed the Class 900 rating",
                "Where a single high-pressure class simplifies the spec",
            ],
            "faq": [
                ("Are Class 900 and Class 1500 flanges interchangeable?",
                 "<p>In NPS 1/2 through NPS 2 1/2 they are the same flange, so "
                 "they bolt together. From NPS 3 upward the dimensions differ "
                 "and they do not.</p>"),
                ("Which classes require ring type joints?",
                 "<p>None strictly, but RTJ is the practical default at Class "
                 "900 and above, and is near-universal at Class 1500 in "
                 "hydrocarbon service.</p>"),
                ("What bolting is used at Class 1500?",
                 "<p>ASTM A193 B7 studs with A194 2H nuts in ordinary service, "
                 "moving to B16 or B8M for high temperature or corrosion. "
                 "Bolt tensioning rather than torquing is common above NPS 8.</p>"),
                ("Is Class 1500 the highest B16.5 class?",
                 "<p>No — Class 2500 is, and it is published through NPS 12 "
                 "only. Above NPS 12 the highest B16.5 class available is "
                 "Class 1500.</p>"),
            ],
        },
        {
            "a": "1500", "b": "2500", "slug": "class-1500-vs-class-2500",
            "title": "Class 1500 vs Class 2500 Flange Comparison",
            "desc": "Class 2500 is the highest ASME B16.5 class at 6170 psig "
                    "and 100 °F, published only through NPS 12. Full dimension "
                    "and rating comparison.",
            "h1": "Class 1500 vs Class 2500 Flanges",
            "lede": "The top two classes in ASME B16.5 — and the size limit "
                    "that decides between them more often than pressure does.",
            "verdict": "Class {cb} holds {rb} psig at 100 °F against Class "
                       "{ca}'s {ra}, a factor of {ratio}. The catch is size: "
                       "B16.5 publishes Class 2500 only through NPS 12, so above "
                       "that diameter Class 1500 is the highest class available "
                       "and higher pressures need a different standard.",
            "extra": "<h2>Class 2500 stops at NPS 12</h2>"
                     "<p>This is the constraint that usually settles the "
                     "choice. B16.5 tabulates Class 2500 for NPS 1/2 through "
                     "NPS 12 and no further. A large-bore line above Class 1500 "
                     "leaves B16.5 entirely — it goes to API 6A, to a "
                     "manufacturer's proprietary connector, or to a "
                     "design-by-analysis flange under ASME Section VIII "
                     "Appendix 2. The flanges are also genuinely heavy: a Class "
                     "2500 NPS 12 flange is a crane lift, not a two-person "
                     "carry.</p>",
            "a_when": [
                "Any size above NPS 12 — Class 2500 does not exist there",
                "Pressures within 3705 psig at temperature",
                "Where flange weight, handling and bolt tensioning access "
                "are limiting",
            ],
            "b_when": [
                "Small and medium bore above the Class 1500 rating",
                "High-pressure injection, test and hydraulic headers",
                "Where the alternative is leaving B16.5 for a proprietary "
                "connector",
            ],
            "faq": [
                ("What is the highest ASME B16.5 flange class?",
                 "<p>Class 2500, rated 6170 psig at 100 °F in Group 1.1 carbon "
                 "steel. It is published from NPS 1/2 through NPS 12 only.</p>"),
                ("What comes above Class 2500?",
                 "<p>Nothing within B16.5. Higher pressures use API 6A flanges, "
                 "proprietary clamp connectors, or a flange designed under "
                 "ASME Section VIII Division 1 Appendix 2.</p>"),
                ("Are Class 2500 flanges always RTJ?",
                 "<p>Almost always in practice. Raised face Class 2500 flanges "
                 "are published, but the bolt loads involved make a metal ring "
                 "seal the sensible choice.</p>"),
                ("Can I use a Class 2500 flange at low pressure?",
                 "<p>You can, and it is occasionally done for erosion or "
                 "mechanical reasons, but it is expensive, heavy and usually "
                 "signals that something else in the design should change.</p>"),
            ],
        },
    ]
    for cfg in cfgs:
        cmp_flange_class(cfg, b165, pt, ftypes)

# --------------------------------------------------------------------------
# flange type comparisons
# --------------------------------------------------------------------------

def hub_rows(b165, classes=("150", "300")):
    """Length through hub for weld neck and slip-on, per size, per class.

    B16.5 tabulates Y for Classes 150 and 300 only, so the table stops there
    rather than rendering four columns of em dashes.
    """
    by_nps = {}
    for c in classes:
        for r in b165["classes"][c]["rows"]:
            by_nps.setdefault(r["nps"], {})[c] = r
    out = []
    for nps in sorted(by_nps, key=nps_value):
        blk = by_nps[nps]
        if all(c in blk and blk[c].get("y_wn") and blk[c].get("y_so")
               for c in classes):
            out.append((nps, blk))
    return out


def shared_dims_note(a_name, b_name):
    return (f"<p>ASME B16.5 tabulates one set of outside diameters, "
            f"thicknesses, bolt circles and bolt patterns per class per size, "
            f"and every flange type in that class uses them. A {a_name} and a "
            f"{b_name} of the same class and size have identical bolting and "
            f"are fully interchangeable at the joint — the difference is "
            f"entirely in how each attaches to the pipe.</p>")


def ftypes_by_slug(ftypes):
    return {t["slug"]: t for t in ftypes}


def cmp_wn_so(b165, ftypes):
    ft = ftypes_by_slug(ftypes)
    wn, so = ft["weld-neck"], ft["slip-on"]
    rows = hub_rows(b165)
    trs = []
    for nps, blk in rows:
        a, b = blk["150"], blk["300"]
        trs.append([
            f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(a["y_wn"], 2), dual(a["y_so"], 2),
            f'{a["y_wn"] - a["y_so"]:+.2f} in',
            dual(b["y_wn"], 2), dual(b["y_so"], 2),
            f'{b["y_wn"] - b["y_so"]:+.2f} in',
        ])
    extra150 = sum(blk["150"]["y_wn"] - blk["150"]["y_so"] for _, blk in rows) / len(rows)

    body = (facts([("Weld type", "Butt weld vs two fillet welds"),
                   ("Bore", "Matches schedule vs matches pipe OD"),
                   ("Extra length, Class 150", f"{extra150:.2f} in average"),
                   ("Fatigue life", "Weld neck roughly 3× slip-on")])
            + verdict(
                "A weld neck is butt-welded to the pipe through a tapered hub, "
                "so its bore matches the pipe bore and the joint is a single "
                "radiographable full-penetration weld. A slip-on slides over "
                "the pipe OD and takes two fillet welds. The weld neck is "
                "stronger in fatigue by roughly a factor of three and is the "
                "correct choice for pressure, temperature and cyclic service; "
                "the slip-on is cheaper, shorter and easier to fit, and belongs "
                "in low-pressure utility work.")
            + shared_dims_note("weld neck", "slip-on")
            + "<h2>The bore is the real difference</h2>"
            "<p>A weld neck flange is bored to a specific schedule. Order a "
            "weld neck for NPS 6 Schedule 40 and its bore is the Schedule 40 "
            "bore, so the inside of the joint is smooth and continuous — no "
            "step, no crevice, no turbulence. That is why weld necks are "
            "mandatory in most specs for erosive, cyclic and hydrogen "
            "service.</p>"
            "<p>A slip-on is bored to clear the pipe <em>outside</em> diameter, "
            "so one slip-on fits every schedule in that NPS. Convenient for "
            "stocking, but the pipe end stops short of the flange face and "
            "leaves an internal step, and the inner fillet weld sits right in "
            "the flow.</p>"
            + '<div class="formula">Weld neck: bore = pipe bore (schedule '
            "specific) · Slip-on: bore = pipe OD + clearance</div>"
            + "<h2>Length through hub</h2>"
            "<p>The weld neck hub adds length. Across the sizes B16.5 "
            f"tabulates, a Class 150 weld neck runs about {extra150:.2f} in "
            "longer through the hub than a slip-on in the same size — enough to "
            "matter in a congested rack, and enough to change a spool "
            "dimension if the type is substituted late.</p>"
            + UNITS_NOTE
            + table(["NPS", "Cl 150 WN hub", "Cl 150 SO hub", "Difference",
                     "Cl 300 WN hub", "Cl 300 SO hub", "Difference"], trs,
                    caption="Length through hub, weld neck against slip-on, "
                            "ASME B16.5 Classes 150 and 300.",
                    note="B16.5 tabulates length through hub for Classes 150 "
                         "and 300 only; above Class 300 the hub follows the "
                         "bore and is given per bore in the standard.")
            + vs_columns("Weld neck", wn["pros"] + [f"<em>{esc(wn['use'])}</em>"],
                         "Slip-on", so["pros"] + [f"<em>{esc(so['use'])}</em>"])
            + "<h2>Cost, honestly</h2>"
            "<p>The flange itself is the smaller part of the difference. A weld "
            "neck costs more to buy, but the joint needs one weld instead of "
            "two, and that weld can be radiographed rather than requiring "
            "magnetic particle inspection on two fillets. On a line with NDE "
            "requirements the installed cost gap narrows considerably, and on a "
            "line without them the slip-on usually wins.</p>"
            + '<div class="callout"><p>Dimensions: '
            '<a href="/flanges/weld-neck/">weld neck flanges</a> · '
            '<a href="/flanges/slip-on/">slip-on flanges</a> · '
            '<a href="/compare/lap-joint-vs-slip-on-flange/">lap joint vs '
            "slip-on</a> · "
            '<a href="/guides/flange-bolt-torque/">bolt torque</a>.</p></div>')

    q = [
        ("Which is stronger, a weld neck or a slip-on flange?",
         "<p>The weld neck, decisively in fatigue — roughly three times the "
         "cycle life. In static pressure the difference is smaller, but the "
         "weld neck still carries load better because the tapered hub moves "
         "the stress concentration away from the flange face.</p>"),
        ("Can I use a slip-on flange on high-pressure service?",
         "<p>B16.5 rates slip-on flanges to the same class as weld necks, so "
         "the flange is not the limit. Most piping specs still restrict slip-ons "
         "to Class 300 and below, and exclude them from cyclic, hydrogen and "
         "lethal service, because the two fillet welds are harder to inspect.</p>"),
        ("Does a weld neck flange need a schedule specified?",
         "<p>Yes. The bore is machined to a schedule, so a weld neck ordered "
         "without one cannot be made. A slip-on needs only the NPS.</p>"),
        ("Are the bolt holes the same on both types?",
         "<p>Identical. Bolt circle, bolt count and bolt diameter are a "
         "function of class and size, not of flange type, so the two bolt "
         "together without issue.</p>"),
        ("Which is shorter?",
         f"<p>The slip-on, by about {extra150:.2f} in through the hub in Class "
         "150. That is often the deciding factor where space is tight.</p>"),
    ]

    compare("weld-neck-vs-slip-on-flange",
            "Weld Neck vs Slip-On Flange: Which to Use",
            "Weld neck flanges butt-weld to the pipe with a matching bore; "
            "slip-ons take two fillet welds and fit any schedule. Full hub "
            "length and selection comparison.",
            "Weld Neck vs Slip-On Flange",
            "The two most-specified B16.5 flange types: identical bolting, "
            "completely different joints. What each costs, where each is "
            "allowed, and how much length the hub adds.",
            body, faq_pairs=q, card_meta="Hub length · fatigue · cost")


def cmp_wn_blind(b165, ftypes):
    ft = ftypes_by_slug(ftypes)
    wn, bl = ft["weld-neck"], ft["blind"]
    rf = b165["raised_face"]
    r150 = class_rating("150") or 285
    r300 = class_rating("300") or 740
    rows = []
    for r in b165["classes"]["150"]["rows"]:
        nps = r["nps"]
        if nps not in rf:
            continue
        d = rf[nps]
        area = area_sqin(d)
        r3 = next((x for x in b165["classes"]["300"]["rows"]
                   if x["nps"] == nps), None)
        rows.append([
            f'<strong><a href="/flanges/blind/class-150/#nps-{nps_slug(nps)}">NPS {esc(nps)}</a></strong>',
            dual(d, 2), f"{area:.1f} in²",
            dual(r["tf"], 2), dual(r3["tf"], 2) if r3 else '<span class="na">—</span>',
            f"{area * r150 / 1000:,.1f} kip",
            f"{area * r300 / 1000:,.1f} kip" if r3 else '<span class="na">—</span>',
        ])

    body = (facts([("Weld neck", "Continues the line"),
                   ("Blind", "Closes the line"),
                   ("Shared bolting", "Yes — identical per class and size"),
                   ("Highest bending stress", "Blind, of all six types")])
            + verdict(
                "These two flanges do opposite jobs. A weld neck carries the "
                "line onward through a butt weld to the next pipe; a blind is a "
                "solid disc that closes the opening. They share outside "
                "diameter, thickness, bolt circle and bolting within a class, "
                "so a blind bolts straight onto a weld neck — which is exactly "
                "how a line is blanked for a hydrotest or a tie-in.")
            + shared_dims_note("weld neck", "blind")
            + "<h2>A blind carries the full end load</h2>"
            "<p>Nothing supports the middle of a blind flange. The full "
            "internal pressure acts across the whole gasket-diameter disc, and "
            "the flange has to take that load in bending. It is the highest "
            "stressed of the six B16.5 types at a given class, which is why "
            "blinds are thick and, in large sizes, genuinely heavy.</p>"
            + '<p class="formula">End load F = P × π/4 × d² '
            "&nbsp;·&nbsp; d = raised face outside diameter</p>"
            "<p>The table below works that out for every size at the ambient "
            "rating of each class. A NPS 24 Class 300 blind holds back more "
            "than four hundred thousand pounds of force — the number is worth "
            "seeing before anyone treats a blind as a convenient temporary "
            "cover.</p>"
            + UNITS_NOTE
            + table(["NPS", "Raised face OD", "Disc area", "Cl 150 thickness",
                     "Cl 300 thickness", "Cl 150 end load", "Cl 300 end load"],
                    rows,
                    caption="Blind flange thickness and hydrostatic end load "
                            "at the ambient class rating, ASME B16.5.",
                    note=f"End load computed at {r150} psig for Class 150 and "
                         f"{r300} psig for Class 300 — the Group 1.1 ratings at "
                         "100 °F. 1 kip = 1000 lbf.")
            + vs_columns("Weld neck", wn["pros"] + [f"<em>{esc(wn['use'])}</em>"],
                         "Blind", bl["pros"] + [f"<em>{esc(bl['use'])}</em>"])
            + "<h2>Blanking a line safely</h2>"
            "<p>A blind flange bolted to a live joint is a pressure boundary "
            "like any other and must be rated for the service. A <em>spade</em> "
            "or <em>spectacle blind</em> is a different component — a plate "
            "inserted between two existing flanges, sized by calculation for "
            "the pressure and the span, and not covered by B16.5 at all. The "
            "two are not interchangeable.</p>"
            + '<div class="callout"><p>Dimensions: '
            '<a href="/flanges/weld-neck/">weld neck flanges</a> · '
            '<a href="/flanges/blind/">blind flanges</a> · '
            '<a href="/guides/hydrostatic-test-pressure/">hydrostatic test '
            "pressure</a> · "
            '<a href="/guides/flange-bolt-torque/">bolt torque</a>.</p></div>')

    q = [
        ("Can a blind flange bolt onto a weld neck flange?",
         "<p>Yes. Within a class and size the two share bolt circle, bolt count "
         "and bolt diameter, so they mate directly with the appropriate "
         "gasket.</p>"),
        ("Why are blind flanges thicker than other flanges?",
         "<p>Because the disc is unsupported in the middle. B16.5 gives blinds "
         "the same tabulated thickness as other types in most sizes, but the "
         "stress state is bending across the full disc rather than a ring load, "
         "which is what makes it the most highly stressed type.</p>"),
        ("What is the difference between a blind flange and a spade?",
         "<p>A blind flange bolts onto an open flanged connection and is a "
         "B16.5 component. A spade is a plate inserted between two mated "
         "flanges, is not covered by B16.5, and must be sized by "
         "calculation.</p>"),
        ("Can a blind flange be drilled later for a branch?",
         "<p>Yes — that is one of the reasons blinds are left on future tie-in "
         "points. The drilled and tapped result is no longer a standard B16.5 "
         "item and needs to be assessed for the service.</p>"),
    ]

    compare("weld-neck-vs-blind-flange",
            "Weld Neck vs Blind Flange: Uses and Loads",
            "A weld neck continues the line, a blind closes it — with identical "
            "bolting per class. Full thickness and hydrostatic end load "
            "comparison for every size.",
            "Weld Neck vs Blind Flange",
            "One flange carries the line onward, the other stops it. Identical "
            "bolting, opposite jobs, and a very different stress state in the "
            "disc.",
            body, faq_pairs=q, card_meta="End load · thickness · blanking")


def cmp_so_thd(b165, ftypes):
    ft = ftypes_by_slug(ftypes)
    so, thd = ft["slip-on"], ft["threaded"]
    rows = hub_rows(b165)
    trs = [[f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(blk["150"]["o"], 2), dual(blk["150"]["y_so"], 2),
            f'{blk["150"]["bolts"]} × {esc(blk["150"]["bolt"])}"',
            dual(blk["300"]["o"], 2), dual(blk["300"]["y_so"], 2),
            f'{blk["300"]["bolts"]} × {esc(blk["300"]["bolt"])}"']
           for nps, blk in rows]

    body = (facts([("Dimensions", "Identical — same B16.5 table"),
                   ("Joint", "Two fillet welds vs NPT thread"),
                   ("Hot work needed", "Slip-on yes, threaded no"),
                   ("Thread standard", "ASME B1.20.1 NPT")])
            + verdict(
                "Dimensionally these are the same flange. ASME B16.5 gives "
                "slip-on and threaded flanges one shared set of dimensions, "
                "including the same length through hub. The only difference is "
                "the bore: one is smooth and gets fillet-welded to the pipe, "
                "the other is tapped NPT and screws on. Choose threaded when "
                "welding is impossible or forbidden; choose slip-on "
                "everywhere else.")
            + shared_dims_note("slip-on", "threaded")
            + "<h2>Same table, different bore</h2>"
            "<p>B16.5 tabulates length through hub Y once for slip-on, threaded "
            "and socket weld flanges together. That is not an approximation in "
            "this site's data — it is how the standard publishes it. So the "
            "table below is simultaneously the slip-on table and the threaded "
            "table.</p>"
            + UNITS_NOTE
            + table(["NPS", "Cl 150 OD", "Cl 150 hub", "Cl 150 bolting",
                     "Cl 300 OD", "Cl 300 hub", "Cl 300 bolting"], trs,
                    caption="Shared slip-on and threaded flange dimensions, "
                            "ASME B16.5 Classes 150 and 300.",
                    note="Slip-on and threaded flanges share one length "
                         "through hub, measured from the flange face without "
                         "the raised face.")
            + "<h2>Where threaded flanges earn their place</h2>"
            "<p>No hot work. In an operating plant, a live tank farm, or a "
            "classified area, getting a welding permit can take longer than the "
            "job — and sometimes it will not be granted at all. A threaded "
            "flange turns a welding job into a wrench job. That is the whole "
            "argument, and it is often decisive.</p>"
            "<p>The cost is durability. An NPT thread seals on interference "
            "between tapered flanks, and it loosens under vibration and thermal "
            "cycling. The thread root also cuts into the pipe wall, so threaded "
            "connections need a heavier schedule — Schedule 80 as a practical "
            "minimum in most specs, and many codes prohibit threading thin-wall "
            "pipe outright.</p>"
            + vs_columns("Slip-on", so["pros"] + so["cons"],
                         "Threaded", thd["pros"] + thd["cons"])
            + "<h2>What the codes say</h2>"
            "<p>ASME B31.3 restricts threaded joints in several ways: they are "
            "not permitted where severe cyclic conditions apply, they are "
            "limited in size and pressure in many fluid services, and threaded "
            "joints in Category M fluid service require seal welding. Check the "
            "governing code before assuming a threaded flange is acceptable.</p>"
            + '<div class="callout"><p>Dimensions: '
            '<a href="/flanges/slip-on/">slip-on flanges</a> · '
            '<a href="/flanges/threaded/">threaded flanges</a> · '
            '<a href="/compare/socket-weld-vs-threaded-flange/">socket weld vs '
            "threaded</a> · "
            '<a href="/guides/pipe-end-connections/">end connections</a>.'
            "</p></div>")

    q = [
        ("Are slip-on and threaded flanges the same size?",
         "<p>Yes. ASME B16.5 publishes one set of dimensions covering slip-on, "
         "threaded and socket weld flanges, including the same length through "
         "hub. Only the bore differs.</p>"),
        ("When should I use a threaded flange instead of a slip-on?",
         "<p>When welding is not possible: hazardous areas without a hot work "
         "permit, live plant, galvanised pipe that must not be burned, or "
         "remote sites without qualified welders.</p>"),
        ("What pipe schedule does a threaded flange need?",
         "<p>Heavy enough that the thread root leaves adequate wall. Schedule "
         "80 is the practical minimum in most specifications, and thin-wall "
         "pipe such as Schedule 10 cannot be threaded at all.</p>"),
        ("Can a threaded flange be seal welded?",
         "<p>Yes, and some codes require it — ASME B31.3 requires seal welding "
         "of threaded joints in Category M fluid service. Seal welding makes "
         "the joint non-removable and defeats the reason for choosing "
         "threaded in the first place.</p>"),
    ]

    compare("slip-on-vs-threaded-flange",
            "Slip-On vs Threaded Flange: Same Dims, New Joint",
            "ASME B16.5 gives slip-on and threaded flanges identical "
            "dimensions — only the bore differs. Full shared dimension table "
            "and selection guidance.",
            "Slip-On vs Threaded Flange",
            "Dimensionally the same flange in B16.5. The choice is entirely "
            "about the joint: two fillet welds, or an NPT thread and no hot "
            "work at all.",
            body, faq_pairs=q, card_meta="Identical dims · welded vs threaded")


def cmp_sw_thd(b165, ftypes):
    ft = ftypes_by_slug(ftypes)
    sw, thd = ft["socket-weld"], ft["threaded"]
    rows = [(nps, blk) for nps, blk in hub_rows(b165) if nps_value(nps) <= 3]
    trs = [[f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(blk["150"]["o"], 2), dual(blk["150"]["y_so"], 2),
            f'{blk["150"]["bolts"]} × {esc(blk["150"]["bolt"])}"',
            dual(blk["300"]["o"], 2), dual(blk["300"]["y_so"], 2),
            f'{blk["300"]["bolts"]} × {esc(blk["300"]["bolt"])}"']
           for nps, blk in rows]

    body = (facts([("Both published", "NPS 1/2 – NPS 3 (small bore)"),
                   ("Joint", "Fillet weld vs NPT thread"),
                   ("Expansion gap", "1/16 in, socket weld only"),
                   ("Dimensions", "Identical — shared B16.5 table")])
            + verdict(
                "Both are small-bore flanges sharing one set of B16.5 "
                "dimensions. A socket weld flange takes the pipe into a "
                "counterbored socket and is fillet-welded outside; a threaded "
                "flange screws on with no welding. Socket weld wins on "
                "strength, vibration resistance and leak-tightness; threaded "
                "wins where hot work is impossible. In new construction, socket "
                "weld is the default and threaded is the exception.")
            + shared_dims_note("socket weld", "threaded")
            + "<h2>The 1/16 inch gap</h2>"
            "<p>A socket weld joint is assembled by inserting the pipe to the "
            "bottom of the socket and then <em>backing it out about 1/16 in</em> "
            "before welding. Without that gap, the pipe bottoms out and thermal "
            "expansion of the weld drives a crack into the fillet root. It is "
            "the single most common socket weld defect, and it is invisible "
            "once the joint is welded.</p>"
            "<p>The gap is also a crevice — a dead volume where the fluid does "
            "not move. That rules socket welds out of sanitary service, and "
            "makes them a poor choice in chloride-bearing or otherwise "
            "crevice-corrosion-prone systems.</p>"
            + UNITS_NOTE
            + table(["NPS", "Cl 150 OD", "Cl 150 hub", "Cl 150 bolting",
                     "Cl 300 OD", "Cl 300 hub", "Cl 300 bolting"], trs,
                    caption="Shared socket weld and threaded flange "
                            "dimensions in small bore, ASME B16.5.",
                    note="Socket weld flanges are published in the small sizes "
                         "shown; the same table serves threaded flanges.")
            + vs_columns("Socket weld", sw["pros"] + sw["cons"],
                         "Threaded", thd["pros"] + thd["cons"])
            + "<h2>Vibration decides most of these</h2>"
            "<p>Small-bore connections off pumps, compressors and steam headers "
            "fail by fatigue at the joint more often than by anything else. A "
            "welded socket has no mechanism to loosen; an NPT thread does. "
            "Where a small-bore line is cantilevered off vibrating equipment, "
            "socket weld — properly gapped and, ideally, braced — is the "
            "answer, and a threaded connection is a future leak.</p>"
            + '<div class="callout"><p>Dimensions: '
            '<a href="/flanges/socket-weld/">socket weld flanges</a> · '
            '<a href="/flanges/threaded/">threaded flanges</a> · '
            '<a href="/compare/slip-on-vs-threaded-flange/">slip-on vs '
            "threaded</a> · "
            '<a href="/guides/pipe-end-connections/">end connections</a>.'
            "</p></div>")

    q = [
        ("Why do socket weld joints need a gap?",
         "<p>To let the weld and the pipe expand without loading the fillet "
         "root. Pipe bottomed out in the socket puts the shrinkage strain "
         "straight into the weld and cracks it. About 1/16 in of clearance is "
         "specified.</p>"),
        ("Are socket weld and threaded flanges the same dimensions?",
         "<p>Yes. B16.5 publishes one set of dimensions for slip-on, threaded "
         "and socket weld flanges, including the same length through hub.</p>"),
        ("What sizes are socket weld flanges available in?",
         "<p>Small bore — commonly NPS 1/2 through NPS 3. Above that the "
         "socket becomes impractical and slip-on or weld neck is used.</p>"),
        ("Which is better for steam tracing and instrument lines?",
         "<p>Socket weld, in almost every case. Those lines are small, hot and "
         "often vibrating, which is exactly where threads loosen.</p>"),
    ]

    compare("socket-weld-vs-threaded-flange",
            "Socket Weld vs Threaded Flange Compared",
            "Socket weld and threaded flanges share B16.5 dimensions but not "
            "the joint — one is fillet welded with a 1/16 in gap, the other is "
            "NPT. Full comparison.",
            "Socket Weld vs Threaded Flange",
            "The two small-bore options in ASME B16.5: same dimensions, and a "
            "joint that either resists vibration or slowly gives in to it.",
            body, faq_pairs=q, card_meta="Small bore · vibration · hot work")


def cmp_lj_so(b165, ftypes):
    ft = ftypes_by_slug(ftypes)
    lj, so = ft["lap-joint"], ft["slip-on"]
    rows = hub_rows(b165)
    trs = [[f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(blk["150"]["o"], 2), dual(blk["150"]["bc"], 2),
            f'{blk["150"]["bolts"]} × {esc(blk["150"]["bolt"])}"',
            dual(blk["150"]["y_so"], 2), dual(blk["300"]["y_so"], 2)]
           for nps, blk in rows]

    body = (facts([("Dimensions", "Same OD and bolting as slip-on"),
                   ("Wetted?", "Lap joint never touches the fluid"),
                   ("Extra component", "Lap joint needs a stub end"),
                   ("Bolt hole alignment", "Lap joint rotates freely")])
            + verdict(
                "A lap joint flange is a loose backing ring that never contacts "
                "the process — a separate stub end provides the bore and the "
                "gasket face. A slip-on is welded directly to the pipe and is "
                "fully wetted. The lap joint costs an extra component but lets "
                "you put a cheap carbon steel flange behind an expensive alloy "
                "stub end, and it spins freely so bolt holes always line up.")
            + shared_dims_note("lap joint", "slip-on")
            + "<h2>Two components instead of one</h2>"
            "<p>The lap joint arrangement splits the flange's two jobs. The "
            "<strong>stub end</strong> — a short pipe section with a flared lap "
            "— is butt-welded to the line, provides the bore, and forms the "
            "gasket surface. The <strong>backing flange</strong> slides on "
            "behind the lap and does nothing but deliver bolt load. Stub end "
            "dimensions come from ASME B16.9, not B16.5.</p>"
            "<p>Because the backing flange is never wetted, it can be a "
            "different material from the line. On a stainless or duplex system "
            "that turns a large, expensive alloy flange into a small alloy stub "
            "end plus an ordinary carbon steel ring — a genuine saving that "
            "grows with size and alloy cost.</p>"
            + UNITS_NOTE
            + table(["NPS", "Flange OD", "Bolt circle", "Bolting",
                     "Cl 150 slip-on hub", "Cl 300 slip-on hub"], trs,
                    caption="Lap joint and slip-on flange dimensions, ASME "
                            "B16.5.",
                    note="Outside diameter, bolt circle and bolting are "
                         "common to both types. The hub length shown is the "
                         "slip-on figure. B16.5 tabulates the lap joint hub "
                         "separately, and from NPS 14 upward it is longer "
                         "than the slip-on hub; those figures are not yet on "
                         "this site. The mating stub end is dimensioned by "
                         "ASME B16.9.")
            + "<h2>The rotation advantage</h2>"
            "<p>A welded flange's bolt holes are fixed the moment the weld "
            "cools. Get the orientation wrong on a long spool with a flange at "
            "each end and the correction is a cut and a reweld. A lap joint "
            "flange spins on the stub end until the moment the bolts go in, "
            "which is why they show up on large fabricated spools, on tank "
            "nozzles, and on anything that has to mate with equipment whose "
            "bolt pattern is not certain until it arrives.</p>"
            + vs_columns("Lap joint", lj["pros"] + lj["cons"],
                         "Slip-on", so["pros"] + so["cons"])
            + "<h2>Where not to use one</h2>"
            "<p>Lap joints are the weakest of the six types in fatigue. The lap "
            "is a stress riser and the backing flange bears on it "
            "unevenly. Keep them out of cyclic service, out of high-temperature "
            "work, and off lines subject to significant external moment.</p>"
            + '<div class="callout"><p>Dimensions: '
            '<a href="/flanges/lap-joint/">lap joint flanges</a> · '
            '<a href="/flanges/slip-on/">slip-on flanges</a> · '
            '<a href="/fittings/">B16.9 fittings and stub ends</a> · '
            '<a href="/compare/weld-neck-vs-slip-on-flange/">weld neck vs '
            "slip-on</a>.</p></div>")

    q = [
        ("What is a stub end?",
         "<p>A short butt-welding component with a flared lap at one end, "
         "dimensioned by ASME B16.9. It provides the bore and the gasket "
         "surface for a lap joint flange, which supplies only bolt load.</p>"),
        ("Do lap joint flanges use the same dimensions as slip-on flanges?",
         "<p>Yes. ASME B16.5 publishes one set of dimensions covering both, so "
         "the outside diameter, bolt circle and bolting are identical.</p>"),
        ("Why use a lap joint flange on stainless piping?",
         "<p>Because the backing flange is never wetted, so it can be carbon "
         "steel. On large alloy lines that removes most of the flange's alloy "
         "content and a large part of its cost.</p>"),
        ("Are lap joint flanges suitable for high pressure?",
         "<p>They carry the same class rating, but they have the poorest "
         "fatigue performance of the six types, so most specs exclude them from "
         "cyclic and high-temperature service regardless of class.</p>"),
    ]

    compare("lap-joint-vs-slip-on-flange",
            "Lap Joint vs Slip-On Flange: When Each Wins",
            "A lap joint flange never touches the fluid and rotates freely on a "
            "stub end; a slip-on welds directly to the pipe. Full dimension and "
            "selection comparison.",
            "Lap Joint vs Slip-On Flange",
            "Same B16.5 dimensions, very different assembly: one flange is "
            "welded to the line, the other floats behind a stub end and never "
            "sees the process.",
            body, faq_pairs=q, card_meta="Stub end · alloy saving · rotation")


def cmp_rf_rtj(b165):
    rf = b165["raised_face"]
    rf_h = {c: b165["classes"][c].get("rf_height") for c in CLASS_ORDER
            if c in b165["classes"]}
    rows = []
    for r in b165["classes"]["150"]["rows"]:
        nps = r["nps"]
        if nps not in rf:
            continue
        rows.append([
            f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(rf[nps], 2), dual(r["o"], 2),
            dual(rf_h.get("150") or 0.06, 2),
            dual(0.25, 2),
        ])

    body = (facts([("RF height, Cl 150/300", "1/16 in (1.6 mm)"),
                   ("RF height, Cl 400+", "1/4 in (6.4 mm)"),
                   ("RTJ seal", "Metal ring, plastic deformation"),
                   ("Typical RTJ use", "Class 600 and above")])
            + verdict(
                "A raised face seals a compressed gasket between two flat "
                "annular surfaces; a ring type joint seals a solid metal ring "
                "crushed into a machined groove. RF is the B16.5 default and "
                "covers most service. RTJ takes over at high pressure and "
                "temperature — usually Class 600 upward — because a metal seal "
                "survives thermal cycling that flattens a sheet or spiral "
                "wound gasket.")
            + "<h2>How each one actually seals</h2>"
            "<p>A raised face concentrates the bolt load onto a smaller annulus "
            "than a flat face would, raising the gasket seating stress for the "
            "same bolt torque. The gasket — sheet, spiral wound, or "
            "kammprofile — deforms into the serrated finish and seals by "
            "compression. It relaxes over time and with temperature, which is "
            "why RF joints need retorquing after a thermal cycle.</p>"
            "<p>An RTJ groove takes a solid metal ring, oval or octagonal, "
            "softer than the flange. Tightening the bolts yields the ring into "
            "the groove flanks, so the seal is a metal-to-metal interference "
            "fit rather than a compressed pad. The flange faces stand apart and "
            "never touch, and internal pressure acting on the ring tends to "
            "push it harder against the groove — the seal gets tighter as "
            "pressure rises.</p>"
            + '<p class="formula">RF: gasket compressed between faces · RTJ: '
            "metal ring yielded into a groove</p>"
            + UNITS_NOTE
            + table(["NPS", "Raised face OD", "Class 150 flange OD",
                     "RF height, Cl 150/300", "RF height, Cl 400+"], rows,
                    caption="Raised face gasket contact diameters, ASME "
                            "B16.5.",
                    note="Raised face outside diameter is common to Classes 150 "
                         "through 600. Classes 900, 1500 and 2500 use different "
                         "gasket surfaces in several sizes — check B16.5 Table 9 "
                         "before ordering a gasket.")
            + vs_columns(
                "Raised face (RF)",
                ["The B16.5 default — widest availability and lowest cost",
                 "Works with cheap sheet and spiral wound gaskets",
                 "Tolerant of modest face damage and misalignment",
                 "Easy to inspect: face contact is visible",
                 "Relaxes with thermal cycling and needs retorquing"],
                "Ring type joint (RTJ)",
                ["Metal seal survives high temperature and thermal cycling",
                 "Pressure-energised — tighter as pressure rises",
                 "Standard for Class 600 and above in hydrocarbon service",
                 "Groove must be machined true and undamaged; repair is costly",
                 "Ring is a single-use consumable and adds standoff to the "
                 "spool length"])
            + "<h2>Flat face is the third option</h2>"
            "<p>A flat face flange has no raised area at all and takes a "
            "full-face gasket. It exists for one reason: bolting to a brittle "
            "casting. Against a cast iron pump or valve flange, a raised face "
            "bends the mating flange as the bolts pull it in and cracks it. Any "
            "time steel meets cast iron, the steel flange should be flat faced "
            "with a full-face gasket. See the "
            '<a href="/reference/flange-face-types/">full face type '
            "reference</a>.</p>"
            "<h2>Never mix faces</h2>"
            "<p>An RF flange will not seal against an RTJ flange, and a raised "
            "face against a flat face is the cracked-casting scenario above. "
            "Face type is part of the flange specification and must match on "
            "both halves of every joint — it is worth checking on site rather "
            "than trusting the isometric.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/flange-face-types/">all six face types</a> · '
            '<a href="/guides/flange-face-types/">choosing a face</a> · '
            '<a href="/guides/flange-bolt-torque/">bolt torque</a> · '
            '<a href="/flanges/">flange dimensions</a>.</p></div>')

    q = [
        ("Can an RF flange bolt to an RTJ flange?",
         "<p>No. The RTJ flange has a machined groove and no flat gasket "
         "surface at the right diameter, so there is nothing for an RF gasket "
         "to seal against. The faces must match.</p>"),
        ("At what class should I switch to RTJ?",
         "<p>There is no code threshold. In practice most specs use RF through "
         "Class 300 or 600 and RTJ above, with the switch driven by temperature "
         "and thermal cycling as much as by pressure.</p>"),
        ("How tall is the raised face?",
         "<p>1/16 in (1.6 mm) for Classes 150 and 300, and 1/4 in (6.4 mm) for "
         "Class 400 and above. The tabulated flange thickness excludes it, so "
         "the measured flange is thicker than the table.</p>"),
        ("Are RTJ rings reusable?",
         "<p>No. The ring seals by yielding into the groove, so it is "
         "permanently deformed once made up. Fit a new ring every time the "
         "joint is broken.</p>"),
        ("What gasket does a flat face flange need?",
         "<p>A full-face gasket that covers the entire face including the bolt "
         "holes. A ring gasket on a flat face lets the flange bend inside the "
         "bolt circle.</p>"),
    ]

    compare("raised-face-vs-ring-type-joint",
            "Raised Face vs RTJ Flange: Sealing Compared",
            "A raised face compresses a gasket; an RTJ yields a metal ring into "
            "a groove. Gasket diameters, face heights and when to switch "
            "between them.",
            "Raised Face vs Ring Type Joint",
            "Two ways to seal a flanged joint — a compressed gasket or a "
            "yielded metal ring — and the pressure and temperature at which the "
            "second becomes necessary.",
            body, faq_pairs=q, card_meta="RF vs RTJ · sealing · gaskets")

# --------------------------------------------------------------------------
# pipe type and material comparisons
# --------------------------------------------------------------------------

def grade(mats, spec):
    """Look a material grade out of materials.yaml by its spec string."""
    for cat in mats:
        for g in cat["grades"]:
            if g["spec"] == spec:
                return g
    raise KeyError(spec)


def grade_table(mats, specs, caption):
    rows = []
    for s in specs:
        g = grade(mats, s)
        rows.append([f"<strong>{esc(g['spec'])}</strong>", esc(g["form"]),
                     f"{g['tensile']} ksi", f"{g['yield']} ksi",
                     esc(g["temp"]), esc(g["note"])])
    return table(["Specification", "Form", "Tensile min", "Yield min",
                  "Temperature range", "Notes"], rows, caption=caption,
                 note="Strengths are specified minimums in ksi. 1 ksi = 1000 "
                      "psi = 6.895 MPa.")


# Longitudinal weld joint quality factor E, ASME B31.3 Table A-1B. E multiplies
# the allowable stress in the wall thickness equation, so it is the number that
# makes seamless and welded pipe different on paper.
JOINT_EFFICIENCY = [
    ("Seamless", "1.00", "No longitudinal weld exists, so nothing derates."),
    ("Electric resistance welded (ERW)", "0.85",
     "Longitudinal weld, no filler metal, not normally radiographed."),
    ("Electric fusion welded, double butt, 100% radiographed", "1.00",
     "Full radiography restores the seamless factor."),
    ("Electric fusion welded, double butt, spot radiographed", "0.90",
     "Partial examination, partial credit."),
    ("Electric fusion welded, single butt", "0.80",
     "Welded from one side only."),
    ("Furnace butt welded, continuous weld", "0.60",
     "The lowest factor in the table; A53 Type F.")]


def cmp_seamless_welded(pipes, mats):
    eff_rows = [[f"<strong>{esc(m)}</strong>", e, esc(note)]
                for m, e, note in JOINT_EFFICIENCY]

    # Required wall at 1000 psig, NPS 6, S = 20,000 psi, for each E factor.
    six = next(s for s in pipes["sizes"] if s["nps"] == "6")
    P, S = 1000.0, 20000.0
    wall_rows = []
    for m, e, _ in JOINT_EFFICIENCY:
        ef = float(e)
        t = P * six["od"] / (2 * (S * ef + P * 0.4))
        wall_rows.append([f"<strong>{esc(m)}</strong>", e,
                          f"{t:.4f} in", f"{t * MM:.2f} mm",
                          pct(t, P * six["od"] / (2 * (S * 1.0 + P * 0.4)))])

    body = (facts([("Seamless joint factor E", "1.00"),
                   ("ERW joint factor E", "0.85"),
                   ("Furnace butt weld E", "0.60"),
                   ("Effect on wall", "Lower E ⇒ thicker wall required")])
            + verdict(
                "Seamless pipe is pierced and drawn from a solid billet and has "
                "no longitudinal weld; welded pipe is rolled from plate or strip "
                "and seam-welded. The practical difference is the joint quality "
                "factor E in the wall thickness equation: seamless takes E = "
                "1.00, ordinary ERW takes 0.85, and furnace butt welded takes "
                "0.60. A lower E means a thicker wall for the same pressure — "
                "so welded pipe is cheaper per foot but not always cheaper per "
                "installed line.")
            + "<h2>E is where the difference becomes arithmetic</h2>"
            "<p>ASME B31.3 sizes a pressure-retaining wall with the same "
            "equation whatever the pipe is made of, and the manufacturing "
            "method enters only through E:</p>"
            + '<p class="formula">t = P·D / [ 2(S·E·W + P·Y) ]</p>'
            "<p>Drop E from 1.00 to 0.85 and the required wall rises by about "
            "18%. Whether that costs money depends on whether the next "
            "schedule up is needed — if the calculated wall still fits inside "
            "the schedule you were going to buy anyway, the lower E is free.</p>"
            + table(["Manufacturing method", "Joint factor E", "Why"],
                    eff_rows,
                    caption="Longitudinal weld joint quality factor E, ASME "
                            "B31.3.",
                    note="E multiplies the allowable stress. It applies to the "
                         "longitudinal seam only — it does not derate girth "
                         "welds.")
            + "<h2>Worked example: NPS 6 at 1000 psig</h2>"
            f"<p>Take NPS 6 pipe, outside diameter {inch_mm(six['od'])}, "
            "design pressure 1000 psig, allowable stress 20,000 psi, Y = 0.4. "
            "The required pressure-design wall for each manufacturing method "
            "is:</p>"
            + table(["Manufacturing method", "E", "Required wall",
                     "Required wall (mm)", "vs seamless"], wall_rows,
                    caption="Pressure design thickness at 1000 psig, NPS 6, "
                            "S = 20,000 psi.",
                    note="Pressure design thickness only. Corrosion allowance, "
                         "threading and grooving depth, and mill tolerance "
                         "(commonly 12.5%) are added on top.")
            + vs_columns(
                "Seamless pipe",
                ["No longitudinal weld to inspect, derate or fail",
                 "E = 1.00, so the thinnest calculated wall",
                 "Required by many specs for hydrogen, lethal and "
                 "high-temperature service",
                 "Available in heavy walls and small bore where welded is not",
                 "More expensive per foot, and lead times can be long",
                 "Wall thickness varies more around the circumference"],
                "Welded pipe (ERW/EFW)",
                ["Cheaper per foot, especially in large diameter",
                 "More uniform wall thickness and better dimensional control",
                 "Widely available from stock in common sizes",
                 "Carries a joint factor below 1.00 unless fully radiographed",
                 "The seam is a defined feature — it must be located away from "
                 "branch connections and supports",
                 "Excluded by some specs from severe cyclic and sour service"])
            + "<h2>Large diameter changes the answer</h2>"
            "<p>Above about NPS 16, seamless pipe becomes scarce and expensive "
            "— the piercing process has practical limits. Large-diameter lines "
            "are normally welded pipe, and where the service is demanding the "
            "answer is not seamless but <em>fully radiographed</em> welded "
            "pipe, which recovers E = 1.00 through examination rather than "
            "through the absence of a seam.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/compare/a106-vs-a53-pipe/">A106 vs A53</a> · '
            '<a href="/guides/pipe-wall-thickness-calculation/">wall thickness '
            "calculation</a> · "
            '<a href="/reference/material-grades/">material grades</a> · '
            '<a href="/guides/pipe-material-selection/">material '
            "selection</a>.</p></div>")

    q = [
        ("Is seamless pipe stronger than welded pipe?",
         "<p>The base metal is the same strength. What differs is the "
         "longitudinal seam: welded pipe carries a joint quality factor E below "
         "1.00 unless it is fully radiographed, which means a thicker wall is "
         "required for the same pressure.</p>"),
        ("What is the joint efficiency of ERW pipe?",
         "<p>E = 0.85 for ordinary electric resistance welded pipe under ASME "
         "B31.3. Fully radiographed double-butt electric fusion welded pipe "
         "takes E = 1.00.</p>"),
        ("Is A53 seamless or welded?",
         "<p>Both. ASTM A53 covers Type S (seamless), Type E (electric "
         "resistance welded) and Type F (furnace butt welded). The type must be "
         "stated on the purchase order because each carries a different joint "
         "factor.</p>"),
        ("Why is seamless pipe specified for high-temperature service?",
         "<p>Partly for the joint factor, and partly because the seam is a "
         "metallurgical discontinuity that behaves differently from the base "
         "metal under creep and thermal cycling.</p>"),
        ("Does the weld seam need to be oriented in a particular way?",
         "<p>Most specs require the longitudinal seam to be positioned away "
         "from branch connections, from support contact points, and generally "
         "in the upper quadrant so it can be inspected.</p>"),
    ]

    compare("seamless-vs-welded-pipe",
            "Seamless vs Welded Pipe: Joint Factor and Wall",
            "Seamless pipe takes joint factor E = 1.00; ERW takes 0.85, which "
            "raises the required wall about 18%. Full E table and a worked wall "
            "thickness example.",
            "Seamless vs Welded Pipe",
            "The difference is a single number in the wall thickness equation. "
            "Here is what the joint quality factor E does to the metal you have "
            "to buy.",
            body, faq_pairs=q, card_meta="Joint factor E · wall thickness")


def cmp_cs_ss(pt, mats):
    temps = pt["temperatures"]
    g11 = rating_row(pt, "150", "1-1")
    g21 = rating_row(pt, "150", "2-1")
    h11 = rating_row(pt, "300", "1-1")
    h21 = rating_row(pt, "300", "2-1")
    rows = [[f"<strong>{t} °F</strong>", f"{g11[i]} psig", f"{g21[i]} psig",
             pct(g21[i], g11[i]), f"{h11[i]} psig", f"{h21[i]} psig",
             pct(h21[i], h11[i])]
            for i, t in enumerate(temps)]
    # The crossover: where stainless overtakes carbon steel in Class 300.
    # Computed from the rating tables rather than asserted, so a data edit
    # cannot leave the prose claiming the wrong temperature.
    cross = next((temps[i] for i in range(len(temps)) if h21[i] > h11[i]),
                 temps[-1])

    body = (facts([("Carbon steel group", "B16.5 Group 1.1 (A105)"),
                   ("Stainless group", "B16.5 Group 2.1 (F304)"),
                   ("Ambient advantage", "Carbon steel"),
                   ("High-temperature advantage", f"Stainless, above {cross} °F")])
            + verdict(
                "Carbon steel is stronger at ambient temperature, far cheaper, "
                "and easier to weld. Austenitic stainless starts lower but "
                "holds its rating almost flat to 1000 °F, and it resists "
                "corrosion that would consume carbon steel. Carbon steel is the "
                "default for utility, steam and clean hydrocarbon service; "
                "stainless is specified for corrosion, contamination "
                "sensitivity, cryogenic service and sustained high "
                f"temperature — above about {cross} °F it also carries the "
                "higher pressure rating.")
            + "<h2>The rating curves cross</h2>"
            "<p>This is the fact that surprises people. At 100 °F a Class 300 "
            f"carbon steel flange is rated {h11[0]} psig against stainless at "
            f"{h21[0]} — carbon steel wins. Carbon steel then falls away "
            "steadily as temperature rises, while austenitic stainless flattens "
            f"out. By {cross} °F the stainless rating is the higher of the two, "
            "and by 1000 °F it is several times higher.</p>"
            + "<p>Carbon steel is also limited by oxidation and "
            "graphitisation before its rating runs out. B16.5 permits it "
            "above 800 °F but advises against prolonged use there, and most "
            "specifications stop carbon steel at about that temperature. "
            "Stainless keeps going.</p>"
            + table(["Temperature", "Cl 150 carbon", "Cl 150 stainless",
                     "Difference", "Cl 300 carbon", "Cl 300 stainless",
                     "Difference"], rows,
                    caption="ASME B16.5 pressure-temperature ratings, Group "
                            "1.1 carbon steel against Group 2.1 austenitic "
                            "stainless.",
                    note="Group 1.1 is A105 and equivalents; Group 2.1 is "
                         "F304. Type 316 is Group 2.2 and rates slightly "
                         "higher when hot. Ratings are maximum allowable working "
                         "gauge pressure at metal temperature.")
            + "<h2>Strength and temperature limits by grade</h2>"
            + grade_table(mats,
                          ["ASTM A106 Gr B", "ASTM A53 Gr B", "ASTM A333 Gr 6",
                           "ASTM A312 TP304", "ASTM A312 TP316",
                           "ASTM A312 TP316L", "ASTM A790 S31803"],
                          "Common carbon and stainless pipe grades compared.")
            + vs_columns(
                "Carbon steel",
                ["Cheapest material in the table by a wide margin",
                 "Higher allowable stress at and near ambient",
                 "Easy to weld with no special filler or purge",
                 "Needs a corrosion allowance — typically 1/16 in",
                 "Practical upper limit around 800 °F",
                 "Brittle below about −20 °F unless impact tested (A333)"],
                "Austenitic stainless",
                ["Holds its rating to 1000 °F and beyond",
                 "No corrosion allowance needed in most clean services",
                 "Tough to cryogenic temperatures — usable to −425 °F",
                 "Three to five times the material cost",
                 "Needs a back purge when welded, and sensitises if held at "
                 "800–1500 °F unless a low-carbon grade is used",
                 "Vulnerable to chloride stress corrosion cracking"])
            + "<h2>Thermal expansion is a real constraint</h2>"
            "<p>Austenitic stainless expands roughly 40–50% more than carbon "
            "steel per degree. A stainless line designed with a carbon steel "
            "flexibility layout will overload its anchors and nozzles. Any "
            "material substitution between the two has to go back through the "
            "stress analysis — it is not a like-for-like swap.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/compare/304-vs-316-stainless-steel-pipe/">304 vs 316</a> · '
            '<a href="/compare/a106-vs-a53-pipe/">A106 vs A53</a> · '
            '<a href="/reference/pressure-temperature-ratings/">full P-T '
            "ratings</a> · "
            '<a href="/guides/pipe-material-selection/">material '
            "selection</a>.</p></div>")

    q = [
        ("Is stainless steel pipe stronger than carbon steel?",
         "<p>Not at ambient temperature — carbon steel has the higher allowable "
         "stress and the higher B16.5 rating there. Stainless becomes stronger "
         f"above roughly {cross} °F because it derates far more slowly.</p>"),
        ("Why does carbon steel piping stop at 800 °F?",
         "<p>Oxidation and graphitisation, not the pressure rating. Carbon "
         "steel scales rapidly in air above that range and long exposure can "
         "convert carbides to graphite, embrittling the steel.</p>"),
        ("How much more does stainless pipe cost?",
         "<p>Typically three to five times carbon steel per foot for 304/316, "
         "which is why thin-wall S-schedules are used — removing the corrosion "
         "allowance removes a large part of the cost.</p>"),
        ("Can stainless and carbon steel pipe be welded together?",
         "<p>Yes, with a suitable filler — usually a 309 type — and with "
         "attention to the differential thermal expansion at the joint. "
         "Dissimilar metal welds are also a galvanic couple and need "
         "consideration in wet service.</p>"),
        ("Does stainless need a corrosion allowance?",
         "<p>Usually not in clean service, which is the main reason thin-wall "
         "S-schedule pipe is viable in stainless and not in bare carbon "
         "steel.</p>"),
    ]

    compare("carbon-steel-vs-stainless-steel-pipe",
            "Carbon Steel vs Stainless Pipe: Ratings & Cost",
            "Carbon steel is stronger and cheaper at ambient; stainless holds "
            "its rating to 1000 °F. Full pressure-temperature and material "
            "strength comparison.",
            "Carbon Steel vs Stainless Steel Pipe",
            "Two materials whose pressure-temperature curves cross. Where each "
            "wins, what each costs, and the thermal expansion difference that "
            "makes substitution non-trivial.",
            body, faq_pairs=q, card_meta="P-T curves · cost · temperature")


def cmp_a106_a53(mats, pipes):
    body = (facts([("Both", "Carbon steel, 60 ksi tensile, 35 ksi yield"),
                   ("A106", "Seamless only, high-temperature service"),
                   ("A53", "Seamless or welded, general service"),
                   ("Temperature limit", "A106 800 °F · A53 750 °F")])
            + verdict(
                "A106 Grade B and A53 Grade B have the same minimum tensile and "
                "yield strength, so at ambient they are interchangeable on "
                "strength alone. They are not interchangeable in service: A106 "
                "is a seamless high-temperature specification with tighter "
                "chemistry and mandatory mechanical testing, while A53 is a "
                "general-service specification that also permits welded pipe. "
                "Specify A106 for process and high-temperature lines, A53 for "
                "utility and structural work.")
            + "<h2>Same numbers, different specifications</h2>"
            + grade_table(mats,
                          ["ASTM A106 Gr A", "ASTM A106 Gr B", "ASTM A106 Gr C",
                           "ASTM A53 Gr B", "API 5L Gr B"],
                          "A106 and A53 grades with their specified minimum "
                          "strengths and temperature ranges.")
            + "<h2>What A106 adds</h2>"
            "<p>A106 is titled <em>Seamless Carbon Steel Pipe for "
            "High-Temperature Service</em>, and every word of that is load "
            "bearing. It is seamless only — there is no welded A106. It "
            "specifies tighter limits on carbon, manganese and silicon, "
            "requires a minimum silicon content for elevated-temperature "
            "performance, and mandates tensile, bend and flattening tests on "
            "the product. A53 requires less, permits three manufacturing types, "
            "and is written for ordinary service.</p>"
            + table(
                ["Property", "ASTM A106 Gr B", "ASTM A53 Gr B"],
                [["<strong>Full title</strong>",
                  "Seamless carbon steel pipe for high-temperature service",
                  "Pipe, steel, black and hot-dipped, zinc-coated, welded and "
                  "seamless"],
                 ["<strong>Manufacture</strong>", "Seamless only",
                  "Type S seamless, Type E ERW, Type F furnace butt welded"],
                 ["<strong>Joint factor E</strong>", "1.00",
                  "1.00 seamless, 0.85 ERW, 0.60 furnace butt welded"],
                 ["<strong>Tensile minimum</strong>", "60 ksi", "60 ksi"],
                 ["<strong>Yield minimum</strong>", "35 ksi", "35 ksi"],
                 ["<strong>Silicon</strong>", "0.10% minimum specified",
                  "Not specified"],
                 ["<strong>Typical service limit</strong>", "800 °F", "750 °F"],
                 ["<strong>Galvanised available</strong>", "No", "Yes"],
                 ["<strong>Typical use</strong>",
                  "Process piping, steam, refinery and power service",
                  "Water, air, low-pressure steam, fire mains, structural"]],
                caption="ASTM A106 Grade B against ASTM A53 Grade B.",
                note="Both are ASME B31.3 listed materials. The differences are "
                     "in manufacturing route, chemistry control and testing "
                     "rather than in specified strength.")
            + "<h2>Dual-certified pipe</h2>"
            "<p>A great deal of seamless carbon steel pipe is rolled to meet "
            "A106 Gr B, A53 Gr B and API 5L Gr B simultaneously and stencilled "
            "with all three. That is legitimate — the requirements are "
            "compatible — and it is why a warehouse may offer one item against "
            "any of the three call-outs. Check the stencil rather than assuming "
            "it: A53 Type E pipe cannot be dual-certified to A106, because A106 "
            "does not permit a weld.</p>"
            + vs_columns(
                "Specify A106 when",
                ["The line is process, steam or above about 400 °F",
                 "The spec requires seamless pipe",
                 "Joint factor E = 1.00 is needed to make the wall work",
                 "The service is cyclic, hot, or safety-critical",
                 "Bending or cold forming is planned — Grade A for tight radii"],
                "Specify A53 when",
                ["The service is water, air, fire main or low-pressure steam",
                 "Galvanised pipe is wanted — A106 is not galvanised",
                 "The line is structural, a sleeve, or a support",
                 "Cost and availability matter more than pedigree",
                 "Threading is planned in ordinary utility service"])
            + '<div class="callout"><p>See also: '
            '<a href="/compare/seamless-vs-welded-pipe/">seamless vs '
            "welded</a> · "
            '<a href="/reference/material-grades/">material grades</a> · '
            '<a href="/guides/pipe-material-selection/">material '
            "selection</a> · "
            '<a href="/pipes/schedule-40/">Schedule 40 dimensions</a>.'
            "</p></div>")

    q = [
        ("Can A53 pipe be substituted for A106?",
         "<p>Not without approval. Even seamless A53 Gr B lacks A106's "
         "chemistry control and elevated-temperature testing, and A53 Type E is "
         "welded, which A106 does not permit. Substitution the other way — A106 "
         "in place of A53 — is generally acceptable.</p>"),
        ("Do A106 and A53 have the same strength?",
         "<p>Grade B of each specifies 60 ksi minimum tensile and 35 ksi "
         "minimum yield. The specified strengths are identical; the "
         "specifications are not.</p>"),
        ("Is A106 available galvanised?",
         "<p>No. A53 covers hot-dipped zinc-coated pipe; A106 does not. "
         "Galvanised process pipe would in any case be unsuitable for the "
         "high-temperature service A106 exists for.</p>"),
        ("What is dual-certified A106/A53/API 5L pipe?",
         "<p>Seamless pipe manufactured and tested to satisfy all three "
         "specifications at once and stencilled accordingly. It is common in "
         "distribution stock for the Grade B carbon steel sizes.</p>"),
        ("Which grade should I use for cold bending?",
         "<p>A106 Grade A. Its lower carbon makes it softer and more formable "
         "than Grade B, at the cost of some strength.</p>"),
    ]

    compare("a106-vs-a53-pipe",
            "A106 vs A53 Pipe: Same Strength, Different Spec",
            "A106 Gr B and A53 Gr B share 60 ksi tensile and 35 ksi yield, but "
            "A106 is seamless-only for high-temperature service. Full "
            "specification comparison.",
            "ASTM A106 vs A53 Pipe",
            "Identical specified strengths and very different specifications. "
            "What A106 controls that A53 does not, and when the difference "
            "actually matters.",
            body, faq_pairs=q, card_meta="Grade B · seamless vs welded")


def cmp_304_316(mats, pt):
    g21 = rating_row(pt, "300", "2-1")
    g22 = rating_row(pt, "300", "2-2")
    g23 = rating_row(pt, "300", "2-3")
    temps = pt["temperatures"][:len(g23)]
    rows = [[f"<strong>{t} °F</strong>", f"{g21[i]} psig", f"{g22[i]} psig",
             pct(g22[i], g21[i]), f"{g23[i]} psig"]
            for i, t in enumerate(temps)]
    # Where 316 rates above 304 and by how much, from the tables.
    all_t = pt["temperatures"]
    gaps = [(all_t[i], g22[i] - g21[i]) for i in range(len(all_t))]
    widest = max(gaps, key=lambda x: x[1])

    body = (facts([("Key difference", "316 adds 2–3% molybdenum"),
                   ("Chloride pitting", "316 markedly better"),
                   ("Cost premium", "316 roughly 20–30% over 304"),
                   ("Strength", "Identical specified minimums"),
                   ("B16.5 group", "304 is Group 2.1, 316 is Group 2.2")])
            + verdict(
                "304 and 316 have the same specified minimum strength at "
                "room temperature, but ASME B16.5 puts them in different "
                "material groups: 304 in Group 2.1 and 316 in Group 2.2. "
                f"Their flange ratings are equal at 100 °F and 316 rates "
                f"higher when hot, by up to {widest[1]} psig in Class 300 "
                f"at {widest[0]} °F. The main difference is chemistry: 316 "
                "contains 2–3% "
                "molybdenum, which markedly improves resistance to chloride "
                "pitting and crevice corrosion. Use 304 for clean, dry and "
                "non-chloride service; use 316 wherever chlorides, seawater, "
                "or aggressive process chemistry are present.")
            + "<h2>Molybdenum is the whole story</h2>"
            "<p>Both are austenitic 18-8 type stainless steels — roughly 18% "
            "chromium, 8–10% nickel — relying on a passive chromium oxide film "
            "for corrosion resistance. Chloride ions attack that film locally "
            "and start pits. Molybdenum stabilises the film against exactly "
            "that attack, which is why 316 survives coastal atmospheres, "
            "brackish water and chloride-bearing process streams that pit "
            "304.</p>"
            "<p>Neither grade is immune to chloride <em>stress corrosion "
            "cracking</em>, which is a different mechanism and remains the "
            "characteristic failure mode of austenitic stainless above about "
            "140 °F in chloride service. Where that is the risk, the answer is "
            "a duplex grade such as 2205, not a step from 304 to 316.</p>"
            + grade_table(mats,
                          ["ASTM A312 TP304", "ASTM A312 TP304L",
                           "ASTM A312 TP316", "ASTM A312 TP316L",
                           "ASTM A790 S31803"],
                          "304, 316 and their low-carbon variants, with duplex "
                          "2205 for comparison.")
            + "<h2>The L grades matter more than 304 vs 316</h2>"
            "<p>Holding an austenitic stainless between roughly 800 and 1500 °F "
            "— which welding does, either side of the bead — precipitates "
            "chromium carbides at the grain boundaries and strips those "
            "boundaries of the chromium that was protecting them. The result is "
            "intergranular attack alongside the weld, called sensitisation or "
            "weld decay. The low-carbon L grades hold carbon low enough that "
            "there is little to precipitate.</p>"
            "<p>For welded piping the practical default is 316L, which is why "
            "it is the most commonly stocked stainless pipe grade. The cost of "
            "the L grade is a lower allowable stress — B16.5 puts the L "
            "grades in Group 2.3, below both 304 and 316:</p>"
            + table(["Temperature", "Class 300 Group 2.1 (304)",
                     "Class 300 Group 2.2 (316)", "316 against 304",
                     "Class 300 Group 2.3 (304L, 316L)"], rows,
                    caption="ASME B16.5 Class 300 flange ratings for Type "
                            "304, Type 316 and the low-carbon grades.",
                    note="Group 2.1 covers F304 and F304H; Group 2.2 covers "
                         "F316, F316H and F317; Group 2.3 covers the L "
                         "grades, with no B16.5 rating published above "
                         "850 °F and 304L limited to 800 °F.")
            + vs_columns(
                "Choose 304 / 304L when",
                ["The service is clean water, steam condensate, food or "
                 "pharmaceutical without chlorides",
                 "The environment is dry and inland",
                 "Cost matters and the corrosion case does not demand moly",
                 "Cryogenic service — both grades are tough to −425 °F",
                 "Nitric acid and other oxidising acids, where 304 is "
                 "actually the better choice"],
                "Choose 316 / 316L when",
                ["Chlorides are present in any concentration worth measuring",
                 "The plant is coastal or marine",
                 "The service is a process chemical, sour, or reducing",
                 "The line handles seawater, brine, or treated effluent",
                 "The pipe is insulated — chlorides concentrate under "
                 "insulation as moisture evaporates"])
            + "<h2>A note on cost</h2>"
            "<p>316 typically runs 20–30% above 304 for the same item, and the "
            "premium tracks the nickel and molybdenum markets rather than "
            "staying fixed. On a large project the difference is real money — "
            "but a chloride pitting failure in a process line costs more than "
            "the entire material premium, so the corrosion case should decide "
            "this and not the budget.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/compare/carbon-steel-vs-stainless-steel-pipe/">carbon '
            "steel vs stainless</a> · "
            '<a href="/compare/schedule-5s-vs-schedule-10s/">5S vs 10S</a> · '
            '<a href="/reference/stainless-pipe-schedules/">B36.19M '
            "stainless</a> · "
            '<a href="/reference/material-grades/">material grades</a>.'
            "</p></div>")

    q = [
        ("What is the main difference between 304 and 316 stainless?",
         "<p>316 contains 2–3% molybdenum, which substantially improves "
         "resistance to chloride pitting and crevice corrosion. Specified "
         "minimum strength is the same; flange pressure ratings are equal "
         "at 100 °F and slightly higher for 316 when hot.</p>"),
        ("Is 316 stainless stronger than 304?",
         "<p>Not at room temperature: both A312 TP304 and TP316 specify "
         "75 ksi tensile and 30 ksi yield minimum. When hot, 316 keeps its "
         "strength a little better, which is why B16.5 puts it in Group 2.2 "
         f"and rates a Class 300 flange at {g22[5]} psig at 600 °F against "
         f"{g21[5]} psig for 304 in Group 2.1.</p>"),
        ("Should I use 316 or 316L?",
         "<p>316L for anything welded, which is nearly all piping. The low "
         "carbon prevents sensitisation at the weld. The cost is a slightly "
         "lower allowable stress, since B16.5 puts the L grades in Group "
         "2.3.</p>"),
        ("Will 316 stainless resist seawater?",
         "<p>Better than 304, but not indefinitely. In stagnant or crevice "
         "conditions 316 still pits in seawater. Continuous seawater service "
         "normally calls for a duplex, super-duplex or 6-moly grade.</p>"),
        ("Does 316 prevent chloride stress corrosion cracking?",
         "<p>No. Both 304 and 316 are susceptible above roughly 140 °F in "
         "chloride service. Duplex grades such as 2205 are the usual answer "
         "where SCC is the governing risk.</p>"),
    ]

    compare("304-vs-316-stainless-steel-pipe",
            "304 vs 316 Stainless Pipe: Which Grade to Use",
            "316 adds 2–3% molybdenum for chloride pitting resistance. 304 "
            "and 316 share minimum strength but sit in different B16.5 "
            "rating groups. Full comparison.",
            "304 vs 316 Stainless Steel Pipe",
            "Same minimum strength, nearly the same pressure rating, "
            "different corrosion behaviour. What the molybdenum buys, and "
            "why the L grade question matters more than the 304-versus-316 "
            "one.",
            body, faq_pairs=q, card_meta="Molybdenum · chlorides · L grades")


# --------------------------------------------------------------------------
# fitting comparisons
# --------------------------------------------------------------------------

def fitting_by_slug(fittings):
    return {f["slug"]: f for f in fittings}


def cmp_lr_sr_elbow(fittings, pipes):
    fb = fitting_by_slug(fittings)
    lr, sr = fb["90-degree-elbow"], fb["90-degree-elbow-short-radius"]
    shared = sorted(set(lr["rows"]) & set(sr["rows"]), key=nps_value)
    # NPS 3/4 is tabulated below the 1.5 x NPS rule, so the ratio column is
    # computed per size rather than asserted as a constant.
    rows = []
    for nps in shared:
        a, b = lr["rows"][nps], sr["rows"][nps]
        rows.append([
            f"<strong>NPS {esc(nps)}</strong>",
            dual(a, 2), dual(b, 2), f"{a - b:+.2f} in",
            f"{b / a:.2f}" if a else '<span class="na">—</span>',
        ])
    saved = sum(lr["rows"][n] - sr["rows"][n] for n in shared) / len(shared)

    body = (facts([("Long radius", "Centreline radius = 1.5 × NPS"),
                   ("Short radius", "Centreline radius = 1.0 × NPS"),
                   ("Average length saved", f"{saved:.2f} in"),
                   ("Pressure drop", "Short radius roughly double")])
            + verdict(
                "A long radius elbow turns through a centreline radius of 1.5 "
                "times the nominal size; a short radius elbow uses 1.0 times. "
                "Long radius is the default everywhere — lower pressure drop, "
                "less erosion, and easier to inspect. Short radius exists for "
                "one reason: it fits where a long radius will not. Use it only "
                "where space genuinely forces it.")
            + "<h2>Two radii, one standard</h2>"
            f"<p>ASME B16.9 sets the long radius elbow centre-to-end at "
            f"<code>{esc(lr['formula'])}</code> and the short radius elbow at "
            f"<code>{esc(sr['formula'])}</code>. Both dimensions depend on NPS "
            "alone — they do not change with wall thickness or schedule, so one "
            "table covers every schedule in that size.</p>"
            f"<p>Across the {len(shared)} sizes tabulated for both, the short "
            f"radius elbow saves an average of {saved:.2f} in per fitting. On a "
            "compact skid or inside a vessel skirt that can be the difference "
            "between a layout that fits and one that does not.</p>"
            + UNITS_NOTE
            + table(["NPS", "Long radius centre-to-end",
                     "Short radius centre-to-end", "Length saved",
                     "SR / LR ratio"], rows,
                    caption="90° long radius against short radius elbow "
                            "centre-to-end dimensions, ASME B16.9.",
                    note="Dimensions are a function of NPS only and apply to "
                         "every schedule in that size.")
            + "<h2>What the tighter turn costs</h2>"
            "<p>Pressure drop through a short radius elbow is roughly twice "
            "that of a long radius elbow — commonly quoted as an equivalent "
            "length of about 30 pipe diameters against 16 for the long radius, "
            "though the exact figures depend on the correlation used. On a "
            "system with many elbows that adds up to real pump head.</p>"
            "<p>The tighter turn also concentrates erosion. In slurry, "
            "catalyst, flashing and any particle-laden service, the outer heel "
            "of a short radius elbow wears through markedly faster. Erosive "
            "services normally specify long radius as a minimum and often go to "
            "3D or 5D bends instead.</p>"
            + vs_columns(
                "Long radius (1.5D)",
                ["The default — assume it unless something forces otherwise",
                 "About half the pressure drop of a short radius elbow",
                 "Better erosion and wear behaviour",
                 "Easier to pig, inspect and radiograph",
                 "Needs more space, which is its only real drawback"],
                "Short radius (1.0D)",
                ["Fits where a long radius elbow will not",
                 "Useful on compact skids, in vessel internals and headers",
                 "Roughly double the pressure drop",
                 "Higher erosion at the heel; poor in slurry service",
                 "Cannot be pigged in most systems"])
            + '<div class="callout"><p>Dimensions: '
            '<a href="/fittings/90-degree-elbow/">90° long radius elbow</a> · '
            '<a href="/fittings/90-degree-elbow-short-radius/">90° short radius '
            "elbow</a> · "
            '<a href="/compare/90-degree-vs-45-degree-elbow/">90° vs 45°</a> · '
            '<a href="/fittings/">all B16.9 fittings</a>.</p></div>')

    q = [
        ("What is the difference between LR and SR elbows?",
         "<p>The centreline radius. A long radius elbow uses 1.5 times the "
         "nominal pipe size; a short radius elbow uses 1.0 times. The short "
         "radius elbow is more compact and has roughly twice the pressure "
         "drop.</p>"),
        ("When should a short radius elbow be used?",
         "<p>Only when space forces it. Most piping specifications require long "
         "radius as standard and treat short radius as an exception needing "
         "justification.</p>"),
        ("Do LR and SR elbows have the same pressure rating?",
         "<p>Yes. B16.9 fittings are rated by their matching pipe wall, so an "
         "elbow in a given schedule and material carries the same rating "
         "regardless of radius.</p>"),
        ("Are elbow dimensions affected by schedule?",
         "<p>No. ASME B16.9 centre-to-end dimensions depend only on NPS. The "
         "schedule sets the wall thickness and the weld end preparation, not "
         "the length.</p>"),
    ]

    compare("long-radius-vs-short-radius-elbow",
            "Long vs Short Radius Elbow: Dimensions & Drop",
            "Long radius elbows turn through 1.5 × NPS, short radius through "
            "1.0 × NPS with roughly double the pressure drop. Full B16.9 "
            "dimension comparison.",
            "Long Radius vs Short Radius Elbow",
            "1.5D against 1.0D: how much length the tighter elbow saves, and "
            "how much pressure drop and erosion it costs you to save it.",
            body, faq_pairs=q, card_meta=f"1.5D vs 1.0D · {len(shared)} sizes")


def cmp_conc_ecc_reducer(fittings):
    fb = fitting_by_slug(fittings)
    con, ecc = fb["concentric-reducer"], fb["eccentric-reducer"]
    shared = sorted(set(con["rows"]) & set(ecc["rows"]), key=nps_value)
    rows = [[f"<strong>NPS {esc(nps)}</strong>", dual(con["rows"][nps], 2),
             dual(ecc["rows"][nps], 2),
             '<span class="yes">Identical</span>'
             if abs(con["rows"][nps] - ecc["rows"][nps]) < 1e-9 else
             f'{ecc["rows"][nps] - con["rows"][nps]:+.2f} in']
            for nps in shared]
    identical = all(abs(con["rows"][n] - ecc["rows"][n]) < 1e-9 for n in shared)

    body = (facts([("End-to-end length",
                    "Identical" if identical else "Differs by size"),
                   ("Concentric", "Common centreline"),
                   ("Eccentric", "Common tangent — one flat side"),
                   ("Length set by", "The larger end")])
            + verdict(
                "Both reducers change pipe size over the same end-to-end "
                "length; the difference is where the centreline goes. A "
                "concentric reducer keeps both ends on one centreline. An "
                "eccentric reducer keeps one side flat, offsetting the "
                "centreline by half the diameter difference. Use concentric on "
                "vertical runs, and eccentric on horizontal runs where trapped "
                "vapour or trapped liquid would cause a problem.")
            + "<h2>Same length, different centreline</h2>"
            + ("<p>ASME B16.9 gives the concentric and eccentric reducer the "
               "same end-to-end dimension H in every tabulated size, and that "
               "length is set by the larger of the two ends. Swapping one for "
               "the other therefore does not change the spool length — only "
               "the centreline elevation.</p>" if identical else
               "<p>ASME B16.9 tabulates end-to-end dimension H for both "
               "reducer types; the table below shows where they agree.</p>")
            + '<p class="formula">Eccentric offset = (larger ID − smaller ID) / 2</p>'
            + UNITS_NOTE
            + table(["Larger end NPS", "Concentric end-to-end H",
                     "Eccentric end-to-end H", "Difference"], rows,
                    caption="Concentric against eccentric reducer end-to-end "
                            "dimensions, ASME B16.9.",
                    note="H is set by the larger of the two ends, so a "
                         "6 × 4 and a 6 × 2 reducer share the same length.")
            + "<h2>Which way up the eccentric goes</h2>"
            "<p>This is the detail that gets built wrong, and it is worth being "
            "explicit about because the two orientations solve opposite "
            "problems.</p>"
            + table(
                ["Location", "Orientation", "Why"],
                [["<strong>Pump suction, horizontal</strong>",
                  "Flat side up (FSU)",
                  "A concentric reducer would form a high pocket at the top "
                  "where vapour collects, and the pump would cavitate. Flat "
                  "side up leaves no high point."],
                 ["<strong>Horizontal run, liquid service</strong>",
                  "Flat side down (FSD)",
                  "Keeps the bottom of the line continuous so liquid drains "
                  "and no low pocket forms."],
                 ["<strong>Horizontal run, gas or steam</strong>",
                  "Flat side down (FSD)",
                  "Lets condensate run through rather than pooling in a low "
                  "spot at the size change."],
                 ["<strong>Vertical run</strong>", "Concentric reducer",
                  "There is no top or bottom to trap anything, so the "
                  "symmetric fitting is correct and cheaper."]],
                caption="Reducer selection and orientation by location.",
                note="Flat side up on pump suction is the single most "
                     "frequently cited reducer rule, and the one most often "
                     "found installed backwards.")
            + vs_columns(
                "Concentric reducer",
                ["Vertical runs, in any service",
                 "Anywhere the centreline must be maintained",
                 "Symmetric flow, so no induced swirl or asymmetric wear",
                 "Slightly cheaper and more widely stocked",
                 "Traps vapour at the top on a horizontal liquid line"],
                "Eccentric reducer",
                ["Horizontal runs where a pocket would cause trouble",
                 "Pump suction lines — flat side up, always",
                 "Lines that must drain completely",
                 "Steam and condensate service",
                 "Offsets the centreline, which the pipe support design has "
                 "to account for"])
            + '<div class="callout"><p>Dimensions: '
            '<a href="/fittings/concentric-reducer/">concentric reducer</a> · '
            '<a href="/fittings/eccentric-reducer/">eccentric reducer</a> · '
            '<a href="/fittings/">all B16.9 fittings</a> · '
            '<a href="/guides/pipe-sizing/">pipe sizing</a>.</p></div>')

    q = [
        ("Are concentric and eccentric reducers the same length?",
         "<p>Yes. ASME B16.9 gives both the same end-to-end dimension H for a "
         "given larger end size, so substituting one for the other does not "
         "change the spool length.</p>"),
        ("Which way up does an eccentric reducer go on a pump suction?",
         "<p>Flat side up. A flat-side-down or concentric reducer creates a "
         "high pocket where vapour collects, which leads to cavitation at the "
         "pump.</p>"),
        ("When should I use a concentric reducer?",
         "<p>On vertical runs, and anywhere the centreline needs to stay put. "
         "On vertical pipe there is no pocket to worry about, so the symmetric "
         "fitting is correct.</p>"),
        ("What sets a reducer's length?",
         "<p>The larger of the two ends. A 6 × 4 reducer and a 6 × 2 reducer "
         "have the same end-to-end dimension because both have a 6 in "
         "end.</p>"),
    ]

    compare("concentric-vs-eccentric-reducer",
            "Concentric vs Eccentric Reducer: Which and Why",
            "Concentric and eccentric reducers share the same B16.9 end-to-end "
            "length; only the centreline differs. Full dimension table and "
            "orientation rules.",
            "Concentric vs Eccentric Reducer",
            "Identical lengths, different centrelines — and a flat-side-up rule "
            "on pump suction that is worth getting right the first time.",
            body, faq_pairs=q, card_meta="Same length · FSU/FSD rules")


def cmp_90_45_elbow(fittings):
    fb = fitting_by_slug(fittings)
    e90, e45 = fb["90-degree-elbow"], fb["45-degree-elbow"]
    shared = sorted(set(e90["rows"]) & set(e45["rows"]), key=nps_value)
    ratio = math.tan(math.radians(22.5))
    ratios = {nps: e45["rows"][nps] / e90["rows"][nps] for nps in shared}
    rows = [[f"<strong>NPS {esc(nps)}</strong>", dual(e90["rows"][nps], 2),
             dual(e45["rows"][nps], 2), f"{ratios[nps]:.3f}"]
            for nps in shared]
    # B16.9 publishes rounded dimensions, so the tabulated ratio is not exactly
    # tan 22.5. Find where it settles and which sizes depart from it, from the
    # data, rather than claiming the formula holds everywhere.
    large = [nps for nps in shared if nps_value(nps) >= 4]
    settled = sum(ratios[n] for n in large) / len(large)
    off = [nps for nps in shared if abs(ratios[nps] - settled) > 0.01]
    big = max(off, key=lambda n: abs(ratios[n] - settled)) if off else None

    body = (facts([("90° centre-to-end", "A = 1.5 × NPS"),
                   ("45° centre-to-end", "B = 1.5 × NPS × tan 22.5°"),
                   ("Tabulated B/A, NPS 4+", f"{settled:.3f}"),
                   ("Both", "Long radius, 1.5D centreline")])
            + verdict(
                "Both are long radius elbows turning on a 1.5 × NPS centreline "
                "radius; they differ only in how far around they go. The 45° "
                "elbow's centre-to-end dimension is geometrically the 90° "
                f"figure times tan 22.5° ≈ {ratio:.4f} — though B16.9 publishes "
                f"rounded values, so the tabulated ratio is nearer "
                f"{settled:.3f} from NPS 4 up. Either way it is a little over "
                "four-tenths the length. Two 45° elbows produce a gentler, "
                "lower-loss "
                "offset than one 90°, which is why offsets and jog-arounds are "
                "usually built from a pair of 45s.")
            + "<h2>Where the tangent comes from</h2>"
            "<p>Centre-to-end is measured from the intersection of the two end "
            "centrelines to the welding end. For an elbow of centreline radius "
            "R turning through angle θ, that distance is R·tan(θ/2). At 90° the "
            "half-angle is 45°, tan 45° = 1, and the dimension is simply R = "
            f"1.5 × NPS. At 45° the half-angle is 22.5°, tan 22.5° = "
            f"{ratio:.4f}, and the dimension shrinks accordingly.</p>"
            + '<p class="formula">Centre-to-end = R · tan(θ/2) &nbsp;·&nbsp; '
            "R = 1.5 × NPS for long radius</p>"
            + UNITS_NOTE
            + table(["NPS", "90° centre-to-end A", "45° centre-to-end B",
                     "B / A"], rows,
                    caption="90° against 45° long radius elbow centre-to-end "
                            "dimensions, ASME B16.9.",
                    note=f"The geometry gives tan 22.5° = {ratio:.4f}, but the "
                         "published dimensions are rounded, so the tabulated "
                         f"ratio settles near {settled:.3f} from NPS 4 upward "
                         "and departs from it in small bore. Always take the "
                         "dimension from the table.")
            + "<h2>The table is not the formula</h2>"
            f"<p>Worth noticing in the ratio column: the tabulated dimensions do "
            f"not follow tan 22.5° exactly. From NPS 4 upward the ratio settles "
            f"at about {settled:.3f} — B16.9 rounds the published figures to "
            "convenient fractions rather than carrying the trigonometry through."
            + (f" Below that the departure is larger still; at NPS "
               f"{esc(big)} the ratio is {ratios[big]:.3f}, because small-bore "
               "fittings are set by practical minimum dimensions rather than by "
               "the centreline radius rule." if big else "")
            + " The formula explains where the dimensions came from; the table "
            "is what the fitting actually measures, and it is the table that "
            "governs a fabrication drawing.</p>"
            + "<h2>Two 45s or one 90?</h2>"
            "<p>For a change of direction, one 90° elbow. For an offset — "
            "stepping the line sideways and continuing in the same direction — "
            "a pair of 45° elbows is almost always better. The flow turns twice "
            "through a gentler angle instead of twice through a sharp one, so "
            "the combined pressure loss is lower, and the offset distance can "
            "be tuned by varying the spool between them rather than being fixed "
            "by the fitting.</p>"
            + table(
                ["Requirement", "Use", "Why"],
                [["<strong>Change direction 90°</strong>", "One 90° LR elbow",
                  "The direct solution; fewest welds."],
                 ["<strong>Offset a line sideways</strong>", "Two 45° elbows",
                  "Lower total pressure drop than two 90s, and the offset is "
                  "adjustable by the spool length between them."],
                 ["<strong>Route around an obstruction</strong>",
                  "Two 45° elbows", "A gentler path with fewer flow "
                  "disturbances for downstream instruments."],
                 ["<strong>Enter a header at an angle</strong>",
                  "One 45° elbow",
                  "Reduces the turning loss into the header and keeps the "
                  "branch geometry compact."],
                 ["<strong>Upstream of a flow meter</strong>",
                  "Fewest fittings possible",
                  "Every elbow distorts the profile; meters specify straight "
                  "run in pipe diameters, so 45s help but do not "
                  "substitute."]],
                caption="Choosing between 90° and 45° elbows.")
            + '<div class="callout"><p>Dimensions: '
            '<a href="/fittings/90-degree-elbow/">90° long radius elbow</a> · '
            '<a href="/fittings/45-degree-elbow/">45° long radius elbow</a> · '
            '<a href="/compare/long-radius-vs-short-radius-elbow/">LR vs SR</a> · '
            '<a href="/fittings/">all B16.9 fittings</a>.</p></div>')

    q = [
        ("How do I calculate the centre-to-end of a 45° elbow?",
         f"<p>The geometry is B = 1.5 × NPS × tan 22.5° = 1.5 × NPS × "
         f"{ratio:.4f}, but ASME B16.9 publishes rounded dimensions, so read "
         f"the tabulated value rather than computing it. NPS 6 is "
         f"{n(e45['rows']['6'], 2)} in, not the {9.0 * ratio:.2f} in the "
         "formula gives.</p>"),
        ("Is a 45° elbow shorter than a 90° elbow?",
         f"<p>Yes — the centre-to-end dimension is about {settled:.3f} times "
         "the 90° figure in NPS 4 and above, a little over four-tenths, because "
         "the fitting turns through half the angle.</p>"),
        ("Do two 45° elbows have less pressure drop than one 90°?",
         "<p>For an offset, yes. Two gentle turns lose less than one sharp "
         "turn plus the recovery behind it, and the flow leaves the pair with "
         "a less distorted profile.</p>"),
        ("Do 45° elbows come in short radius?",
         "<p>ASME B16.9 does not tabulate a short radius 45° elbow. Short "
         "radius is published for the 90° elbow only.</p>"),
    ]

    compare("90-degree-vs-45-degree-elbow",
            "90° vs 45° Elbow: Dimensions and When to Use",
            "A 45° long radius elbow's centre-to-end is tan 22.5° times the 90° "
            "figure — about 0.41. Full B16.9 dimension table and offset "
            "guidance.",
            "90° vs 45° Elbow",
            "Same 1.5D centreline radius, half the turn. Where the tangent "
            "factor comes from, and why offsets are built from a pair of 45s.",
            body, faq_pairs=q, card_meta="tan 22.5° · offsets")

# --------------------------------------------------------------------------
# guides
# --------------------------------------------------------------------------

# Q (US gpm) = 3.1169 * A (in^2) * v (ft/s). Derivation: A*v gives in^2*ft/s;
# x12 -> in^3/s, x60 -> in^3/min, /231 -> US gal/min.
GPM_PER_INSQ_FTS = 12 * 60 / 231.0


def gpm(area_in2, v_fts):
    return GPM_PER_INSQ_FTS * area_in2 * v_fts


# Sizes most piping specifications leave off their standard size list, so a
# worked example that lands on one can say so rather than quietly recommending
# a size the reader's spec forbids.
ODD_SIZES = {"1 1/4", "2 1/2", "3 1/2", "5", "7", "9", "11", "22"}

# Customary design velocity limits. These are engineering practice, not code
# requirements, and the page says so.
VELOCITY_LIMITS = [
    ("Pump suction, liquid", "2 – 4 ft/s",
     "Kept low to protect NPSH available. High suction velocity is a leading "
     "cause of cavitation."),
    ("Pump discharge, liquid", "6 – 12 ft/s",
     "The usual economic range for water and light hydrocarbons."),
    ("General process liquid", "4 – 8 ft/s",
     "Balances pumping cost against pipe cost."),
    ("Gravity drain and flow lines", "2 – 5 ft/s",
     "Limited by available head, and must stay self-venting."),
    ("Slurry", "4 – 8 ft/s",
     "Above the settling velocity to keep solids suspended, below the erosion "
     "limit for the material."),
    ("Saturated steam", "80 – 150 ft/s",
     "Higher velocities are tolerable but noise and erosion rise sharply."),
    ("Superheated steam", "100 – 200 ft/s",
     "Dry steam is less erosive, so higher velocity is acceptable."),
    ("Compressed air and gas", "30 – 60 ft/s",
     "Set by pressure drop over the run rather than by erosion."),
    ("Pump suction, boiling liquid", "1 – 3 ft/s",
     "Anything at its bubble point needs the lowest suction velocity you can "
     "afford."),
]


def guide_pipe_sizing(pipes):
    rows = []
    for s in pipes["sizes"]:
        if "40" not in s["walls"] or s["val"] > 24:
            continue
        id_ = inside_dia(s["od"], s["walls"]["40"])
        a = area_sqin(id_)
        rows.append([
            f'<strong><a href="/pipes/nps-{s["slug"]}/#schedule-40">NPS {esc(s["nps"])}</a></strong>',
            dual(id_, 3), f"{a:.3f} in²",
            f"{gpm(a, 3):,.0f}", f"{gpm(a, 6):,.0f}", f"{gpm(a, 8):,.0f}",
            f"{gpm(a, 10):,.0f}",
            f"{gal_per_ft(id_):.3f}",
        ])

    vel_rows = [[f"<strong>{esc(svc)}</strong>", v, esc(note)]
                for svc, v, note in VELOCITY_LIMITS]

    # Worked example, selected from the data rather than asserted: the first
    # Schedule 40 size whose flow area meets the requirement.
    ex_q, ex_v = 400.0, 8.0
    req_a = ex_q / (GPM_PER_INSQ_FTS * ex_v)
    req_d = math.sqrt(4 * req_a / math.pi)
    pick = next(s for s in pipes["sizes"]
                if "40" in s["walls"]
                and area_sqin(inside_dia(s["od"], s["walls"]["40"])) >= req_a)
    pick_id = inside_dia(pick["od"], pick["walls"]["40"])
    pick_a = area_sqin(pick_id)
    pick_v = ex_q / (GPM_PER_INSQ_FTS * pick_a)

    body = (facts([("Sizing basis", "Velocity, then pressure drop"),
                   ("Typical liquid target", "6 – 8 ft/s"),
                   ("Pump suction target", "2 – 4 ft/s"),
                   ("Capacity formula", "Q = 3.117 × A × v")])
            + verdict(
                "Pipe is sized by picking a target velocity for the service, "
                "converting it to a required flow area, and then rounding up to "
                "the next standard NPS. Pressure drop is checked afterwards and "
                "governs on long runs. For ordinary pumped liquid, 6 to 8 ft/s "
                "is the usual starting point; pump suction lines are sized much "
                "lower, at 2 to 4 ft/s, to protect NPSH.")
            + "<h2>The sizing equation</h2>"
            "<p>Flow rate, area and velocity are related by continuity. In the "
            "units the work is actually done in:</p>"
            + '<p class="formula">Q (US gpm) = 3.117 × A (in²) × v (ft/s)'
            "<br>A (in²) = Q / (3.117 × v) &nbsp;·&nbsp; "
            "v (ft/s) = Q / (3.117 × A)</p>"
            "<p>The constant is not magic: it converts in²·ft/s to US gallons "
            "per minute — multiply by 12 for inches per second, by 60 for "
            "minutes, and divide by 231 cubic inches per gallon.</p>"
            "<h2>Worked example</h2>"
            f"<p>Size a line for {ex_q:.0f} gpm of cooling water on a pump "
            f"discharge, target velocity {ex_v:.0f} ft/s.</p>"
            "<ol>"
            f"<li>Required area: A = {ex_q:.0f} / (3.117 × {ex_v:.0f}) = "
            f"{req_a:.2f} in²</li>"
            f"<li>Required bore: d = √(4A/π) = {req_d:.2f} in</li>"
            f"<li>The smallest Schedule 40 size meeting that is "
            f'<a href="/pipes/nps-{pick["slug"]}/#schedule-40">NPS '
            f'{esc(pick["nps"])}</a>, bore {inch_mm(pick_id)}, area '
            f"{pick_a:.2f} in².</li>"
            f"<li>Actual velocity at {ex_q:.0f} gpm in NPS "
            f"{esc(pick['nps'])} Sch 40: v = {ex_q:.0f} / (3.117 × "
            f"{pick_a:.2f}) = {pick_v:.1f} ft/s — below the target, because "
            "the standard size is larger than the exact requirement.</li>"
            "</ol>"
            + (f"<p>Note that NPS {esc(pick['nps'])} is one of the sizes many "
               "piping specifications exclude from their standard size list. "
               "Where that applies, the next permitted size is taken instead — "
               "which is the usual reason a line ends up larger than the "
               "calculation strictly requires.</p>"
               if pick["nps"] in ODD_SIZES else "")
            +
            "<p>Always round <em>up</em> to the next standard size. Rounding "
            "down raises velocity, and pressure drop rises with roughly the "
            "square of velocity — so a half-size saving on pipe can cost far "
            "more in pump power over the life of the line.</p>"
            + "<h2>Design velocity by service</h2>"
            + table(["Service", "Typical velocity", "Why"], vel_rows,
                    caption="Customary design velocity ranges by service.",
                    note="These are common engineering practice, not code "
                         "requirements. Project specifications and the fluid's "
                         "own erosion behaviour override them.")
            + "<h2>Flow capacity, Schedule 40</h2>"
            "<p>Capacity at four common design velocities for every Schedule 40 "
            "size through NPS 24, computed from the published bore.</p>"
            + UNITS_NOTE
            + table(["NPS", "Bore (Sch 40)", "Flow area", "3 ft/s", "6 ft/s",
                     "8 ft/s", "10 ft/s", "US gal per ft"], rows,
                    caption="Schedule 40 flow capacity in US gpm at four design "
                            "velocities.",
                    note="Computed from the ASME B36.10M bore. For another "
                         "schedule, scale by the ratio of flow areas — see the "
                         "size pages for each schedule's bore.")
            + "<h2>Erosional velocity</h2>"
            "<p>For two-phase and gas service, API RP 14E gives an erosional "
            "velocity limit that is widely used as a check:</p>"
            + '<p class="formula">V<sub>e</sub> = C / √ρ &nbsp;·&nbsp; '
            "ρ in lb/ft³, V<sub>e</sub> in ft/s</p>"
            "<p>C is commonly taken as 100 for continuous service in carbon "
            "steel and 125 for intermittent service, with higher values for "
            "corrosion-resistant alloys and clean, non-corrosive fluids. The "
            "correlation is empirical and has been criticised as conservative "
            "for clean service and unconservative where sand is present — treat "
            "it as a screening check, not a design basis.</p>"
            "<h2>When pressure drop governs instead</h2>"
            "<p>Velocity sizing works because on a short line the velocity "
            "limit is reached before the pressure drop budget is. On long runs "
            "the reverse is true, and the line must be sized on the "
            "Darcy-Weisbach loss against the available head. As a rule of "
            "thumb, anything over a few hundred feet should be checked on "
            "pressure drop, and anything feeding a pump or a control valve "
            "should be checked regardless of length.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/pipes/schedule-40/">Schedule 40 dimensions</a> · '
            '<a href="/reference/pipe-weight-chart/">pipe weight chart</a> · '
            '<a href="/compare/schedule-40-vs-schedule-80/">how schedule '
            "changes the bore</a> · "
            '<a href="/guides/pipe-wall-thickness-calculation/">wall thickness '
            "from pressure</a>.</p></div>")

    q = [
        ("What velocity should I size a water line for?",
         "<p>6 to 8 ft/s for a pumped discharge line is the usual starting "
         "point. Pump suction lines are sized much lower — 2 to 4 ft/s — "
         "because suction velocity eats into the NPSH available.</p>"),
        ("How do I convert gpm to pipe size?",
         "<p>Divide the flow by 3.117 times the target velocity to get the "
         "required area in square inches, convert that to a diameter, and round "
         "up to the next standard NPS. The capacity table above does the same "
         "thing by lookup.</p>"),
        ("How many gallons per minute can a 4 inch pipe carry?",
         "<p>NPS 4 Schedule 40 has a bore of 4.026 in and a flow area of 12.73 "
         "in², giving about 238 gpm at 6 ft/s and 397 gpm at 10 ft/s. The "
         "practical answer depends entirely on the velocity you are willing to "
         "run.</p>"),
        ("Does schedule affect flow capacity?",
         "<p>Yes, significantly. A heavier schedule has the same outside "
         "diameter but a smaller bore, so it carries less at the same velocity. "
         "Schedule 80 gives up roughly 15% of the Schedule 40 flow area.</p>"),
        ("Why size pump suction lines larger than discharge lines?",
         "<p>To protect the net positive suction head available. Friction loss "
         "and velocity head on the suction side subtract directly from NPSHa, "
         "and losing it causes cavitation.</p>"),
    ]

    guide("pipe-sizing",
          "Pipe Sizing Guide: Velocity, Flow Rate and NPS",
          "Size pipe by target velocity, then check pressure drop. Design "
          "velocity ranges by service plus Schedule 40 flow capacity in gpm for "
          "every size to NPS 24.",
          "Pipe Sizing by Flow Rate and Velocity",
          "How to get from a flow rate to a nominal pipe size: the continuity "
          "equation, the velocity limits that apply to each service, and a full "
          "Schedule 40 capacity table.",
          body, faq_pairs=q, card_meta="Velocity limits · gpm capacity")


def guide_hydrotest(pt, b165):
    temps = pt["temperatures"]
    groups = pt["groups"]

    def shell_test(rating):
        """B16.5 shell test: 1.5 x the 100 degF rating, rounded up to 25 psi."""
        return int(math.ceil(rating * 1.5 / 25.0) * 25)

    rows = []
    for g in groups:
        cells = [f"<strong>{esc(g['name'])}</strong>"]
        for c in CLASS_ORDER:
            r = g["ratings"].get(c)
            cells.append(f"{shell_test(r[0])}" if r else '<span class="na">—</span>')
        rows.append(cells)

    rating_rows = []
    for g in groups:
        cells = [f"<strong>{esc(g['name'])}</strong>"]
        for c in CLASS_ORDER:
            r = g["ratings"].get(c)
            cells.append(f"{r[0]}" if r else '<span class="na">—</span>')
        rating_rows.append(cells)

    g11 = next(g for g in groups if g["slug"] == "1-1")
    ex_rating = g11["ratings"]["300"][0]

    body = (facts([("B16.5 shell test", "1.5 × the 100 °F rating"),
                   ("B31.3 hydrotest", "1.5 × design pressure, stress adjusted"),
                   ("Minimum hold time", "10 minutes, then examine"),
                   ("Rounding", "Up to the next 25 psi increment")])
            + verdict(
                "Two different test pressures get confused with each other. The "
                "<em>shell test</em> in ASME B16.5 is a factory proof test on "
                "the flange itself, at 1.5 times the 100 °F rating of its class "
                "and material group. The <em>hydrostatic leak test</em> in ASME "
                "B31.3 is a field test on the completed system, at 1.5 times the "
                "design pressure, adjusted upward when the test temperature is "
                "colder than the design temperature.")
            + "<h2>The B16.5 shell test</h2>"
            "<p>Every flanged fitting is proof tested at the works before it "
            "ships. B16.5 sets that pressure at 1.5 times the ambient rating "
            "for the class and material group, rounded up to the next 25 psi "
            "increment. It is a one-off structural proof, not a leak test of a "
            "gasketed joint — the flange is tested as a pressure-containing "
            "shell.</p>"
            + '<p class="formula">Shell test = 1.5 × (rating at 100 °F), '
            "rounded up to the next 25 psi</p>"
            + table(["Material group"] + [f"Class {c}" for c in CLASS_ORDER],
                    rating_rows,
                    caption="ASME B16.5 pressure ratings at 100 °F, psig — the "
                            "basis of the shell test.",
                    note="These are the ambient ratings for each material "
                         "group. See the full "
                         '<a href="/reference/pressure-temperature-ratings/">'
                         "P-T rating tables</a> for the temperature curves.")
            + table(["Material group"] + [f"Class {c}" for c in CLASS_ORDER],
                    rows,
                    caption="Computed hydrostatic shell test pressures, psig — "
                            "1.5 × the ambient rating, rounded up to 25 psi.",
                    note="Computed from the ratings in the table above. "
                         "Confirm against ASME B16.5 Table F2 before using "
                         "these for acceptance.")
            + "<h2>The B31.3 system hydrotest</h2>"
            "<p>Once the system is built, ASME B31.3 paragraph 345.4.2 requires "
            "a hydrostatic leak test at not less than 1.5 times the design "
            "pressure. Where the design temperature is above the test "
            "temperature — which it almost always is — the test pressure is "
            "raised by the ratio of allowable stresses, because the material is "
            "stronger cold than it will be hot:</p>"
            + '<p class="formula">P<sub>T</sub> = 1.5 × P × (S<sub>T</sub> / S)'
            "<br>S<sub>T</sub> = allowable stress at test temperature · "
            "S = allowable stress at design temperature</p>"
            "<p>The ratio S<sub>T</sub>/S is capped at 6.5, and the test "
            "pressure must not produce a stress above the material's yield "
            "point at test temperature — which is what actually limits it in "
            "practice on a hot line.</p>"
            "<h2>Worked example</h2>"
            "<p>A Class 300 carbon steel line, design pressure 500 psig, design "
            "temperature 650 °F, tested with water at ambient.</p>"
            "<ol>"
            "<li>Base test pressure: 1.5 × 500 = 750 psig</li>"
            "<li>Allowable stress for A106 Gr B: 20,000 psi at 100 °F and "
            "17,300 psi at 650 °F, so S<sub>T</sub>/S = 1.156</li>"
            "<li>Corrected test pressure: 750 × 1.156 = 867 psig</li>"
            f"<li>Check against the flange: a Class 300 Group 1.1 flange is "
            f"rated {ex_rating} psig at 100 °F, so 867 psig is within it and "
            "the flanges do not limit the test.</li>"
            "</ol>"
            "<p>That last check is the one that gets missed. If the corrected "
            "test pressure exceeds the ambient rating of any flange, valve or "
            "component in the test envelope, the test cannot proceed at that "
            "pressure — either the component is removed and blanked, or the "
            "test is split into sections, or a lower test pressure is agreed "
            "with the owner under the code's provisions.</p>"
            "<h2>Practical requirements</h2>"
            + table(
                ["Requirement", "ASME B31.3 provision"],
                [["<strong>Minimum hold time</strong>",
                  "10 minutes at test pressure, after which the pressure may "
                  "be reduced to the design pressure for examination."],
                 ["<strong>Test fluid</strong>",
                  "Water unless there is a risk of damage from freezing or "
                  "from adverse effects on the piping material."],
                 ["<strong>Water chemistry</strong>",
                  "Chloride content is limited for austenitic stainless — "
                  "commonly 50 ppm or less — to avoid stress corrosion "
                  "cracking. Drain and dry promptly."],
                 ["<strong>Test temperature</strong>",
                  "Kept above the material's ductile-brittle transition. "
                  "Testing carbon steel with cold water on a cold day is a "
                  "real brittle fracture risk."],
                 ["<strong>Venting</strong>",
                  "The system must be vented at high points while filling. "
                  "Trapped air stores energy and makes a failure violent."],
                 ["<strong>Pneumatic testing</strong>",
                  "Permitted only where hydrostatic testing is impracticable, "
                  "and requires a documented hazard assessment — stored energy "
                  "in a gas test is orders of magnitude higher."]],
                caption="Hydrostatic leak testing requirements, ASME B31.3.",
                note="Paraphrased for orientation. The governing code text "
                     "prevails — confirm against a current copy before "
                     "planning a test.")
            + '<div class="callout"><p>See also: '
            '<a href="/reference/pressure-temperature-ratings/">P-T rating '
            "tables</a> · "
            '<a href="/guides/pressure-temperature-derating/">how derating '
            "works</a> · "
            '<a href="/compare/weld-neck-vs-blind-flange/">blanking with blind '
            "flanges</a> · "
            '<a href="/guides/flange-bolt-torque/">bolt torque</a>.</p></div>')

    q = [
        ("What is the hydrostatic test pressure for a Class 150 flange?",
         f"<p>The B16.5 shell test for a Group 1.1 Class 150 flange is "
         f"{shell_test(g11['ratings']['150'][0])} psig — 1.5 times the "
         f"{g11['ratings']['150'][0]} psig ambient rating, rounded up to the "
         "next 25 psi. That is a works proof test, not the field system "
         "test.</p>"),
        ("How is the B31.3 hydrotest pressure calculated?",
         "<p>1.5 times the design pressure, multiplied by the ratio of "
         "allowable stress at test temperature to allowable stress at design "
         "temperature. The ratio is capped at 6.5 and the result must not "
         "yield the material.</p>"),
        ("How long must a hydrostatic test be held?",
         "<p>ASME B31.3 requires at least 10 minutes at the test pressure. The "
         "pressure may then be reduced to the design pressure while the joints "
         "are examined for leakage.</p>"),
        ("Why is chloride limited in stainless steel test water?",
         "<p>Because residual chlorides concentrate as the system dries and can "
         "initiate stress corrosion cracking in austenitic stainless. A limit "
         "around 50 ppm is common, with prompt draining and drying "
         "required.</p>"),
        ("Can I pneumatically test instead of hydrostatically?",
         "<p>Only where a hydrostatic test is impracticable — for example where "
         "the system cannot take the water weight or traces of water are "
         "unacceptable. A gas test stores vastly more energy and requires a "
         "documented hazard assessment.</p>"),
    ]

    guide("hydrostatic-test-pressure",
          "Hydrostatic Test Pressure: B16.5 and B31.3",
          "Flange shell test is 1.5x the 100 degF rating; the B31.3 system test "
          "is 1.5x design pressure stress-adjusted. Computed test pressures for "
          "every class and group.",
          "Hydrostatic Test Pressure",
          "Two different tests that get confused with each other: the factory "
          "shell test on a flange, and the field leak test on a completed "
          "system. Both worked through with tables.",
          body, faq_pairs=q, card_meta="Shell test · B31.3 · by class")


def guide_wall_thickness(pipes):
    six = next(s for s in pipes["sizes"] if s["nps"] == "6")
    P, S, E, W, Y = 900.0, 20000.0, 1.0, 1.0, 0.4
    t_barlow = P * six["od"] / (2 * S)
    t_b313 = P * six["od"] / (2 * (S * E * W + P * Y))
    ca = 0.0625
    t_req = t_b313 + ca
    t_mill = t_req / 0.875

    # Which schedules satisfy the requirement, computed from the data.
    ok = [(k, six["walls"][k]) for k in SCHEDULE_ORDER
          if k in six["walls"] and six["walls"][k] >= t_mill]
    chosen = ok[0] if ok else None

    sched_rows = []
    for k in SCHEDULE_ORDER:
        if k not in six["walls"]:
            continue
        t = six["walls"][k]
        p_allow = 2 * S * E * W * t / (six["od"] - 2 * Y * t)
        sched_rows.append([
            f'<strong><a href="/pipes/nps-6/#{sched_slug(k)}">{sched_label(k)}</a></strong>',
            dual(t, 3), dual(t * 0.875, 4),
            f"{p_allow:,.0f} psig",
            f"{2 * S * t / six['od']:,.0f} psig",
            '<span class="yes">Yes</span>' if t >= t_mill else
            '<span class="na">No</span>',
        ])

    body = (facts([("Barlow's formula", "t = P·D / 2S"),
                   ("ASME B31.3", "t = P·D / 2(SEW + PY)"),
                   ("Mill tolerance", "−12.5% on nominal wall"),
                   ("Worked example", "NPS 6 at 900 psig")])
            + verdict(
                "Barlow's formula, t = PD/2S, gives the wall thickness needed "
                "to contain pressure in a thin-walled pipe. ASME B31.3 uses a "
                "refined version that adds the joint quality factor E, the weld "
                "strength reduction factor W and the coefficient Y. Whichever "
                "you use, the pressure design thickness is only the starting "
                "point: corrosion allowance is added to it, and the sum is then "
                "divided by 0.875 to allow for the 12.5% mill tolerance before "
                "a schedule is chosen.")
            + "<h2>Barlow's formula</h2>"
            "<p>Barlow's formula comes straight from a force balance on a thin "
            "cylinder. Internal pressure acting across the diameter must be "
            "carried by hoop stress in two wall sections:</p>"
            + '<p class="formula">P · D = 2 · S · t &nbsp;⟹&nbsp; '
            "t = P·D / (2·S) &nbsp;·&nbsp; P = 2·S·t / D</p>"
            "<p>D is the <strong>outside</strong> diameter, which makes the "
            "result conservative — the true hoop stress is developed at the "
            "mean diameter. S is the allowable stress for the material at the "
            "design temperature, not the yield or tensile strength.</p>"
            + "<h2>The ASME B31.3 equation</h2>"
            "<p>B31.3 paragraph 304.1.2 refines Barlow with three factors:</p>"
            + '<p class="formula">t = P·D / [ 2 · (S·E·W + P·Y) ]</p>'
            + table(
                ["Symbol", "Meaning", "Typical value"],
                [["<strong>P</strong>", "Internal design gauge pressure",
                  "From the process design"],
                 ["<strong>D</strong>", "Pipe outside diameter",
                  "From ASME B36.10M"],
                 ["<strong>S</strong>",
                  "Allowable stress at design temperature",
                  "20,000 psi for A106 Gr B at ambient"],
                 ["<strong>E</strong>", "Quality factor — the longitudinal "
                  "weld joint factor",
                  "1.00 seamless, 0.85 ERW, 0.60 furnace butt welded"],
                 ["<strong>W</strong>", "Weld strength reduction factor",
                  "1.00 below about 950 °F"],
                 ["<strong>Y</strong>", "Coefficient from B31.3 Table 304.1.1",
                  "0.4 for ferritic steel below 900 °F"]],
                caption="Terms in the ASME B31.3 pressure design thickness "
                        "equation.",
                note="Y accounts for the difference between thin-wall and "
                     "thick-wall behaviour; the equation reduces to Barlow "
                     "when Y = 0.")
            + "<h2>Worked example: NPS 6 at 900 psig</h2>"
            f"<p>Seamless A106 Gr B pipe, outside diameter "
            f"{inch_mm(six['od'])}, design pressure 900 psig, allowable stress "
            "20,000 psi, E = 1.0, W = 1.0, Y = 0.4, corrosion allowance "
            "1/16 in.</p>"
            "<ol>"
            f"<li><strong>Barlow:</strong> t = 900 × {n(six['od'], 3)} / "
            f"(2 × 20000) = {t_barlow:.4f} in</li>"
            f"<li><strong>B31.3:</strong> t = 900 × {n(six['od'], 3)} / "
            f"[2 × (20000 × 1.0 × 1.0 + 900 × 0.4)] = {t_b313:.4f} in</li>"
            f"<li><strong>Add corrosion allowance:</strong> "
            f"{t_b313:.4f} + {ca:.4f} = {t_req:.4f} in required minimum</li>"
            f"<li><strong>Allow mill tolerance:</strong> "
            f"{t_req:.4f} / 0.875 = {t_mill:.4f} in nominal wall required</li>"
            + (f"<li><strong>Select a schedule:</strong> the thinnest NPS 6 "
               f"schedule meeting {t_mill:.4f} in is "
               f'<a href="/pipes/nps-6/#{sched_slug(chosen[0])}">'
               f"{sched_long(chosen[0])}</a> at {n(chosen[1], 3)} in.</li>"
               if chosen else
               "<li><strong>Select a schedule:</strong> no published NPS 6 "
               "schedule satisfies this, so the wall must be specified "
               "directly.</li>")
            + "</ol>"
            "<p>Note how little difference the B31.3 refinement makes here — "
            f"{t_b313:.4f} in against Barlow's {t_barlow:.4f} in. The PY term "
            "matters at high pressure and thick wall; at moderate pressure "
            "Barlow is a perfectly good first pass.</p>"
            "<h2>The 12.5% mill tolerance</h2>"
            "<p>This is the step most often skipped, and it is not optional. "
            "Pipe is permitted to be manufactured up to 12.5% thinner than its "
            "nominal wall. The calculation produces a <em>minimum</em> "
            "thickness, so the nominal wall ordered must be that minimum "
            "divided by 0.875 — about 14.3% thicker. Buying to the calculated "
            "figure means accepting pipe that can legally arrive under it.</p>"
            + '<p class="formula">t<sub>nominal</sub> = (t<sub>pressure</sub> + '
            "corrosion allowance) / 0.875</p>"
            + "<h2>Allowable pressure by schedule, NPS 6</h2>"
            "<p>The same equation rearranged: what each published NPS 6 "
            "schedule will hold at S = 20,000 psi, E = 1.0, Y = 0.4.</p>"
            + UNITS_NOTE
            + table(["Schedule", "Nominal wall", "Wall at −12.5%",
                     "Allowable P (B31.3)", "Allowable P (Barlow)",
                     f"Meets {t_mill:.3f} in?"], sched_rows,
                    caption="NPS 6 allowable pressure by schedule, "
                            "S = 20,000 psi.",
                    note="Pressure design only — no allowance for bending, "
                         "external load, thermal expansion or occasional "
                         "loads, all of which B31.3 requires to be considered "
                         "separately.")
            + "<h2>What the equation does not cover</h2>"
            "<p>Pressure design thickness is one of several checks. B31.3 also "
            "requires the wall to handle sustained loads including weight, "
            "thermal expansion stress, occasional loads such as wind and "
            "seismic, and any external pressure. On a large-diameter thin-wall "
            "line, collapse under vacuum or under burial load frequently "
            "governs over internal pressure entirely.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/schedule-chart/">schedule chart</a> · '
            '<a href="/compare/seamless-vs-welded-pipe/">joint factor E</a> · '
            '<a href="/reference/material-grades/">material grades</a> · '
            '<a href="/guides/pipe-schedule-explained/">what a schedule '
            "is</a>.</p></div>")

    q = [
        ("What is Barlow's formula?",
         "<p>t = PD / 2S, relating wall thickness to internal pressure, outside "
         "diameter and allowable stress. Rearranged as P = 2St / D it gives the "
         "pressure a known wall will hold.</p>"),
        ("Why divide by 0.875 in wall thickness calculations?",
         "<p>Because pipe may be manufactured up to 12.5% under its nominal "
         "wall. The calculation gives a required minimum, so the nominal wall "
         "ordered must be that minimum divided by 0.875.</p>"),
        ("Does Barlow's formula use the inside or outside diameter?",
         "<p>The outside diameter. Using the OD makes the result conservative, "
         "since the actual hoop stress develops at the mean diameter.</p>"),
        ("What allowable stress should I use for A106 Grade B?",
         "<p>20,000 psi up to about 400 °F, falling as temperature rises — "
         "17,300 psi at 650 °F and lower above that. The values come from ASME "
         "B31.3 Table A-1 and must be read at the design temperature.</p>"),
        ("Is the pressure design thickness the final wall?",
         "<p>No. Add corrosion allowance, any threading or grooving depth, and "
         "then allow for mill tolerance. Sustained loads, thermal expansion and "
         "external pressure must also be checked separately.</p>"),
    ]

    guide("pipe-wall-thickness-calculation",
          "Pipe Wall Thickness Calculation: Barlow & B31.3",
          "Barlow's formula t = PD/2S and the ASME B31.3 equation explained, "
          "with a full worked NPS 6 example covering corrosion allowance and "
          "12.5% mill tolerance.",
          "Pipe Wall Thickness Calculation",
          "Barlow's formula, the ASME B31.3 refinement of it, and the two steps "
          "after the equation — corrosion allowance and mill tolerance — that "
          "decide which schedule you actually buy.",
          body, faq_pairs=q, card_meta="Barlow · B31.3 · worked example")


# Bolt tensile stress area: As = pi/4 * (D - 0.9743/n)^2, where n is threads
# per inch. B16.5 flange bolting is UNC below 1 in and 8-thread series at and
# above 1 in.
def threads_per_inch(d):
    return {0.5: 13, 0.625: 11, 0.75: 10, 0.875: 9}.get(d, 8)


def stress_area(d):
    n_ = threads_per_inch(d)
    return math.pi / 4 * (d - 0.9743 / n_) ** 2


def bolt_torque(d, target_psi=50000, k=0.16):
    """T (lb-ft) = K x F x d / 12, with F = target stress x stress area."""
    return k * target_psi * stress_area(d) * d / 12.0


def guide_bolt_torque(b165, ftypes):
    # Label each diameter with the fraction B16.5 actually prints, taken from
    # the data rather than a lookup that would drift if a size were added.
    frac = {nps_value(r["bolt"]): r["bolt"]
            for blk in b165["classes"].values() for r in blk["rows"]}
    sizes = sorted(frac)
    rows = []
    for d in sizes:
        a = stress_area(d)
        rows.append([
            f'<strong>{esc(frac[d])} in</strong>',
            f"{threads_per_inch(d)} TPI",
            f"{a:.4f} in²",
            f"{a * 50000 / 1000:.1f} kip",
            f"{bolt_torque(d, 50000, 0.16):,.0f}",
            f"{bolt_torque(d, 50000, 0.20):,.0f}",
            f"{bolt_torque(d, 40000, 0.16):,.0f}",
        ])

    # A worked joint, straight out of the flange data.
    r = next(x for x in b165["classes"]["300"]["rows"] if x["nps"] == "6")
    bd = nps_value(r["bolt"])
    total = r["bolts"] * stress_area(bd) * 50000

    body = (facts([("Torque equation", "T = K · F · d"),
                   ("Nut factor K", "0.16 lubricated · 0.20 dry"),
                   ("Typical target stress", "50,000 psi for A193 B7"),
                   ("Pattern", "Cross-pattern, in at least four passes")])
            + verdict(
                "Bolt torque is not a property of the flange — it is whatever "
                "produces the bolt stress the joint needs. The working "
                "relationship is T = K·F·d, where F is the target bolt load, d "
                "is the nominal bolt diameter and K is the nut factor, around "
                "0.16 well lubricated and 0.20 dry. Lubrication is not a detail: "
                "a dry bolt reaches roughly 20% less preload at the same "
                "torque.")
            + "<h2>The torque equation</h2>"
            "<p>Torque is an indirect way of producing bolt tension, and most "
            "of it is spent overcoming friction rather than stretching the "
            "bolt. That is why the nut factor dominates the result:</p>"
            + '<p class="formula">T (lb-ft) = K · F (lbf) · d (in) / 12'
            "<br>F = target bolt stress × tensile stress area"
            "<br>A<sub>s</sub> = π/4 · (d − 0.9743/n)²</p>"
            "<p>Roughly half the applied torque is lost to friction under the "
            "nut face, and another 40% to friction in the threads. Only about "
            "10% actually goes into stretching the bolt — which is why torque "
            "control has a scatter of ±25 to ±30% on bolt load even when done "
            "carefully, and why critical joints are bolt-tensioned or "
            "ultrasonically measured instead.</p>"
            + "<h2>Torque by bolt size</h2>"
            "<p>Computed for ASTM A193 Grade B7 studs at the target stresses "
            "shown. These are the bolt diameters that appear in the ASME B16.5 "
            "flange tables.</p>"
            + table(["Bolt size", "Thread", "Stress area", "Bolt load at 50 ksi",
                     "Torque, lubricated (lb-ft)", "Torque, dry (lb-ft)",
                     "Torque at 40 ksi, lubricated (lb-ft)"], rows,
                    caption="Computed bolt torque for A193 B7 studs, "
                            "T = K·F·d.",
                    note="Lubricated assumes K = 0.16, dry K = 0.20. These are "
                         "computed reference values, not a substitute for the "
                         "gasket manufacturer's assembly procedure or a "
                         "project-specific bolt-up specification.")
            + '<div class="callout"><p>For a broader torque reference beyond '
            "flange bolting — fastener grades, metric sizes and general "
            'assembly torque — see <a href="https://torquespec.org" '
            'rel="noopener">torquespec.org</a>.</p></div>'
            + "<h2>Worked example: NPS 6 Class 300</h2>"
            f"<p>ASME B16.5 gives an NPS 6 Class 300 flange "
            f"{r['bolts']} bolts of {esc(r['bolt'])} in diameter on a "
            f"{n(r['bc'], 2)} in bolt circle.</p>"
            "<ol>"
            f"<li>Stress area per bolt: A<sub>s</sub> = "
            f"{stress_area(bd):.4f} in²</li>"
            f"<li>Target load per bolt at 50,000 psi: "
            f"{stress_area(bd) * 50000 / 1000:.1f} kip</li>"
            f"<li>Torque, lubricated: T = 0.16 × "
            f"{stress_area(bd) * 50000:,.0f} × {n(bd, 3)} / 12 = "
            f"{bolt_torque(bd):,.0f} lb-ft</li>"
            f"<li>Total bolt load on the joint: {r['bolts']} × "
            f"{stress_area(bd) * 50000 / 1000:.1f} = {total / 1000:,.0f} "
            "kip</li>"
            "</ol>"
            "<p>That total is the number worth carrying away. The joint is held "
            "together by hundreds of thousands of pounds of clamping force, and "
            "the gasket only seals while that force stays above what the "
            "internal pressure is trying to push apart.</p>"
            + "<h2>Tightening sequence</h2>"
            "<p>Sequence matters as much as the final torque. Tightening one "
            "bolt fully before its neighbours cocks the flange and crushes the "
            "gasket unevenly — the joint then leaks at low pressure no matter "
            "what the torque wrench said.</p>"
            + table(
                ["Pass", "Torque", "Pattern"],
                [["<strong>1 — snug</strong>", "20–30% of target",
                  "Cross-pattern (star), hand tight then snug"],
                 ["<strong>2</strong>", "50–70% of target", "Cross-pattern"],
                 ["<strong>3</strong>", "100% of target", "Cross-pattern"],
                 ["<strong>4 — check</strong>", "100% of target",
                  "Clockwise, bolt to bolt, until no nut turns"],
                 ["<strong>5 — retorque</strong>", "100% of target",
                  "After 4–24 hours, or after the first thermal cycle, to "
                  "recover gasket relaxation"]],
                caption="Flange bolt-up sequence, following ASME PCC-1 "
                        "practice.",
                note="ASME PCC-1 Guidelines for Pressure Boundary Bolted Flange "
                     "Joint Assembly is the reference for joint assembly and "
                     "gives legacy and alternative patterns in full.")
            + "<h2>What changes the number</h2>"
            "<p>Gasket type is the largest single influence on target stress. A "
            "spiral wound gasket needs enough load to seat the winding but not "
            "so much that it crushes past its compression stop; a kammprofile "
            "needs less; a sheet gasket less again; and an RTJ ring needs "
            "enough to yield it into the groove. Always take the seating stress "
            "from the gasket manufacturer and work back to torque from there, "
            "rather than starting from a generic bolt torque table.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/flange-bolt-chart/">flange bolt chart</a> · '
            '<a href="/compare/raised-face-vs-ring-type-joint/">RF vs RTJ</a> · '
            '<a href="/guides/flange-face-types/">choosing a flange face</a> · '
            '<a href="/flanges/">flange dimensions</a>.</p></div>')

    q = [
        ("How do I calculate flange bolt torque?",
         "<p>T = K · F · d / 12 for lb-ft, where F is the target bolt load "
         "(bolt stress × tensile stress area), d is the nominal diameter in "
         "inches, and K is the nut factor — about 0.16 lubricated and 0.20 "
         "dry.</p>"),
        ("What is the nut factor K?",
         "<p>An empirical coefficient rolling up thread and nut-face friction. "
         "0.16 is typical for a well-lubricated stud with anti-seize, 0.20 for "
         "a dry one. Because most of the torque goes into friction, K dominates "
         "the resulting bolt load.</p>"),
        ("What bolt stress should I target for A193 B7 studs?",
         "<p>50,000 psi is a common general-purpose target, with 40,000 psi "
         "used for softer gaskets and lower classes. B7 has a minimum yield of "
         "105,000 psi, so 50 ksi is under half yield.</p>"),
        ("Why does a flange joint need retorquing?",
         "<p>Gaskets relax and bolts creep slightly after initial bolt-up, and "
         "the first thermal cycle relaxes them further. A retorque after 4 to "
         "24 hours or after the first heat-up recovers the lost load.</p>"),
        ("Is torque an accurate way to set bolt load?",
         "<p>Not very. Scatter of ±25 to ±30% on the resulting bolt load is "
         "normal even with a calibrated wrench, because friction varies. "
         "Critical joints use hydraulic tensioning or ultrasonic bolt "
         "measurement instead.</p>"),
    ]

    guide("flange-bolt-torque",
          "Flange Bolt Torque: Values, Formula and Sequence",
          "Flange bolt torque from T = K·F·d, computed for every B16.5 bolt "
          "size at 40 and 50 ksi, lubricated and dry, with the PCC-1 tightening "
          "sequence.",
          "Flange Bolt Torque",
          "Where torque values come from, computed for every bolt diameter in "
          "ASME B16.5 — plus the tightening sequence that decides whether the "
          "joint actually seals.",
          body, faq_pairs=q, card_meta="T = K·F·d · all B16.5 bolt sizes")


def guide_face_types(b165):
    rf_od = b165["raised_face"]
    rows = []
    for r in b165["classes"]["150"]["rows"]:
        nps = r["nps"]
        if nps not in rf_od:
            continue
        rows.append([
            f'<strong><a href="/pipes/nps-{nps_slug(nps)}/#flanges">NPS {esc(nps)}</a></strong>',
            dual(rf_od[nps], 2), dual(r["o"], 2), dual(0.0625, 4),
            dual(0.25, 3),
        ])

    body = (facts([("Faces in B16.5", "6"),
                   ("Default", "Raised face (RF)"),
                   ("RF height, Cl 150/300", "1/16 in"),
                   ("RF height, Cl 400+", "1/4 in")])
            + verdict(
                "Pick the face from what you are bolting to and how hot the "
                "joint runs. Raised face is the default and covers most "
                "service. Flat face is mandatory against cast iron and other "
                "brittle castings — a raised face will crack them. Ring type "
                "joint takes over at high pressure and temperature, typically "
                "Class 600 and above, where a metal seal outlasts a compressed "
                "gasket.")
            + "<h2>Choosing a face</h2>"
            + table(
                ["If the joint is…", "Use", "Because"],
                [["<strong>Ordinary process or utility, Class 150–600</strong>",
                  "Raised face (RF)",
                  "The B16.5 default. Cheapest, most available, works with "
                  "sheet and spiral wound gaskets."],
                 ["<strong>Against a cast iron pump, valve or "
                  "equipment flange</strong>",
                  "Flat face (FF) with a full-face gasket",
                  "A raised face bends the brittle casting inside the bolt "
                  "circle as the bolts pull it in, and cracks it."],
                 ["<strong>Class 600 and above, or above about 800 °F</strong>",
                  "Ring type joint (RTJ)",
                  "A yielded metal ring survives thermal cycling that relaxes "
                  "a compressed gasket, and it is pressure-energised."],
                 ["<strong>Hydrocarbon service at high pressure</strong>",
                  "Ring type joint (RTJ)",
                  "The usual specification choice where a leak is a fire or "
                  "toxic release."],
                 ["<strong>Frequently dismantled, gasket must not blow "
                  "out</strong>",
                  "Tongue and groove (T&amp;G)",
                  "The gasket is trapped in the groove and cannot be "
                  "extruded or over-compressed."],
                 ["<strong>Cryogenic or very low temperature</strong>",
                  "RTJ or RF with a suitable gasket",
                  "Differential contraction relaxes bolt load; a metal seal "
                  "or a spring-energised gasket handles it better."],
                 ["<strong>Behind a stub end (lap joint)</strong>",
                  "The stub end face governs",
                  "The backing flange never contacts the gasket, so the stub "
                  "end's face is what matters."]],
                caption="Flange face selection by service.",
                note="Face type must match on both halves of a joint. RF will "
                     "not seal against RTJ, and RF against FF is the "
                     "cracked-casting case.")
            + "<h2>Raised face dimensions</h2>"
            "<p>The raised face outside diameter is the gasket contact "
            "diameter, and it is common to Classes 150 through 600 in a given "
            "size. The tabulated flange thickness in B16.5 <em>excludes</em> "
            "the raised face, so a flange always measures thicker than the "
            "table by the face height.</p>"
            + UNITS_NOTE
            + table(["NPS", "Raised face OD", "Class 150 flange OD",
                     "RF height, Cl 150/300", "RF height, Cl 400+"], rows,
                    caption="Raised face gasket contact diameters and face "
                            "heights, ASME B16.5.",
                    note="Raised face OD is common to Classes 150–600. Classes "
                         "900, 1500 and 2500 use different gasket surfaces in "
                         "several sizes — check B16.5 Table 9.")
            + "<h2>Surface finish</h2>"
            "<p>ASME B16.5 requires a serrated finish on raised and flat faces, "
            "concentric or spiral, at 125 to 500 microinches Ra. This is "
            "counter-intuitive but important: a face machined smooth seals "
            "<em>worse</em>, because the gasket has nothing to key into and can "
            "slide radially under pressure. If a face has been skimmed during "
            "repair, the serration must be restored.</p>"
            "<h2>The mistakes that cause leaks</h2>"
            "<ul>"
            "<li><strong>Mixing faces.</strong> RF against RTJ cannot seal. RF "
            "against FF cracks castings. Both are found on site more often than "
            "they should be.</li>"
            "<li><strong>Ring gasket on a flat face.</strong> A flat face needs "
            "a full-face gasket covering the bolt holes; a ring gasket lets the "
            "flange bend inside the bolt circle.</li>"
            "<li><strong>Reusing an RTJ ring.</strong> The ring seals by "
            "yielding. Once used it is deformed and will not seal again.</li>"
            "<li><strong>Ignoring the face height in spool "
            "dimensions.</strong> The 1/4 in face at Class 400 and above adds "
            "to the make-up length and to the bolt length required.</li>"
            "<li><strong>Damaged serrations.</strong> A radial scratch across "
            "the face is a leak path; circumferential marks generally are "
            "not.</li>"
            "</ul>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/flange-face-types/">all six face types in '
            "detail</a> · "
            '<a href="/compare/raised-face-vs-ring-type-joint/">RF vs RTJ '
            "compared</a> · "
            '<a href="/guides/flange-bolt-torque/">bolt torque</a> · '
            '<a href="/flanges/">flange dimensions</a>.</p></div>')

    q = [
        ("Which flange face should I use as a default?",
         "<p>Raised face. It is the ASME B16.5 default, the most widely "
         "available, and suitable for most service through Class 600.</p>"),
        ("Why must a flat face be used against cast iron?",
         "<p>Because a raised face concentrates load inside the bolt circle and "
         "bends the mating flange. Cast iron is brittle and cracks rather than "
         "yielding, so a steel flange bolting to a cast iron one should be flat "
         "faced with a full-face gasket.</p>"),
        ("Can I bolt an RF flange to an RTJ flange?",
         "<p>No. The RTJ flange has a machined groove where the RF gasket would "
         "need a flat surface, so there is nothing to seal against. Face types "
         "must match.</p>"),
        ("How tall is a raised face?",
         "<p>1/16 in (1.6 mm) for Classes 150 and 300, and 1/4 in (6.4 mm) for "
         "Class 400 and above. The B16.5 thickness tables exclude it.</p>"),
        ("Should flange faces be machined smooth?",
         "<p>No. B16.5 specifies a serrated finish of 125 to 500 microinches "
         "Ra. A smooth face gives the gasket nothing to grip and seals worse "
         "than a properly serrated one.</p>"),
    ]

    guide("flange-face-types",
          "Flange Face Types: RF, FF and RTJ Selection",
          "Choose a flange face by service: RF as the default, FF against cast "
          "iron, RTJ above Class 600. Selection table, dimensions and the "
          "mistakes that cause leaks.",
          "Choosing a Flange Face Type",
          "Raised face, flat face and ring type joint — which to specify for a "
          "given service, and the face-mixing mistakes that show up as leaks on "
          "commissioning.",
          body, faq_pairs=q, card_meta="RF · FF · RTJ selection")

def guide_material_selection(mats, pt):
    body = (facts([("A106", "Seamless carbon, high temperature"),
                   ("A53", "General service carbon, welded or seamless"),
                   ("A312", "Austenitic stainless"),
                   ("A335", "Chrome-moly alloy, creep range")])
            + verdict(
                "Four specifications cover most steel process piping. A53 is "
                "general service carbon steel for utilities. A106 is seamless "
                "carbon steel for process and high temperature — the default "
                "for hydrocarbons and steam. A312 is austenitic stainless for "
                "corrosion resistance and for temperatures above what carbon "
                "steel tolerates. A335 is chrome-moly alloy for the creep "
                "range, roughly 800 °F and up.")
            + "<h2>The decision in order</h2>"
            "<p>Material selection runs corrosion first, then temperature, then "
            "pressure, then cost — in that order, because the first three are "
            "constraints and only the last is a preference.</p>"
            + table(
                ["Question", "If yes", "Leads to"],
                [["<strong>Is the fluid corrosive to carbon steel?</strong>",
                  "Chlorides, acids, oxygenated water, sour service",
                  "Stainless (A312), duplex (A790), or lined/clad carbon steel"],
                 ["<strong>Is the design temperature above 800 °F?</strong>",
                  "Carbon steel oxidises and can graphitise",
                  "Chrome-moly (A335 P11/P22/P91) or stainless (A312)"],
                 ["<strong>Is the design temperature below −20 °F?</strong>",
                  "Ordinary carbon steel is brittle",
                  "Impact-tested carbon (A333 Gr 6) or austenitic stainless"],
                 ["<strong>Is the service hydrocarbon, steam or "
                  "process?</strong>",
                  "Needs a seamless, tested, high-temperature grade",
                  "A106 Gr B"],
                 ["<strong>Is it water, air, or a fire main?</strong>",
                  "General service is sufficient",
                  "A53 Gr B, galvanised where appropriate"],
                 ["<strong>Is product purity critical?</strong>",
                  "Contamination from iron oxide is unacceptable",
                  "A312 stainless, often 316L with a specified finish"]],
                caption="Material selection decision sequence.",
                note="Constraints first. Cost only decides between options that "
                     "have already satisfied corrosion, temperature and "
                     "pressure.")
            + "<h2>The four specifications</h2>"
            + table(
                ["Specification", "Full scope", "Manufacture", "Typical use"],
                [["<strong>ASTM A53</strong>",
                  "Pipe, black and hot-dipped zinc-coated, welded and seamless",
                  "Type S seamless, Type E ERW, Type F furnace butt welded",
                  "Water, air, low-pressure steam, fire protection, structural "
                  "and sleeves"],
                 ["<strong>ASTM A106</strong>",
                  "Seamless carbon steel pipe for high-temperature service",
                  "Seamless only",
                  "Process piping, steam, refinery and power generation — the "
                  "carbon steel default in process plant"],
                 ["<strong>ASTM A312</strong>",
                  "Seamless, welded and heavily cold worked austenitic "
                  "stainless pipe",
                  "Seamless and welded grades, each separately designated",
                  "Corrosive service, high purity, cryogenic, and sustained "
                  "high temperature"],
                 ["<strong>ASTM A335</strong>",
                  "Seamless ferritic alloy steel pipe for high-temperature "
                  "service",
                  "Seamless only",
                  "Creep range service: heaters, high-energy steam, "
                  "hydroprocessing"]],
                caption="The four principal pipe specifications compared.")
            + "<h2>Grades and strengths</h2>"
            + grade_table(mats,
                          ["ASTM A53 Gr B", "ASTM A106 Gr B", "ASTM A106 Gr C",
                           "ASTM A333 Gr 6", "ASTM A335 P11", "ASTM A335 P22",
                           "ASTM A335 P91", "ASTM A312 TP304",
                           "ASTM A312 TP316L", "ASTM A790 S31803"],
                          "Principal pipe grades with specified minimum "
                          "strengths and temperature ranges.")
            + "<h2>Temperature is the hardest constraint</h2>"
            "<p>Pressure can be answered with more wall. Temperature usually "
            "cannot, because the limits are metallurgical rather than "
            "structural.</p>"
            + table(
                ["Temperature range", "Material", "Governing concern"],
                [["<strong>Below −50 °F</strong>", "A312 austenitic stainless",
                  "Austenitic stainless stays tough to −425 °F; ferritic steels "
                  "do not."],
                 ["<strong>−50 to −20 °F</strong>", "A333 Gr 6",
                  "Impact-tested carbon steel with a guaranteed Charpy value."],
                 ["<strong>−20 to 750 °F</strong>", "A53 or A106",
                  "Ordinary carbon steel range. A53 stops around 750 °F, A106 "
                  "around 800 °F."],
                 ["<strong>750 to 1000 °F</strong>", "A335 P11 or P22",
                  "Carbon steel graphitises and oxidises; chrome-moly resists "
                  "both and has creep strength."],
                 ["<strong>1000 to 1200 °F</strong>", "A335 P91, A312 TP304H/347H",
                  "Creep governs entirely. P91 needs strict PWHT control."],
                 ["<strong>Above 1200 °F</strong>",
                  "Austenitic stainless and nickel alloys",
                  "Beyond the ferritic alloys; oxidation and creep both "
                  "severe."]],
                caption="Material selection by design temperature.",
                note="Ranges are indicative. The design code's allowable stress "
                     "tables and the owner's specification govern.")
            + "<h2>Corrosion allowance</h2>"
            "<p>Carbon steel in a corrosive service is normally given a "
            "corrosion allowance — typically 1/16 in (1.6 mm), sometimes 1/8 in "
            "for aggressive duty — added to the pressure design thickness. "
            "Stainless in clean service is usually given none, which is the "
            "reason thin-wall S-schedule stainless is viable while thin-wall "
            "carbon steel generally is not.</p>"
            "<p>Watch the economics here. A corrosion allowance that pushes "
            "carbon steel up two schedules can cost more than specifying "
            "stainless with no allowance at all, particularly in small "
            "bore.</p>"
            "<h2>Things that catch people out</h2>"
            "<ul>"
            "<li><strong>Galvanised pipe cannot be welded</strong> without "
            "removing the zinc first — the fumes are toxic and the weld is "
            "poor. Galvanised systems are threaded or flanged.</li>"
            "<li><strong>Stainless expands 40–50% more than carbon "
            "steel.</strong> Substituting materials invalidates the flexibility "
            "analysis.</li>"
            "<li><strong>Austenitic stainless sensitises</strong> if held "
            "between 800 and 1500 °F, which welding does. Use L grades for "
            "welded assemblies.</li>"
            "<li><strong>P91 is unforgiving.</strong> Its properties depend "
            "entirely on correct post-weld heat treatment; a mis-heat-treated "
            "P91 weld is weaker than the carbon steel it replaced.</li>"
            "<li><strong>Dissimilar welds are galvanic couples.</strong> "
            "Stainless welded to carbon steel in a wet service will corrode the "
            "carbon steel preferentially.</li>"
            "</ul>"
            + '<div class="callout"><p>See also: '
            '<a href="/compare/a106-vs-a53-pipe/">A106 vs A53</a> · '
            '<a href="/compare/carbon-steel-vs-stainless-steel-pipe/">carbon '
            "vs stainless</a> · "
            '<a href="/compare/304-vs-316-stainless-steel-pipe/">304 vs 316</a> · '
            '<a href="/reference/material-grades/">full material grade '
            "tables</a>.</p></div>")

    q = [
        ("When should I use A106 instead of A53?",
         "<p>For process piping, steam and anything above roughly 400 °F. A106 "
         "is seamless, has controlled chemistry for elevated temperature, and "
         "is mechanically tested. A53 is a general service specification.</p>"),
        ("What pipe material is used above 800 °F?",
         "<p>Chrome-moly alloy to ASTM A335 — P11 or P22 through about 1100 °F "
         "and P91 to 1200 °F — or austenitic stainless to A312. Carbon steel is "
         "limited by oxidation and graphitisation rather than by its pressure "
         "rating.</p>"),
        ("What is the standard low-temperature pipe material?",
         "<p>ASTM A333 Grade 6 for carbon steel down to −50 °F, and austenitic "
         "stainless below that — 304 and 316 stay tough to −425 °F.</p>"),
        ("How much corrosion allowance should carbon steel pipe have?",
         "<p>1/16 in (1.6 mm) is the usual default, with 1/8 in for aggressive "
         "service. It is added to the pressure design thickness before mill "
         "tolerance is applied.</p>"),
        ("Can I substitute stainless for carbon steel pipe?",
         "<p>Not without rechecking the design. Stainless has a lower allowable "
         "stress at ambient, expands 40–50% more, and changes the flexibility "
         "analysis and support loads.</p>"),
    ]

    guide("pipe-material-selection",
          "Pipe Material Selection: A53, A106, A312, A335",
          "Choose pipe material by corrosion, then temperature, then pressure. "
          "Decision tables for A53, A106, A312 and A335 with grade strengths "
          "and temperature limits.",
          "Pipe Material Selection",
          "Corrosion first, temperature second, pressure third, cost last — "
          "working through the four specifications that cover most steel "
          "process piping.",
          body, faq_pairs=q, card_meta="A53 · A106 · A312 · A335")


def guide_pt_derating(pt):
    temps = pt["temperatures"]
    g11 = next(g for g in pt["groups"] if g["slug"] == "1-1")
    g21 = next(g for g in pt["groups"] if g["slug"] == "2-1")

    # Interpolation example, computed from the data so it cannot go stale.
    i400, i500 = temps.index(400), temps.index(500)
    p400, p500 = g11["ratings"]["300"][i400], g11["ratings"]["300"][i500]
    t_ex = 450
    p_ex = p400 + (p500 - p400) * (t_ex - 400) / (500 - 400)

    rows = [[f"<strong>{t} °F</strong>"]
            + [f"{g11['ratings'][c][i]}" for c in CLASS_ORDER]
            for i, t in enumerate(temps)]
    loss = [[f"<strong>{t} °F</strong>",
             f"{g11['ratings']['150'][i]} psig",
             pct(g11["ratings"]["150"][i], g11["ratings"]["150"][0]),
             f"{g21['ratings']['150'][i]} psig",
             pct(g21["ratings"]["150"][i], g21["ratings"]["150"][0])]
            for i, t in enumerate(temps)]

    body = (facts([("Ratings are", "Maximum allowable working gauge pressure"),
                   ("Basis", "Metal temperature, not fluid temperature"),
                   ("Interpolation", "Permitted between listed temperatures"),
                   ("Extrapolation", "Not permitted")])
            + verdict(
                "A pressure class is not a pressure. It selects a curve, and "
                "the pressure a component may hold is read off that curve at "
                "the metal temperature, for the material group the component is "
                "made from. Ratings fall as temperature rises because the "
                "material's allowable stress falls. Linear interpolation "
                "between listed temperatures is permitted; extrapolation beyond "
                "the table is not.")
            + "<h2>Reading a rating in four steps</h2>"
            "<ol>"
            "<li><strong>Find the material group.</strong> A105 carbon steel is "
            "Group 1.1; F304 stainless is Group 2.1 and F316 is Group 2.2; "
            "the L grades are "
            "Group 2.3. The group, not the class, determines the shape of the "
            "curve.</li>"
            "<li><strong>Use the metal temperature.</strong> B16.5 rates on the "
            "temperature of the flange metal. For uninsulated flanges that is "
            "usually taken as the fluid temperature; where they differ and it "
            "can be justified, the actual metal temperature may be used.</li>"
            "<li><strong>Read across to the class.</strong> That gives the "
            "maximum allowable working gauge pressure.</li>"
            "<li><strong>Interpolate if needed.</strong> Linear interpolation "
            "between two listed temperatures is permitted.</li>"
            "</ol>"
            "<h2>Worked example: interpolation</h2>"
            f"<p>A Class 300 A105 flange operating at {t_ex} °F. The table lists "
            f"{p400} psig at 400 °F and {p500} psig at 500 °F.</p>"
            + f'<p class="formula">P = {p400} + ({p500} − {p400}) × '
            f"({t_ex} − 400) / (500 − 400) = {p_ex:.0f} psig</p>"
            f"<p>So the flange is rated {p_ex:.0f} psig at {t_ex} °F. Note that "
            "this is a linear interpolation between two tabulated points, not a "
            "formula — B16.5 permits it precisely because the underlying "
            "allowable stress curve is close to linear over a 100 °F "
            "interval.</p>"
            + "<h2>Group 1.1 carbon steel, every class</h2>"
            + table(["Temperature"] + [f"Class {c}" for c in CLASS_ORDER], rows,
                    caption="ASME B16.5 pressure-temperature ratings for "
                            "material Group 1.1, psig.",
                    note="Group 1.1 covers A105, A216 WCB, A515/A516 Gr 70 and "
                         "A350 LF2 as forged. See the "
                         '<a href="/reference/pressure-temperature-ratings/">'
                         "full rating tables</a> for the other groups.")
            + "<h2>How much rating is lost, and where</h2>"
            "<p>The two material families behave completely differently. Carbon "
            "steel starts higher and falls away steadily. Austenitic stainless "
            "starts lower and then flattens — it holds most of its rating right "
            "through to 1000 °F.</p>"
            + table(["Temperature", "Class 150 carbon", "Retained",
                     "Class 150 stainless", "Retained"], loss,
                    caption="Rating retained against the ambient value, Class "
                            "150.",
                    note="Percentages are relative to each material's own "
                         "100 °F rating, so they show shape rather than "
                         "absolute capacity.")
            + "<h2>Where derating catches people out</h2>"
            "<ul>"
            "<li><strong>Steam service in Class 150.</strong> A Class 150 "
            "carbon steel flange holds 285 psig cold but only 140 psig at "
            "600 °F. Saturated steam at 150 psig is around 366 °F, which is "
            "within rating — but a small superheat or an upset can put the "
            "joint outside it.</li>"
            "<li><strong>Steam-out and regeneration.</strong> Lines are often "
            "steamed out at temperatures far above their normal operating "
            "point. The rating at the steam-out temperature governs, not the "
            "rating at the process temperature.</li>"
            "<li><strong>Mixed material groups in one joint.</strong> A "
            "stainless flange bolted to a carbon steel flange is limited by "
            "whichever has the lower rating at temperature — and which one that "
            "is changes with temperature.</li>"
            "<li><strong>Valves and fittings are not all B16.5.</strong> A "
            "B16.34 valve, a B16.47 large flange and a B16.5 flange in the same "
            "line have different rating tables. The lowest-rated component "
            "governs the line.</li>"
            "<li><strong>Bolting material matters.</strong> The tabulated "
            "ratings assume bolting of at least the strength B16.5 specifies. "
            "Lower-strength bolting derates the joint further.</li>"
            "</ul>"
            "<h2>The −20 °F to 100 °F column</h2>"
            "<p>The first column covers everything from −20 °F up to 100 °F "
            "with a single value. Below −20 °F, B16.5 does not extend the "
            "rating — the limit becomes the material's toughness, and impact "
            "testing under the applicable code decides whether the material may "
            "be used at all. That is a material qualification question, not a "
            "pressure rating one.</p>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/pressure-temperature-ratings/">full P-T '
            "rating tables</a> · "
            '<a href="/compare/class-150-vs-class-300/">Class 150 vs 300</a> · '
            '<a href="/compare/carbon-steel-vs-stainless-steel-pipe/">carbon '
            "vs stainless</a> · "
            '<a href="/guides/hydrostatic-test-pressure/">test '
            "pressure</a>.</p></div>")

    q = [
        ("Is a Class 150 flange rated for 150 psi?",
         "<p>No. A Class 150 carbon steel flange is rated 285 psig at 100 °F "
         "and drops below 150 psig only above about 550 °F. The class number is "
         "a series label, not a working pressure.</p>"),
        ("Can I interpolate between temperatures in the P-T tables?",
         "<p>Yes. ASME B16.5 permits linear interpolation between listed "
         "temperatures. Extrapolation beyond the ends of the table is not "
         "permitted.</p>"),
        ("Should I use the fluid temperature or the metal temperature?",
         "<p>The metal temperature. For uninsulated flanges it is normally "
         "taken as equal to the fluid temperature; a lower metal temperature "
         "may be used where it can be justified.</p>"),
        ("Why does stainless hold its rating better at high temperature?",
         "<p>Because austenitic stainless retains allowable stress far better "
         "above about 600 °F. Carbon steel loses strength steadily and is "
         "additionally limited by oxidation and graphitisation.</p>"),
        ("What limits the rating below −20 °F?",
         "<p>Material toughness, not pressure. B16.5 does not rate below "
         "−20 °F; use below that requires impact testing under the applicable "
         "code.</p>"),
    ]

    guide("pressure-temperature-derating",
          "Pressure-Temperature Derating Explained | B16.5",
          "A flange class selects a rating curve, not a pressure. How to read "
          "ASME B16.5 P-T tables, interpolate between temperatures, and avoid "
          "the common derating traps.",
          "Pressure-Temperature Derating",
          "Why a Class 150 flange is not a 150 psi flange, how to read the "
          "rating out of B16.5 at your actual temperature, and where the "
          "derating catches designs out.",
          body, faq_pairs=q, card_meta="Reading P-T tables · interpolation")


def guide_schedule_explained(pipes):
    four = next(s for s in pipes["sizes"] if s["nps"] == "4")
    # Schedule 40 wall by size, so the "same schedule, different wall" point is
    # made with the published numbers rather than remembered ones.
    sm = {s["nps"]: s["walls"]["40"] for s in pipes["sizes"]
          if "40" in s["walls"]}
    rows = []
    for k in SCHEDULE_ORDER:
        if k not in four["walls"]:
            continue
        t = four["walls"][k]
        id_ = inside_dia(four["od"], t)
        rows.append([
            f'<strong><a href="/pipes/nps-4/#{sched_slug(k)}">{sched_label(k)}</a></strong>',
            dual(four["od"], 3), dual(t, 3), dual(id_, 3),
            dual_w(weight_lbft(four["od"], t)),
            f"{area_sqin(id_):.2f} in²",
            f"{2 * 20000 * t / four['od']:,.0f} psig",
        ])

    body = (facts([("Schedule is", "A wall thickness series"),
                   ("Outside diameter", "Fixed by NPS, never by schedule"),
                   ("Schedules in B36.10M", "14"),
                   ("Higher schedule", "Thicker wall, smaller bore")])
            + verdict(
                "A pipe schedule is a wall thickness designation. Within one "
                "nominal pipe size the outside diameter is fixed and the "
                "schedule sets how far the wall extends inward — so a higher "
                "schedule means a thicker wall, a smaller bore, more weight and "
                "a higher pressure rating, with the outside of the pipe "
                "unchanged. That fixed outside diameter is what lets one set of "
                "fittings, flanges and supports serve every schedule in a "
                "size.")
            + "<h2>Where the number comes from</h2>"
            "<p>The schedule number originated as an approximation of "
            "1000 × P/S — a thousand times the working pressure divided by the "
            "allowable stress. Schedule 40 corresponded roughly to a pipe whose "
            "pressure-to-stress ratio was 0.04.</p>"
            + '<p class="formula">Schedule ≈ 1000 × P / S &nbsp;(historical '
            "origin, not a design method)</p>"
            "<p>That relationship is history, not a calculation you should use. "
            "The published walls have been rounded to manufacturing "
            "convenience for a century, and modern design derives the required "
            "wall from the "
            '<a href="/guides/pipe-wall-thickness-calculation/">B31.3 '
            "equation</a> and then picks the schedule that satisfies it. The "
            "number is a label for a wall, nothing more.</p>"
            "<h2>One size, every schedule</h2>"
            f"<p>NPS 4 pipe is {inch_mm(four['od'])} on the outside in every "
            "schedule. Here is what changes and what does not:</p>"
            + UNITS_NOTE
            + table(["Schedule", "Outside diameter", "Wall", "Bore", "Weight",
                     "Flow area", "Barlow P at S = 20 ksi"], rows,
                    caption="Every published NPS 4 schedule, ASME B36.10M.",
                    note="The outside diameter column is constant by design. "
                         "Allowable pressure is Barlow's 2St/D at 20,000 psi "
                         "allowable stress, for illustration only.")
            + "<h2>The three schedule families</h2>"
            + table(
                ["Family", "Members", "Where you meet them"],
                [["<strong>Numbered schedules</strong>",
                  "5, 10, 20, 30, 40, 60, 80, 100, 120, 140, 160",
                  "ASME B36.10M. The main series for carbon and alloy steel "
                  "pipe."],
                 ["<strong>Named walls</strong>", "STD, XS, XXS",
                  "Pre-schedule designations still in wide use. STD tracks "
                  "Sch 40 and XS tracks Sch 80 in small bore, then diverges."],
                 ["<strong>S-schedules</strong>", "5S, 10S, 40S, 80S",
                  "ASME B36.19M, for stainless. Thinner in some sizes, and "
                  "carrying stainless material and tolerance requirements."]],
                caption="The three pipe wall designation families.",
                note="See the "
                     '<a href="/compare/std-vs-xs/">STD vs XS comparison</a> '
                     "for exactly where the named walls stop tracking the "
                     "numbered ones.")
            + "<h2>Schedule does not mean the same thing in every size</h2>"
            "<p>This is the part that trips people up. Schedule 40 in NPS 1 is "
            f"a {n(sm['1'], 3)} in wall; Schedule 40 in NPS 24 is "
            f"{n(sm['24'], 3)} in. The schedule "
            "number describes a <em>series</em>, not a thickness, and the "
            "thickness for a given schedule rises with diameter. Two pipes both "
            "labelled Schedule 40 can have walls differing by a factor of five, "
            "so a wall thickness is never adequately specified by a schedule "
            "number alone — the NPS has to come with it.</p>"
            "<h2>What schedule does not tell you</h2>"
            "<ul>"
            "<li><strong>Material.</strong> Schedule 40 says nothing about "
            "whether the pipe is A53, A106 or A312.</li>"
            "<li><strong>Manufacturing method.</strong> Seamless and welded "
            "pipe share the same schedules but carry different joint quality "
            "factors.</li>"
            "<li><strong>Pressure rating.</strong> The rating depends on "
            "material, temperature and joint factor as well as wall.</li>"
            "<li><strong>Actual delivered wall.</strong> Mill tolerance permits "
            "up to 12.5% under nominal.</li>"
            "</ul>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/schedule-chart/">the full schedule chart</a> · '
            '<a href="/compare/schedule-40-vs-schedule-80/">Sch 40 vs '
            "Sch 80</a> · "
            '<a href="/guides/nps-vs-dn-explained/">NPS vs DN</a> · '
            '<a href="/pipes/">all pipe sizes</a>.</p></div>')

    q = [
        ("What does pipe schedule mean?",
         "<p>It is a wall thickness designation. Within a nominal pipe size the "
         "outside diameter is fixed, so a higher schedule number means a "
         "thicker wall and a smaller bore.</p>"),
        ("Does a higher schedule mean a bigger pipe?",
         "<p>No — the outside diameter is unchanged. A higher schedule means a "
         "thicker wall, so the pipe is heavier and the bore is smaller, but it "
         "fits the same fittings and supports.</p>"),
        ("Is Schedule 40 the same wall thickness in every size?",
         f"<p>No. Schedule 40 is {n(sm['1'], 3)} in in NPS 1 and "
         f"{n(sm['24'], 3)} in in NPS 24. The schedule identifies a series, and "
         "the wall for that series rises with diameter.</p>"),
        ("How does schedule relate to pressure rating?",
         "<p>Only through wall thickness. Pressure capacity follows roughly "
         "2St/D, so a thicker wall holds more — but the rating also depends on "
         "material, temperature and the joint quality factor.</p>"),
        ("What is the difference between Schedule 40 and 40S?",
         "<p>40S is the ASME B36.19M stainless designation. In most sizes the "
         "wall matches Schedule 40, but B36.19M publishes 40S only through NPS "
         "12 and adds stainless material and tolerance requirements.</p>"),
    ]

    guide("pipe-schedule-explained",
          "Pipe Schedule Explained: What the Number Means",
          "Pipe schedule is a wall thickness series, not a size. Why the "
          "outside diameter never changes, where the number came from, and how "
          "it relates to pressure.",
          "What a Pipe Schedule Actually Is",
          "Schedule sets the wall, never the outside diameter. Where the number "
          "came from, why Schedule 40 is a different thickness in every size, "
          "and what it does not tell you.",
          body, faq_pairs=q, card_meta="Wall series · fixed OD")


def guide_nps_dn(pipes):
    rows = []
    for s in pipes["sizes"]:
        if s["val"] > 24:
            continue
        rows.append([
            f'<strong><a href="/pipes/nps-{s["slug"]}/">NPS {esc(s["nps"])}</a></strong>',
            f'DN {s["dn"]}', dual(s["od"], 3),
            f'{n(s["dn"] * 1.0, 0)} mm',
            f'{n(s["od"] * MM - s["dn"], 1)} mm',
            '<span class="yes">Yes</span>' if abs(s["val"] - s["od"]) < 1e-9
            else '<span class="na">No</span>',
        ])
    exact = [s["nps"] for s in pipes["sizes"] if abs(s["val"] - s["od"]) < 1e-9]

    body = (facts([("NPS", "Nominal Pipe Size — dimensionless"),
                   ("DN", "Diamètre Nominal — dimensionless"),
                   ("Conversion", "Table lookup, never arithmetic"),
                   ("NPS = OD from", f"NPS {exact[0] if exact else '14'} upward")])
            + verdict(
                "NPS and DN are both <em>designators</em> — labels for a size, "
                "not measurements of anything. DN 50 pipe is not 50 mm in any "
                "dimension: it is NPS 2, whose outside diameter is 2.375 in or "
                "60.3 mm. The two series were aligned by agreement rather than "
                "by arithmetic, so converting between them is always a table "
                "lookup and never a calculation.")
            + "<h2>Neither number measures the pipe</h2>"
            "<p>Below NPS 14, the NPS number matches neither the outside "
            "diameter nor the bore. It is inherited from nineteenth-century "
            "wrought iron pipe, where the number described the approximate "
            "inside diameter of the thick-walled pipe of the day. Walls got "
            "thinner, bores got larger, and the labels stayed where they "
            "were.</p>"
            f"<p>From NPS {exact[0] if exact else '14'} upward the designator "
            "finally tells the truth: NPS equals the outside diameter in inches "
            "exactly. NPS 14 pipe is 14.000 in outside, NPS 24 is 24.000 in. "
            "The last column of the table below marks where that "
            "changeover happens.</p>"
            + '<p class="formula">DN ≠ NPS × 25.4 &nbsp;·&nbsp; '
            "DN 50 = NPS 2 = 60.3 mm OD</p>"
            + UNITS_NOTE
            + table(["Nominal pipe size", "Metric designator",
                     "True outside diameter", "DN as a number",
                     "OD minus DN", "NPS = OD?"], rows,
                    caption="NPS and DN designators against the true outside "
                            "diameter, ASME B36.10M.",
                    note="The “OD minus DN” column shows how far the metric "
                         "designator sits from the real dimension — from a few "
                         "millimetres in small bore to tens of millimetres in "
                         "large.")
            + "<h2>Why DN is not NPS × 25.4</h2>"
            "<p>Because DN was chosen to be a tidy round-number series that "
            "maps one-to-one onto the existing inch sizes, not to be a "
            "conversion of them. NPS 2 is 2.375 in, which is 60.3 mm — and its "
            "designator is DN 50. The 50 is a label picked for "
            "convenience.</p>"
            "<p>The series is also not evenly spaced. It runs 15, 20, 25, 32, "
            "40, 50, 65, 80, 100 — matching the irregular inch progression "
            "beneath it. Any attempt to compute one from the other breaks "
            "immediately.</p>"
            + "<h2>Other terms you will meet</h2>"
            + table(
                ["Term", "Means", "Note"],
                [["<strong>NPS</strong>", "Nominal Pipe Size",
                  "The North American designator. Dimensionless."],
                 ["<strong>DN</strong>", "Diamètre Nominal / Diameter Nominal",
                  "The ISO metric designator. Also dimensionless."],
                 ["<strong>NB</strong>", "Nominal Bore",
                  "Used interchangeably with NPS in British and Commonwealth "
                  "practice. Same numbers as NPS."],
                 ["<strong>OD</strong>", "Outside diameter",
                  "A real measurement — the only one on this list."],
                 ["<strong>ID</strong>", "Inside diameter / bore",
                  "A real measurement, but it changes with schedule."],
                 ["<strong>NPT</strong>", "National Pipe Taper thread",
                  "A thread standard, not a size designator. Frequently "
                  "confused with NPS."]],
                caption="Pipe size terminology.",
                note="NPS, DN and NB are labels. OD and ID are dimensions.")
            + "<h2>Where this actually bites</h2>"
            "<ul>"
            "<li><strong>Ordering from a metric supplier.</strong> Asking for "
            "50 mm pipe may get DN 50 (NPS 2, 60.3 mm OD) or genuine 50 mm OD "
            "tube. They are different products and do not share "
            "fittings.</li>"
            "<li><strong>Pipe versus tube.</strong> Tube is specified by actual "
            "outside diameter and wall. Pipe is specified by NPS and schedule. "
            "A 2 in tube and NPS 2 pipe are not the same size.</li>"
            "<li><strong>Drawing take-offs.</strong> Using DN as a millimetre "
            "dimension in a clash check or an insulation calculation "
            "understates every diameter on the drawing.</li>"
            "<li><strong>Hydraulic calculations.</strong> Neither designator is "
            "the bore. The bore depends on schedule and must be read from the "
            "dimension table.</li>"
            "</ul>"
            + '<div class="callout"><p>See also: '
            '<a href="/reference/nps-dn-conversion/">the full NPS to DN '
            "conversion chart</a> · "
            '<a href="/guides/pipe-schedule-explained/">what a schedule '
            "is</a> · "
            '<a href="/pipes/">all pipe sizes</a> · '
            '<a href="/guides/pipe-sizing/">pipe sizing</a>.</p></div>')

    q = [
        ("Is DN the pipe diameter in millimetres?",
         "<p>No. DN is a dimensionless designator. DN 50 pipe has an outside "
         "diameter of 60.3 mm, not 50 mm. Treating DN as a measurement is one "
         "of the most common sizing errors.</p>"),
        ("How do I convert NPS to DN?",
         "<p>By table lookup. There is no formula — the two series were aligned "
         "by agreement. NPS 2 is DN 50, NPS 4 is DN 100, NPS 10 is DN 250.</p>"),
        ("Is NB the same as NPS?",
         "<p>Effectively yes. Nominal Bore is the British and Commonwealth term "
         "for the same designator and uses the same numbers.</p>"),
        ("When does NPS equal the actual outside diameter?",
         f"<p>From NPS {exact[0] if exact else '14'} upward. NPS 14 pipe is "
         "exactly 14.000 in outside. Below that the designator matches neither "
         "the outside diameter nor the bore.</p>"),
        ("What is the difference between pipe and tube sizing?",
         "<p>Pipe is specified by NPS and schedule, where NPS is a label. Tube "
         "is specified by its actual outside diameter and wall thickness. A "
         "2 in tube is 2.000 in across; NPS 2 pipe is 2.375 in.</p>"),
    ]

    guide("nps-vs-dn-explained",
          "NPS vs DN: Imperial and Metric Pipe Designators",
          "NPS and DN are labels, not measurements — DN 50 pipe is 60.3 mm "
          "across, not 50 mm. Why conversion is always a lookup, with the full "
          "designator table.",
          "NPS vs DN Explained",
          "Two designation systems, neither of which measures the pipe. Why DN "
          "is not NPS × 25.4, and where treating either one as a dimension goes "
          "wrong.",
          body, faq_pairs=q, card_meta="Designators · not measurements")


def guide_end_connections(pipes):
    body = (facts([("Bevelled end", "37.5° ± 2.5°, for butt welding"),
                   ("Plain end", "Square cut, for socket or slip-on"),
                   ("Threaded end", "NPT taper, ASME B1.20.1"),
                   ("Root face", "1/16 in ± 1/32 in typical")])
            + verdict(
                "Pipe ships with one of three end preparations. A "
                "<strong>bevelled end</strong> is machined to 37.5° for butt "
                "welding and is the default for process piping. A "
                "<strong>plain end</strong> is cut square, for socket welding, "
                "slip-on flanges and grooved couplings. A "
                "<strong>threaded end</strong> carries an NPT taper thread for "
                "screwed joints. The end preparation has to be stated on the "
                "purchase order — it is not implied by the schedule.")
            + "<h2>The three preparations</h2>"
            + table(
                ["End type", "Preparation", "Joined by", "Typical use"],
                [["<strong>Bevelled (BE)</strong>",
                  "37.5° ± 2.5° bevel with a 1/16 in root face",
                  "Butt weld, usually with a root gap of about 1/16 in",
                  "Process piping, all sizes. The default above NPS 2."],
                 ["<strong>Plain (PE)</strong>", "Cut square, deburred",
                  "Socket weld, slip-on flange, or grooved coupling",
                  "Small bore socket weld systems and slip-on flanged work."],
                 ["<strong>Threaded (TE / TOE)</strong>",
                  "NPT taper thread to ASME B1.20.1",
                  "Screwed joint, usually with PTFE tape or pipe dope",
                  "Utility piping, hazardous areas, instrument connections."],
                 ["<strong>Grooved</strong>",
                  "Rolled or cut groove near the end",
                  "Mechanical coupling clamped over the groove",
                  "Fire protection, HVAC, and thin-wall systems."],
                 ["<strong>Belled / spigot</strong>",
                  "One end expanded to receive the next",
                  "Solvent weld, gasket, or caulked joint",
                  "Cast iron and plastic pipe; not standard for steel."]],
                caption="Pipe end preparations and how each is joined.")
            + "<h2>The butt weld bevel</h2>"
            "<p>ASME B16.25 governs the welding end preparation for buttwelding "
            "fittings and pipe. The standard preparation is a single V bevel at "
            "37.5° from the pipe axis — giving a 75° included angle when two "
            "ends meet — with a root face, or land, of about 1/16 in.</p>"
            + '<p class="formula">Bevel angle 37.5° ± 2.5° &nbsp;·&nbsp; '
            "root face 1/16 in ± 1/32 in &nbsp;·&nbsp; root gap ≈ 1/16 in</p>"
            "<p>Heavier walls change this. Above about 3/4 in wall thickness "
            "B16.25 provides compound bevels — a steeper angle near the root "
            "and a shallower one further out — to reduce the volume of weld "
            "metal required. The root face and gap exist so the root pass "
            "achieves full penetration without burning through.</p>"
            "<h2>Matching mismatched walls</h2>"
            "<p>When two components of different wall thickness are butt "
            "welded, the bores do not line up and the transition has to be "
            "handled. ASME B31.3 limits the permissible internal misalignment "
            "and requires the thicker component to be tapered — no steeper than "
            "30° — down to the thinner bore. In practice that means the heavier "
            "part is counterbored and back-bevelled before fit-up.</p>"
            "<p>This is exactly the situation created by welding "
            '<a href="/compare/schedule-40-vs-schedule-80/">Schedule 40 to '
            "Schedule 80</a> pipe, and it is why a schedule change should "
            "happen at a fitting or a flange rather than mid-run wherever "
            "possible.</p>"
            + "<h2>Threaded ends in practice</h2>"
            "<p>NPT threads seal on interference between the tapered flanks, "
            "not on the sealant — the tape or dope is a lubricant and a gap "
            "filler, not the seal. Three consequences follow:</p>"
            "<ul>"
            "<li><strong>Wall thickness matters.</strong> The thread root cuts "
            "into the wall, so threaded pipe needs a heavier schedule — "
            "Schedule 80 as a practical minimum in most specifications.</li>"
            "<li><strong>Vibration loosens threads.</strong> Threaded joints "
            "are excluded from severe cyclic service by ASME B31.3 and are a "
            "poor choice on anything that shakes.</li>"
            "<li><strong>Seal welding is sometimes required.</strong> B31.3 "
            "requires threaded joints in Category M fluid service to be seal "
            "welded, which makes them permanent.</li>"
            "</ul>"
            + "<h2>Specifying an end on a purchase order</h2>"
            + table(
                ["Abbreviation", "Means"],
                [["<strong>BE</strong>", "Bevelled end, both ends"],
                 ["<strong>PE</strong>", "Plain end, both ends"],
                 ["<strong>TBE</strong>", "Threaded both ends"],
                 ["<strong>TOE</strong>", "Threaded one end"],
                 ["<strong>T&amp;C</strong>",
                  "Threaded and coupled — threaded both ends with a coupling "
                  "fitted to one"],
                 ["<strong>POE</strong>", "Plain one end"]],
                caption="End preparation abbreviations used on purchase "
                        "orders.",
                note="Always state the end preparation explicitly. Mill stock "
                    "defaults vary by size and supplier.")
            + '<div class="callout"><p>See also: '
            '<a href="/fittings/">B16.9 buttweld fittings</a> · '
            '<a href="/compare/socket-weld-vs-threaded-flange/">socket weld vs '
            "threaded</a> · "
            '<a href="/compare/slip-on-vs-threaded-flange/">slip-on vs '
            "threaded</a> · "
            '<a href="/guides/pipe-material-selection/">material '
            "selection</a>.</p></div>")

    q = [
        ("What angle is a standard pipe bevel?",
         "<p>37.5° ± 2.5° from the pipe axis, giving a 75° included angle when "
         "two ends meet, with a root face of about 1/16 in. ASME B16.25 governs "
         "the preparation.</p>"),
        ("What does BE mean on a pipe purchase order?",
         "<p>Bevelled end — the pipe is supplied with a butt welding bevel on "
         "both ends. PE is plain end, TBE is threaded both ends, and TOE is "
         "threaded one end.</p>"),
        ("Why is a root face needed on a weld bevel?",
         "<p>To give the root pass something to fuse into without burning "
         "through. A knife-edge root melts away and leaves a concave or "
         "burned-through root.</p>"),
        ("Can I butt weld Schedule 40 to Schedule 80 pipe?",
         "<p>Yes, but the internal misalignment must be handled. ASME B31.3 "
         "requires the thicker component to be tapered at no more than 30° down "
         "to the thinner bore, which means counterboring and back-bevelling the "
         "heavier part.</p>"),
        ("What schedule is needed for threaded pipe?",
         "<p>Heavy enough that the thread root leaves adequate wall — Schedule "
         "80 is the practical minimum in most specifications. Thin-wall pipe "
         "such as Schedule 10 cannot be threaded.</p>"),
    ]

    guide("pipe-end-connections",
          "Pipe End Connections: Bevelled, Plain, Threaded",
          "Pipe ends come bevelled for butt welding, plain for socket weld and "
          "slip-on, or NPT threaded. Bevel geometry, wall mismatch rules and "
          "purchase order codes.",
          "Pipe End Connections",
          "Bevelled, plain, threaded and grooved — how each end preparation is "
          "made, what it joins to, and the wall-mismatch rule that catches "
          "schedule changes mid-run.",
          body, faq_pairs=q, card_meta="Bevel · plain · threaded")


# --------------------------------------------------------------------------
# section index pages
# --------------------------------------------------------------------------

def section_index(section_slug, section_name, h1, title, desc, lede, groups,
                  intro_html, outro_html, callout):
    """Index for a section of articles.

    `groups` is a list of (heading, blurb, [urls]). Any registered page that no
    group names is listed under a final heading, so a new article cannot go
    missing from its index.
    """
    registry = COMPARE_PAGES if section_slug == "compare" else GUIDE_PAGES
    by_url = {u: (nm, meta) for nm, u, meta in registry}
    named = {u for _, _, urls in groups for u in urls}
    rest = [u for _, u, _ in registry if u not in named]
    if rest:
        groups = groups + [("More " + section_name.lower(), "", rest)]

    def card(u):
        nm, meta = by_url[u]
        why = READING[u][1] if u in READING else ""
        return (f'<a class="card" href="{u}"><span class="card-title">'
                f'{esc(nm)}</span><span class="card-meta">{meta}</span>'
                + (f'<span class="card-spec">{esc(why)}</span>' if why else "")
                + "</a>")

    blocks = "".join(
        f"<h2>{esc(h)}</h2>" + (f"<p>{b}</p>" if b else "")
        + '<div class="grid">' + "".join(card(u) for u in urls) + "</div>"
        for h, b, urls in groups)

    crumb_html, crumb_ld = crumbs([("Home", "/"), (section_name, None)])
    body = (crumb_html + '<div class="wrap">'
            f'<div class="page-head"><h1>{esc(h1)}</h1>'
            f'<p class="lede">{lede}</p></div>'
            + intro_html + blocks + outro_html
            + f'<div class="callout"><p>{callout}</p></div></div>')
    page(f"/{section_slug}/", title, desc, body,
         ld=[crumb_ld, item_list([(nm, u) for nm, u, _ in registry], h1)])
    index_entry(f"{section_name} index", f"/{section_slug}/", h1)


def compare_index():
    section_index(
        "compare", "Compare", "Piping Comparisons",
        "Pipe, Flange and Fitting Comparisons | PipeData",
        "Side-by-side comparisons of pipe schedules, flange types and classes, "
        "pipe materials and buttweld fittings — each with published "
        "dimensions for both options.",
        "Schedule against schedule, flange against flange, material against "
        "material — each comparison built on the published dimensions for both "
        "sides rather than on generalities.",
        [
            ("Pipe schedules and weight classes",
             "The outside diameter of a pipe never changes with the "
             "schedule, so choosing between two schedules is a trade between "
             "wall and bore. A heavier wall holds more pressure and leaves "
             "more metal for corrosion and threading; it also weighs more, "
             "costs more and carries less flow. These pages put the two "
             "walls side by side in every size where both exist.",
             ["/compare/schedule-40-vs-schedule-80/",
              "/compare/schedule-10-vs-schedule-40/",
              "/compare/schedule-40-vs-schedule-160/",
              "/compare/schedule-5s-vs-schedule-10s/",
              "/compare/std-vs-xs/", "/compare/xs-vs-xxs/"]),
            ("Flange types",
             "All six ASME B16.5 flange types share one set of outside "
             "diameters, bolt circles and bolting in a given size and class, "
             "so any two will bolt together. What separates them is how "
             "each is fixed to the pipe, and that decides the pressure, "
             "temperature and cycling each one suits.",
             ["/compare/weld-neck-vs-slip-on-flange/",
              "/compare/weld-neck-vs-blind-flange/",
              "/compare/slip-on-vs-threaded-flange/",
              "/compare/socket-weld-vs-threaded-flange/",
              "/compare/lap-joint-vs-slip-on-flange/"]),
            ("Flange classes and facings",
             "Moving up a pressure class changes almost everything about a "
             "flange: its diameter, its thickness, the bolt circle and the "
             "bolts. These pages show how much metal each step adds and "
             "what rating it buys, and compare the two common ways of "
             "seating the gasket.",
             ["/compare/class-150-vs-class-300/",
              "/compare/class-300-vs-class-600/",
              "/compare/class-600-vs-class-900/",
              "/compare/class-900-vs-class-1500/",
              "/compare/class-1500-vs-class-2500/",
              "/compare/raised-face-vs-ring-type-joint/"]),
            ("Pipe materials and manufacture",
             "Dimensions are the same whatever the pipe is made of. The "
             "material and the way the pipe is made decide the allowable "
             "stress, the temperature range and the resistance to "
             "corrosion, and through the joint factor they also change the "
             "wall thickness a given pressure calls for.",
             ["/compare/seamless-vs-welded-pipe/",
              "/compare/carbon-steel-vs-stainless-steel-pipe/",
              "/compare/a106-vs-a53-pipe/",
              "/compare/304-vs-316-stainless-steel-pipe/"]),
            ("Buttweld fittings",
             "Fitting choices are mostly about space and flow: how much "
             "room a change of direction takes, how much pressure it "
             "costs, and whether a line will drain or vent once it is "
             "built.",
             ["/compare/long-radius-vs-short-radius-elbow/",
              "/compare/90-degree-vs-45-degree-elbow/",
              "/compare/concentric-vs-eccentric-reducer/"]),
        ],
        "<h2>How these comparisons are built</h2>"
        "<p>Most questions about piping components arrive as a choice "
        "between two things. Is Schedule 80 worth the extra weight over "
        "Schedule 40? Will a slip-on flange do where a weld neck was "
        "drawn? Each page here takes one such pair and answers it in the "
        "same order: a short answer first, then the dimensions of both "
        "options in every size where both are published, then the "
        "practical differences in cost, fabrication and service, and "
        "finally the cases where each one is the right choice.</p>"
        "<p>The tables are not written by hand. They are generated from "
        "the same data files as the dimension pages, so a figure on a "
        "comparison page always agrees with the figure on the page for "
        "that size or class. Percentages and differences are calculated "
        "from those figures when the site is built.</p>",
        "<h2>Using a comparison</h2>"
        "<p>Start from the short answer at the top of the page, then check "
        "the row for your own size, because the gap between two options is "
        "rarely the same across the range. Schedule 40 and standard weight "
        "are the same pipe up to NPS 10 and different pipes above it; "
        "Class 400 and Class 600 flanges share dimensions in the small "
        "sizes and not in the large ones. Where a comparison depends on "
        "pressure, the rating has to be read at the design temperature "
        "and for the actual material, which the "
        '<a href="/reference/pressure-temperature-ratings/">'
        "pressure-temperature tables</a> give.</p>",
        "<strong>Every table on these pages is generated from the same data as "
        "the specification pages.</strong> Where a figure is derived rather "
        "than tabulated — bore, weight, flow area, end load — the formula is "
        "stated alongside it.")


def guides_index():
    section_index(
        "guides", "Guides", "Piping Engineering Guides",
        "Piping Design Guides & Calculations | PipeData",
        "Practical piping guides: pipe sizing by velocity, wall thickness from "
        "Barlow's formula, hydrostatic test pressure, flange bolt torque and "
        "material selection.",
        "Worked calculations and selection guidance — sizing, wall thickness, "
        "test pressure, bolt torque and material choice, each with the formula "
        "and a full example.",
        [
            ("Start here: how pipe is designated",
             "Pipe is ordered by a nominal size and a schedule, and neither "
             "of them is a measurement. These guides explain what the "
             "designations mean, which is the background every table on "
             "the site assumes.",
             ["/guides/pipe-schedule-explained/",
              "/guides/nps-vs-dn-explained/",
              "/guides/pipe-end-connections/"]),
            ("Sizing a line and its wall",
             "The usual order of work is to size the bore for the flow, "
             "then find the wall the pressure needs, then pick the "
             "schedule that provides it, and finally choose the material. "
             "The guides follow that order and each carries a worked "
             "example with the arithmetic shown.",
             ["/guides/pipe-sizing/",
              "/guides/pipe-wall-thickness-calculation/",
              "/guides/pipe-material-selection/"]),
            ("Flanged joints",
             "A flange is chosen by pressure class at the design "
             "temperature, given a facing to suit the gasket, and made up "
             "to a bolt load. These guides cover each of those steps.",
             ["/guides/pressure-temperature-derating/",
              "/guides/flange-face-types/",
              "/guides/flange-bolt-torque/"]),
            ("Testing",
             "What a finished line is pressure tested to, and how the "
             "flange class limits the test.",
             ["/guides/hydrostatic-test-pressure/"]),
        ],
        "<h2>What these guides are for</h2>"
        "<p>The dimension tables on this site say what a component "
        "measures. They do not say which component to pick. These guides "
        "cover that second question: how a flow rate becomes a line size, "
        "how a design pressure becomes a wall thickness and then a "
        "schedule, and how a pressure class is read off at the design "
        "temperature.</p>"
        "<p>Each guide gives the formula, says where it comes from, and "
        "then works one example through to a result using the pipe and "
        "flange data published here. The example figures are calculated "
        "when the site is built, so the answer in the text is the answer "
        "the data gives. Where a guide depends on a value that a code "
        "fixes, such as an allowable stress, the value used is stated so "
        "that it can be replaced with the one that applies to your "
        "job.</p>",
        "<h2>What the guides leave out</h2>"
        "<p>A pressure design calculation is one check among several. A "
        "real line also has to carry its own weight between supports, "
        "absorb thermal expansion, and withstand wind, earthquake and "
        "relief valve reaction where those apply. The guides stay with "
        "the calculations that lead directly to a size, a schedule or a "
        "class, because those are the ones the tables on this site can "
        "answer. For comparisons between two specific options, see the "
        '<a href="/compare/">comparison pages</a>.</p>',
        "<strong>These guides explain method, not code compliance.</strong> "
        "Every calculation here is a worked illustration. Confirm the governing "
        "equations, allowable stresses and acceptance criteria against a "
        "current copy of the applicable ASME, ASTM or API document before "
        "using them in design.")


# --------------------------------------------------------------------------
# top-level pages
# --------------------------------------------------------------------------

# Standards the pages refer to, with the most recent edition known when
# CONTENT_UPDATED was last set. Shown on the About page. The site does not
# claim to have been transcribed from any one of them; see about_page().
STANDARDS = [
    ("ASME B36.10", "2022", "Welded and Seamless Wrought Steel Pipe",
     "Pipe outside diameters and wall thicknesses. Published as B36.10M "
     "until the 2022 edition dropped the M."),
    ("ASME B36.19", "2022", "Welded and Seamless Wrought Stainless Steel Pipe",
     "The 5S, 10S, 40S and 80S stainless wall series. Published as B36.19M "
     "until 2022."),
    ("ASME B16.5", "2025", "Pipe Flanges and Flanged Fittings, NPS 1/2 "
     "through NPS 24",
     "Flange dimensions, bolting and pressure-temperature ratings. The 2025 "
     "edition replaced B16.5-2020."),
    ("ASME B16.47", "2025", "Large Diameter Steel Flanges, NPS 26 through "
     "NPS 60",
     "Series A and Series B large flanges. The 2025 edition replaced "
     "B16.47-2020."),
    ("ASME B16.9", "2024", "Factory-Made Wrought Buttwelding Fittings",
     "Centre-to-end and end-to-end dimensions of elbows, tees, reducers "
     "and caps. The 2024 edition replaced B16.9-2018."),
    ("ASME A13.1", "2023", "Scheme for the Identification of Piping Systems",
     "Pipe marker colours and legend sizes."),
    ("ASME B31.3", "", "Process Piping",
     "Source of the pressure design equation used in the guides and on the "
     "pipe size pages. Revised every two years."),
]


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

    def feature(paths):
        return "".join(
            f'<a class="card" href="{p}"><span class="card-title">'
            f'{esc(READING[p][0])}</span><span class="card-meta">'
            f'{esc(READING[p][1])}</span></a>' for p in paths)

    guide_cards = feature([
        "/guides/pipe-schedule-explained/", "/guides/pipe-sizing/",
        "/guides/pipe-wall-thickness-calculation/",
        "/guides/pressure-temperature-derating/",
        "/guides/flange-bolt-torque/", "/guides/pipe-material-selection/"])
    compare_cards = feature([
        "/compare/schedule-40-vs-schedule-80/",
        "/compare/class-150-vs-class-300/",
        "/compare/weld-neck-vs-slip-on-flange/",
        "/compare/seamless-vs-welded-pipe/",
        "/compare/long-radius-vs-short-radius-elbow/",
        "/compare/304-vs-316-stainless-steel-pipe/"])

    n_pipe = len(pipes["sizes"])
    n_flange = sum(len(t["classes"]) for t in ftypes)

    # The worked example in the introduction is read from the data, like
    # every other figure on the site.
    four = next(s for s in pipes["sizes"] if s["nps"] == "4")
    t40, t80 = four["walls"]["40"], four["walls"]["80"]
    f150 = next(r for r in b165["classes"]["150"]["rows"] if r["nps"] == "4")
    f300 = next(r for r in b165["classes"]["300"]["rows"] if r["nps"] == "4")
    r150, r300 = class_rating("150"), class_rating("300")

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
        ("Can I design or buy from these tables?",
         "<p>Use them to look a figure up, to check a drawing or to prepare "
         "an enquiry. Before anything is fabricated, purchased or signed "
         "off, confirm the figure against a current copy of the governing "
         "standard. The <a href='/about/'>About page</a> explains how the "
         "data is compiled and checked, and what that checking does not "
         "cover.</p>"),
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
        "publisher": PUBLISHER_LD,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": SITE + "/?q={search_term_string}"},
            "query-input": "required name=search_term_string",
        },
    }
    org_ld = dict({"@context": "https://schema.org"}, **PUBLISHER_LD)
    org_ld["contactPoint"] = {"@type": "ContactPoint", "email": EMAIL,
                              "contactType": "corrections and enquiries",
                              "url": SITE + "/contact/"}

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
  <h2>What PipeData is</h2>
  <p>PipeData is a free reference for the dimensions of steel pipe, flanges
     and buttweld fittings made to the ASME B36 and B16 standards. It is
     written for the people who look these figures up during a working day:
     piping designers and drafters checking a drawing, pipefitters and
     welders on a fabrication floor, buyers and estimators preparing an
     enquiry, inspectors identifying what is in front of them, and students
     learning how piping is specified.</p>
  <p>A table of numbers only helps if you know what the numbers mean, so the
     site pairs every table with an explanation. Pipe size pages say what
     each schedule does to the bore, the weight and the pressure the pipe
     will hold. Flange pages say what a pressure class is rated for and
     where each flange type is used. The <a href="/guides/">guides</a> work
     through the calculations behind a size or a schedule, and the
     <a href="/compare/">comparisons</a> set two options side by side.</p>

  <h2>How to use the data</h2>
  <p>Three rules explain most of what is on this site, and most mistakes
     made with piping tables come from forgetting one of them.</p>
  <ol>
    <li><strong>The outside diameter belongs to the size.</strong> NPS 4 pipe
        is {inch_mm(four["od"])} across in every schedule. Schedule 40 has a
        {inch_mm(t40)} wall and Schedule 80 a {inch_mm(t80)} wall, and the
        difference comes out of the bore, which goes from
        {inch_mm(inside_dia(four["od"], t40))} to
        {inch_mm(inside_dia(four["od"], t80))}. Start on the
        <a href="/pipes/">pipe size pages</a>.</li>
    <li><strong>A flange is sized by NPS and pressure class.</strong> An
        NPS 4 Class 150 flange is {inch_mm(f150["o"], 2)} across with
        {f150["bolts"]} bolts; in Class 300 it is {inch_mm(f300["o"], 2)}
        across with {f300["bolts"]} bolts. The class is not a pressure: in
        A105 carbon steel those two are rated {r150} and {r300} psig at
        100 °F, and less when hot. Start on the
        <a href="/flanges/">flange pages</a> and the
        <a href="/reference/pressure-temperature-ratings/">rating tables</a>.</li>
    <li><strong>A buttweld fitting is sized by NPS alone.</strong> An elbow
        or a tee takes up the same space in every schedule, so a layout can
        be dimensioned before the wall is chosen. Start on the
        <a href="/fittings/">fitting pages</a>.</li>
  </ol>
  <p>Figures that come straight from a standard are shown as tabulated.
     Figures worked out from them, such as bore, weight, flow area and water
     content, carry the formula beside the table. Inches come first
     because the standards are written in them; millimetres, kilograms per
     metre and bar are shown alongside.</p>

  <h2>Guides</h2>
  <p>Worked methods for the decisions the tables cannot make for you.</p>
  <div class="grid">{guide_cards}</div>
  <p><a class="more" href="/guides/">All guides →</a></p>

  <h2>Comparisons</h2>
  <p>Two options, the published dimensions of both, and when to use each.</p>
  <div class="grid">{compare_cards}</div>
  <p><a class="more" href="/compare/">All comparisons →</a></p>

  <h2>Pipe dimensions</h2>
  <p>Outside diameter, wall thickness, bore, weight and flow area for every
     ASME B36.10M size from NPS 1/8 to NPS 36, across all fourteen schedules.
     Each size page also lists the flanges and fittings made for that
     size.</p>
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
  <p>Conversion charts and whole-range tables, for when the question spans
     every size at once.</p>
  <div class="grid">{ref_cards}</div>

  {faq_html}

  <div class="callout">
    <p><strong>Reference only.</strong> PipeData reproduces published standard
    data for quick lookup. Confirm every dimension against a current copy of the
    governing ASME, ASTM or API standard before fabrication, procurement or
    design. How the data is compiled and checked is set out on the
    <a href="/about/">About page</a>; corrections are welcome through the
    <a href="/contact/">contact page</a>.</p>
  </div>
</div>
"""
    page("/", "PipeData — Pipe, Flange & Fitting Specifications",
         "Pipe, flange and fitting dimensions from ASME B36.10, B16.5, B16.47 "
         "and B16.9. Wall thickness, weight, bolt circles and pressure ratings, "
         "imperial with metric.",
         body, ld=[website_ld, org_ld, faq_ld])


def email_link():
    return (f'<!--email_off--><a href="mailto:{EMAIL_HTML}">{EMAIL_HTML}</a>'
            '<!--/email_off-->')


def about_page(pipes, ftypes, fittings):
    std_rows = [[f"<strong>{esc(code)}</strong>", esc(name),
                 esc(code + "-" + ed) if ed else "Current edition",
                 esc(note)]
                for code, ed, name, note in STANDARDS]
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("About", None)])
    body = (crumb_html
            + '<div class="wrap narrow">'
            '<div class="page-head"><h1>About PipeData</h1>'
            '<p class="lede">A fast, free lookup for the dimensional and rating '
            'data that piping work depends on — with the caveats stated plainly '
            'rather than buried.</p>'
            f'<p class="byline">Updated <time datetime="{CONTENT_UPDATED}">'
            f'{long_date(CONTENT_UPDATED)}</time></p></div>'
            "<h2>What this is</h2>"
            "<p>PipeData reproduces the dimension and rating tables from the "
            "ASME B16 and B36 series in a form you can read on a phone at a "
            "fabrication shop. Every page answers one question directly: the "
            "wall thickness of a size, the bolting of a class, the centre-to-end "
            "of a fitting. Around the tables it explains what the figures "
            "mean and how they are used, in guides and comparisons that work "
            "their examples from the same data.</p>"
            '<h2 id="who">Who publishes it</h2>'
            "<p>PipeData is an independent publication. Its pages are written "
            "and maintained under the name " + esc(EDITORIAL) + ", and the "
            "site names no individual author. It is a reference work and not "
            "an engineering practice: nobody at PipeData is acting as your "
            "engineer, and nothing here has been reviewed or stamped by a "
            "licensed professional engineer for your application.</p>"
            "<p>We say this plainly because a site like this one is only as "
            "good as its figures, and the honest basis for trusting a figure "
            "here is the method described below and the fact that you can "
            "check it against the standard, not the standing of whoever "
            "typed it.</p>"
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
            '<h2 id="method">How the figures are checked</h2>'
            "<p>The site is generated by a program from a small set of data "
            "files, one for each standard. No table on any page is typed by "
            "hand, so a figure exists in one place only, and the pipe page, "
            "the flange page and the comparison page that quote it cannot "
            "disagree with each other.</p>"
            "<ul>"
            "<li><strong>Tabulated values</strong> such as outside diameter, "
            "wall thickness, flange diameter, bolt circle, bolting and "
            "pressure rating are entered once in the data files from "
            "published tables.</li>"
            "<li><strong>Derived values</strong> such as bore, weight, flow "
            "area, water content, bolt hole spacing and end load are "
            "calculated from the tabulated ones every time the site is "
            "built. The formula is printed beside the table.</li>"
            "<li><strong>Figures in the text</strong> are calculated from "
            "the data wherever that is possible, so a sentence cannot go on "
            "saying one thing after a table has been corrected to say "
            "another.</li>"
            "<li><strong>The build stops</strong> if the data contains a wall "
            "so thick it would close the bore, if a stainless pipe size "
            "is missing from the carbon steel table it takes its diameter "
            "from, if two pages share a title or a description, or if any "
            "link on the site leads to a page that does not exist.</li>"
            "</ul>"
            "<p>These checks catch inconsistency. They cannot catch a figure "
            "that was copied wrongly at the start and is wrong everywhere, "
            "which is why corrections from readers matter.</p>"
            '<h2 id="corrections-log">Errors found and corrected</h2>'
            "<p>Working the examples from the data has so far exposed four "
            "statements that read plausibly and were wrong. They are listed "
            "here because a reference that hides its corrections gives you "
            "no way to judge it.</p>"
            "<ul>"
            "<li>The pipe sizing example gave NPS 6 as its answer. Worked "
            "from the flow areas, the smallest Schedule 40 size that meets "
            "the duty is NPS 5.</li>"
            "<li>The 45° elbow page said the centre-to-end dimension is "
            "always the same fraction of the size. ASME B16.9 publishes "
            "rounded figures, so the ratio varies, and the table governs, "
            "not the formula.</li>"
            "<li>The wall of NPS 24 Schedule 40 pipe was written as "
            "0.687 in. The published value is 0.688 in.</li>"
            "<li>An early page said that the 5S and 10S stainless walls are "
            "thinner than anything in B36.10M. They match Schedule 5 and "
            "Schedule 10 in every size where both are published.</li>"
            "</ul>"
            "<p>A check of the flange data against published reproductions "
            "of the ASME B16.5 tables, completed on "
            + long_date(CONTENT_UPDATED) + ", found larger errors. All have "
            "been corrected:</p>"
            "<ul>"
            "<li><strong>Pressure-temperature ratings, Group 1.1.</strong> "
            "Classes 300 to 2500 carried values from an edition of the "
            "1980s and 1990s at 850 °F and above, and in a few cells "
            "below. At 900 °F a Class 300 carbon steel flange was shown as "
            "170 psig; the current figure is 230 psig.</li>"
            "<li><strong>Pressure-temperature ratings, Group 1.2.</strong> "
            "Both the list of materials and the whole table were wrong and "
            "have been replaced.</li>"
            "<li><strong>Type 316 stainless steel</strong> was listed in "
            "Group 2.1 with Type 304. It belongs to Group 2.2, which now "
            "has its own table. The Group 2.1 table had carried old Type "
            "316 values and now carries the Type 304 values.</li>"
            "<li><strong>Flange thickness and hub length in Classes 150 "
            "and 300</strong> were the older figures that include the "
            "1/16 in raised face, on pages that said the raised face was "
            "excluded. Every one was 0.06 in too large and has been "
            "reduced.</li>"
            "<li><strong>Slip-on hub lengths in Class 150, NPS 14 to "
            "24,</strong> were those of the lap joint flange.</li>"
            "<li><strong>Slip-on and socket weld flanges</strong> were "
            "shown in classes and sizes in which ASME B16.5 does not "
            "publish them. There is no Class 2500 slip-on flange, and no "
            "socket weld flange in Class 400, 900 or 2500. Those pages "
            "have been withdrawn and the remaining tables cut back to the "
            "published sizes.</li>"
            "</ul>"
            "<p>The check was made against manufacturers' and distributors' "
            "reproductions of the tables, with two or more independent "
            "sources for most values. It was not made against the standard "
            "itself. A few items could not be confirmed and are marked on "
            "the pages concerned: the Class 400 rows of four rating "
            "tables, the size range of threaded flanges in Classes 1500 "
            "and 2500, and most of the Class 300 hub lengths. Raised face "
            "diameters above NPS 3 have not been checked.</p>"
            '<h2 id="editions">Standards and editions</h2>'
            "<p>The table lists the standards these pages refer to and the "
            "most recent edition of each that we know of. The data on "
            "PipeData was compiled from published tables of these "
            "standards and cross-checked between sources. It was not "
            "transcribed line by line from one particular printed edition, "
            "and we do not claim that it was. Pipe and flange dimensions of "
            "the kind tabulated here have been stable across many editions, "
            "but ratings, notes and the range of sizes covered do change, "
            "so the edition your contract invokes is the one that "
            "governs.</p>"
            + table(["Standard", "Title", "Latest edition known", "Used for"],
                    std_rows,
                    caption="Standards referred to on PipeData, as of "
                            + long_date(CONTENT_UPDATED) + ".")
            + "<p>ASME, ASTM and API publish and sell these standards. "
            "PipeData does not reproduce their text, and links to none of "
            "it; it tabulates dimensions and explains them in its own "
            "words.</p>"
            "<h2>Units</h2>"
            "<p>Imperial is primary — inches, pounds per foot, psig — because "
            "that is how these standards are written and how the material is "
            "ordered in North America. The metric equivalent appears alongside "
            "in every table: millimetres beneath inches, kilograms per metre "
            "beneath pounds per foot, bar beside psig.</p>"
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
            "<h2>How the site is paid for</h2>"
            "<p>PipeData is free to use and carries advertising. "
            "Advertisers have no say in what the pages contain, and the "
            "site sells no products and takes no commission on any. The "
            '<a href="/privacy/">privacy page</a> lists every third-party '
            "service the site loads.</p>"
            "<h2>Corrections</h2>"
            "<p>If a figure here disagrees with your copy of the standard, the "
            "standard is right and we want to know. Email "
            + email_link() + " with the page and the clause, or see the "
            '<a href="/contact/">contact page</a> for what to include.</p>'
            "<h2>Independence</h2>"
            "<p>PipeData is an independent reference project. It is not "
            "affiliated with, endorsed by or sponsored by ASME, ASTM, API, MSS "
            "or any manufacturer or distributor.</p>"
            "</div>")
    about_ld = {
        "@context": "https://schema.org", "@type": "AboutPage",
        "name": "About PipeData", "url": SITE + "/about/",
        "dateModified": CONTENT_UPDATED, "publisher": PUBLISHER_LD,
    }
    page("/about/", "About PipeData — Piping Specification Reference",
         "PipeData is an independent reference for ASME pipe, flange and "
         "fitting dimensions. What it covers, where the numbers come from, and "
         "the gaps it admits to.",
         body, ld=[crumb_ld, about_ld])
    index_entry("About PipeData", "/about/", "Scope, sources and caveats")


def contact_page():
    crumb_html, crumb_ld = crumbs([("Home", "/"), ("Contact", None)])
    body = (crumb_html
            + '<div class="wrap narrow">'
            '<div class="page-head"><h1>Contact PipeData</h1>'
            '<p class="lede">One address for corrections, questions and '
            'everything else. A person reads every message.</p></div>'
            '<div class="callout"><p><strong>Email:</strong> '
            + email_link() + "</p></div>"
            "<h2>Reporting an error</h2>"
            "<p>Corrections are the most useful thing you can send. If a "
            "figure on this site disagrees with your copy of a standard, "
            "the standard is right. To let us find and fix it quickly, "
            "please include:</p>"
            "<ul>"
            "<li>the address of the page, or the size, schedule or class "
            "concerned;</li>"
            "<li>the figure shown here and the figure you believe is "
            "correct;</li>"
            "<li>the standard, its edition year, and the table or paragraph "
            "number you are reading from.</li>"
            "</ul>"
            "<p>Please do not send scans or photographs of pages from a "
            "standard. They are copyright documents, and the table number "
            "is all we need to look the figure up. Confirmed errors are "
            "corrected in the data file, which corrects every page that "
            "quotes the figure, and significant ones are recorded on the "
            '<a href="/about/#corrections-log">About page</a>.</p>'
            "<h2>Asking for something to be added</h2>"
            "<p>Requests for a size range, a standard or a guide that is "
            "not here are welcome, and they decide what gets built next. "
            "Tell us what you were trying to look up and what you expected "
            "to find.</p>"
            "<h2>What we cannot do</h2>"
            "<p>PipeData is a reference, not an engineering service. We "
            "cannot check a design, confirm that a component suits your "
            "service, or say what your code or contract requires. Those "
            "answers have to come from the governing standard and from the "
            "engineer responsible for the work. We also do not sell pipe, "
            "flanges or fittings and cannot quote for them.</p>"
            "<h2>Replies</h2>"
            "<p>We aim to answer within a few working days. There is no "
            "contact form on this site because a form would mean collecting "
            "and storing what you type; email leaves the message with you "
            "and with us and nowhere else. Your address is used to reply "
            "to you and for nothing else. See the "
            '<a href="/privacy/">privacy page</a>.</p>'
            "<h2>Advertising and privacy questions</h2>"
            "<p>Questions about the advertising shown on the site, about "
            "cookies or about personal data go to the same address. Ads "
            "are placed by Google AdSense, and the privacy page explains "
            "how to control them.</p>"
            "</div>")
    contact_ld = {
        "@context": "https://schema.org", "@type": "ContactPage",
        "name": "Contact PipeData", "url": SITE + "/contact/",
        "publisher": PUBLISHER_LD,
    }
    page("/contact/", "Contact PipeData — Corrections and Questions",
         "Contact PipeData at info@pipedata.org to report an error in a pipe, "
         "flange or fitting table, ask a question or request a standard "
         "that is not covered.",
         body, ld=[crumb_ld, contact_ld])
    index_entry("Contact PipeData", "/contact/", "Corrections and questions")


def privacy_page():
    ga_para = (
        "<p>This site uses Google Analytics 4 to count visits and see which "
        "pages get used. It sets cookies and sends your IP address to Google, "
        "who process it as our data processor. We do not use it to build "
        "profiles or to target ads.</p>"
        if GA_ENABLED else "")

    ahrefs_para = (
        "<p>It also uses Ahrefs Web Analytics for the same purpose. That "
        "script is loaded from <code>analytics.ahrefs.com</code>, so your "
        "browser sends your IP address and user agent to Ahrefs when a page "
        "loads. It sets no cookies and is not used to build a profile of "
        "you.</p>" if AHREFS_KEY else "")

    ads_para = (
        "<p>Pages carry advertising served by Google AdSense. Google and its "
        "partners use cookies to serve ads based on your prior visits to this "
        "and other websites. You can opt out of personalised advertising in "
        '<a href="https://adssettings.google.com/" rel="nofollow">Google Ads '
        "Settings</a>, or opt out of some third-party vendors' use of cookies "
        'at <a href="https://www.aboutads.info/choices/" rel="nofollow">'
        "aboutads.info</a>. How Google uses this data is described at "
        '<a href="https://policies.google.com/technologies/partner-sites" '
        'rel="nofollow">policies.google.com/technologies/partner-sites</a>.'
        "</p>" if ADSENSE_CLIENT else "")

    # Consent Mode changes what the two paragraphs above actually do on a
    # first visit, so say so rather than leaving "it sets cookies" to stand
    # unqualified.
    consent_para = (
        "<p>Where you are reading from decides what happens next. Before any "
        "Google tag loads, the page sets Google Consent Mode defaults. In the "
        "EEA, the UK and Switzerland those defaults refuse analytics storage, "
        "advertising storage, ad user data and ad personalisation: the "
        "consent dialog Google serves on your first visit is what lifts them, "
        "and until you answer it — and for the whole visit if you decline — "
        "the tags run without storage and set no cookies. Outside those "
        "countries the defaults start granted, and the tags set cookies on "
        "the first page view.</p>"
        if (GA_ENABLED or ADSENSE_CLIENT) else "")

    if not (ga_para or ahrefs_para or ads_para):
        ga_para = ("<p>This site runs no analytics, no advertising and no "
                   "third-party tracking scripts. Nothing on these pages sets "
                   "a cookie, and no profile of you is built or bought.</p>")

    # Google Fonts stopped being the only third-party request the moment a
    # second script went in the head; don't let the old absolute claim stand.
    others = bool(GA_ENABLED or AHREFS_KEY or ADSENSE_CLIENT)
    fonts = ("<p>Typefaces are loaded from Google Fonts, which means your "
             "browser makes a request to <code>fonts.googleapis.com</code> and "
             "<code>fonts.gstatic.com</code> when a page loads. That request "
             "carries your IP address and user agent to Google."
             + (" The analytics, advertising and delivery services "
                "described on this page are the only other third parties "
                "your browser contacts.</p>"
                if others else
                " It is the only third-party request the site makes.</p>"))

    cookie_setters = ([n for n, on in (("Google Analytics", GA_ENABLED),
                                       ("Google AdSense", ADSENSE_CLIENT))
                       if on])
    if cookie_setters:
        cookies = ("<p>" + " and ".join(cookie_setters)
                   + (" set their own cookies, in the EEA, the UK and "
                      "Switzerland only once you have consented to them."
                      if len(cookie_setters) > 1
                      else " sets its own cookies, in the EEA, the UK and "
                      "Switzerland only once you have consented to them.")
                   + (" Ahrefs Web Analytics and the site itself set none.</p>"
                      if AHREFS_KEY else " The site itself sets none.</p>"))
    else:
        cookies = "<p>This site sets no cookies at all.</p>"

    body = (crumbs([("Home", "/"), ("Privacy", None)])[0]
            + '<div class="wrap narrow">'
            '<div class="page-head"><h1>Privacy</h1>'
            '<p class="lede">What this site does and does not collect, stated '
            'in full.</p></div>'
            "<h2>Analytics</h2>" + ga_para + ahrefs_para
            + ("<h2>Advertising</h2>" + ads_para if ads_para else "")
            + ("<h2>Consent</h2>" + consent_para if consent_para else "")
            + "<h2>Fonts</h2>" + fonts
            + "<h2>What we never collect</h2>"
            "<p>There are no accounts, no logins and no forms on this site. We "
            "do not ask for your name, your email address or your employer, and "
            "there is nowhere on the site to give them to us. Nothing you type "
            "into the search box leaves your browser — the search index is a "
            "static file your browser downloads once and queries locally.</p>"
            "<h2>Hosting and delivery</h2>"
            "<p>PipeData is a set of static files hosted on GitHub Pages and "
            "delivered through Cloudflare. Every request for a page passes "
            "through Cloudflare's network first, so Cloudflare receives "
            "your IP address, your user agent and the address of the page "
            "you asked for. It uses them to deliver the page, to cache it "
            "and to filter out abusive traffic. Cloudflare also adds a "
            "small script of its own to each page, served from "
            "<code>static.cloudflareinsights.com</code> and from "
            "<code>/cdn-cgi/</code> on this domain, which measures page "
            "load performance and helps tell people from automated "
            "traffic. It sets no advertising cookies. Cloudflare's "
            "practices are covered by the Cloudflare Privacy Policy.</p>"
            "<p>GitHub receives the request from Cloudflare as part of "
            "delivering the page and keeps its own server logs, which we do "
            "not have access to. Their practices are covered by the GitHub "
            "Privacy Statement.</p>"
            "<h2>Email</h2>"
            "<p>If you write to us, we receive your email address and "
            "whatever you put in the message. We use them to reply and to "
            "correct the site, and for nothing else. We do not add you to "
            "any list.</p>"
            "<h2>Cookies</h2>"
            + cookies
            + "<h2>Changes</h2>"
            "<p>This page describes the site as it is now. If a third-party "
            "service is added, removed or replaced, the page will be updated "
            "to say so before or at the same time as the change goes live, "
            "and the date below will change with it.</p>"
            "<h2>Contact</h2>"
            f'<p>Questions about any of this: <!--email_off-->'
            f'<a href="mailto:{EMAIL_HTML}">{EMAIL_HTML}</a><!--/email_off-->, '
            f'or see the <a href="/contact/">contact page</a>.</p><p class="muted">Last updated {long_date(CONTENT_UPDATED)}.</p></div>')
    page("/privacy/", "Privacy Policy | PipeData",
         "What PipeData loads and what your browser sends to Google, Ahrefs "
         "and Cloudflare: analytics, advertising cookies, consent and "
         "opt-outs. No accounts, no forms.",
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
               "/404.html", noindex=True, ads=False) + body + foot())


# --------------------------------------------------------------------------
# site plumbing
# --------------------------------------------------------------------------

def sitemap(urls):
    entries = "".join(
        f"<url><loc>{esc(SITE + u)}</loc><lastmod>{CONTENT_UPDATED}</lastmod>"
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
    if ADSENSE_CLIENT:
        write("ads.txt", "google.com, "
              + ADSENSE_CLIENT.removeprefix("ca-")
              + ", DIRECT, f08c47fec0942fa0\n")


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


def audit_links():
    """Fail the build if any internal href="/..." points at a file that
    was never written.

    Runs over the rendered docs/ tree rather than the template source, so it
    catches a stale link regardless of which page generator produced it.
    """
    href_re = re.compile(r'href="(/[^"#?]*)')
    broken = defaultdict(list)
    checked = 0

    for dirpath, _, files in os.walk(OUT):
        for fname in files:
            if not fname.endswith(".html"):
                continue
            src = os.path.join(dirpath, fname)
            page_path = "/" + os.path.relpath(src, OUT).replace(os.sep, "/")
            with open(src, encoding="utf-8") as f:
                for href in href_re.findall(f.read()):
                    checked += 1
                    target = (os.path.join(OUT, href.strip("/"), "index.html")
                              if href.endswith("/") or href == ""
                              else os.path.join(OUT, href.lstrip("/")))
                    if not os.path.isfile(target):
                        broken[href].append(page_path)

    print(f"  internal links: {checked} hrefs checked, {len(broken)} broken")
    if broken:
        print("  BROKEN internal links:", file=sys.stderr)
        for href, pages in broken.items():
            print(f"    {href!r} — linked from {len(pages)} page(s):",
                  file=sys.stderr)
            for p in pages[:5]:
                print(f"        {p}", file=sys.stderr)
        print("\nLink audit failed.", file=sys.stderr)
        sys.exit(1)
    print("  link audit passed (every internal href resolves)")


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
        pipe_page(s, pipes, sizes_by_slug, b165, fittings)
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
            flange_class_page(ft, cls, blk, b165, ftypes, sizes_by_nps)
            if ft in detail_types:
                for r in blk["rows"]:
                    flange_detail_page(ft, cls, r, blk, b165, sizes_by_nps,
                                       ftypes)
        for cls in ft["unpublished"]:
            flange_unpublished_page(ft, cls, ftypes)
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
    ref_face_types(b165)
    ref_color_coding(pipes)
    reference_index()

    ARTICLE_FIGS.update({
        "/compare/weld-neck-vs-slip-on-flange/":
            lambda: fig_flange_pair(["weld-neck", "slip-on"]),
        "/compare/weld-neck-vs-blind-flange/":
            lambda: fig_flange_pair(["weld-neck", "blind"]),
        "/compare/slip-on-vs-threaded-flange/":
            lambda: fig_flange_pair(["slip-on", "threaded"]),
        "/compare/socket-weld-vs-threaded-flange/":
            lambda: fig_flange_pair(["socket-weld", "threaded"]),
        "/compare/lap-joint-vs-slip-on-flange/":
            lambda: fig_flange_pair(["lap-joint", "slip-on"]),
        "/compare/long-radius-vs-short-radius-elbow/":
            lambda: fig_fitting_pair(["90-degree-elbow",
                                      "90-degree-elbow-short-radius"],
                                     fittings),
        "/compare/90-degree-vs-45-degree-elbow/":
            lambda: fig_fitting_pair(["90-degree-elbow", "45-degree-elbow"],
                                     fittings),
        "/compare/concentric-vs-eccentric-reducer/":
            lambda: fig_fitting_pair(["concentric-reducer",
                                      "eccentric-reducer"], fittings),
        "/guides/pipe-schedule-explained/": fig_pipe_section,
        "/guides/pipe-wall-thickness-calculation/": fig_pipe_section,
        "/guides/nps-vs-dn-explained/": fig_pipe_section,
        "/guides/pipe-sizing/": fig_pipe_section,
    })

    # ---- comparisons (order here is the order on the index) ----
    cmp_sched_40_80(pipes)
    cmp_sched_10_40(pipes)
    cmp_sched_40_160(pipes)
    cmp_5s_10s(ss, pipes, ss_cmp)
    cmp_std_xs(pipes)
    cmp_xs_xxs(pipes)
    cmp_wn_so(b165, ftypes)
    cmp_wn_blind(b165, ftypes)
    cmp_so_thd(b165, ftypes)
    cmp_sw_thd(b165, ftypes)
    cmp_lj_so(b165, ftypes)
    cmp_rf_rtj(b165)
    flange_class_comparisons(b165, pt, ftypes)
    cmp_seamless_welded(pipes, mats)
    cmp_cs_ss(pt, mats)
    cmp_a106_a53(mats, pipes)
    cmp_304_316(mats, pt)
    cmp_lr_sr_elbow(fittings, pipes)
    cmp_conc_ecc_reducer(fittings)
    cmp_90_45_elbow(fittings)
    compare_index()

    # ---- guides (order here is the order on the index) ----
    guide_pipe_sizing(pipes)
    guide_wall_thickness(pipes)
    guide_hydrotest(pt, b165)
    guide_pt_derating(pt)
    guide_bolt_torque(b165, ftypes)
    guide_face_types(b165)
    guide_material_selection(mats, pt)
    guide_schedule_explained(pipes)
    guide_nps_dn(pipes)
    guide_end_connections(pipes)
    guides_index()

    # ---- top level ----
    homepage(pipes, ftypes, fittings, b165)
    about_page(pipes, ftypes, fittings)
    contact_page()
    privacy_page()
    not_found()

    audit_descriptions()
    audit_titles()

    search_index()
    copy_static()
    robots()
    webmanifest()

    urls = [("/", "1.0"), ("/pipes/", "0.9"), ("/flanges/", "0.9"),
            ("/fittings/", "0.9"), ("/compare/", "0.9"), ("/guides/", "0.9"),
            ("/reference/", "0.8"),
            ("/flanges/large/", "0.7"),
            ("/about/", "0.4"), ("/contact/", "0.3"), ("/privacy/", "0.2")]
    # Comparison and guide pages are the highest-intent entry points on the
    # site, so they sit above the individual dimension pages in priority.
    urls += [(u, "0.9") for _, u, _ in COMPARE_PAGES]
    urls += [(u, "0.9") for _, u, _ in GUIDE_PAGES]
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
        # A noindex page has no business in the sitemap, and one that was
        # never built has even less. Both are read off what head() emitted.
        if u in seen or u not in DESC_REGISTRY or DESC_REGISTRY[u]["noindex"]:
            continue
        seen.add(u)
        deduped.append((u, p))
    sitemap(deduped)

    audit_links()

    print(f"Built {len(DESC_REGISTRY)} pages, {len(deduped)} sitemap URLs, "
          f"{len(SEARCH)} search entries → {OUT}")


if __name__ == "__main__":
    main()
