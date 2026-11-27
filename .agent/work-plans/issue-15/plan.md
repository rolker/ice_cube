# Plan: Logger: retune the false-fix guard (start-of-session false alarms, a missed false fix)

## Issue

https://github.com/rolker/ice_cube/issues/15

## Context

Scope is the newest Checkpoint (owner, 2026-11-27): a narrow first PR; #39 (height test), #40 (extra cues), #41 (retune on past sessions) and #23 (validated-by-known-point mark) hold the rest.
Owner's answers: a young fix gets an amber warning and the capture goes ahead with the fix age recorded (no refusal); settle time 120 s, kept as a setting; narrow first PR.

Today (`core/FixGuard.java`, `Survey.java`): a stretch of quality-4 epochs starts after any non-fixed epoch or a >0.5 m / >2 s break. Its jump is the 3D distance from the previous epoch with a position (any quality, up to 2 s old, else unknown). `suspect` = jump > 1.0 m. `settling()` (setting `fix_settle_s`, 0-60, default 10) makes MARK, ref counting and tilt leans refuse. Rows carry `fix_age_s`, `fix_jump_m`, `fix_suspect` (`PointRow`, `TiltSet`).
Field misses: (A) 11-21, a fix after a float spell jumped only 0.48 m so it was not suspect but was wrong; (B) 11-26, a re-fix was still +6..+9 cm at 95-113 s; (C) at session start the jump is measured from a single/DGPS position (2.33 m) and a good stretch is flagged.

## Approach

1. **FixGuard float spell and float-only jump** — track the length of the current run of float (quality 5) epochs. A stretch's jump is measured only from the last float epoch (<= 2 s older); after single/DGPS/no position/first epoch it is unknown (NaN, never 0, never suspect on its own). A stretch is suspect if jump > jump threshold OR it starts after a float spell of at least `FLOAT_SPELL_S` (new constant, default 20 s, to confirm). Absence of data does not make a stretch clean and does not make it suspect: the new `fix_start` value says which case it was. A stretch inside a single-only start stays unknown/unsuspect (case C).
2. **Stretch carries its reason** — `Stretch` gains `prior` (`float`, `single`, `none`, `fixed-jump`, `gap`), `floatSpellS`, and `reason` (`jump`, `float_spell`, or both). `eventText()` extends the `events.csv` line (`... after float 45 s suspect: float_spell`).
3. **Settling becomes a young-fix warning** — `settling` is renamed `young` in the guard API; `Survey` stops refusing MARK, ref counting and tilt leans while young. A young capture gets the amber notice `fix young: N s`, the existing warn cue (a new distinct cue only if the owner wants one), and the row records `fix_age_s` (already) plus the new `fix_young` column. A ref keeps counting epochs and is marked young from its last-epoch age; the fix chip still shows `FIX young Ns` in amber.
4. **Settings** — `Limit.SETTLE_S` becomes range 0-300, default 120 (label "Fix young limit"; pref key `fix_settle_s` unchanged so saved values still apply, but an existing stored 10 stays 10, noted in the README). New `Limit.FLOAT_SPELL_S` (0-300, default 20, 0 turns the rule off). `SettingsActivity` help text updated.
5. **Columns, appended after `void_reason`** — `fix_start` (prior state), `float_spell_s`, `suspect_reason`, `fix_young` (1/0). `PointRow.HEADER`/`COLUMNS` (29 -> 33), `of`/`parse` read them only when the row is long enough, so older rows parse with `fix_start` empty (unknown), `fix_young` unknown. `TiltSet` rows: youngest lean wins for `fix_age_s`, any-young for `fix_young`, any-suspect combines reasons. `PointRowTest` keeps the 17/27/29 column cases and adds 33.
6. **Version** — `build.gradle` 0.10.0 -> 0.11.0, versionCode 14 -> 15; README title and the "e.g. Ice Cube Logger" example; `docs/log.md` entry.
7. **Tests** — see below.
8. **Processing side** — see below.
9. **README** — rewrite the False-fix guard section (float-only jump, float spell, young warning, why settle is a warning), the Settings list (ranges, defaults), the points.csv table and example header/rows, the "Re-shoot such points" advice, and the closing paragraph about thresholds (it says longer settle cannot catch minute-long false fixes: still true; the 120 s covers case B only).

## Tests (JVM, synthetic epoch sequences, relative metres only)

New cases in `FixGuardTest` and `PointRowTest`/`SurveyTest` style (existing seam: `FixGuardTest` feeds `Epoch`s):
- **Case A (11-21)**: 60 s of float, fix at 0.48 m jump: suspect via `float_spell`, reason set, jump recorded 0.48. Control: 5 s float then fix at 0.48 m: not suspect. Control: float 60 s, fix with 1.5 m: suspect with both reasons.
- **Case B (11-26)**: fix at t=0, captures at 95-113 s: young under the new 120 s default (warning, captured, row `fix_young` 1, `fix_age_s` ~100), not young at 125 s; capture is never refused. A ref spanning the boundary still counts its epochs.
- **Case C (start of session)**: first epoch single, then a 2.33 m step to fixed: jump unknown (NaN), not suspect, `fix_start` = `single`. Same for first epoch fixed (`none`) and a gap > 2 s (`gap`). Float then fix at 2.33 m after a short float: jump measured, suspect by jump.
- Existing tests that assume jump-from-any-position or refusal (`floatToFixStartsAStretchWithItsJump`, `settlingBlocksForTheSettleTime`, `refOccupationDoesNotCountSettlingEpochs`) are changed to the new rules, each with a comment saying which rule moved.
- `TiltSetTest`: a young lean and a suspect-by-float-spell lean combine into the tilt row.
- `SoundingSigmaTest`: constants against the regenerated vectors (below).

## Processing side (how the settle change moves coverage red nodes)

- `SoundingSigma.SETTLE_S` (90) and `soundings.py DEFAULTS["settle_s"]` (90) mirror each other and are separate from the logger setting. Keeping them at 90 would leave the overlay trusting a 95-113 s fix that the field says is still 6-9 cm off. The plan moves both to 120 and regenerates `coverage_vectors.json` with `python3 -m icecube.logger_vectors`; `SoundingSigmaTest` checks it.
- Effect on the overlay: a capture with `fix_age_s` < 120 gets `settle = 0.08 * (1 - age/120)^2` added to sigma_z (about 0.5 cm more at 90 s, 2 cm at 60 s, 8 cm at 0). #29's replays showed 37 of 58 red nodes from young fixes, so more nodes cross the 3 cm sigma target; on ice work the offline `soundings.py` also drops (flag `settle`, excluded) whole passes whose first fix age is < settle_s, so passes of young fixes drop out of the surface. Expect a few more red nodes after a re-fix, not a change in pass-1/plane logic. The plan reports relative counts only in the PR text, from one replayed session; the wider retune is #41.
- Float-spell and the dropped jump source also change `fix_suspect`: `Coverage` (line ~585) excludes suspect captures from feeding nodes, so a capture after a float spell now leaves its nodes red until re-shot. That is the intended behaviour (prefer re-shoot to a decimetre offset); the amber live notice tells the surveyor on the spot.
- `analysis/qgis/soundings.py`: `fix_stretches` recomputes stretches from raw epochs (flags `suspect_jump`, `settle`, line 743/890/1003). Port the float-only jump and float-spell rule to it (needs the epoch quality; check `Epochs` for a float marker and add one if missing) and add flag `float_spell` to `EXCLUDING` and the sigma-clearing lists (`ppk` handling, lines ~547, 1221) beside `suspect_jump`. `verify_suspect` stays: it can still clear a float-spell stretch that independent data agree with. A parity test with a synthetic epoch sequence pins python to the Java cases above.
- `analysis/qgis/logger_session.py` (`POINTS_06_*`, read_points) and `import_sessions.py` (`LOGGER_06`, `SCHEMA_VERSION` 4 -> 5): add the four columns as NULL for older sessions; an import test with an old and a new points.csv. `icecube/io.py`/`logger_vectors.py` read only `fix_age_s`; `icecube.example.toml` `settle_s` default for passes is looked at and stays a user setting (note in `docs/howto/ice-cube.md`).
- `docs/howto/ice-cube.md` and `docs/log.md`: new column names, 120 s, the warning instead of a refusal.

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/FixGuard.java` | float-only jump, float spell, prior/reason on Stretch, young naming |
| `logger/.../Survey.java` | no refusal while young; amber young notice; new columns; ref/tilt behaviour |
| `logger/.../core/{PointRow,TiltSet,Limit,SoundingSigma}.java`, `Settings.java`, `SettingsActivity.java`, `MainActivity.java` (chip text), `MapView.java` (tilt banner text) | columns, limits, texts |
| `logger/app/src/test/.../{FixGuardTest,PointRowTest,TiltSetTest,SoundingSigmaTest}.java`, `coverage_vectors.json` | the three field cases and parsing of old rows; regenerated vectors |
| `logger/app/build.gradle`, `logger/README.md`, `docs/log.md`, `docs/howto/ice-cube.md` | version 0.11.0, docs |
| `analysis/qgis/{soundings,logger_session,import_sessions}.py` and their tests | port rule, read new columns, old sessions NULL |

## Prior Art

The existing `fix_stretches` in `soundings.py` (offline mirror of FixGuard) and the in-repo #29 pattern of mirrored constants with regenerated vectors are reused. The jump/settle ideas come from the issue's field comments; no outside practice looked up. Nothing outside the guard is built new.

## Serves

Survey-readiness for cm-level ice accumulation between sessions (false fixes are the largest error term); project has no goals or design document: none.

## Design Picture

No change to the design picture (no design document). Documented behaviour and the points.csv schema change in `logger/README.md`.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo, no coordinates | Tests use synthetic local-frame sequences; README and PR numbers are relative (m, s, cm). Replay of private sessions stays offline, counts only. |
| Older sessions stay readable | New columns appended after `void_reason`; parsers take them only if present; blank means unknown, never 0 or suspect. |
| Unknown is not suspect and not clean | Jump unknown without a float epoch; `fix_start` says why; float spell is the rule that errs toward suspect. |
| Scripts take settings from config | New python settings, if any, go in `config.example.toml`/`icecube.example.toml`. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project has no `docs/decisions/`) | No | n/a |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| points.csv columns | PointRow, TiltSet, ShareZip check, `logger_session.py`, `import_sessions.py`, README | Yes |
| settle 90 -> 120 | SoundingSigma, soundings.py DEFAULTS, vectors, Coverage red nodes | Yes |
| fix_suspect meaning | Coverage feed, soundings flags, `verify_suspect` | Yes |
| height test, extra cues, retune, validated mark | #39, #40, #41, #23 | No, follow-ups |

## Open Questions

- Float-spell length that counts as "a while": 20 s default proposed. Evidence is one case; #41 retunes. OK?
- Does a float spell count only quality 5, or also a recent single/DGPS stretch? Proposed: float only (single start is case C).
- Young refs and tilt leans: allowed with a warning and recorded (proposed), or should a ref still pause until the fix is 120 s old, since a ref feeds the pole check?
- Distinct cue for "young" (vs reusing the suspect warble)? Proposed: reuse for this PR.
- Existing stored `fix_settle_s` values (10) stay; acceptable, or reset to 120 on upgrade?
- Move `settle_s` in soundings.py and SoundingSigma to 120 (proposed), changing offline surfaces of past sessions on rerun.

## Estimated Scope

Single PR, about 12 files plus tests; the python port is the part to split off if it runs long.
