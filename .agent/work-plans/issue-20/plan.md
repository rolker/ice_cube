# Plan: icecube: read Ice Cube Logger sessions

## Issue

https://github.com/rolker/ice_cube/issues/20

## Context

`icecube` reads SW Maps, ENU CSV and the `soundings` table of the season gpkg (`icecube/io.py` `read_soundings`, `READERS`, `read_survey`). A new logger session can only get in by rewriting the shared season gpkg with `analysis/qgis/soundings.py`. `analysis/qgis/logger_session.py` (`find_sessions`, `load_session`) already parses sessions, `import_sessions.import_session` writes the `captures` table and `soundings.logger_source` turns captures plus raw epochs into rows with per-capture sigma, fix stretches and pass ids. Host decisions (Checkpoint): a thin `icecube.io` adapter over those (no third parser); robust nodes are done (#24/#29) and not re-scoped; the settle re-fit is planned here and flagged if large; implementation starts after #15 merges.
Built on #15 (its plan, not yet merged): settle default 120 s (`SoundingSigma.SETTLE_S`, `soundings.py DEFAULTS["settle_s"]`, `sigma_terms` default), new `points.csv` columns `fix_start`, `float_spell_s`, `suspect_reason`, `fix_young` (NULL for older sessions via `logger_session`/`import_sessions`, `SCHEMA_VERSION` 5), flag `float_spell` in `soundings.fix_stretches`/`EXCLUDING`, vectors regenerated. This plan assumes those names; re-check them against the merged code before step 1.

## Approach

1. **Rebase check** — after #15 merges, rebase on `main`; re-read `soundings.py` (`fix_stretches`, `sigma_terms`, `EXCLUDING`), `logger_session.read_points`, `import_sessions` schema and `logger_vectors.py`; fix any name in this plan that moved.
2. **Rows to Points, one function** — in `icecube/io.py` split the body of `read_soundings` after the row filter into `_points_from_rows(rows, survey, frame, date)` (pure refactor, existing tests unchanged), so the new reader and `read_soundings` share it.
3. **Reader `logger_session`** — `read_logger_session(path, survey, frame, date, site, sessions=None, classes=("ground","anchor"), include_voided=False, soundings_cfg=None)`. `path` is a session folder, a zip, or a directory of them (`logger_session.find_sessions`). It imports the session(s) into a throwaway gpkg in a temp dir (`import_sessions.ensure_schema` + `import_session`, with the site's local frame so `x_m/y_m/z_m` match `[site]`), runs `soundings.logger_source` against it, takes the rows it returns (unwritten to the season gpkg; the real gpkg is never opened for write) and feeds `_points_from_rows`. The analysis dir is put on `sys.path` the way `icecube/logger_vectors.py` already does. No new CSV parsing, no new sigma, stretch or pass code.
   - sigma: from `soundings.sigma_terms` as-is (ref/anchor classes, settle term, fix stretch), so a session checked alone gets what it would get in the season table.
   - passes: rows carry `pass_id` from `soundings.pass_name` (fix stretches, split at `max_pass_s`); `read_survey` sets `pass_rule.from_file` for this reader as it does for the soundings reader, so stretches are the pass boundaries.
   - voided captures: left out (`use = 0`, flag `void`) unless `include_voided`; the `use` filter of `read_soundings` already drops them.
   - #15 columns: `fix_age_s`, `fix_jump_m`, `fix_suspect`, `fix_young`, `float_spell_s` flow through the `soundings.py` flags (`suspect_jump`, `float_spell`, `settle`, `settle_inflated`), so excluded rows (`use = 0`) are dropped here and not re-judged. Older sessions without the new columns behave as under #15 (unknown, not suspect).
4. **Wire it** — `READERS["logger_session"]`, `read_survey` branch, config keys `sessions` (list of folder names), `include_voided`, `table`-free; `icecube.example.toml` gets a commented `[[surveys]]` block (`reader = "logger_session"`, `path`, `sessions`, `wander_prior`); `docs/howto/ice-cube.md` documents the reader and the "check a new session before it joins the season" use. Site-specific paths stay in the user's config.
5. **Tests** (synthetic sessions only, relative metres): extend `analysis/qgis/tests/synth.py` use or add a small session builder in `icecube/tests/` (folder with `events.csv`, `points.csv`, 1 Hz GGA/gst stand-in as `analysis/qgis/tests` already fakes). Cases: a ground session reads into Points with expected sigma_z equal to `soundings.sigma_terms` for the same inputs; two fix stretches give two pass ids; a voided capture is excluded and included with `include_voided`; a young-fix capture (age < 120 s) is inflated; a post-float-spell capture (#15) is excluded; an old session without #15 columns reads; a zip equals the folder; an end-to-end `python -m icecube run` on a synthetic config with one `logger_session` survey and one soundings survey.
6. **Settle re-fit (separable second commit)** — fit the settle term on the 11-11, 11-15 and 11-21 sessions offline (private data stays private; only relative numbers in the repo). Issue evidence: robust sd 7.1 cm inside 90 s of a fix against 3.1 cm after 150 s, flatter than 8 cm x (1 - t/90)^2. After #15 the window is 120 s, so the refit is on top of that: a shape that is flat-ish out to a hold time and then tapers (`settle_extra` x (1 - t/settle_s)^p, or flat until `settle_hold_s`), parameters from a fit of residual-vs-fix-age on robust sd. Changes: `soundings.sigma_terms` and `DEFAULTS["sigma"]` docstring; `SoundingSigma.java` port and `SoundingSigmaTest`; `icecube/logger_vectors.py` cases and regenerated `coverage_vectors.json`; text about the term in `docs/howto/ice-cube.md` (the 8 cm x (1 - t/90)^2 table row), `logger/README.md` and `docs/log.md`. Size: small if the form stays a one- or two-parameter change to one function mirrored in one Java method; the fit itself needs the private sessions, which an agent run can do offline from the owner's data folder.
   - If the fit wants a new shape that changes more than `sigma_terms` and its Java mirror, or needs more data than the three sessions, stop and ask the owner to split it into its own issue (a new issue is his).
7. **Docs and log** — `docs/howto/ice-cube.md`, `icecube.example.toml`, `docs/log.md` entry; note robust nodes are delivered by #24/#29.

## Files to Change

| File | Change |
|------|--------|
| `icecube/io.py` | `_points_from_rows` split; `read_logger_session`; `READERS` and `read_survey` branch |
| `icecube/tests/test_logger_session.py` (new) | synthetic-session reader tests, end-to-end run |
| `icecube.example.toml`, `docs/howto/ice-cube.md`, `docs/log.md` | reader option, how-to, journal |
| `analysis/qgis/soundings.py` (step 6) | `sigma_terms` settle shape and constants |
| `logger/app/src/main/java/rink/icecube/logger/core/SoundingSigma.java`, `.../SoundingSigmaTest.java` (step 6) | Java port and test |
| `icecube/logger_vectors.py`, `coverage_vectors.json` (step 6) | cases and regenerated vectors |
| `logger/README.md` (step 6) | settle term text |

## Prior Art

Reused whole: `logger_session.load_session`/`find_sessions`, `import_sessions.import_session`, `soundings.logger_source`, `sigma_terms`, `fix_stretches`, `pass_name`, and `icecube.io.read_soundings`. Rejected: a standalone parser in `icecube` (a third copy of the sigma/stretch rules, the 2026-11-21 failure the issue describes), and requiring the season gpkg (the issue's complaint). The scratch-gpkg route gives the same rows as the season table at the cost of a temp sqlite file; a direct `logger_source` that takes parsed sessions without sqlite would be cleaner but is a larger edit to `soundings.py` and is left for later.

## Serves

Season goal in `docs/roadmap.md` (surface and ice-growth from logger sessions); the project keeps no design document: none.

## Design Picture

No change to the design picture (no design document). The how-to gains the new reader.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Reuse over duplication | Thin adapter over `logger_session`, `import_sessions`, `soundings`; no new parser or sigma code. |
| One rule, one place (sigma) | Reader gets sigma from `sigma_terms`; the refit changes it once and mirrors it in Java and vectors in the same PR. |
| Public repo hygiene | Synthetic sessions; refit figures relative only; data paths in the user's config. |
| Only what's needed | Robust nodes not re-scoped; refit is the only addition and is a separate commit. |
| Scripts take settings from config | New keys documented in `icecube.example.toml`. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project has no `docs/decisions/`) | No | n/a |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| settle term | `soundings.py`, Java port and tests, vectors, howto, README | Yes (step 6) |
| `read_soundings` split | `read_soundings` tests unchanged | Yes |
| new `[survey]` reader | `icecube.example.toml`, howto | Yes |
| #15 names | step 1 re-check after merge | Yes |

## Open Questions

- Is the settle re-fit small enough to keep? Planner view: yes if the form is a one- or two-parameter change inside `sigma_terms`; the owner decides on a split if the fit wants more (see step 6).
- The reader goes through a scratch gpkg (reuse now). Accept that, or should `soundings.logger_source` be refactored to take parsed sessions? Planner suggests accept now.
- Reader needs raw session epochs (GGA/gst) for fix stretches: it requires the session folder or zip, not only `points.csv`. Fall back to per-capture `fix_age_s` (no passes from stretches) when no raw epochs exist, or refuse? Planner suggests the fallback with a logged warning.

## Estimated Scope

Single PR, two commits (reader; settle re-fit), about 10 files plus tests. Starts after #15 merges.
