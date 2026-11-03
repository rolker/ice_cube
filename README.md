During the winter of 2025-2026, I had chats with Gemini and probably other agents regarding how to build and maintain a backyard ice rink.

I finally decided to assemble that knowlege and resulting scripts and such into a repo so I could maintain some sense of continuity as a go way overboard with this project.

## Layout

| Path | What goes there |
| --- | --- |
| `docs/` | Knowledge and how-tos: this is the place to look first |
| `docs/base-station.md` | RTK base station setup |
| `ha-addon/gnss_base/` | Home Assistant app that logs the base receiver and shows its status |
| `ha-addon/ntrip_base/` | Earlier draft of a corrections relay, to be retired |
| `scripts/` | Processing code |
| `figures/` | Published diagrams and maps, in local coordinates only |
| `config.example.toml` | Template for local paths and settings |
| `data/`, `private/`, `config.toml` | Untracked local data, notes and settings |

Scripts and de-georeferenced figures are published here. The data itself is kept private.

## Using the scripts

Copy `config.example.toml` to `config.toml` and point it at your own data, or pass paths on the command line. Command-line arguments take priority over the config file.

