---
issue: 9
---

# Issue #9 — soundings.py: sigma rule for tilt captures

## Issue Review
**Status**: complete
**When**: 2026-11-26 18:26 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)

**Issue**: #9

### Scope Assessment

**Well-scoped?** Yes. One PR: read five `tilt_*` columns in the session reader (`soundings.py` captures query, around line 583), give the capture loop a tilt branch (around line 666). Hand-made tilt sets are out of scope (issue 26).
**Right repo?** Yes. `analysis/qgis/soundings.py` is the post-processing reference; the logger and `icecube/logger_vectors.py` already carry the rule.
**Serves**: the project's goal of ice growth reported from logger surveys (`docs/roadmap.md` Direction); no `docs/design.md` in this project.
**Design picture?** No change. The rule is specified in the issue and in `logger/README.md`; this brings the reference into line.
**Dependencies**: #6 (merged) defined the rule and the vectors. Issue 26 will follow and builds on the tilt columns being read.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Public repo, no private data | OK | Only generic column names and synthetic vector values. |
| Scripts take values from config | OK | No new constants; reuses `tip_sink` and `ground` from `[soundings.sigma]`. |
| Keep things consistent (post-processing and live view agree) | Watch | `sigma_terms` is mirrored in `SoundingSigma.java`. If its signature changes, the mirror, the docstring pointer and the vectors must follow. Prefer the caller passing `sd_u`, `sd_h` and `tilt_deg=0` so `sigma_terms` needs no change. |
| Missing data handled | Watch | Older (pre-0.6) logs and single captures have no `tilt_*` columns; the reader must tolerate absent columns and NULLs, falling back to the single-capture rule, as `logger_vectors.py` does. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no ADRs; no workspace ADR is triggered. |

### Consequences

- `icecube/logger_vectors.py` docstring (line 14) says soundings.py has no tilt rule; update it. A tilt vector case already exists (around line 225), so "add the case" is mostly done; verify it runs through the soundings path and covers missing sd values.
- `logger/README.md` (around lines 514-519) says soundings.py lacks the rule (issue 9); drop that caveat.
- The `soundings.py` module docstring (line 35) and `analysis/README.md` should describe the tilt-capture rule.
- Add a test for a tilt capture on the soundings side.

### Recommendations

- Keep `sigma_terms`'s signature unchanged: choose the tilt inputs in the capture loop (sd_u = `tilt_sd_u_m`, sd_h = hypot(`tilt_sd_e_m`, `tilt_sd_n_m`)/√2, tilt_deg 0) so it matches the Java mirror and the vectors with no regeneration needed.
- Make the reader tolerate databases or CSVs without the tilt columns (NULL when absent) and fall back to the single-capture rule when `method` is not tilt or the tilt sds are missing.
- Check the parity test against the existing tilt vector, including a case with missing `tilt_sd_*`, and update the docs that say the rule is absent.

### Actions
- [ ] Keep `sigma_terms`'s signature unchanged: choose the tilt inputs in the capture loop (sd_u = `tilt_sd_u_m`, sd_h = hypot(`tilt_sd_e_m`, `tilt_sd_n_m`)/√2, tilt_deg 0) so it matches the Java mirror and the vectors with no regeneration needed.
- [ ] Make the reader tolerate databases or CSVs without the tilt columns (NULL when absent) and fall back to the single-capture rule when `method` is not tilt or the tilt sds are missing.
- [ ] Check the parity test against the existing tilt vector, including a case with missing `tilt_sd_*`, and update the docs that say the rule is absent.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:26 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (actions are plan inputs within the issue's scope; orchestrator table allows proceed)
**After**: issue-actions
**Decision**: proceed

Actions go to plan-task: tilt rule applied in the capture loop (sd_u = tilt_sd_u_m, sd_h = hypot(tilt_sd_e_m, tilt_sd_n_m)/sqrt(2), tilt 0) without changing sigma_terms' signature; tolerate missing/NULL tilt columns (fall back to the single rule); check the existing tilt parity vector against the soundings path; fix the docs that say the rule is absent. Issue 26 stays out of scope.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:27 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)
**Plan**: `.agent/work-plans/issue-9/plan.md` at `197feb7`

Single PR: make `read_points` and the captures gpkg table carry method, fix_suspect and tilt_* columns (NULL for older logs), add a shared rule helper in soundings.py (tilt sds as sd_u/sd_h with tilt 0, else single rule; sigma_terms unchanged) used by the capture loop and logger_vectors, add a missing-sd vector and tests, and fix the docs saying the rule is absent.

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:29 -0500
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: needs-work

**Issue**: #9 — soundings.py: sigma rule for tilt captures
**Plan**: `.agent/work-plans/issue-9/plan.md` at `197feb7`
**Branch**: `feature/issue-9`

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | One PR: reader, captures schema, one helper, capture loop, vectors, tests, docs. Adding the Java fix (finding 1) keeps it one PR. |
| Issue alignment | Good | Covers the issue and the review-issue recommendations: `sigma_terms` unchanged, the reader tolerates missing columns, a missing-sd vector, docs updated. |
| File targeting | Needs work | `logger/app/src/main/java/rink/icecube/logger/core/SoundingSigma.java` is missing (finding 1). The vectors file is `logger/app/src/test/resources/coverage_vectors.json`. |
| Consequences | Needs work | The row "Java untouched; sigma_terms unchanged" is wrong once the fallback is added (finding 1). The row "re-import fills them" is wrong for a plain re-run (finding 2). |
| Principle alignment | Needs work | "Post-processing and live view agree" breaks for a tilt capture with a missing sd unless Java changes too. "Missing data handled" is silent for gpkgs imported before this change (finding 2). Serves: "Ice growth reported from logger surveys (`docs/roadmap.md` Direction); project has no design document." This is correct. |
| ADR compliance | Good | The project has no ADRs and no workspace ADR is triggered. |
| ROS conventions | N/A | Not a ROS project. |

### Findings

1. **[Consequences / open question 1]** Java does not fall back. `SoundingSigma.of` picks the tilt rule whenever `method` is `tilt`, and NaN sds become 0. A tilt capture with a missing sd therefore gets sd_u at the floor, sd_h at the floor, tilt 0 and only the tip sink, which is an optimistic sigma. The current `logger_vectors.logger_sigma` does the same, because `sigma_terms` treats None as 0. So Java and the vectors agree today, but neither matches the plan's fallback.

   With the plan as written, the new missing-sd vector would make `SoundingSigmaTest` fail on the regenerated `coverage_vectors.json` (it builds a PointRow with NaN tilt sds from the vector). Align Java in this PR rather than in a follow-up:
   - In `of`, require `TILT.equals(r.method)` and all three of tiltSdE/N/U to be non-NaN.
   - Rewrite the class comment's "soundings.py does not have that rule yet (issue #9)" and state the fallback there.
   - Add the file to the Files table.
   - Run the logger unit tests (`cd logger && ./gradlew testDebugUnitTest --max-workers 4`, or the project's usual test task) after regenerating.

   Put the case in `SIGMA_CASES` (a `tilt_missing_sd` row), which the Java test reads. The coverage chain is not where it is read.
2. **[Consequences / open question 2]** "Re-import" does not happen on its own. `import_sessions` skips a session whose source signature is unchanged unless `--force` is given. `ensure` will ALTER the new columns into an old gpkg, but they stay NULL. After that the plan's `PRAGMA table_info` check finds the columns present, and tilt captures silently keep the single rule.

   A silent fallback is not acceptable here. Make the importer re-read sessions when the captures schema gains columns, for example by bumping `SCHEMA_VERSION` and treating a stored version older than it as `--force` for sessions. Add a test for that. Also have `logger_source` log one warning when tilt columns are absent, or are NULL for captures of sessions whose `sessions.app` is 0.6 or later, naming the re-import. Update the Consequences row to match.
3. **[Approach, minor]** The plan does not say whether `gst_u`/`gst_h` still floor a tilt capture's rtk/h in `sigma_terms`. Keeping them is reasonable and matches Java's documented "no GST" difference, but state it. The end-to-end test asserting "sigma_rtk uses tilt_sd_u" must then use rows with no raw GST epochs, or allow max(tilt_sd_u, gst_u).

### Summary

The approach is sound: one shared selection helper, `sigma_terms` unchanged, and tolerant reads. Two consequences are wrong as written. The Java mirror does not fall back on missing tilt sds, so the new vector would break its test unless Java changes in this PR. A plain re-run of the importer leaves old gpkgs with NULL tilt columns and a silent single rule. Both fixes are small.

### Recommended Actions

- [ ] Add `SoundingSigma.java` to the plan: fall back to the single rule when any tilt sd is NaN, fix its class comment, and run the logger unit tests after regenerating the vectors.
- [ ] Put `tilt_missing_sd` in `SIGMA_CASES`.
- [ ] Make the importer refill the new columns on old gpkgs without `--force` (schema version bump forces a re-read), with a test. Have `logger_source` warn when the tilt columns are absent or NULL for 0.6+ sessions.
- [ ] State whether GST still floors tilt captures and shape the end-to-end test to match.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:29 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work; orchestrator table allows revise)
**After**: plan
**Decision**: revise

Address the review's must-fix 1-3 (Java SoundingSigma falls back to the single rule when any tilt sd is missing, in this PR, with its comment fixed and the logger unit tests run on regenerated vectors; tilt_missing_sd in SIGMA_CASES; importer refills the new columns on old gpkgs without --force via a SCHEMA_VERSION bump + test, and logger_source warns once when tilt columns are absent/empty for >=0.6 sessions). Host decision on the minor: GST stays a floor for tilt captures (sigma = the larger of the GST term and the tilt sd), and the end-to-end test uses rows without GST or asserts the larger.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:30 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-9/plan.md` at `4eff77d`

Revision after plan review: Java SoundingSigma falls back to the single rule when any tilt sd is missing (in this PR, comment fixed, vectors regenerated, logger unit tests run); tilt_missing_sd goes in SIGMA_CASES; SCHEMA_VERSION bump makes the importer re-read old sessions without --force (with a test) and logger_source warns once when tilt columns are absent or empty for 0.6+ sessions; GST stays a floor for tilt captures and the end-to-end test is shaped to match.

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:33 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #9 — soundings.py: sigma rule for tilt captures
**Plan**: `.agent/work-plans/issue-9/plan.md` at `4eff77d`
**Branch**: `feature/issue-9`

Second review, after the revision that answered the first review's needs-work.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Still one PR. The Java change is small: a guard in `SoundingSigma.of` and a rewritten comment. |
| Issue alignment | Good | Covers the issue and all review-issue actions. All three earlier findings are addressed: Java falls back (step 5), `tilt_missing_sd` is in `SIGMA_CASES` (step 6), and `SCHEMA_VERSION` 3 with a re-read test plus a `logger_source` warning (steps 2, 4, 7). The host's GST-floor decision is stated, and the end-to-end test is shaped to match. |
| File targeting | Good | `SoundingSigma.java` and `coverage_vectors.json` are now listed. `SoundingSigmaTest` needs no edit because it reads `SIGMA_CASES`. |
| Consequences | Good | The table now matches the approach: Java changed, `sigma_terms` unchanged, old gpkgs refilled by the version bump. Two edge cases are in Findings. |
| Principle alignment | Good | Post-processing and the live view agree through one helper and the same vectors. Missing data falls back to the single rule with a warning. Public repo: only synthetic values. Serves: "Ice growth reported from logger surveys (`docs/roadmap.md` Direction); project has no design document." Checked and correct. |
| ADR compliance | Good | The project has no ADRs, and no workspace ADR is triggered. |
| ROS conventions | N/A | Not a ROS project. |

### Findings

1. **[Consequences, fix during implementation]** The schema-upgrade re-read must not bypass the snapshot guard. Step 2 says to treat an older stored `schema_version` "as `--force` for sessions (skip test at line ~536)". The `if old and not args.force` block holds two guards: the signature check, and "snapshot of a session already imported in full, skipped" (lines 542-546).

   If both guards are skipped, a plain re-run of an upgrade can replace a full import with a snapshot. This happens when the input folder now holds only a snapshot of a session that was earlier imported in full. With an explicit `--force`, the user chose that. On an implicit upgrade, they did not.

   Bypass only the signature check when upgrading, and keep the snapshot guard. Cover it in the step 7 schema-2 test, or in a second small case.
2. **[Approach, minor]** In step 4, the "`sessions.app` is 0.6 or later" check needs a numeric version comparison, not a string compare. Parse `app` as a tuple of ints. A NULL or unparseable value means "not 0.6+", because 0.2 to 0.5 files exist and have no app string in the tests' sense. Example: "0.10" sorts before "0.6" as a string.
3. **[Approach, note]** Keep the helper's sd_h None-as-0 behaviour. The existing `missing_sd` vector (single rule, all sds None) depends on `_sd_h` treating None as 0. The current `logger_source` handles the single rule the same way, so this is a regression check, not a change.

### Summary

The revision fixes all three findings from the first review, and the plan is ready for implementation. One new edge case needs care: the implicit schema-upgrade re-read must keep the snapshot guard. Two small implementation notes are also listed. None of them needs another plan revision.

### Recommended Actions

- [ ] On a schema-version upgrade, skip only the unchanged-signature check, keep the "snapshot of a session already imported in full" guard, and test it.
- [ ] Compare `sessions.app` versions numerically for the 0.6+ warning. NULL or unparseable means older.
- [ ] Keep None-as-0 in the helper's sd_h so the `missing_sd` vector is unchanged.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:31 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready, no must-fix; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

The review's three implementation notes go into the implement handoff: keep the snapshot guard on the schema-upgrade re-read (skip only the unchanged-signature check), with a test; compare app versions numerically (missing/unreadable = older); the helper treats a missing sd as 0 like today so the existing missing_sd vector is unchanged.

## Implementation
**Status**: complete
**When**: 2026-11-26 18:37 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Branch**: feature/issue-9 at `4f95ee2`

- `116f7f3` import: `logger_session.read_points` returns the 0.6 columns (method, fix_suspect, fix_age_s, fix_jump_m, tilt_n/rms/cover, tilt_sd_e/n/u; None for older files). `import_sessions.py`: captures gain them (`LOGGER_06`), `SCHEMA_VERSION` 3; a gpkg with an older stored schema has every session re-read once without `--force`, skipping only the unchanged-signature check, so the snapshot guard still holds. Tests: v0.6 columns / v0.5 NULLs, schema-2 re-read without `--force` (user label kept, once only), schema-2 upgrade still refuses a snapshot over a full import. `synth.py` gains `POINTS_HDR_06`, `captures_v06`, `session_v06`.
- `70d0f76` soundings: `capture_sigma_inputs(sg, method, sd_u, sd_e, sd_n, tilt_sd_u, tilt_sd_e, tilt_sd_n)` -> (sd_u, sd_h, tilt); tilt rule only with method tilt and all three tilt sds, else the single rule; sd_h treats a missing sd as 0 (existing `missing_sd` vector unchanged). `sigma_terms` unchanged, so GST still floors. `logger_source` selects the tilt columns via `PRAGMA table_info` (NULL when absent) and logs one warning naming `--force` when they are absent or all NULL for non-photo captures of sessions whose app version is >= 0.6 (numeric compare; missing/unreadable app = older). `logger_vectors.logger_sigma` calls the helper; new `tilt_missing_sd` in `SIGMA_CASES` (sd_u above the floor so it distinguishes the rules); vectors regenerated. `SoundingSigma.java`: tilt rule only when tiltSdU/E/N are all non-NaN; class comment rewritten. Tests: helper and version unit tests, end-to-end tilt capture (no GST: sigma_pole = tip_sink, sigma_rtk = tilt_sd_u, tilt_deg 0; tilt without sds and single take the bubble rule), GST floor case (rtk = max(tilt_sd_u, GST)), old gpkg without columns builds and warns once, NULL columns of a 0.6 session warn and of a 0.5 session do not; `test_logger_vectors` missing-sd case.
- `4f95ee2` docs: `logger/README.md` (importer note, sigma caveat), `analysis/README.md` soundings row, `docs/log.md` 11-26 line.

Suites (all hooks ran on every commit, none skipped):
- `python3 -m unittest discover icecube/tests`: Ran 67 tests, OK
- `python3 -m unittest discover -s analysis/qgis/tests` (test_import, test_soundings and the rest): Ran 103 tests, OK (skipped=5)
- `cd logger && ./gradlew testDebugUnitTest --max-workers 4`: BUILD SUCCESSFUL, all suites 0 failures (SoundingSigmaTest 3/3). C: free 115 GB before and after.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 18:40 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-9 at `a44f484`
**Base**: main
**Depth**: Deep (reason: 15 files, +652/-47 lines, Python and Java layers)
**Must-fix**: 2 | **Suggestions**: 6
**Round**: 1 | **Ship**: continue — round 1: 2 must-fix; first round always re-reviews after fixes

Cross-model: gemini complete, codex complete. Claude adversarial complete. Tests: analysis/qgis test_import + test_soundings, icecube test_logger_vectors pass (unittest); regenerated vectors match the committed JSON; gradle not run in review. Public-repo check: no coordinates or private data in the diff.

### Findings
- [ ] (must-fix) Schema upgrade bumps `schema_version` to 3 for the whole gpkg even when only some existing sessions were in this run's inputs (or a session was skipped by the snapshot guard); the rest keep NULL 0.6 columns and are then skipped as unchanged forever without --force. Keep the upgrade pending until every session in `done` is re-read, or track it per session; docstring "every session re-read once" is wrong otherwise (gemini, codex, claude-adversarial) — `analysis/qgis/import_sessions.py:541,605`
- [ ] (must-fix) The soundings safety-net warning fires only when all 0.6+ captures across all sessions lack `method`; a mix of upgraded and stranded 0.6+ sessions gives no warning. Check per session and name the sessions (gemini, codex, claude-adversarial) — `analysis/qgis/soundings.py:631-633`
- [ ] (suggestion) `read_points` does not strip `method`; Java `PointRow` trims before `TILT.equals`, so " tilt" diverges — `analysis/qgis/logger_session.py:239`
- [ ] (suggestion) Non-finite tilt sds: `_f("nan")` gives float NaN and `capture_sigma_inputs` checks only `None`; Java checks NaN. Map non-finite to None in `_f` or guard with `math.isfinite` — `analysis/qgis/soundings.py:517`
- [ ] (suggestion) Add a partially missing tilt-sd vector (one of three null) so the Java `&&` chain is pinned by the shared vectors — `icecube/logger_vectors.py:77`
- [ ] (suggestion) `make_session(tilt_sd=...)` writes 0.6 points.csv but events app stays "0.5"; pass app="0.6" for the tilt variant — `analysis/qgis/tests/test_soundings.py:190`
- [ ] (suggestion) Docstring table row `photos       PointZ` lost a space and no longer aligns — `analysis/qgis/import_sessions.py:23`
- [ ] (suggestion) Run `./gradlew testDebugUnitTest --max-workers 4` before push to confirm SoundingSigmaTest passes the new `tilt_missing_sd` vector (plan step 5) — `logger/app/src/test/java/rink/icecube/logger/core/SoundingSigmaTest.java`
