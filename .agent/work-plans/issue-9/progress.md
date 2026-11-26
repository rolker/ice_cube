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
