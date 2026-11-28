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
- [ ] (must-fix) A finished Surface tilt set does not count as the stop's capture: `finishTilt` never latches auto, so when the pole is stood up on the same tip auto writes a duplicate Surface row (and a duplicate Coverage node) 2 s later. Latch at the tilt tip's horizontal position, and re-arm only on a move away, because a plumb re-arm would clear the latch on the first leaning epoch. Same path for `finishTilt(true)`. Found by the lead and the Claude adversarial pass. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:678`
- [ ] (suggestion) An accepted calibration runs `auto.reset()`, so auto can capture the calibration point about 2 s after DONE if the user pauses plumb. Latch there with the same move-away-only rule. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:513`
- [ ] (suggestion) The plumb re-arm has no hysteresis, and a NaN plumb reading (a gravity gap) also unlatches, so drifting from 1.9° to 2.1° at a stop can give a duplicate. Unlatch on a finite tilt clearly over the limit (for example 2x), or only after it lasts. — `logger/app/src/main/java/rink/icecube/logger/core/AutoCapture.java:150`
- [ ] (suggestion) The latch is set before `average(now)` is checked, so a skipped capture (no RTK-fixed 1 s average) leaves the stop "taken" and the bar green with a stale label. Reset the latch on that skip. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:479`
- [ ] (suggestion) UNDO (void) of an auto capture leaves the latch and the "taken" label in place; reset when the voided row is the latched one. — `logger/app/src/main/java/rink/icecube/logger/Survey.java` (`voidCapture`)
- [ ] (suggestion) The calibration's still phase has no epoch-gap check: two fixed epochs on either side of a GPS stall can complete it. Restart it on a gap, as AutoCapture does. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:145`
- [ ] (suggestion) The calibration advances only on epochs: with no GGA it never times out and the bar freezes at "3 s" while MARK, tilt and ref stay refused. Add a wall-clock timeout or a GPS-gone notice. Also a NaN height or no gravity sensor fails as "could not hold still", which hides the real cause. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:94`
- [ ] (suggestion) Calibration acceptance: `MIN_SAMPLES` counts the still sample, so 3 leans pass (the plan says 4 leans), and the twist phase is never checked for any twist. Make the comparisons NaN-safe (`!(residual <= MAX)`), since a NaN currently passes every gate. — `logger/app/src/main/java/rink/icecube/logger/core/PoleCalibration.java:174`
- [ ] (suggestion) The comment for the gravity window says ±150 ms about 100 ms before the epoch, but the upper 50 ms lies in the future at GGA arrival, so the window really used is about [-250, 0] ms. Fix the comment or the window. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:77`
- [ ] (suggestion) Wording: the DONE notice says "Auto capture on" even when the setting is off or the mode is not Surface; RESTART says "moved" when the cause was a lost fix or missing gravity; HOLDING's `%.0f` shows "0 / 1 s" for a 0.5 s hold. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:497`
- [ ] (suggestion) Still owed before merge: the phone check of plumb sign, calibration feel, cues and layout listed in the Implementation entry. — `logger/README.md`
