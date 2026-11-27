---
issue: 15
---

# Issue #15 — Logger: retune the false-fix guard (start-of-session false alarms, a missed false fix)

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:45 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #15

### Scope Assessment

**Well-scoped?** Mostly, but large and still carrying open decisions. The body (2026-11-15 evidence) has since been widened by three comments (11-21 float-start false fix and settle; 11-26 ~120 s re-fix settle). Together that is: (a) start-jump reference (float-only, unknown when no float); (b) a float-spell rule (fix starting after a long float is suspect whatever the jump); (c) a young-fix window, now 90 s or 120 s; (d) a separate height-component test; (e) a "validated" mark from occupying a known point; (f) extra cues (sats, ratio/GST, correction age; still-pole height drift); (g) retuning on private sessions. One PR is feasible only if (a), (b), (c) and the column additions come first; (d)-(g) should be split out or explicitly deferred in the plan.
**Right repo?** Yes: `logger/` (FixGuard, Survey, PointRow, Settings, README); the overlay consumer is in `core/Coverage.java`.
**Serves**: the owner's survey-readiness for winter ice accumulation (ice growth is a few cm between sessions, so false fixes and session offsets dominate the error budget). Project design/goals document: none.
**Design picture?** No change to the logger's structure. It does change the documented "False-fix guard" behaviour and the CSV schema (`logger/README.md`, False-fix guard and points.csv sections).
**Dependencies**: #23 (pole check; a validated mark in (e) shares its "robust reference" logic, so do not build two reference rules); #24 and #29 (closed; they consume `fix_age_s`/`fix_suspect`, and the overlay replays show 37 of 58 red nodes from young fixes, so the settle choice feeds the coverage overlay); #27 and #9 (merged; no conflict, but the new columns must not break the `void`/`flag_reason` export or the tilt rows). Not blocked by any of them.

### Principle Alignment

No project principles document exists, so the checks are the project's CLAUDE.md rules and the issue's own constraints.

| Principle | Status | Notes |
|---|---|---|
| Public repo: no coordinates, private data stays private | Watch | Retuning uses private sessions. Tests and the README may carry only relative numbers (jump in m, age in s, height offsets in cm) and synthetic epoch sequences. Fixtures built from real sessions would leak positions. |
| Keep older sessions readable | Action needed | Keep `fix_age_s`, `fix_jump_m`, `fix_suspect` as they are and append new columns after them (the issue says so). Check that `PointRow`, `ShareZip` and the Python readers (`analysis/`, `icecube/`) tolerate extra columns, and that a row with a blank/unknown jump reads as "unknown", not as 0 or suspect. |
| Settle time must match the evidence | Action needed | The 10 s default catches little. Evidence: 7.9 cm rms under 90 s, ~3 cm after 150 s (11-21), and one re-fix still +6..+9 cm at 95-113 s (11-26). The plan must decide, with the owner, between a hard refusal and a warn-and-record rule, and the numbers (90 vs 120 s). The existing settle refusal blocks capture outright, which in the field means standing and waiting 2 minutes after every drop. |
| Unknown is not suspect, and not clean | Action needed | With no recent float epoch the jump is unknown. Comment 2 shows a start-of-session stretch flagged at 2.33 m was fine, and the 11-15 miss had a 0.75 m jump. Define the float-spell rule so it cannot be satisfied by absence of data: how long a float counts as "a while", and what happens to a stretch that starts after single/DGPS only. Add the "not confirmed by a ref" clause carefully: a stretch cannot be validated by a ref taken inside it. |
| Height handled separately | Watch | False fixes are worst in height. A separate threshold needs a reference height (a previous float average has a poor height itself), so it may belong to the validated-mark logic rather than the start-jump rule. |
| Validated mark vs pole check (#23) | Watch | Do not give two different notions of "agrees with an earlier visit". Reuse one robust reference (median of visits, skipping failed visits) for the validated mark and the pole check, or order the work so #23 lands the reference first. |
| Test with synthetic sequences | Watch | FixGuard has unit-test seams (`FixGuardTest`-style, `PointRowTest`). The three field cases (start-of-session jump from single, float-spell start at 0.48 m, re-fix settle) should each be a synthetic epoch-sequence test. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no `docs/decisions/`. |

### Consequences

- Update `logger/README.md` (False-fix guard, Settings defaults, points.csv columns, notice text) and the logger version/changelog.
- New columns appended in `PointRow`, the share zip and anything in `analysis/` or `icecube/` that reads `points.csv` by position.
- New settings go in the Settings screen and the README Settings list; `config.example.toml` only if an analysis script gains a setting.
- If captures are fed to the overlay with age-based sigma, #20's sigma rule and `core/SoundingSigma` are affected: say whether this issue changes the overlay's feed or only records age and flags.

### Recommendations

- Split by value. First PR: float-only start jump with "unknown" when no float epoch, a float-spell suspect rule, a young-fix warning and record (age already logged), appended columns, synthetic tests, README. Leave the height-component test, the validated mark, the extra cues (satellites, ratio/GST, correction age) and the retune for follow-up issues unless the owner wants them now.
- Ask the owner before planning: should a young fix (under 90 or 120 s) refuse the capture as today, or capture with an amber warning and a recorded age? This sets the field routine for every session.
- Treat the false-fix risk as asymmetric for ice work: prefer flagging a good stretch (cost: a re-shoot) to missing a bad one (cost: a session offset of decimetres). The float-spell rule should err toward suspect.
- Make the retune evidence-based: replay the 11-11, 11-15, 11-21 and 11-26 sessions offline through the new FixGuard, report only relative counts (flagged, missed, false alarms per session), and keep the data out of the repo.
- Ship the guard changes before the first ice-surveying session this winter, and say in the plan which analysis weights (`fix_age_s`) the processing side applies.

### Actions
- [ ] Keep `fix_age_s`, `fix_jump_m`, `fix_suspect` as they are and append new columns after them (the issue says so). Check that `PointRow`, `ShareZip` and the Python readers (`analysis/`, `icecube/`) tolerate extra columns, and that a row with a blank/unknown jump reads as "unknown", not as 0 or suspect.
- [ ] The 10 s default catches little. Evidence: 7.9 cm rms under 90 s, ~3 cm after 150 s (11-21), and one re-fix still +6..+9 cm at 95-113 s (11-26). The plan must decide, with the owner, between a hard refusal and a warn-and-record rule, and the numbers (90 vs 120 s).
- [ ] With no recent float epoch the jump is unknown. Define the float-spell rule so it cannot be satisfied by absence of data: how long a float counts as "a while", and what happens to a stretch that starts after single/DGPS only. A stretch cannot be validated by a ref taken inside it.
- [ ] Split by value. First PR: float-only start jump with "unknown" when no float epoch, a float-spell suspect rule, a young-fix warning and record (age already logged), appended columns, synthetic tests, README. Leave the height-component test, the validated mark, the extra cues (satellites, ratio/GST, correction age) and the retune for follow-up issues unless the owner wants them now.
- [ ] Ask the owner before planning: should a young fix (under 90 or 120 s) refuse the capture as today, or capture with an amber warning and a recorded age? This sets the field routine for every session.
- [ ] Treat the false-fix risk as asymmetric for ice work: prefer flagging a good stretch (cost: a re-shoot) to missing a bad one (cost: a session offset of decimetres). The float-spell rule should err toward suspect.
- [ ] Make the retune evidence-based: replay the 11-11, 11-15, 11-21 and 11-26 sessions offline through the new FixGuard, report only relative counts (flagged, missed, false alarms per session), and keep the data out of the repo.
- [ ] Ship the guard changes before the first ice-surveying session this winter, and say in the plan which analysis weights (`fix_age_s`) the processing side applies.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:44 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: owner
**After**: issue-actions
**Decision**: proceed

Owner's answers (2026-11-27): young fix: "Amber warning, capture (Recommended)" (capture, warning cue, record the fix age; processing flags it); settle time: "120 s (Recommended)" (a setting); scope: "Narrow first PR, open follow-ups (Recommended)". First PR: float-spell rule (a fix starting inside or after a float spell is suspect whatever the jump; start jump measured from float epochs only, unknown without one), young-fix amber warning + recorded age with settle 120 s, new columns appended after fix_age_s/fix_jump_m/fix_suspect, synthetic epoch-sequence tests for the three field cases, README. Follow-ups opened: #39 height test, #40 extra cues (sats, ratio/GST, correction age), #41 retune on past sessions; the "validated by a known point" mark goes to #23 (comment posted). Context: the owner is preparing to survey ice accumulation this winter (cm-level differences between sessions).

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:46 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-15/plan.md` at `c5af79b`

First PR only, as the owner scoped it: float-only start jump plus a float-spell suspect rule in FixGuard, the 120 s settle becomes a young-fix amber warning (capture allowed, `fix_young` recorded), four columns appended after `void_reason`, synthetic tests for the three field cases, python/importer/soundings port, README and version 0.11.0. Six open questions are listed in the plan.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:47 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: needs-work

**Issue**: #15 — Logger: retune the false-fix guard (start-of-session false alarms, a missed false fix)
**Plan**: `.agent/work-plans/issue-15/plan.md` at `c5af79b`
**Branch**: `feature/issue-15`

Reviewed against main and the newest Checkpoint (owner: amber warning and capture; settle 120 s; narrow first PR) together with the host's answers to the plan's six open questions. The host's answers are sound; three of them change the plan in ways it does not yet list (findings 1-3). Small fixes, no change of approach.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Narrow first PR as the owner chose; #39, #40, #41, #23 hold the rest. About 12 files plus tests; the plan names the python port as the part to split off if it runs long. |
| Issue alignment | Good | Covers all three field cases (A float spell, B 120 s young, C single start) and every issue-review action. Serves: "Survey-readiness for cm-level ice accumulation between sessions (false fixes are the largest error term); project has no goals or design document: none." |
| File targeting | Needs work | Missing `core/PoleCheck.java` (host answer 3), `Feedback.java` (answer 4), the one-time migration in `Settings.java` (answer 5), and `icecube/logger_vectors.py` (its `fix_age_past_settle` case is hardcoded at 90 s). |
| Consequences | Needs work | The table is missing "young ref is not a pole-check reference" (PoleCheck, refs.csv) and the vector case name/value. |
| Principle alignment | Good | Unknown is neither suspect nor clean; columns appended; synthetic tests only; relative numbers in the PR. |
| ADR compliance | Good | The project has no ADRs. |
| ROS conventions | N/A | Not a ROS project. |

### Host answers checked

1. Float spell 20 s, a setting, retuned in #41: fine. Plan step 4 already adds `Limit.FLOAT_SPELL_S` (0 turns it off).
2. Float only (quality 5): fine, and it matches the offline `fix_stretches`, which already measures the jump only from a float or fixed epoch (`ep.fix[prev] in (4, 5)`, soundings.py about line 371).
3. Young ref captured and recorded, not a pole-check reference; young lean kept and marked: right for cm-level work, but the plan needs it written in (finding 1).
4. Distinct young cue: fine, since young is not suspect. The plan currently says "reuse the warn cue" (finding 2).
5. Stored 10 becomes 120: fine only as a one-time migration (finding 3).
6. Processing moves to 120 s: fine and consistent; say in the PR that rerun sessions change slightly (finding 4 for the vector case).

### Findings

1. **[File targeting / Consequences]** Host answer 3: `PoleCheck.matches` (core/PoleCheck.java:33) skips only voided rows and rows with no position or ground height. It must also skip earlier refs with `fix_young` = 1, while a young new ref is still checked against older refs and the result is shown. Older refs.csv rows (no column, so young is unknown) stay usable, as they are today. Add PoleCheck and a `PoleCheckTest` case. Keep the #23 note at line 31 (selection must keep skipping voided rows) and add young beside it.
2. **[Approach]** Host answer 4: replace "the existing warn cue (a new distinct cue only if the owner wants one)" with a new `Feedback.Cue` (for example `YOUNG`) in `Feedback.java`: a short tone and a vibration pattern that differ from SUSPECT and ERROR and are loud enough to hear with gloves on. Played on a young MARK, a young lean and a young ref end. Add a line to the README cue list.
3. **[Approach]** Host answer 5: plan step 4 says "an existing stored 10 stays 10". Change it to a one-time migration in `Settings.java`: if `fix_settle_s` holds 10 and a marker preference (for example `fix_settle_migrated`) is not set, write 120; then always set the marker. If the check ran on every read, a surveyor who later chooses 10 on purpose would be reset to 120 at each start. Any other stored value is kept. Add a test if Settings has a JVM seam, otherwise say why there is none. Update the README note.
4. **[Consequences]** Host answer 6: `icecube/logger_vectors.py:91` `fix_age_past_settle` uses fix age 90.0. Under 120 s it is no longer past settle, so the case would quietly test the settle term instead. Move it to an age past 120 (for example 150) before regenerating `coverage_vectors.json`, and keep a case inside the window.
5. **[Approach]** Young ref marking: plan step 3 marks a ref young "from its last-epoch age", which is how `fix_age_s` is set today (Survey.java about line 229). Now that young epochs are no longer paused, a ref that starts at 100 s and ends at 160 s averages 20 s of young epochs but is recorded as not young, and so becomes a pole-check reference. Mark the ref young if any averaged epoch was young (the age at the ref's first counted epoch below the limit), the same worst-case rule the plan uses for tilt rows. Also, `refJump`/`refSuspect` are only gathered while not settling (Survey.java:199); gather them for every fixed epoch once the pause is gone.
6. **[Approach, minor]** Python port: `fix_stretches` already measures the jump from float or fixed epochs only and `Epochs.fix` carries the quality, so only the float-spell rule and the `float_spell` flag need porting. "Check Epochs for a float marker and add one if missing" can go. The `TiltSet` per-lean header (TiltSet.java:23) carries `fix_age_s,fix_jump_m,fix_suspect`; say whether leans also get `fix_young`/`fix_start` or only the combined tilt row.

### Summary

The approach is right and matches the owner's choices; the host's six answers are all sound. Before implementing, the plan should take in the young-ref exclusion in PoleCheck, the distinct cue, the one-time settle migration, the vector case at 90 s and worst-case young marking for refs.

### Recommended Actions

- [ ] Add PoleCheck young-ref exclusion and test (finding 1).
- [ ] Add a distinct `YOUNG` cue in Feedback.java and the README (finding 2).
- [ ] Make the 10 to 120 change a one-time migration with a marker (finding 3).
- [ ] Move the `fix_age_past_settle` vector case past 120 s before regenerating (finding 4).
- [ ] Mark a ref young if any counted epoch was young; gather the ref jump/suspect from all fixed epochs (finding 5).
- [ ] Trim the python port to float spell only; decide on the per-lean columns (finding 6).
- [ ] Replace the plan's Open Questions with the host's answers.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:48 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work on plan completeness only; orchestrator table allows revise)
**After**: plan
**Decision**: revise

Host answers to the planner's six open questions (owner may veto; the review found all six right): 1 float spell 20 s, a setting, retuned in #41; 2 float (quality 5) only, single/DGPS logged not suspect; 3 young refs and tilt leans captured with the warning, a young ref recorded young and never used as a pole-check reference (still checked and shown), a young lean kept and its row marked young; 4 a distinct short YOUNG cue, loud for gloved use; 5 one-time migration (marker pref): a stored fix_settle_s of 10 becomes 120, other values kept; 6 processing moves to 120 s, regenerate coverage_vectors.json, say it in the PR. Revise the plan for all findings of the Plan Review at 7b8f635 (PoleCheck.matches skips young refs + test; Feedback.Cue YOUNG + README; one-time settings migration; move fix_age_past_settle past 120 s before regenerating vectors; a ref is young if any counted epoch was young, refJump/refSuspect gathered on every fixed epoch; port only the float-spell rule to Python; state the per-lean tilt header choice) and replace the Open Questions with these answers.

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:48 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-15/plan.md` at `1886544`

Revision after the Plan Review (needs-work): added the PoleCheck young-ref skip, a distinct YOUNG cue, a one-time settle migration (pure core function plus test), the 150 s vector case, worst-case young refs with ref jump gathered on every fixed epoch, a Python port trimmed to the float-spell rule, and the per-lean tilt header choice. Open Questions replaced by the host's six answers.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:49 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: ready

**Issue**: #15 — Logger: retune the false-fix guard (start-of-session false alarms, a missed false fix)
**Plan**: `.agent/work-plans/issue-15/plan.md` at `1886544`
**Branch**: `feature/issue-15`

Re-review after the revision for the Plan Review at 7b8f635. All six earlier findings are resolved against main's code. The revision adds one wording problem in step 1 (finding 1) and a few small items; none needs another plan round. Each is listed below to settle during implementation.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Narrow first PR as the owner chose; #39, #40, #41, #23 hold the rest. The python port is still named as the part to split off. |
| Issue alignment | Good | Cases A, B, C and the owner's three answers covered; Decisions record the host's six answers. Serves: "Survey-readiness for cm-level ice accumulation between sessions (false fixes are the largest error term); project has no goals or design document: none." |
| File targeting | Good | PoleCheck, SettleMigration, Feedback, logger_vectors.py now listed. Small gaps (findings 3, 4) are in files already listed. |
| Consequences | Good | Young-ref row added; the "ShareZip check" in the points.csv row does not exist on main (ShareZip does not read PointRow's header); `RefsStore` (line 140) rewrites the refs.csv header to the new `REFS_HEADER` itself. Minor (finding 5). |
| Principle alignment | Good | Unknown is neither suspect nor clean; appended columns; synthetic tests; relative numbers only. |
| ADR compliance | Good | The project has no ADRs. |
| ROS conventions | N/A | Not a ROS project. |

### Earlier findings checked

1. PoleCheck young-ref skip with test, voided-row skip and #23 note kept: resolved (step 3, Refs/Pole check).
2. Distinct `Feedback.Cue.YOUNG`, README cue list: resolved (step 3a).
3. One-time settle migration with marker pref; pure `core/SettleMigration` with JVM test since `Settings` has no JVM seam (tests are only under `core/`): resolved (step 4).
4. `fix_age_past_settle` (logger_vectors.py:91, age 90.0) moved to 150 s before regenerating; `fix_age_45` and `tilt_capture_settling` (30 s) stay inside the window: resolved.
5. Ref young if the age at its first counted epoch is under the limit; `refJump`/`refSuspect` gathered on every fixed epoch: resolved.
6. Python port trimmed to the float-spell rule; per-lean `OBS_HEADER` keeps its three columns, new ones only on the combined tilt row: resolved.

### Findings (all to settle during implementation, none blocking)

1. **[Approach]** Step 1 says the jump is measured "only from the last float epoch" and that after anything else it is unknown, yet it keeps a `fixed-jump` prior. On main, `FixGuard.onEpoch` also starts a stretch on a step > 0.5 m between fixed epochs and measures its jump from that fixed epoch (`withinFix`); a 2 m in-fix jump is suspect today. Read literally, step 1 would make it unknown and not suspect, which is a regression, and it would break parity with `fix_stretches`, which measures from `ep.fix[prev] in (4, 5)`. Implement it as: the jump comes from the previous float **or fixed** epoch (<= 2 s); it is unknown after single/DGPS, no position, the first epoch or a gap. Keep a FixGuardTest case for a suspect in-fix jump.
2. **[Approach]** The plan says `verify_suspect` "can still clear a float-spell stretch", but on main it selects only rows flagged `suspect_jump` (soundings.py:1107) and renames only that flag (line 1136). Extend both to `float_spell` (or decide it stays uncleared and say so in the docs).
3. **[File targeting]** Smaller UI and setting work in files already listed: `MapView.java:574` ref banner "PAUSED: fix settling" and `Survey.RefView.settling` (line 125-127, which sets `paused`) go along with the tilt banner text; `Limit.FLOAT_SPELL_S` needs a pref key in `Settings.LIMIT_KEYS`/`LIMITS` and a FixGuard constructor/`setLimits` parameter; the python float-spell threshold needs a `DEFAULTS` entry (e.g. `float_spell_s`), and an `icecube.example.toml`/config note if it is user-facing.
4. **[Approach]** Run the settle migration wherever the setting is first read, not only at service start: `SettingsActivity` can be opened before the service ever runs and would show 10. Calling it from `Settings.prefs()` (or both entry points) is enough.
5. **[Consequences]** Drop "ShareZip check" from the points.csv row (no such check on main) and add `RefsStore` (header rewrite, older rows parsed short) so the row is right.

### Summary

The revision took in every earlier finding correctly. The one point to get right in code is finding 1 (keep fixed epochs as a jump source for in-fix jumps); the rest is bookkeeping. Ready for implementation.

### Recommended Actions

- [ ] Measure the start jump from the previous float or fixed epoch; unknown only after single/DGPS/none/gap; keep an in-fix suspect test (finding 1).
- [ ] Extend `verify_suspect` to `float_spell` or document that it is not cleared (finding 2).
- [ ] Ref banner/RefView, FLOAT_SPELL_S wiring, python `float_spell_s` default (finding 3).
- [ ] Run the migration on first read of the setting (finding 4).
- [ ] Fix the consequences row wording (finding 5).

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:50 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implementation must also settle the Plan Review at 6b369a9's items: start jump from the previous float OR fixed epoch (unknown only after single/DGPS, no position, first epoch or a gap; keep a test for a suspect jump within a fix, matching Python fix_stretches); extend verify_suspect to float_spell stretches (or document they are not cleared: prefer extending); MapView ref banner and RefView.settling; FLOAT_SPELL_S pref key in Settings.LIMIT_KEYS/LIMITS, FixGuard constructor and setLimits; Python float_spell_s default; run the settle migration on first read of the setting; consequences: drop ShareZip check, add RefsStore header rewrite.

## Implementation
**Status**: complete
**When**: 2026-11-27 18:03 -05:00
**By**: Claude Code Agent (claude-opus)
**Branch**: feature/issue-15 at e9f8177

First PR of the plan, plus every item of the Checkpoint after the plan review at 6b369a9.

### Commits
- 26abd98 FixGuard: jump only from a float or fixed epoch (unknown after single/DGPS, none, first epoch, gap; in-fix jumps still measured and suspect, matching `fix_stretches`); float spell rule (`Limit.FLOAT_SPELL_S` 0-300, default 20, 0 off; pref `fix_float_spell_s` in `LIMIT_KEYS`/`LIMITS`, constructor and `setLimits`); `Stretch.prior/floatSpellS/reason`; settling renamed young; `Limit.SETTLE_S` "Fix young limit" 0-300 default 120; `core/SettleMigration` + test, run from `Settings.prefs()` on the first read (service or Settings screen).
- e3b8284 points.csv `fix_start,float_spell_s,suspect_reason,fix_young` after `void_reason` (29 -> 33; older rows unknown); TiltSet row combines leans (any young, every reason; per-lean OBS_HEADER unchanged); PoleCheck skips `fix_young` = 1 earlier refs (voided skip and #23 note kept). RefsStore/VoidRule header upgrade covers the new header with no code change.
- 507acc8 Survey: no refusal while young (MARK, leans, ref counting); amber `fix young: N s` notice and new `Feedback.Cue.YOUNG`; ref young if any counted epoch was, ref jump/suspect/reasons from every fixed epoch; RefView `young` replaces `settling` (MapView ring `(fix young)`), tilt banner `fix young N s`; chip `FIX young Ns` / `FIXED? after float`.
- 7021a71 soundings.py settle_s 90 -> 120 (and `sigma_terms` default), `SoundingSigma.SETTLE_S` 120, `fix_age_past_settle` vector to 150 s plus a 90 s case inside the window, vectors regenerated; `fix_stretches` float-spell rule (`DEFAULTS["float_spell_s"]` 20, `config.example.toml`), flag `float_spell` in EXCLUDING, cleared by `verify_suspect` (both flags become `suspect_verified`) and PPK replace; parity and verify tests.
- 995a6cc import: captures carry the four columns (`LOGGER_11`), SCHEMA_VERSION 5, SCHEMA_APP 5 -> 0.11; `logger_session.read_points` and `REFS_COLUMNS`; import test old vs new.
- e9f8177 0.11.0 / versionCode 15; README (guard section, settings, chip, cues, points.csv table/example, refs, overlay budget), docs/log.md, docs/howto/ice-cube.md.

### Tests
- `python3 -m unittest discover -s analysis/qgis/tests`: 162 OK (5 skipped).
- `python3 -m unittest discover icecube/tests`: 102 OK.
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 228 tests, 0 failures. APK `logger/app/build/outputs/apk/debug/app-debug.apk`, 0.11.0 (build 15). Not installed.

### Private replay (counts only, python fix_stretches, 4 sessions of 2026-27)
Stretches 26/17/1/11. New `float_spell` reason on 9/1/1/0 stretches (alone on 3/1/0/0). Jump unknown at 1/1/0/2 starts. Captures with fix age 90-120 s (newly young, newly inside the settle window): 1/25/21/7 of 30/713/275/713.

### Deferred / notes
- Live-notice texts for the young ref start are unchanged (young is reported at ref end).
- The chip and notices show the young fix's age, not the time left.
- Follow-ups stay with #39, #40, #41, #23.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 18:09 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-15 at `2a4cdad`
**Base**: main
**Depth**: Deep (reason: 35 files, about 1600 lines, cross-layer Java logger and Python processing with a schema change)
**Must-fix**: 1 | **Suggestions**: 11
**Round**: 1 | **Ship**: continue: round 1, 1 must-fix; the first round always re-reviews after fixes

Outside reviewers: Claude adversarial subagent, gemini (complete), codex (complete). Verified: analysis/qgis tests 162 OK (5 skipped), icecube tests 102 OK, regenerated coverage vectors are byte-identical to the committed file. Gradle not re-run here (the implementation entry reports 228 JVM tests passing). No Python or Java linter is installed or configured, so static analysis was not run. Java/Python parity of the float-spell rule and the jump source (fixed-to-fixed jumps still measured and suspect), parsing of 17/27/29/33-column rows, the refs.csv header upgrade, PoleCheck's young skip (old rows usable), importer schema 5, and the coverage overlay (suspect float-spell captures stay red, Coverage.java:585) all check out.

### Findings
- [x] (must-fix) Settle migration race: `settleChecked` is set before `migrateSettle` runs, without synchronisation, so a concurrent first read can see the old "10". Make `prefs()` synchronized, or set the flag after the migration (gemini, codex, Claude) — `logger/app/src/main/java/rink/icecube/logger/Settings.java:68-73`
- [x] (suggestion) Parity: an epoch with no position inside a fix ends the Java stretch (young again for 120 s, prior "none", jump measured from the earlier fixed epoch), while Python `fix_stretches` keeps the same stretch. Pick one rule and pin it with a parity test (Claude, codex) — `logger/app/src/main/java/rink/icecube/logger/core/FixGuard.java:108-139`, `analysis/qgis/soundings.py:401-408`
- [x] (suggestion) One single, DGPS or no-position epoch between a long float spell and the fix bypasses the float-spell rule in both Java and Python: the 11-21 case with a one-epoch dropout goes unflagged. Consider letting a spell carry over a short interruption of 2 s or less, or defer to #41 (Claude) — `FixGuard.java:111-118`, `soundings.py:388-389`
- [x] (suggestion) A ref that is both young and suspect plays YOUNG at ref end, then SUSPECT from the pole check. Elsewhere SUSPECT wins over YOUNG (gemini) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:247`
- [x] (suggestion) The young-ref notice shows the last epoch's age, so a ref that crosses 120 s reads "fix young: 135 s". Show the youngest age or leave the seconds out (gemini) — `Survey.java:299-301`
- [x] (suggestion) `refView()` uses `guard.young(now)`, not the `refYoung` latch, so "(fix young)" drops off the ring even though the saved ref will be young (gemini) — `Survey.java:715`
- [x] (suggestion) `mark()`/`lean()` test young at `now` but record `fix_age_s` at `a.lastNs`, so a row at the boundary can have `fix_age_s` < limit with `fix_young` 0. Use `a.lastNs` (gemini) — `Survey.java:330,375`
- [x] (suggestion) When a logger source has no `session_paths`, the logger's own `fix_suspect`/`suspect_reason` are ignored and float-spell captures get use = 1. This predates the change, but the change widens it; fall back to the capture's columns (codex) — `analysis/qgis/soundings.py:782`
- [x] (suggestion) README version example still reads "0.8.1 (build 12)"; plan step 6 said to update it (gemini) — `logger/README.md:27`
- [x] (suggestion) Stale docstring "(SCHEMA_APP: 0.6 for 3, 0.9 for 4)" is missing "0.11 for 5" (Claude) — `analysis/qgis/import_sessions.py:81`
- [x] (suggestion) Python treats a negative `float_spell_s` as on (Java needs > 0), and the build log says "float spell >= 0 s" when the rule is off (gemini) — `analysis/qgis/soundings.py:393,1382`
- [x] (suggestion) Owner call: "young if any counted epoch was young" makes a ref started at fix age 100 s permanently unusable as a pole-check reference. This follows the plan's decision 3 and is documented in the README. Confirm it, or decide by the age at the end or the share of young epochs (Claude) — `Survey.java:217`, `PoleCheck.java` (deferred: host decision: keep 'young if any counted epoch was young' (plan decision 3); listed for the owner's veto)

## Implementation
**Status**: complete
**When**: 2026-11-27 18:17 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**Branch**: feature/issue-15 at `57cf63e`
**Addressed**: Local Review (Pre-Push) at `2a4cdad` (2026-11-27 18:09 -05:00)
**Commits**: 64ed440, be59995, 36ceb54, 462dd77, 2768ddb, 1038b6c, 14000b5, cc386eb, 63cfd89, 04c27d3, 57cf63e

### Actions
- [x] (must-fix) Settle migration race: migration runs under a lock, flag set after it — `logger/app/src/main/java/rink/icecube/logger/Settings.java:68-80`
- [x] Parity of dropouts inside a fix (host-decided, owner may veto): a no-position, single or DGPS dropout of 2 s or less keeps the stretch in Java and Python, a longer one, a float epoch or an over-limit step ends it; pinned by mirrored tests (FixGuardTest.shortDropoutInsideAFixKeepsTheStretch, test_soundings test_dropout_parity_with_logger_fixguard) — `FixGuard.java`, `analysis/qgis/soundings.py` fix_stretches
- [x] Float spell carries over a dropout of 2 s or less (host-decided, owner may veto), both sides, tested with the 11-21 shape plus a one-epoch no-position/single/DGPS dropout; a mid-spell dropout of 2 s or less also no longer restarts the spell — same files
- [x] Suspect and young ref plays one cue (suspect wins) — `Survey.java` ref end
- [x] Young-ref notice reads "fix young during the ref" (no stale seconds); any row with age not under the limit drops the seconds — `Survey.java` youngText
- [x] refView respects the refYoung latch (refYoung || young now) — `Survey.java` refView
- [x] mark()/lean() test young at a.lastNs, where fix_age_s is taken — `Survey.java` mark, lean
- [x] No-raw path uses the capture's own fix_suspect/suspect_reason (float-spell capture gets use 0; test) — `analysis/qgis/soundings.py` capture_suspect_flags
- [x] README version example 0.11.0 (build 15) — `logger/README.md:27`
- [x] import_sessions docstring names 0.11 for schema 5 — `analysis/qgis/import_sessions.py:81`
- [x] Negative float_spell_s is off; the build log says "float spell rule off" (tests) — `analysis/qgis/soundings.py`, `config.example.toml`
- [x] Young-ref rule — `Survey.java:217` (deferred: host decision, keep "young if any counted epoch was young"; listed for the owner's veto)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 165 tests, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 102 tests, OK
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD SUCCESSFUL, 230 tests, 0 failures; APK logger/app/build/outputs/apk/debug/app-debug.apk (0.11.0 build 15). C: had 110 GB free.
- coverage_vectors.json unchanged (no sigma change).

### Notes
- The jump at a new stretch after a short single/DGPS dropout is still measured only from a float or fixed previous epoch, so it stays unknown there; only the float-spell rule carries over. A candidate for #41 if the owner wants the jump to carry over too.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 18:23 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**Branch**: feature/issue-15 at `c73ab09`
**Base**: main
**Depth**: Deep (reason: whole-branch diff, 35 files, about 1600 lines, cross-layer Java logger and Python processing with a schema change; round 2 classified on the whole diff)
**Must-fix**: 0 | **Suggestions**: 6
**Round**: 2 | **Ship**: recommended — no must-fix findings; remaining suggestions can be applied or tracked

Outside reviewers: Claude adversarial subagent, gemini (complete), codex (complete). Verified: analysis/qgis tests 165 OK (5 skipped), icecube tests 102 OK, `./gradlew testDebugUnitTest --max-workers 4` 230 tests 0 failures (C: 110 GB free). No Python or Java linter is configured, so static analysis was not run. All round-1 items checked as addressed: the settle migration lock, the 2 s dropout rule and spell carry-over (Java and Python agree rule by rule, mirrored tests), the cue, notice, refView and mark/lean fixes, the no-raw fallback, the docs. Not counted as findings: gemini's "jump lost after a short single/DGPS dropout between float and fix" (both sides) is the host decision of round 1 (jump unknown there, only the spell carries over), already listed for the owner's veto; gemini's COVERAGE_FIRST worry is a false positive (found by key); codex's "keep the logger's flag when raw epochs are present" is dropped (the raw rebuild reads the same epochs the logger saw, and verify_suspect/PPK must be able to clear it).

### Findings
- [x] (suggestion) The 2 s tests use different clocks: Java phone arrival time (`Epoch.elapsedNs`), Python GGA time; a whole-epoch dropout now lands right on "2 s or less", so the two can disagree at the boundary. Use `Epoch.tod` in FixGuard, or a half-epoch tolerance on both sides (Claude) — `logger/app/src/main/java/rink/icecube/logger/core/FixGuard.java:107-109`, `analysis/qgis/soundings.py:379-382`
- [x] (suggestion) The sigma table still reads "8 cm x (1 - t/90 s)^2 ... within 90 s are dropped"; the default is now 120 s (Claude) — `docs/howto/ice-cube.md:382`
- [x] (suggestion) With a station filter, a fixed epoch from another station is a dropout in Python (not in Java): an A-B-A switch within 2 s continues the stretch, a float spell carries over it, and the next start jump can be measured from the other station's fix. End the stretch on it, or note it in the docstring (Claude, gemini, codex) — `analysis/qgis/soundings.py:376-416`
- [ ] (suggestion) Captures without raw epochs take the logger's suspect flags but not its `fix_age_s`/`fix_young`, so a young capture gets no settle sigma or flag; fall back to the capture's columns (Claude) — `analysis/qgis/soundings.py:691,816`
- [ ] (suggestion) `capture_suspect_flags`: `fix_suspect` 1 with an unknown non-empty reason (a future one) adds no flag and the capture stays usable; add a generic suspect flag when no known reason matched (gemini) — `analysis/qgis/soundings.py:670-680`
- [ ] (suggestion) The ref's young/suspect latches are set before `RefOccupation.onEpoch` decides whether to count the epoch; an epoch stamped before the ref start (or before the last counted one) can latch young without being counted. Latch only for counted epochs (codex) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:210-218`

## Checkpoint
**Status**: complete
**When**: 2026-11-27 18:24 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push approved with suggestions; one host decision reversed for ice-survey safety; orchestrator table allows address)
**After**: publish
**Decision**: address

Host reverses its round-1 decision on the start jump (owner may veto): the jump also carries over a dropout of 2 s or less (no position, single or DGPS), measured from the last float or fixed epoch before the dropout, in Java and Python alike, with parity tests (the 11-21 shape with a one-epoch single dropout and a float spell under 20 s must be flagged by the jump). Also fix the six suggestions of the Local Review at 551d5fb: one clock for the 2 s test (state which, same on both sides); docs/howto/ice-cube.md sigma table 120 s; a fixed epoch from another station breaks the stretch in Python's station filter; no-raw captures take the logger's fix_age_s/fix_young for the settle sigma; an unknown suspect_reason with fix_suspect=1 counts as suspect; ref young/suspect flags set only for epochs RefOccupation counts.
