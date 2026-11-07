# ice_cube

Tools and notes for a geodesy-grade backyard ice rink. See README.md for layout.

**This repo is public.** Never commit coordinates (lat/lon, ECEF, UTM, absolute ellipsoidal
heights, or anything that can be converted back), street addresses, NTRIP hosts or
credentials, people's names, or photos with EXIF GPS. Town-level location ("Epping, NH")
is fine. Figures must use local coordinates relative to an arbitrary origin, and no
basemap imagery.

Do not commit indexes, links, file IDs or folder structure from the owner's Google
Drive. Those go in `private/` (start with `private/drive-index.md`), which is gitignored.

## Layout

- `analysis/`: Python siting and survey-quality scripts (ground model, footprint search,
  survey wander, u-blox log quality). See `analysis/README.md`. Site-specific values for
  these scripts (the yard's location, data paths) live in the gitignored
  `analysis/site_config.json`; `analysis/site_config.example.json` is the template.
- `logger/`: Ice Cube Logger, an Android app that logs the rover over USB with its own
  NTRIP client and phone IMU data. See `logger/README.md`.
- `ha-addon/gnss_base/`: the Home Assistant app that runs the base receiver.
- `docs/`: `howto/` for reference, `story/` for the narrative, `log.md` for the journal.

## Scripts

Scripts take site-specific values from `config.toml` or command-line arguments (arguments
take priority), never hardcoded, so the repo stays free of personal paths, Drive IDs and
secrets. Add any new setting to `config.example.toml`. Beyond that, write for a reader
with an agent: clear names, a docstring saying what a script expects and produces, and no
hidden assumptions. Polished interfaces for other people's data are not a goal.
