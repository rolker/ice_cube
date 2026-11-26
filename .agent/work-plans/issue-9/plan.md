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
2. **gpkg** — `import_sessions.py`: add the new columns to `SCHEMA["captures"]` (after `label`;
   `ensure` adds columns to existing gpkgs by ALTER) and to the `caps` dict in `import_session`.
   Not in `MEAS`, so `refs` is unchanged.
3. **Rule helper** — in `soundings.py` add `capture_sigma_inputs(sg, method, sd_u, sd_e, sd_n,
   tilt_sd_u, tilt_sd_e, tilt_sd_n)` returning `(sd_u, sd_h, tilt_deg)`: tilt rule when
   method == "tilt" and all three tilt sds are present; otherwise the single-capture rule
   (bubble tilt). `sigma_terms` is unchanged, so the Java mirror needs no change.
4. **Capture loop** — `logger_source`: extend the SELECT to read the new columns, tolerant of an
   old gpkg without them (look up `PRAGMA table_info(captures)` and select NULL for absent
   ones). Use the helper in the `sigma_terms` call and set `r["tilt_deg"]` to the helper's tilt.
   A tilt capture with missing sds falls back to the single rule (no flag change).
5. **Vectors** — `icecube/logger_vectors.logger_sigma` calls the same helper so there is one
   implementation; add a vector chain entry `tilt_missing_sd` (method tilt, tilt_sd_* None)
   to the sigma chain and regenerate the vectors file (`python3 -m icecube.logger_vectors --out ...`).
   Check the Java mirror's test still passes on the regenerated vectors, and tell the Java side
   if its fallback differs (open question below).
6. **Tests** — `analysis/qgis/tests/test_soundings.py`: unit test for the helper (tilt, tilt with
   missing sd, single, NULL method); an end-to-end `build` case with a tilt capture row checking
   sigma_pole == tip_sink, sigma_rtk uses tilt_sd_u and tilt_deg 0 in the row; an old-schema gpkg
   (no tilt columns) still builds. `test_import.py` (+ `synth.py` 0.6 points.csv rows): the
   gpkg `captures` row carries `method`, `fix_suspect`, `tilt_*`; a 0.5 file yields NULLs.
   `icecube/tests/test_logger_vectors.py`: the new missing-sd case.
7. **Docs** — fix `logger_vectors.py` docstring (lines 13-14), `logger/README.md` (lines 353-357
   importer note; 519-520 caveat), `soundings.py` module docstring (pole/tilt lines ~35-37) and
   `analysis/README.md` soundings row, plus `docs/log.md` entry per repo convention.

## Files to Change

| File | Change |
|------|--------|
| `analysis/qgis/logger_session.py` | `read_points` returns method, fix_suspect, fix_age/jump, tilt_* |
| `analysis/qgis/import_sessions.py` | captures schema + row dict carry the new columns |
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
| sigma selection | vectors file, Java mirror tests | Yes (Java untouched; sigma_terms unchanged) |
| Existing gpkgs lack columns | soundings SELECT tolerates it; re-import fills them | Yes |
| Issue 26 hand-made tilt sets | builds on the reader | No, out of scope |

## Open Questions

- Fallback when method is tilt but a tilt sd is missing: single-capture rule (planned). Does the
  Java mirror do the same? Confirm when the missing-sd vector is added; if not, align Java in a follow-up.
- Existing gpkgs need a re-import to populate tilt columns until then tilt captures keep the
  single rule; acceptable?

## Estimated Scope

Single PR.
