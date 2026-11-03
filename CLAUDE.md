# ice_cube

Tools and notes for a geodesy-grade backyard ice rink. See README.md for layout.

**This repo is public.** Never commit coordinates (lat/lon, ECEF, UTM, or anything
that can be converted back), street addresses, NTRIP credentials, people's names,
or photos with EXIF GPS. Town-level location ("Epping, NH") is fine. Figures must use
local coordinates relative to an arbitrary origin, and no basemap imagery.

Do not commit indexes, links, file IDs or folder structure from the owner's Google
Drive. Those go in `private/` (start with `private/drive-index.md`), which is gitignored.

## Scripts

Published scripts must work for other people's data. Take paths and settings from
command-line arguments or `config.toml`, with arguments taking priority. Never hardcode
personal paths, Drive IDs or site-specific values. Keep `config.example.toml` generic,
and add any new setting there.
