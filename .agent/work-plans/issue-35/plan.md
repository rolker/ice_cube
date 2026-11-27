# Plan: Logger: auto capture when the pole is held plumb and still

## Issue

https://github.com/rolker/ice_cube/issues/35

## Context

Scope: the issue, the owner's answers (2026-11-27: Surface only; 2 deg, 2 s, 2 cm as settings; calibration once per session, auto capture off until done, screen says why; off during a tilt set; one capture per stop, same tone as MARK), the calibration-routine comment (hold still, lean ~10 s, two slow twists), the Issue Review and the newest Checkpoint (host decisions; tilt capture does exist in `logger/`).

Existing pieces: `ImuLogger` records phone accel/gravity/gyro and keeps a ring of recent gravity vectors stamped on the GGA clock (`gravityMean(fromNs,toNs)`). `core/TiltSet` + `TiltSolver` solve the tip from leans (antenna positions + gravity); `TiltSolver.fitAxis(gravity, tiltDeg)` already fits the pole axis in phone axes from gravity vectors and per-lean tilts, and `angleDeg` compares vectors. `Survey` owns the epoch stream, MARK, the tilt state (`tilt != null`, `tiltRunning()`), `Notice` and `Feedback.Cue`. `EpochAverage` gives mean and sd of recent fixed epochs.

Depends on #15 (separate plan, read-only): it renames `FixGuard.settling` to `young` (limit 120 s) and adds the float-spell `suspect`. Auto capture arms only on an RTK-fixed epoch whose stretch #15 marks neither young nor suspect. MARK is unchanged (young still captures, amber). Implementation starts after #15 merges; names below use #15's (`young`, `suspect`); if they differ on merge, adjust.

## Approach

1. **Pure arming state machine** `core/AutoCapture.java` (no Android). Inputs per epoch: time, RTK-fixed flag, young, suspect, epoch ENU position, plumb angle (deg, NaN if no gravity or no calibration), plus context flags `calibrated`, `tiltRunning`, `refRunning`, `mode == SURFACE`, `enabled`. Output: `Decision {FIRE | WAIT}` and a `Why` enum for the screen when off: `OFF_SETTING`, `NOT_SURFACE`, `NO_CALIBRATION`, `TILT_SET`, `REF_RUNNING`, `NO_FIX`, `FIX_YOUNG`, `FIX_SUSPECT`, `NOT_PLUMB`, `MOVING`, `HOLDING (n s)`, `DONE_MOVE_AWAY`, `ARMED`. States: WAIT_STILL (condition set must hold continuously for hold time, a sliding window of epochs: plumb angle <= limit at every epoch and horizontal scatter sqrt(sdE^2+sdN^2) of the window < scatter limit, window spanning >= hold seconds) -> FIRE -> LATCHED. Re-arm from LATCHED when the pole moves away (position more than the scatter limit x some factor from the capture position, set to 3 x scatter = 6 cm default? see open question) or tilts past the plumb limit, and then the still condition holds again. A fix break, young/suspect or any unmet condition resets the window. Any gap in epochs > 2 s resets.
2. **Pole-axis calibration** `core/PoleCalibration.java` plus `core/PoleAxis.java` (value: unit axis in phone axes, spread/residual in deg, time). Phases as the owner described, tip on one point: (a) still ~3 s (mean gravity, extension point: gyro bias for #38), (b) lean round the tip ~10 s, as in a tilt set, (c) two slow twists about the pole axis (collected, but for #35 only checked as "happened"; extension point for the off-axis lever arm, #38). Reuse, do not duplicate, the tilt solver: samples are collected as 1 s leans (antenna position average + gravity mean, as `TiltSet` does), the tip and per-lean tilt come from `TiltSolver.solve(positions, poleHeight)`, then `TiltSolver.fitAxis(gravity, tilt)` gives the axis. Acceptance: >= 4 leans, coverage >= a minimum (`TiltSolver.coverage`), solve rms under the lean rms gate, fit residual (rms of angle(g_i,a) - tilt_i) under ~1 deg, otherwise refuse with the reason and keep auto capture off. Result is held in memory per session (`Session`), cleared on a new session, and written once to the event log (`events.csv`: `pole axis calibrated residual_deg=.. leans=..`, never the vector's location data; the axis is mounting geometry, not a coordinate, and can also go in session metadata). Not persisted across sessions (owner: once per session). `PoleCalibration` is a small state machine (IDLE, STILL, LEAN, TWIST, DONE/FAILED) with injected samples so it is JVM-testable. Outputs beyond the axis are a documented `extras` map left empty (hook for #38); nothing of #38 is built.
3. **Plumb angle live**: `ImuLogger` gets `latestGravity(windowNs)` (or reuses `gravityMean` over the last 0.5 s) for the arming check; plumb angle = `TiltSolver.angleDeg(gravityMean, axis)`. Sign convention is the same as `fitAxis`, pinned by a test.
4. **Survey wiring**: `Survey.onEpoch` feeds `AutoCapture` after the guard update (same lock). On FIRE it runs the same capture path as `mark(SurveyMode.SURFACE)` (extract the common capture into a private method so MARK and auto share the row, the cue and the young/suspect columns), with the row marked `method` auto (new `PointRow` method value, no new column; check `PointRow` parse of unknown methods) and event `auto capture n=..`. Plays the MARK capture tone (`Feedback.Cue`, the same cue; no new tone), plus a short distinct cue when auto capture becomes armed or turns off after being armed (new `Cue.AUTO_ARMED`, `Cue.AUTO_OFF`, both vibration + tone for gloves; kept low-rate to avoid chatter: only on a state change that stays 1 s). Auto capture is disabled while `tilt != null`, a ref occupation, and a calibration run. A capture of its own can be flagged (#27) as a normal row.
5. **Settings**: `Limit.AUTO_PLUMB_DEG` (0.5-10, default 2), `AUTO_HOLD_S` (0.5-30, default 2), `AUTO_SCATTER_CM` (0.2-20, default 2), and a boolean `auto_capture` (default on, since it also needs calibration to do anything); Settings screen entries and help text; `config.example.toml` untouched (app setting, not a script). `LimitTest` cases.
6. **UI**: a status line on the map screen showing `AUTO: <Why>` (armed, holding 1.2 s, off: no calibration, off: tilt set, off: fix young 40 s, ...), large and readable with gloves. Menu entry `Calibrate pole` (large button, and a volume-key chord is not added) opens a calibration overlay: current phase, countdown, and an audible/haptic tick per phase change and a success/failure cue. Layout sizing follows the existing tilt overlay (`MapView`); no new screen file unless MapView cannot host it (decide while coding).
7. **Version and docs**: `build.gradle` version bump (minor; #15 takes 0.11.0 so check main on branch, expect 0.12.0, versionCode +1), `logger/README.md` (auto capture section, how to calibrate, the arming rule incl. young/suspect, settings, cues, new `method` value), `docs/log.md` entry.

## Tests (JVM, `core/`, synthetic sequences, relative metres only)

- `AutoCaptureTest`: fires after the hold time with plumb + still + fixed; no fire when plumb limit exceeded at one epoch, scatter over limit, float/single fix, young, suspect (each reports its `Why`); no fire without calibration, during a tilt set, during a ref, in Object/Ref modes; exactly one capture per stop; no second capture while the pole stays; re-arm after moving away and after tilting past the limit then returning; epoch gap resets; settings changes apply.
- `PoleCalibrationTest`: synthetic leans on a sphere around a tip with a known mount offset (axis tilted 3 deg from a phone axis): recovered axis within 0.2 deg; too few leans, poor coverage, high residual are refused with reasons; twist phase recorded but not needed for the axis.
- Plumb-angle sign test (`angleDeg` with `fitAxis` output). `PointRowTest` for the `auto` method; `LimitTest` for the new limits. Android sensor and UI code is checked by hand on the phone (the repo has no instrumented tests); the PR lists what was checked.

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java` (new) | arming state machine and `Why` |
| `.../core/PoleCalibration.java`, `PoleAxis.java` (new) | calibration routine and result, #38 extension point |
| `.../ImuLogger.java` | short-window gravity accessor (for plumb angle, calibration still phase) |
| `.../Survey.java` | feed epochs to `AutoCapture`, shared capture path, calibration hosting, gates for tilt/ref |
| `.../Session.java`, `LoggerService.java` | per-session axis, plumb angle plumbing |
| `.../Feedback.java` | `AUTO_ARMED`, `AUTO_OFF` cues (and calibration progress cues) |
| `.../core/Limit.java`, `Settings.java`, `SettingsActivity.java` | three limits and the on/off setting |
| `.../MainActivity.java`, `MapView.java` | AUTO status line, Calibrate pole entry and overlay |
| `.../core/PointRow.java` | `auto` method value |
| `logger/app/src/test/.../core/{AutoCaptureTest,PoleCalibrationTest}.java` (new), `LimitTest`, `PointRowTest` | tests above |
| `logger/app/build.gradle`, `logger/README.md`, `docs/log.md` | version, docs |

## Prior Art

Reused: `TiltSolver.solve/fitAxis/angleDeg/coverage`, `TiltSet`'s lean = 1 s antenna average + gravity mean, `EpochAverage` scatter, `FixGuard` young/suspect (#15), `Feedback.Cue`, `Limit`/`Settings` pattern, `ImuLogger` gravity ring. No outside practice looked up (pole-tilt compensation in commercial GNSS rovers uses a similar magnetometer/IMU calibration; the owner's routine is followed instead). Rejected: raw phone gravity as "plumb" (mounting is not vertical, per the issue); a new calibration solver (fitAxis already does it); persisting the axis across sessions (owner: per session); building #38's lever arm and gyro bias now.

## Serves

None stated: the project keeps no goals or design document. Practically: gloved winter ice surveys and fewer accidental captures (#27).

## Design Picture

No change to the design picture (the project has no design document). Behaviour is documented in `logger/README.md`.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo | Tests are synthetic and relative; the owner's rough lever-arm numbers are not used or committed; the axis is mounting geometry, no coordinates. |
| Fail visibly | The `AUTO:` line names the unmet condition; refused calibrations say why. |
| No capture on a doubted fix | Arms only on fixed, not young, not suspect (#15); MARK unchanged. |
| Logic apart from Android | State machines in `core/`, JVM tested; Survey/UI wiring thin. |
| Do not pre-build #38 | Only an empty extension point for lever arm and gyro bias. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project has no `docs/decisions/`) | No | n/a |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `Survey` capture path | MARK, tilt, ref paths and #15's young/suspect handling | Yes (shared method; rebase on #15) |
| `PointRow` `method` values | `analysis/qgis` readers of `method` | Check on implementation; add to the PR if any filters on method |
| Settings | README settings list, `LimitTest` | Yes |
| #38 calibration outputs | lever arm, gyro bias | No, follow-up in #38 |

## Open Questions

- The "moved away" distance for re-arming (default proposal: 3 x the scatter limit, 6 cm, or any epoch leaving the plumb limit). Confirm or give a number.
- Is "auto capture" on by default (proposal: yes; it stays inert until calibrated)?
- Calibration acceptance gates (residual ~1 deg, >= 4 leans, coverage like the tilt gate): agree or retune after the first field run.
- Plumb-angle sign/axis convention must be confirmed on the phone with a real calibration (hand check before merge).
- Branch from main only after #15 merges; names (`young`, `suspect`) may shift.

## Estimated Scope

Single PR (about 10 files, two new core classes with tests). Larger than #15, so if the UI overlay grows, split the calibration overlay from the arming line.
