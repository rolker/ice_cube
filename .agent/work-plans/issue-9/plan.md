# Plan: soundings.py: sigma rule for tilt captures

## Issue

https://github.com/rolker/ice_cube/issues/9

## Context

Logger 0.6 tilt captures carry `method=tilt` and `tilt_sd_e/n/u_m`, but `logger_session.read_points`
keeps only the first 17 columns, so `method`, `fix_suspect` and `tilt_*` never reach the gpkg
`captures` table, and `soundings.logger_source` gives tilt captures the single-capture sigma
(bubble tilt pole term). The rule (agreed in #6, mirrored in Java and in `icecube/logger_vectors.py`):
sd_u = `tilt_sd_u_m`, sd_h = hypot(`tilt_sd_e_m`, `tilt_sd_n_m`)/sqrt(2), tilt 0, tip sink and
ground kept.

## Approach

1. **Reader (general)** — `logger_session.read_points`: also return `fix_age_s`, `fix_jump_m`,
   `fix_suspect`, `method`, `tilt_n`, `tilt_rms_m`, `tilt_cover_deg`, `tilt_sd_e_m`, `tilt_sd_n_m`,
   `tilt_sd_u_m` via `r.get(...)` (None for pre-0.6 files and empty cells; `method` None -> not
   tilt). Keep keys as raw column names so issue 26 can reuse them. `read_refs_csv` keeps its fixed
   17-column list (its rows are refs only); no change beyond confirming it still works.
2. **gpkg** — `import_sessions.py`: bump `SCHEMA_VERSION` 2 -> 3; read the stored
   `schema_version` meta before the session loop (line ~524) and, when it is older than
   `SCHEMA_VERSION`, treat it as `--force` for sessions (skip test at line ~536), so a plain
   re-run refills the new columns on an old gpkg. Add the new columns to `SCHEMA["captures"]` (after `label`;
   `ensure` adds columns to existing gpkgs by ALTER) and to the `caps` dict in `import_session`.
   Not in `MEAS`, so `refs` is unchanged.
3. **Rule helper** — in `soundings.py` add `capture_sigma_inputs(sg, method, sd_u, sd_e, sd_n,
   tilt_sd_u, tilt_sd_e, tilt_sd_n)` returning `(sd_u, sd_h, tilt_deg)`: tilt rule when
   method == "tilt" and all three tilt sds are present; otherwise the single-capture rule
   (bubble tilt). `sigma_terms` is unchanged (Java gets the same fallback, step 5).
4. **Capture loop** — `logger_source`: extend the SELECT to read the new columns, tolerant of an
   old gpkg without them (look up `PRAGMA table_info(captures)` and select NULL for absent
   ones). Use the helper in the `sigma_terms` call and set `r["tilt_deg"]` to the helper's tilt.
   A tilt capture with missing sds falls back to the single rule (no flag change). GST stays a
   floor: `sigma_terms` is unchanged, so a tilt capture's sigma is the larger of the GST term and
   the tilt sd. Log one warning (naming a re-import with `--force`) when the tilt columns are
   absent from the gpkg, or all NULL for captures of sessions whose `sessions.app` is 0.6 or later.
5. **Java mirror** — `logger/app/src/main/java/rink/icecube/logger/core/SoundingSigma.java`:
   in `of`, use the tilt rule only when `TILT.equals(r.method)` and tiltSdE/N/U are all non-NaN,
   else the single-capture rule; rewrite the class comment (drop "soundings.py does not have that
   rule yet (issue #9)", state the fallback and that GST still floors). Regenerate
   `logger/app/src/test/resources/coverage_vectors.json` and run
   `cd logger && ./gradlew testDebugUnitTest`.
6. **Vectors** — `icecube/logger_vectors.logger_sigma` calls the same helper so there is one
   implementation; add a vector chain entry `tilt_missing_sd` (method tilt, tilt_sd_* None)
   to `SIGMA_CASES` (the list the Java test reads, not the coverage chain) and regenerate the vectors
   file (`python3 -m icecube.logger_vectors --out ...`).
7. **Tests** — `analysis/qgis/tests/test_soundings.py`: unit test for the helper (tilt, tilt with
   missing sd, single, NULL method); an end-to-end `build` case with a tilt capture row checking
   sigma_pole == tip_sink, sigma_rtk uses tilt_sd_u and tilt_deg 0 in the row (rows without GST data, plus a case asserting the larger of GST term and tilt sd); an old-schema gpkg
   (no tilt columns) still builds and logs the warning. `test_import.py` (+ `synth.py` 0.6 points.csv rows): the
   gpkg `captures` row carries `method`, `fix_suspect`, `tilt_*`; a 0.5 file yields NULLs; a gpkg with stored schema_version 2 and unchanged sources is re-read without `--force` and its tilt columns filled (next to `test_schema1_columns_migrated`).
   `icecube/tests/test_logger_vectors.py`: the new missing-sd case.
8. **Docs** — fix `logger_vectors.py` docstring (lines 13-14), `logger/README.md` (lines 353-357
   importer note; 519-520 caveat), `soundings.py` module docstring (pole/tilt lines ~35-37) and
   `analysis/README.md` soundings row, plus `docs/log.md` entry per repo convention.

## Files to Change

| File | Change |
|------|--------|
| `analysis/qgis/logger_session.py` | `read_points` returns method, fix_suspect, fix_age/jump, tilt_* |
| `analysis/qgis/import_sessions.py` | captures schema + row dict carry the new columns; `SCHEMA_VERSION` 3 forces a session re-read |
| `logger/app/src/main/java/rink/icecube/logger/core/SoundingSigma.java` | fall back to single rule when any tilt sd is NaN; class comment |
| `logger/app/src/test/resources/coverage_vectors.json` | regenerated |
| `analysis/qgis/soundings.py` | helper, tolerant SELECT, capture loop, docstring |
| `icecube/logger_vectors.py` | use helper, missing-sd vector, docstring; regenerate vectors file |
| `analysis/qgis/tests/{test_soundings,test_import,synth}.py`, `icecube/tests/test_logger_vectors.py` | new cases |
| `logger/README.md`, `analysis/README.md`, `docs/log.md` | drop "rule absent" statements |

## Prior Art

The rule and fallback behaviour already exist in `logger_vectors.logger_sigma` and the Java
`SoundingSigma`; this lifts the selection into one helper rather than a third copy. Header
tolerance follows the existing `r.get` style of `read_points`.

## Serves

Ice growth reported from logger surveys (`docs/roadmap.md` Direction); project has no design document.

## Design Picture

No change to the design picture (the project has no design document).

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo | Synthetic values only, no coordinates |
| Config, not hardcode | Reuses `[soundings.sigma]` tip_sink/ground; no new constants |
| Post-processing and live view agree | Same rule, shared helper, same vectors |
| Missing data handled | NULL/absent columns fall back to the single rule |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none | No | Project has no ADRs |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| `read_points` keys | `import_sessions` caps dict, schema | Yes |
| sigma selection | vectors file, `SoundingSigma.java` + its comment, Java tests | Yes (Java fallback changed; `sigma_terms` unchanged) |
| Existing gpkgs lack columns | SELECT tolerates it; `SCHEMA_VERSION` bump makes a plain import re-read sessions and fill them; `logger_source` warns once if still absent/empty | Yes |
| Issue 26 hand-made tilt sets | builds on the reader | No, out of scope |

## Open Questions

- None. Decided with the host: GST stays a floor for tilt captures; Java aligned in this PR.

## Estimated Scope

Single PR.
