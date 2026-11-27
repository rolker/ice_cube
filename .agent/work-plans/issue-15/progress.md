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
