# ice_cube

I built a backyard ice rink in Epping, NH with no prior experience or plan, and started
using LLMs to help keep it up: photos and a forecast in, a plan for the next 48 hours out.
"I used the ice rink as an excuse to buy an RTK GPS." This repo is the tools that followed
(a survey workflow, a home base station, siting scripts) and the story of how a rink
turned into a geodesy project.

## Two halves

**Tools.** Code that runs the base station, logs the rover and sizes the rink. Published
with the data left out: the georeferenced survey data, logs and coordinates stay private,
and the scripts take everything site-specific from local config.

**Story.** The narrative, in my voice, in `docs/story/`. Right now the chapters are
outlines with the facts and prompts; the writing comes later. The journal in
`docs/log.md` has the dates and numbers.

## Layout

| Path | What is there |
| --- | --- |
| `docs/story/` | The chapters, in order. Outlines only so far. |
| `docs/howto/` | Reference: the base station as it stands, the RTK mountpoint test. |
| `docs/log.md` | Dated journal, one or a few lines per step. |
| `ha-addon/gnss_base/` | Home Assistant app that runs the base receiver: logging, commands, NTRIP caster, live position. |
| `ha-addon/ntrip_base/` | Earlier relay draft. Retired, kept for the record. |
| `analysis/` | Siting and survey-quality scripts. See `analysis/README.md`. |
| `logger/` | Android app that logs the rover over USB with its own NTRIP client and phone IMU. |
| `config.example.toml` | Template for local paths and settings. |
| `private/`, `data/`, `config.toml` | Untracked: data, notes, credentials, local settings. |

There is no `figures/` or `scripts/` folder yet. Figures from the October analysis exist
but have not been redrawn in local, relative coordinates, which is the condition for
publishing them. Processing scripts beyond `analysis/` are still private notes.

## Using the scripts

The scripts are written for a reader with an agent: clear names, a docstring saying what
each expects and produces, no hidden assumptions, and no site-specific values in the
code. Copy `config.example.toml` to `config.toml` (or, for `analysis/`,
`site_config.example.json` to `site_config.json`) and point it at your own data, or pass
paths on the command line; arguments take priority. Polished interfaces for other
people's data are not the goal, so expect to read the code.
