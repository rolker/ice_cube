# Survey data in QGIS

How to get Ice Cube Logger sessions into QGIS, look at them, and label the captures. The
**Ice Cube plugin** for QGIS does it from a toolbar; the scripts it runs are in
`analysis/qgis/` and still work on their own (see "Without the plugin" below). The
logger's files are described in `../../logger/README.md`.

The pipeline:

1. The phone shares each session as `IceCubeLogger_<session>.zip` to your Drive season
   folder.
2. **Import new sessions** reads every zip (and any extracted session folder) in that
   folder into one GeoPackage per season, `<season>.gpkg`, and copies the photos next to
   it. Sessions already imported are skipped; your labels and notes are kept.
3. **Build / refresh project** makes `<season>.qgz` with the layers styled, the map in
   local metres about the origin (monument G), and forms for labelling.
4. You label captures in QGIS. **Export labels** writes the captures with labels to a
   CSV that icecube reads.

Everything the import writes holds positions: the gpkg, the photos, the CSV and the .qgz
(it carries the local CRS). Keep them in Drive or a local folder, never in this repository.

## Install QGIS (Windows)

From qgis.org, Download: the standalone installer for Windows, either the **Long Term
Release** (3.34 or later) or QGIS 4.x; the plugin works on both. Install with the
defaults.

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

## Install the plugin (once, and again to update)

1. Build the zip, in the repo (WSL or any Python 3.11+):

       python3 analysis/qgis/package_plugin.py <a folder Windows can see>

   It writes `icecube_qgis.zip`: the plugin with the pipeline modules (import, export,
   project builder) copied in, so QGIS needs nothing else from the repo. It holds code
   only.
2. In QGIS: Plugins > Manage and Install Plugins… > **Install from ZIP**, pick
   `icecube_qgis.zip`, Install Plugin. (If QGIS warns about installing plugins from
   untrusted sources, that is the generic warning for any zip.) The "Ice Cube" toolbar
   appears, and the same actions are under Plugins > Ice Cube.
3. To update after a change in the repo: build the zip again and install it the same
   way; QGIS replaces the old version. If an action still behaves the old way, restart
   QGIS (Python keeps loaded modules).

## Settings (once)

Plugins > Ice Cube > **Settings** (or the toolbar's gear):

| setting | what |
|---|---|
| Season GeoPackage | the `<season>.gpkg` to write; `photos/`, the `.qgz` and the labels CSV go next to it. A local folder is safest (see "Where the data lives") |
| Season input folder | where the session zips land, e.g. the Drive sessions folder (searched recursively) |
| Site config (TOML) | the private config with `[season]` and `[site]`: the season name, the base and the origin (`[site.origin]`), see "The site config" below |
| Map layer GeoJSON files | the logger's map layers (base, boundary, ...), imported as `layer_*` tables |

Empty fields fall back to the site config's `[season]` (`gpkg`, `input_dir`, `layers`,
relative to the config file), so a site config with everything in it is enough. The
dialog shows the paths in use. QGIS keeps the settings in its profile, not in the repo.

### The site config

A TOML file in the layout of `config.example.toml` at the repo root (the scripts read
the same file as `config.toml` or with `--config`). It is private: keep it outside the
repository (next to the gpkg is fine) and never commit it. Set the `[season]` and
`[site]` sections: the season name, `input_dir` (the sessions folder), `gpkg` (the
output file), `layers` (the GeoJSON files), the base (usually `base_geojson` pointing at
the layer file with the `kind: base` point) and the origin of the local frame,
`[site.origin]` with `name`, `lat`, `lon`, `h` (ellipsoidal) and `source`. Relative paths
are relative to the config file, so a folder holding `site.toml`, `layers/` and the gpkg
can move as a whole.

## Import new sessions

Toolbar: the ice cube button (Plugins > Ice Cube > Import new sessions).

- You can leave the season project open. If a layer from the gpkg has unsaved edits
  (labels, notes), the plugin asks: Save, Discard or Cancel. If the project itself has
  unsaved changes it asks to save them. Then it closes the project, so QGIS lets go of
  the gpkg file (Windows keeps open files locked).
- The import runs in the background (progress in the status bar, cancel there too); QGIS
  stays usable. Sessions finished before a cancel are kept.
- Afterwards the project opens again at the same map view, showing the new rows. If the
  import added tables (a new map-layer file, the first import of refs, ...) it rebuilds
  the project instead (keeping the previous one as `<season>.qgz.bak`). With no project
  open it opens the season project, building it the first time.
- A message bar sums up: new sessions, re-read ones, unchanged ones, captures and photos
  read, and the season totals. The full report (one line per session: format, app
  version, captures, photos, track epochs) is in View > Panels > Log Messages, tab
  **Ice Cube**. Errors show in red in the message bar and in that tab, never silently.

What it reads:
- Logger 0.5 and later: `points.csv` (surface, object, ref, photo rows), photos,
  `refs.csv` in the zip, pole checks from `events.csv`.
- 0.3/0.4 sessions: each `control n=...` mark becomes a capture of class `control`, its
  position averaged from the GGA in `gps.ubx` over the last second before the mark (the
  0.5 Surface rule), ground = antenna minus the pole height logged with the mark.
- 0.2 sessions: only the track (their control marks carry no number or pole height).
- Zips named `IceCubeLogger_<session>.zip`, older ones with other names or without
  `.zip`, snapshot zips (a full share of the same session wins), and extracted folders.
  A session whose zip changed is re-read in place; nothing is duplicated.

## Build / refresh project

Toolbar: the refresh button. It builds `<season>.qgz` next to the gpkg and opens it. If
a `<season>.qgz` exists it is kept as `<season>.qgz.bak` (replacing an older .bak) and
overwritten: nothing to delete by hand. It asks first only when the open project has
unsaved changes or a layer has unsaved edits. Use it the first time, and later for fresh
styles; it never changes the data. From then on Project > Open (or Recent) opens the
season project.

It reads the CRS from the gpkg (`fromWkt`, then the proj strings as fallbacks), saves it
as a user CRS "Ice Cube local ENU (origin G)" (Settings > Custom Projections shows it), and
logs "local CRS from local_wkt (WKT2)" and "user CRS USER:1000xx ..." (Ice Cube log tab).
The status bar's CRS button then shows that name, not "Unknown CRS" or EPSG:4326.

What you see: captures by class (surface cyan dots, object magenta squares, ref dark
rings, photo brown cameras, control orange triangles) labelled S12, O13, R14 plus your
label; refs as rings, red where the pole check failed, with the height difference to the
last visit; monuments green (measured) or orange rings (predicted) with their
uncertainty; boundary, rink, sheds and other layers in the colours of their GeoJSON;
tracks as thin grey lines (solid fixed, dashed float). No web basemap: the Imagery group
at the bottom holds the local orthophoto copies, if any (see Imagery). The track points
layer is off; tick it to see each epoch.

## Label objects

1. Select the `captures` layer, click the pencil (Toggle Editing).
2. Use Identify Features (the "i" cursor) and click a capture: its form opens. Only
   `note` and `label` can be typed in; the label box offers the labels used so far, so
   "spigot" stays "spigot". The photo, if any, is shown in the form.
3. Or open the attribute table (F6), filter with `"class" = 'object'`, and type in the
   `label` column.
4. Save with the "Save Layer Edits" button (or toggle editing off and say Save).

Labels appear on the map at once. Before the next import the plugin asks about edits you
have not saved. Suggested use: `label` is a short name to group by
(`spigot`, `deck corner`, `stake`, `bad`), `note` is free text.

## Open photos

Toolbar: the folder button opens `photos/` next to the gpkg in Explorer. In the map:

- Identify a capture or a photo: the form shows the picture.
- Map tips: View > Show Map Tips, then hover over a capture or a camera: time, fix,
  scatter, height, your note and the photo.
- The project finds the photos by relative path wherever the folder goes.

## Export labels for icecube

Toolbar: the save-as button. It saves pending edits first (asks), then writes the
captures to a CSV you choose; the default is `<gpkg stem>-captures.csv` next to the gpkg
(or `[season].labels_csv` of the site config). From the command line:

    python3 analysis/qgis/export_labels.py [--labelled-only] [--classes surface,object]

The CSV has the SW Maps CSV columns icecube already reads (`Latitude`, `Longitude`, `Elevation` = ground
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

## The local frame

ROS style local tangent plane (ENU, as GeographicLib's LocalCartesian): **x east, y north,
z up, in metres, origin at lot monument G**, the upright granite bound next to corner A
(the most reliable monument). G's position is its measured one (the mean of its four
visits on 2026-11-11), and its height is the ground at the bound, so G is x = y = z = 0.
The numbers are only in the private site config; the repository holds no coordinates.

- Every point table (captures, photos, refs, track_points, the `layer_*_points` tables)
  has `x_m`, `y_m`, `z_m`, computed exactly: ECEF of the point minus ECEF of G, rotated
  to east/north/up at G (WGS84). `z_m` is height above the tangent plane at G, so it
  includes the earth-curvature drop (0.8 mm at 100 m). They replace the old `e_m`,
  `n_m`, `u_m` about the base.
- The base is kept as a separate known point (`icecube_meta`: `base_lat/lon/h` and its
  `base_x_m/y_m/z_m` from G). Without `[site.origin]` the base is the origin.
- If the origin changes in the config, the next import recomputes `x_m`/`y_m`/`z_m` of
  every row; your `note` and `label` values are not touched.
- The QGIS CRS is called **"Ice Cube local ENU (origin G)"**: an orthographic projection
  (EPSG method 9840, on the WGS84 ellipsoid) at G with no false easting/northing, axes
  east and north in metres. The cursor coordinates in the status bar are x, y from G.
  The orthographic map is the exact ENU east/north of points on the ellipsoid; for a point
  h metres above it, true x/y are larger by h / 6390 km (under 0.2 mm across the yard,
  under 1 mm within 100 m). Use the `x_m`, `y_m`, `z_m` columns for numbers; the map
  places things to well under a millimetre. Distances measured in QGIS are planar metres
  (the project's ellipsoid is set to None).

## Imagery

The project's **Imagery** group (at the bottom, collapsed) shows local GeoTIFF copies of the
NH GRANIT orthophotos from the `imagery/` folder next to the season gpkg: the newest year
is ticked, older years and the LiDAR shaded relief are there but unticked. They load from
disk, so QGIS makes no web requests and works offline.

What the copies are: `analysis/qgis/fetch_imagery.py` downloads each dataset once over the
lot (the lot boundary's box plus 30 m) in the service's own projection and pixels (NH State
Plane, NAD83; the shaded relief in UTM 19N), then resamples it into the local frame (x east,
y north from G, north up) at the native pixel size (0.15 m for the 2021-22 6 inch photos,
0.30 m for the 1 ft years, 1 m for the 1990s DOQs, 0.76 m for the relief), bilinear, and
writes it with the local CRS. RGB only: band 4 of the GRANIT 4-band orthos is near-infrared
(the service's CIR view is the same raster) and is dropped. A `<dataset>.json` next to each
file records the dataset, years, pixel size, the shift applied and when it was fetched.

The datum shift, and why the live layers are off by ~1.3 m: our coordinates are ITRF2020
(the base's PPP frame) used as WGS84; GRANIT data are NAD83. At the site NAD83(2011) minus
ITRF2020 is E +0.561, N -1.193 m, so a NAD83 image drawn as if its coordinates were WGS84
(which is what QGIS does with a live GRANIT layer unless a datum transformation is set,
and the NAD83-WGS84 "transformations" it offers are null or 1 m class) sits 1.3 m north-east
of where it belongs relative to our points. The copies are moved by minus that (E -0.561,
N +1.193 m). NAD83(HARN), the label of the 2021-22 service, differs from NAD83(2011) by
about 2 cm here; NAD83(1986), if an older dataset really is on it, by about 0.2 m (not
applied: below what the 1 ft images can show).

The images' own registration is a separate error. Checked on 2026-11-11 with
`imagery_check.py crops` against RTK-measured features: in the 2021-22 copy, the garden
shed corners, the boat shed outline and the PPK house corner fall on the image's roof corners
within about 0.2 m (1-2 pixels; roof overhang and the lean of tall things in a non-true
ortho limit the check), so no extra shift. The granite bounds are not visible. The 2015 and
2010-11 copies agree with the 2021-22 one and the outlines to roughly 0.3-0.5 m by eye
(sheds, driveway edge, house); automatic correlation between years did not give a stable
answer (leaf, shadow and building changes), so no shift either. The 1992-98 DOQ predates the
house and has nothing to check against; treat its position as unverified (metres).

Refetch or add years: put an `[imagery]` section in your site config (template in
`config.example.toml`: datasets, extent, `datum_shift_m`, per-dataset `extra_shift_m`) and
run, with numpy and rasterio installed (a venv: `pip install numpy rasterio`):

    python3 analysis/qgis/fetch_imagery.py --config <site config> [--datasets NAME ...]
    python3 analysis/qgis/fetch_imagery.py --config <site config> --list    # which cover the lot

Dataset names are the ImageServer names under the service folder (GRANIT has NAIP years,
CIR versions and more); the coastal 1 ft sets do not reach Epping. Then Build / refresh
project. To measure an image's residual, `imagery_check.py crops IMAGE.tif --layers
<things/fence GeoJSON> --gpkg <season gpkg> --match "<names>"` draws the surveyed features
on crops of the image; `imagery_check.py align REF.tif IMAGE.tif --window x0 y0 x1 y1`
estimates one image's offset against another. A consistent offset goes in
`[imagery.extra_shift_m]`, and the next fetch applies it.

The copies show the site and carry its location in their CRS: **never commit imagery**
(`*.tif` is git-ignored) or put it in a public figure.

### Development extent

The same datasets are also copied over the whole subdivision, into `imagery/development/`
next to the gpkg; the project lists them under the lot copies as "... (development)". The
lot-sized copies stay as they were and draw on top; the newest development photo is ticked
too, so it fills in around the lot, and the others are unticked. The extent is the bounding
box of the subdivision's parcels (see below, `group = core`) plus 40 m:

    python3 analysis/qgis/fetch_imagery.py --config <site config> --out-dir <qgis folder>/imagery/development \
        --extent-layer <parcels GeoJSON> --extent-filter group=core --margin 40

Downloads go in tiles of at most 2048 px (`tile_px`; GRANIT's service advertises 4100 px
but answers HTTP 500 to 3000 x 3000 four-band requests), one at a time with a pause. Over a
subdivision-sized area the 6 inch photo is about 100 MB with DEFLATE; for much larger areas
`[imagery.compress]` can switch a dataset to JPEG-in-TIFF (about 5x smaller, lossy). Every
copy has overviews, so QGIS stays fast zoomed out.

## Parcels

`analysis/qgis/fetch_parcels.py` writes the tax parcels of a subdivision (the `where` of
`[parcels]`, e.g. one tax map lot's PID prefix) and the ring of parcels touching it as a
GeoJSON map layer, from NH GRANIT's ParcelMosaic (the towns' assessing parcels, NAD83 NH
State Plane). Each polygon is converted to geodetic NAD83 and moved into our frame by the
same datum shift as the imagery (E -0.561, N +1.193 m here). Properties: `pid`, `map`,
`lot`, `town`, `land_use`, `area_m2`, `area_ac`, `group` (core or neighbour), `kind =
parcel`, `source` (with the fetch date) and a `note`. Only those fields are ever requested
from the service: owner names and street or mailing addresses are not fetched, even
privately. It also prints the core parcels' extent, used for the development imagery.

    python3 analysis/qgis/fetch_parcels.py --config <site config>

Add the GeoJSON to `[season].layers` of the site config, then **Import new sessions** (it
reloads the map layers into the gpkg) and **Build / refresh project**: the parcels are in
"Map layers", thin grey outlines with small PID labels (map tips show area, note and source).

What they are not: tax-map polygons are drawn from the recorded plans and fitted to the
town's tax-map base; they are typically good to 1-3 m (worse on curves) and are **not
survey boundaries**. For our lot, the measured boundary (found monuments, RTK) lies about
2.2 m south of the datum-corrected parcel, with no significant rotation: that offset is the
tax map's placement, not the datum (without the datum shift the parcel happens to sit
closer, about 1.2 m off, because the two errors partly cancel). Use the parcels for context
(neighbours, open space, roads), never to place anything near a line.

The parcels GeoJSON holds the location like the imagery: keep it private.

## The tables

| table | what | yours to edit |
|---|---|---|
| `captures` | one point per capture, key session + n; class, time, heights, fix, scatter, photo, x/y/z from G | `note`, `label` |
| `photos` | one point per photo, at its capture | `note` |
| `refs` | ref occupations, with pole-check result, visits, and `site` (refs within 0.30 m share a site number) | `note` |
| `tracks` | the GGA track at 1 Hz, one line per stretch of the same fix quality | |
| `track_points` | the 1 Hz epochs: fix, satellites, HDOP, correction age | |
| `sessions` | one row per session: times, app, link, caster mount, frame lines, base station, pole heights, counts, link losses, % fixed | `note` |
| `layer_*` | your map-layer GeoJSON files, one table per file and geometry type | (replaced on import) |

Point heights (Z) are ellipsoidal: the ground height for captures, photos and refs, the
antenna height for tracks. `z_m` is the ground (antenna for track points) above the
tangent plane at G.

## Run Ice Cube (ground topography)

The ground model of the season is not a plugin action yet (it can become one: the steps
are scripts). From the repo, with a site config holding `[soundings]` (see
`config.example.toml`) and an icecube config with `[[grids]]` (see `icecube.example.toml`):

    python3 analysis/qgis/soundings.py --config <site.toml>          # soundings table in the gpkg
    python3 -m icecube run <topo.toml>                                 # grids, contours, figures
    python3 -m icecube validate <topo.toml> --grid lot                 # optional: how good the sigmas are
    python3 analysis/qgis/topo_to_gpkg.py <run dir> --config <site.toml>

Close the project in QGIS first (the scripts write the gpkg). Then Build / refresh project
picks up `layer_topo_contours` under Map layers (labelled with their level; whole metres
darker) and the `soundings` and `topo_points` tables can be added from the gpkg (Layer >
Add Layer > Add Vector Layer). Add the grids with Layer > Add Raster Layer: `z_*.asc`
(height, m), `sigma_*.asc` (node uncertainty), `count_*.asc`, `flags_*.asc` (-1 masked, else
the inconsistency count) from the run folder; each has a `.prj` with the project's local CRS.
How it works, the error budget and the validation: `docs/howto/ice-cube.md`, section 5.

## Without the plugin

The scripts the plugin runs work on their own; use them if the plugin is not installed
or to script things.

#### Import from a command line

From WSL, in the repo:

    python3 analysis/qgis/import_sessions.py

or from the OSGeo4W Shell (QGIS's own Python) or any Windows Python 3.11+:

    python analysis\qgis\import_sessions.py --config config.toml

Without arguments it reads `[season].input_dir`. You can also name zips or folders:
`import_sessions.py "G:\My Drive\...\IceCubeLogger_2026-11-20_13-05-00.zip"`. Other
options: `--gpkg`, `--season`, `--layers`, `--base-geojson`, `--origin LAT LON H` (with
`--origin-name`), `--force` (re-read sessions that look unchanged), `--no-photos`; `--help`
lists them. **Close the project in QGIS first**: QGIS keeps the file open.

### Build the project from the Python console

1. Plugins > Python Console; in the console's toolbar, click "Show Editor".
2. "Open Script…" and pick `analysis/qgis/build_project.py` (from Windows the WSL repo
   is under `\\wsl$\<distro>\home\...`).
3. "Run Script", and pick your `<season>.gpkg`. Same result as Build / refresh project,
   including the `.qgz.bak`.

## Troubleshooting

- "... is in use (database is locked)" from Import new sessions: another program (a
  second QGIS, DB Browser, Drive syncing the file) has the gpkg open. Close it and try
  again. From the command line: close the project in QGIS first.
- The Ice Cube toolbar is missing: Plugins > Manage and Install Plugins > Installed,
  tick "Ice Cube". View > Toolbars lists it too. If loading failed, the Python error is
  in the Log Messages panel (Python warning tab).
- "pipeline modules not found": the plugin folder was copied without `pipeline/`;
  install the zip built by `package_plugin.py`.
- "no such function: ST_IsEmpty" when changing the gpkg with another SQLite tool: the
  spatial index triggers need GeoPackage functions. Edit in QGIS, or open the file with
  `gpkg_writer.connect()` from Python.
- Build stops with "Ice Cube: no valid local CRS in the GeoPackage": the
  project is not built (it never falls back to longitude/latitude). Check that the
  import reported (Ice Cube log tab, or the console) "local frame: ENU about G (...)"; if not, add `[site.origin]` to the
  site config and import again. If it did, the message lists what QGIS rejected; send
  that line, the QGIS version (Help > About) and the PROJ version shown there.
- The status bar says "Unknown CRS" or the cursor shows degrees: the project was made
  by an older build_project.py, or with the old gpkg. Build / refresh project again (the
  old one is kept as .qgz.bak).
- The tests use only made-up data: `python3 -m unittest discover -s analysis/qgis/tests`.
