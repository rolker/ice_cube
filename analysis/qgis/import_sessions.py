#!/usr/bin/env python3
"""Import Ice Cube Logger sessions into one GeoPackage per season, for QGIS.

    python3 analysis/qgis/import_sessions.py [INPUT ...] [--config config.toml]
            [--gpkg OUT.gpkg] [--season NAME] [--layers F.geojson ...]
            [--base-geojson F.geojson | --base LAT LON H]
            [--origin LAT LON H [--origin-name NAME]] [--force] [--no-photos]

Inputs: session share zips (IceCubeLogger_<session>.zip, or older names without .zip),
session folders, or folders holding them (searched recursively). Without INPUT the
`[season] input_dir` of the config is used. Settings come from the config (`[season]`
and `[site]`, see config.example.toml; default `config.toml` at the repo root when it
exists); command-line arguments take priority. Relative paths in the config are relative
to the config file.

Output (all private: it holds positions): `<season>.gpkg` and, next to it,
`photos/<session>/<file>.jpg` so QGIS shows photos by relative path. Tables:

  captures      PointZ  one row per capture, key (session, n). Z = ground height.
                        `note` and `label` are yours: edited in QGIS, never overwritten.
  photos        PointZ  one row per photo file, key (session, name); `note` is yours.
  refs          PointZ  ref captures plus refs.csv rows, pole-check results from events,
                        `site` groups refs within 0.30 m; `note` is yours.
  tracks        LineStringZ  1 Hz GGA track per session, split at gaps and fix changes,
                        one `fix` value per line. Z = antenna height.
  track_points  PointZ  the 1 Hz epochs with fix, satellites, HDOP, correction age.
  sessions      table   one row per session: times, app, link, caster mount, frame
                        lines, base station, pole heights, counts, losses; `note` is yours.
  layer_<file>_<points|lines|polygons>  the map-layer GeoJSON files, replaced on import.
  icecube_meta  table   season name, the local frame origin, the base, and the display
                        CRS (WKT2 and proj string) for build_project.py.

Geometries are EPSG:4326 lon/lat with ellipsoidal heights as Z; lat, lon and heights are
also plain columns. Every point table (captures, photos, refs, track_points and the
layer_*_points tables) also has x_m, y_m, z_m: the exact local ENU coordinates (x east,
y north, z up, metres; see local_frame.py) about the origin, `[site.origin]` of the
config (for the yard: lot monument G). The base (`[site].base`, `base_geojson`, a
kind=base point in the layers, or an RTCM 1005 in the sessions) is kept as a separate
known point in icecube_meta; it is the origin only when no origin is configured. When
the origin changes, x_m/y_m/z_m of every existing row are recomputed (user columns are
untouched). The display CRS (orthographic at the origin) is stored in
gpkg_spatial_ref_sys (WKT 1 and, in definition_12_063, WKT2) and in icecube_meta.

Idempotent and incremental: a session already imported from an unchanged source is
skipped (--force re-reads it); rows are matched by their keys and updated in place, so
nothing is duplicated and the user columns survive. Close the project in QGIS before
importing from the command line; the Ice Cube QGIS plugin (plugin/) does that itself.
`run()` is the same import as a function (the plugin calls it in a background task).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gpkg_writer import GeoPackage, gpkg_geometry, point, point_xyz  # noqa: E402
from local_frame import LocalFrame, ecef_to_geodetic  # noqa: E402
from logger_session import find_sessions, load_session, read_refs_csv  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LOCAL_SRS_ID = 100001
SCHEMA_VERSION = 2
USER_COLUMNS = {"captures": ("note", "label"), "photos": ("note",), "refs": ("note",),
                "sessions": ("note",)}
TRACK_GAP_S = 3.0      # a gap longer than this starts a new track line
REF_SITE_M = 0.30      # refs this close horizontally are the same mark (as the app's check)

R, I, T = "REAL", "INTEGER", "TEXT"
MEAS = [("utc", T), ("lat", R), ("lon", R), ("h_ant", R), ("pole_m", R), ("h_ground", R),
        ("fix", I), ("n_epochs", I), ("sd_e_m", R), ("sd_n_m", R), ("sd_u_m", R), ("age_s", R)]
ENU = [("x_m", R), ("y_m", R), ("z_m", R)]
LEGACY_ENU = ("e_m", "n_m", "u_m")   # schema 1: E/N/U about the base, replaced by x/y/z_m
POINT_TABLES = ("captures", "photos", "refs", "track_points")
NO_XYZ = {"x_m": None, "y_m": None, "z_m": None}   # set on (re)import, filled by update_local
SCHEMA = {
    "captures": [("session", T), ("n", I), ("class", T)] + MEAS +
                [("photo", T), ("photo_count", I)] + ENU +
                [("source", T), ("elapsed_ns", I), ("note", T), ("label", T)],
    "photos": [("session", T), ("n", I), ("name", T), ("photo", T), ("utc", T), ("attached", T),
               ("bytes", I), ("lat", R), ("lon", R), ("h_ground", R)] + ENU + [("note", T)],
    "refs": [("session", T), ("n", I)] + MEAS +
            [("pauses", I), ("pole_check", T), ("diff_last_m", R), ("diff_mean_m", R),
             ("visits", I), ("last_visit", T), ("pole_ok", I), ("site", I)] + ENU +
            [("source", T), ("note", T)],
    "tracks": [("session", T), ("seg", I), ("fix", I), ("start_utc", T), ("end_utc", T),
               ("n_vertices", I), ("duration_s", R)],
    "track_points": [("session", T), ("utc", T), ("fix", I), ("nsat", I), ("hdop", R),
                     ("age_s", R), ("h_ant", R)] + ENU,
    "sessions": [("session", T), ("format", T), ("app", T), ("device", T), ("snapshot", I),
                 ("start_utc", T), ("end_utc", T), ("duration_min", R), ("link", T),
                 ("caster_mount", T), ("frame", T), ("base_station", T), ("base_type", T),
                 ("base_ecef", T), ("layer_base_check", T), ("pole_heights", T),
                 ("n_captures", I), ("n_surface", I), ("n_object", I), ("n_ref", I),
                 ("n_photo", I), ("n_control", I), ("n_photos", I), ("n_track_points", I),
                 ("pct_fixed", R), ("gga", I), ("gga_lost", I), ("gga_parsed", I), ("rawx", I),
                 ("rawx_lost", I), ("gps_lost", I), ("caster_lost", I), ("rtcm_crc_errors", I),
                 ("remarks", T), ("source", T), ("source_sig", T), ("imported_utc", T),
                 ("note", T)],
}


# ----------------------------------------------------------------------------- config

def load_config(path):
    """(config dict, its directory). Missing file -> empty config."""
    if path is None:
        p = REPO / "config.toml"
        if not p.is_file():
            return {}, Path.cwd()
    else:
        p = Path(path)
    return tomllib.loads(p.read_text()), p.resolve().parent


def _abs(p, base):
    p = Path(p).expanduser()
    return p if p.is_absolute() else (base / p)


def base_from_geojson(path):
    """(lat, lon, h) of the first Point with kind 'base' (any case) and a height."""
    d = json.loads(Path(path).read_text())
    feats = d.get("features") or ([d] if d.get("type") == "Feature" else [])
    for f in feats:
        g = f.get("geometry") or {}
        if (str((f.get("properties") or {}).get("kind", "")).lower() == "base"
                and g.get("type") == "Point" and len(g.get("coordinates", [])) >= 3):
            lon, lat, h = g["coordinates"][:3]
            return lat, lon, h
    return None


def resolve_base(args, cfg, cfg_dir, layer_files, sessions):
    """The base as a LocalFrame named "base", by priority: --base, --base-geojson,
    [site].base, [site].base_geojson, a kind=base point in the layer files, then (only if
    [site].base_from_sessions = true) the first RTCM 1005 logged in the sessions."""
    site = cfg.get("site", {})
    b = lambda *llh, source: LocalFrame(*llh, name="base", source=source)
    if args.base:
        return b(*args.base, source="command line")
    if args.base_geojson:
        p = base_from_geojson(args.base_geojson)
        if p:
            return b(*p, source=f"base point in {Path(args.base_geojson).name}")
    if "base" in site:
        return b(*site["base"], source="config [site].base")
    if "base_geojson" in site:
        f = _abs(site["base_geojson"], cfg_dir)
        p = base_from_geojson(f)
        if p:
            return b(*p, source=f"base point in {f.name}")
    for f in layer_files:
        p = base_from_geojson(f)
        if p:
            return b(*p, source=f"base point in {Path(f).name}")
    if site.get("base_from_sessions"):
        for s in sessions:
            e = s.info.get("base_ecef")
            if e:
                x, y, z = (float(v) for v in e.split(";"))
                return b(*ecef_to_geodetic(x, y, z),
                         source=f"RTCM 1005 of station {s.info.get('base_station')} in {s.name}")
    return None


def resolve_origin(args, cfg, base, meta):
    """The local frame origin, by priority: --origin, [site.origin] of the config, the
    origin already stored in the gpkg, then the base. None when there is none of these."""
    o = cfg.get("site", {}).get("origin")
    if args.origin:
        return LocalFrame(*args.origin, name=args.origin_name or "origin", source="command line")
    if o:
        missing = [k for k in ("lat", "lon", "h") if k not in o]
        if missing:
            raise SystemExit(f"[site.origin] lacks {', '.join(missing)}")
        return LocalFrame(o["lat"], o["lon"], o["h"], name=str(o.get("name", "origin")),
                          source=str(o.get("source", "config [site.origin]")))
    if meta.get("origin_lat"):
        return LocalFrame(float(meta["origin_lat"]), float(meta["origin_lon"]), float(meta["origin_h"]),
                          name=meta.get("origin_name", "origin"), source=meta.get("origin_source", ""))
    if base is not None:
        return LocalFrame(base.lat, base.lon, base.h, name="base", source=base.source)
    return None


# ----------------------------------------------------------------------------- schema

def ensure_schema(gp):
    gp.ensure_feature_table("captures", SCHEMA["captures"], "POINT", z=True, unique=("session", "n"),
                            description="Logger captures; note and label are user columns")
    gp.ensure_feature_table("photos", SCHEMA["photos"], "POINT", z=True, unique=("session", "name"),
                            description="Session photos, path relative to the gpkg folder")
    gp.ensure_feature_table("refs", SCHEMA["refs"], "POINT", z=True, unique=("session", "n"),
                            description="Ref occupations with pole checks")
    gp.ensure_feature_table("tracks", SCHEMA["tracks"], "LINESTRING", z=True,
                            description="1 Hz GGA track, one fix value per line")
    gp.ensure_feature_table("track_points", SCHEMA["track_points"], "POINT", z=True,
                            unique=("session", "utc"), description="1 Hz GGA epochs")
    gp.ensure_attributes_table("sessions", SCHEMA["sessions"], unique=("session",),
                               description="One row per logger session")
    gp.ensure_attributes_table("icecube_meta", [("key", T), ("value", T)], unique=("key",),
                               description="Season and local frame settings")
    for table in ("captures", "refs"):
        left = gp.drop_columns(table, LEGACY_ENU)
        for c in left:   # SQLite older than 3.35: keep the column, empty
            gp.con.execute(f'UPDATE {table} SET "{c}" = NULL')
    gp.commit()


def set_meta(gp, **kv):
    gp.upsert("icecube_meta", [{"key": k, "value": None if v is None else str(v)}
                               for k, v in kv.items()], key=("key",))


def get_meta(gp):
    return dict(gp.con.execute("SELECT key, value FROM icecube_meta"))


# ----------------------------------------------------------------------------- import

def _geom(lat, lon, h):
    if lat is None or lon is None:
        return None
    return point(lon, lat, h if h is not None else 0.0)


def import_session(gp, src, s, photos_dir, extract_photos=True):
    """Write one parsed session; returns the number of photos extracted."""
    rel = lambda name: f"photos/{s.name}/{name}"
    caps = []
    for c in s.captures:
        files = c["photo_files"]
        caps.append({
            "session": s.name, "n": c["n"], "class": c["class"], "utc": c.get("utc"),
            "lat": c.get("lat"), "lon": c.get("lon"), "h_ant": c.get("h_ant"),
            "pole_m": c.get("pole_m"), "h_ground": c.get("h_ground"), "fix": c.get("fix"),
            "n_epochs": c.get("n_epochs"), "sd_e_m": c.get("sd_e_m"), "sd_n_m": c.get("sd_n_m"),
            "sd_u_m": c.get("sd_u_m"), "age_s": c.get("age_s"),
            "photo": rel(files[0]) if files else None, "photo_count": len(files),
            "source": c.get("source"), "elapsed_ns": c.get("elapsed_ns"), "note": c.get("note"),
            "geom": _geom(c.get("lat"), c.get("lon"), c.get("h_ground")), **NO_XYZ,
        })
    gp.upsert("captures", caps, key=("session", "n"), preserve=USER_COLUMNS["captures"])

    capd = {c["n"]: c for c in s.captures}
    rows = []
    for p in s.photos:
        c = capd.get(p["n"], {})
        rows.append({"session": s.name, "n": p["n"], "name": p["name"], "photo": rel(p["name"]),
                     "utc": p["utc"], "attached": p["attached"], "bytes": p["bytes"],
                     "lat": c.get("lat"), "lon": c.get("lon"), "h_ground": c.get("h_ground"),
                     "geom": _geom(c.get("lat"), c.get("lon"), c.get("h_ground")), **NO_XYZ})
    gp.upsert("photos", rows, key=("session", "name"), preserve=USER_COLUMNS["photos"])

    refs = []
    for c in s.captures:
        if c["class"] != "ref":
            continue
        r = {k: c.get(k) for k, _ in MEAS}
        r.update({"session": s.name, "n": c["n"], "source": "points.csv", **NO_XYZ,
                  "geom": _geom(c.get("lat"), c.get("lon"), c.get("h_ground"))})
        for k in ("pauses", "pole_check", "diff_last_m", "diff_mean_m", "visits", "last_visit", "pole_ok"):
            r[k] = s.refs.get(c["n"], {}).get(k)
        refs.append(r)
    gp.upsert("refs", refs, key=("session", "n"), preserve=USER_COLUMNS["refs"])

    gp.con.execute("DELETE FROM tracks WHERE session=?", (s.name,))
    gp.con.execute("DELETE FROM track_points WHERE session=?", (s.name,))
    gp.insert("track_points", [{
        "session": s.name, "utc": e["utc"], "fix": e["fix"], "nsat": e["nsat"], "hdop": e["hdop"],
        "age_s": e["age_s"], "h_ant": e["h"], "geom": point(e["lon"], e["lat"], e["h"])}
        for e in s.track])
    gp.insert("tracks", track_lines(s.name, s.track))

    info = dict(s.info)
    info["imported_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gp.upsert("sessions", [info], key=("session",), preserve=USER_COLUMNS["sessions"])

    n_new = 0
    if extract_photos:
        for p in s.photos:
            n_new += src.extract_photo(p["name"], photos_dir / s.name / p["name"])
    return n_new


def track_lines(session, track):
    """Split 1 Hz epochs into lines at gaps > TRACK_GAP_S and at fix changes (a fix change
    shares its vertex with the previous line, so the track stays continuous)."""
    out, cur = [], []
    def flush():
        if len(cur) >= 2:
            out.append({
                "session": session, "seg": len(out) + 1, "fix": cur[-1]["fix"],
                "start_utc": cur[0]["utc"], "end_utc": cur[-1]["utc"], "n_vertices": len(cur),
                "duration_s": round(cur[-1]["t"] - cur[0]["t"], 1),
                "geom": gpkg_geometry("LINESTRING", [(e["lon"], e["lat"], e["h"]) for e in cur],
                                      4326, z=True)})
    for e in track:
        if cur and e["t"] - cur[-1]["t"] > TRACK_GAP_S:
            flush()
            cur = []
        elif cur and e["fix"] != cur[-1]["fix"]:
            last = cur[-1]
            flush()
            cur = [dict(last, fix=e["fix"])]
        cur.append(e)
    flush()
    return out


def import_refs_csv(gp, texts):
    """refs.csv rows of sessions not otherwise imported (rows already there are kept)."""
    rows = {}
    for text in texts:
        for r in read_refs_csv(text):
            rows[(r["session"], r["n"])] = r
    out = []
    for (sess, n), r in sorted(rows.items()):
        d = {k: r.get(k) for k, _ in MEAS}
        d.update({"session": sess, "n": n, "source": "refs.csv",
                  "geom": _geom(r.get("lat"), r.get("lon"), r.get("h_ground"))})
        out.append(d)
    if out:
        gp.upsert("refs", out, key=("session", "n"), preserve=[k for k in out[0] if k not in ("session", "n")])
    return len(out)


def local_xyz(frame, lat, lon, h):
    """Exact ENU (x_m, y_m, z_m) rounded to 0.01 mm; z None when h is None (x, y then use
    the origin height: 5 m of height error moves x, y by 0.02 mm at 30 m)."""
    if frame is None or lat is None or lon is None:
        return None, None, None
    x, y, z = frame.enu(lat, lon, frame.h if h is None else h)
    return round(x, 5), round(y, 5), (None if h is None else round(z, 5))


def update_local(gp, frame, recompute=False):
    """Fill x_m, y_m, z_m of the point tables about the frame (rows still empty, or all rows
    when `recompute`), and group refs into sites. Only these columns are written."""
    for table in POINT_TABLES:
        where = "" if recompute else " WHERE x_m IS NULL"
        if table == "track_points":   # position only in the geometry: lon, lat, antenna h
            rows = []
            for fid, blob in gp.con.execute(f"SELECT fid, geom FROM {table}{where}"):
                p = point_xyz(blob)
                rows.append((fid,) + ((p[1], p[0], p[2]) if p else (None, None, None)))
        else:
            rows = gp.con.execute(f"SELECT fid, lat, lon, h_ground FROM {table}{where}").fetchall()
        gp.con.executemany(f"UPDATE {table} SET x_m=?, y_m=?, z_m=? WHERE fid=?",
                           [local_xyz(frame, lat, lon, h) + (fid,) for fid, lat, lon, h in rows])
    rows = gp.con.execute("SELECT fid, lat, lon FROM refs WHERE lat IS NOT NULL "
                          "ORDER BY utc, session, n").fetchall()
    if rows:
        fr = frame or LocalFrame(rows[0][1], rows[0][2], 0.0)
        sites = []   # (e, n, id)
        upd = []
        for fid, lat, lon in rows:
            e, n, _ = fr.enu(lat, lon, 0.0)
            hit = next((sid for se, sn, sid in sites if math.hypot(e - se, n - sn) <= REF_SITE_M), None)
            if hit is None:
                hit = len(sites) + 1
                sites.append((e, n, hit))
            upd.append((hit, fid))
        gp.con.executemany("UPDATE refs SET site=? WHERE fid=?", upd)


# ----------------------------------------------------------------------------- layers

def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "x"


def _rgba(c):
    """'#rrggbb' or Android '#aarrggbb' -> 'r,g,b,a' (a QGIS colour string)."""
    if not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c):
        return None
    h = c[1:]
    a, h = (int(h[:2], 16), h[2:]) if len(h) == 8 else (255, h)
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a}"


def _parts(geom):
    """(family, [parts]) with family points/lines/polygons and parts as coordinates of
    single Points, LineStrings or Polygons."""
    t, c = geom.get("type"), geom.get("coordinates")
    if not c:
        return None, []
    return {"Point": ("points", [c]), "MultiPoint": ("points", c), "LineString": ("lines", [c]),
            "MultiLineString": ("lines", c), "Polygon": ("polygons", [c]),
            "MultiPolygon": ("polygons", c)}.get(t, (None, []))


KEEP_LAYER_PREFIXES = ("layer_topo_",)   # written by other tools; an import must not drop them


def import_layers(gp, files, frame=None):
    """Replace the map-layer tables (layer_*) by the given GeoJSON files; point tables get
    x_m, y_m, z_m about `frame`. Tables other tools add under layer_* (the topography's
    layer_topo_contours, see topo_to_gpkg.py) are left alone. Returns {table: rows}."""
    for t in [t for t in gp.tables() if t.startswith("layer_") and not t.startswith(KEEP_LAYER_PREFIXES)]:
        gp.drop_table(t)
    counts = {}
    for f in files:
        f = Path(f)
        d = json.loads(f.read_text())
        feats = (d.get("features") if d.get("type") == "FeatureCollection"
                 else [d] if d.get("type") == "Feature" else [{"geometry": d, "properties": {}}])
        fam = {}
        for ft in feats:
            family, parts = _parts(ft.get("geometry") or {})
            if family:
                fam.setdefault(family, []).append((ft.get("properties") or {}, parts))
        for family, items in fam.items():
            table = f"layer_{_slug(f.stem)}_{family}"
            z = all(len(p) >= 3 for _, parts in items for p in _flat(family, parts))
            props, types = [], {}
            for pr, _ in items:
                for k, v in pr.items():
                    col = _slug(k)
                    if col in ("fid", "geom", "source_file", "h", "stroke_rgba", "fill_rgba", "part",
                               "x_m", "y_m", "z_m"):
                        col = "prop_" + col
                    if col not in types:
                        props.append((k, col))
                        types[col] = I
                    if v is None:
                        continue
                    if isinstance(v, bool) or isinstance(v, (str, list, dict)):
                        types[col] = T
                    elif isinstance(v, float) and types[col] == I:
                        types[col] = R
            cols = [(c, types[c]) for _, c in props] + [("stroke_rgba", T), ("fill_rgba", T),
                                                        ("source_file", T)]
            if family == "points":
                cols += [("h", R), ("part", I)] + ENU
            gtype = {"points": "POINT", "lines": "MULTILINESTRING", "polygons": "MULTIPOLYGON"}[family]
            gp.ensure_feature_table(table, cols, gtype, z=z,
                                    description=f"{f.name}: {family}, imported from GeoJSON")
            rows = []
            for pr, parts in items:
                base = {}
                for k, c in props:
                    v = pr.get(k)
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v)
                    elif isinstance(v, bool):
                        v = str(v).lower()
                    base[c] = v
                base["stroke_rgba"] = _rgba(pr.get("stroke"))
                base["fill_rgba"] = _rgba(pr.get("fill"))
                base["source_file"] = f.name
                if family == "points":
                    for i, p in enumerate(parts):
                        h = p[2] if len(p) > 2 else None
                        x, y, zz = local_xyz(frame, p[1], p[0], h)
                        rows.append(dict(base, h=h, part=i, x_m=x, y_m=y, z_m=zz,
                                         geom=gpkg_geometry("POINT", tuple(p[:3] if z else p[:2]), 4326, z)))
                else:
                    gt = "MULTILINESTRING" if family == "lines" else "MULTIPOLYGON"
                    rows.append(dict(base, geom=gpkg_geometry(gt, parts, 4326, z)))
            gp.insert(table, rows)
            gp.update_extent(table)
            counts[table] = len(rows)
    return counts


def _flat(family, parts):
    if family == "points":
        return parts
    if family == "lines":
        return [p for line in parts for p in line]
    return [p for poly in parts for ring in poly for p in ring]


# ----------------------------------------------------------------------------- main

def run(argv=None, log=print, progress=None):
    """The import as a function: `argv` as on the command line, `log(str)` for the report
    lines, `progress(done, total, name)` called before each session and at the end.
    Returns a summary dict: gpkg, season, found, added (one dict per session read:
    session, captures, photos, photos_extracted, track_points, replaced), unchanged
    (names skipped), photos_extracted, refs_csv_rows, totals (rows per table), origin,
    recomputed. Bad arguments raise SystemExit (argparse)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="session zips, session folders or folders of them")
    ap.add_argument("--config", help="TOML config (default: config.toml at the repo root, if any)")
    ap.add_argument("--gpkg", help="output GeoPackage (default [season].gpkg)")
    ap.add_argument("--season", help="season name (default [season].name, else the gpkg name)")
    ap.add_argument("--layers", nargs="*", help="map-layer GeoJSON files (default [season].layers)")
    ap.add_argument("--base-geojson", help="GeoJSON with a kind=base Point [lon, lat, h]")
    ap.add_argument("--base", nargs=3, type=float, metavar=("LAT", "LON", "H"),
                    help="the base position (ellipsoidal h); the origin too when none is set")
    ap.add_argument("--origin", nargs=3, type=float, metavar=("LAT", "LON", "H"),
                    help="local frame origin (ellipsoidal h), instead of [site.origin]")
    ap.add_argument("--origin-name", help="name of the --origin point (shown in the CRS name)")
    ap.add_argument("--force", action="store_true", help="re-read sessions even if unchanged")
    ap.add_argument("--no-photos", action="store_true", help="do not extract photos")
    args = ap.parse_args(argv)
    added, unchanged = [], []

    cfg, cfg_dir = load_config(args.config)
    season = cfg.get("season", {})
    gpkg_path = Path(args.gpkg) if args.gpkg else (_abs(season["gpkg"], cfg_dir) if "gpkg" in season else None)
    if gpkg_path is None:
        ap.error("no output: give --gpkg or [season].gpkg in the config")
    inputs = args.inputs or ([str(_abs(season["input_dir"], cfg_dir))] if "input_dir" in season else [])
    if not inputs:
        ap.error("no inputs: give session zips/folders or [season].input_dir in the config")
    layer_files = (args.layers if args.layers is not None
                   else [str(_abs(p, cfg_dir)) for p in season.get("layers", [])])
    name = args.season or season.get("name") or gpkg_path.stem

    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    gp = GeoPackage(gpkg_path)
    try:
        ensure_schema(gp)
        done = {r[0]: (r[1], r[2]) for r in gp.con.execute("SELECT session, source_sig, snapshot FROM sessions")}

        sources = find_sessions(inputs)
        log(f"{len(sources)} session(s) found")
        parsed, refs_texts, n_photos = [], [], 0
        for k_src, src in enumerate(sources):
            if progress is not None:
                progress(k_src, len(sources), src.name)
            t = src.refs_csv()
            if t:
                refs_texts.append(t)
            old = done.get(src.name)
            if old and not args.force:
                if old[0] == src.signature():
                    log(f"  {src.name}: unchanged, skipped")
                    unchanged.append(src.name)
                    src.close()
                    continue
                if src.snapshot and not old[1]:
                    log(f"  {src.name}: snapshot of a session already imported in full, skipped")
                    unchanged.append(src.name)
                    src.close()
                    continue
            s = load_session(src)
            k = import_session(gp, src, s, gpkg_path.parent / "photos", not args.no_photos)
            n_photos += k
            gp.commit()
            parsed.append(s)
            i = s.info
            added.append({"session": s.name, "captures": i["n_captures"], "photos": i["n_photos"],
                          "photos_extracted": k, "track_points": i["n_track_points"],
                          "replaced": s.name in done})
            log(f"  {s.name}: {i['format']}, app {i.get('app')}, {i['n_captures']} captures, "
                  f"{i['n_photos']} photos ({k} extracted), {i['n_track_points']} track epochs"
                  + (f"; {i['remarks']}" if i.get("remarks") else ""))
            src.close()
        n_refs = import_refs_csv(gp, refs_texts)

        meta = get_meta(gp)
        base = resolve_base(args, cfg, cfg_dir, layer_files, parsed)
        if base is None and meta.get("base_lat"):
            base = LocalFrame(float(meta["base_lat"]), float(meta["base_lon"]), float(meta["base_h"]),
                              name="base", source=meta.get("base_source") or meta.get("frame_source", ""))
        frame = resolve_origin(args, cfg, base, meta)
        recompute = frame is not None and (meta.get("origin_key") != frame.key()
                                           or meta.get("schema_version") != str(SCHEMA_VERSION))
        if frame is not None:
            gp.add_srs(LOCAL_SRS_ID, frame.crs_name(), "ICECUBE", 1, frame.wkt1(),
                       f"Local ENU display CRS: orthographic at {frame.name}, metres east and north of it",
                       wkt2=frame.wkt2())
            set_meta(gp, origin_name=frame.name, origin_lat=f"{frame.lat:.10f}", origin_lon=f"{frame.lon:.10f}",
                     origin_h=f"{frame.h:.5f}", origin_source=frame.source, origin_key=frame.key(),
                     local_crs_name=frame.crs_name(), local_wkt=frame.wkt2(), local_proj=frame.proj_string(),
                     local_proj_tmerc=frame.tmerc_proj_string(), local_srs_id=LOCAL_SRS_ID,
                     local_frame="ENU: x east, y north, z up (m) about the origin, exact; WGS84")
        if base is not None:
            set_meta(gp, base_lat=f"{base.lat:.10f}", base_lon=f"{base.lon:.10f}", base_h=f"{base.h:.5f}",
                     base_source=base.source)
            if frame is not None:
                bx, by, bz = frame.enu(base.lat, base.lon, base.h)
                set_meta(gp, base_x_m=f"{bx:.5f}", base_y_m=f"{by:.5f}", base_z_m=f"{bz:.5f}")
        gp.con.execute("DELETE FROM icecube_meta WHERE key IN ('frame_source')")   # schema 1 key
        set_meta(gp, season=name, schema_version=SCHEMA_VERSION)
        update_local(gp, frame, recompute)

        if layer_files:
            counts = import_layers(gp, layer_files, frame)
            log(f"layers: {', '.join(f'{t} ({n})' for t, n in counts.items())}")
        for t in ("captures", "photos", "refs", "tracks", "track_points"):
            gp.update_extent(t)
        gp.touch("sessions")
        gp.commit()
        summary = {t: gp.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                   for t in ("sessions", "captures", "photos", "refs", "tracks", "track_points")}
    except BaseException:
        gp.con.rollback()   # the session being read is not half-written; earlier ones are kept
        gp.con.close()      # no handle stays open (Windows locks the file)
        raise
    gp.close()
    log(f"refs.csv rows seen: {n_refs}; photos extracted this run: {n_photos}")
    if frame is None:
        log("local frame: none (no origin or base given; x_m/y_m/z_m left empty)")
    else:
        log(f"local frame: ENU about {frame.name} ({frame.source})"
              + ("; x/y/z_m recomputed for all rows" if recompute else ""))
    if base is not None:
        log(f"base: {base.source}")
    log(f"{gpkg_path}: " + ", ".join(f"{t} {n}" for t, n in summary.items()))
    if progress is not None:
        progress(len(sources), len(sources), "done")
    return {"gpkg": str(gpkg_path), "season": name, "found": len(sources), "added": added,
            "unchanged": unchanged, "photos_extracted": n_photos, "refs_csv_rows": n_refs,
            "totals": summary, "origin": frame.name if frame is not None else None,
            "recomputed": bool(recompute)}


def main(argv=None):
    run(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
