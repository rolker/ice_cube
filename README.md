# ice_cube

I built a backyard ice rink in Epping, NH with no prior experience or plan, and started
using LLMs to help keep it up: photos and a forecast in, a plan for the next 48 hours out.
"I used the ice rink as an excuse to buy an RTK GPS." This repo is the tools that followed
(a survey workflow, a home base station, siting scripts) and the story of how a rink
turned into a geodesy project.

## Goals

Learning how to build and maintain an ice rink, while documenting the process and
building tools to help me.

1. Build an ice skating rink in my backyard.
2. A way to keep all the data I use to track and care for the rink, from the first
   planning in the fall through the end of the season: the base map, surveys, photos,
   notes, weather, and my conversations with AI.
3. An easy way to collect data from my phone, with or without the RTK GPS connected:
   photos and notes work with the phone's own GPS, and only surveys need the RTK.
4. AI agents can use the data being collected, as well as weather forecasts and such, to
   help me decide how to care for the rink.
5. A public rink status page that tracks the rink's progress, a bit like a blog, without
   too much work for me to edit and keep up. Some entries carry my notes and flavor, and
   together they make this season's story.
6. Discussions of my findings and techniques, and last season's story, edited and
   posted, for anyone interested in the nerdy bits.

**Principle:** The tools (the phone app, the base station, the GIS tools) are
general-purpose survey and GIS tools where that is cheap, with special features for the
rink.

**Principle:** Nothing public (the repo or the status page) gives away where the rink is
or who lives there. Town-level location is fine.

Why each goal and principle is what it is: [`docs/decisions.md`](docs/decisions.md).

## Two halves

**Tools.** Code that runs the base station, logs the rover and sizes the rink. Published
with the data left out: the georeferenced survey data, logs and coordinates stay private,
and the scripts take everything site-specific from local config.

The survey tools (the logger, the base station and the QGIS tools in `analysis/qgis/`)
are general-purpose: they work for any backyard survey or GIS question, not only the
rink. The rink comes first, though. They are generalised where that is cheap, and the
rink-specific parts live in `icecube/` and the siting scripts.

**Story.** The narrative, in my voice, in `docs/story/`. Right now the chapters are
outlines with the facts and prompts; the writing comes later. The journal in
`docs/log.md` has the dates and numbers.

## Layout

| Path | What is there |
| --- | --- |
| `docs/story/` | The chapters, in order. Outlines only so far. |
| `docs/howto/` | Reference: the base station as it stands, the RTK mountpoint test, the Ice-Cube estimator. |
| `docs/log.md` | Dated journal, one or a few lines per step. |
| `ha-addon/gnss_base/` | Home Assistant app that runs the base receiver: logging, commands, NTRIP caster, live position. |
| `ha-addon/ntrip_base/` | Earlier relay draft. Retired, kept for the record. |
| `analysis/` | Siting and survey-quality scripts. See `analysis/README.md`. |
| `icecube/` | The surface and ice-growth estimator (CUBE-derived node estimator with a crossover adjustment). See `docs/howto/ice-cube.md`. |
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
