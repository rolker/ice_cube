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
