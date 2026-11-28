---
issue: 23
---

# Issue #23 — Logger: pole check against a robust reference, with placement and steadiness cues

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)

**Issue**: #23

### Scope Assessment

**Well-scoped?** Not yet as one PR. The issue now carries four things: the robust reference, the placement and steadiness cues, the "validated by a known point" mark moved here from #15, and (per the owner's comment) board-top session check points tied to stable refs. Suggest two PRs: (A) robust reference plus placement and steadiness cues, plus the shared reference rule; (B) the validated mark and board-top checks, built on A.
**Right repo?** Yes. All of it is in `logger/` (pole check, `refs.csv`) and, for the validated mark, possibly the `icecube/` reader.
**Serves**: none written down for the project (no goals document found in the checkout); the practical goal is trustworthy survey checks before the winter ice survey. Design section: none.
**Design picture?** No change to a design document (the project has none). The shared reference rule is a design decision and should be written once, in `logger/README.md` "Refs and the pole check", so #15 and #23 do not describe it twice.
**Dependencies**: #15 (in implementation on `feature/issue-15`): `PoleCheck.matches` will skip young refs (`fix_young`), `refs.csv` and `points.csv` gain columns, settle moves to 120 s. #23 implementation must start after #15 merges and extend those rows, not a parallel schema change. #27 (merged): voided refs are already skipped by `PoleCheck.matches`; the robust reference must keep skipping them.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Small steps; step back when they stop converging | Action needed | Split into PR A and PR B as above; the issue's "To decide in the plan" list has eight items and the board-top idea adds a ninth axis. |
| A change includes its consequences | Watch | README "Refs and the pole check" text (compares to "most recent" and "mean of all") and `PoleCheckTest` become untrue; update in the same PR. Older `refs.csv` rows must still read (issue already says so). |
| Keep one current design | Watch | One reference rule shared by the fix-stretch "validated" mark and the ref check; define it once. |
| Only what's needed | Watch | Board-top checks need a named problem: frost heave means a board top is not a fixed point, so it needs its own history, not the ref rule. Keep it out of PR A. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no ADRs or principles file; nothing triggered. |

### Consequences

- `logger/README.md` pole check section, settings text, and `PoleCheckTest` change with the reference rule.
- `refs.csv`/`points.csv` columns added by #15 are the base; any new column (for example a validated flag) must follow the same header-upgrade path and stay readable by `analysis/` and `icecube/` readers.
- Tuning needs the private 2026-11-11, -15 and -21 sessions; only relative numbers may go in the public repo.

### Recommendations

- Start implementation only after #15 merges; plan against its final `PoleCheck` and column set.
- Split into PR A (median or passing-visit reference, difference to last visit kept as information, placement cue about 2 cm, steadiness cue about 1.5 cm, tolerance applied to the robust reference) and PR B (validated mark for fix stretches, board-top check points).
- Decide in the plan how a robust reference behaves with 1 and 2 earlier visits (median of two is the mean; a bad first visit has no defence), and skip voided and young refs when forming it.
- Treat board tops as their own mark class compared against their own robust history, with a drift cue against the stable refs, never as references for the ref check.
- Write the reference rule once in the README and have #15's follow-up text point to it.

### Actions
- [ ] Split into PR A and PR B as described under Scope Assessment; the issue's decision list has eight items plus board tops.
- [ ] Start implementation only after #15 merges; plan against its final `PoleCheck` and column set.
- [ ] Split into PR A (median or passing-visit reference, difference to last visit kept as information, placement cue about 2 cm, steadiness cue about 1.5 cm, tolerance applied to the robust reference) and PR B (validated mark for fix stretches, board-top check points).
- [ ] Decide in the plan how a robust reference behaves with 1 and 2 earlier visits (median of two is the mean; a bad first visit has no defence), and skip voided and young refs when forming it.
- [ ] Treat board tops as their own mark class compared against their own robust history, with a drift cue against the stable refs, never as references for the ref check.
- [ ] Write the reference rule once in the README and have #15's follow-up text point to it.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism; owner asked to orchestrate in parallel while he is busy: "Orchestrate the issues while I'm busy and run them in parallel if practical")
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): two PRs on this issue: PR A robust reference + placement and steadiness cues (extends #15's PoleCheck and columns; implementation after #15 merges); PR B the "validated by a known point" mark and board-top check points. Board tops: an existing ref mark gets a kind, chosen when the mark is first shot in Ref mode ("fixed ref" default, or "check point (may move)"), stored with the mark; check points get their own robust history and a drift cue against the fixed refs and are never references for fixed refs. Few earlier visits: plan states the rule (1 visit: compare with it, flagged as a single-visit reference; 2: mean, with a disagreement cue if they differ by more than the tolerance; 3+: median). Shared reference rule written once in logger/README.md.

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:54 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)
**Plan**: `.agent/work-plans/issue-23/plan.md` at `5928531`

Two PRs. PR A (detailed): reference = median of earlier non-voided, non-young visits (1 visit single, 2 mean with disagreement flag, 3+ median), tolerance applied to it, last-visit difference kept, placement (2 cm) and steadiness (1.5 cm) cues as settings, one reference rule in the README, no new row columns. PR B (outline): validated mark and check-point kind chosen in Ref mode. Implementation waits for #15 to merge.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #23 — Logger: pole check against a robust reference, with placement and steadiness cues
**Plan**: `.agent/work-plans/issue-23/plan.md` at `5928531`
**Branch**: `feature/issue-23`

Reviewed against main (`b208c31`: `PoleCheck.java`, `Survey.poleCheck`, `Limit`, `TiltSet`, README "Refs and the pole check") and #15's plan (read-only). Host decisions on the planner's open questions (owner may veto): 1 amber when two earlier visits disagree; 2 the 2 cm / 1.5 cm limits are settings, 0 = off; 3 a mark's kind is fixed at its first shot; 4 an earlier visit that failed its own check stays in the reference; 5 re-check #15's names after it merges.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Split into PR A (detailed) and PR B (outline) as the Issue Review asked; PR A is about 7 files plus tests, no schema change. |
| Issue alignment | Good | Covers the reference height (median; mean-of-passing rejected with a reason), last-visit difference kept, placement and steadiness cues, tolerance on the robust reference, tuning on the three sessions (relative numbers only), old refs.csv rows readable. The record and validated-mark items go to PR B. Serves: "Survey-readiness for cm-level ice accumulation: trustworthy pole checks before the winter ice survey. Project has no goals or design document: none." |
| File targeting | Good | Matches the code: `PoleCheck.check` returns null on a first visit and `Survey.poleCheck` builds the notice, so the separate `Cues` path in step 5 is needed for first-visit steadiness. Pole tolerance lives in `SurveyLimits`, the new limits as `Limit` instances is consistent with the other typed settings. |
| Consequences | Good | README section, `PoleCheckTest`, Settings list and notices covered; `matches` stays the single reference-set filter (voided from #27, young from #15). |
| Principle alignment | Good | Public-repo rule respected (synthetic tests, offline replay, relative numbers); one rule written once in the README; small steps. |
| ADR compliance | Good | Project has no ADRs; none triggered. |
| ROS conventions | N/A | No ROS code. |

### Findings

None block implementation; all can be settled while implementing.

1. **[Tests]** The "bad middle visit" test says the +7 cm visit "fails against a 3-visit reference". With visits 0, 0 before it, the +7 visit is checked against the PAIR (mean of two agreeing visits), not a median. Reword the case (+7 fails red against an agreeing pair) and add a separate median case with three earlier visits.
2. **[Issue alignment] Tilt refs.** A ref can come from a tilt capture (`finishTilt` runs `poleCheck` for a REF tilt row), and that row's `sdU` is the RMS of the per-lean scatters (`TiltSet` ~line 279), not the scatter over a held occupation. Say in the plan and README whether the steadiness cue applies to occupation refs only (`method == OCCUPATION`) or also to tilt refs; tilt already has its own lean-scatter gate.
3. **[Consequences] Notice and alarm details.** Settle and write down in the README: for a doubtful-pair failure, which cue plays (not `POLE_ALARM`) and whether `poleWarning` (the persistent warnings bar) is set; how a passing single-visit check shows ("amber-leaning info" is ambiguous: green with the "single-visit reference" text, or amber); and the order when a cue or doubtful reference meets #15's young or suspect amber on the same notice (one amber notice listing each reason).
4. **[Issue alignment] Even counts.** For 4 or more visits with an even count the median is the mean of the middle two; state it in the README rule so the docs and code agree.
5. **[Tuning] Expected outcome.** Step 8 says "72 and 127 warn". Under the new rule 127 should fail red and 72 is expected amber by the placement cue (its difference to the reference is likely within 3 cm). Write the expected colour per ref so the replay is a real check.
6. **[Plan hygiene]** The plan's Open Questions are now host decisions (above). Record them as a Decisions section in the plan when implementation starts, as #15's plan does, and re-check `fixYoung`/`fix_young` and the column count against #15's merged code.

### Summary

The plan is ready for PR A once #15 merges. The reference rule, cues and settings fit the current code; the findings are small details to settle and document during implementation.

### Recommended Actions

- [ ] Reword the bad-middle-visit test (PAIR, not median) and add a 3-visit median case.
- [ ] Decide whether the steadiness cue applies to tilt refs; document it.
- [ ] Specify cue, warnings bar and colour precedence for the doubtful-pair, single-visit and young/suspect combinations in the README.
- [ ] State the even-count median in the README rule.
- [ ] Give the expected colour per ref (72, 127, 128, 329) for the step 8 replay.
- [ ] Move the Open Questions to a Decisions section and re-check #15's merged names before step 1.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implementation (PR A) held until #15 merges; then rebase on main and re-check #15's names and column count. Settle during implementation the Plan Review at bc9125d's items, with these host decisions (owner may veto): the steadiness cue covers held (occupation) refs only, not tilt refs; a doubtful two-visit failure plays the existing warning cue (not the alarm) and does not set the persistent warnings bar; a pass against a single earlier visit is green with "1 earlier visit" in the text; #15's young/suspect amber notice and the pole-check result appear together, the more severe colour wins; even-count median = mean of the middle two, written in the README; replay expectations 127 red, 72 amber (placement), 128 and 329 not failing; fix the mislabelled test and add a three-visit median test; Open Questions become a Decisions section.

## Implementation
**Status**: complete
**When**: 2026-11-27 19:17 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Branch**: feature/issue-23 at aa2c57d

PR A of the plan: robust reference, placement and steadiness cues. PR B not started.

### Commits
- `19fb20c` plan: Open Questions become a Decisions section (host decisions from the Plan Review and Checkpoint, plus the event-line finding)
- `4b0e5c8` `PoleCheck`: `RefKind` SINGLE/PAIR/MEDIAN, `reference`, `diffRef`, `pairSpread`, `doubtful`, `failed()`, `placeOffset`, `median()` (even count = mean of middle two), `Cues` (off-centre, pole moved for occupation refs only), `colour()`; `PoleCheckTest` rewritten (bad-middle-visit as PAIR, three-visit median, outlier, even count, single, doubtful pair, placement, steadiness incl. first visit and tilt, red over cues, boundaries)
- `619a543` `Limit.PLACE_CM` (0-20, 2.0) and `Limit.STEADY_CM` (0-20, 1.5), in `Settings` LIMIT_KEYS/LIMITS (first in Survey), getters `placeM`/`steadyM`, Settings screen label and tolerance text; `LimitTest`
- `6c5661d` `Survey.poleCheck`: one notice with check text, cues and young/suspect text, the more severe colour wins; red = alarm + warnings bar; amber = `ERROR` buzz, no warnings bar; green with a check = `POLE_OK`
- `aa2c57d` README "Refs and the pole check" rewritten as the one reference rule (visits, reference, check, cues, notice colours, event line), settings list, feedback, notice line; version 0.12.0 (build 16); `docs/log.md` entry

### #15 names re-checked (main 42d5955)
`PoleCheck.matches` skips `voided` and `fixYoung == 1`, refs.csv/points.csv columns as #15's plan: no adaptation. `Feedback.Cue` has no separate warning cue, so "the existing warning cue" is `ERROR` (low buzz).

### Deviation from plan
- Event line: `analysis/qgis/logger_session.py` takes the text before `; diff_last_m=` as the message and `ok=false` as red. To keep the importer unchanged, `diff_last_m` stays the first key and `ok` now means "not a red fail" (a doubtful pair outside the tolerance logs `ok=true within_tol=false doubtful=true`); new keys `diff_ref_m ref_kind within_tol doubtful place_cm sd_u_cm colour` follow. The first-visit event carries no keys (the importer would put them in the message).

### Tests
`./gradlew testDebugUnitTest assembleDebug --max-workers 4`: all pass (34 test classes), APK built: `logger/app/build/outputs/apk/debug/app-debug.apk`, 0.12.0 (build 16). Not installed.

### Replay (private refs.csv, 41 refs over 11-11, -15, -21; counts and colours only)
- 127 red (expected red); 128 amber, off-centre (not failing, as expected); 329 green (not failing, as expected).
- 72 green, not amber as expected: within tolerance of the median, and its off-centre offset against earlier visits only is under 2 cm. The issue's 2.5 cm was against all visits of the mark (hindsight, including later ones), which the live check cannot see.
- Overall: 5 red (old rule 6), 7 amber, 29 green. On 11-21: red 4 -> 3, but two of the three are new reds (300 and 658, each just over 3 cm from a median that spans visits from three days; 658 by under 0.5 mm). The 11-15 reds (24, 25, single-visit) are unchanged.
- Amber: off-centre 6 (two on 11-15 refs 1 and 2 against a median of 11-11 visits), pole moved 2 (one first visit on 11-15). The 11-11 rows have no `method` column, so the steadiness cue did not fire on them in the replay (the app always sets method).

### For the host / owner
- The median across days ignores real ground motion between sessions (frost heave, settling); the old last-visit rule tracked it. The two new reds may be that. Possible follow-ups: limit visits to a time window or weight recent sessions; not in this PR.
- 72 amber would need an off-centre default of about 1.5 cm, which also makes about 6 more refs amber; default kept at 2 cm per the plan.
- QGIS still labels refs with `diff_last_m` (`build_project.py`); showing `diff_ref_m` is a processing-side follow-up.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 19:22 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-23 at `144b0de`
**Base**: main
**Depth**: Standard (reason: ~450 changed code/doc lines over 12 files, behaviour change in the pole check decision)
**Must-fix**: 1 | **Suggestions**: 7
**Round**: 1 | **Ship**: continue — round 1: 1 must-fix; first round always re-reviews after fixes

Verified: `./gradlew testDebugUnitTest --max-workers 4` passes; a 0.12.0 event line (cue text, `ok=true doubtful=true`, trailing keys) run through `logger_session.py`'s `_kv` and message split gives the expected message and keys; `COVERAGE_FIRST` is computed by `indexOf`, so the new keys do not shift the Coverage section; `Feedback` queues cues (no cut-off); test fixtures are synthetic (LAT 10, LON 20). README rule matches `PoleCheck` (median, even count, doubtful pair only for k = 2, 1e-9 boundary, voided and young skipped from both the height reference and the placement median).

Reviewers: Claude adversarial subagent (no must-fix), Gemini (5 suggestions, one verified as a non-issue), Codex (1 must-fix, 1 suggestion).

Open owner question (not a finding): the multi-day median can flag a ref after real ground movement between sessions (frost heave; the replay's two new 11-21 reds). Whether to window visits by time stays with the owner.

### Findings
- [x] (must-fix) Deduplicate visits by session/n before forming the reference: `RefsStore.readAll` returns one row per refs.csv copy, and the README expects the same ref in several storage roots, so a duplicated visit now changes the median (e.g. a doubtful pair 50.00/50.07 becomes a 3-visit median of 50.00 and a 50.07 shot fails red); also keeps `last` correct. Add a test — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:69`
- [x] (suggestion) QGIS still labels and stores only `diff_last_m`; import `diff_ref_m`/`colour` and label with `diff_ref_m` (here or a follow-up issue), else a passing ref after a bad visit reads like a failure and amber looks green — `analysis/qgis/logger_session.py:665`, `analysis/qgis/build_project.py:204`
- [x] (suggestion) Add an importer test fixture with a 0.12.0 `pole check` line (cue text, `ok=true doubtful=true`, trailing keys) to pin the compatibility the Survey comment relies on — `analysis/qgis/tests/synth.py:124`
- [x] (suggestion) First-visit / no-height event line drops `sd_u_cm` and `colour` although the README lists them as event keys; say so in the README or write keys the importer tolerates — `logger/app/src/main/java/rink/icecube/logger/Survey.java:559`
- [x] (suggestion) A suspect (or young tilt) ref with an amber pole check plays SUSPECT/YOUNG then ERROR back to back; README says a suspect ref plays only the suspect cue. Skip ERROR when a fix cue played, or document it — `logger/app/src/main/java/rink/icecube/logger/Survey.java:573`
- [x] (suggestion) On a red failure the persistent warnings bar omits the off-centre / pole-moved cue that may explain it; append `q.describe()` to `poleWarning` — `logger/app/src/main/java/rink/icecube/logger/Survey.java:579`
- [x] (suggestion) An even count of 4+ visits split 2/2 by more than the tolerance gives a midpoint reference with no doubt flag (a good shot fails red); consider extending `doubtful` to the middle two, or note it in the README rule — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:55`
- [x] (suggestion) Test gaps: both cues at once, placement median with 3+ visits and one off in east/north, doubtful pair plus a cue stays amber — `logger/app/src/test/java/rink/icecube/logger/core/PoleCheckTest.java`

## Implementation
**Status**: complete
**When**: 2026-11-27 19:31 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-23 at `e51bb54`
**Addressed**: Local Review (Pre-Push) at `144b0de` (2026-11-27 19:22 -05:00)
**Commits**: 75dd6cc, f596462, a1a819e, 0e57fa8, 0a2d4d5, 746edba, e51bb54

### Actions
- [x] (must-fix) Visits deduplicated by (session, n) and ordered by session then n, so a ref in several refs.csv copies is one visit and `last` is the most recent; tests for the 50.00/50.07 duplicate (stays a doubtful pair, amber) and for copy order — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java` (`matches`)
- [x] QGIS imports `diff_ref_m` and `colour` (importer schema 6, main is at 5; `SCHEMA_APP` 6: 0.12), labels refs with `coalesce(diff_ref_m, diff_last_m)` and draws amber rings — `analysis/qgis/logger_session.py`, `analysis/qgis/import_sessions.py`, `analysis/qgis/build_project.py`, `docs/howto/qgis.md`
- [x] synth fixture takes a `pole_check` line; test with a 0.12.0 line (cue text, `ok=true doubtful=true`, trailing keys) and a first-visit line — `analysis/qgis/tests/synth.py`, `analysis/qgis/tests/test_import.py`
- [x] Event line always carries `place_cm`, `sd_u_cm` and `colour` (empty value when n/a, was `none`); importer ends the message at the first `; key=` — `logger/app/src/main/java/rink/icecube/logger/Survey.java`, `logger/README.md`
- [x] A suspect or young ref with an amber check plays only its fix cue (no error buzz too) — `Survey.java`, README Feedback
- [x] Red warnings bar appends the cue text (`q.describe()`) — `Survey.java`, README
- [x] Doubtful generalised: even count, middle two over the tolerance; odd count 3+, no neighbour of the median within it; `pairSpread` renamed `spread`; tests for a 2/2 split and 0/+5/+10 — `PoleCheck.java`, README rule
- [x] Tests added: both cues at once, placement median with an east/north outlier, doubtful pair plus a cue stays amber — `PoleCheckTest.java`

### Checks
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD SUCCESSFUL, 249 tests, 0 failures (C: 109.4 GB free before)
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 172 tests, OK (skipped=5)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 19:35 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-23 at `a08f0c5`
**Base**: main
**Depth**: Standard (reason: whole-branch diff ~950 lines over 16 code/doc files, behaviour change in the pole check; fix round classified on the whole diff)
**Must-fix**: 1 | **Suggestions**: 4
**Round**: 2 | **Ship**: recommended — round 2: 1 mechanical must-fix (prev 1), not rising; fix and ship rather than another full round

Verified: `./gradlew testDebugUnitTest --max-workers 4` passes (C: 109.4 GB free); `python3 -m unittest discover -s analysis/qgis/tests` 172 OK (5 skipped); all round-1 items checked as addressed; `COVERAGE_FIRST` is `indexOf` (Gemini's Settings item is a non-issue); `VoidRule.liveRefs` reconciles void flags across copies before `matches`, and `readAll` runs before the new ref is appended (Gemini's dedup/self items are non-issues); `_kv` skips an empty `place_cm=` without swallowing the next key. No Python linter installed (ruff/flake8 absent): Python not linted. Merge with feature/issue-20 (`git merge-tree`): only `docs/log.md` conflicts (both append an entry; keep both); `logger_session.py` and `test_import.py` auto-merge.

Reviewers: Claude adversarial subagent (no must-fix, 4 suggestions), Gemini (3 suggestions, 2 verified non-issues), Codex (1 must-fix, confirmed).

Open owner question (not a finding): the multi-day median vs ground movement between sessions.

### Findings
- [x] (must-fix) Radius test inverted to `hypot > MATCH_RADIUS_M ... continue`, which admits a NaN distance (e.g. an `Infinity`/non-finite coordinate parsed from refs.csv; `hasPosition` only rejects NaN); the old `<=` test rejected it. Use `!(d <= MATCH_RADIUS_M)` or check `Double.isFinite` — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:94`
- [x] (suggestion) `check()` javadoc still says earlier refs come "oldest first ... the last match is the most recent visit"; `matches` now sorts, so input order no longer matters — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:117`
- [x] (suggestion) `matches` javadoc says rows without a session "keep their given order", but the sort still orders them by n and puts them before every sessioned row (so one can never be `last`); fix the wording or use a constant secondary key for them — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:80`
- [x] (suggestion) README says `place_cm`/`sd_u_cm` are "on every line", but the `pole check n=…: result not shown (voided)` line carries no keys (the importer then resets that ref's `colour`/`diff_ref_m` to None, harmless as a voided ref draws grey); narrow to check and no-check lines — `logger/README.md:553`
- [x] (suggestion) Placement cue is never computed when the new ref has no ground height (`check` returns null before positions are used), though earlier visits within 0.30 m could judge it; document or compute the offset apart from the height check — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:117`

## Implementation
**Status**: complete
**When**: 2026-11-27 19:38 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-23 at `585baad`
**Addressed**: Local Review (Pre-Push) at `a08f0c5` (2026-11-27 19:35 -05:00)
**Commits**: c930103, fee6386, 461dce0, 585baad

### Actions
- [x] (must-fix) Non-finite distance admitted: radius test is now `!(d <= MATCH_RADIUS_M)` and `PointRow.hasPosition` needs finite lat/lon; test `nonFiniteCoordinatesNeverMatch` (Infinity, -Infinity, NaN) — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:94`, `logger/app/src/main/java/rink/icecube/logger/core/PointRow.java:101`
- [x] (suggestion) `check()` javadoc now says any input order; `matches` sorts by session then n — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:117`
- [x] (suggestion) `matches` javadoc: rows without a session sort first by n, so one is last only when no match has a session — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:80`
- [x] (suggestion) README (and the Survey comment): `place_cm`/`sd_u_cm` on every check and no-check line; the voided line carries no keys — `logger/README.md:553`
- [x] (suggestion) Off-centre cue computed apart from the height check: new `PoleCheck.placeOffset` and `Cues.of(now, earlier, c, …)`, used by Survey; test `placementCueWithoutAGroundHeight`; README notes it — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:117`

### Checks
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD OK, 251 tests, 0 failures (PoleCheckTest 25); `app-debug.apk` built 19:37. C: had 109 GB free.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 19:39 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**Branch**: feature/issue-23 at `ed41169`
**Base**: main
**Depth**: Standard (reason: round 3 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 0
**Round**: 3 | **Ship**: recommended — no must-fix findings

Scope: round 2's findings (Local Review at `a08f0c5`, recorded in c826130) and the fix diff c826130..ed41169 (6 files, +116 -30). Cross-model: skipped (round-3 convergence check, per handoff).

Verified: all five round-2 items addressed as stated. Radius test is `!(d <= MATCH_RADIUS_M)`; `PointRow.hasPosition` now needs finite lat/lon (its other callers, FixGuard, EpochAverage, MapView, Coverage and Survey, only gain the stricter rejection of an infinite position, which is garbage for all of them). `placeOffset` guards a non-positioned `now`, and `matches` returns empty for it. `Cues.of(now, earlier, c, ...)` reuses the check's offset when there is one, and otherwise falls back to `matches` + `placeOffset`. Survey's no-ground-height notice and event line carry the new cue; `place_cm` was already parsed on no-check lines. The javadocs and README now match the sort and the voided line. `ANDROID_HOME=~/Android/Sdk ./gradlew testDebugUnitTest --max-workers 4`: OK, 0 failures (up to date with ed41169; PoleCheckTest 25 tests). C: 110 GB free.

### Findings
- [ ] No issues found. LGTM.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 19:39 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push round 3 approved; orchestrator table allows publish)
**After**: publish
**Decision**: publish

## Local Review
**Status**: complete
**When**: 2026-11-27 19:41 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**PR**: #43 at `b345296`
**Depth**: Deep (reason: cold read, cross-model only: codex, per handoff)
**Must-fix**: 0 | **Suggestions**: 1

Cross-model: codex completed (EXIT=0). 1187 changed lines, 19 files (Deep by size). Lead checked the finding against the code.

### Findings
- [ ] (suggestion) matches() rejects only a NaN ground height; PointRow.dbl() uses Double.parseDouble, which accepts "Infinity", so a corrupt refs.csv row could enter the reference (the writer never emits one); reject non-finite hGround as hasPosition now does — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:93`

## Integrated Review
**Status**: complete
**When**: 2026-11-27 19:42 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #43 at `10f661e`
**Sources**: 2 (Local Review (Pre-Push) @ `ed41169`, round 3, approved, 0 findings; Local Review @ `b345296` (Codex cold read, covers 10f661e via bookkeeping), approved, 1 suggestion). No hosted reviews (Copilot out of quota, not requested); no CI checks configured.
**Cross-source confirmations**: 0
**CI**: all-pass (no checks configured on this repository)

### Findings
- [x] (suggestion, Local Review @ `b345296`, covers `10f661e` via bookkeeping) `matches()` rejects only a NaN ground height of an earlier ref (`Double.isNaN(r.hGround)`); `PointRow.dbl()` uses `Double.parseDouble`, which accepts "Infinity", so a corrupt refs.csv row with an infinite hGround would enter the reference and make the median and diffs infinite. The writer never emits one, so only a corrupted or hand-edited file reaches it. Fix: use `!Double.isFinite(r.hGround)` at line 93, and the same guard for `now.hGround` in `check()` (line 122) for consistency with `hasPosition()` — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:93`

### False positives

## Checkpoint
**Status**: complete
**When**: 2026-11-27 19:42 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (one valid suggestion, a two-line fix; orchestrator table allows address)
**After**: findings
**Decision**: address

Reject a non-finite ground height in matches() (line 93) and in check() for the new ref (line 122), with a test.

## Implementation
**Status**: complete
**When**: 2026-11-27 19:43 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**PR**: #43 at `d2e0296`
**Addressed**: Integrated Review at `10f661e` (2026-11-27 19:42 -05:00)
**Commits**: d2e0296

### Actions
- [x] Non-finite ground height rejected: `!Double.isFinite(r.hGround)` in `matches()` and `!Double.isFinite(now.hGround)` in `check()`, javadoc updated, new test `infiniteGroundHeightIsNoHeight` — `logger/app/src/main/java/rink/icecube/logger/core/PoleCheck.java:93`

### Checks
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD SUCCESSFUL; 252 tests, 0 failures, 0 errors (PoleCheckTest 26/26). C: had 109 GB free.

## Local Review
**Status**: complete
**When**: 2026-11-27 19:44 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**PR**: #43 at `59f1602`
**Depth**: Deep (reason: convergence check per handoff, the newest Integrated Review's one finding and the fix diff c443065..59f1602)
**Must-fix**: 0 | **Suggestions**: 0

Cross-model: skipped (convergence check, per handoff). Lead checked the fix: `matches()` and `check()` now use `!Double.isFinite(...hGround)`; no other `isNaN(hGround)` remains in PoleCheck; `Cues` needs no ground height. New test `infiniteGroundHeightIsNoHeight` covers +Inf on the new ref, -Inf on an earlier ref, and that the good ref still forms the reference. Rerun at HEAD: PoleCheckTest 26/26 pass.

### Findings
- [ ] No issues found. LGTM.

## Integrated Review
**Status**: complete
**When**: 2026-11-27 19:45 -0500
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #43 at `885c288`
**Sources**: 1 (Local Review @ `59f1602`, covers `885c288` via bookkeeping; no hosted reviews, Copilot out of quota; no CI checks configured)
**Cross-source confirmations**: 0
**CI**: all-pass

### Findings
- [ ] No open findings. The convergence Local Review @ `59f1602` approved with 0 findings and confirms the fix (`d2e0296`) for the one suggestion from Local Review @ `b345296` (non-finite ground height), which the Integrated Review @ `10f661e` ruled on.

### False positives
