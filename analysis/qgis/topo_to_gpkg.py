"""Load an Ice-Cube topography run (python -m icecube run, a config with [[grids]]) into
the season GeoPackage for QGIS.

    python3 analysis/qgis/topo_to_gpkg.py <icecube output dir> [--gpkg season.gpkg] [--config config.toml]

Writes (each replaced on every run; derived data, no user columns):
- `layer_topo_contours`: the contour lines of every `contours_<surface>_<grid>.geojson` in the
  run folder, LINESTRING in the gpkg's local CRS (icecube_meta local_srs_id), with level (m,
  local z), surface, grid, interval, name (the level, the project builder's label) and
  stroke_rgba (whole metres darker; the builder colours map lines by it).
- `topo_points`: every sounding of every `points_<surface>.csv`, POINT Z in the local CRS at
  its corrected height, with survey, session (group), pass, anchor, the raw height, the pass
  bias and its sigma, and the sounding's sigmas: the points-by-pass / bias table.

The rasters (`z_*.asc`, `sigma_*.asc`, `count_*.asc`, `flags_*.asc`, each with a .prj) are
not copied: add them in QGIS from the run folder (Layer > Add Raster Layer); the .prj puts
them on the project's local CRS. The contours and points are in the run's local frame, which
must be the gpkg's: run icecube with `[site] origin_from_gpkg` pointing at this gpkg (its
soundings reader refuses a gpkg whose origin differs from the run's).
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gpkg_writer as gw  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

CONTOUR_COLS = [("level", "REAL"), ("surface", "TEXT"), ("grid", "TEXT"), ("interval", "REAL"),
                ("name", "TEXT"), ("stroke_rgba", "TEXT")]   # name = the level as a label; whole metres darker
POINT_COLS = [("surface", "TEXT"), ("survey", "TEXT"), ("sid", "TEXT"), ("session", "TEXT"), ("pass", "TEXT"),
              ("anchor", "TEXT"), ("x_m", "REAL"), ("y_m", "REAL"), ("z_m", "REAL"), ("bias_m", "REAL"),
              ("bias_sigma_m", "REAL"), ("z_corrected_m", "REAL"), ("sigma_z_m", "REAL"), ("sigma_xy_m", "REAL")]


def local_srs(con):
    m = dict(con.execute("SELECT key, value FROM icecube_meta"))
    if not m.get("local_srs_id"):
        raise SystemExit("the gpkg has no local CRS (icecube_meta local_srs_id): import sessions with [site.origin]")
    return int(m["local_srs_id"])


def load(run_dir, gpkg, log=print):
    run_dir, gpkg = Path(run_dir), Path(gpkg)
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    srs = local_srs(con)
    con.close()
    gp = gw.GeoPackage(gpkg)
    try:
        for t in ("layer_topo_contours", "topo_points"):
            if t in gp.tables():
                gp.drop_table(t)
        gp.ensure_feature_table("layer_topo_contours", CONTOUR_COLS, "LINESTRING", srs_id=srs,
                                description="Ice-Cube contours (analysis/qgis/topo_to_gpkg.py)")
        rows, nfiles = [], 0
        for f in sorted(run_dir.glob("contours_*.geojson")):
            nfiles += 1
            for ft in json.loads(f.read_text())["features"]:
                p = ft["properties"]
                coords = [tuple(c[:2]) for c in ft["geometry"]["coordinates"]]
                if len(coords) < 2:
                    continue
                lev = float(p.get("level"))
                major = abs(lev - round(lev)) < 1e-6
                rows.append({"level": lev, "surface": p.get("surface"), "grid": p.get("grid"),
                             "interval": p.get("interval"), "name": f"{lev:.2f}",
                             "stroke_rgba": "60,60,60,255" if major else "150,150,150,255",
                             "geom": gw.gpkg_geometry("LINESTRING", coords, srs)})
        gp.insert("layer_topo_contours", rows)
        gp.update_extent("layer_topo_contours")
        log(f"layer_topo_contours: {len(rows)} lines from {nfiles} files")
        gp.ensure_feature_table("topo_points", POINT_COLS, "POINT", srs_id=srs, z=True,
                                description="Soundings by pass with crossover biases (analysis/qgis/topo_to_gpkg.py)")
        pts = []
        for f in sorted(run_dir.glob("points_*.csv")):
            surface = f.stem[len("points_"):]
            with open(f, newline="") as fh:
                for r in csv.DictReader(fh):
                    d = {"surface": surface, "survey": r["survey"], "sid": r["sid"], "session": r["group"],
                         "pass": r["pass"], "anchor": r["anchor"] or None}
                    for k in ("x_m", "y_m", "z_m", "bias_m", "bias_sigma_m", "z_corrected_m", "sigma_z_m", "sigma_xy_m"):
                        d[k] = float(r[k])
                    d["geom"] = gw.point(d["x_m"], d["y_m"], d["z_corrected_m"], srs_id=srs)
                    pts.append(d)
        gp.insert("topo_points", pts)
        gp.update_extent("topo_points")
        gp.commit()
        log(f"topo_points: {len(pts)} points")
    finally:
        gp.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="icecube output folder (holds contours_*.geojson, points_*.csv)")
    ap.add_argument("--gpkg", help="season GeoPackage (default [season].gpkg of the config)")
    ap.add_argument("--config", help="site config TOML (default config.toml at the repo root)")
    a = ap.parse_args(argv)
    gpkg = a.gpkg
    if not gpkg:
        p = Path(a.config) if a.config else REPO / "config.toml"
        cfg = tomllib.loads(p.read_text())
        g = cfg.get("season", {}).get("gpkg")
        if not g:
            raise SystemExit("give --gpkg or [season].gpkg in the config")
        gpkg = Path(g) if Path(g).is_absolute() else p.resolve().parent / g
    load(a.run_dir, gpkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
