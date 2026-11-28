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

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)
**Plan**: `.agent/work-plans/issue-20/plan.md` at `a956d66`

Revised per the Plan Review and checkpoint: reader now calls `soundings.build(write=False)` on a scratch gpkg with a generated one-source config; requires an ENU `[site]`; base from survey key, season gpkg meta or `base_from_sessions`, plus `base_history`; points.csv-only fallback in `logger_source` with its test; `from_file` defaulted; `kinds` captures-only default; settle re-fit changes inflation only (drop rule stays 120 s).

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:57 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #20 — icecube: read Ice Cube Logger sessions
**Plan**: `.agent/work-plans/issue-20/plan.md` at `a956d66`
**Branch**: `feature/issue-20`

Repository: rolker/ice_cube. Re-review of the revision for the Plan Review at 9390e09, checked against `origin/main` (b208c31). #15 is still open; the plan waits for it.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | One PR, two commits; re-fit has a stop-and-ask rule and a stated scope (inflation only). |
| Issue alignment | Good | All Issue Review and Checkpoint items covered. Serves: "Season goal in `docs/roadmap.md` (surface and ice-growth from logger sessions); the project keeps no design document: none." Matches the roadmap line "`icecube` runs on logger sessions in the project's local frame". |
| File targeting | Good | `soundings.py`/`test_soundings.py` now listed for the fallback; entry point is `soundings.build(write=False)`, which on main opens the gpkg read-only, runs `finish_use`, `assign_anchors`, `verify_suspect`, `flag_local_outliers` and returns the rows. |
| Consequences | Good | Frame (ENU `[site]` required), base source order, `base_history`, fallback's effect on season rows, and the build-scoped anchors note are all in the table. |
| Principle alignment | Good | Reuse, one sigma rule, public-repo hygiene, config-not-hardcoded respected. |
| ADR compliance | Good | No project ADRs; workspace ADRs not triggered. |
| ROS conventions | N/A | Not a ROS project. |

### Previous findings

1. `logger_source` vs `build`: resolved (step 3 Rows).
2. Frame/base inputs: resolved (step 3 Frame, Base; step 4 keys, placeholders only).
3. `session_paths` + fallback with test: resolved (step 3, Files table, step 5).
4. `from_file`: resolved (reader defaults it; `cli.py:93-94` and `topo.py:88-97` call `read_survey` before `passes.segment(pts, scfg.get("pass_rule"))`, so a default set on `scfg` is seen).
5. `kinds`: resolved (captures-only default).
6. Re-fit scope: resolved (inflation only, drop rule stays 120 s, Java same scope).
7. #15 names: covered by step 1.

### Findings (settle during implementation, none blocking)

1. **[Approach]** `import_sessions.resolve_base(args, cfg, cfg_dir, layer_files, sessions)` takes an argparse namespace and reads `cfg["site"]["base"]`/`base_from_sessions`; the icecube `[site]` table is a different shape. Call it with a stub namespace and a generated `{"site": {"base_from_sessions": True}}`, or read the session's `base_ecef` directly. With no base found, raise a clear error: `build` does `float(meta["base_lat"])` and would fail with a bare KeyError.
2. **[Approach]** Call `import_session(..., extract_photos=False)` so the scratch import writes no photos, and build the scratch gpkg in a `TemporaryDirectory` that is removed afterwards (disk budget).
3. **[Approach]** The `from_file` default should be set with `setdefault` on the survey's `pass_rule` so a user value wins; a test asserts pass ids come from the stretches.
4. **[Approach]** Set the scratch gpkg's `origin_*` meta from `site.origin_geodetic` (after `origin_from_gpkg` is resolved by `Site`), so `build`'s frame equals the icecube frame and no shift is needed.

### Summary

All seven earlier findings are resolved in the plan and the main-branch claims check out. Ready for implementation once #15 merges; the four items above are small and belong in the implementation.

### Recommended Actions

- [ ] After #15 merges: step 1 re-check, then implement, folding in findings 1-4.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:57 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implementation held until #15 merges (soundings.py overlap), then rebase on main. Settle during implementation the Plan Review at ce6621a's items: base from the session's base_ecef or resolve_base via a stub, clear error if none; import_session(extract_photos=False) into a temporary directory removed afterwards (disk budget); pass_rule.from_file via setdefault; scratch origin from site.origin_geodetic.

## Implementation
**Status**: complete
**When**: 2026-11-27 19:23 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Branch**: feature/issue-20 at 7d9be12

### Step 1: #15 names re-checked (main 42d5955)

All plan names hold: points.csv `fix_start`, `float_spell_s`, `suspect_reason`, `fix_young` (`import_sessions.LOGGER_11`), `SCHEMA_VERSION` 5, flag `float_spell` in `fix_stretches`/`EXCLUDING`, `DEFAULTS["settle_s"]` 120, `soundings.build(cfg_all, cfg_dir, gpkg=None, csv_out=None, log=print, write=True)`. Adaptations:
- The points.csv-only fallback was already delivered by #15 (`capture_fix_info`: per-capture `fix_age_s` gives the settle sigma, suspect columns give the flags, pass `<session>/c<n>`). Only the logged warning was missing: added (worded "no raw epochs for session(s) ...", not "warning", which the tilt-column tests count); a session whose gps.ubx has no GGA counts too.
- The season gpkg base comes from a survey key `base_from_gpkg` (read_survey sees only the survey table, not `[season]`).

### Commits

- `643c9ff` soundings: name sessions read without raw epochs in the log (+ test)
- `28461cb` soundings: count a session without GGA as read without raw epochs
- `4ba080b` logger_session: keep the whole RTCM 1005 ecef (bug found here: `_kv` stopped at ';', so `base_from_sessions` raised an unpack error; + test)
- `f2dc975` icecube: reader `logger_session` (scratch gpkg in a TemporaryDirectory, `import_session(extract_photos=False)`, `soundings.build(write=False)` with one generated logger source and `session_paths`; ENU `[site]` required; base from `base`, `base_from_gpkg`, or the sessions' 1005, clear error if none; `base_history` passed through; kinds captures-only by default; `include_voided`; `pass_rule.from_file` via setdefault; `read_soundings` split into `_keep_rows` + `_points_from_rows`); example toml, how-to; `icecube/tests/test_logger_session.py` (10 tests, synthetic, including an end-to-end `cli run` next to the season table of the same session: growth 0)
- `b36f82b` settle re-fit: `settle = 10 cm x (1 - t/120 s)` (was 8 cm x (1 - t/120)^2) in `sigma_terms`, `DEFAULTS`, `SoundingSigma.java`; vectors regenerated; how-to row, logger README coverage text
- `7d9be12` docs/log.md entry

### Settle re-fit (relative numbers)

11-11, 11-15, 11-21 sessions offline via soundings.build on a scratch gpkg (scratch removed). Residual of each usable ground capture against a plane through mature captures (fix age >= 150 s) within 1 m; 571 residuals. Mature robust sd 3.2 cm. Excess robust sd over that: 0-60 s 5.1 cm (n 39), 60-120 s 2.6 cm (n 34), 90-150 s 5.7 cm (n 26). Fit of A (1 - t/120)^p: A 9.5 cm, p 0.75 (bootstrap p 0.25-1.5, A 6-14 cm); linear with A 10 cm is as good (log-likelihood within 0.4), old p 2 clearly worse. Fits the plan's size limit (one function, one Java method); drop rule unchanged at 120 s. The 120-150 s bin still shows excess (n 10) that a 120 s window cannot carry; that would need a settle_s change (drop rule, out of scope).

### Tests

- `python3 -m unittest discover -s analysis/qgis/tests`: OK (172 + 2 new, 5 skipped)
- `python3 -m unittest discover icecube/tests`: 112 OK
- `./gradlew testDebugUnitTest --max-workers 4`: 233 tests, 0 failures (C: 109 GB free)

### Deferred / for the owner

- Logger app version not bumped for the SoundingSigma change (the #23 agent is in logger/ in parallel); bump at merge time if wanted.
- A direct `logger_source` on parsed sessions (no scratch sqlite) stays for later, as planned.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 19:28 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-20 at `74a0894`
**Base**: main
**Depth**: Deep (reason: cross-layer Python + Java logger port, ~860 lines over 14 files)
**Must-fix**: 1 | **Suggestions**: 6
**Round**: 1 | **Ship**: continue — round 1: 1 must-fix; first round always re-reviews after fixes

Verified: icecube/tests 112 OK; analysis/qgis/tests 173 OK (5 skipped); regenerated coverage_vectors.json is identical to the committed file; Gradle SoundingSigmaTest (3) and CoverageTest pass (C: 109 GB free); grep of added lines finds no real coordinates, names or hosts (only synthetic 0N 0E). Outside reviewers: gemini and codex completed; Claude adversarial subagent completed.

Logger version: leaving versionName at 0.11.0 is acceptable. SoundingSigma feeds only the live coverage overlay (Coverage.java), and its output is not written to points.csv, so no data provenance depends on the version. The docs key the change to #20. The 0.12.0 bump in #23 should list the settle change when it ships.

### Findings
- [x] (must-fix) logger_session anchors are named site_N because the scratch gpkg never runs import_sessions.update_local, so refs.site is NULL. Crossover pairs anchors across surveys by name, so these never tie to the season's siteN refs and can collide with a season site_N group (controls or snap-joined) on a different mark. Namespace the names per survey, or match them to the season sites when a season gpkg is given, and test this with a ref capture (codex, confirmed) — `icecube/io.py:378-418`, `analysis/qgis/soundings.py:891-893,1190`
- [x] (suggestion) Session sources from find_sessions are not closed when the sessions filter raises "not found" or an import step raises; wrap them in a try/finally. Pre-existing: logger_source's raws from session_paths are never closed — `icecube/io.py:363-389`, `analysis/qgis/soundings.py:796-798`
- [x] (suggestion) base_from_gpkg is resolved only after every session is imported. A missing file, a non-gpkg file or a partial base_* gives a raw sqlite3/KeyError. The file:{path} URI is not escaped (#, ?, %), and there is no test — `icecube/io.py:316-327,394-397`
- [x] (suggestion) setdefault("from_file", True) also adds from_file to a pass_rule the user wrote without it (e.g. only gap_s), so the user's rule is silently overridden; the comment says the user's rule wins — `icecube/io.py:709`
- [x] (suggestion) The fallback to the first RTCM 1005 makes that session's own base the "current" base, so base_history shifts toward it. That is wrong for a session logged under an older base. Warn, or require base/base_from_gpkg, when base_history is set — `icecube/io.py:398-401`
- [x] (suggestion) site.z0 is not subtracted (other readers do it via Site.local; read_soundings has the same gap). Apply it or document it — `icecube/io.py:424-425`
- [x] (suggestion) Untested: a gps.ubx with no GGA (the no_raw append branch), base_from_gpkg, base_history, kinds with "stop" — `icecube/tests/test_logger_session.py`, `analysis/qgis/tests/test_soundings.py:677`

## Implementation
**Status**: complete
**When**: 2026-11-27 19:38 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-20 at `c77969c`
**Addressed**: Local Review (Pre-Push) at `74a0894` (2026-11-27 19:28 -05:00)
**Commits**: 0fb046a, c77969c

### Actions
- [x] (must-fix) Anchor names, per the host decision: with base_from_gpkg the scratch refs take the season's sites read-only (_season_sites: the stored site for a session the season holds, else update_local's seed rule within REF_SITE_M), named site<k>; every other anchor is <survey>:<name>; tested with ref captures (no season, season-held, seed-matched, unmatched) — `icecube/io.py` (_season_sites, read_logger_session)
- [x] (suggestion) Sources closed on every path: try/finally in read_logger_session; logger_source now a wrapper that closes its session_paths sources (only those lines touched in soundings.py, no pole-check parsing) — `icecube/io.py`, `analysis/qgis/soundings.py:730`
- [x] (suggestion) base_from_gpkg read first (_read_season) with ValueErrors for a missing file, a non-gpkg file and a partial base; read-only URI escaped via Path.as_uri (_ro_uri, used by every gpkg reader in io.py); tested incl. a path with # ? % — `icecube/io.py`
- [x] (suggestion) pass_rule: from_file = true only when the survey has no pass_rule at all; tested — `icecube/io.py` (read_survey)
- [x] (suggestion) base_history without base or base_from_gpkg is a ValueError; the 1005 fallback warns on stderr; tested — `icecube/io.py`
- [x] (suggestion) site.z0 subtracted in read_logger_session and read_soundings; tested. No existing result changes: the example and the only private config with a soundings survey have no or zero z0 — `icecube/io.py`, `icecube/tests/test_topo.py`
- [x] (suggestion) Tests added: gps.ubx with no GGA, base_from_gpkg, base_history, kinds with stop (a 4 Hz session with a ref stand and a plumb stop) — `icecube/tests/test_logger_session.py`

### Checks
- python3 -m unittest discover -s analysis/qgis/tests: Ran 173 tests, OK (skipped=5)
- python3 -m unittest discover icecube/tests: Ran 121 tests, OK
- Docs: docs/howto/ice-cube.md and icecube.example.toml updated for the above.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 19:44 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**Branch**: feature/issue-20 at `ff90d82`
**Base**: main
**Depth**: Deep (reason: whole-diff classification kept for round 2; cross-layer Python + Java logger port, ~1150 lines over 15 files)
**Must-fix**: 0 | **Suggestions**: 8
**Round**: 2 | **Ship**: recommended — no must-fix findings; the round-1 must-fix (anchor names) is resolved; remaining suggestions can be applied or tracked

Verified: the Claude adversarial subagent ran icecube/tests (121 OK) and analysis/qgis/tests (173 OK, 5 skipped); py_compile clean; no Python linter installed (ruff, flake8, pyflakes absent), so static analysis was not run. The added lines leak nothing private (only synthetic coordinates near 0 N 0 E). No double subtraction of z0. Outside reviewers: gemini and codex completed (EXIT=0). Codex's must-fix (no station filter) and gemini's must-fix (relative base_dir) are downgraded to suggestions: by default the season source has no station filter either, and the CLI always passes an absolute base_dir. Merge check against origin/feature/issue-23: only docs/log.md conflicts (journal text). The schema 6 bump adds refs columns only; _read_season's SELECT still works on a v6 season gpkg.

### Findings
- [x] (suggestion) The logger_session reader cannot pass a `station` filter to fix_stretches the way a season logger source can, so with a station-filtered season config its rows can differ. Expose `station` (codex) — `icecube/io.py:740`
- [x] (suggestion) season_names is the set of stored refs.site values. When assign_anchors has merged two season sites into one group, it names the group names[0], so a ref given the other site name ties to nothing (a missed tie, never a false one). Take the names from the season's anchors, or map each site to its group (claude) — `icecube/io.py:617`
- [x] (suggestion) read_soundings now subtracts z0 with a UTM site too, which moves the soundings vertically for a UTM config with z0 set. Subtract only when the site has an ENU origin, or document that the reader expects ENU (claude) — `icecube/io.py:517`
- [x] (suggestion) A relative base_dir from a library caller gives a double prefix: read_survey has already joined base_dir/path and ab() joins it again. Resolve once (gemini) — `icecube/io.py:678,795`
- [x] (suggestion) `base` is not validated, so a 2-element list fails with an IndexError. Check for 3 values and raise a ValueError that names the survey (gemini, claude) — `icecube/io.py:721`
- [ ] (suggestion) A caller that passes `log` gets no stderr echo. The RTCM 1005 warning bypasses `log`, and an empty survey id gives an empty message prefix; use `tag` (gemini, claude) — `icecube/io.py:699,732`
- [ ] (suggestion) soundings.build, which the reader now runs on a temp scratch gpkg, opens it with an unescaped `file:` URI and closes it only on the normal path. Use try/finally and the escaped URI (claude) — `analysis/qgis/soundings.py:1440,1467`
- [ ] (suggestion) Tests: the stored-site branch of _season_sites is never exercised apart from the seed rule (re-read the season-held session after the move), and the pass_rule tests assert only the dict, not the passes that segment produces (claude) — `icecube/tests/test_logger_session.py:204,314`

## Checkpoint
**Status**: complete
**When**: 2026-11-27 19:45 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push approved with 8 small valid suggestions; orchestrator table allows address)
**After**: publish
**Decision**: address

Fix all 8 suggestions of the Local Review at 4586ad2: expose a station filter; merged-site groups map to the season's group name (or document the missed tie, prefer mapping); z0 subtracted only when the site has an ENU origin (document); base_dir joined once; base checked for 3 finite values; logging (stderr echo with a caller log, 1005 warning through log, no empty prefix); soundings.build escapes the URI and closes in finally; tests for the stored-site branch and pass_rule effects on passes.
