# Survey data in QGIS

How to get Ice Cube Logger sessions into QGIS, look at them, and label the captures. The
scripts are in `analysis/qgis/`; the logger's files are described in `../../logger/README.md`.

The pipeline:

1. The phone shares each session as `IceCubeLogger_<session>.zip` to your Drive season
   folder.
2. `import_sessions.py` reads every zip (and any extracted session folder) in that folder
   into one GeoPackage per season, `<season>.gpkg`, and copies the photos next to it.
3. `build_project.py`, run once inside QGIS, makes `<season>.qgz` with the layers styled,
   the map in local metres about the base, and forms for labelling.
4. You label captures in QGIS. Re-running the import after new sessions adds them and
   keeps your labels. `export_labels.py` writes the captures with labels to a CSV that
   icecube reads.

Everything the import writes holds positions: the gpkg, the photos, the CSV and the .qgz
(it carries the local CRS). Keep them in Drive or a local folder, never in this repository.

## Install QGIS (Windows)

From qgis.org, Download, take the **Long Term Release (LTR)** standalone installer for
Windows and install it with the defaults. That gives "QGIS <version> LTR" in the Start
menu and the "OSGeo4W Shell", a command prompt with QGIS's own Python, which is enough
to run the import (it needs only the Python standard library, 3.11 or later).

## Where the data lives

A layout that works (names are only a suggestion):

    <your Drive season folder>/
      sessions/        the session zips shared from the phone (and older ones, any name)
      layers/          the map-layer GeoJSON files the phone uses (base, boundary, ...)
      qgis/            output: 2026-2027.gpkg, photos/, 2026-2027.qgz, the labels CSV

Drive for desktop shows the folder as a drive (e.g. `G:\My Drive\...` on Windows,
`/mnt/g/My Drive/...` from WSL). A GeoPackage is a single SQLite file: fine in Drive as
long as it is open on one computer at a time and Drive has finished syncing before you
open it elsewhere. If Drive gets in the way, put `qgis/` in a local folder instead and
copy it to Drive now and then.

## Configure

Copy `config.example.toml` at the repo root to `config.toml` (git-ignored) and set the
`[season]` and `[site]` sections: the season name, `input_dir` (the sessions folder),
`gpkg` (the output file), `layers` (the GeoJSON files) and the local frame source, usually
`base_geojson` pointing at the layer file with the `kind: base` point. Relative paths are
relative to the config file. Or keep a private config anywhere and pass `--config`.

The local frame is a transverse Mercator projection centred on the base, scale 1, metres
east and north of the base (`+proj=tmerc +lat_0=<base lat> +lon_0=<base lon> +k=1
+ellps=WGS84`). It is built from your config at import time and stored in the gpkg; the
repository holds no coordinates. Within a few hundred metres it agrees with the logger
map's east/north to well under a millimetre, and distances measured in QGIS are true
metres.

## Run the import

From WSL, in the repo:

    python3 analysis/qgis/import_sessions.py

or from the OSGeo4W Shell (or any Windows Python 3.11+):

    python analysis\qgis\import_sessions.py --config config.toml

Without arguments it reads `[season].input_dir`. You can also name zips or folders:
`import_sessions.py "G:\My Drive\...\IceCubeLogger_2026-11-20_13-05-00.zip"`. Other
options: `--gpkg`, `--season`, `--layers`, `--base-geojson`, `--force` (re-read sessions
that look unchanged), `--no-photos`; `--help` lists them.

It prints one line per session (format, app version, captures, photos, track epochs) and
the row counts. **Close the project in QGIS first**: QGIS keeps the file open.

What it handles:
- Logger 0.5 and later: `points.csv` (surface, object, ref, photo rows), photos,
  `refs.csv` in the zip, pole checks from `events.csv`.
- 0.3/0.4 sessions: each `control n=...` mark becomes a capture of class `control`, its
  position averaged from the GGA in `gps.ubx` over the last second before the mark (the
  0.5 Surface rule), ground = antenna minus the pole height logged with the mark.
- 0.2 sessions: only the track (their control marks carry no number or pole height).
- Zips named `IceCubeLogger_<session>.zip`, older ones with other names or without
  `.zip`, snapshot zips (a full share of the same session wins), and extracted folders.

## The tables

| table | what | yours to edit |
|---|---|---|
| `captures` | one point per capture, key session + n; class, time, heights, fix, scatter, photo, E/N/U about the base | `note`, `label` |
| `photos` | one point per photo, at its capture | `note` |
| `refs` | ref occupations, with pole-check result, visits, and `site` (refs within 0.30 m share a site number) | `note` |
| `tracks` | the GGA track at 1 Hz, one line per stretch of the same fix quality | |
| `track_points` | the 1 Hz epochs: fix, satellites, HDOP, correction age | |
| `sessions` | one row per session: times, app, link, caster mount, frame lines, base station, pole heights, counts, link losses, % fixed | `note` |
| `layer_*` | your map-layer GeoJSON files, one table per file and geometry type | (replaced on import) |

Point heights (Z) are ellipsoidal: the ground height for captures, photos and refs, the
antenna height for tracks. `u_m` is the ground height minus the base's height.

## Build the QGIS project (once)

1. Start QGIS. Plugins > Python Console. In the console's toolbar, click "Show Editor".
2. In the editor, "Open Script…" and pick `analysis/qgis/build_project.py` from the repo
   (from Windows the WSL repo is under `\\wsl$\<distro>\home\...`).
3. Click "Run Script". Pick your `<season>.gpkg` when asked.

It saves `<season>.qgz` next to the gpkg and opens it. From then on, open the .qgz
(Project > Open). Run the script again only to get fresh styles; it rewrites the .qgz,
never the data.

What you see: captures by class (surface cyan dots, object magenta squares, ref dark
rings, photo brown cameras, control orange triangles) labelled S12, O13, R14 plus your
label; refs as rings, red where the pole check failed, with the height difference to the
last visit; monuments green (measured) or orange rings (predicted) with their
uncertainty; boundary, rink, sheds and other layers in the colours of their GeoJSON;
tracks as thin grey lines (solid fixed, dashed float). No basemap. The track points
layer is off; tick it to see each epoch.

## Label objects

1. Select the `captures` layer, click the pencil (Toggle Editing).
2. Use Identify Features (the "i" cursor) and click a capture: its form opens. Only
   `note` and `label` can be typed in; the label box offers the labels used so far, so
   "spigot" stays "spigot". The photo, if any, is shown in the form.
3. Or open the attribute table (F6), filter with `"class" = 'object'`, and type in the
   `label` column.
4. Save with the "Save Layer Edits" button (or toggle editing off and say Save).

Labels appear on the map at once. Suggested use: `label` is a short name to group by
(`spigot`, `deck corner`, `stake`, `bad`), `note` is free text.

## View photos

- Identify a capture or a photo: the form shows the picture.
- Map tips: View > Show Map Tips, then hover over a capture or a camera: time, fix,
  scatter, height, your note and the photo.
- The files are in `photos/<session>/` next to the gpkg, so the project finds them by
  relative path wherever the folder goes.

## After new sessions

1. Share the session from the phone to the Drive sessions folder.
2. Close the project in QGIS.
3. Run the import again (same command). New sessions are added; sessions already imported
   from the same file are skipped; a session whose zip changed is re-read in place. Your
   `note` and `label` values are never overwritten, and no row is duplicated.
4. Open the .qgz again.

## Export labels for icecube

    python3 analysis/qgis/export_labels.py [--labelled-only] [--classes surface,object]

writes `<season>-captures.csv` next to the gpkg (or `[season].labels_csv`). It has the SW
Maps CSV columns icecube already reads (`Latitude`, `Longitude`, `Elevation` = ground
height, `Time`, `Fix ID`, `Remarks` = "<class> <label>"), then session, n, class, label,
note and the rest. In an icecube config:

    [[surveys]]
    id = "ice_dec"
    reader = "swmaps_csv"
    path = ".../qgis/2026-2027-captures.csv"
    exclude_remarks = ["object", "ref"]

Later icecube can read the gpkg directly: `SELECT lat, lon, h_ground, utc, fix, class,
label FROM captures` with sqlite3 is all a `logger_gpkg` reader needs (sessions give the
survey date; `label` and `class` replace remarks).

## Troubleshooting

- "database is locked": QGIS (or another program) has the gpkg open. Close the project.
- "no such function: ST_IsEmpty" when changing the gpkg with another SQLite tool: the
  spatial index triggers need GeoPackage functions. Edit in QGIS, or open the file with
  `gpkg_writer.connect()` from Python.
- QGIS asks for a CRS or the map is empty: check that the import printed a local frame
  ("local frame: base point in ..."); without one the project stays in longitude/latitude.
- The tests use only made-up data: `python3 -m unittest discover analysis/qgis/tests`.
