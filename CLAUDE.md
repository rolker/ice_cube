# ice_cube

Tools and notes for a geodesy-grade backyard ice rink. See README.md for layout.

**This repo is public.** Never commit coordinates (lat/lon, ECEF, UTM, or anything
that can be converted back), street addresses, NTRIP credentials, people's names,
or photos with EXIF GPS. Town-level location ("Epping, NH") is fine. Figures must use
local coordinates relative to an arbitrary origin, and no basemap imagery.

Do not commit indexes, links, file IDs or folder structure from the owner's Google
Drive. Those go in `private/` (start with `private/drive-index.md`), which is gitignored.

## Scripts

Scripts take site-specific values from `config.toml` or command-line arguments (arguments
take priority), never hardcoded, so the repo stays free of personal paths, Drive IDs and
secrets. Add any new setting to `config.example.toml`. Beyond that, write for a reader
with an agent: clear names, a docstring saying what a script expects and produces, and no
hidden assumptions. Polished interfaces for other people's data are not a goal.
