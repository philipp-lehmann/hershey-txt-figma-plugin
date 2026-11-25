"""
Convert an outline font (TTF/OTF) into a single-stroke SVG font, in the
same format as the Hershey/EMS fonts in /fonts.

Each glyph is rasterised, thinned to its centreline (skeletonize), traced
into polylines and cleaned up (spur pruning, junction merging, straight
terminals, smoothing, simplification), then written back in font units.

Normally run via build_fonts.py (configured in fonts.json). Standalone:
    python outline_to_stroke.py MyFont.ttf ../fonts/MyFont.svg --name "My Font"

Requires: fonttools, numpy, scipy, scikit-image, pillow
"""
import argparse
import math

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from scipy import ndimage
from skimage.morphology import skeletonize

RES = 2.0       # pixels per font unit
PAD = 40        # raster padding in pixels
# ASCII, Latin-1 and common punctuation; glyphs missing from a font are skipped
CHARS = ([chr(c) for c in range(33, 127)] + [chr(c) for c in range(161, 256) if c != 173]
         + list('ŒœŸĂăĞğİıŞş–—‘’‚“”„•…‹›€™'))


# --- outline flattening -------------------------------------------------------

class FlattenPen(BasePen):
    """Collects glyph contours as lists of points, flattening curves."""

    def __init__(self, glyphset, steps=16):
        super().__init__(glyphset)
        self.contours, self.cur, self.steps = [], [], steps

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append(tuple(mt**3 * a + 3 * mt * mt * t * b + 3 * mt * t * t * c + t**3 * d
                                  for a, b, c, d in zip(p0, p1, p2, p3)))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append(tuple(mt * mt * a + 2 * mt * t * b + t * t * c for a, b, c in zip(p0, p1, p2)))

    def _closePath(self):
        if len(self.cur) > 2:
            self.contours.append(self.cur)
        self.cur = []

    _endPath = _closePath


def rasterise(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    x0, y0 = min(xs), min(ys)
    w = int((max(xs) - x0) * RES) + 2 * PAD
    h = int((max(ys) - y0) * RES) + 2 * PAD

    def to_px(p):  # font units (y up) -> pixels (y down)
        return ((p[0] - x0) * RES + PAD, h - ((p[1] - y0) * RES + PAD))

    # Nonzero-winding scanline fill. Variable fonts often have overlapping
    # and self-intersecting contours (corners, crossings); these must stay
    # filled, which PIL's polygon fill (even-odd) does not do.
    x_a, y_a, x_b, y_b = [], [], [], []
    for c in contours:
        pts = [to_px(p) for p in c]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:] + pts[:1]):
            if ya != yb:
                x_a.append(xa); y_a.append(ya); x_b.append(xb); y_b.append(yb)
    x_a, y_a, x_b, y_b = map(np.array, (x_a, y_a, x_b, y_b))

    diff = np.zeros((h, w + 1), np.int32)
    lo, hi = np.minimum(y_a, y_b), np.maximum(y_a, y_b)
    direction = np.where(y_b > y_a, 1, -1)
    for i in range(len(x_a)):
        rows = np.arange(max(int(np.ceil(lo[i] - 0.5)), 0), min(int(np.ceil(hi[i] - 0.5)), h))
        if len(rows) == 0:
            continue
        yc = rows + 0.5  # pixel centres
        xc = x_a[i] + (yc - y_a[i]) * (x_b[i] - x_a[i]) / (y_b[i] - y_a[i])
        cols = np.clip(np.ceil(xc - 0.5).astype(int), 0, w)
        np.add.at(diff, (rows, cols), direction[i])
    winding = np.cumsum(diff[:, :w], axis=1)

    def to_units(r, c):
        return ((c - PAD) / RES + x0, (h - r - PAD) / RES + y0)

    return np.ascontiguousarray(winding != 0), to_units


# --- skeleton graph -----------------------------------------------------------

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def neighbours(sk, p):
    r, c = p
    return [(r + dr, c + dc) for dr, dc in NB
            if 0 <= r + dr < sk.shape[0] and 0 <= c + dc < sk.shape[1] and sk[r + dr, c + dc]]


def trace_edges(sk):
    """Split the skeleton into edges between nodes (pixels with degree != 2)."""
    pix = set(zip(*np.nonzero(sk)))
    deg = {p: len(neighbours(sk, p)) for p in pix}
    nodes = {p for p in pix if deg[p] != 2}

    # collapse clusters of adjacent junction pixels into one node
    node_id, nid = {}, 0
    for p in nodes:
        if p in node_id or deg[p] < 3:
            continue
        stack = [p]
        node_id[p] = nid
        while stack:
            q = stack.pop()
            for n in neighbours(sk, q):
                if n in nodes and deg[n] >= 3 and n not in node_id:
                    node_id[n] = nid
                    stack.append(n)
        nid += 1
    for p in nodes:
        if p not in node_id:
            node_id[p] = nid
            nid += 1

    edges, visited = [], set()
    for p in nodes:
        for n in neighbours(sk, p):
            if n in nodes:
                if node_id[n] != node_id[p] and (n, p) not in visited:
                    visited.add((p, n))
                    edges.append([p, n])
                continue
            if (p, n) in visited:
                continue
            path, prev, cur = [p, n], p, n
            while cur not in nodes:
                nxt = [q for q in neighbours(sk, cur) if q != prev]
                # prefer non-diagonal step to stay on the thin line
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                path.append(cur)
            visited.add((path[-1], path[-2]))
            visited.add((p, n))
            edges.append(path)

    # pure loops (e.g. 'O' without junctions)
    used = {q for e in edges for q in e}
    for p in pix:
        if p in used or p in nodes:
            continue
        path, prev, cur = [p], None, p
        while True:
            used.add(cur)
            nxt = [q for q in neighbours(sk, cur) if q != prev and q not in used]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur)
        path.append(p)
        edges.append(path)

    return edges, node_id


def build_graph(edges, node_id):
    """Graph keyed by node id; edges as pixel polylines."""
    E = []
    for e in edges:
        a = node_id.get(e[0], ('free', id(e)))
        b = node_id.get(e[-1], ('free', id(e)))
        if a == b and len(e) < 4:
            continue
        E.append({'a': a, 'b': b, 'pts': e})
    return E


def degrees(E):
    d = {}
    for e in E:
        d[e['a']] = d.get(e['a'], 0) + 1
        d[e['b']] = d.get(e['b'], 0) + 1
    return d


def prune_spurs(E, width_at, stroke_width, factor=1.2):
    """
    Remove short dangling branches created by corners and terminals.
    All spurs at a node are removed in the same pass, so the two corner
    branches of a flat stroke end disappear together and leave a clean,
    straight end instead of a bend towards one corner.
    """
    while True:
        d = degrees(E)
        spurs = []
        for e in E:
            if e['a'] == e['b']:
                continue
            for end, other, tip, anchor in (('a', 'b', 0, -1), ('b', 'a', -1, 0)):
                base = width_at(e['pts'][anchor])
                # corner artefacts run out into a sharp outline corner (thickness
                # drops towards zero); real short strokes (the crossbar of t, the
                # foot of u) end with full thickness and are kept
                if (d[e[end]] == 1 and d[e[other]] >= 3 and len(e['pts']) < factor * base
                        and width_at(e['pts'][tip]) < 0.5 * stroke_width):
                    spurs.append(e)
                    break
        if not spurs:
            return E
        # never remove every branch of a node: keep the longest one
        by_node = {}
        for e in spurs:
            node = e['b'] if d[e['a']] == 1 else e['a']
            by_node.setdefault(node, []).append(e)
        remove = []
        for node, es in by_node.items():
            if len(es) >= d[node]:
                es = sorted(es, key=lambda e: len(e['pts']))[:-1]
            remove += es
        if not remove:
            return E
        E = [e for e in E if not any(e is r for r in remove)]

def merge_chains(E):
    """Join edges through degree-2 nodes and pair straightest edges at junctions."""
    def direction(pts, at_start, n=12):
        seg = pts[:n] if at_start else pts[::-1][:n]
        (r0, c0), (r1, c1) = seg[0], seg[-1]
        v = np.array([r1 - r0, c1 - c0], float)
        return v / (np.linalg.norm(v) + 1e-9)

    changed = True
    while changed:
        changed = False
        ends = {}
        for i, e in enumerate(E):
            if e['a'] != e['b']:
                ends.setdefault(e['a'], []).append((i, True))
                ends.setdefault(e['b'], []).append((i, False))
        for node, inc in ends.items():
            if len(inc) < 2:
                continue
            # pick the pair forming the straightest continuation
            best = None
            for x in range(len(inc)):
                for y in range(x + 1, len(inc)):
                    (i, si), (j, sj) = inc[x], inc[y]
                    if i == j:
                        continue
                    cos = -np.dot(direction(E[i]['pts'], si), direction(E[j]['pts'], sj))
                    if best is None or cos > best[0]:
                        best = (cos, i, si, j, sj)
            if best is None:
                continue
            cos, i, si, j, sj = best
            if len(inc) > 2 and cos < 0.7:
                continue  # don't bend through a real junction
            ei, ej = E[i], E[j]
            pi = ei['pts'][::-1] if si else ei['pts']        # ends at node
            pj = ej['pts'] if sj else ej['pts'][::-1]        # starts at node
            start = ei['b'] if si else ei['a']
            end = ej['b'] if sj else ej['a']
            new = {'a': start, 'b': end, 'pts': pi + pj[1:]}
            E = [e for k, e in enumerate(E) if k not in (i, j)] + [new]
            changed = True
            break
    return E


# --- geometry cleanup ---------------------------------------------------------

def smooth(pts, closed, k=3):
    a = np.array(pts, float)
    if len(a) < 2 * k + 1:
        return a
    out = a.copy()
    n = len(a)
    for i in range(n):
        if not closed and (i < k or i >= n - k):
            # shrink window near the ends so endpoints stay put
            kk = min(i, n - 1 - i)
            if kk == 0:
                continue
            out[i] = a[i - kk:i + kk + 1].mean(0)
        else:
            idx = [(i + j) % n for j in range(-k, k + 1)]
            out[i] = a[idx].mean(0)
    return out


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    L = np.linalg.norm(ab)
    if L < 1e-9:
        dist = np.linalg.norm(pts - a, axis=1)
    else:
        rel = pts - a
        dist = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / L
    i = int(np.argmax(dist))
    if dist[i] > eps:
        return np.vstack([rdp(pts[:i + 1], eps)[:-1], rdp(pts[i:], eps)])
    return np.array([a, b])


def _line(p, q):
    """Pixel-spaced points from p to q (excluding p)."""
    n = max(int(math.dist(p, q)), 1)
    return [(p[0] + (q[0] - p[0]) * t / n, p[1] + (q[1] - p[1]) * t / n) for t in range(1, n + 1)]


def _trim(pts, anchor, radius):
    """Drop leading points closer than radius to anchor (keep at least 3)."""
    i = 0
    while i < len(pts) - 3 and math.dist(pts[i], anchor) < radius:
        i += 1
    return pts[i:]


def contract_junctions(E, dist, node_id):
    """Merge junctions joined by edges shorter than a stroke width (e.g. Q's tail)."""
    changed = True
    while changed:
        changed = False
        deg = degrees(E)
        for e in E:
            a, b = e['a'], e['b']
            if a == b or deg[a] < 3 or deg[b] < 3:
                continue
            if len(e['pts']) < 2.5 * max(dist[e['pts'][0]], dist[e['pts'][-1]]):
                for p, nid in node_id.items():
                    if nid == b:
                        node_id[p] = a
                for p in e['pts']:
                    node_id[p] = a
                E.remove(e)
                for f in E:
                    f['a'] = a if f['a'] == b else f['a']
                    f['b'] = a if f['b'] == b else f['b']
                # short loops left behind collapse into the junction
                E = [f for f in E if not (f['a'] == f['b'] == a and len(f['pts']) < 6 * dist[f['pts'][0]] + 8)]
                changed = True
                break
    return E


def _tangent(pts, h):
    """Unit direction at pts[0], pointing away from the rest of the stroke."""
    k = min(len(pts) - 1, max(int(round(h)), 3))
    v = np.subtract(pts[0], pts[k])
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


def _ray_to_outline(mask, p, v, max_len):
    """Walk from p along v while inside the glyph; return the last inside point."""
    last, t = np.asarray(p, float), 0.0
    while t < max_len:
        q = np.asarray(p, float) + v * t
        r, c = int(round(q[0])), int(round(q[1]))
        if not (0 <= r < mask.shape[0] and 0 <= c < mask.shape[1]) or not mask[r, c]:
            break
        last, t = q, t + 0.5
    return tuple(last)


def _intersect(p, v, a, b):
    """Ray p + t*v (t > 0) with the infinite line through a, b; returns (t, point) or None."""
    d = np.subtract(b, a)
    m = np.array([[v[0], -d[0]], [v[1], -d[1]]])
    if abs(np.linalg.det(m)) < 1e-6:
        return None
    t, _ = np.linalg.solve(m, np.subtract(a, p))
    return (t, tuple(np.add(p, v * t))) if t > 0 else None


def _segments_cross(p1, p2, q1, q2):
    """Intersection point of segments p1-p2 and q1-q2, or None."""
    d1, d2 = np.subtract(p2, p1), np.subtract(q2, q1)
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    w = np.subtract(q1, p1)
    t = (w[0] * d2[1] - w[1] * d2[0]) / den
    u = (w[0] * d1[1] - w[1] * d1[0]) / den
    if 0 < t < 1 and 0 < u < 1:
        return tuple(np.add(p1, d1 * t))
    return None


def fix_ends(E, dist, node_id, mask):
    """
    Skeletons are unreliable within about a stroke width of stroke ends and
    junctions, so those parts are rebuilt from the reliable middle:

    Junctions are rebuilt first, so free ends next to a junction measure their
    direction along an already straightened stroke.

    - Free ends: cut back, then continue along the stroke's own tangent until
      the outline is reached. Angled terminals end in the middle of the cut.
    - Junctions: branches that line up become straight through-strokes (the
      stem of n; both lines of a crossing). Other branches (the arch of n)
      follow their tangent until they meet the nearest through-stroke.
      Without a through-stroke all branches meet at the junction centre.
    """
    centres = {}
    for p, nid in node_id.items():
        centres.setdefault(nid, []).append(p)
    centres = {k: tuple(np.mean(v, axis=0)) for k, v in centres.items()}
    deg = degrees(E)

    for e in E:
        e['pts'] = [tuple(map(float, p)) for p in e['pts']]
        e['h'] = max(float(np.median([dist[int(r), int(c)] for r, c in e['pts']])), 1.0)  # half stroke width

    def get(e, end):
        return e['pts'] if end == 'a' else e['pts'][::-1]

    def put(e, end, pts):
        e['pts'] = pts if end == 'a' else pts[::-1]

    # junctions
    at_node = {}
    for i, e in enumerate(E):
        for end in ('a', 'b'):
            if deg.get(e[end], 0) >= 3:
                at_node.setdefault(e[end], []).append((i, end))

    for node, branches in at_node.items():
        c = centres[node]
        info = []
        for i, end in branches:
            e = E[i]
            rest = _trim(get(e, end), c, 1.8 * e['h'])
            info.append({'i': i, 'end': end, 'rest': rest, 'v': _tangent(rest, e['h'])})

        # pair up branches that continue each other: most collinear first
        cands = []
        for x in range(len(info)):
            for y in range(x + 1, len(info)):
                vx, vy = info[x]['v'], info[y]['v']
                if vx is not None and vy is not None:
                    cos = float(np.dot(vx, vy))
                    if cos < -0.85:
                        cands.append((cos, x, y))
        lines, paired = [], set()
        for _, x, y in sorted(cands):
            if x in paired or y in paired:
                continue
            paired |= {x, y}
            p1, p2 = info[x]['rest'][0], info[y]['rest'][0]
            seg = np.subtract(p2, p1)
            t = np.clip(np.dot(np.subtract(c, p1), seg) / max(np.dot(seg, seg), 1e-9), 0, 1)
            mid = tuple(np.add(p1, seg * t))
            info[x]['target'] = info[y]['target'] = mid
            lines.append((p1, p2, mid))

        side = [b for k, b in enumerate(info) if k not in paired]
        for b in side:
            b['target'] = c
            if not lines:
                continue
            # follow own tangent to the nearest through-stroke
            best = None
            if b['v'] is not None:
                for p1, p2, _ in lines:
                    hit = _intersect(b['rest'][0], b['v'], p1, p2)
                    if hit and hit[0] < 6 * E[b['i']]['h'] and (best is None or hit[0] < best[0]):
                        best = hit
            b['target'] = best[1] if best else min((m for _, _, m in lines), key=lambda m: math.dist(m, b['rest'][0]))

        # side branches that would cross (arm and leg of k): the one with
        # further to go stops where it meets the other
        for b1 in side:
            for b2 in side:
                if b1 is b2:
                    continue
                hit = _segments_cross(b1['rest'][0], b1['target'], b2['rest'][0], b2['target'])
                if hit and math.dist(hit, b1['target']) > math.dist(hit, b2['target']):
                    b1['target'] = hit

        for b in info:
            put(E[b['i']], b['end'], _line(b['rest'][0], b['target'])[::-1] + b['rest'])

    # free ends
    for e in E:
        for end in ('a', 'b'):
            pts = get(e, end)
            if deg.get(e[end], 0) != 1 or len(pts) < 4:
                continue
            h = e['h']
            rest = _trim(pts, pts[0], 1.6 * h)
            v = _tangent(rest, 1.2 * h)
            if v is None:
                continue
            tip = _ray_to_outline(mask, rest[0], v, math.dist(pts[0], rest[0]) + 4 * h)
            put(e, end, _line(rest[0], tip)[::-1] + rest)

    return E

def glyph_strokes(contours, eps_units=2.0):
    mask, to_units = rasterise(contours)
    dist = ndimage.distance_transform_edt(mask)
    sk = skeletonize(mask)
    labels, nlab = ndimage.label(mask, structure=np.ones((3, 3)))

    def width_at(p):
        return 2 * dist[p]

    edges, node_id = trace_edges(sk)
    E = build_graph(edges, node_id)
    E = prune_spurs(E, width_at, 2 * float(np.median(dist[sk])))

    # dots (i, j, umlauts, period...) are drawn as small circles: a part that
    # is about as long as it is wide has no meaningful centreline
    dots = []
    for lab, sl in enumerate(ndimage.find_objects(labels), start=1):
        part = labels[sl] == lab
        thickness = dist[sl][part].max()
        if max(part.shape) < 3 * thickness:
            rr, cc = np.nonzero(part)
            dots.append((rr.mean() + sl[0].start, cc.mean() + sl[1].start, thickness))
    dot_labels = {labels[int(round(r)), int(round(c))] for r, c, _ in dots}
    E = [e for e in E if labels[e['pts'][0]] not in dot_labels]
    covered = {labels[e['pts'][0]] for e in E} | dot_labels

    E = contract_junctions(E, dist, node_id)
    E = fix_ends(E, dist, node_id, mask)
    E = merge_chains(E)

    strokes = []
    for e in E:
        pts = e['pts']
        closed = e['a'] == e['b'] and len(pts) > 8 and pts[0] == pts[-1]
        arr = smooth([(c, r) for r, c in pts], closed)
        arr = rdp(arr, eps_units * RES)
        strokes.append([to_units(r, c) for c, r in arr])

    def circle(cy, cx, rad):
        return [to_units(cy + rad * math.sin(t), cx + rad * math.cos(t))
                for t in np.linspace(0, 2 * math.pi, 13)]

    for cy, cx, thickness in dots:
        strokes.append(circle(cy, cx, max(0.5 * thickness, 2)))

    # any other part without a skeleton (very small specks)
    for lab in range(1, nlab + 1):
        if lab not in covered:
            rr, cc = np.nonzero(labels == lab)
            strokes.append(circle(rr.mean(), cc.mean(), max(dist[labels == lab].max() * 0.5, 2)))

    return strokes


def fmt(v):
    s = f'{v:.1f}'
    return s[:-2] if s.endswith('.0') else s


def convert(font, out_path, name, source='', designer='', link='',
            license='SIL Open Font License http://scripts.sil.org/OFL', verbose=True):
    """Convert a TTFont (or a path to one) into a single-stroke SVG font at out_path."""
    if not isinstance(font, TTFont):
        font = TTFont(font)

    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    upm = font['head'].unitsPerEm
    os2 = font['OS/2']
    k = 1000 / upm
    font_id = ''.join(ch for ch in name if ch.isalnum())

    def glyph_top(ch):
        # fallback for fonts whose OS/2 table predates cap/x-height
        from fontTools.pens.boundsPen import BoundsPen
        pen = BoundsPen(gs)
        gs[cmap[ord(ch)]].draw(pen)
        return pen.bounds[3] if pen.bounds else 0

    cap_height = getattr(os2, 'sCapHeight', 0) or glyph_top('H')
    x_height = getattr(os2, 'sxHeight', 0) or glyph_top('x')
    space_adv = round(gs[cmap[32]].width * k)

    out = [
        '<?xml version="1.0" encoding="UTF-8" ?>',
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" >',
        '',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1">',
        '',
        '<metadata>',
        f'Font name:               {name}',
        f'License:                 {license}',
        'Single-stroke conversion: tools/outline_to_stroke.py (skeletonised centrelines)',
        f'A derivative of:         {source}',
        f'Designer:                {designer}',
        f'Link:                    {link}',
        '</metadata>',
        '<defs>',
        f'<font id="{font_id}" horiz-adv-x="{space_adv}" >',
        '<font-face',
        f'font-family="{name}"',
        'units-per-em="1000"',
        'ascent="800"',
        'descent="-200"',
        f'cap-height="{round(cap_height * k)}"',
        f'x-height="{round(x_height * k)}"',
        '/>',
        f'<missing-glyph horiz-adv-x="{space_adv}" />',
        f'<glyph unicode=" " glyph-name="space" horiz-adv-x="{space_adv}" />',
    ]

    for ch in CHARS:
        gname = cmap.get(ord(ch))
        if not gname:
            continue
        g = gs[gname]
        pen = FlattenPen(gs)
        g.draw(pen)
        adv = round(g.width * k)
        d = ''
        if pen.contours:
            contours = [[(x * k, y * k) for x, y in c] for c in pen.contours]
            for s in glyph_strokes(contours):
                d += ' M ' + ' L '.join(f'{fmt(x)} {fmt(y)}' for x, y in s)
        uni = ch if ch.isalnum() or ch in '!#$%()*+,-./:;=?@[]^_`{|}~' else f'&#x{ord(ch):x};'
        out.append(f'<glyph unicode="{uni}" glyph-name="{gname}" horiz-adv-x="{adv}" d="{d.strip()}" />')

    out += ['</font>', '</defs>', '</svg>', '']
    with open(out_path, 'w') as f:
        f.write('\n'.join(out))
    if verbose:
        print(f'  written {out_path}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--name', required=True)
    ap.add_argument('--source', default='')
    ap.add_argument('--designer', default='')
    ap.add_argument('--link', default='')
    args = ap.parse_args()
    convert(args.src, args.out, args.name, args.source, args.designer, args.link)


if __name__ == '__main__':
    main()
