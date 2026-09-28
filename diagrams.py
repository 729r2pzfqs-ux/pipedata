"""
Inline SVG diagrams for PipeData.org.

Every figure is drawn from coordinates in this file; nothing is traced or
imported. Two kinds of figure live here:

- Schematic figures (flange sections, fittings, the labelled pipe section) are
  not to scale. They exist to show which dimension a table column measures.
- To-scale figures (pipe walls on the size and schedule pages, the flange face
  on the class pages) are drawn from the numbers in data/, so they change from
  page to page and cannot disagree with the table beside them.

Colours and type come from classes defined in static/css/style.css (.dg-*), so
the figures follow the site palette and print cleanly.
"""

import html
import math

# ids for <pattern> elements have to be unique within a page. A counter that
# runs for the whole build is the simplest way to guarantee that, and the build
# order is fixed, so the output is still reproducible.
_SEQ = [0]


def _uid(prefix):
    _SEQ[0] += 1
    return f"{prefix}{_SEQ[0]}"


def _e(s):
    return html.escape(str(s), quote=True)


def _f(v):
    """Coordinate as a short string."""
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def _pts(points):
    return " ".join(f"{_f(x)},{_f(y)}" for x, y in points)


def poly(points, cls):
    return f'<polygon class="{cls}" points="{_pts(points)}"/>'


def line(x1, y1, x2, y2, cls):
    return (f'<line class="{cls}" x1="{_f(x1)}" y1="{_f(y1)}" '
            f'x2="{_f(x2)}" y2="{_f(y2)}"/>')


def text(x, y, s, cls="dg-t", anchor="middle", rotate=None):
    rot = (f' transform="rotate({rotate} {_f(x)} {_f(y)})"'
           if rotate is not None else "")
    return (f'<text class="{cls}" x="{_f(x)}" y="{_f(y)}" '
            f'text-anchor="{anchor}"{rot}>{_e(s)}</text>')


def _arrow(x, y, dx, dy, size=7.0):
    """Filled arrowhead with its tip at (x, y), pointing along (dx, dy)."""
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px, py = -uy, ux
    base_x, base_y = x - ux * size, y - uy * size
    w = size * 0.36
    return poly([(x, y), (base_x + px * w, base_y + py * w),
                 (base_x - px * w, base_y - py * w)], "dg-arrow")


def dim_h(x1, x2, y, label, ext=(), above=True):
    """Horizontal dimension between x1 and x2 at height y.

    ext: y-coordinates the extension lines start from, one per end.
    """
    out = []
    for x, y0 in zip((x1, x2), ext):
        end = y - 5 if y < y0 else y + 5
        out.append(line(x, y0, x, end, "dg-ext"))
    out.append(line(x1, y, x2, y, "dg-dim"))
    out.append(_arrow(x1, y, -1, 0))
    out.append(_arrow(x2, y, 1, 0))
    out.append(text((x1 + x2) / 2, y - 6 if above else y + 16, label))
    return "".join(out)


def dim_v(y1, y2, x, label, ext=(), left=True):
    """Vertical dimension between y1 and y2 at position x, label rotated."""
    out = []
    for y, x0 in zip((y1, y2), ext):
        end = x - 5 if x < x0 else x + 5
        out.append(line(x0, y, end, y, "dg-ext"))
    out.append(line(x, y1, x, y2, "dg-dim"))
    out.append(_arrow(x, y1, 0, -1))
    out.append(_arrow(x, y2, 0, 1))
    if len(label) <= 2:
        # a single letter reads better upright beside the line
        out.append(text(x - 12 if left else x + 12, (y1 + y2) / 2 + 5, label))
    else:
        tx = x - 7 if left else x + 17
        out.append(text(tx, (y1 + y2) / 2, label, rotate=-90))
    return "".join(out)


def leader(x, y, tx, ty, label, anchor="start"):
    """A note with a leader line from the text to the feature at (x, y)."""
    return (line(tx, ty, x, y, "dg-dim") + _arrow(x, y, x - tx, y - ty, 6)
            + text(tx + (5 if anchor == "start" else -5), ty + 4, label,
                   anchor=anchor))


def centreline(x1, y1, x2, y2):
    return line(x1, y1, x2, y2, "dg-cl")


def hatch_def(pid, angle=45):
    return (f'<defs><pattern id="{pid}" width="7" height="7" '
            f'patternUnits="userSpaceOnUse" '
            f'patternTransform="rotate({angle})">'
            '<line class="dg-hatchline" x1="0" y1="0" x2="0" y2="7"/>'
            "</pattern></defs>")


def svg(w, h, body, title, desc):
    tid, did = _uid("dgt"), _uid("dgd")
    return (f'<svg class="dg" viewBox="0 0 {w} {h}" role="img" '
            f'aria-labelledby="{tid} {did}" '
            'xmlns="http://www.w3.org/2000/svg">'
            f'<title id="{tid}">{_e(title)}</title>'
            f'<desc id="{did}">{_e(desc)}</desc>{body}</svg>')


def figure(svgs, caption, cls=""):
    """One or more SVGs in a row, with a caption. caption may contain HTML."""
    cells = "".join(f'<div class="diagram-cell">{s}</div>' for s in svgs)
    extra = f" {cls}" if cls else ""
    return (f'<figure class="diagram{extra}"><div class="diagram-row">{cells}'
            f'</div><figcaption>{caption}</figcaption></figure>')


# --------------------------------------------------------------------------
# flanges — schematic sections
# --------------------------------------------------------------------------

FLANGE_TITLES = {
    "weld-neck": "Weld neck flange",
    "slip-on": "Slip-on flange",
    "socket-weld": "Socket weld flange",
    "lap-joint": "Lap joint flange",
    "threaded": "Threaded flange",
    "blind": "Blind flange",
}

FLANGE_DESCS = {
    "weld-neck": "Section through a weld neck flange. A long tapered hub runs "
                 "from the back of the flange down to the pipe wall and is "
                 "joined to the pipe by a single butt weld. The bore of the "
                 "flange matches the bore of the pipe.",
    "slip-on": "Section through a slip-on flange. The pipe passes through the "
               "bore of the flange and stops short of the face. One fillet "
               "weld joins the hub to the outside of the pipe and a second "
               "joins the pipe end to the bore.",
    "socket-weld": "Section through a socket weld flange. The pipe sits in a "
                   "counterbored socket, held back from the shoulder by a "
                   "small gap, and is joined by one fillet weld at the hub.",
    "lap-joint": "Section through a lap joint flange. The flange is loose on "
                 "the pipe and bears against the lap of a stub end, which is "
                 "butt-welded to the pipe and forms the gasket face.",
    "threaded": "Section through a threaded flange. The bore carries a taper "
                "pipe thread and the pipe screws into it. There is no weld.",
    "blind": "Section through a blind flange. It is a solid disc with a "
             "raised face and bolt holes, and no bore.",
}

# Geometry shared by every flange section, in drawing units. r is measured
# from the pipe centreline.
_CY = 205           # centreline
_XF = 150           # front of the raised face
_RFH = 9            # raised face height
_T = 46             # flange thickness
_RO = 140           # flange outside radius
_RBC = 109          # bolt circle radius
_RH = 10            # bolt hole radius
_RRF = 78           # raised face radius
_RB = 40            # pipe bore radius
_RP = 50            # pipe outside radius
_W, _H = 520, 410


def _mirror(points):
    """Upper and lower copies of a half-section given as (x, r) points."""
    return ([(x, _CY - r) for x, r in points],
            [(x, _CY + r) for x, r in points])


def _metal(points, pid, cls="dg-metal"):
    up, lo = _mirror(points)
    out = ""
    for p in (up, lo):
        out += poly(p, cls)
        out += (f'<polygon points="{_pts(p)}" fill="url(#{pid})" '
                'stroke="none"/>')
        out += poly(p, "dg-outline")
    return out


def _plain(points, cls):
    up, lo = _mirror(points)
    return poly(up, cls) + poly(lo, cls)


def _bolt_holes(x1, x2):
    out = ""
    for sign in (-1, 1):
        y = _CY + sign * _RBC
        out += (f'<rect class="dg-hole" x="{_f(x1)}" y="{_f(y - _RH)}" '
                f'width="{_f(x2 - x1)}" height="{_f(2 * _RH)}"/>')
        out += line(x1, y - _RH, x2, y - _RH, "dg-edge")
        out += line(x1, y + _RH, x2, y + _RH, "dg-edge")
        out += centreline(x1 - 14, y, x2 + 14, y)
    return out


def _pipe(x1, x2, r_in=_RB, r_out=_RP):
    """Pipe wall from x1 to x2 with a break line at the far end."""
    out = _plain([(x1, r_in), (x1, r_out), (x2, r_out), (x2, r_in)], "dg-pipe")
    # break line
    out += (f'<path class="dg-edge" d="M{_f(x2)} {_f(_CY - r_out - 8)} '
            f'q 9 {_f((r_out + 8) / 2)} 0 {_f(r_out + 8)} '
            f'q -9 {_f((r_out + 8) / 2)} 0 {_f(r_out + 8)}"/>')
    return out


def _weld(points):
    return _plain(points, "dg-weld")


def flange_section(slug, labels=True):
    """Schematic section of one flange type, axis horizontal, face to the left."""
    pid = _uid("dgh")
    back = _XF + _RFH + _T          # back of the flange ring
    face = _XF + _RFH               # flange face behind the raised face
    body = hatch_def(pid)
    notes = ""
    hub_end = None
    bore_r = _RB

    if slug == "weld-neck":
        hub_end = 326
        metal = [(_XF, _RB), (_XF, _RRF), (face, _RRF), (face, _RO),
                 (back, _RO), (back, 74), (296, _RP), (hub_end - 8, _RP),
                 (hub_end, _RB + 3), (hub_end, _RB)]
        body += _pipe(hub_end + 6, 480)
        body += _metal(metal, pid)
        body += _weld([(hub_end - 8, _RP), (hub_end, _RB + 3),
                       (hub_end + 6, _RB + 3), (hub_end + 14, _RP),
                       (hub_end + 3, _RP + 5)])
        notes += leader(hub_end + 3, _CY - _RP - 4, 372, 84, "Butt weld")
        notes += leader(262, _CY - 62, 300, 50, "Tapered hub")
    elif slug == "slip-on":
        hub_end = 236
        bore_r = _RP + 1.5
        metal = [(_XF, bore_r), (_XF, _RRF), (face, _RRF), (face, _RO),
                 (back, _RO), (back, 74), (hub_end, 68), (hub_end, bore_r)]
        body += _pipe(_XF + 12, 480)
        body += _metal(metal, pid)
        body += _weld([(hub_end, _RP), (hub_end, _RP + 13),
                       (hub_end + 13, _RP)])
        body += _weld([(_XF + 12, _RB), (_XF + 12, _RP), (_XF, _RP),
                       (_XF + 2, _RP - 1)])
        notes += leader(hub_end + 5, _CY - _RP - 6, 300, 60,
                        "Fillet weld, outside")
        notes += leader(_XF + 6, _CY + _RP - 4, 300, 352,
                        "Fillet weld, inside")
    elif slug == "socket-weld":
        hub_end = 236
        shoulder = _XF + 30
        sock_r = _RP + 1.5
        metal = [(_XF, _RB), (_XF, _RRF), (face, _RRF), (face, _RO),
                 (back, _RO), (back, 74), (hub_end, 68), (hub_end, sock_r),
                 (shoulder, sock_r), (shoulder, _RB)]
        body += _pipe(shoulder + 5, 480)
        body += _metal(metal, pid)
        body += _weld([(hub_end, _RP), (hub_end, _RP + 13),
                       (hub_end + 13, _RP)])
        notes += leader(hub_end + 5, _CY - _RP - 6, 300, 60, "Fillet weld")
        notes += leader(shoulder + 2.5, _CY + _RB + 5, 300, 352,
                        "Expansion gap")
    elif slug == "lap-joint":
        hub_end = 236
        bore_r = _RP + 2.5
        lap = 10                      # thickness of the stub end lap
        # the flange itself is flat faced and sits behind the lap
        metal = [(_XF + lap, bore_r + 7), (_XF + lap, _RO),
                 (_XF + lap + _T, _RO), (_XF + lap + _T, 74),
                 (hub_end + lap, 68), (hub_end + lap, bore_r),
                 (_XF + lap + 7, bore_r)]
        stub_end = 330
        stub = [(_XF, _RB), (_XF, _RRF), (_XF + lap, _RRF),
                (_XF + lap, _RP + 8), (_XF + lap + 8, _RP),
                (stub_end - 8, _RP), (stub_end, _RB + 3), (stub_end, _RB)]
        body += _pipe(stub_end + 6, 480)
        body += _metal(metal, pid)
        sid = _uid("dgh")
        body += hatch_def(sid, angle=-45)
        body += _metal(stub, sid, cls="dg-metal2")
        body += _weld([(stub_end - 8, _RP), (stub_end, _RB + 3),
                       (stub_end + 6, _RB + 3), (stub_end + 14, _RP),
                       (stub_end + 3, _RP + 5)])
        notes += leader(stub_end + 3, _CY - _RP - 4, 376, 84, "Butt weld")
        notes += leader(286, _CY - _RP - 1, 300, 50, "Stub end")
        notes += leader(_XF + 5, _CY - _RRF + 6, 128, 24, "Lap",
                        anchor="end")
        face = _XF + lap
        back = face + _T
        hub_end = hub_end + lap
    elif slug == "threaded":
        hub_end = 236
        bore_r = _RP
        metal = [(_XF, _RP - 3), (_XF, _RRF), (face, _RRF), (face, _RO),
                 (back, _RO), (back, 74), (hub_end, 68), (hub_end, _RP + 1)]
        body += _pipe(_XF + 18, 480)
        body += _metal(metal, pid)
        # thread, drawn as a zigzag along the joint between pipe and bore
        for sign in (-1, 1):
            d = f"M{_f(_XF + 18)} {_f(_CY + sign * (_RP - 2))}"
            x = _XF + 18
            up = True
            while x < hub_end:
                x += 4
                r = (_RP + 2) if up else (_RP - 2)
                d += f" L{_f(min(x, hub_end))} {_f(_CY + sign * r)}"
                up = not up
            body += f'<path class="dg-thread" d="{d}"/>'
        notes += leader(200, _CY - _RP - 3, 300, 60, "Taper pipe thread")
    elif slug == "blind":
        metal = [(_XF, 0), (_XF, _RRF), (face, _RRF), (face, _RO),
                 (back, _RO), (back, 0)]
        body += _metal(metal, pid)
        bore_r = None
    else:
        raise ValueError(slug)

    body += _bolt_holes(face, back)
    width = _W if slug != "blind" else 330
    body += centreline(100, _CY, width - 20, _CY)

    if labels:
        left = _XF if slug != "lap-joint" else _XF
        # outside diameter and bolt circle, stacked to the left
        body += dim_v(_CY - _RO, _CY + _RO, 34, "Flange OD",
                      ext=(face, face))
        body += dim_v(_CY - _RBC, _CY + _RBC, 66, "Bolt circle",
                      ext=(face - 14, face - 14))
        body += dim_v(_CY - _RRF, _CY + _RRF, 98,
                      "Lap OD" if slug == "lap-joint" else "Raised face OD",
                      ext=(left, left))
        # thickness above
        body += dim_h(face, back, 36, "Thickness",
                      ext=(_CY - _RO, _CY - _RO))
        # length through hub below
        if hub_end:
            body += dim_h(_XF, hub_end, 388, "Length through hub",
                          ext=(_CY + _RRF, _CY + (68 if slug != "weld-neck"
                                                  else _RP)), above=False)
        else:
            body += dim_h(_XF, face, 388, "Raised face", above=False,
                          ext=(_CY + _RRF, _CY + _RO))
        if slug == "weld-neck":
            # the flange bore and the pipe bore are the same surface
            body += dim_v(_CY - _RB, _CY + _RB, 430, "Bore = pipe ID",
                          left=False)
        elif bore_r:
            # every other type is bored to clear the outside of the pipe
            body += dim_v(_CY - _RP, _CY + _RP, 440, "Pipe OD", left=False)
    if labels:
        body += notes
    return svg(width, _H, body, FLANGE_TITLES[slug] + ", section",
               FLANGE_DESCS[slug])


def flange_face(od, bc, rf, bolts, hole, bore=None, title="Flange face",
                note=None, schematic=False):
    """Flange seen from the front, drawn to scale from real dimensions.

    od, bc, rf, hole and bore are in inches; bolts is the bolt count. The bolt
    holes straddle the centrelines, as ASME B16.5 requires.
    """
    W, H = 420, 410
    cx, cy = 210, 200
    k = 165.0 / (od / 2.0)
    body = (f'<circle class="dg-metal" cx="{cx}" cy="{cy}" r="{_f(od / 2 * k)}"/>'
            f'<circle class="dg-outline" cx="{cx}" cy="{cy}" '
            f'r="{_f(od / 2 * k)}"/>')
    if rf:
        body += (f'<circle class="dg-face" cx="{cx}" cy="{cy}" '
                 f'r="{_f(rf / 2 * k)}"/>')
    if bore:
        body += (f'<circle class="dg-hole dg-edge" cx="{cx}" cy="{cy}" '
                 f'r="{_f(bore / 2 * k)}"/>')
    body += (f'<circle class="dg-cl" fill="none" cx="{cx}" cy="{cy}" '
             f'r="{_f(bc / 2 * k)}"/>')
    step = 2 * math.pi / bolts
    for i in range(bolts):
        a = step / 2 + i * step          # straddle the centrelines
        x = cx + bc / 2 * k * math.sin(a)
        y = cy - bc / 2 * k * math.cos(a)
        body += (f'<circle class="dg-hole dg-edge" cx="{_f(x)}" cy="{_f(y)}" '
                 f'r="{_f(hole / 2 * k)}"/>')
    body += centreline(cx - 185, cy, cx + 185, cy)
    body += centreline(cx, cy - 185, cx, cy + 185)
    # angle between holes
    a0, a1 = step / 2, step / 2 + step
    rr = bc / 2 * k
    if bolts <= 12:
        body += line(cx, cy, cx + rr * math.sin(a0), cy - rr * math.cos(a0),
                     "dg-ext")
        body += line(cx, cy, cx + rr * math.sin(a1), cy - rr * math.cos(a1),
                     "dg-ext")
        am = (a0 + a1) / 2
        ra = rr * 0.45
        body += text(cx + ra * math.sin(am), cy - ra * math.cos(am) + 4,
                     f"{360 / bolts:g}°")
    if note:
        body += text(cx, 398, note, cls="dg-t dg-note")
    return svg(W, H, body, title,
               f"Front view of a flange with {bolts} bolt holes set evenly "
               f"on the bolt circle, {360 / bolts:g} degrees apart, with the "
               "holes straddling the vertical and horizontal centrelines.")


# --------------------------------------------------------------------------
# pipe
# --------------------------------------------------------------------------

def pipe_section_labelled():
    """The three dimensions that define a pipe: OD, wall and bore."""
    W, H = 460, 380
    cx, cy, ro, ri = 215, 195, 140, 108
    pid = _uid("dgh")
    body = hatch_def(pid)
    ring = (f'M{cx - ro} {cy} a{ro} {ro} 0 1 0 {2 * ro} 0 '
            f'a{ro} {ro} 0 1 0 {-2 * ro} 0 Z '
            f'M{cx - ri} {cy} a{ri} {ri} 0 1 1 {2 * ri} 0 '
            f'a{ri} {ri} 0 1 1 {-2 * ri} 0 Z')
    body += f'<path class="dg-metal" fill-rule="evenodd" d="{ring}"/>'
    body += (f'<path fill="url(#{pid})" stroke="none" fill-rule="evenodd" '
             f'd="{ring}"/>')
    body += f'<circle class="dg-outline" cx="{cx}" cy="{cy}" r="{ro}"/>'
    body += f'<circle class="dg-outline" cx="{cx}" cy="{cy}" r="{ri}"/>'
    body += centreline(cx - ro - 22, cy, cx + ro + 22, cy)
    body += centreline(cx, cy - ro - 22, cx, cy + ro + 22)
    body += dim_h(cx - ro, cx + ro, 26, "Outside diameter (OD)",
                  ext=(cy, cy))
    body += dim_h(cx - ri, cx + ri, cy + 58, "Inside diameter (ID)")
    # wall, on the right at 3 o'clock
    body += line(cx + ri, cy - 46, cx + ri, cy, "dg-ext")
    body += line(cx + ro, cy - 46, cx + ro, cy, "dg-ext")
    body += line(cx + ri - 26, cy - 40, cx + ro + 26, cy - 40, "dg-dim")
    body += _arrow(cx + ri, cy - 40, 1, 0)
    body += _arrow(cx + ro, cy - 40, -1, 0)
    body += text(cx + ro + 30, cy - 36, "Wall (t)", anchor="start")
    body += text(cx, 366, "ID = OD − 2t", cls="dg-t dg-note")
    return svg(W, H, body, "Pipe cross-section",
               "Cross-section of a pipe showing the outside diameter across "
               "the whole pipe, the inside diameter across the bore, and the "
               "wall thickness between them. The inside diameter equals the "
               "outside diameter less twice the wall.")


def pipe_walls(items, od_label=None):
    """Pipe sections side by side, each drawn to scale.

    items: list of (heading, od, wall, line2) where od and wall are in inches
    and line2 is the text printed under the heading. Every ring is drawn at
    the same outside radius, so what the eye compares is wall against
    diameter.
    """
    n = len(items)
    cell = 190
    W, H = cell * n, 250
    R = 78.0
    body = ""
    for i, (heading, od, wall, line2) in enumerate(items):
        cx, cy = cell * i + cell / 2, 100
        ri = R * (od - 2 * wall) / od
        ring = (f'M{_f(cx - R)} {cy} a{R} {R} 0 1 0 {2 * R} 0 '
                f'a{R} {R} 0 1 0 {-2 * R} 0 Z '
                f'M{_f(cx - ri)} {cy} a{_f(ri)} {_f(ri)} 0 1 1 {_f(2 * ri)} 0 '
                f'a{_f(ri)} {_f(ri)} 0 1 1 {_f(-2 * ri)} 0 Z')
        body += f'<path class="dg-solid" fill-rule="evenodd" d="{ring}"/>'
        body += centreline(cx - R - 10, cy, cx + R + 10, cy)
        body += centreline(cx, cy - R - 10, cx, cy + R + 10)
        body += text(cx, 208, heading, cls="dg-t dg-head")
        body += text(cx, 228, line2)
    desc = "; ".join(f"{h}: {l2}" for h, _, _, l2 in items)
    return svg(W, H, body,
               "Pipe wall thickness to scale"
               + (f", {od_label}" if od_label else ""),
               "Pipe cross-sections drawn to scale at the same outside "
               "diameter so the wall thicknesses can be compared. " + desc)


# --------------------------------------------------------------------------
# buttweld fittings — schematic outlines
# --------------------------------------------------------------------------

_FR = 30        # half the pipe diameter in the fitting figures


def _arc_band(cx, cy, R, r, a0, a1):
    """Closed path for a bent pipe: centreline radius R, pipe radius r,
    from angle a0 to a1 in degrees, measured clockwise from 12 o'clock."""
    def p(rad, a):
        t = math.radians(a)
        return cx + rad * math.sin(t), cy - rad * math.cos(t)
    ro, ri = R + r, R - r
    x0, y0 = p(ro, a0)
    x1, y1 = p(ro, a1)
    x2, y2 = p(ri, a1)
    x3, y3 = p(ri, a0)
    big = 1 if abs(a1 - a0) > 180 else 0
    return (f'M{_f(x0)} {_f(y0)} A{_f(ro)} {_f(ro)} 0 {big} 1 {_f(x1)} {_f(y1)} '
            f'L{_f(x2)} {_f(y2)} A{_f(ri)} {_f(ri)} 0 {big} 0 {_f(x3)} {_f(y3)} Z')


def _arc_cl(cx, cy, R, a0, a1):
    t0, t1 = math.radians(a0), math.radians(a1)
    x0, y0 = cx + R * math.sin(t0), cy - R * math.cos(t0)
    x1, y1 = cx + R * math.sin(t1), cy - R * math.cos(t1)
    big = 1 if abs(a1 - a0) > 180 else 0
    return (f'<path class="dg-cl" fill="none" d="M{_f(x0)} {_f(y0)} '
            f'A{_f(R)} {_f(R)} 0 {big} 1 {_f(x1)} {_f(y1)}"/>')


def _elbow90(R, title, desc, radius_note):
    W, H = 440, 380
    r = _FR
    # centre of curvature bottom-left; elbow runs from 12 o'clock (end facing
    # left... ) round to 3 o'clock
    cx, cy = 120, 300
    body = f'<path class="dg-solid" d="{_arc_band(cx, cy, R, r, 0, 90)}"/>'
    body += _arc_cl(cx, cy, R, 0, 90)
    # end faces: one at the top (on the vertical through the centre) facing
    # left, one at the right (on the horizontal) facing down
    top = (cx, cy - R)
    right = (cx + R, cy)
    corner = (cx + R, cy - R)          # where the two end axes meet
    body += centreline(cx - 30, cy - R, corner[0] + 30, cy - R)
    body += centreline(cx + R, cy + 30, cx + R, corner[1] - 30)
    body += line(top[0], top[1] - r, top[0], top[1] + r, "dg-edge2")
    body += line(right[0] - r, right[1], right[0] + r, right[1], "dg-edge2")
    body += dim_h(top[0], corner[0], cy - R - r - 34, "A",
                  ext=(top[1] - r, corner[1]))
    body += dim_v(corner[1], right[1], cx + R + r + 40, "A",
                  ext=(corner[0], right[0] + r), left=False)
    # radius
    a = math.radians(45)
    body += line(cx, cy, cx + R * math.sin(a), cy - R * math.cos(a), "dg-dim")
    body += _arrow(cx + R * math.sin(a), cy - R * math.cos(a),
                   math.sin(a), -math.cos(a))
    body += text(cx + 12, cy + 22, radius_note, anchor="start")
    body += f'<circle class="dg-arrow" cx="{cx}" cy="{cy}" r="2.5"/>'
    return svg(W, H, body, title, desc)


def fitting(slug):
    r = _FR
    if slug == "90-degree-elbow":
        return _elbow90(
            165, "90° long radius elbow",
            "Outline of a 90 degree long radius elbow. Dimension A runs from "
            "the point where the two end centrelines cross to the face of "
            "each end, and equals the centreline bend radius of 1.5 times "
            "the nominal pipe size.", "Radius = 1.5 × NPS")
    if slug == "90-degree-elbow-short-radius":
        return _elbow90(
            110, "90° short radius elbow",
            "Outline of a 90 degree short radius elbow. Dimension A runs "
            "from the point where the two end centrelines cross to the face "
            "of each end, and equals the centreline bend radius of 1.0 times "
            "the nominal pipe size.", "Radius = 1.0 × NPS")
    if slug == "45-degree-elbow":
        W, H = 440, 340
        R = 190
        cx, cy = 90, 300
        body = f'<path class="dg-solid" d="{_arc_band(cx, cy, R, r, 0, 45)}"/>'
        body += _arc_cl(cx, cy, R, 0, 45)
        t = math.radians(45)
        top = (cx, cy - R)
        end = (cx + R * math.sin(t), cy - R * math.cos(t))
        B = R * math.tan(math.radians(22.5))
        apex = (cx + B, cy - R)
        body += centreline(cx - 30, cy - R, apex[0] + 60, cy - R)
        # second end axis: through `end`, tangent to the arc
        tx, ty = math.cos(t), math.sin(t)
        body += centreline(apex[0] - tx * 40, apex[1] - ty * 40,
                           end[0] + tx * 40, end[1] + ty * 40)
        body += line(top[0], top[1] - r, top[0], top[1] + r, "dg-edge2")
        body += line(end[0] - ty * r, end[1] + tx * r,
                     end[0] + ty * r, end[1] - tx * r, "dg-edge2")
        body += dim_h(top[0], apex[0], cy - R - r - 30, "B",
                      ext=(top[1] - r, apex[1]))
        # B along the slanted axis, offset outward
        ox, oy = ty * (r + 34), -tx * (r + 34)
        x1, y1 = apex[0] + ox, apex[1] + oy
        x2, y2 = end[0] + ox, end[1] + oy
        body += line(apex[0], apex[1], x1 + ty * 5, y1 - tx * 5, "dg-ext")
        body += line(end[0] + ty * r, end[1] - tx * r, x2 + ty * 5,
                     y2 - tx * 5, "dg-ext")
        body += line(x1, y1, x2, y2, "dg-dim")
        body += _arrow(x1, y1, -tx, -ty) + _arrow(x2, y2, tx, ty)
        body += text((x1 + x2) / 2 + 12, (y1 + y2) / 2 - 8, "B")
        body += text(cx + 10, cy - 60, "45°", anchor="start")
        body += line(cx, cy, cx, cy - R + r, "dg-ext")
        body += line(cx, cy, cx + (R - r) * math.sin(t),
                     cy - (R - r) * math.cos(t), "dg-ext")
        body += f'<circle class="dg-arrow" cx="{cx}" cy="{cy}" r="2.5"/>'
        return svg(W, H, body, "45° long radius elbow",
                   "Outline of a 45 degree long radius elbow. Dimension B "
                   "runs from the point where the two end centrelines cross "
                   "to the face of each end.")
    if slug == "180-degree-return-bend":
        W, H = 470, 360
        R = 95
        cx, cy = 190, 180
        body = (f'<path class="dg-solid" '
                f'd="{_arc_band(cx, cy, R, r, 180, 360)}"/>')
        body += _arc_cl(cx, cy, R, 180, 360)
        # ends lie on the vertical through the centre, facing right
        for y in (cy - R, cy + R):
            body += centreline(cx - R - r - 20, y, cx + 60, y)
            body += line(cx, y - r, cx, y + r, "dg-edge2")
        body += dim_v(cy - R, cy + R, cx + 96, "O",
                      ext=(cx + 60, cx + 60), left=False)
        body += dim_h(cx - R - r, cx, cy + R + r + 40, "K",
                      ext=(cy, cy + R + r), above=False)
        body += text(cx + 128, cy - 8, "O = centre to centre",
                     anchor="start", cls="dg-t dg-note")
        body += text(cx + 128, cy + 12, "K = back to face",
                     anchor="start", cls="dg-t dg-note")
        return svg(W, H, body, "180° long radius return bend",
                   "Outline of a 180 degree return bend. Dimension O is the "
                   "distance between the centrelines of the two ends. "
                   "Dimension K is the distance from the back of the bend to "
                   "the end faces.")
    if slug in ("straight-tee", "reducing-tee", "straight-cross"):
        W, H = 440, 360 if slug != "straight-cross" else 400
        cx = 220
        cy = 230 if slug != "straight-cross" else 200
        C, M = 120, 120
        rb = r if slug != "reducing-tee" else 20     # branch radius
        body = ""
        run = [(cx - C, cy - r), (cx - rb - 14, cy - r),
               (cx - rb, cy - r - 14), (cx - rb, cy - M), (cx + rb, cy - M),
               (cx + rb, cy - r - 14), (cx + rb + 14, cy - r),
               (cx + C, cy - r)]
        if slug == "straight-cross":
            lower = [(cx + C, cy + r), (cx + rb + 14, cy + r),
                     (cx + rb, cy + r + 14), (cx + rb, cy + M),
                     (cx - rb, cy + M), (cx - rb, cy + r + 14),
                     (cx - rb - 14, cy + r), (cx - C, cy + r)]
        else:
            lower = [(cx + C, cy + r), (cx - C, cy + r)]
        body += poly(run + lower, "dg-solid")
        body += centreline(cx - C - 30, cy, cx + C + 30, cy)
        body += centreline(cx, cy - M - 30, cx,
                           cy + (M + 30 if slug == "straight-cross" else r + 24))
        for x in (cx - C, cx + C):
            body += line(x, cy - r, x, cy + r, "dg-edge2")
        body += line(cx - rb, cy - M, cx + rb, cy - M, "dg-edge2")
        if slug == "straight-cross":
            body += line(cx - rb, cy + M, cx + rb, cy + M, "dg-edge2")
        ybase = cy + (M if slug == "straight-cross" else r) + 40
        body += dim_h(cx - C, cx, ybase, "C", above=False,
                      ext=(cy + r, cy + (M if slug == "straight-cross" else r)))
        body += dim_h(cx, cx + C, ybase, "C", above=False,
                      ext=(cy + (M if slug == "straight-cross" else r), cy + r))
        body += dim_v(cy - M, cy, cx + C + 40,
                      "M" if slug != "straight-cross" else "C",
                      ext=(cx + rb, cx + C), left=False)
        names = {"straight-tee": "Straight tee",
                 "reducing-tee": "Reducing tee",
                 "straight-cross": "Straight cross"}
        descs = {
            "straight-tee": "Outline of a straight tee. Dimension C runs "
                            "from the centre of the fitting to the face of "
                            "each run end. Dimension M runs from the centre "
                            "to the face of the outlet.",
            "reducing-tee": "Outline of a reducing tee, with an outlet "
                            "smaller than the run. Dimension C runs from the "
                            "centre to the face of each run end. Dimension M "
                            "runs from the centre to the face of the outlet.",
            "straight-cross": "Outline of a straight cross, with four ends "
                              "of the same size. Dimension C runs from the "
                              "centre of the fitting to the face of each "
                              "end.",
        }
        return svg(W, H, body, names[slug], descs[slug])
    if slug in ("concentric-reducer", "eccentric-reducer"):
        W, H = 440, 340
        x1, x2 = 110, 330
        cy = 150
        big, small = 66, 34
        if slug == "concentric-reducer":
            pts = [(x1, cy - big), (x1 + 30, cy - big),
                   (x2 - 30, cy - small), (x2, cy - small),
                   (x2, cy + small), (x2 - 30, cy + small),
                   (x1 + 30, cy + big), (x1, cy + big)]
            body = poly(pts, "dg-solid")
            body += centreline(x1 - 30, cy, x2 + 30, cy)
            ext = (cy + big, cy + small)
        else:
            top = cy - big
            pts = [(x1, top), (x2, top), (x2, top + 2 * small),
                   (x2 - 30, top + 2 * small), (x1 + 30, cy + big),
                   (x1, cy + big)]
            body = poly(pts, "dg-solid")
            body += centreline(x1 - 30, cy, x1 + 90, cy)
            body += centreline(x2 - 90, top + small, x2 + 30, top + small)
            body += leader(x1 + 110, top, x1 + 60, top - 34, "Flat side",
                           anchor="end")
            ext = (cy + big, top + 2 * small)
        body += line(x1, pts[0][1], x1, cy + big, "dg-edge2")
        body += dim_h(x1, x2, cy + big + 44, "H", ext=ext, above=False)
        body += text(x1, cy + big + 92, "Large end", anchor="middle",
                     cls="dg-t dg-note")
        body += text(x2, cy + big + 92, "Small end", anchor="middle",
                     cls="dg-t dg-note")
        if slug == "concentric-reducer":
            return svg(W, H, body, "Concentric reducer",
                       "Outline of a concentric reducer. Both ends share one "
                       "centreline. Dimension H is the length from end face "
                       "to end face.")
        return svg(W, H, body, "Eccentric reducer",
                   "Outline of an eccentric reducer. One side is flat, so "
                   "the centrelines of the two ends are offset. Dimension H "
                   "is the length from end face to end face.")
    if slug == "cap":
        W, H = 440, 320
        x1 = 130
        cy = 150
        rr = 70
        straight = 70
        d = (f'M{x1} {cy - rr} L{x1 + straight} {cy - rr} '
             f'A{60} {rr} 0 0 1 {x1 + straight} {cy + rr} '
             f'L{x1} {cy + rr} Z')
        body = f'<path class="dg-solid" d="{d}"/>'
        body += centreline(x1 - 30, cy, x1 + straight + 90, cy)
        body += line(x1, cy - rr, x1, cy + rr, "dg-edge2")
        body += dim_h(x1, x1 + straight + 60, cy + rr + 44, "E",
                      ext=(cy + rr, cy), above=False)
        body += leader(x1 + 2, cy - rr + 14, x1 - 20, cy - rr - 30,
                       "Welding end", anchor="end")
        return svg(W, H, body, "Buttweld cap",
                   "Outline of a buttweld cap. Dimension E is the overall "
                   "length from the welding end to the crown of the cap.")
    raise ValueError(slug)
