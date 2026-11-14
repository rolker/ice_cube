# Roadmap

## Direction

What must be true for the project's current goal, each line with the check that says
whether it is true today, and what was set aside on purpose. The workspace's sit-rep
runs the checks; this section holds no status and no dates. A line with no check is
judged by the owner.

**Goal.** This season's rink is sited, surveyed with the logger and built, and the
Ice-Cube estimator tracks it through the season. It is done when the season's growth
between survey dates is reported by `icecube` from logger surveys.

| Line | Check |
|---|---|
| The base station runs as a fixed base with a PPP coordinate, and serves corrections to the rover | `file-contains docs/log.md 60 h coordinate applied ; file-exists ha-addon/gnss_base/gnss_base/caster.py` |
| The logger records surveys over USB with its own NTRIP client, and guards against false fixes | `file-contains logger/README.md false-fix guard` |
| The footprint is chosen and the ground under it surveyed before building | |
| The rink is built and flooded | |
| `icecube` runs on logger sessions in the project's local frame | `file-contains icecube.example.toml origin_geodetic` |
| Ice growth between survey dates is reported through the season | |

| Deferral | Revisit check |
|---|---|
| Moving the base antenna to a permanent roof or mast mount (deferred to next year) | |
| An independent static height for the base (OPUS rapid static) | |
