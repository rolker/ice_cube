# Agent Guide: ice_cube

> Tools and notes for a geodesy-grade backyard ice rink in Epping, NH: a home GNSS base
> station, a rover logger, siting scripts and a surface and ice-growth estimator, plus the
> story of how the rink turned into a geodesy project.

## Rules that come first

**This repository is public.** Before committing, writing a plan, a review comment or a
pull request description, check that it contains none of:

- coordinates of the site in any form that can be converted back: latitude/longitude,
  ECEF, UTM, absolute ellipsoidal heights;
- street addresses, NTRIP hosts or credentials, people's names;
- photos with EXIF GPS (`*.jpg`, `*.jpeg`, `*.heic` are ignored; strip EXIF before a
  `git add -f`);
- indexes, links, file IDs or folder structure from the owner's Google Drive (those go in
  the ignored `private/`).

Town-level location ("Epping, NH") is fine. Figures use local coordinates relative to an
arbitrary origin, with no basemap imagery. Site-specific values come from ignored config
files (`config.toml`, `analysis/site_config.json`, a local `*.toml` for `icecube`), never
from code. `.gitignore` default-denies survey, GIS and GNSS log formats (`*.csv`,
`*.geojson`, `*.ubx`, `*.rtcm3`, ...); a file that needs one of them committed is a
decision for the owner, not a `git add -f`.

The authoritative copy of these rules is `CLAUDE.md`; this guide repeats them so the
workspace's review skills, which read `.agents/`, check them too.

## Components

| Path | Language | What it is |
|---|---|---|
| `icecube/` | Python (numpy, matplotlib) | The surface and ice-growth estimator: crossover adjustment of pass biases, per-node fusion, growth between dates. `python -m icecube run <config.toml>` |
| `analysis/` | Python (numpy, scipy, pandas, matplotlib, pyproj) | Siting and survey-quality scripts: ground model, footprint search, survey wander, u-blox log quality, still-pole detection |
| `analysis/qgis/` | Python (standard library; QGIS 3.34+ / 4.x for the plugin) | Logger sessions into a season GeoPackage, the QGIS project builder, the Ice Cube QGIS plugin, the soundings table, local imagery and parcel copies |
| `logger/` | Java, Android (Gradle) | Ice Cube Logger: logs the rover over Bluetooth or USB with its own NTRIP client and phone IMU, captures points on a local map |
| `ha-addon/gnss_base/` | Python (aiohttp, pyserial) | Home Assistant app that runs the base receiver: raw logging, commands, NTRIP caster and upload, live position |
| `ha-addon/ntrip_base/` | - | Earlier relay draft, retired, kept for the record |
| `docs/` | Markdown | `howto/` reference, `story/` narrative (outlines), `log.md` dated journal |
| `figures/` | PNG | Published figures, local coordinates only |

## Key files to read first

1. `CLAUDE.md` and `README.md`: the rules and the layout.
2. `docs/log.md`: the dated journal; the current state of the survey and base station.
3. `docs/howto/ice-cube.md`: the estimator's error model and how to run it.
4. `docs/howto/qgis.md`: the survey pipeline from logger session to QGIS project.
5. `analysis/README.md`, `logger/README.md`, `ha-addon/gnss_base/README.md`: each
   component's own reference.
6. `icecube.example.toml`, `config.example.toml`, `analysis/site_config.example.json`:
   the templates for the ignored local config.

## Build and test

The workspace runs `python3 -m unittest discover icecube/tests` (the registered test
command) from the repository root; it needs `numpy` and `matplotlib`. Nothing in the
Python parts is built.

Other components test on their own:

- `analysis/qgis/`: from the repository root, `python3 -m unittest discover -s analysis/qgis/tests`
  (tests that need QGIS or rasterio are skipped without them).
- `ha-addon/gnss_base/`: from that folder, with `aiohttp` and `pyserial` (Python 3.11+):
  `python -m unittest tests.test_app tests.test_outputs tests.test_measured tests.test_reference tests.test_ntripclient tests.test_datums`
- `logger/`: from that folder, `./gradlew assembleDebug test lint` (needs the Android SDK).

Tests use synthetic data only (made-up coordinates at the equator, or plain local
metres); keep it that way.

## Common pitfalls

- The real site data lives in Google Drive and the ignored `data/` and `private/`; it is
  never needed to run the tests. Do not go looking for it to "make a test realistic".
- New settings go in the matching `*.example.*` template, and command-line arguments take
  priority over config files.
- The orthophoto and parcel copies `analysis/qgis/` fetches are for the local QGIS
  project only; they never go into the repository or into a published figure.
- The local ENU frame and its origin are private config; code and docs refer to it, never
  to its values.
- Changes that touch the base receiver (the Odroid running Home Assistant) are proposed
  and agreed with the owner before anything is installed or sent to it.
