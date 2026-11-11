"""Outputs for GIS: ESRI ASCII grids with a .prj, contour lines, and sampling a surface.

ESRI ASCII (`.asc`): the node values with `xllcenter`/`yllcenter` (nodes are cell centres)
and rows from north to south, NODATA -9999; QGIS and GDAL read it directly. The `.prj` next
to it holds the WKT of the local CRS (for the QGIS season project: icecube_meta local_wkt of
the season gpkg, `[output] prj_from_gpkg`), so the grid lands on the project's map.

Contours: marching squares on the node grid (linear along cell edges; a saddle cell is
resolved by the cell's mean), joined into polylines. Written as GeoJSON in local metres
(no CRS member: the coordinates are the local frame), with `level` and `grid` properties;
analysis/qgis/topo_to_gpkg.py loads them into the season gpkg.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .nodes import Grid


def write_asc(path, grid: Grid, A, nodata=-9999.0, fmt="%.4f"):
    """Write node array A (ny, nx; NaN = no data) as an ESRI ASCII grid."""
    A = np.asarray(A, float)
    path = Path(path)
    with open(path, "w") as fh:
        fh.write(f"ncols {grid.nx}\nnrows {grid.ny}\nxllcenter {grid.x0:.4f}\nyllcenter {grid.y0:.4f}\n"
                 f"cellsize {grid.cell}\nNODATA_value {nodata:g}\n")
        for row in A[::-1]:
            fh.write(" ".join(fmt % v if np.isfinite(v) else f"{nodata:g}" for v in row) + "\n")


def write_prj(path, wkt):
    Path(path).write_text(wkt.strip() + "\n")


_EDGES = {  # case -> list of (edge a, edge b); edges 0 bottom, 1 right, 2 top, 3 left
    1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)], 6: [(0, 2)], 7: [(3, 2)], 8: [(2, 3)],
    9: [(2, 0)], 11: [(2, 1)], 12: [(1, 3)], 13: [(1, 0)], 14: [(0, 3)]}


def contour_segments(xs, ys, Z, level):
    """Line segments [((x0, y0), (x1, y1))] of Z = level on the grid (Z[j, i] at xs[i], ys[j])."""
    segs = []
    ny, nx = Z.shape
    for j in range(ny - 1):
        for i in range(nx - 1):
            c = (Z[j, i], Z[j, i + 1], Z[j + 1, i + 1], Z[j + 1, i])   # bl, br, tr, tl
            if not all(np.isfinite(c)):
                continue
            case = sum(1 << k for k, v in enumerate(c) if v > level)
            if case in (0, 15):
                continue
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]

            def edge(e):
                a, b, pa, pb = {0: (c[0], c[1], (x0, y0), (x1, y0)), 1: (c[1], c[2], (x1, y0), (x1, y1)),
                                2: (c[3], c[2], (x0, y1), (x1, y1)), 3: (c[0], c[3], (x0, y0), (x0, y1))}[e]
                t = (level - a) / (b - a) if b != a else 0.5
                return (pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1]))
            if case in (5, 10):
                centre_above = np.mean(c) > level
                # the two "above" corners are joined through the centre when it is above
                if case == 5:     # bl, tr above
                    pairs = [(0, 1), (2, 3)] if centre_above else [(3, 0), (1, 2)]
                else:             # br, tl above
                    pairs = [(3, 0), (1, 2)] if centre_above else [(0, 1), (2, 3)]
            else:
                pairs = _EDGES[case]
            for a, b in pairs:
                segs.append((edge(a), edge(b)))
    return segs


def join_segments(segs, nd=6):
    """Join segments sharing end points into polylines (lists of (x, y))."""
    key = lambda p: (round(p[0], nd), round(p[1], nd))  # noqa: E731
    ends = {}
    for k, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append(k); ends.setdefault(key(b), []).append(k)
    used = [False] * len(segs)
    lines = []
    for k in range(len(segs)):
        if used[k]:
            continue
        used[k] = True
        line = [segs[k][0], segs[k][1]]
        for forward in (True, False):
            while True:
                tip = line[-1] if forward else line[0]
                nxt = next((q for q in ends.get(key(tip), []) if not used[q]), None)
                if nxt is None:
                    break
                used[nxt] = True
                a, b = segs[nxt]
                p = b if key(a) == key(tip) else a
                if forward:
                    line.append(p)
                else:
                    line.insert(0, p)
        lines.append(line)
    return lines


def contours(grid: Grid, Z, interval, base=0.0):
    """[(level, [polyline, ...])] for every multiple of `interval` (offset `base`) within Z."""
    zmin, zmax = np.nanmin(Z), np.nanmax(Z)
    k0, k1 = int(np.ceil((zmin - base) / interval)), int(np.floor((zmax - base) / interval))
    out = []
    for k in range(k0, k1 + 1):
        lev = base + k * interval
        lines = join_segments(contour_segments(grid.xs, grid.ys, Z, lev))
        if lines:
            out.append((round(lev, 6), lines))
    return out


def contours_geojson(path, levels, props=None):
    """Write contours as a GeoJSON FeatureCollection of LineStrings in local metres."""
    feats = []
    for lev, lines in levels:
        for ln in lines:
            feats.append({"type": "Feature", "properties": dict(level=lev, **(props or {})),
                          "geometry": {"type": "LineString", "coordinates": [[round(x, 3), round(y, 3)] for x, y in ln]}})
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "name": Path(path).stem,
                                      "note": "local metres (the run's local frame); no CRS member on purpose",
                                      "features": feats}))
    return len(feats)


def sample(grid: Grid, A, x, y):
    """Bilinear value of node array A at points x, y; NaN outside or next to an empty node."""
    from .dem import bilinear
    return bilinear(grid.xs, grid.ys, A, x, y)
