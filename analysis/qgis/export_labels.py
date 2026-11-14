#!/usr/bin/env python3
"""Export the season's captures, with the labels and notes edited in QGIS, to CSV.

    python3 analysis/qgis/export_labels.py [--config config.toml] [--gpkg SEASON.gpkg]
            [--out FILE.csv] [--classes surface,object,ref,control] [--labelled-only]
            [--session NAME ...]

Reads the `captures` table written by import_sessions.py (default gpkg: `[season].gpkg`
of the config; output: `[season].labels_csv`, else `<gpkg stem>-captures.csv` next to the
gpkg). The CSV is private: it holds positions.

Columns: the SW Maps CSV columns that icecube's `swmaps_csv` reader already understands,

    Latitude, Longitude, Elevation (= ground ellipsoidal height), Time (UTC),
    Fix ID, Remarks (= "<class> <label>"), Horizontal Accuracy, Vertical Accuracy,

followed by session, n, class, label, note, utc, h_ant, pole_m, fix, n_epochs, sd_e_m,
sd_n_m, sd_u_m, age_s, x_m, y_m, z_m (exact local ENU about the origin, e.g. monument G;
see local_frame.py). So an icecube survey entry can use the file as is:

    [[surveys]]
    reader = "swmaps_csv"
    path = ".../2026-2027-captures.csv"
    exclude_remarks = ["object", "ref"]     # or a label, e.g. "deck"

The accuracy columns are left empty on purpose: a capture's scatter (sd_*) is over 1 s of
epochs (30 s for a ref) and says nothing about the slowly varying RTK bias, so icecube
should use its configured sigma defaults. `--accuracy-from-scatter` fills them anyway.
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_sessions import _abs, load_config  # noqa: E402

FIELDS = ["session", "n", "class", "label", "note", "utc", "h_ant", "pole_m", "fix", "n_epochs",
          "sd_e_m", "sd_n_m", "sd_u_m", "age_s", "x_m", "y_m", "z_m"]
SWMAPS = ["Latitude", "Longitude", "Elevation", "Time", "Fix ID", "Remarks",
          "Horizontal Accuracy", "Vertical Accuracy"]


def export(gpkg, out, classes=("surface", "object", "ref", "control"), labelled_only=False,
           sessions=None, accuracy_from_scatter=False):
    """Write the CSV; returns the number of rows."""
    con = sqlite3.connect(f"file:{Path(gpkg).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = (f"SELECT * FROM captures WHERE lat IS NOT NULL AND class IN ({','.join('?' * len(classes))})")
    params = list(classes)
    if labelled_only:
        q += " AND ((label IS NOT NULL AND label != '') OR (note IS NOT NULL AND note != ''))"
    if sessions:
        q += f" AND session IN ({','.join('?' * len(sessions))})"
        params += list(sessions)
    rows = con.execute(q + " ORDER BY utc, session, n", params).fetchall()
    con.close()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SWMAPS + FIELDS)
        for r in rows:
            utc = (r["utc"] or "").replace("T", " ").replace("Z", "")
            ha = va = ""
            if accuracy_from_scatter and r["sd_e_m"] is not None:
                ha = f"{math.hypot(r['sd_e_m'], r['sd_n_m']) / math.sqrt(2):.4f}"
                va = f"{r['sd_u_m']:.4f}"
            remark = f"{r['class']} {r['label'] or ''}".strip()
            w.writerow([f"{r['lat']:.9f}", f"{r['lon']:.9f}",
                        "" if r["h_ground"] is None else f"{r['h_ground']:.4f}",
                        f"{utc} UTC" if utc else "", r["fix"], remark, ha, va]
                       + ["" if r[k] is None else r[k] for k in FIELDS])
    return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="TOML config (default: config.toml at the repo root, if any)")
    ap.add_argument("--gpkg", help="season GeoPackage (default [season].gpkg)")
    ap.add_argument("--out", help="CSV to write (default [season].labels_csv or <gpkg>-captures.csv)")
    ap.add_argument("--classes", default="surface,object,ref,control",
                    help="comma-separated capture classes to export")
    ap.add_argument("--labelled-only", action="store_true", help="only captures with a label or note")
    ap.add_argument("--session", nargs="*", help="only these sessions")
    ap.add_argument("--accuracy-from-scatter", action="store_true",
                    help="fill the accuracy columns from the capture scatter")
    args = ap.parse_args(argv)
    cfg, cfg_dir = load_config(args.config)
    season = cfg.get("season", {})
    gpkg = Path(args.gpkg) if args.gpkg else (_abs(season["gpkg"], cfg_dir) if "gpkg" in season else None)
    if gpkg is None:
        ap.error("give --gpkg or [season].gpkg in the config")
    out = (Path(args.out) if args.out else _abs(season["labels_csv"], cfg_dir) if "labels_csv" in season
           else gpkg.with_name(gpkg.stem + "-captures.csv"))
    n = export(gpkg, out, tuple(c.strip() for c in args.classes.split(",") if c.strip()),
               args.labelled_only, args.session, args.accuracy_from_scatter)
    print(f"{out}: {n} captures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
