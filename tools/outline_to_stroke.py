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
from PIL import Image, ImageChops, ImageDraw
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

    img = Image.new('1', (w, h), 0)
    for c in contours:  # even-odd fill via XOR
        m = Image.new('1', (w, h), 0)
        ImageDraw.Draw(m).polygon([to_px(p) for p in c], fill=1)
        img = ImageChops.logical_xor(img, m)

    def to_units(r, c):
        return ((c - PAD) / RES + x0, (h - r - PAD) / RES + y0)

    return np.ascontiguousarray(np.asarray(img.convert('L')) > 127), to_units


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


def prune_spurs(E, width_at, factor=1.2):
    """Remove short dangling branches created by corners and terminals."""
    changed = True
    while changed:
        changed = False
        d = degrees(E)
        for e in sorted(E, key=lambda e: len(e['pts'])):
            if e['a'] == e['b']:
                continue
            for end, other, anchor in (('a', 'b', -1), ('b', 'a', 0)):
                if d[e[end]] == 1 and d[e[other]] >= 3:
                    limit = factor * width_at(e['pts'][anchor])
                    if len(e['pts']) < limit:
                        E.remove(e)
                        changed = True
                        break
            if changed:
                break
    return E


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


def fix_ends(E, dist, node_id):
    """
    Skeletons are noisy near stroke ends and junctions. At junctions, replace
    the last bit of each branch with a straight line into the junction centre.
    At free ends, cut off the hook caused by angled terminals and extend
    straight to where the outline ends.
    """
    centres = {}
    for p, nid in node_id.items():
        centres.setdefault(nid, []).append(p)
    centres = {k: tuple(np.mean(v, axis=0)) for k, v in centres.items()}
    deg = degrees(E)

    for e in E:
        pts = [tuple(map(float, p)) for p in e['pts']]
        h = float(np.median([dist[p] for p in e['pts']]))  # half stroke width
        for end in ('a', 'b'):
            if end == 'b':
                pts = pts[::-1]
            if len(pts) > 6:
                d = deg.get(e[end], 0)
                if d >= 3:
                    c = centres[e[end]]
                    rest = _trim(pts, c, 1.8 * h)
                    pts = _line(rest[0], c)[::-1] + rest
                elif d == 1:
                    tip = pts[0]
                    rest = _trim(pts, tip, 1.5 * h)
                    k = min(len(rest) - 1, max(int(h), 4))
                    v = np.subtract(rest[0], rest[k])
                    n = np.linalg.norm(v)
                    if n > 1e-9:
                        ext = math.dist(tip, rest[0]) + 0.8 * h
                        newtip = tuple(np.add(rest[0], v / n * ext))
                        pts = _line(rest[0], newtip)[::-1] + rest
            if end == 'b':
                pts = pts[::-1]
        e['pts'] = pts
    return E


# --- per glyph ----------------------------------------------------------------

def glyph_strokes(contours, eps_units=2.0):
    mask, to_units = rasterise(contours)
    dist = ndimage.distance_transform_edt(mask)
    sk = skeletonize(mask)
    labels, nlab = ndimage.label(mask, structure=np.ones((3, 3)))

    def width_at(p):
        return 2 * dist[p]

    edges, node_id = trace_edges(sk)
    E = build_graph(edges, node_id)
    E = prune_spurs(E, width_at)
    covered = {labels[e['pts'][0]] for e in E}
    E = contract_junctions(E, dist, node_id)
    E = fix_ends(E, dist, node_id)
    E = merge_chains(E)

    strokes = []
    for e in E:
        pts = e['pts']
        closed = e['a'] == e['b'] and len(pts) > 8 and pts[0] == pts[-1]
        arr = smooth([(c, r) for r, c in pts], closed)
        arr = rdp(arr, eps_units * RES)
        strokes.append([to_units(r, c) for c, r in arr])

    # tiny blobs (dots of i, j, period...) whose skeleton is a point
    for lab in range(1, nlab + 1):
        if lab in covered:
            continue
        rr, cc = np.nonzero(labels == lab)
        cy, cx = rr.mean(), cc.mean()
        rad = max(dist[labels == lab].max() * 0.5, 2)
        circle = [to_units(cy + rad * math.sin(t), cx + rad * math.cos(t))
                  for t in np.linspace(0, 2 * math.pi, 9)]
        strokes.append(circle)

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
        f'cap-height="{round(os2.sCapHeight * k)}"',
        f'x-height="{round(os2.sxHeight * k)}"',
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
