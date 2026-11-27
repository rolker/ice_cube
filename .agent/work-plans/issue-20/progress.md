---
issue: 20
---

# Issue #20 — icecube: read Ice Cube Logger sessions

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)

**Issue**: #20

### Scope Assessment

**Well-scoped?** Mostly. One PR is feasible if the reader and the settle re-fit stay as two commits; if the re-fit grows, split it into its own issue (it touches `soundings.py`, the logger's Java port and the test vectors).
**Right repo?** Yes. `icecube/` plus the shared sigma rules in `analysis/qgis/soundings.py`.
**Serves**: the season goal in `docs/roadmap.md`; the project keeps no separate design document, so none cited.
**Design picture?** No change.
**Dependencies**: #15 (in implementation) changes the settle default to 120 s and adds `points.csv` columns (`fix_start`, `float_spell_s`, `suspect_reason`, `fix_young`) and a float-spell rule in `soundings.fix_stretches`; start implementation after #15 merges. #27 and #9 are merged; the reader must exclude voided captures. #24 (robust nodes) is closed, so drop it from this scope.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Reuse over duplication | Action needed | `analysis/qgis/logger_session.py` and `soundings.logger_source` already read sessions and build per-capture sigma, stretches and passes. The reader should wrap those, not add a third parser. Decide in the plan between a thin `icecube.io` adapter and running `soundings` on a scratch gpkg. |
| One rule, one place (sigma) | Action needed | The settle term lives in `soundings.sigma_terms`, mirrored in the logger's Java port and pinned by `logger_vectors.py`. A re-fit must change the Python rule, regenerate the vectors and update the Java port together, after #15's default lands. |
| Public repo hygiene | Watch | Re-fit numbers must be relative only. |
| Only what's needed | Watch | Robust nodes is done under #24/#29; do not re-scope it here. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| Workspace ADRs | No | Project-level change. |

### Consequences

- Settle-term change: update `soundings.py` docstring and `DEFAULTS`, `logger_vectors.py` and `coverage_vectors.json`, the Java sigma port and tests, and the howto/README text on the 90 s term.
- New reader: add a `[survey]` source option to `icecube.example.toml` and document it in `docs/howto/ice-cube.md`.

### Recommendations

- Start implementation only after #15 merges; build on its `points.csv` columns and float-spell rule.
- Reader reuses `logger_session.py` and the `soundings.py` sigma and stretch rules; carry `fix_age_s`, `fix_jump_m` and `fix_suspect` (and #15's `fix_young`, `float_spell_s`) through, exclude voided captures, and make fix stretches the pass boundaries.
- Re-fit the settle term on the 11-11, 11-15 and 11-21 sessions (flat to about the settle time, then taper), report only relative figures, and ship it with the vectors and Java port in the same PR.
- Treat the settle re-fit as separable: if it grows beyond a small change, split it from the reader.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:51 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism only; owner asked to orchestrate the issues in parallel while he is busy: "Orchestrate the issues while I'm busy and run them in parallel if practical")
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): the reader is a thin icecube.io adapter over logger_session.py and the soundings rules (no third parser); robust nodes are already delivered by #24/#29, noted as done rather than re-scoped; the settle re-fit is planned in this issue, and if it grows beyond a small change the plan says so and the split goes to the owner as a question (a new issue is his). Implementation starts only after #15 merges and builds on it (120 s settle, new points.csv columns, float-spell rule).

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)
**Plan**: `.agent/work-plans/issue-20/plan.md` at `2451b23`

Thin `icecube.io` reader over logger_session/import_sessions/soundings (scratch gpkg, sigma and stretches from the existing rules, voided excluded), built on #15's 120 s settle, new columns and float-spell rule; settle re-fit as a separate second commit, flagged for owner split if it outgrows `sigma_terms` and its Java mirror. Implementation after #15 merges.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:54 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: needs-work

**Issue**: #20 — icecube: read Ice Cube Logger sessions
**Plan**: `.agent/work-plans/issue-20/plan.md` at `2451b23`
**Branch**: `feature/issue-20`

Repository: rolker/ice_cube. Reviewed against `main` code and the #15 plan (read-only). Host decisions taken as given: scratch-gpkg route accepted for now; with only points.csv (no raw epochs) fall back to per-capture `fix_age_s` with a warning; step 1 re-checks #15's names after it merges.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | One PR, two commits; the settle re-fit has a clear stop-and-ask rule. |
| Issue alignment | Good | Covers reader, sigma reuse, stretches as passes, settle re-fit, synthetic tests; Issue Review points (voided excluded, #15 columns, robust nodes out of scope) addressed. Serves: "Season goal in `docs/roadmap.md` (surface and ice-growth from logger sessions); the project keeps no design document: none." |
| File targeting | Needs work | Entry point is `logger_source` but the finishing steps live in `soundings.build` (finding 1); the points.csv-only fallback needs code in `soundings.py` and its tests, not listed (finding 3). |
| Consequences | Needs work | Frame/base inputs of `logger_source` not planned (finding 2); fallback changes the season table for raw-less sessions too (finding 3). |
| Principle alignment | Good | Reuse over duplication, one sigma rule, public-repo hygiene and config-not-hardcoded are all respected. |
| ADR compliance | Good | No project ADRs; workspace ADRs not triggered. |
| ROS conventions | N/A | Not a ROS project. |

### Findings

1. **[File targeting / approach, blocking]** `soundings.logger_source` (soundings.py:619) does not produce finished rows: `use` is left `None` for every row except `no_position` ones; `finish_use`, `assign_anchors`, `verify_suspect` and `flag_local_outliers` run only in `soundings.build` (soundings.py:1331-1343). Feeding `logger_source` output to `_points_from_rows` would drop every row at the `use` filter, and anchors would be unset. Resolution: call `soundings.build(cfg_all, cfg_dir, gpkg=<scratch>, write=False)` with a generated `[soundings]` block holding one `type = "logger"` source (it also handles `base_history`, `corrections_csv` and the summary log), instead of `logger_source` directly. Note in the how-to that anchor joining and `verify_suspect` then see only the checked session(s), so results can differ slightly from the season table.
2. **[Consequences, blocking]** Frame and base: `build` reads `origin_*` and `base_*` from the gpkg's `icecube_meta` and builds a `BaseHistory` (current base plus optional `base_history` CSV), and `logger_source` applies `history.shift(t)` to every position. The plan says only "the site's local frame". It must say (a) the reader requires `[site]` with `origin_geodetic` or `origin_from_gpkg` (a UTM-only site has no ENU origin; error clearly), (b) where the current base comes from (a survey key such as `base`, or `base_from_sessions` via `import_sessions.resolve_base`, or read-only from the season gpkg's meta), written into the scratch gpkg with `set_meta`, and (c) a `base_history` key passed through. New keys go in `icecube.example.toml` with placeholder values only.
3. **[Consequences, settle during implementation]** Raw epochs reach `logger_source` only through `src["session_paths"]`; the reader must set it to the session folder/zip paths. The host's points.csv-only fallback is new code: `logger_source` does not select `captures.fix_age_s` and flags `no_fix_info` with `fix_age_s` None when there is no raw. If the fallback goes into `logger_source` (the only place that keeps one rule), it also changes season-table rows for raw-less sessions; list `analysis/qgis/soundings.py` and `analysis/qgis/tests/test_soundings.py` in commit 1 and add a test (settle inflation from per-capture `fix_age_s`, warning logged, pass id per capture).
4. **[Approach, settle during implementation]** `read_survey` does not set `pass_rule.from_file` for the soundings reader; the user sets `[surveys.pass_rule] from_file = true` (icecube.example.toml:147-148), and `passes.segment` is called from `cli.py`/`topo.py`. Either the example block sets `from_file = true` or the reader defaults it (then `cli.py`/`topo.py` are touched). Pick one.
5. **[Approach, settle during implementation]** `logger_source` also returns pole stops (`kind = "stop"`). The plan does not say whether the reader keeps them; expose `kinds` as `read_soundings` does and state the default.
6. **[Consequences, settle during implementation]** The settle re-fit (step 6) changes the inflation shape; the pass-drop rule (flag `settle`, `stops_from_epochs` line ~892, `ep.fix_age < settle_s`) uses `settle_s` alone. State in the re-fit commit whether a hold time changes that drop rule or only the inflation, and keep the Java mirror to the same scope.
7. **[Dependency, settle during implementation]** Test "post-float-spell capture excluded" and the #15 names (`float_spell`, `fix_young`, `SCHEMA_VERSION` 5) are only in the #15 plan today; step 1's re-check covers them.

### Summary

Sound direction and good reuse, but the plan names the wrong `soundings.py` entry point (rows come back unfinished) and leaves the frame/base inputs unplanned. Amend those two in the plan; findings 3-7 are small and can be settled during implementation after #15 merges.

### Recommended Actions

- [ ] Plan step 3: call `soundings.build(..., gpkg=scratch, write=False)` with a generated one-source `[soundings]` config, not `logger_source` directly.
- [ ] Plan step 3/4: require an ENU `[site]` origin; name the source of the current base and the `base_history` key, written to the scratch gpkg's meta; add the keys to `icecube.example.toml`.
- [ ] During implementation: `session_paths` wiring; fallback in `logger_source` with its test; `from_file` default; `kinds` default; settle drop rule scope in the re-fit; re-check #15 names.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work on mechanism; orchestrator table allows revise)
**After**: plan
**Decision**: revise

Revise per the Plan Review at 9390e09: call soundings.build(..., write=False) on a scratch gpkg with a generated one-source [soundings] block (note in the howto that anchors/verify_suspect then see only this session); require an ENU [site]; base position from a survey key, else read-only from the season gpkg, else base_from_sessions, written to the scratch gpkg metadata; pass base_history through; placeholders only in icecube.example.toml. Fold in the implementation-level items 3-6 (session_paths, the points.csv-only fallback with its own test in soundings.py, pass_rule.from_file defaulted by the reader, expose kinds with captures-only default, and state that the settle re-fit changes the sigma inflation only, the pass-drop rule stays at #15's 120 s, Java mirror the same scope).
