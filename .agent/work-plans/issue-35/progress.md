---
issue: 35
---

# Issue #35 — Logger: auto capture when the pole is held plumb and still

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #35

### Scope Assessment

**Well-scoped?** Mostly. The owner's answers settle modes, thresholds, per-session calibration and tilt-set interaction. The calibration routine is shared with #38, so it should be built as a standalone, reusable routine here, and the #38 outputs (lever arm, gyro biases) can be added later. It fits one PR if the calibration stores only the pole axis now.
**Right repo?** Yes. It is all in `logger/`.
**Serves**: none stated. The project keeps no goals or design document. Practically it serves gloved winter ice surveys and fewer accidental captures (#27).
**Design picture?** No change. There is no project design document.
**Dependencies**: #15 must merge first (young-fix warning under 120 s and the float-spell suspect rule; shared Survey/MainActivity code). Related: #27, #9 (tilt sets; no tilt code is in `logger/` yet, so "off during a tilt set" needs a hook that may not exist), #38 (shares the calibration routine).

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Public repo rules (CLAUDE.md) | OK | Thresholds and lever-arm defaults are not site data. Keep the owner's rough lever-arm numbers out of committed fixtures if they identify a specific rig. |
| Auto capture must not fire on a bad fix (#15) | Action needed | Define the arming rule against #15. Auto capture should arm only on a RTK-fixed, settled fix that #15 does not flag as young or suspect. A young-fix warning still captures on MARK with the amber cue, but a capture nobody pressed should not be taken on a fix the app itself doubts. |
| Fail visibly | OK | Owner decision: the screen states why auto capture is off (no calibration, tilt set, unsettled fix). Show the unmet condition. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| Workspace ADRs | No | A project-level change in `logger/`. |

### Consequences

- Update `logger/README.md` (auto capture, calibration, settings).
- Add the new settings with their defaults (2 deg, 2 s, 2 cm).
- Pure-logic unit tests for the arming and re-arming state machine (still detection, one capture per stop, re-arm on move or tilt).

### Recommendations

- Start implementation only after #15 merges; branch from main then.
- Auto capture waits for a settled, non-suspect fix: no auto capture while #15 flags the fix as young (under 120 s) or suspect. MARK is unchanged. This is a recommendation for the owner to confirm.
- Build the calibration as a reusable routine with the phases (still, lean about 10 s, two slow twists). Store only the pole axis for #35, and keep the lever-arm and bias outputs for #38 or leave a clear extension point. Do not pre-build #38 pieces.
- Keep the arming logic a pure, testable state machine separate from the sensor and UI code.
- Gloved use: large touch targets, and an audible or haptic cue for calibration progress and for auto-capture armed or off.
- Check how "tilt set in progress" is represented in the code. If #9 has not landed, gate on a flag that #9 will set.

### Actions
- [ ] Define the arming rule against #15. Auto capture should arm only on a RTK-fixed, settled fix that #15 does not flag as young or suspect. A young-fix warning still captures on MARK with the amber cue, but a capture nobody pressed should not be taken on a fix the app itself doubts.
- [ ] Start implementation only after #15 merges; branch from main then.
- [ ] Auto capture waits for a settled, non-suspect fix: no auto capture while #15 flags the fix as young (under 120 s) or suspect. MARK is unchanged. This is a recommendation for the owner to confirm.
- [ ] Build the calibration as a reusable routine with the phases (still, lean about 10 s, two slow twists). Store only the pole axis for #35, and keep the lever-arm and bias outputs for #38 or leave a clear extension point. Do not pre-build #38 pieces.
- [ ] Keep the arming logic a pure, testable state machine separate from the sensor and UI code.
- [ ] Gloved use: large touch targets, and an audible or haptic cue for calibration progress and for auto-capture armed or off.
- [ ] Check how "tilt set in progress" is represented in the code. If #9 has not landed, gate on a flag that #9 will set.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism of a behaviour the owner decided; owner asked to orchestrate in parallel while busy)
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): auto capture arms only on an RTK-fixed fix that #15 marks neither young (< 120 s) nor suspect; MARK still captures on a young fix with the amber cue; the screen and a cue say which condition is unmet. Calibration as a reusable routine (hold still, lean ~10 s, two slow twists) storing the pole axis now, with an extension point for #38's lever arm and gyro bias. Arming logic a pure, unit-tested state machine. Large touch targets and audible/haptic cues for gloved use. Correction to the review: tilt capture exists in logger/ (core/TiltSet.java, TiltSolver, Survey's tilt mode); auto capture is off while a tilt set is in progress. Implementation after #15 merges.

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:54 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-35/plan.md` at `c2ab992`

Pure-logic arming state machine (AutoCapture) and a per-session pole-axis calibration (PoleCalibration) that reuses TiltSolver.fitAxis; arms only on a fixed, not-young, not-suspect fix (#15), Surface only, off during tilt sets and refs, with an AUTO status line stating the unmet condition. Implementation after #15 merges.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #35 — Logger: auto capture when the pole is held plumb and still
**Plan**: `.agent/work-plans/issue-35/plan.md` at `c2ab992`
**Branch**: `feature/issue-35`

Repository: rolker/ice_cube. Reviewed against main, the owner's answers (2026-11-27), the calibration-routine comment, the Issue Review, the host Checkpoint, the host's answers to the planner's open questions (below), and the #15 plan (read-only, issue-15 worktree).

Host answers to the plan's open questions (owner may veto), now binding on implementation:
1. Re-arm after the antenna moves more than 3x the scatter limit (6 cm at defaults) horizontally from the last auto capture, OR the pole tilts past the plumb limit.
2. Auto capture on by default, inert until calibrated; the screen says "calibrate to enable".
3. Calibration gates as proposed (>= 4 leans, coverage like the tilt gate, fit residual about 1 deg), retuned after the first field run.
4. Plumb-angle sign/axis convention: a JVM test plus a phone check before merge.
5. Version after #15's: 0.12.0 if #15 lands as 0.11.0 (versionCode +1 from #15's).

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | One PR, ~10 files, two new pure `core/` classes. The plan's own split point (calibration overlay vs arming line) is sensible if the UI grows. |
| Issue alignment | Good | Surface only; 2 deg / 2 s / 2 cm as settings; calibration once per session, off until done with the reason shown; off during tilt sets; one capture per stop; MARK tone. Calibration follows the owner's still / lean / twist routine with a #38 extension point and nothing of #38 built. Issue Review actions all addressed (arm only on fixed, not young, not suspect; pure state machine; gloved cues; tilt hook exists as `Survey.tiltRunning()`). Serves: "None stated: the project keeps no goals or design document. Practically: gloved winter ice surveys and fewer accidental captures (#27)." Accepted (project keeps no goals). |
| File targeting | Needs work | Missing `core/Coverage.java` (see finding 1). Otherwise right: `Survey`, `ImuLogger`, `Feedback`, `Limit`/`Settings`/`SettingsActivity`, `MainActivity`/`MapView`, `PointRow`, tests, `build.gradle`, README, `docs/log.md`. |
| Consequences | Needs work | The new `method` value is consumed by the logger's own coverage filter, not only by Python readers (finding 1). Python readers checked: no filter breaks. |
| Principle alignment | Good | Logic in `core/` with JVM tests; fail visibly via the AUTO line; no pre-building of #38; public-repo rule respected (synthetic relative fixtures, owner's lever-arm numbers kept out). Design picture: none (no project design doc), stated. Prior art: reuses `TiltSolver.solve/fitAxis/angleDeg/coverage`, `TiltSet` lean pattern, `EpochAverage`, `FixGuard`. |
| ADR compliance | Good | No project ADRs; workspace ADRs not triggered. |
| ROS conventions | N/A | Android app, no ROS. |

### Findings

1. **[Consequences / File targeting] `Coverage.feeds` drops `method=auto` rows.** `logger/.../core/Coverage.java:583-587` feeds only `SINGLE` or `TILT` surface rows. An auto capture with a new `method` value would never feed the live coverage map, so auto-captured points would leave nodes red. Fix during implementation: add the new constant (e.g. `PointRow.AUTO = "auto"`) to `feeds`, add a `CoverageTest` case, and update the `PointRow` method comment (line 29) and the README `points.csv` `method` row (README line ~423). This is the only code consumer that filters on `method`.
2. **[Consequences] Python readers checked, no filter breaks.** `analysis/qgis/soundings.py` `capture_sigma_inputs` (line ~519) special-cases only `method == "tilt"`; anything else gets the single-capture sigma, which is right for an auto row (same 1 s average path as MARK). `import_sessions.py` stores `method` as text; the "stranded 0.6 session" check (soundings.py ~670) only tests for NULL. `icecube/logger_vectors.py` and the Java `SoundingSigma` mirror the same tilt-only rule. Settle during implementation: mention `auto` in the `soundings.py` docstring (line ~522) and the `import_sessions.py` column note if they list method values; optionally one `test_soundings` case that an `auto` row gets the single sigma. No change to `config.example.toml`.
3. **[Issue alignment] Re-arm rule: use host answer 1 exactly.** Replace the open question with: re-arm when horizontal distance from the last auto capture position > 3 x `AUTO_SCATTER_CM`, or the plumb angle exceeds `AUTO_PLUMB_DEG`; then the full still window must hold again. Tests in `AutoCaptureTest` already planned for both paths; add the boundary (5.9 vs 6.1 cm at defaults). A MARK press while latched should probably also count as "this stop is taken" (no auto capture right after a manual MARK at the same spot) — settle during implementation.
4. **[Issue alignment] "Calibrate to enable" text.** Host answer 2: the `NO_CALIBRATION` reason renders as `AUTO: calibrate to enable`. Small, settle during implementation.
5. **[Approach] Cue chatter (settle during implementation).** `AUTO_ARMED` / `AUTO_OFF` as described fire on "armed or turns off after being armed"; if NOT_PLUMB / MOVING count as off, every walk between points would beep. Suggest cueing only gate changes (calibration done, fix lost/young/suspect, tilt or ref start/end, setting toggled), not the per-stop plumb/still state, which the capture tone already covers.
6. **[Approach] Stillness window at 1 Hz (settle during implementation).** A 2 s window holds 2-3 epochs; an sd from so few samples is noisy and may never fall under 2 cm or fall under it by luck. Consider max horizontal deviation from the window mean (or the range) as the test, and define the window as epochs within the last `AUTO_HOLD_S` with at least hold/epoch-interval + 1 epochs. Check the rover's epoch rate on the branch and pin it in tests.
7. **[Approach] Calibration needs RTK fixed epochs.** `TiltSolver.solve` uses antenna positions, so calibration leans should require fixed epochs (as `TiltSet` leans do) and refuse with a reason otherwise; a young fix is acceptable for a relative solve (state which). Also start a calibration only when no tilt set or ref is running, and disable MARK-as-finish semantics during it. Settle during implementation.
8. **[Dependencies] Rebase on #15.** #15 renames `settling` to `young`, removes MARK's settling refusal (Survey.java ~290) and appends columns `fix_start, float_spell_s, suspect_reason, fix_young`. The shared capture method extracted from `mark()` must carry those columns, so auto rows get them for free; branch from main after #15 merges as planned. Version per host answer 5.
9. **[Tests] Plumb sign.** Host answer 4: keep the planned `angleDeg(gravity, fitAxis(...))` test that a plumb pole reads ~0 deg and a 5 deg lean reads ~5 deg (not ~175), plus a phone check listed in the PR.

### Summary

A sound, well-reused plan that matches the owner's decisions and the host's answers. One real consequence is missing (the logger's own `Coverage.feeds` filters on `method`); it is a one-line change with a test and does not block starting. The rest are implementation-level details.

### Recommended Actions

- [ ] Add the new `auto` method to `Coverage.feeds` with a `CoverageTest` case; update the `PointRow` method comment and README `method` row.
- [ ] Implement re-arm per host answer 1 (6 cm horizontal at defaults, or tilt past the plumb limit) with boundary tests.
- [ ] Show `AUTO: calibrate to enable` when uncalibrated; auto capture on by default.
- [ ] Settle during implementation: cue only gate changes; stillness test robust at 1 Hz; calibration requires fixed epochs and no running tilt/ref; MARK while latched counts as taken; Python docstrings mention `auto`.
- [ ] Branch after #15 merges; version 0.12.0 (or one minor past #15's); carry #15's columns through the shared capture path.
- [ ] Plumb sign test plus phone check before merge.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:56 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implementation held until #15 merges (then rebase on main, re-check #15's names and columns). Settle during implementation the Plan Review at 75b4165's items: add PointRow.AUTO to Coverage.feeds with a CoverageTest case and docs; cues only on condition changes; stillness by max deviation/range over the hold window; calibration only on RTK-fixed epochs and not during a tilt set or ref; a MARK while latched counts as that stop's capture; the shared capture path carries #15's new columns.

## Implementation
**Status**: complete
**When**: 2026-11-27 19:59 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Branch**: feature/issue-35 at be243bf

Implemented the plan plus the newest Checkpoint's items, on main with #15 (PR #42) and #23 PR A (PR #43) merged in. Logger 0.13.0 (build 17).

Merged names re-checked against the plan: #15's `FixGuard.young(nowNs)` / `young(stretch, nowNs)`, `Stretch.suspect` / `reason`, `Feedback.Cue.YOUNG` / `SUSPECT`, and the PointRow columns `fix_start, float_spell_s, suspect_reason, fix_young` (via `setStretch`) are as the plan assumed; no adaptation needed. #23's PoleCheck and its cues are untouched (auto capture is Surface only, so no ref path changes). Version: main was 0.12.0 / 16, so 0.13.0 / 17 as the dispatch said (not the plan's 0.12.0).

Commits:
- `08477d5` PointRow.AUTO; Coverage.feeds takes auto rows and now requires `Double.isFinite(hGround)` (the #23 cold-read gap), with CoverageTest cases (auto feeds; NaN and both infinities do not) and a PointRowTest round trip.
- `1c113c2` core/AutoCapture (pure state machine) + AutoCaptureTest (14 cases).
- `d08732d` core/PoleCalibration + PoleAxis + PoleCalibrationTest (9 cases).
- `99f81ae` settings (Limit.AUTO_PLUMB_DEG / AUTO_HOLD_S / AUTO_STILL_CM, boolean auto_capture default on) and Feedback cues (AUTO_ARMED, AUTO_OFF, CAL_START, CAL_PHASE, CAL_DONE).
- `cfbb7ee` Survey / LoggerService / MainActivity wiring.
- `be243bf` version, README, docs/log.md.

How the Plan Review items were settled:
- Re-arm: horizontal move over 3x the still limit from the capture (boundary test 5.9 vs 6.1 cm), or the pole past the plumb limit.
- `AUTO: calibrate to enable (tap)` when uncalibrated; auto capture on by default.
- Stillness: max horizontal deviation from the window mean (not an sd), window from the first good epoch, fires once it spans the hold time with at least 2 epochs (3 at 1 Hz and 2 s). Rate-agnostic; the rover's rate was not pinned (README cites 4 Hz in one place, the tests use 1 Hz).
- Cues: the armed state is debounced 1 s and cued only when the fix caused the change (no fix, young, suspect); mode, tilt, ref, calibration and the setting have their own cues; plumb/still never cue.
- Calibration takes only RTK fixed epochs; young and suspect are accepted (relative geometry of one short spell). It is refused during a tilt set or ref, and MARK, tilt start and ref start are refused during it (no MARK-as-finish).
- A Surface MARK counts as the stop's capture (`AutoCapture.taken`).
- The shared `Survey.capture()` path is used by MARK (single) and auto, so auto rows carry #15's columns, cue and notice.

Deviations from the plan's file list: no ImuLogger change (the existing `gravityMean(from, to)` is enough); no Session change (the axis lives in Survey, which is already per session); no MapView overlay: the calibration and the arming state show on one large AUTO bar under the notice (56 dp, 18 sp, tap to calibrate or stop), plus MENU > Calibrate pole / Recalibrate pole / Stop pole calibration and Back to stop. The calibration's twist phase is timed and counted only; `PoleAxis.extras` is the empty #38 extension point.

Tests: `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 279 tests, 0 failures; APK `logger/app/build/outputs/apk/debug/app-debug.apk` (0.13.0, versionCode 17). C: had 109 GB free. Not installed on the phone.

Needs a phone check before merge: the plumb sign (a plumb pole should read about 0°, a 5° lean about 5°, on the AUTO line's `hold plumb` readout); the calibration feel (3 / 10 / 8 s phases, the 2 cm still restart, the 1° residual and 180° coverage gates, the 100 ms GGA latency guess for the gravity window, the 0.5 m/s swing gate); the cue tones; and the AUTO line's layout in both orientations.

Deferred: mentioning `auto` in `analysis/qgis/soundings.py`'s `capture_sigma_inputs` docstring (that file belongs to the #20 work in flight; its rule already gives an auto row the single-capture sigma, which is right). No `test_soundings` case added for the same reason.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 20:04 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-35 at `085d8e9`
**Base**: main
**Depth**: Standard (reason: ~1500 lines over 20 logger files, new core state machines; no security or cross-layer trigger)
**Must-fix**: 1 | **Suggestions**: 10
**Round**: 1 | **Ship**: continue — round 1: 1 must-fix; first round always re-reviews after fixes

Specialists: static analysis (no Java linter profile; `./gradlew testDebugUnitTest --max-workers 4`: 279 tests, 0 failures), governance (public repo: tests synthetic and relative, the calibration event logs only phone-axis mounting geometry; pass), plan drift (deviations documented in the Implementation entry), Claude adversarial, cross-model gemini and codex (both complete). Clean areas, confirmed by more than one reviewer: gate order and window clearing (nothing fires during a tilt set, ref or calibration), young/suspect gating, a Surface MARK while latched, the cue debounce, plumb sign (TYPE_GRAVITY is an up vector and fitAxis starts from the mean gravity), lock order (Survey then ImuLogger, never reversed), and auto rows going through the shared `capture()` path, so they carry #15's columns and feed Coverage. False positives dropped: gemini's NPE in `autoEnu` (`Epoch.fixed()` implies finite lat/lon) and its `COVERAGE_FIRST` concern (it is computed with indexOf).

### Findings
- [x] (must-fix) A finished Surface tilt set does not count as the stop's capture: `finishTilt` never latches auto, so when the pole is stood up on the same tip auto writes a duplicate Surface row (and a duplicate Coverage node) 2 s later. Latch at the tilt tip's horizontal position, and re-arm only on a move away, because a plumb re-arm would clear the latch on the first leaning epoch. Same path for `finishTilt(true)`. Found by the lead and the Claude adversarial pass. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:678`
- [x] (suggestion) An accepted calibration runs `auto.reset()`, so auto can capture the calibration point about 2 s after DONE if the user pauses plumb. Latch there with the same move-away-only rule. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:513`
- [x] (suggestion) The plumb re-arm has no hysteresis, and a NaN plumb reading (a gravity gap) also unlatches, so drifting from 1.9° to 2.1° at a stop can give a duplicate. Unlatch on a finite tilt clearly over the limit (for example 2x), or only after it lasts. — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java:150`
- [x] (suggestion) The latch is set before `average(now)` is checked, so a skipped capture (no RTK-fixed 1 s average) leaves the stop "taken" and the bar green with a stale label. Reset the latch on that skip. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:479`
- [x] (suggestion) UNDO (void) of an auto capture leaves the latch and the "taken" label in place; reset when the voided row is the latched one. — `logger/app/src/main/java/rink/icecube/logger/Survey.java` (`voidCapture`)
- [x] (suggestion) The calibration's still phase has no epoch-gap check: two fixed epochs on either side of a GPS stall can complete it. Restart it on a gap, as AutoCapture does. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:145`
- [x] (suggestion) The calibration advances only on epochs: with no GGA it never times out and the bar freezes at "3 s" while MARK, tilt and ref stay refused. Add a wall-clock timeout or a GPS-gone notice. Also a NaN height or no gravity sensor fails as "could not hold still", which hides the real cause. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:94`
- [x] (suggestion) Calibration acceptance: `MIN_SAMPLES` counts the still sample, so 3 leans pass (the plan says 4 leans), and the twist phase is never checked for any twist. Make the comparisons NaN-safe (`!(residual <= MAX)`), since a NaN currently passes every gate. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:174`
- [x] (suggestion) The comment for the gravity window says ±150 ms about 100 ms before the epoch, but the upper 50 ms lies in the future at GGA arrival, so the window really used is about [-250, 0] ms. Fix the comment or the window. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:77`
- [x] (suggestion) Wording: the DONE notice says "Auto capture on" even when the setting is off or the mode is not Surface; RESTART says "moved" when the cause was a lost fix or missing gravity; HOLDING's `%.0f` shows "0 / 1 s" for a 0.5 s hold. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:497`
- [x] (suggestion) Still owed before merge: the phone check of plumb sign, calibration feel, cues and layout listed in the Implementation entry. — `logger/README.md` (deferred: needs the owner's phone in the field; stays an owner check before merge (plumb sign, calibration feel incl. the new 90 deg twist minimum, cues, layout))

## Implementation
**Status**: complete
**When**: 2026-11-27 20:13 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-35 at `741120e`
**Addressed**: Local Review (Pre-Push) at `085d8e9` (2026-11-27 20:04 -05:00)
**Commits**: ea0daa4, 741120e

Host-decided (owner may veto): one position latch. Every capture at a stop (MARK, auto,
a finished Surface tilt set at its tip, an accepted calibration at its tip) latches auto
capture there, only once the capture really happened. It clears only when the antenna is
more than 3x the still limit (6 cm) horizontally from the latched position, or when the
latched capture is undone. Refinement: the distance is read only on a plumb epoch, because
a leaning antenna is not over its tip (a 15 deg lean on 1.8 m is 47 cm off it), so the
first leaning epoch after a tilt set cannot clear the tip latch. Twist minimum: 90 deg
about the fitted axis (gyro, range of the running angle); a phone with no gyro fails.

### Actions
- [x] (must-fix) Surface tilt set latches at its tip; no duplicate on standing up — `logger/app/src/main/java/rink/icecube/logger/Survey.java` (finishTilt, autoTaken)
- [x] Accepted calibration latches at its tip (`PoleCalibration.tipGeodetic`) — `Survey.java` (calibrationChange)
- [x] No plumb re-arm: tilt, lost plumb and NaN gravity never clear the latch — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java`
- [x] A fire no longer latches; Survey calls `taken` after the 1 s average check, and the bar shows "ready" when not latched — `AutoCapture.java`, `Survey.java` (autoEpoch, autoView)
- [x] UNDO of the latched capture resets the latch and label — `Survey.java` (voidCapture)
- [x] Still phase restarts on an epoch gap > 2 s — `core/PoleCalibration.java`
- [x] No-epoch timeout (5 s) via the service tick, with its notice; distinct failures for no GGA height, no gravity reading, no RTK fixed — `PoleCalibration.java`, `Survey.java` (onTick), `LoggerService.java`
- [x] MIN_LEANS = 4 counts leans only; twist minimum 90 deg (gyro integral, new `ImuLogger.gyroIntegral`); NaN-safe gates — `PoleCalibration.java`, `ImuLogger.java`
- [x] Gravity window now +-100 ms about 100 ms before the GGA (ends at arrival), comment fixed — `Survey.java:72`
- [x] Wording: done notice respects the setting and mode, restart names its cause, HOLDING shows one decimal — `Survey.java`
- [x] Phone check of plumb sign, calibration feel, cues and layout — `logger/README.md` (deferred: needs the owner's phone in the field; stays an owner check before merge, now including the 90 deg twist minimum)

### Checks
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 294 tests, 0 failures; debug APK built (`logger/app/build/outputs/apk/debug/app-debug.apk`). C: had 110 GB free.
- New tests: AutoCaptureTest (fire not latched until taken, reset clears latch, tilting does not re-arm, NaN gravity / position never clear, tip latch over a tilt set); PoleCalibrationTest (tip is the calibration point, gap restart, lost-fix cause, no-epoch timeout, missing height, no gravity, 3 leans refused, twist under 90 deg, back-and-forth twist, no gyro, NaN gates).
- Not unit tested (Android): the Survey wiring, the service tick and the gyro ring; covered by the owner's phone check.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 20:19 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-35 at `deb885f`
**Base**: main
**Depth**: Standard (reason: ~2200 lines over 23 files, new core state machines; fix round classified on the whole diff)
**Must-fix**: 2 | **Suggestions**: 6
**Round**: 2 | **Ship**: continue — round 2: 2 must-fix include a design/correctness concern (not mechanical)

Specialists: static analysis (no Java linter profile; `./gradlew testDebugUnitTest --max-workers 4`: 294 tests, 0 failures), governance (public repo: no coordinates in the diff, tests synthetic; pass), plan drift (no new drift beyond the documented deviations), Claude adversarial, cross-model gemini and codex (both complete). All round-1 fixes verified in code (tilt-set and calibration tip latch, latch only after capture, undo reset, still gap restart, no-epoch tick timeout, MIN_LEANS, NaN-safe gates, twist check, gravity window, wording). Dropped as false positives or already settled: gemini's autoEnu NPE (`Epoch.fixed()` requires finite lat/lon) and COVERAGE_FIRST (computed with indexOf), both again; gemini's whole-window clear on a settling epoch (conservative, delays a hold by an epoch); gemini's cue debounce switch (adversarial checked the debounce).

### Findings
- [x] (must-fix) The 6 cm re-arm distance is smaller than the plumb cone: a "plumb" antenna can sit L*sin(plumbDeg) = 6.3 cm off its tip (1.8 m pole, 2 deg), so standing up after a tilt set or calibration at about 1.9 deg, or swaying across the cone after a MARK/auto (up to 12.6 cm), clears the latch and a duplicate auto capture follows after the hold; the settings (plumb up to 10 deg, still down to 0.2 cm) make it worse. Re-arm at least pole*sin(plumbDeg) + 3x still (pole height into AutoCapture), or project to the tip. Touches the host's 6 cm decision. Found by the lead and the Claude adversarial pass. — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java:123`
- [x] (must-fix) Settings help text and the journal still say the latch re-arms on a tilt past the plumb limit, which the round-1 fix removed. — `logger/app/src/main/java/rink/icecube/logger/SettingsActivity.java:197`, `docs/log.md:160`
- [x] (suggestion) Lean speed gate 0.5 m/s vs the 1 deg residual gate: a 100 ms latency error at 0.5 m/s is about 1.6 deg (adversarial says too loose); gemini says brisk leans may exceed it (too tight). Log each lean's speed so the field run can set it. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:43`
- [x] (suggestion) A hold under 1 s (allowed down to 0.5 s) still captures the 1 s average, which can include pre-hold epochs; average only the hold or set the minimum hold to 1 s (gemini and codex). — `logger/app/src/main/java/rink/icecube/logger/Survey.java:490`
- [x] (suggestion) An RTK fixed epoch with no GGA height arms and fires auto capture (autoEnu substitutes 0), writing a Surface row with no height; require a finite height (codex). — `logger/app/src/main/java/rink/icecube/logger/Survey.java:472`
- [x] (suggestion) `autoLatchN` stays set after the latch clears by moving, so a later undo of that row resets a hold at the next stop; and a MARK at an auto-taken stop moves `autoLatchN`, so undoing the MARK unlatches although the auto row stands (adversarial, lead). — `logger/app/src/main/java/rink/icecube/logger/Survey.java:934`
- [x] (suggestion) `stillBadCause` is not cleared by a good epoch, so a later timeout can name an old no-gravity/not-fixed cause (gemini). — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:188`
- [x] (suggestion) A silent GPS stall shows "not RTK fixed" after 3 s but plays no AUTO_OFF cue, since only epochs drive AutoCapture (gemini). — `logger/app/src/main/java/rink/icecube/logger/Survey.java` (autoView / onTick)

## Implementation
**Status**: complete
**When**: 2026-11-27 20:29 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-35 at `5e6fedb`
**Addressed**: Local Review (Pre-Push) at `deb885f` (2026-11-27 20:19 -05:00)
**Commits**: bd01255 (merge origin/main, #20), d8f2c25, 048756f, 1f19c8f, 5a80e52, c256734, c14664d, a0101eb, 5e6fedb

### Actions
- [x] (must-fix) Re-arm compares tips, not antennas (host decision): the latch stores the capture's tip and clears only when the epoch's tip is certainly more than 3x the still limit from it; an epoch with NaN gravity, axis, pole height or position never clears it. The lean's azimuth needs a heading the logger lacks (gravity gives only the tilt angle), so each tip is a disk: centre the antenna, radius pole x sin(theta) (0 for a solved tilt-set or calibration tip; a MARK uses its 1 s gravity, the pole height if unknown; a fire the largest lean of its hold). Tests: standing up at 1.9 deg after a tilt set, swaying across the 2 deg cone after a MARK, a real 7 cm tip move, plumb 10 deg with still 0.2 cm. — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java`, `Survey.java`, `logger/README.md`
- [x] (must-fix) Settings help text and journal reworded: the tip must move 3x the still limit; a tilt does not re-arm. — `SettingsActivity.java`, `docs/log.md`
- [x] (suggestion) Each lean-phase epoch's speed goes to the calibrated and failed events (`lean_speeds_mps=`); the 0.5 m/s gate is unchanged. — `PoleCalibration.java`, `Survey.java`
- [x] (suggestion) Chose the minimum: hold time is now 1 to 30 s (a stored 0.5 reads as the 2 s default), so the 1 s capture average never reaches before the hold. — `core/Limit.java`
- [x] (suggestion) New NO_HEIGHT gate: a fixed epoch with no GGA height never arms or fires (fix-caused, so it cues). — `AutoCapture.java`, `Survey.java`
- [x] (suggestion) autoLatchN clears when the latch clears; a capture at a still-latched stop keeps the first latch and its row (AutoCapture.taken returns false), so undoing that MARK leaves the stop latched by the auto row. — `Survey.java`, `AutoCapture.java`
- [x] (suggestion) A good still-phase epoch clears stillBadCause. — `PoleCalibration.java`
- [x] (suggestion) AUTO_OFF plays once when epochs stop for 3 s (the "not RTK fixed" condition) via AutoCapture.onStale on the service tick; ARMED plays when they return. — `AutoCapture.java`, `Survey.java`

### Checks
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD SUCCESSFUL, 303 unit tests, 0 failures; APK `logger/app/build/outputs/apk/debug/app-debug.apk`. C: had 108.9 GB free.
- After the merge: `python3 -m unittest discover icecube/tests` 130 OK; `analysis/qgis/tests` 175 OK (5 skipped).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 20:31 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**Branch**: feature/issue-35 at `10bcdf2`
**Base**: main
**Depth**: Standard (reason: round 3 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 2
**Round**: 3 | **Ship**: recommended — no must-fix findings; remaining suggestions can be applied or tracked

Scope: round 2's findings (Local Review at `deb885f`) and the fix diff e6066da..10bcdf2; the merge of main (#20, bd01255) was clean and not re-reviewed. Cross-model: skipped (round-3 convergence check, per handoff). Static analysis: no Java linter profile; `./gradlew testDebugUnitTest --max-workers 4`: 303 tests, 0 failures. All 8 round-2 items verified fixed in code and tests. The tip-disk deviation (radius pole x sin(theta), no heading sensor) meets the intent: at one stop the disks overlap (standing up after a tilt set, swaying across the 2 deg cone, plumb 10 deg with still 0.2 cm, all tested), and a real move re-arms once the antenna centres are more than R_latch + r + 3x still apart (at most about 19 cm at the defaults, 68 cm at the 10 deg plumb limit), well under a sounding spacing. Pole height always parses to a finite default, so the fire radius is never NaN in practice.

### Findings
- [ ] (suggestion) README says the tip must move 6 cm; the antenna spacing that actually re-arms is up to 2 x pole x sin(plumb limit) + 3x still (about 19 cm at the defaults, 68 cm at 10 deg); say so, so a close grid at a wide plumb limit is not a surprise. — `logger/README.md` (Auto capture)
- [ ] (suggestion) A Surface MARK with no gravity reading after calibration latches with a pole-length radius (about 1.8 m), so auto capture stays latched until the antenna is about 1.9 m away; conservative (never a duplicate) and rare, but a radius of pole x sin(plumb limit) or skipping the latch would keep close stops auto. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:409`

## Checkpoint
**Status**: complete
**When**: 2026-11-27 20:31 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push round 3 approved; orchestrator table allows publish)
**After**: publish
**Decision**: publish

The round-3 review's two suggestions (README re-arm spacing; MARK without gravity latching at full pole height) go to the PR-mode triage.

## Local Review
**Status**: complete
**When**: 2026-11-27 20:32 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**PR**: #45 at `049b845`
**Depth**: Deep (reason: cold read, cross-model only: codex, per handoff)
**Must-fix**: 0 | **Suggestions**: 2

Codex ran (EXIT=0) on the PR title, URL and diff only. It reported 2 must-fix and 1 suggestion; checked against the code, as lead:
- Codex must-fix "a stationary pole's RTK drift clears the latch": dropped. Clearing needs the antenna to move more than 6 cm plus both tip radii on an RTK-fixed, settled, non-suspect fix; a wander that large is the false fix FixGuard is there to catch, not normal fixed noise.
- Codex must-fix "pole height change leaves the axis active": downgraded to suggestion. Plumb (PoleAxis.plumbDeg) uses only gravity and the axis, not the pole height, so the claimed wrong plumb does not happen; but changing the height usually means the pole was handled, which may move the phone in its mount.

### Findings
- [ ] (suggestion) Changing pole height mid-session keeps the calibrated axis; consider asking for a recalibration (or a notice) when the height changes after a calibration — `logger/app/src/main/java/rink/icecube/logger/LoggerService.java:130`
- [ ] (suggestion) setLimits keeps the hold window, so epochs accepted under a looser plumb limit still count after the limit is tightened; clear the window in setLimits — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java:131`

## Integrated Review
**Status**: complete
**When**: 2026-11-27 20:35 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #45 at `3ec21cd`
**Sources**: 2 (Local Review (Pre-Push) @ `10bcdf2` (covers 3ec21cd via bookkeeping), Local Review @ `049b845` (Codex cold read, covers 3ec21cd via bookkeeping)); no hosted reviews (Copilot out of quota); CI none
**Cross-source confirmations**: 0
**CI**: all-pass (no checks configured)

### Findings
- [ ] (suggestion, Local Review (Pre-Push) @ 10bcdf2) README's Auto capture section says only that the tip must move 3x the still limit (6 cm); the antenna spacing that actually re-arms is up to 2 x pole x sin(plumb limit) + 3x still (about 19 cm at the defaults, 68 cm at 10 deg). Add that sentence so a close grid at a wide plumb limit is not a surprise. Still open at README line ~470. — `logger/README.md`
- [ ] (suggestion, Local Review (Pre-Push) @ 10bcdf2) A Surface MARK after calibration with no gravity reading latches with tipR = pole height (about 1.8 m), so auto capture stays latched until about 1.9 m away. Conservative, never a duplicate, but use pole x sin(plumb limit) as the fallback radius, or skip the latch when gravity is missing. Still open at Survey.java:406-409. — `logger/app/src/main/java/rink/icecube/logger/Survey.java`
- [ ] (suggestion, Local Review @ 049b845) A pole height change mid-session only logs `pole_height=... changed` and keeps the calibrated axis. Post a notice (or ask for recalibration) when the height changes after a calibration. Still open at LoggerService.java:129-134. — `logger/app/src/main/java/rink/icecube/logger/LoggerService.java`
- [ ] (suggestion, Local Review @ 049b845) `setLimits` does not clear the hold window, so epochs accepted under a looser plumb or still limit still count toward the hold after the limit is tightened. Add `window.clear()` to `setLimits` (reached via `Survey.applyAutoSettings`). Still open at AutoCapture.java:131-133. — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java`

### False positives
None.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 20:35 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (4 small valid suggestions; orchestrator table allows address)
**After**: findings
**Decision**: address

Fix all four of the Integrated Review at 24c63c3. Host decisions (owner may veto): a MARK without gravity latches with radius pole × sin(plumb limit), not the full pole height; a pole-height change after calibration keeps the axis but shows a notice "pole height changed: recalibrate if the phone moved in its mount" (no forced recalibration); setLimits clears the hold window; README states the real antenna re-arm spacing (≈ 19 cm at the defaults, 68 cm at 10°).
