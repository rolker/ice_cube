# Analysis

Python scripts behind the 2026–27 rink siting, the survey-wander check and the
still-pole point detector. Survey data and the yard's location stay out of the
repository.

## Setup

```bash
pip install numpy scipy pandas matplotlib pyproj
cp site_config.example.json site_config.json   # git-ignored
```

In `site_config.json`, set `origin_utm19n` to a point near the yard, the local
coordinates (metres east and north of that origin) of the control point, spigot,
stone-wall label and camper, and `data_dir` and `files` to your exported data.
You can also point `ICECUBE_CONFIG` at a config stored elsewhere.

## Run order

| Script | Does | Writes to `out/` |
|---|---|---|
| `ground.py` | Merged, Oct-only and Nov-only ground models on a 0.25 m grid, with leave-one-out error | `dem.pkl` |
| `search.py` | Best footprints by size, position and angle, avoiding obstacles and the east drop | |
| `candidates.py` | Fill, relief, clearances and stake-out corners in both frames for options A–C | `res.pkl` |
| `site_plan/build.py template.html out.html` | The interactive site plan page | |
| `survey_wander.py` | Repeat differences by time gap for each survey, and a crossover drift test | |
| `log_quality.py [log.ubx]` | Lost positions and RAWX epochs in a u-blox log, fix status | `gga.pkl` |
| `detect_points.py` | Still-pole points from the log, scored against SW Maps clicks | `dwells.pkl` |
| `fix_map.py` | Map of the rover track by RTK status | `fix-map.png` |
| `datum_check.py` | Measured PPP-to-MassDOT offset against ITRF2014 to NAD83(2011) | |

Fill uses a level rink with the water surface 5 cm above the 99th percentile of
ground inside the footprint. Generated pages and coordinates contain the real
location, so keep `out/` out of the repository.

## Survey data in QGIS (`qgis/`)

Ice Cube Logger sessions into one GeoPackage per season, for viewing and labelling in
QGIS. Standard library only (Python 3.11+); settings in the repo-root `config.toml`
(`[season]`, `[site]`, see `config.example.toml`) or `--config`. How-to:
`docs/howto/qgis.md`.

| Script | Does |
|---|---|
| `qgis/import_sessions.py [zips/folders]` | Sessions (0.5 points.csv, 0.3 control marks, 0.2 tracks), photos, refs, tracks and map layers into `<season>.gpkg`; incremental, keeps the `note`/`label` columns edited in QGIS |
| `qgis/build_project.py` | Run inside QGIS: `<season>.qgz` with the local CRS, styles, forms and photo map tips |
| `qgis/export_labels.py` | Captures with labels to a CSV in the SW Maps columns icecube's `swmaps_csv` reader takes |
| `qgis/gpkg_writer.py`, `logger_session.py`, `local_frame.py` | GeoPackage 1.3 writer on sqlite3, session parsing, local frame |

Tests (synthetic sessions near 0 N 0 E): `python3 -m unittest discover analysis/qgis/tests`.
