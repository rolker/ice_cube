---
issue: 27
---

# Issue #27 — Logger: undo or flag an accidental capture

## Issue Review
**Status**: complete
**When**: 2026-11-26 18:26 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #27

### Scope Assessment

**Well-scoped?** Mostly. Undo, flag/unflag, export columns, overlay rebuild and reader skipping are one coherent feature, but the owner's comment adds photo re-targeting and a photo-target indicator. That is separable UI work; consider keeping it in this PR only if the void/flag data model is done first, else split into a follow-up issue.
**Right repo?** Yes. Logger changes live in `logger/`; reader skipping touches `icecube/` (#20) and `analysis/` (#21), which are open and not yet built, so those are acceptance notes for them rather than work here.
**Serves**: project goal "surveyed with the logger" (`docs/roadmap.md` Goal); the project has no design document, so no design section.
**Design picture?** No change to a design document (none exists). The data model does change: `points.csv` gains `void` / `flag_reason` columns and `events.csv` gains `flag`/`unflag` events.
**Dependencies**: #23 (pole-check reference "last visit") is open, so the void exclusion must be coded against whatever #23 lands; #24 is closed, so the node-red rule it introduced can be tested now. #20/#21 readers are open: the skipping requirement should be recorded on them. #15 is related only.

### Principle Alignment

The project has no principles file or ADRs; checked against CLAUDE.md rules and the logger README.

| Principle | Status | Notes |
|---|---|---|
| Public repo (no coordinates, names) | OK | The 2026-11-26 session details in the comment carry no coordinates; keep test fixtures and docs synthetic. |
| Data stays honest / append-only | Action needed | The issue says `points.csv` is "append-only", but `logger/README.md` says the app rewrites the whole file on every change (e.g. a photo attach). Settle which it is: voiding can set the `void` column in the rewrite, with `events.csv` as the real append-only audit trail. Say so in the README. |
| Docs follow behaviour | Watch | `logger/README.md` (points.csv columns, events, Main screen, beeps), the logger version/build and `docs/howto/ice-cube.md` if icecube reads the column need updating. |
| Scripts/config, no hardcoded values | Watch | The undo window (about 8 s) and reason list should be constants or a setting, not scattered literals. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none (the project keeps no ADRs) | n/a | |

### Consequences

- `logger/README.md`: points.csv column table, events.csv event list, Main screen, photo behaviour.
- Capture numbering: a voided capture keeps its number (numbers are per session across all classes), so readers and photo rows must not renumber.
- Session readers #20 and #21: skip voided rows by default and report the count; older sessions without the column must read as not voided.
- Pole-check history and ref "last visit" (#23, #24): exclude voided captures, including after an app restart (rebuild from the file).
- Photo attachment: voiding a capture that has a photo moves it to the previous capture (or asks); define what happens when there is no previous capture, or the previous one is a ref or itself voided.

### Recommendations

- Resolve the append-only wording against the README's rewrite-on-change behaviour, and state the source of truth (events.csv) and the derived column (points.csv).
- Specify the photo rule: on void, re-attach to the nearest earlier non-voided capture; show the target number and age on the PHOTO button; handle no valid target.
- Define how voiding interacts with a capture of class `ref` or `tilt` (a ref occupation or tilt set spanning several epochs), or scope this to single captures first.
- Add tests for the photo re-attach and for reading an older session with no `void` column, beyond the ones the issue lists.
- Note the voided-skip requirement on #20 and #21 so the reader work picks it up.

### Actions
- [ ] Settle which it is: voiding can set the `void` column in the rewrite, with `events.csv` as the real append-only audit trail. Say so in the README. (the issue says `points.csv` is "append-only", but the README says the app rewrites the whole file on every change)
- [ ] Resolve the append-only wording against the README's rewrite-on-change behaviour, and state the source of truth (events.csv) and the derived column (points.csv).
- [ ] Specify the photo rule: on void, re-attach to the nearest earlier non-voided capture; show the target number and age on the PHOTO button; handle no valid target.
- [ ] Define how voiding interacts with a capture of class `ref` or `tilt` (a ref occupation or tilt set spanning several epochs), or scope this to single captures first.
- [ ] Add tests for the photo re-attach and for reading an older session with no `void` column, beyond the ones the issue lists.
- [ ] Note the voided-skip requirement on #20 and #21 so the reader work picks it up.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:26 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (actions are plan inputs within the issue's scope; orchestrator table allows proceed)
**After**: issue-actions
**Decision**: proceed

Actions go to plan-task: events.csv is the append-only source of truth and points.csv's void column is derived (points.csv is rewritten today); photo re-attachment rule (owner's comment on the issue) stays in scope, the plan may split it into a second PR; decide voiding for ref and tilt captures; voided captures keep their numbers; old sessions without the column read as not voided; follow #23's ref history; note the reader skip for #20/#21; docs and version.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:35 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-27/plan.md` at `4844630`

Soft-void with events.csv as the append-only source of truth and derived void/void_reason columns in points.csv and refs.csv; Coverage.feeds and PoleCheck skip voided rows; voided photos move to the nearest earlier live capture. Two PRs: A (data model, undo/flag UI, readers, docs, 0.9.0), B (PHOTO target indicator).

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:30 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: needs-work

**Issue**: #27 — Logger: undo or flag an accidental capture
**Plan**: `.agent/work-plans/issue-27/plan.md` at `4844630`
**Branch**: `feature/issue-27`

Reviewed with the host's decisions as given: in-place atomic refs.csv rewrite logged in events.csv; new `photo` row when there is no earlier capture; no 60 s limit on void-time re-attach; qgis importer updated here; 0.9.0 / build 13; UI split into a second PR only if PR A exceeds about a dozen files of real change (implementer decides and records why).

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Needs work | PR A lists about 16 source/doc files plus tests (PointRow, VoidRule, PhotoRule, PoleCheck, Coverage, CoverageWorker, Survey, RefsStore, Storage, MainActivity, MapView, logger_session.py, import_sessions.py, README, howto, build.gradle). That is past the host's "about a dozen" line, so the UI split (step 7) is likely; the implementer decides and records why in progress.md. Not a blocker. |
| Issue alignment | Good | Covers undo, flag/unflag with reasons, columns, overlay, pole-check history, reader skipping, the owner's photo case, and every review-issue action (source of truth, photo rule, ref/tilt, old-file tests, #20/#21 notes). Serves: project goal "surveyed with the logger" (`docs/roadmap.md` Direction); no design document. |
| File targeting | Needs work | The undo binding in `Survey.Notice` and the earlier-refs map layer (`MainActivity` refs loader keyed on `refsGeneration`) need explicit mention; see findings 1 and 3. `analysis/qgis/soundings.py` reads logger captures from the gpkg the importer writes (finding 4). |
| Consequences | Needs work | Missing: voiding a ref must bump `refsGeneration` so the map's earlier-refs layer drops it; refs.csv may exist in more than one storage root (`Storage.refsFiles`). |
| Principle alignment | Good | Public-repo fixtures synthetic; nothing deleted, events.csv is the trail, numbers kept; docs and version in the same PR; window and reasons as constants. Tests planned for each rule. |
| ADR compliance | Good | The project keeps no ADRs; none triggered. |
| ROS conventions | N/A | Android app and Python scripts. |

**Coverage vs #29 (host check)**: fine. The plan touches `Coverage.java` only in `feeds()` (one more condition, `!r.voided`), and puts the forced rebuild in `CoverageWorker.update`, which already rebuilds via `rebuild()` when the row count shrinks. #29's plan keeps its PR A in Python and moves its `Coverage.java` port (entry offsets, ring re-reduction) to its PR B; it does not list `CoverageWorker.java`. The forced rebuild also covers the case where a void appends a new `photo` row (row count grows, which would otherwise be only a feed).

**UNDO vs the stray tap (host check)**: placement is good. The notice bar sits above the map in portrait (buttons along the bottom) and in the left column in landscape (buttons in a right column), so a repeated stray tap on MARK lands on MARK, not UNDO, and a volume-key capture cannot hit UNDO. The gaps are in binding and timing (finding 1).

### Findings

1. **[File targeting / UI]** The UNDO must be bound to the capture explicitly, not inferred from `Notice.at`. Today `photoTaken` sets `notice = new Notice("Photo on S12", ..., target)` with `at` set to the capture, so "notice has an `at` capture" would offer UNDO of the capture the photo just went on. Add an explicit undo target (`undoN`, `undoUntilMs`) on `Notice`, set only by the capture's own notice (surface/object single capture, tilt end, ref end). Also specify: (a) an arm delay of about 0.5-1 s after the notice appears during which a tap on the bar is ignored, so the second tap of a double tap, or a press already in progress when the bar becomes visible, cannot undo; (b) `Survey.voidCapture(n, "accidental", "undo")` checks the window itself against the capture's `elapsedNs` and `VoidRule.UNDO_WINDOW_MS` and refuses an already-voided row; (c) the UNDO label goes away at 8 s although the notice stays for `NOTICE_MS` = 10 s; (d) a newer notice (next capture, photo, ref status) ends the UNDO; the owner's 11-26 case (photo 3 s later) is then handled through Recent captures, which the README should say.
2. **[Principle: correctness]** `voided` is mutated on the Survey side while the coverage worker thread and `MapView` read the same `PointRow` objects (`Survey.rows()` copies the array, not the rows). Make the field `volatile` (or replace the row object) so the forced rebuild is guaranteed to see the new value.
3. **[Consequences]** `RefsStore.setVoid` must say which file it rewrites: `Storage.refsFiles` can return one refs.csv per storage root and `RefsStore.readAll` reads them all, so rewrite the matching (session, n) row in every root that has it (each atomically). Voiding or un-voiding a ref must bump `refsGeneration` so `MainActivity`'s earlier-refs layer reloads, and that layer (and `MapView`'s drawing of it) must skip voided rows.
4. **[File targeting]** `analysis/qgis/soundings.py` reads logger captures from the gpkg `captures` table that `import_sessions.py` writes. If the importer drops voided rows, nothing more is needed; if it keeps them with a void flag (useful to see them in QGIS), `soundings.py` must filter them. State which, and fix the plan's "the one reader on main" wording.

### Summary

A sound soft-void design that answers every review-issue action and fits the host's decisions; the coverage change is kept clear of #29. Before implementation, pin down how UNDO is bound and guarded, the visibility of the `voided` field across threads, and the refs.csv and earlier-refs consequences.

### Recommended Actions

- [ ] Bind UNDO to an explicit `undoN`/`undoUntilMs` on the capture's own notice, with an arm delay of about 0.5-1 s, a window check in `Survey`, and the label hidden at 8 s; document that a newer notice ends it.
- [ ] Make `PointRow.voided` (and `voidReason`) safe to read from the coverage and drawing threads.
- [ ] Specify `RefsStore.setVoid` over every storage root's refs.csv, bump `refsGeneration` on ref void/unvoid, and skip voided rows in the earlier-refs layer.
- [ ] State whether the importer drops or flags voided rows, and update `soundings.py` if flagged.
- [ ] Record the PR A / UI split decision and the reason in progress.md.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:30 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work; orchestrator table allows revise)
**After**: plan
**Decision**: revise

Address must-fix 1-4: UNDO tied to the capture's own notice (undoN/undoUntilMs), taps ignored for ~0.75 s after it appears, voidCapture checks the 8 s window against capture time and refuses already-voided rows, UNDO label hidden at 8 s (notice stays 10 s), a newer notice ends the UNDO (the 11-26 photo case goes through Recent captures); PointRow.voided made thread-safe (volatile or replace the row object); RefsStore.setVoid rewrites the matching row in every refs.csv under Storage.refsFiles atomically and bumps refsGeneration. Host decision on must-fix 4: the importer DROPS voided rows (they never reach the gpkg) and reports the count per session; fix the 'one reader' wording. The UI split into PR B is likely (PR A ~16 files); the implementer decides and records why.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:31 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-27/plan.md` at `8bffd83`

Revision after plan review: UNDO bound to the capture's own notice (undoN/undoUntilMs, 0.75 s arm delay, window checked in Survey, label hidden at 8 s, newer notice ends it); volatile voided/voidReason; RefsStore.setVoid over every refs.csv root with refsGeneration bump; importer drops voided rows and counts them. Split decided: PR A data model, coverage, refs, importer, docs (~12 files); PR B UI, PHOTO target line, version 0.9.0 (~5 files), because the whole was ~16 files and the halves review differently.

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:32 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: ready

**Issue**: #27 — Logger: undo or flag an accidental capture
**Plan**: `.agent/work-plans/issue-27/plan.md` at `8bffd83`
**Branch**: `feature/issue-27`

Second review, after the revision. Host decisions taken as given: refs.csv rewrite atomic and logged; new `photo` row when there is no earlier capture; no re-attach time limit; importer drops voided rows and counts them; 0.9.0 / build 13 in PR B.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Split recorded with its reason: PR A (steps 1-9, about 12 files, JVM-tested data model, coverage, refs, importer, docs) and PR B (steps 10-12, about 5 files of UI plus the version). PR A stands alone: nothing in it can void a row (no UI calls `voidCapture`), so `lastCapture`, PhotoRule, PoleCheck and the overlay behave as on main; the only visible effect is two extra columns in points.csv and refs.csv, which older readers tolerate (`PointRow.parse` reads extra columns past `COLUMNS`; the 0.6 header upgrade runs once). |
| Issue alignment | Good | Every review-issue action and earlier must-fix covered. Serves: project goal "surveyed with the logger" (`docs/roadmap.md` Direction); the project has no design document. |
| File targeting | Needs work | `import_sessions.py` has a second refs path, `import_refs_csv` (refs.csv rows of sessions not otherwise imported), not covered by "drops rows with `void=1`" as written (finding 2). |
| Consequences | Good | refsGeneration bump, every refs.csv root, earlier-refs layer, `soundings.py` reading only the gpkg, #23/#29 and #20/#21 notes all present. |
| Principle alignment | Good | Synthetic fixtures; nothing deleted; constants in `VoidRule`; tests for each rule. One planned test rests on a behaviour the app does not have (finding 1). |
| ADR compliance | Good | The project keeps no ADRs; none triggered. |
| ROS conventions | N/A | Android app and Python scripts. |

**Earlier must-fixes**: 1 (UNDO bound to `undoN`/`undoUntilMs` on the capture's own notice, 0.75 s arm, window checked in `Survey`, label gone at 8 s, newer notice ends it, 11-26 case via Recent captures) addressed in steps 3 and 10. 2 (`volatile` voided/voidReason) addressed in step 1. 3 (`setVoid` over every `Storage.refsFiles` root, atomic, `refsGeneration` bump, map read skips voided) addressed in step 4. 4 (importer drops, `soundings.py` unchanged, "one reader" wording fixed) addressed in step 7.

### Findings

1. **[Principle: correctness of the plan's facts]** There is no session resume. `Survey`'s constructor writes a fresh points.csv (`writePoints(new ArrayList<String>())`) and `LoggerService` makes a new `Survey` per REC; nothing reads points.csv back on the phone. So the "On session resume the void state ... is read from the `void` column" paragraph and the `CoverageWorkerTest` case "restart rebuild from file skips it" describe something that does not exist. Drop both; the only state that outlives a session on the phone is refs.csv, which step 4 already covers (PoleCheck and the earlier-refs layer read the `void` column). The README should say so rather than promise a resume rule.
2. **[File targeting]** `import_sessions.py` imports refs twice: from each session's points.csv (covered by the drop) and through `import_refs_csv`, which reads `read_refs_csv` rows of sessions not otherwise imported. That path must also skip `void=1` (and count them), or a voided ref reaches the gpkg `refs` table from another session's zip. Add it to step 7 and to the `test_import.py` case (a voided ref in refs.csv of a session not in the import).
3. **[Consequences, minor]** `gp.upsert` never deletes, and `import_refs_csv` keeps existing rows unchanged. A row imported before it was voided (a zip shared mid-session, then flagged) stays in the gpkg after a later import. Either delete the (session, n) rows that are now voided when importing (counted with the dropped ones) or state the limitation in the importer docstring.
4. **[PR A standalone, minor]** After PR A merges, a build of main still says 0.8.1 but writes the 29-column points.csv and the new refs.csv header. Label the format change in `logger/README.md` "from 0.9.0" so it matches the version B ships, and mark the `flag`/`unflag`/`photo moved` events as written from 0.9.0 (PR A has nothing that writes them).

### Summary

The revision addresses all four earlier must-fixes and the A/B split is sound: A changes no behaviour a user can trigger and its tests stand alone. The remaining items are small and can be fixed during implementation: drop the non-existent session-resume rule and its test, and make the importer's refs.csv path skip voided rows too.

### Recommended Actions

- [ ] Remove the session-resume paragraph and the "restart rebuild from file" `CoverageWorkerTest` case; say in the README that refs.csv is the only void state read back.
- [ ] Make `import_refs_csv` skip and count `void=1` rows; add that case to `test_import.py`.
- [ ] Delete now-voided (session, n) rows on re-import, or document that an earlier-imported row stays.
- [ ] Label the new columns and events in `logger/README.md` as from 0.9.0.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:33 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready, no must-fix; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implement PR A. Review notes into the handoff: drop the 'session resume' paragraph and the restart-from-file test (the phone never reopens a session; refs.csv is the only void state read back); import_refs_csv also skips and counts void=1 refs, with a test; host decision on minor 3: re-import DELETES (session, n) rows that are now voided, with a test; README labels the new columns and flag/unflag/photo moved events 'from 0.9.0'.

## Implementation
**Status**: complete
**When**: 2026-11-26 18:43 -05:00
**By**: Claude Code Agent (claude-opus)
**Branch**: feature/issue-27 at `17ede67`

PR A (plan steps 1-9). Owner veto during the pass, verbatim: "So flagged data gets deleted? I wonder if it should only be flagged, which is a common approach in multbeam sonar processing." It replaces the host decisions "importer drops voided rows" and "re-import deletes now-voided rows": nothing is deleted anywhere; readers filter by default and count.

- `3f400d4` PointRow: volatile `voided`/`voidReason`, columns `void,void_reason` (COLUMNS 29; 17- and 27-column rows read as not voided), `isLive()`. New `core/VoidRule` (UNDO_WINDOW_MS 8000, UNDO_ARM_MS 750, reasons, reason sanitising, photo target, lastLive/liveCount, photo row for a voided row, refs.csv row rewrite). PhotoRule `attachTo(PointRow, ns)` refuses a voided last capture; PoleCheck.matches skips voided refs.
- `89eecd8` Coverage.feeds() adds `!r.voided` (only change to Coverage.java); CoverageWorker.update gains a void-generation overload that forces a rebuild; MainActivity passes `sv.voidGeneration()` (one line). No coverage vectors regenerated.
- `df8ade9` Survey.voidCapture(n, reason, via) / unvoidCapture(n): refusals (photo row, already voided, ref/tilt running, via=undo past 8 s), events `flag`/`unflag`/`photo moved`, photos to nearest earlier live capture or a new photo row, lastCapture/captures from live rows, voidGeneration bump, capture notice and poleWarning cleared for a voided ref, pole check skipped if voided while queued. RefsStore.setVoid rewrites the (session, n) row in every refs.csv (tmp + rename) on the io thread after the append, then bumps refsGeneration; readAll skips voided rows. Nothing calls voidCapture yet (UI in PR B).
- `b487b69` Python, per the veto: importer keeps voided captures/refs with `void`, `void_reason` (captures, refs) and `n_voided` (sessions) columns, rewritten on every import so an unflag clears them; import_refs_csv carries and updates the flag both ways for refs.csv-sourced rows and counts them (`refs_csv_voided`). soundings.py: a voided capture stays a row with flag `void:<reason>`, use 0, count logged; `[soundings] include_voided` / `--include-voided` uses it; voided refs are not plumb references. export_labels.py leaves voided captures out by default (count via `stats`, printed by the CLI), `--include-voided` exports them with the void columns. config.example.toml: `include_voided`.
- `17ede67` logger/README.md: Flagged (void) captures section, columns and events labelled "from 0.9.0", points.csv not append-only (events.csv is the record), refs.csv only void state read back, no session-resume rule; docs/howto/ice-cube.md soundings flag line.

Tests (project pass, every hook ran on each commit):
- `cd logger && ./gradlew testDebugUnitTest --max-workers=4`: 176 tests, 0 failures (new VoidRuleTest; new cases in PointRowTest, PhotoRuleTest, PoleCheckTest, CoverageTest, CoverageWorkerTest).
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 96, OK (skipped=5) (new test_import `test_voided_captures_kept_and_flag_updates`: kept with reason, refs.csv void, unflag clears, export_labels default/include; new test_soundings `test_voided_capture_flagged_not_used_unless_included`).
- icecube/ not touched; its suite not run. C: 115 GB free before, 114 GB after.

Not done here: the issue comments for #20/#21/#23 (plan step 7/4) were not posted; the requirement under the veto is "keep void=1 rows, exclude by default, report the count, option to include; missing column means not voided; drop tilt_obs.csv leans of a voided n; ignore refs.csv void=1 as a visit". QGIS styling of voided captures (build_project.py) unchanged.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 18:48 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: changes-requested

**Branch**: feature/issue-27 at `102e6e3`
**Base**: main
**Depth**: Standard (reason: 25 files, about 1,250 lines across the logger app and the QGIS readers; data-format change)
**Must-fix**: 4 | **Suggestions**: 10
**Round**: 1 | **Ship**: continue (round 1: 4 must-fix; first round always re-reviews after fixes)

Checked against the owner's rule (his words: "So flagged data gets deleted? I wonder if it should only be flagged, which is a common approach in multbeam sonar processing."): nothing is deleted anywhere. The importer keeps voided rows, the flag follows re-import both ways, and soundings.py and export_labels.py exclude flagged rows by default, count them and can include them. Two places in soundings.py still let a flagged row change usable output, and the QGIS project draws flagged rows like live ones. Public-repo check: clean (fixtures use synthetic 10/20 degree or offset coordinates; no names, hosts or addresses). Tests, from the Claude adversarial pass: python qgis 96 OK (5 skipped); logger testDebugUnitTest passes.

Cross-model: codex complete (3 must-fix, all kept: anchors, verify_suspect, poleCheck race); gemini complete (4 must-fix: anchors and the poleCheck race kept; refs.csv order and headerless read moved to suggestions as edge cases).

### Findings
- [x] (must-fix) assign_anchors: a voided ref alone at its spot still makes a site (the `or g` fallback), and nearby good ground soundings join it as anchors and lose their ground sigma; leave `void:` rows out of `an` unless include_voided (Claude, codex, gemini) — `analysis/qgis/soundings.py:911-935`
- [x] (must-fix) verify_suspect: voided rows in a suspect pass add their dz to the pass's verification and can clear suspect_jump on live rows; leave void-flagged rows out of the members compared (codex) — `analysis/qgis/soundings.py:968-985`
- [x] (must-fix) build_project.py styles captures and refs with no use of `void`: flagged rows look like live ones in QGIS; draw them distinctly (hollow/grey or a rule-based category) and show void_reason in the map tip (host check, Claude) — `analysis/qgis/build_project.py:156,181`
- [x] (must-fix) poleCheck on the io thread checks `r.voided` once, then sets notice/poleWarning/poleWarningN and the alarm without the Survey lock; a voidCapture in between leaves a warning or alarm on a voided ref; `poleWarningN` is not volatile. Do the final update under the lock with a recheck (Claude, codex, gemini) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:501-530,564`
- [x] (suggestion) update_local's refs.site grouping includes voided refs, which can seed or chain sites that soundings uses for anchor names — `analysis/qgis/import_sessions.py:374`
- [x] (suggestion) import_refs_csv keeps the last file in input order per (session, n) and then sets void unconditionally, so an older refs.csv (pre-0.9.0 or a snapshot from before the flag) imported later un-flags a ref; prefer the row that has the void column, or the newest file — `analysis/qgis/import_sessions.py:333-348`
- [x] (suggestion) read_refs_csv: a headerless file, or one whose header was not upgraded, drops columns past the header, so void is lost — `analysis/qgis/logger_session.py:246-256`
- [x] (suggestion) With include_voided, soundings adds no flag, so the output loses the trace that a row was flagged; add a non-excluding marker (e.g. `voided_included`) — `analysis/qgis/soundings.py:648`
- [x] (suggestion) RefsStore.setVoid failing on every root leaves only a "(0 rows)" event; the ref still counts as a visit in later sessions. Warn when nothing was rewritten — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:79-88`
- [x] (suggestion) README says readers drop tilt_obs.csv leans of a voided n, but no tilt_obs.csv reader exists; say a future reader must — `logger/README.md`
- [x] (suggestion) When a voided ref's photos move, refs.csv keeps the old photo names (points.csv is updated); fix or note it — `logger/app/src/main/java/rink/icecube/logger/core/VoidRule.java:105`
- [x] (suggestion, PR B) MapView draws voided captures like live ones — `logger/app/src/main/java/rink/icecube/logger/MapView.java:481-515` (deferred: PR B (the logger UI build: MapView drawing / UNDO bar); out of scope for this PR)
- [x] (suggestion, PR B) Check that `elapsedNs` is the capture's completion time before the UNDO bar relies on the 8 s window for 30 s refs and tilt sets — `logger/app/src/main/java/rink/icecube/logger/Survey.java:553` (deferred: PR B (the logger UI build: MapView drawing / UNDO bar); out of scope for this PR)
- [x] (suggestion) Minor: void_reason is not stripped in the soundings flag; export_labels prints no count with --include-voided and writes "" not 0 for a pre-0.9.0 gpkg — `analysis/qgis/soundings.py:649`, `analysis/qgis/export_labels.py:88`

## Implementation
**Status**: complete
**When**: 2026-11-26 18:54 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-27 at `b334922`
**Addressed**: Local Review (Pre-Push) at `102e6e3` (2026-11-26 18:48 -05:00)
**Commits**: ad72db0, 30437b7, 5a4083e, 4cfc6ed, 8c5ffa2, bd5bd92, 98157a2, ab97749, b334922

### Actions
- [x] (must-fix) assign_anchors: void-flagged refs neither make nor join a site — `analysis/qgis/soundings.py` (ad72db0, test added)
- [x] (must-fix) verify_suspect: void-flagged rows left out of the suspect comparison — `analysis/qgis/soundings.py` (ad72db0, test added)
- [x] (must-fix) build_project.py: void=1 captures a grey x category "Flagged (void)", refs a grey dashed ring, void_reason in map tips; pre-0.9.0 gpkg unchanged — `analysis/qgis/build_project.py` (30437b7; QGIS not installed here, compile-checked only)
- [x] (must-fix) Survey.poleCheck: void recheck and notice/warning/alarm set under the Survey lock; poleWarningN volatile — `logger/.../Survey.java` (5a4083e)
- [x] (suggestion) update_local: void refs out of site grouping, their site cleared — `analysis/qgis/import_sessions.py` (bd5bd92, test added)
- [x] (suggestion) import_refs_csv: copies ordered by zip entry / file mtime; flag from the newest copy with a void column — `analysis/qgis/import_sessions.py`, `logger_session.py` (bd5bd92, test added)
- [x] (suggestion) read_refs_csv: rows longer than a headerless/old header read by the logger's column order, void kept; `void_known` marks rows with no void value (not voided) — `analysis/qgis/logger_session.py` (bd5bd92, test added)
- [x] (suggestion) include_voided rows carry the non-excluding flag `voided_included` — `analysis/qgis/soundings.py` (ad72db0)
- [x] (suggestion) RefsStore.setVoid reaching no file logs an `error:` event — `logger/.../Survey.java` refsVoid (4cfc6ed)
- [x] (suggestion) README: no tilt_obs.csv reader exists yet; a future one must drop voided leans — `logger/README.md` (8c5ffa2)
- [x] (suggestion) refs.csv photo names: documented as kept as appended, points.csv is the record — `logger/README.md`, `core/VoidRule.java` (8c5ffa2)
- [x] (suggestion, PR B) MapView drawing of voided captures — (deferred: PR B)
- [x] (suggestion, PR B) elapsedNs as completion time for the UNDO window — (deferred: PR B)
- [x] (suggestion) void_reason stripped in soundings (ad72db0); export_labels counts flagged with --include-voided and writes void 0 for a pre-0.9.0 gpkg (98157a2)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 99 tests, OK (skipped=5)
- `cd logger && ./gradlew testDebugUnitTest --max-workers 4`: BUILD OK (exit 0); C: 113.9 GB free before the run
- Docs: `docs/howto/ice-cube.md` updated (b334922). Untracked review-*-prompt/findings.md removed.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 19:00 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: changes-requested

**Branch**: feature/issue-27 at `17bc9a3`
**Base**: main
**Depth**: Standard (reason: 26 files, about 1,000 lines of code across the logger app and the QGIS readers; data-format change; fix-round re-review on the whole diff)
**Must-fix**: 1 | **Suggestions**: 9
**Round**: 2 | **Ship**: recommended — round 2: 1 mechanical must-fix (prev 4), not rising; fix and ship rather than another full round

Round-1 must-fixes all verified fixed: assign_anchors and verify_suspect skip `void:` rows (tests added); build_project.py draws flagged captures as a grey x "Flagged (void)" and flagged refs as a grey dashed ring, with void_reason in map tips (compile-checked only, QGIS not installed); Survey.poleCheck rechecks `r.voided` and sets notice/warning/alarm under the lock, poleWarningN volatile. Checked against the owner's rule (his words: "So flagged data gets deleted? I wonder if it should only be flagged, which is a common approach in multbeam sonar processing."): nothing is deleted on the phone or in the gpkg; one place in soundings.py still lets a flagged capture remove a valid sounding (must-fix below). Public-repo check: clean (synthetic 10/20 degree fixtures; no names, hosts or addresses). Tests (Claude adversarial pass): python qgis 99 OK (5 skipped); logger testDebugUnitTest exit 0. Static analysis: no Python linter installed (py_compile clean); Java compiled by the gradle test run.

Cross-model: codex complete (1 must-fix kept: dup_capture windows; 2 suggestions); gemini complete (3 "must-fix" downgraded to suggestions: refs.csv flag cleared only when the newest 0.9.0 copy is no longer among the inputs, void "" for NULL-upgraded rows, zip-time ordering; cue-under-lock dismissed, cue() already runs under the Survey lock in mark/onEpoch; plan-drift note kept as a suggestion).

### Findings
- [x] (must-fix) clist_windows builds the capture windows from every capture, voided ones included, so an accidental MARK that was flagged still marks a real pole stop in its window `dup_capture` and drops it from use; skip `c["void"]` captures unless include_voided, with a test (codex, Claude) — `analysis/qgis/soundings.py:733-742`
- [x] (suggestion) assign_anchors' snap loop can turn a voided ground capture into an anchor of a site (class, anchor, sigma_ground changed; use stays 0); skip is_voided rows there (codex, gemini) — `analysis/qgis/soundings.py:948-956`
- [x] (suggestion) import_refs_csv's UPDATE sets void=0 for a refs.csv-sourced row when no copy in this run has a void column (the 0.9.0 copy no longer among the inputs); update only keys present in `flags` (gemini) — `analysis/qgis/import_sessions.py:356-358`
- [x] (suggestion) export_labels.cell writes "" for void on rows whose void is NULL (sessions imported before 0.9.0 and skipped as unchanged after the column was added); write 0 when r["void"] is None too (gemini) — `analysis/qgis/export_labels.py:94-99`
- [x] (suggestion) refs_csv_dated orders zip copies by time.mktime of the DOS local time against folder st_mtime; a timezone difference can misorder mixed zip/folder copies, and a zero DOS date may raise; catch errors to None and say "local time" in the docstring (gemini) — `analysis/qgis/logger_session.py:131-135`
- [x] (suggestion) QGIS plugin Export calls export_labels.export without stats, so flagged captures are left out with no count in the message bar; the plugin import summary also ignores voided counts (Claude) — `analysis/qgis/plugin/icecube_qgis/plugin.py:317`
- [x] (suggestion) poleCheck is queued after onEpoch's synchronized block; a void in that gap reaches the io queue before append, so setVoid finds no row and logs a false `error:` event (refs.csv ends right). Queue poleCheck inside the lock; reachable once PR B's UNDO exists (Claude) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:236-238,615` (deferred: to PR B (owner): the false error event needs UNDO to be reachable; queue poleCheck inside the lock there)
- [x] (suggestion) VOID_TIP puts void_reason into map-tip HTML unescaped; VoidRule.reason does not strip `<>&`; escape it in the tip or the sanitiser (gemini) — `analysis/qgis/build_project.py:158-159`
- [x] (suggestion) plan.md's Files to Change and step 7 still say the importer drops voided rows; update them to the keep-and-flag model of the owner's veto (gemini, plan drift) — `.agent/work-plans/issue-27/plan.md:150`
- [x] (suggestion, note) a photo planned for a capture flagged while the camera is open keeps its `photo_<voidedN>_...` file name on the row it moves to; document or log the original n (Claude) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:638-649` (deferred: to PR B (owner): document or log the original n of a photo moved off a capture flagged while the camera is open)

## Implementation
**Status**: complete
**When**: 2026-11-26 19:04 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-27 at `9b39fc4`
**Addressed**: Local Review (Pre-Push) at `17bc9a3` (2026-11-26 19:00 -05:00)
**Commits**: 10856ce, 870dbc6, 60051c0, 522ab64, e47f89c, d120348, 8ac0791, 37d1de1, 9b39fc4

### Actions
- [x] (must-fix) clist_windows skips voided captures unless include_voided, with a test — `analysis/qgis/soundings.py:733`
- [x] assign_anchors' snap loop never makes a voided ground capture an anchor, with a test — `analysis/qgis/soundings.py`
- [x] import_refs_csv updates void only for rows a copy in this run has a flag for; test of a later pre-0.9.0-only run — `analysis/qgis/import_sessions.py`
- [x] export_labels writes void 0 for a NULL void, with a test — `analysis/qgis/export_labels.py`
- [x] refs_csv_dated: zip_entry_mtime uses the UTC extended timestamp when present, DOS local time otherwise, None for a zero date, with a test — `analysis/qgis/logger_session.py`
- [x] QGIS plugin Export reports the flagged count (core.export_text); the import summary counts flagged captures and refs — `analysis/qgis/plugin/icecube_qgis/plugin.py`, `core.py`
- [x] poleCheck queued after the lock: false error event — `logger/.../Survey.java:236-238,615` (deferred: to PR B, reachable once UNDO exists)
- [x] void_reason escaped (& < >) in the map tip, with a test — `analysis/qgis/build_project.py:158`
- [x] plan.md updated to the keep-and-flag model — `.agent/work-plans/issue-27/plan.md`
- [x] photo file name keeps the voided n — `logger/.../Survey.java:638-649` (deferred: to PR B, document or log the original n)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 103 tests, OK (skipped=5)
- No Java changed: Gradle unit tests not run. No pre-commit config in this repo.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 19:06 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: approved

**Branch**: feature/issue-27 at `3e9388d`
**Base**: main
**Depth**: Standard (reason: round 3 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 1
**Round**: 3 | **Ship**: recommended — no must-fix findings

Scope: the round 2 findings and the fix diff 17bc9a3..3e9388d (12 files, +193 -27). All 8 addressed round-2 items verified in code: clist_windows skips voided captures unless include_voided (call site passes it, the unused `ep` parameter is gone, no other callers); assign_anchors' snap loop skips is_voided rows; import_refs_csv's void UPDATE only for keys in `flags`; export_labels.cell writes 0 for a NULL void; zip_entry_mtime reads the 0x5455 UTC timestamp (flag bit 0) and returns None for a zero DOS date; plugin Export reports the flagged count via core.export_text and the import summary counts flagged captures and refs (keys `voided` and `refs_csv_voided` exist in import_sessions' result); VOID_TIP escapes & first, then < >; plan.md now describes keep-and-flag. The two deferrals to PR B (poleCheck ordering, photo name of a moved capture) are recorded. Owner's rule (his words: "So flagged data gets deleted? I wonder if it should only be flagged, which is a common approach in multbeam sonar processing."): the fixes only narrow what a flagged capture can affect; nothing is deleted. Public-repo check: clean (synthetic fixtures only). Tests: `python3 -m unittest discover -s analysis/qgis/tests` Ran 103, OK (skipped=5). No Java changed since round 2.

Cross-model: skipped (round-3 convergence check, per handoff).

### Findings
- [ ] (suggestion) import_refs_csv returns the flagged count from this run's copies only, so a later run with only pre-0.9.0 copies reports 0 flagged while the stored flag stays 1; the import summary's refs.csv flagged count can understate (count from the table, or say "in this run") — `analysis/qgis/import_sessions.py:362`

## Checkpoint
**Status**: complete
**When**: 2026-11-26 19:06 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push review approved at round 3, no must-fix; orchestrator table allows publish)
**After**: publish
**Decision**: publish

PR A of two (Part of #27). Round 3 suggestion (refs flag count from this run only) left for triage/PR B.

## Integrated Review
**Status**: complete
**When**: 2026-11-26 19:07 -05:00
**By**: Claude Code Agent (claude-opus)
**Reviews-read**: none

**PR**: #33 at `f3d76a0`
**Sources**: 2 (Local Review (Pre-Push) @ `3e9388d` (covers f3d76a0 via bookkeeping), CI rollup)
**Cross-source confirmations**: 0
**CI**: none

No hosted reviews or PR comments yet (Copilot not requested; out of quota today). Rounds 1 and 2 of the local review are closed (all boxes ticked; three items deferred to PR B by the owner: MapView drawing, elapsedNs/UNDO window, poleCheck ordering and the moved-photo name). The one open item from round 3 is confirmed against the code: `import_refs_csv` sums `d["void"]` over this run's rows, and a row with no void column in this run gets `void=0` in `out` while the UPDATE (rightly) leaves the stored flag alone, so the returned count can understate. It only affects a summary count, not stored data; not a merge blocker.

### Findings
- [x] (suggestion, Local Review @ `3e9388d`) import_refs_csv's flagged count comes from this run's copies only; a later run with only pre-0.9.0 copies reports 0 flagged while the stored flag stays 1. Count flagged rows from the refs table for the imported keys, or label the summary count "in this run" — `analysis/qgis/import_sessions.py:362`

### False positives

## Checkpoint
**Status**: complete
**When**: 2026-11-26 19:07 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (one small suggestion open; orchestrator table allows address at findings)
**After**: findings
**Decision**: address

Fix the open suggestion: import_refs_csv's flagged count comes from the refs table (so a run with only pre-0.9.0 copies doesn't report 0 while a stored flag stays 1), or is labelled 'flagged in this run'.

## Implementation
**Status**: complete
**When**: 2026-11-26 19:09 -05:00
**By**: Claude Code Agent (claude-sonnet)

**PR**: #33 at `8619946`
**Addressed**: Integrated Review at `f3d76a0` (2026-11-26 19:07 -05:00)
**Commits**: 8619946

### Actions
- [x] import_refs_csv flagged count now read from the refs table (source refs.csv, void=1) for the imported keys; test extended for a later pre-0.9.0-only run — `analysis/qgis/import_sessions.py:362`

Tests: `python3 -m unittest discover -s analysis/qgis/tests` OK (103, 5 skipped).

## Local Review
**Status**: complete
**When**: 2026-11-26 19:16 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: approved

**PR**: #33 at `eae8b68`
**Depth**: Deep (reason: 31 files, about 1,900 lines across the logger app and the QGIS readers; PR-mode round 1, so the round-3 convergence exception does not apply)
**Must-fix**: 0 | **Suggestions**: 7

PR-mode round 1 (no earlier PR-mode Local Review entry); full whole-diff review, cross-model included. The fix in 8619946 is correct: the per-row parameterised SELECT runs after the upsert and void UPDATE on the same connection, `coalesce` covers NULL void in older gpkgs, and the test pins the pre-0.9.0-only run at (1, 1). Static: no linter installed in this environment (ruff/flake8/pyflakes missing; no project lint config); py_compile on the changed Python is clean; Java not linted. Tests: `python3 -m unittest discover -s analysis/qgis/tests` Ran 103, OK (skipped=5). Public-repo check: fixtures use synthetic coordinates (10/20 degrees). No PR comments, reviews or CI checks. Codex rated items 2 and 5 below must-fix; downgraded because nothing calls voidCapture/setVoid yet (the UI is PR B), the zip/dir signature predates this PR, and setVoid keeps lines it cannot parse. Dropped: gemini's concurrent toCsv read (no caller yet), parse() of a 28-column row (speculative), and unsplit EXCLUDING checks (every site already splits on ':').

Cross-model: gemini completed (7 findings), codex completed (3 findings).

### Findings
- [ ] (suggestion) import_refs_csv: rows seen counts every refs.csv row, but the flagged count covers only rows stored with source='refs.csv', so a voided ref of a session imported from points.csv counts as seen but not as flagged (reproduced: returns (1, 0)); document the choice or drop the source filter, and pin it with a test (Claude adversarial + gemini) — `analysis/qgis/import_sessions.py:367`
- [ ] (suggestion) The re-import signature is name+size only, so a points.csv whose void flag flips 0->1 with an empty reason keeps its size and the re-import is skipped; add mtime or a hash before PR B wires voidCapture (codex) — `analysis/qgis/logger_session.py:147`
- [ ] (suggestion) The newest-copy rule holds only within a run: a later run whose only copy is an older 0.9.0 refs.csv (void column, void=0) clears a flag stored from a newer copy, because the stored row keeps no mtime (gemini) — `analysis/qgis/import_sessions.py:360`
- [ ] (suggestion) Owner call: a voided capture makes no capture window, so stops_from_epochs can find the same stationary period and emit a use=1 pole-stop sounding at a capture voided as 'moved' or a bad ref (gemini) — `analysis/qgis/soundings.py:739`
- [ ] (suggestion) RefsStore.setVoid rewrites line by line, but PointRow.text() quotes fields holding line breaks, so a multi-line record would be handled as fragments; forbid line breaks in text() or read whole records (codex) — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:95`
- [ ] (suggestion) setVoid reports success when one refs.csv copy was updated and another could not be written, and readAll can still serve the stale unvoided copy (codex) — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:86`
- [ ] (suggestion) import_session stores void_reason even when void=0, unlike import_refs_csv's `reason if void else None` (gemini) — `analysis/qgis/import_sessions.py:259`

## Integrated Review
**Status**: complete
**When**: 2026-11-26 19:20 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #33 at `4a7db43`
**Sources**: 2 (Local Review @ `eae8b68` (covers 4a7db43 via bookkeeping), CI rollup: no checks configured)
**Cross-source confirmations**: 0
**CI**: all-pass (no CI checks on this repository)

Owner rule applied: "So flagged data gets deleted? I wonder if it should only be flagged, which is a common approach in multbeam sonar processing." The PR keeps voided captures and refs as flagged rows end to end; finding 3 below is where a flag can be lost downstream. The stale pre-push finding (flagged count from this run's copies only) is addressed by 8619946, which counts from the refs table.

### Findings
- [x] (suggestion, Local Review @ eae8b68; needs owner's call) A voided capture makes no capture window, so stops_from_epochs can find the same stationary period and emit a live use=1 pole-stop sounding at a capture voided as 'moved' or a bad ref. This bears on what a flag means under the owner rule: options are to keep the voided window and emit the stop flagged (use=0, reason naming the voided capture), or leave it as is so an accidental MARK does not hide a real stop. Do not change without the owner's decision — `analysis/qgis/soundings.py:739`
- [ ] (suggestion, Local Review @ eae8b68) setVoid counts success when one refs.csv copy was rewritten and another could not be, and readAll still reads the stale unvoided copy, so the voided ref can come back in the earlier-refs layer; report a partial result (rows changed vs copies holding the row) in the event and Outcome, or make readAll take the void flag from any copy — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:86`
- [x] (suggestion, Local Review @ eae8b68) import_refs_csv counts every refs.csv row as seen but the flagged count only rows stored with source='refs.csv', so a voided ref of a session imported from points.csv is seen but not counted flagged (reproduced: (1, 0)); document the choice in the docstring or drop the source filter, and pin it with a test — `analysis/qgis/import_sessions.py:367`
- [x] (suggestion, Local Review @ eae8b68) The newest-copy rule holds only within a run: a later run whose only copy is an older 0.9.0 refs.csv (void column, void=0) clears a flag stored from a newer copy; the code comment claims only pre-0.9.0 copies are harmless, so either store the copy's mtime with the row and skip older copies, or document the limit next to that comment — `analysis/qgis/import_sessions.py:360`
- [ ] (suggestion, Local Review @ eae8b68) RefsStore.setVoid and readAll read refs.csv line by line while PointRow.text() quotes fields holding line breaks; no field gets a line break today (reasons are sanitized by VoidRule.reason, notes are program-made), so make text() replace line breaks with spaces to keep one record per line — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:95`
- [x] (suggestion, Local Review @ eae8b68) import_session stores void_reason even when void=0, unlike import_refs_csv's `reason if void else None`; use the same rule — `analysis/qgis/import_sessions.py:259`

### False positives
- (Local Review @ eae8b68, codex) A points.csv whose void flag flips with an empty reason keeps its size, so the name+size re-import signature skips it — the directory signature includes events.csv's size, and Survey.voidCapture / unvoidCapture always append a "flag n=… reason=… via=…" / "unflag n=…" event (a Survey exists only with an open session, so the event is written); the reason is never empty either (VoidRule.reason returns "other"). A zip is rebuilt with that events.csv, so its size changes too.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 19:19 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (address at findings; finding 1's meaning decided by the owner)
**After**: findings
**Decision**: address

Owner's answer on finding 1 (AskUserQuestion "What should flagging a capture do to the still-pole period around it?"): "Depends on reason (Recommended)" — 'accidental' flags only the capture; 'moved', 'bad pole placement' or 'other' also flag the stop around it (soundings emits that stop with use=0 and a reason naming the voided capture). Address findings 2-6 as suggested.
