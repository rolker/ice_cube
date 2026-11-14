#!/usr/bin/env python3
"""Tax-parcel polygons from an ArcGIS MapServer layer (NH GRANIT ParcelMosaic by default),
datum shifted into our frame and written as a GeoJSON map layer for import_sessions.py.

    python3 analysis/qgis/fetch_parcels.py --config site.toml [--where "..."] [--out F.geojson]

Reads the `[parcels]` section of the site config (see config.example.toml; command-line
arguments take priority), the datum shift from `[parcels].datum_shift_m` or
`[imagery].datum_shift_m`, and `[site.origin]` (or the origin stored in the season gpkg)
for the local frame. It:

1. reads the layer's field list and requests only the allowed, non-personal fields
   (`fields`, default PID / map-lot / town / land-use / area); any field that looks like an
   owner name or a mailing or street address is dropped even if listed, and is never
   requested, so it is never in the output either;
2. selects the core parcels with the SQL `where` (e.g. one subdivision's PID prefix), plus
   `pids` listed one by one;
3. with `neighbours = true`, adds the ring of parcels touching the core: an envelope query
   around the core, kept when a vertex of one lies within `neighbour_tolerance_m` of an
   edge of the other;
4. converts the polygons from the layer's projection (NH State Plane ftUS, EPSG:3437 /
   ESRI 102710; or anything pyproj knows) to geodetic NAD83, then into our frame with the
   same convention as fetch_imagery.py: content at (NAD83 coordinates read as ours) local
   p belongs at p - datum_shift (datum_shift = NAD83 minus our frame, metres east, north);
5. writes a GeoJSON FeatureCollection ([lon, lat], our frame) with Polygon/MultiPolygon
   features: pid, lot, map, town, land_use, area_m2, area_ac, group (core / neighbour),
   kind "parcel", source, note, and a thin grey stroke; and prints the local-frame bbox of
   the core parcels plus `margin_m` (for fetch_imagery.py: `--extent-layer <out>
   --extent-filter group=core`).

Tax-map polygons are not survey boundaries: typically good to 1-3 m, worse on curves.
The output holds the site location: keep it private (never commit it).
Dependencies: numpy (the transform math, from fetch_imagery.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_imagery as fi  # noqa: E402

DEFAULT_SERVICE = "https://nhgeodata.unh.edu/nhgeodata/rest/services/CAD/ParcelMosaic/MapServer/1"
DEFAULT_FIELDS = ["PID", "DisplayId", "TownID", "Town", "SLU", "SLUC", "Shape_Area"]
# Personal data: never requested, never written, even if a config lists them.
PERSONAL = re.compile(r"owner|name|addr|street|mail|city|zip|state_?code|phone|email|grantee|grantor",
                      re.I)
FT2_M2 = fi.US_FT ** 2
UA = {"User-Agent": "ice_cube-fetch_parcels/1"}
NOTE = ("Tax-map parcel polygon (assessing data), not a survey boundary: typically good to "
        "1-3 m, worse on curves.")


# ----------------------------------------------------------------------------- fields

def allowed_fields(wanted, layer_fields):
    """The fields to request: `wanted` that exist in the layer (case-insensitive, layer's
    spelling) and do not look personal."""
    by_lower = {f.lower(): f for f in layer_fields}
    out = []
    for w in wanted:
        f = by_lower.get(w.lower())
        if f and not PERSONAL.search(f) and f not in out:
            out.append(f)
    return out


def clean_attributes(attrs, allowed):
    """Only the allowed, non-personal attributes (whatever the server returned)."""
    keep = {a.lower() for a in allowed if not PERSONAL.search(a)}
    return {k: v for k, v in attrs.items() if k.lower() in keep and not PERSONAL.search(k)}


def lot_label(pid):
    """'004-012-007' -> ('4', '12-7'): map number and lot label (leading zeros dropped);
    '004-012-000' or '004-012-' -> ('4', '12')."""
    parts = [p for p in re.split(r"[-_ ]", str(pid or "")) if p != ""]
    nz = [p.lstrip("0") or "0" for p in parts]
    if not nz:
        return "", ""
    lot = [p for p in nz[1:]]
    while lot and lot[-1] == "0" and len(lot) > 1:
        lot.pop()
    return nz[0], "-".join(lot)


# ----------------------------------------------------------------------------- geometry

def ring_area(ring):
    """Signed shoelace area (positive counter-clockwise) of [[x, y], ...]."""
    a = np.asarray(ring, float)[:, :2]
    x, y = a[:, 0], a[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][:2]
        x2, y2 = ring[(i + 1) % n][:2]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def esri_rings_to_polygons(rings):
    """Esri rings (outer clockwise, holes counter-clockwise, in any order) -> list of
    polygons, each [outer, hole, ...] with GeoJSON winding (outer counter-clockwise)."""
    outers, holes = [], []
    for r in rings:
        if len(r) < 4:
            continue
        (outers if ring_area(r) < 0 else holes).append([list(p[:2]) for p in r])
    if not outers:   # all counter-clockwise: treat each as an outer ring
        outers, holes = holes, []
    polys = [[o[::-1]] for o in outers]
    for h in holes:
        for p in polys:
            if point_in_ring(h[0], p[0]):
                p.append(h[::-1])
                break
    return polys


def _segment_dist(p, a, b):
    """Distances from points p (N, 2) to segments a->b (M, 2): (N, M)."""
    ab = b - a
    ll = np.maximum(np.sum(ab * ab, axis=1), 1e-12)
    ap = p[:, None, :] - a[None, :, :]
    t = np.clip(np.sum(ap * ab[None], axis=2) / ll[None], 0, 1)
    proj = a[None] + t[..., None] * ab[None]
    return np.hypot(*(p[:, None, :] - proj).transpose(2, 0, 1))


def _edges(polys):
    a, b = [], []
    for poly in polys:
        for r in poly:
            r = np.asarray(r, float)[:, :2]
            a.append(r[:-1])
            b.append(r[1:])
    return np.concatenate(a), np.concatenate(b)


def _verts(polys):
    return np.concatenate([np.asarray(r, float)[:, :2] for poly in polys for r in poly])


def polygons_touch(p1, p2, tol):
    """True when a vertex of either polygon set lies within `tol` of an edge of the other
    (in the units of the coordinates; shared or near-shared boundaries)."""
    v1, v2 = _verts(p1), _verts(p2)
    if (v1[:, 0].min() > v2[:, 0].max() + tol or v2[:, 0].min() > v1[:, 0].max() + tol
            or v1[:, 1].min() > v2[:, 1].max() + tol or v2[:, 1].min() > v1[:, 1].max() + tol):
        return False
    a, b = _edges(p2)
    if _segment_dist(v1, a, b).min() <= tol:
        return True
    a, b = _edges(p1)
    return bool(_segment_dist(v2, a, b).min() <= tol)


# ----------------------------------------------------------------------------- transform

def native_to_ours(frame, proj, x, y, datum_shift):
    """Native projected (x, y) in the parcels' NAD83 projection -> (lat, lon) degrees in our
    frame: geodetic NAD83 read as ours gives local p, the content belongs at p - shift
    (the same move as fetch_imagery.py applies to the images)."""
    lat, lon = proj.inverse(np.asarray(x, float), np.asarray(y, float))
    lat, lon = np.atleast_1d(lat), np.atleast_1d(lon)
    enu = np.array([frame.enu(float(a), float(o), frame.h)[:2] for a, o in zip(lat, lon)])
    ex = enu[:, 0] - datum_shift[0]
    ey = enu[:, 1] - datum_shift[1]
    # back to geodetic on the local tangent plane: z chosen so the height stays frame.h
    z = np.array([frame.enu(float(a), float(o), frame.h)[2] for a, o in zip(lat, lon)])
    return fi.local_to_geodetic(frame, ex, ey, z)


def polygons_to_geojson(polys, frame, proj, datum_shift, decimals=9):
    """Polygons in native coordinates -> GeoJSON geometry ([lon, lat]) in our frame."""
    out = []
    for poly in polys:
        rings = []
        for r in poly:
            a = np.asarray(r, float)
            lat, lon = native_to_ours(frame, proj, a[:, 0], a[:, 1], datum_shift)
            rings.append([[round(float(o), decimals), round(float(t), decimals)] for t, o in zip(lat, lon)])
        out.append(rings)
    if len(out) == 1:
        return {"type": "Polygon", "coordinates": out[0]}
    return {"type": "MultiPolygon", "coordinates": out}


def local_bbox_of(geoms, frame):
    xs, ys = [], []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            for lon, lat in poly[0]:
                x, y, _ = frame.enu(lat, lon, frame.h)
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


# ----------------------------------------------------------------------------- service

def get(url, params, post=False):
    data = urllib.parse.urlencode(params).encode()
    req = (urllib.request.Request(url, data=data, headers=UA) if post
           else urllib.request.Request(url + "?" + data.decode(), headers=UA))
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    if isinstance(d, dict) and "error" in d:
        raise RuntimeError(f"{url}: {d['error']}")
    return d


def query(service, params, pause):
    """All features of a query (paged by resultOffset when the server limits it)."""
    feats, offset = [], 0
    while True:
        p = dict(params, f="json", returnGeometry="true", resultOffset=offset)
        d = get(f"{service}/query", p, post=True)
        got = d.get("features", [])
        feats += got
        if not d.get("exceededTransferLimit") or not got:
            return feats, d.get("spatialReference", {})
        offset += len(got)
        time.sleep(pause)


# ----------------------------------------------------------------------------- main

def settings(args, cfg, cfg_dir):
    pc = cfg.get("parcels", {})
    im = cfg.get("imagery", {})
    out = args.out or (str(fi._abs(pc["out"], cfg_dir)) if pc.get("out") else None)
    if not out:
        raise SystemExit("no output: set [parcels].out or --out")
    where = args.where or pc.get("where")
    pids = args.pids or pc.get("pids", [])
    if not where and not pids:
        raise SystemExit("no selection: set [parcels].where and/or pids (or --where / --pids)")
    datum = args.datum_shift or pc.get("datum_shift_m") or im.get("datum_shift_m")
    if datum is None:
        print("WARNING: no datum_shift_m: the parcels are not datum corrected", file=sys.stderr)
        datum = [0.0, 0.0]
    neighbours = pc.get("neighbours", True) if args.neighbours is None else args.neighbours
    return {
        "service": (args.service or pc.get("service") or DEFAULT_SERVICE).rstrip("/"),
        "where": where, "pids": list(pids), "pid_field": pc.get("pid_field", "PID"),
        "fields": args.fields or pc.get("fields") or DEFAULT_FIELDS,
        "neighbours": bool(neighbours),
        "tol_m": float(pc.get("neighbour_tolerance_m", 1.0)),
        "out": out, "datum_shift": tuple(map(float, datum)),
        "margin_m": args.margin if args.margin is not None else float(pc.get("margin_m", 40.0)),
        "pause_s": float(pc.get("pause_s", 0.5)),
        "gpkg": args.gpkg or (str(fi._abs(cfg["season"]["gpkg"], cfg_dir))
                              if cfg.get("season", {}).get("gpkg") else None),
        "stroke": pc.get("stroke", "#808080"),
        "source_name": pc.get("source_name", "NH GRANIT ParcelMosaic"),
        "frame_name": pc.get("frame_name", "our frame"),
    }


def _sql_list(vals):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)


def to_feature(f, group, s, frame, proj, unit, fetched):
    attrs = clean_attributes(f.get("attributes", {}), s["fields"])
    polys = esri_rings_to_polygons(f["geometry"].get("rings", []))
    if not polys:
        return None
    geom = polygons_to_geojson(polys, frame, proj, s["datum_shift"])
    area_m2 = unit * unit * sum(abs(ring_area(p[0])) - sum(abs(ring_area(h)) for h in p[1:]) for p in polys)
    pid = attrs.get(s["pid_field"]) or attrs.get("DisplayId") or ""
    mp, lot = lot_label(pid)
    props = {"name": f"Parcel {pid}", "label": pid, "pid": pid, "map": mp, "lot": lot,
             "town": attrs.get("Town"), "land_use": attrs.get("SLU"),
             "area_m2": round(area_m2, 1), "area_ac": round(area_m2 / 4046.8564224, 3),
             "group": group, "kind": "parcel", "stroke": s["stroke"],
             "source": f"{s['source_name']} {fetched}, datum-shifted to {s['frame_name']}",
             "datum_shift_m": list(s["datum_shift"]),
             "note": NOTE}
    return {"type": "Feature", "properties": props, "geometry": geom}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="site config TOML with [parcels] and [site.origin] (default: config.toml)")
    ap.add_argument("--service", help="MapServer layer URL (…/MapServer/<n>)")
    ap.add_argument("--where", help="SQL selecting the core parcels")
    ap.add_argument("--pids", nargs="+", help="extra core parcels by PID")
    ap.add_argument("--fields", nargs="+", help="fields to keep (personal ones are always dropped)")
    ap.add_argument("--neighbours", action=argparse.BooleanOptionalAction, default=None,
                    help="add the ring of parcels touching the core (default [parcels].neighbours, true)")
    ap.add_argument("--out", help="output GeoJSON")
    ap.add_argument("--gpkg", help="season gpkg for the origin (default [season].gpkg)")
    ap.add_argument("--margin", type=float, help="metres around the core for the printed extent (default 40)")
    ap.add_argument("--datum-shift", nargs=2, type=float, metavar=("DE", "DN"),
                    help="NAD83 minus our frame at the site, metres east, north")
    args = ap.parse_args(argv)
    cfg, cfg_dir = fi.load_config(args.config)
    s = settings(args, cfg, cfg_dir)
    frame = fi.resolve_frame(cfg, fi.read_gpkg_meta(s["gpkg"]))
    fetched = dt.date.today().isoformat()

    layer = get(s["service"], {"f": "json"})
    layer_fields = [f["name"] for f in layer.get("fields", [])]
    s["fields"] = allowed_fields(s["fields"], layer_fields)
    if s["pid_field"] not in s["fields"]:
        raise SystemExit(f"the PID field {s['pid_field']} is not in the layer or not allowed")
    sr = layer.get("extent", {}).get("spatialReference", {})
    wkid = sr.get("latestWkid") or sr.get("wkid")
    proj = fi.projection_for(wkid)
    unit = getattr(proj, "unit", 1.0)
    base = {"outFields": ",".join(s["fields"]), "outSR": wkid}
    print(f"layer {layer.get('name')}: wkid {wkid}; fields kept {', '.join(s['fields'])}")

    clauses = [f"({s['where']})"] if s["where"] else []
    if s["pids"]:
        clauses.append(f"{s['pid_field']} IN ({_sql_list(s['pids'])})")
    core, _ = query(s["service"], dict(base, where=" OR ".join(clauses)), s["pause_s"])
    if not core:
        raise SystemExit("no parcels match the selection")
    core_pids = {f["attributes"].get(s["pid_field"]) for f in core}
    print(f"core: {len(core)} parcel(s)")
    neigh = []
    if s["neighbours"]:
        tol = s["tol_m"] / unit
        core_polys = [p for f in core for p in esri_rings_to_polygons(f["geometry"]["rings"])]
        v = _verts(core_polys)
        env = {"xmin": v[:, 0].min() - tol, "ymin": v[:, 1].min() - tol,
               "xmax": v[:, 0].max() + tol, "ymax": v[:, 1].max() + tol,
               "spatialReference": {"wkid": wkid}}
        time.sleep(s["pause_s"])
        cand, _ = query(s["service"], dict(base, where="1=1", geometry=json.dumps(env),
                                           geometryType="esriGeometryEnvelope", inSR=wkid,
                                           spatialRel="esriSpatialRelIntersects"), s["pause_s"])
        seen = set(core_pids)
        for f in cand:
            pid = f["attributes"].get(s["pid_field"])
            if pid in seen or not f.get("geometry"):
                continue
            polys = esri_rings_to_polygons(f["geometry"]["rings"])
            if polys and polygons_touch(polys, core_polys, tol):
                neigh.append(f)
                seen.add(pid)
        print(f"neighbours: {len(neigh)} of {len(cand)} candidate(s) in the envelope touch the core")

    feats = []
    for group, fs in (("core", core), ("neighbour", neigh)):
        for f in sorted(fs, key=lambda f: str(f["attributes"].get(s["pid_field"]))):
            ft = to_feature(f, group, s, frame, proj, unit, fetched)
            if ft:
                feats.append(ft)
    out = Path(s["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection",
          "name": out.stem,
          "description": f"{NOTE} Source {s['service']}, fetched {fetched}; moved from NAD83 by "
                         f"E {-s['datum_shift'][0]:+.3f} N {-s['datum_shift'][1]:+.3f} m into our frame.",
          "features": feats}
    out.write_text(json.dumps(fc, indent=1) + "\n")
    x0, y0, x1, y1 = local_bbox_of([f["geometry"] for f in feats if f["properties"]["group"] == "core"], frame)
    m = s["margin_m"]
    print(f"wrote {out}: {len(feats)} parcel(s) ({len(core)} core, {len(neigh)} neighbours)")
    print(f"core extent x {x0:.1f}..{x1:.1f} y {y0:.1f}..{y1:.1f} m ({x1 - x0:.0f} x {y1 - y0:.0f} m); "
          f"with {m:g} m margin: bbox {x0 - m:.1f} {y0 - m:.1f} {x1 + m:.1f} {y1 + m:.1f} "
          f"({x1 - x0 + 2 * m:.0f} x {y1 - y0 + 2 * m:.0f} m)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
