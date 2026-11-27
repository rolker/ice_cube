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
- [x] (suggestion, Local Review @ eae8b68) setVoid counts success when one refs.csv copy was rewritten and another could not be, and readAll still reads the stale unvoided copy, so the voided ref can come back in the earlier-refs layer; report a partial result (rows changed vs copies holding the row) in the event and Outcome, or make readAll take the void flag from any copy — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:86`
- [x] (suggestion, Local Review @ eae8b68) import_refs_csv counts every refs.csv row as seen but the flagged count only rows stored with source='refs.csv', so a voided ref of a session imported from points.csv is seen but not counted flagged (reproduced: (1, 0)); document the choice in the docstring or drop the source filter, and pin it with a test — `analysis/qgis/import_sessions.py:367`
- [x] (suggestion, Local Review @ eae8b68) The newest-copy rule holds only within a run: a later run whose only copy is an older 0.9.0 refs.csv (void column, void=0) clears a flag stored from a newer copy; the code comment claims only pre-0.9.0 copies are harmless, so either store the copy's mtime with the row and skip older copies, or document the limit next to that comment — `analysis/qgis/import_sessions.py:360`
- [x] (suggestion, Local Review @ eae8b68) RefsStore.setVoid and readAll read refs.csv line by line while PointRow.text() quotes fields holding line breaks; no field gets a line break today (reasons are sanitized by VoidRule.reason, notes are program-made), so make text() replace line breaks with spaces to keep one record per line — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:95`
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

## Implementation
**Status**: complete
**When**: 2026-11-26 19:25 -05:00
**By**: Claude Code Agent (claude-opus)

**PR**: #33 at `a7709a6`
**Addressed**: Integrated Review at `4a7db43` (2026-11-26 19:20 -05:00)
**Commits**: 0b43d6b, 5622e9a, d804f99, 0e0a7cb, a7709a6

### Actions
- [x] Owner's call "depends on reason": a capture voided as moved / bad pole placement / other also flags its pole stop (kept, use 0, flag `void_stop:c<n>:<reason>`, counted in the log, no anchor site or suspect comparison); accidental flags only the capture; include_voided brings the capture back and its stop is a dup_capture; tests for moved, bad pole placement, accidental, include_voided and the window rule — `analysis/qgis/soundings.py:776`
- [x] RefsStore.setVoid reports copies holding / rewritten / unreadable and the event is an error on a partial write (VoidRule.refsVoidEvent); readAll drops a (session, n) voided in any copy (VoidRule.liveRefs). The write runs on the io thread after the Outcome is returned, so the report is in events.csv, not the toast — `logger/app/src/main/java/rink/icecube/logger/RefsStore.java:86`
- [x] import_refs_csv flagged count kept to rows it stores (source refs.csv) to avoid double counting a session's own ref; docstring says so and a test pins (1, 0) — `analysis/qgis/import_sessions.py:332`
- [x] refs gains `void_copy_mtime`; a later run changes a stored flag only from a copy at least that new (unknown time changes only an unstamped flag); tests for an older 0.9.0 copy, an unknown-time copy and a newer copy — `analysis/qgis/import_sessions.py:360`
- [x] PointRow.text() writes line breaks as spaces (README updated, test) — `logger/app/src/main/java/rink/icecube/logger/core/PointRow.java:170`
- [x] import_session stores void_reason only when void=1, for captures and refs (test) — `analysis/qgis/import_sessions.py:259`

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 108 tests, OK (skipped=5)
- `./gradlew testDebugUnitTest --max-workers 4`: 179 tests, 0 failures (C: 114 GB free)

## Implementation
**Status**: complete
**When**: 2026-11-26 19:29 -05:00
**By**: Claude Code Agent (claude-opus)

**PR**: #33 at `5af2b92`
**Commits**: 5af2b92 (merge of origin/main 738af9b, PR #32 / issue 9)

### Actions
- [x] Merged origin/main; conflicts resolved keeping both features. `captures` = MEAS + ENU + source/elapsed_ns/note/label + LOGGER_06 + VOID; refs keep VOID + void_copy_mtime; sessions keep n_voided and schema_version — `analysis/qgis/import_sessions.py:105`
- [x] run(): #9's stale-session log/`stale` return key and #27's `refs_csv_voided` both kept — `analysis/qgis/import_sessions.py:670`
- [x] read_points returns both the 0.6 columns and void/void_reason (docstring updated) — `analysis/qgis/logger_session.py:265`
- [x] soundings.logger_source: one SELECT with tilt columns (NULL fallback) and void columns (coalesce, 0 fallback for a gpkg without them); #9's tilt warnings and #27's void count both kept — `analysis/qgis/soundings.py:623`
- [x] SCHEMA_VERSION 4 (captures/refs gain the void flag). The single SCHEMA_MIN_APP became SCHEMA_APP per step {3: 0.6, 4: 0.9} with schema_min_app(schema): a session at schema 3 is re-read only from a 0.9+ (or unknown, points.csv) app; one at schema 2 still from 0.6 — `analysis/qgis/import_sessions.py:87`
- [x] New test test_schema3_reread_fills_void_only_for_09_sessions — `analysis/qgis/tests/test_import.py:472`
- [x] Test conflicts: both sides' tests kept (import line merged with `_i` and `zip_entry_mtime`); logger/README.md paragraph combined (importer carries 0.6 and void columns)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 125 tests, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 68 tests, OK
- `./gradlew testDebugUnitTest --max-workers 4`: 179 tests, 0 failures (C: 114 GB free)

## Local Review
**Status**: complete
**When**: 2026-11-26 19:39 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**PR**: #33 at `9b5a108`
**Depth**: Deep (reason: fix-round re-review on the whole diff, 31 files +2192/-160, cross-layer logger + importer + processing; merge of main 5af2b92 with SCHEMA_VERSION 4)
**Must-fix**: 1 | **Suggestions**: 7

Merge resolution checked: both features intact (0.6 method/tilt columns and per-session re-read; void flag). schema_min_app correct for schema 3 from 0.6-0.8 (marked current), schema 2/NULL from 0.9 (re-read), unknown app points.csv (re-read), snapshot guard kept; a real upgrade from a main-built gpkg (adversarial, in scratch) filled void, n_voided and refs.csv void. Tests: analysis/qgis 125 OK (5 skipped), icecube 68 OK, logger JVM unit tests OK. Reviewers: Claude adversarial, gemini (complete), codex (complete).

### Findings
- [x] (must-fix) A capture voided as 'accidental' that has photos gets a photo row (VoidRule.photoRowFor) with the voided capture's utc; clist_windows makes a window for that photo row, so the pole stop is marked dup_capture (use 0), against the owner's "depends on reason" rule. Skip such photo rows (or photo rows at a voided capture's time) in clist_windows, with a test (gemini) — `analysis/qgis/soundings.py:821`
- [x] (suggestion) stranded_06 compares an unknown-app session's schema with SCHEMA_VERSION (now 4) though the 0.6 columns came in schema 3: a false re-import warning for an unknown-app points.csv session at schema 3; compare with 3 via a named constant (Claude, merge drift) — `analysis/qgis/soundings.py:659`
- [x] (suggestion) The importer takes a ref's flag from the newest copy with a void column; VoidRule.liveRefs drops a ref voided in any copy. After a partial unflag (or a missed copy later appended to) the gpkg and the phone disagree; align the rules or document the difference (Claude + codex) — `analysis/qgis/import_sessions.py:381`
- [x] (suggestion) logger/README.md still describes only the "no refs.csv rewritten" error; add the partial-write event form (k of m copies, unreadable count) and that readers drop a ref voided in any copy (Claude) — `logger/README.md:209`
- [x] (suggestion) find_sessions keeps one source per session name before refs_csv_dated runs, so a refs.csv copy in a second export of the same session never reaches the newest-copy rule; collect refs.csv from every found source or document it (codex) — `analysis/qgis/import_sessions.py:623`
- [x] (suggestion) Sessions marked current at schema 4 without a re-read keep n_voided NULL; set 0 when marking them current (codex) — `analysis/qgis/import_sessions.py:615`
- [x] (suggestion) test_schema3_reread_fills_void_only_for_09_sessions fakes schema 3 by NULLing columns; add a case with a gpkg lacking the void/void_copy_mtime/n_voided columns and assert n_voided and a refs.csv ref's void are filled (Claude) — `analysis/qgis/tests/test_import.py:472`
- [x] (suggestion) When the planned photo target is flagged while the camera is open, the fallback checks only lastCapture; an earlier live capture within 60 s is missed if a newer capture was made meanwhile (PR B scope possible) (codex) — `logger/app/src/main/java/rink/icecube/logger/Survey.java:643` (deferred: host decision: the camera-target fallback goes to PR B)

## Implementation
**Status**: complete
**When**: 2026-11-26 19:49 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**PR**: #33 at `183133c`
**Addressed**: Local Review at `9b5a108` (2026-11-26 19:39 -05:00)
**Commits**: 58fe15b, 70fc8b5, bb48aa6, ee35d62, 603c8b1, d9a83c8, 183133c

### Actions
- [x] (must-fix) A voided capture's photo row (same utc, VoidRule.photoRowFor) makes no capture window, so an accidental capture with photos no longer marks the real pole stop dup_capture; tests for accidental-with-photo-row (full build, include_voided too) and the window rule — `analysis/qgis/soundings.py:clist_windows`
- [x] stranded_06 compares with SCHEMA_TILT = 3 (the schema of the 0.6 columns), not SCHEMA_VERSION; test covers schema 3 and 4 — `analysis/qgis/soundings.py:659`
- [x] Host decision applied: the phone (VoidRule.liveRefs, now taking copies with file times; PointRow.voidKnown) and the importer (import_refs_csv, within a run and against a stored flag) both take a ref's flag from the newest copy with a void value, ties resolve to flagged, a pre-0.9.0 row never decides; tests both sides; documented in import_sessions docstrings and logger/README.md; partial-write event text updated — `analysis/qgis/import_sessions.py:381`, `logger/.../core/VoidRule.java`
- [x] logger/README.md documents the partial-write event form (k of m copies, unreadable count) and its effect — `logger/README.md:209`
- [x] find_sessions keeps set-aside copies in SessionSource.others; the importer reads refs.csv from every export (refs_csv_copies); test with two exports — `analysis/qgis/logger_session.py:find_sessions`, `analysis/qgis/import_sessions.py:623`
- [x] Sessions marked current without a re-read get n_voided coalesce(n_voided, 0) — `analysis/qgis/import_sessions.py:615`
- [x] New test: schema-3 gpkg lacking void/void_reason/void_copy_mtime/n_voided upgrades (n_voided 1 and 0, captures void, refs.csv ref void filled) — `analysis/qgis/tests/test_import.py`
- [x] Camera-target fallback misses an earlier live capture — `logger/.../Survey.java:643` (deferred: host decision, PR B scope)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 130 tests, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 68 tests, OK
- `./gradlew testDebugUnitTest --max-workers 4`: 182 tests, 0 failures, 0 errors (C: 114 GB free)
- Not pushed; the host owns the push.

## Local Review
**Status**: complete
**When**: 2026-11-26 19:55 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**PR**: #33 at `cac6b56`
**Depth**: Deep (reason: round 3 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 0

PR-mode round 3: narrow check of the round-2 Local Review (@ 9b5a108) findings and the fix diff 02c804f..cac6b56. Cross-model: skipped (round-3 convergence check, per handoff). All seven addressed items verified in code and tests: voided capture's photo row (same utc) makes no window, so an accidental capture with photos leaves its stop live, per the owner's "depends on reason" rule; stranded_06 compares with SCHEMA_TILT = 3; phone (VoidRule.liveRefs with RefsCopy mtimes, PointRow.voidKnown) and importer (within a run and against void_copy_mtime in SQL) apply the same newest-copy rule, ties flagged, pre-0.9.0 rows never decide; README documents the partial-write event; refs.csv of every export reaches the rule (find_sessions others, refs_csv_copies, deduped and closed); n_voided coalesced to 0 when marking current; schema-3 gpkg lacking the void columns upgrades. The camera-target fallback stays deferred to PR B (host decision). Flags stay flags end to end (owner rule). Noted, not a finding: under the aligned rule a copy a partial void did not reach brings the old flag back if a ref is later appended to it; this follows the host decision and is documented in logger/README.md and the error event. Checks: `python3 -m unittest discover -s analysis/qgis/tests` Ran 130, OK (skipped=5); `./gradlew testDebugUnitTest --max-workers 4 --tests 'rink.icecube.logger.core.*'` OK. No PR comments, reviews or CI checks.

### Findings
- [ ] No issues found. LGTM.

## Integrated Review
**Status**: complete
**When**: 2026-11-26 19:52 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #33 at `cac6b56`
**Sources**: 2 (Local Review @ `cac6b56` (round 3, approved, 0 findings), CI rollup: no checks configured)
**Cross-source confirmations**: 0
**CI**: all-pass (no CI checks on this repository)

No hosted reviews, no PR conversation comments, no inline comments. The round-3 Local Review at the PR head verified every item the earlier rounds and the Integrated Review @ 4a7db43 closed, and found nothing new. The two stale entries the sources helper lists (Local Review (Pre-Push) @ 3e9388d, Local Review @ eae8b68) were ruled on by the Integrated Reviews @ f3d76a0 and @ 4a7db43 and are not re-listed. Owner rule holds: voided captures and refs stay flagged rows end to end. Deferred to PR B by host decision: the camera-target fallback.

### Findings

### False positives

## Local Review
**Status**: complete
**When**: 2026-11-26 19:56 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**PR**: #33 at `cda9e0f`
**Depth**: Deep (reason: cold read, cross-model only: codex, per handoff)
**Must-fix**: 0 | **Suggestions**: 2

### Findings
- [ ] (suggestion) DOS-time zip entries are read in the importing machine's zone, so refs.csv copies made in another zone can misorder by the offset and pick the wrong void flag; documented in the docstring, low risk while phone and PC share a zone (Codex rated must-fix; lead downgraded) — `analysis/qgis/logger_session.py:176`
- [ ] (suggestion) clist_windows skips any live photo row whose utc (1 s resolution) equals a voided capture's, so a real PHOTO pressed in that same second loses its window; match the moved-photo row by its note ("photos of voided n=") too — `analysis/qgis/soundings.py:821`

## Integrated Review
**Status**: complete
**When**: 2026-11-26 19:57 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: 6082092363

**PR**: #33 at `472e68b`
**Sources**: 3 (Local Review @ `cda9e0f` (covers `472e68b` via bookkeeping; Codex cold read, approved, 2 suggestions), Copilot @ `cda9e0f` (review 6082092363: unable to review, insufficient quota; no findings), CI rollup: no CI checks configured (the only check-run, copilot-pull-request-reviewer, is the same quota failure))
**Cross-source confirmations**: 0
**CI**: all-pass (no CI checks on this repository)

No human reviews, no conversation comments, no inline comments. Both Local Review suggestions verified against the code at head and carried forward as valid. The stale entries the sources helper lists (Local Review (Pre-Push) @ 3e9388d, Local Review @ eae8b68) were ruled on by the Integrated Reviews @ f3d76a0 and @ 4a7db43 and are not re-listed. Owner rule holds: voided captures and refs stay flagged rows end to end.

### Findings
- [ ] (suggestion, Local Review @ cda9e0f) `zip_entry_mtime` falls back to the DOS date/time read with `time.mktime` in the importing machine's zone; the logger's zips carry no 0x5455 extended timestamp, so refs.csv copies zipped on a phone in another zone misorder by the offset and the newest-copy rule can pick the wrong void flag. Documented in the docstring; low risk while phone and PC share a zone. Fix options: have the logger write the extended-timestamp field, or treat copies within the max zone offset as ties (flagged) — `analysis/qgis/logger_session.py:176`
- [ ] (suggestion, Local Review @ cda9e0f) `clist_windows` skips any live photo row whose `utc` (1 s resolution) equals a voided capture's, so a real PHOTO pressed in the same second as a voided capture loses its window. Confirmed the logger's moved-photo row carries `note = "photos of voided n=<n>"` (`VoidRule.photoRowFor`, VoidRule.java:93); require that note prefix as well as the utc match, and add a test with a real photo in the same second — `analysis/qgis/soundings.py:821`

### False positives

## Checkpoint
**Status**: complete
**When**: 2026-11-26 20:28 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: owner
**After**: findings
**Decision**: merge

Owner's answer to "Merge now and fix F1/F2 in part B, or fix them first?": "Merge, fix in part B (Recommended)". F1 (zip DOS times read in the PC's time zone) and F2 (a real PHOTO in the same second as a voided capture's moved photo loses its window) are deferred to PR B. Outside reviewers: Copilot not available (quota); Codex cold read approved; Gemini and Codex ran in earlier rounds.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 20:47 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan.md changed since its last review, 8bffd83 -> 9b39fc4, and PR B must take on items deferred from PR A; run-issue step 13 allows only revise)
**After**: plan
**Decision**: revise

PR A merged as #33 (6a9a9fe). Owner said "27b" (start part B). Refresh the plan's PR B part with what PR A deferred: F1 zip DOS times read in the importing PC's time zone (logger_session.py:176; e.g. the logger writes the zip extended UTC timestamp); F2 a real PHOTO in the same second as a voided capture's moved photo row loses its window (soundings.py:821; also match the "photos of voided" note); the camera-target fallback when the latest capture is flagged (Survey.java:643); a false error: event when a flag lands as a ref finishes; the photo file-name number after a move; MapView drawing; the undo window's time source; version 0.9.0.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 20:49 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-27/plan.md` at `58382ef`

Revision after PR A merged as #33: PR A marked delivered; PR B rewritten against main as steps 10 to 20 (Undo notice, Recent captures, PHOTO target line, MapView flagged drawing, camera-target fallback, photo file-name number, false error: event ordering, one undo clock, F1 zip extended timestamp with tie rule, F2 note match, docs and 0.9.0), each with its test.

## Plan Review
**Status**: complete
**When**: 2026-11-26 20:58 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: needs-work

**Issue**: #27 — Logger: undo or flag an accidental capture
**Plan**: `.agent/work-plans/issue-27/plan.md` at `58382ef`
**Branch**: `feature/issue-27`

Scope: PR B, steps 10 to 20, checked against main at 5da4114.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | About 12 files, one feature finish plus the deferred fixes; fits one PR. |
| Issue alignment | Good | Undo bar, Recent captures with reasons and un-flag, PHOTO target line (owner's comment), flagged drawing. Keep-and-flag (owner's veto) is respected: nothing in steps 10-20 deletes. Serves: Project goal "surveyed with the logger" (`docs/roadmap.md` Direction); the project has no design document. |
| File targeting | Needs work | Step 15 says "if a parse is found": there is one (`logger_session.py:603-617`, see finding 4). `import_sessions.py` (the tie rule's SQL, finding 1) is missing from the PR B file list. |
| Consequences | Needs work | Step 18's tie rule must also go into `import_refs_csv`'s stored-flag UPDATE (`void_copy_mtime` comparison), or a re-run decides differently from a first run. |
| Principle alignment | Needs work | Test what breaks: step 14's test would pass on main (its premise is wrong, finding 2); step 18's `ShareZipTest` runs on the desktop JDK, not Android libcore, so it cannot show what the phone writes. |
| ADR compliance | Good | The project keeps no ADRs. Public repo: fixtures are synthetic; the step 13 screenshot must be in local frame with no basemap (stated). |

### Findings

1. **[Consequences] Step 18's 26 h tie window.** It cannot flag every ref in a normal re-export. In `import_refs_csv` the "flagged wins" branch fires only when copies disagree on a row's flag, and refs.csv rows only change flag in the session that made them. Same-day re-exports with no flag change are unaffected. The real cost runs the other way: flag, export, un-flag, export within 26 h makes a tie, flagged wins, and the un-flag is lost. Only a copy more than 26 h newer recovers it (the refs.csv entry time is the file's mtime, which a later append moves). A tighter rule is available for the old zips. Each logger zip carries `events.csv`, whose last line has `wall_ms` (UTC), and whose DOS entry time is that file's mtime, in the phone's zone. Their difference, rounded to 15 min, is that zip's zone offset. Correct the DOS times by it, and keep the tie only for a zip with no usable `events.csv`. Either way the plan should say: (a) the mixed case (one copy with 0x5455, one DOS-only) and how they compare; (b) that the same rule applies in the stored-flag SQL (`void_copy_mtime`); (c) "report them" means only the rows whose flag a tie decided, not every row of tied copies; (d) add a test for the un-flag case.
2. **[Principle alignment] Step 14's premise does not hold on main.** `PhotoRule.attachTo(PointRow, photoNs)` already returns -1 unless `0 <= photoNs - last.elapsedNs <= 60 s`, and `photoTaken` passes `p.pressNs`. So the re-ask already applies the 60 s window at the press. An older live capture is taken only when it was within 60 s of the press, the same rule `planPhoto` uses. `replanAttach` as written would reproduce today's behaviour. Its test passes without the change. Also, a flag cannot happen while the camera activity is in front: Recent captures and UNDO live in `MainActivity`. Choose one: (a) drop the step and keep a lock-in test, or (b) say what rule should apply instead. One candidate is the void-move rule (step 5: nearest earlier live, no window), so a photo aimed at a capture flagged while the camera was open goes where that capture's earlier photos went. The only real residue is the file name number, which step 15 covers.
3. **[Principle alignment] Step 16: the cause is confirmed in code, but the second half of the fix is unneeded and risky.** `onEpoch` queues `poleCheck` after its synchronized block (`Survey.java:236-238`) on a single-thread executor (`:155`). A `voidCapture` in that gap queues `setVoid` before the append. `finishTilt` is a synchronized method that already queues `poleCheck` under the lock (`:425-428`), so only the occupation path needs the move. Moving `run(() -> poleCheck(f))` inside the lock fixes the ordering. Making `refsVoidEvent` treat `holding == 0` as "pending" would also hide a real failure: when `RefsStore.append` threw, holding 0 is a true error. Drop that half. The gap is microseconds and a ref's UNDO notice is set only by `poleCheck`, after the append, so the UI cannot reproduce it. The test comment states the reasoning (the plan allows this). A Survey-free sequencer is not worth building for a one-line move.
4. **[File targeting] Step 15: the importer does use the name number and the original event.** In `load_session` a photo's `n` is `by_file` (column), else the `photo file=... attached to|own row n=` event, else `PHOTO_RE`'s number from the name. `attached` (capture / own row) comes from the original event only. A photo moved by a void to a new `photo` row therefore keeps `attached="capture"` from the old event. The `photo moved` events are not read. Make the column the only source of `n` and `attached` when the row has the file. Use the event or name only for an orphan file. Test it with a moved-to-own-row photo.
5. **[Principle alignment] Step 18: the Android zip writer is not testable on the JVM.** minSdk is 26, so `setLastModifiedTime` exists. The unit tests run on the desktop JDK's `ZipOutputStream`, not Android's libcore, so a passing `ShareZipTest` says nothing about what the phone writes. Write the 0x5455 extra by hand (`setExtra` with the tested `ShareZip.extendedTimestamp`) so the bytes do not depend on the platform. Add a phone check: share a zip, then read `info.extra` with the importer's `zip_entry_mtime`.
6. **[Issue alignment] Step 10: the new fields duplicate `Notice.at`.** `Notice.at` already carries the row (`n`, `elapsedNs`) but is also set by photo notices. A boolean `undoable`, set only by a capture's own notice, does what `undoN` and `undoCaptureNs` do without copying the row. Note that a ref's notice comes from `poleCheck` (async, after `RefsStore.readAll`), so its UNDO can show with less than 8 s left. That is acceptable, but say so. Step 17 is then mostly part of step 10: `MainActivity` has no undo yet, and `VoidRule.undoOpen` already uses `elapsedRealtimeNanos`.
7. **[Issue alignment] Step 19.** Match the note exactly (`== "photos of voided n=<n>"`, or a regex with an end or word boundary), not by prefix, so `n=1` never matches `n=12`. Confirm `note` is in the clist dict.
8. **[Principle alignment] The phone try-out has no checklist.** The repo has JVM JUnit tests only (no Robolectric), so the bar, dialog, PHOTO label and drawing are checked only on the phone. Add the list the PR text will report against:
   - UNDO shows after surface, object, tilt and ref notices, and never on a "Photo on" notice.
   - A double tap or held press in the first 750 ms does nothing.
   - UNDO goes at 8 s. Screen off for more than 8 s, then on: no UNDO, or a "Too late" refusal.
   - A newer notice removes UNDO.
   - Recent captures is disabled during a ref or tilt. It lists 10 rows, newest first. Free-text reasons with a comma come out clean. Un-flag works.
   - `info` shows "1 flagged". The REC count drops.
   - A flagged capture draws as a grey cross with no camera. A moved photo row keeps its camera.
   - The coverage overlay recovers.
   - The PHOTO line counts up, switches to "own row" after 60 s and after flagging the last capture.
   - A shared zip carries 0x5455 (finding 5).
   - A flagged ref is skipped by the next pole check.

   The step 13 "screenshot of a synthetic session" needs a way to put a synthetic session on the phone. Say how (adb push a folder made by `synth.py`?) or drop it for a field session shown in the local frame.

### Summary

The plan is sound in shape and respects keep-and-flag. Three steps rest on readings of main that need correcting before implementation:
- Step 14 fixes behaviour that is already correct.
- Step 16's "pending" half would mask real errors.
- Step 18's tie rule loses same-day un-flags and is missing from the stored-flag SQL. A per-zip offset from `events.csv` is tighter.

Also add a phone try-out checklist.

### Recommended Actions

- [ ] Step 18: state the mixed-copy and SQL handling and the report scope; consider the per-zip `events.csv` offset; add the un-flag test; write 0x5455 by hand and check it on a phone zip; add `import_sessions.py` to the file list.
- [ ] Step 14: drop it (keep a lock-in test) or name the rule that should replace the 60 s re-ask.
- [ ] Step 16: only move `run(() -> poleCheck(f))` inside `onEpoch`'s lock; keep `refsVoidEvent`'s error for holding 0.
- [ ] Step 15: make the column the source of `n` and `attached`; test a moved-to-own-row photo.
- [ ] Step 10/17: use one `undoable` flag on `Notice`; fold 17 into 10.
- [ ] Step 19: exact note match.
- [ ] Add the phone try-out checklist to step 20, and say how the step 13 screenshot session gets onto the phone.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 20:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work on mechanism only; orchestrator table allows revise; second host revise in a row, so a further plan checkpoint answer is the owner's)
**After**: plan
**Decision**: revise

Revise PR B per the Plan Review at 7bfe31d, all 8 findings: step 18 uses the per-zip zone offset from events.csv's last wall_ms vs its DOS time (15-min rounding), the tie only for a zip with no usable events.csv, the same rule in the stored-flag SQL, extended-vs-DOS comparison stated, report only tie-decided rows, an un-flag test, import_sessions.py in the files; write 0x5455 by hand with setExtra and check a phone zip. Drop step 14 (keep a lock-in test). Step 16: move the run(poleCheck) inside the lock only; no "pending" treatment. Step 15: the photo column is the only source for n/attached when the row has the file. Steps 10+17: a boolean undoable on Notice, elapsed clock via VoidRule.undoOpen. Step 19: exact note match. Add the phone try-out checklist and how a synthetic session gets onto the phone.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 20:54 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-27/plan.md` at `fda6dda`

PR B revised for all 8 plan-review findings: step 18 uses a per-zip zone offset from events.csv (tie only without it), the same rule in the stored-flag SQL, 0x5455 written by hand, un-flag test; step 14 dropped to a lock-in test; step 16 is only the move inside the lock; step 15 takes n/attached from the photo column; steps 10 and 17 merged with one undoable flag; step 19 exact note match; step 20 has the phone checklist and how a synthetic session gets on the phone.

## Plan Review
**Status**: complete
**When**: 2026-11-26 21:02 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #27 — Logger: undo or flag an accidental capture
**Plan**: `.agent/work-plans/issue-27/plan.md` at `fda6dda`
**Branch**: `feature/issue-27`

Re-review of PR B (steps 10 to 20) after the revision for the Plan Review at 7bfe31d. Checked against main (5da4114): `logger_session.zip_entry_mtime`, `SessionSource.refs_csv_dated`, `load_session` photos (lines 600-617), `import_sessions` stored-flag UPDATE (lines 405-412), the `soundings` clist query (lines 627-634), `Sharer.zip`, `ShareZip.addFile` and `Session.event`.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | About 12 files. Step 14 is now only a lock-in test and step 17 is folded into step 10, so the PR is smaller than before. |
| Issue alignment | Good | All 8 earlier findings are resolved in the text: 1 (step 18 b-d, the un-flag test), 2 (step 14 dropped), 3 (step 16 is only the move), 4 (step 15 makes the column the source), 5 (0x5455 by hand plus a phone check), 6 (one `undoable` flag), 7 (exact note match, `note` added to cols) and 8 (step 20 checklist and how a synthetic session gets onto the phone). Keep-and-flag holds. A voided capture's stop "depends on reason" is kept in step 11. Serves: Project goal "surveyed with the logger" (`docs/roadmap.md` Direction); the project has no design document. |
| File targeting | Good | `import_sessions.py` and `ShareZip.java` are now listed. The step 15 lines match main. |
| Consequences | Good | The stored-flag SQL gets the same rule (18c). |
| Principle alignment | Needs work (minor) | Test what breaks: the step 18b offset can be silently wrong in two cases main's code allows (findings 1 and 2). Neither case has a test yet. |
| ADR compliance | Good | The project keeps no ADRs. Public repo: fixtures are synthetic, and a screenshot must be in local frame with no basemap. |

### Findings

1. **[Principle alignment] Step 18b: a snapshot zip can give the wrong 15-min offset, silently.**
   - In `Sharer.zip` the lengths are fixed first (line 151). `ShareZip.addFile` then reads `f.lastModified()` for each entry later, while the session keeps recording.
   - `Session.event` flushes every line. Any event during a long zip (big `gps.ubx` or photos) therefore moves `events.csv`'s mtime past the last line in the cut.
   - A gap over 7.5 min rounds to an offset 15 min off. The +-14 h check does not catch it.
   - The same happens to any zip whose `events.csv` mtime was reset by a copy tool. `find_sessions` reads every zip, not only logger ones.
   - The reverse case (last line after the entry time) is only DOS 2 s truncation or a clock step, and rounding covers it.
   - Fix: accept the offset only when the residual `|(dos_utc - wall) - offset|` is at most a small bound, for example 120 s. Otherwise mark the zip `mtime_approx`.
   - Compute both sides without the PC zone: `calendar.timegm(info.date_time) - wall_ms/1000`, and the corrected time as `timegm(date_time) - offset`. Do not use `time.mktime` and then shift.
   - Tests: a snapshot-like zip with a 10 min gap is `mtime_approx`, not corrected by 15 min; a residual of a few seconds is accepted.
2. **[Principle alignment] Step 18b: DST between the two files' mtimes.**
   - The offset is the phone's offset at `events.csv`'s mtime. A DST change inside a session does not matter, because the last line and the mtime are close together.
   - The entry that matters, though, is `refs.csv`. It is the storage root's cross-session file, and its mtime is its last append, possibly days earlier. If a DST change falls between that append and `events.csv`'s mtime, the corrected `refs.csv` time is off by 1 h.
   - Rule to state and test: when an entry's DOS time is more than about 12 h from `events.csv`'s DOS time, treat that entry as uncertain by 1 h. Two copies within 1 h of each other are then a tie (flagged wins, reported as in 18d), not ordered.
   - The same rule goes into the stored-flag SQL (18c).
   - 15-min rounding is right as it stands: every real zone offset is a multiple of 15 min.

### Summary

The revision resolves all 8 earlier findings, and each one checks out against main's code. The events.csv offset is the right idea. It needs a residual bound, so a snapshot zip or a re-timed copy cannot get a confident wrong offset, and a 1 h DST allowance for a `refs.csv` entry far from `events.csv` in time. Both are small, precise additions to step 18. They can go in during implementation without another plan round.

### Recommended Actions

- [ ] Step 18b: residual bound (about 120 s) on the rounded offset, else `mtime_approx`; compute with `calendar.timegm`; test a 10 min snapshot gap.
- [ ] Step 18b/c: 1 h DST allowance (tie) for an entry more than about 12 h from `events.csv`'s DOS time, in Python and the stored-flag SQL; test it.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 20:56 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready, no must-fix; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

The two new step-18b findings from the Plan Review at 967586e are taken into implementation: accept the events.csv zone offset only when the remainder after 15-min rounding is under about 120 s (else mtime_approx), both times via calendar.timegm, a 10-min snapshot-gap test; a refs.csv entry more than about 12 h from events.csv is uncertain by 1 h (copies within 1 h tie, flagged wins), the same in the stored-flag SQL, with a test.

## Implementation
**Status**: complete
**When**: 2026-11-26 21:13 -05:00
**By**: Claude Code Agent (claude-opus)
**Branch**: feature/issue-27 at `17ae5a3`

PR B (plan steps 10-20) plus the two step-18b refinements from the Checkpoint at 4f521a7. Keep-and-flag holds: nothing deletes a row, a photo or a file.

- `2869933` step 10: `Survey.Notice.undoable` (set by surface/object MARK, tilt end and the ref's pole-check notices; photo notices false). MainActivity shows UNDO beside the notice while `VoidRule.undoOpen(at.elapsedNs, elapsedRealtimeNanos)` holds and the row is not flagged; taps ignored for 750 ms after it first shows (`VoidRule.armed`); a tap calls `voidCapture(n, "accidental", "undo")`, Survey re-checks the window ("Too late to undo"). `PointRow.label` moved to core (Survey.label delegates).
- `4527c12` step 11: MENU > Recent captures… (`VoidRule.recent`, `recentLine`, `ageText`; `Survey.recent(10)`), reason picker from `VoidRule.REASONS` plus typed text, Un-flag for a flagged row, refused while a ref/tilt runs; info box says `N flagged` (`Survey.flagged`).
- `b069672` steps 12 and 14: PHOTO second line `→ S12 (14 s)` / `→ own row` from `PhotoRule.targetText` via `Survey.photoTarget()`, refreshed by the existing 250 ms tick; step-14 lock-in test (flagged planned capture re-asks from the press under the 60 s window); file-name number documented in PhotoRule.
- `98119c6` step 13: flagged capture drawn as a grey cross with grey `S12 ✕` label, no class symbol, no camera; photo rows keep the camera (`VoidRule.glyph`: LIVE/FLAGGED/PHOTO). Also fixes one wrong assertion in the step-11 test.
- `fba12f1` step 16: `run(() -> poleCheck(f))` moved inside onEpoch's synchronized block (as finishTilt); `refsVoidEvent` holding-0 stays an error (test comment added).
- `9760338` step 18a: `ShareZip.extendedTimestamp` (hand-built 0x5455, flags 1, LE UTC seconds) set on every entry; tests for the bytes and the round trip through ZipInputStream (DEFLATED and STORED).
- `b641283` steps 18b-d with both refinements: `zip_zone_offset` (events.csv last wall_ms vs its DOS time via `calendar.timegm`, rounded to 15 min, accepted only with remainder <= 120 s and |offset| <= 14 h), `zip_entry_stamp` -> (mtime, uncertainty): 0 for 0x5455/folder/corrected DOS, 3600 for an entry > 12 h from events.csv (DST), 26 h for mtime_approx. `refs_csv_dated` now returns (text, mtime, uncertainty). `import_refs_csv`: copies within the larger uncertainty tie, flagged wins; the same rule against the stored flag (computed in Python now, new refs column `void_copy_unc`, NULL from before read as 26 h); optional `ties` list, logged in the report and returned as `refs_csv_ties`. One existing test changed: `test_voided_captures_kept_and_flag_updates` dated its un-flag zip +1 h, which is now a tie (its synthetic events.csv gives no offset, so mtime_approx); moved to +2 days with a comment. New `RefsCopyTimeTest` (8 tests): offsets, 10 min snapshot gap, extended across zones, corrected DOS, no events.csv tie and report, mixed, un-flag within 26 h plus re-run, DST allowance (in-run and against the stored flag). `synth.phone_zip` added.
- `89bac66` step 15: a photo listed in a row's photo column takes `n` and `attached` from that row only; event/name only for an orphan. Test with a photo moved to own row n=5 (name/event say n=2). Importer docstring notes files are never renamed.
- `d2d72a8` step 19: `clist_windows` skips only a photo row whose note is exactly `photos of voided n=<n>` (anchored regex) for a voided n at that utc; `note` added to the soundings clist query. Tests: real photo same second keeps window, moved row does not, n=1 vs n=12, edited note.
- `17ae5a3` step 20: logger/README.md (Main screen UNDO and info, PHOTO line, MENU item, Recent captures and grey cross, Photos file-name number, Share 0x5455 and the importer's zone rule), docs/log.md entry, versionName 0.9.0 / versionCode 13.

Tests (every hook ran on each commit):
- `cd logger && ./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 189 tests, 0 failures. C: 112.8 GB free before and after.
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 140, OK (skipped=5); also OK under TZ=Asia/Kolkata and TZ=Pacific/Kiritimati.
- `python3 -m unittest discover icecube/tests`: Ran 94, OK.

APK: `logger/app/build/outputs/apk/debug/app-debug.apk`, versionName 0.9.0, versionCode 13. Not installed.

Not done here / for the phone try-out: the whole step-20 checklist (UNDO after each capture kind and never on a photo notice, 750 ms arm, 8 s and screen-off, newer notice ends UNDO, Recent captures list/reasons/Un-flag/refused during ref or tilt, info `flagged` and REC count, grey cross, coverage recovery, PHOTO line, 0x5455 in a phone-made zip read by `zip_entry_mtime`, flagged ref skipped by the next pole check). No screenshot taken. The #20/#21/#23 issue comments from PR A are still not posted.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 21:22 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-27 at `6fc69be`
**Base**: main
**Depth**: Deep (reason: 1254 lines across 21 files, Android app plus QGIS importer: cross-layer)
**Must-fix**: 2 | **Suggestions**: 5
**Round**: 1 | **Ship**: continue — round 1: 2 must-fix; first round always re-reviews after fixes

PR B only (PR A merged as #33). Checks: `python3 -m unittest discover -s analysis/qgis/tests` Ran 140, OK (skipped=5); `./gradlew --max-workers 4 testDebugUnitTest lintDebug` 189 tests, 0 failures, lint on changed files only SetTextI18n warnings (existing pattern). No Python linter installed (py_compile only). Focus areas: UI threading is sound (every row change synchronized, writes on the io executor, poleCheck now queued under the lock, poleCheck's result check under the lock skips a voided ref; main-thread reads of PointRow.voided are benign because only main-thread calls write it). Undo window is checked twice on one clock (elapsedRealtime) with the 750 ms arm delay, and photo notices are never undoable. `void_copy_unc` without a schema bump is compatible: ensure_schema adds the column, NULL reads as APPROX_S (the conservative side), refs.csv rows are re-evaluated every run, and no 0.9.0 session with moved photos exists before this app, so no session re-read is needed. The changed existing test (test_import unflag re-import, `later` +1 h to +2 days) is justified: synthetic zips have no 0x5455 and no usable zone offset, so they fall in the 26 h tie; the test_soundings change follows the deliberate exact-note rule. Owner rules held: flags stay flags end to end; a voided stop's use depends on the reason. Outside reviewers: Gemini and Codex (both completed); Claude adversarial subagent (no must-fix). Dropped as false positives: poleCheck voided check (present at Survey.java:516), PHOTO allCaps (bigButton sets false), voidReason null (initialised ""), MapView stroke reset (each branch sets its own).

### Findings
- [x] (must-fix) Stored-flag update bypasses the tie rule when the stored copy time is unknown (`st is None` gives order 1): an unknown-time unflagged copy clears an unknown-time stored flag, against "disagreeing unknown-time copies give flagged"; route it through `_stamp_order` (Gemini, Codex) — `analysis/qgis/import_sessions.py:451`
- [x] (must-fix) On a tie the UPDATE replaces the stored `void_copy_mtime`/`void_copy_unc` with the incoming copy's, which may be older or exact: e.g. stored (flagged, T, 26 h) tied by (flagged, T-25 h, 0) becomes (T-25 h, 0), and a later exact unflag at T-24 h then clears it; on a tie keep the later stamp and the larger uncertainty (merge), and add a test (Gemini) — `analysis/qgis/import_sessions.py:456`
- [x] (suggestion) The tie report can name refs a tie did not decide: refs of imported sessions (source points.csv, filter as n_void does) and in-run ties that a surely newer stored copy overrode (Gemini, Codex) — `analysis/qgis/import_sessions.py:465`
- [x] (suggestion) UNDO is keyed on Notice identity: a tilt ref's finishTilt notice is replaced by poleCheck's notice for the same row, so taps in between are dropped silently and the arm delay restarts; key on `nt.at` and give an error cue for an ignored tap (Gemini, Claude) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:533`
- [x] (suggestion) Recent captures dialog callbacks use the captured `sv`: a session stopped (from the notification) while the dialog is open gets flagged in memory with its writes silently rejected; check `survey() == sv` before acting (Gemini) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:569`
- [x] (suggestion) The changed unflag test now covers only the 26 h fallback; let `synth.write_zip` write 0x5455 and keep a +1 h end-to-end unflag case for real 0.9.0 zips (Claude) — `analysis/qgis/tests/test_import.py:157`
- [x] (suggestion) ShareZip: skip the extended timestamp when `lastModified()` is 0 (it would claim an exact 1970 time), and soften the "bytes do not depend on the platform" comment (OpenJDK re-encodes 0x5455 from `setExtra`); confirm on the phone that a shared zip carries one 0x5455 (Gemini, Claude) — `logger/app/src/main/java/rink/icecube/logger/core/ShareZip.java:91`

## Implementation
**Status**: complete
**When**: 2026-11-26 21:28 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-27 at `eed240b`
**Addressed**: Local Review (Pre-Push) at `6fc69be` (2026-11-26 21:22 -05:00)
**Commits**: 3fd28d0, 4bafbe8, 130c01c, 2f1a766, eed240b

### Actions
- [x] (must-fix) Unknown stored copy time now goes through `_stamp_order` (both unknown: a tie, flagged wins); test `test_unknown_stored_time_ties_an_unknown_copy` — `analysis/qgis/import_sessions.py`
- [x] (must-fix) On a tie the stored `void_copy_mtime`/`void_copy_unc` keep the later time and the larger uncertainty; test `test_tie_keeps_the_later_stored_time_and_larger_uncertainty` — `analysis/qgis/import_sessions.py`
- [x] (suggestion) Tie report lists only refs stored from refs.csv, and drops in-run ties a surely newer stored copy overrode; test `test_tie_report_only_names_refs_a_tie_decided` — `analysis/qgis/import_sessions.py`
- [x] (suggestion) UNDO keyed on `nt.at` (row identity, as Survey uses); an ignored tap plays the error cue. No unit test: Activity code, needs the phone — `logger/.../MainActivity.java`
- [x] (suggestion) Recent captures checks `survey() == sv` at each step (item, un-flag confirm, flag) and toasts "That session has ended: nothing was changed" with the error cue. No unit test: Activity code — `logger/.../MainActivity.java`
- [x] (suggestion) `synth.write_zip(refs_utc=...)` writes 0x5455; end-to-end test `test_flag_cleared_an_hour_later_with_extended_timestamps` — `analysis/qgis/tests/synth.py`, `analysis/qgis/tests/test_import.py`
- [x] (suggestion) ShareZip skips the extended timestamp when `lastModified()` is 0 (sets the oldest DOS time instead), comment softened; tests check exactly one 0x5455 per entry and none for mtime 0. On-phone confirmation of one 0x5455 in a shared zip still to do — `logger/.../core/ShareZip.java`

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 144, OK (skipped=5); the three new importer tests fail on the previous code
- `python3 -m unittest discover icecube/tests`: Ran 94, OK
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 190 tests, 0 failures; APK `logger/app/build/outputs/apk/debug/app-debug.apk` (0.9.0, versionCode 13). C: had 112.8 GB free.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 21:36 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-27 at `29f67e2`
**Base**: main
**Depth**: Deep (reason: 1437 lines across 21 files, Android app plus QGIS importer: cross-layer; fix round classified on the whole diff)
**Must-fix**: 2 | **Suggestions**: 5
**Round**: 2 | **Ship**: continue — round 2: 2 must-fix include a design/correctness concern (not mechanical)

PR B only. All seven round-1 items are fixed as described in the Implementation at eed240b (checked in the code and by the new tests). Checks: `python3 -m unittest discover -s analysis/qgis/tests` Ran 144, OK (skipped=5); `./gradlew --max-workers 4 testDebugUnitTest lintDebug` 190 tests, 0 failures, lint warnings only (SetTextI18n, existing pattern); C: 112.8 GB free. No Python linter installed (py_compile only). Both must-fix items concern zips without a 0x5455 timestamp (re-zipped copies, or void-column refs.csv from builds before 0.9.0); real 0.9.0 phone zips are exact. Owner rules held: flags stay flags; nothing deletes a row or file. Outside reviewers: Gemini and Codex (both completed); Claude adversarial subagent (no must-fix; raised the tie-merge and DST items as suggestions). Dropped: poleCheck voided guard (present, round 1), onUndo on an expired notice (error cue given, Survey says too late), the ms-vs-s ordering granularity between phone and importer (needs flag and un-flag within one second plus a partial write; errs to flagged).

### Findings
- [x] (must-fix) Copy-time uncertainty arithmetic: `_stamp_order` ties within max(ua, ub), but independent errors add (two DST-uncertain copies 90 min apart can be in reverse order; 26 h vs 1 h mixes), and the tie merge stores (max t, max u), a bound wider than any input's (stored (T, 26 h) tied by (T+20 h, 0) becomes (T+20 h, 26 h), so an exact un-flag at T+30 h ties and the result depends on import order). Treat a copy time as an interval: tie when the intervals overlap (|d| <= ua + ub, or keep max only where a shared PC-zone error is the reason, and say so), and on a tie store the hull; decide the 26 h same-PC case explicitly (Codex, Gemini, Claude) — `analysis/qgis/import_sessions.py:376`, `:427`, `:460`
- [x] (must-fix) A DST change less than DST_FAR_S (12 h) from events.csv is still possible: Java converts each file's mtime with the zone rules at that moment, so a refs.csv written just across a transition gets a corrected time 1 h off with uncertainty 0 (e.g. fall 2026, ref 05:30 UTC and events 06:30 UTC share a DOS wall time). Give every corrected-DOS entry other than events.csv itself DST_S, or test whether a transition can lie between the two, with a test (Codex, Claude) — `analysis/qgis/logger_session.py:286`
- [x] (suggestion) Stored-flag UPDATE on a tie copies the incoming copy's `void_reason` even when the stored flag's copy is later (the in-run rule keeps the newest copy's reason); and a row stored from a pre-0.9.0 refs.csv (void 0, time NULL) is reported as a tie against a later unknown-time flagged copy though it had no void column (Gemini) — `analysis/qgis/import_sessions.py:457`, `:466`
- [x] (suggestion) UNDO arm delay is checked when the click completes, so a press that starts inside the 750 ms and ends after it undoes; check the arm at ACTION_DOWN (or require the press to start after arming), as the plan's "a press in progress cannot undo" means (Codex) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:554`
- [x] (suggestion) ShareZipTest reads entries with ZipInputStream (local headers); the importer reads the central directory's extra field: also check through java.util.zip.ZipFile or ZipFile's CEN extra (Gemini) — `logger/app/src/test/java/rink/icecube/logger/core/ShareZipTest.java:65`
- [x] (suggestion) `refs_csv_dated` reads and decodes events.csv for the zone offset even when refs.csv has a 0x5455 time; look for the extended timestamp first. Optionally make the zone residual one-sided (DOS truncates down by up to 2 s) to halve false offsets (Gemini, Claude) — `analysis/qgis/logger_session.py:156`, `:264`
- [x] (suggestion) Docs drift from the round-1 UNDO change: README says "a newer notice ends it" and taps in the first 750 ms "are ignored" (now a notice for the same capture keeps UNDO and an ignored tap plays the error cue); the javadoc above `undoAt` still describes the notice; ShareZip javadoc line 71 is 158 characters (reflow) — `logger/README.md:139`, `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:110`, `logger/app/src/main/java/rink/icecube/logger/core/ShareZip.java:71`

## Implementation
**Status**: complete
**When**: 2026-11-26 21:46 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-27 at `3e22699`
**Addressed**: Local Review (Pre-Push) at `29f67e2` (2026-11-26 21:36 -05:00)
**Commits**: bef1761, 810d7c4, aa1b335, 3e22699

Must-fix 1 rule decided by the host (owner may veto): each copy time is the interval [t-u, t+u]; two copies tie when the intervals overlap (|t1-t2| <= u1+u2) and a tie gives flagged; on a tie the stored void_copy_mtime/void_copy_unc become the union interval's centre and half-width, otherwise the surely newer copy's own interval is stored. The 26 h no-offset case follows the same rule (only wider). Consequence: two corrected-DOS copies (now each +-1 h) must be more than 2 h apart to order, and two mtime_approx copies more than 52 h; existing tests moved their later copies accordingly.

### Actions
- [x] (must-fix) Interval overlap ties, union stored on a tie, surely newer copy's own interval otherwise; in-run and against the stored flag; tests `test_tie_stores_the_union_interval` (exact un-flag at T+30 h now clears), `test_two_dst_uncertain_copies_90_min_apart_tie` (both orders, in one run and across runs, then an exact un-flag clears only once surely newer), `test_import_order_does_not_change_the_result` (all permutations, one run and one copy per run) — `analysis/qgis/import_sessions.py` (`_stamp_order`, `_stamp_hull`, `import_refs_csv`)
- [x] (must-fix) Every corrected-DOS entry other than events.csv itself gets DST_S (DST_FAR_S removed); test `test_dst_change_just_before_events_csv` (fall 2026 transition an hour before events.csv) and `test_dst_allowance_for_every_corrected_entry_but_events_csv` — `analysis/qgis/logger_session.py` (`zip_entry_stamp`)
- [x] (suggestion) A tie keeps the newest flagged copy's void_reason (`_flag_reason`, by time then text, order-independent); a ref stored from copies without a void column now has void NULL, so the first copy with one decides it as if new and no tie is reported; tests `test_tie_keeps_the_flagged_copys_reason`, `test_row_from_a_refs_csv_without_void_column_is_not_a_tie` — `analysis/qgis/import_sessions.py`
- [x] (suggestion) UNDO arm delay checked against the press's ACTION_DOWN time (touch listener, elapsedRealtime; a click with no press seen is timed at the click) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java`, `core/VoidRule.java` javadoc
- [x] (suggestion) ShareZipTest also reads the central directory through java.util.zip.ZipFile (both the 0x5455 and the no-timestamp cases) — `logger/app/src/test/java/rink/icecube/logger/core/ShareZipTest.java`
- [x] (suggestion) `refs_csv_dated` takes a 0x5455 time before reading events.csv (test `test_extended_timestamp_skips_events_csv`); zone residual one-sided, -2 s (DOS truncation) to +120 s — `analysis/qgis/logger_session.py`
- [x] (suggestion) Docs: README UNDO paragraph (press-down, error cue, same-capture notice keeps UNDO), README zip-ordering paragraph (1 h DST, 26 h, either-order tie), javadoc above `undoAt`, ShareZip javadoc reflowed; import_sessions module docstring describes the interval rule — `logger/README.md`, `MainActivity.java`, `ShareZip.java`, `analysis/qgis/import_sessions.py`

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 150, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 94, OK
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: 190 tests, 0 failures; app-debug.apk 315686 bytes, sha256 69c86cc0…6139d (C: 112 GB free before the build)
- Pre-commit hooks ran on every commit; nothing pushed.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 21:50 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-27 at `48ea20e`
**Base**: main
**Depth**: Deep (reason: round 3 convergence check per handoff)
**Must-fix**: 1 | **Suggestions**: 0
**Round**: 3 | **Ship**: continue — round 3: 1 must-fix is a design/correctness concern (not mechanical)

Scope: round 2's seven findings and the fix diff 2998717..48ea20e only. Cross-model: skipped (round-3 convergence check, per handoff). All seven round-2 items are fixed as the Implementation at 3e22699 says (interval overlap in `_stamp_order`, union stored on a tie, DST_S for every corrected-DOS entry but events.csv, newest flagged reason kept, void NULL for copies without a void column, UNDO arm at ACTION_DOWN, ShareZipTest reads the central directory, 0x5455 looked up before events.csv, docs). Checks: `python3 -m unittest discover -s analysis/qgis/tests` Ran 150, OK (skipped=5); `ANDROID_HOME=~/Android/Sdk ./gradlew testDebugUnitTest --max-workers 4` 190 tests, 0 failures. Tie rule consistency: two copies are compared the same way in one run and against the stored flag (overlap, |d| <= u1+u2), and a copy surely newer than every tied copy is surely newer than their union, so a later surely newer copy is handled alike. But the stored union keeps the flag of a copy that a later surely newer copy would have dropped (below). Probe (not committed): with X flagged exact at T, Z cleared exact at T+1 h, Y cleared at T+10 h with 26 h (mtime_approx), all three in one run give cleared, as does one per run in the order X, Z, Y; one per run in the orders X,Y,Z / Y,X,Z / Y,Z,X / Z,Y,X give flagged. A random search over 3 copies (uncertainties 0, 1 h, 26 h; 150 cases, all orders) found 32 such differences, every one toward flagged in the run-by-run result, never toward cleared: so no flag is lost, but the docstring's "neither import order nor a later run" claim does not hold and `test_import_order_does_not_change_the_result` does not cover it.

### Findings
- [x] (must-fix) Tie rule differs in one run and across runs, and across runs with import order: in a run a copy surely older than another top copy is dropped before ties are looked at, while across runs its flag is already folded into the stored union and cannot be dropped (X flagged T exact, Z cleared T+1 h exact, Y cleared T+10 h +-26 h: one run cleared; runs X,Y,Z flagged; runs X,Z,Y cleared). It always errs to flagged. Decide: keep each copy's interval and flag per ref (e.g. a refs_copies table) and apply the in-run rule over all copies seen in every run, or accept run-by-run flagged as the safe side and say so in the docstring and README, with a test of the case above — `analysis/qgis/import_sessions.py:445`, `:478`

## Checkpoint
**Status**: complete
**When**: 2026-11-26 21:51 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (one must-fix on a mechanism the host decided; the permanent fix is in reach; first host address at rounds)
**After**: rounds
**Decision**: address

Host decision on the round-3 must-fix (owner may veto): take option 1. Keep every refs.csv copy's (ref, copy time, uncertainty, void, reason) in a new table (refs_copies, deduped by copy identity), and on every import recompute each ref's flag from all copies seen with the one-run rule, so one run and any sequence of runs give the same result. The stored void_copy_mtime/void_copy_unc become derived. A gpkg without the table seeds it from the stored values (treated as one copy). Tests: the reproduced X/Y/Z case in all 6 orders as separate runs equals one run; a random permutation test over 3-4 copies, one run vs. runs.

## Implementation
**Status**: complete
**When**: 2026-11-26 21:56 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-27 at `5d2d4be`
**Addressed**: Local Review (Pre-Push) at `48ea20e` (2026-11-26 21:50 -05:00)
**Commits**: 5d2d4be

Option 1 as the Checkpoint decided. New `refs_copies` table (copy_id, session, n, mtime, unc, void, void_reason), unique per (copy_id, session, n); copy_id is the refs.csv content hash plus its (mtime, uncertainty), so the same file met again (same zip re-imported, or the same refs.csv in two exports) is one copy, while the same text at another time is another. Only rows with a void column are kept. Every import upserts this run's copies, then recomputes each touched ref's flag from all its copies with the one-run rule (`_decide_flag`: copies no other is surely newer than decide; any flagged gives flagged with the newest flagged reason; stored interval is their union). void_copy_mtime/void_copy_unc are now derived. ensure_schema seeds a new refs_copies from each stored refs.csv flag as one copy (copy_id 'stored', unc NULL taken as APPROX_S). Tie report kept: refs stored from refs.csv, held by this run's copies, whose deciding copies disagree. Two older tests encoded the run-by-run union and were changed to the one-run answer: a cleared copy surely older than an exact flagged one no longer reports a tie; an exact un-flag at T+2 h now clears the 90-min DST pair (as one run of all three does).

### Actions
- [x] (must-fix) One-run and run-by-run tie rules differ: refs_copies table, flag recomputed from all copies seen; tests `test_one_run_and_runs_agree_on_the_reviewers_case` (X/Y/Z, all 6 orders, split and together), `test_random_copies_one_run_equals_every_order_of_runs` (60 random cases of 3-4 copies, every permutation, one per run and mixed), `test_same_zip_imported_twice_adds_no_copy`, `test_gpkg_without_refs_copies_seeds_it_from_the_stored_flag`; module and function docstrings, `docs/howto/qgis.md` table list and `logger/README.md` zip-ordering paragraph — `analysis/qgis/import_sessions.py` (`_copy_id`, `_decide_flag`, `import_refs_csv`, `ensure_schema`)

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 154, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 94, OK
- No Java change; gradle not run. Pre-commit hooks ran on every commit; nothing pushed.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 21:59 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: approved

**Branch**: feature/issue-27 at `d87686b`
**Base**: main
**Depth**: Deep (reason: round 4 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 1
**Round**: 4 | **Ship**: recommended — no must-fix findings; remaining suggestions can be applied or tracked

Scope: round 3's one must-fix and the fix diff 85dfcc3..d87686b only. Cross-model: skipped (round-4 convergence check, per handoff). The must-fix is fixed: the flag is now `_decide_flag` over every copy in `refs_copies`, a function of the set of copies, so one run and any run sequence agree as long as each copy gets the same copy_id every time. Checks: `python3 -m unittest discover -s analysis/qgis/tests` Ran 154, OK (skipped=5). Probe (not committed): 80 random cases of 3-4 copies plus one duplicate copy, with unknown (None) times, uncertainties 0/1 h/26 h, two refs per text (one sometimes absent), every permutation imported one per run, split in two runs at a random cut, and the whole set imported twice: 6336 permutations, 0 differences from one run. Copy identity: copy_id is a 128-bit content hash plus mtime and uncertainty; two copies that differ in content or time never collide, and two that share all three carry the same (mtime, unc, void, reason) per ref, so merging them changes nothing. The same copy is counted twice only when its computed time changes between imports (below). Seeding: an old gpkg seeds one 'stored' copy per refs.csv flag (NULL unc taken as APPROX_S, unknown mtime kept as unknown, which is older than any known time, as before); because a tie union is stored as one wide copy, an old gpkg can still err toward flagged, never toward cleared. Tested by `test_gpkg_without_refs_copies_seeds_it_from_the_stored_flag`. The two changed tests now expect the one-run answer, which I checked by hand: the cleared T-24 h copy is surely older than the exact T+20 h flagged copy, so it is dropped and no tie is reported; the exact T+2 h un-flag is surely newer than flagged T+-1 h and ties only cleared T+1.5 h+-1 h, so the result is cleared over [T+0.5 h, T+2.5 h]. Docs (`docs/howto/qgis.md`, `logger/README.md`) match.

### Findings
- [ ] (suggestion) copy_id includes the mtime_approx time, which is read in the PC's own zone. A zip without a 0x5455 stamp and without events.csv, imported again on a PC in another zone, therefore becomes a second copy with a shifted interval, and that copy can change which copies decide the flag, in either direction. This is rare: logger 0.9.0+ zips carry 0x5455, and pre-0.9.0 copies have no void column. Key that path on the raw DOS stamp instead, or note it in the docstring — `analysis/qgis/import_sessions.py:421`

## Checkpoint
**Status**: complete
**When**: 2026-11-26 22:00 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push review approved; orchestrator table allows publish)
**After**: publish
**Decision**: publish

One open suggestion (the copy id includes an approximate time read in the importing PC's zone) is carried into the PR-mode review.

## Integrated Review
**Status**: complete
**When**: 2026-11-26 22:00 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Reviews-read**: none

**PR**: #36 at `cd73f5f`
**Sources**: 1 (Local Review (Pre-Push) @ `d87686b` (covers `cd73f5f` via bookkeeping)); no hosted reviews (Copilot out of quota), no PR comments, no CI checks
**Cross-source confirmations**: 0
**CI**: none (no checks configured on the PR head)

### Findings
- [x] (suggestion, Local Review (Pre-Push) @ `d87686b`) Valid, still open at head: `_copy_id` keys a copy on its mtime and uncertainty, and on the mtime_approx path (no 0x5455 stamp, no events.csv) that time is the DOS stamp read in the importing PC's zone. The same zip imported on a PC in another zone becomes a second copy with a shifted interval, which can change which copies decide the flag. Rare (logger 0.9.0+ zips carry 0x5455; pre-0.9.0 copies have no void column). Fix: key that path on the raw DOS stamp, or state the limitation in the `_copy_id` docstring — `analysis/qgis/import_sessions.py:421`

### False positives

## Checkpoint
**Status**: complete
**When**: 2026-11-26 22:01 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (one valid suggestion with a small permanent fix; orchestrator table allows address)
**After**: findings
**Decision**: address

Key the copy id of a zip with no 0x5455 and no usable events.csv on its raw DOS stamp (zone-free), not the PC-zone reading; test that importing the same zip under two TZ settings adds one copy.

## Implementation
**Status**: complete
**When**: 2026-11-26 22:05 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**PR**: #36 at `ecbdcd9`
**Addressed**: Integrated Review at `cd73f5f` (2026-11-26 22:00 -05:00)
**Commits**: ecbdcd9

### Actions
- [x] mtime_approx refs.csv copy (no 0x5455, no usable events.csv offset) is now identified by its raw DOS stamp: new `logger_session.zip_entry_time_key`, returned as an optional 4th element of `refs_csv_dated`, used by `_copy_id` in place of the PC-zone mtime; the decided time and interval are unchanged. New test `test_approx_copy_imported_in_two_zones_is_one_copy` imports one zip under TZ=UTC0 and TZ=JST-9 (approx times 9 h apart): one copy, flag 1 both times; it fails without the fix (2 copies) — `analysis/qgis/import_sessions.py:421`, `analysis/qgis/logger_session.py`

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 155 tests, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 94 tests, OK
- Full pre-commit pass left to the host. Note: a gpkg that stored an approx copy under the old id gets one more row for it on re-import, with the same time, so the flag does not change.

## Local Review
**Status**: complete
**When**: 2026-11-26 22:12 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**PR**: #36 at `46796b3`
**Depth**: Deep (reason: +1930 -156 over 22 files, cross-layer Android logger + Python importer)
**Must-fix**: 1 | **Suggestions**: 4

PR-mode round 1. Specialists: static (no project pre-commit config and no pre-commit/flake8/ruff installed; ran `git diff --check` clean and py_compile on the changed Python files; public-repo grep of added lines clean), governance (no ADRs in project; public-repo and no-delete rules hold: no row, photo or file is deleted), plan drift (PR B files match the plan's Files to Change), Claude adversarial, cross-model Gemini and Codex (both completed). Checks: analysis/qgis 155 OK (5 skipped), icecube 94 OK; gradle not run. No PR comments or reviews; no CI checks.

### Findings
- [x] (must-fix) The round-4 fix is incomplete: an mtime_approx copy now keeps one copy_id across PCs, but `gp.upsert("refs_copies", ...)` updates `mtime` on conflict, so re-importing the same zip on a PC in another zone overwrites the stored time with that PC's reading and can flip the flag, against the PR's order-independence claim. Store a zone-free time (zip_dos_seconds with an uncertainty covering ZONE_MAX_S + ZONE_TRUNC_S) or keep the first mtime/unc on conflict; extend `test_approx_copy_imported_in_two_zones_is_one_copy` to check the flag against an exact copy ~20-30 h away. Found by Claude adversarial, Gemini and Codex — `analysis/qgis/import_sessions.py:493`, `analysis/qgis/logger_session.py:291`
- [x] (suggestion) The seed for a gpkg from before refs_copies takes every `void IS NOT NULL` row, but main's importer wrote void=0 for every refs.csv row (pre-0.9.0 copies included), so those seed unflagged copies of unknown time (spurious ties, void 0 instead of the documented NULL); and the 'stored' copy never retires, so a later phone un-flag within 26 h cannot clear it. Seed only `void = 1 OR void_copy_mtime IS NOT NULL`, or document it — `analysis/qgis/import_sessions.py:272`
- [x] (suggestion) zip_zone_offset accepts a stale or reset events.csv mtime whenever its gap to the last line falls within -2..+120 s of a multiple of 15 min (about 1 in 7 random gaps), giving an offset wrong by k x 15 min with only the 1 h DST uncertainty; the docstring says such a case gives None. State the limit, or cap the accepted gap (e.g. require the offset to match the phone's zone column if one exists) — `analysis/qgis/logger_session.py:273`
- [x] (suggestion) A 0x5455 time is floored to whole seconds with uncertainty 0, while a folder copy uses sub-second st_mtime with uncertainty 0, so two copies in the same second can misorder (a cleared folder copy at T.2 beats a flagged zip copy written at T.7). Give a 0x5455 time uncertainty 1 s (or centre +0.5 s, unc 0.5) — `analysis/qgis/logger_session.py:159`
- [x] (suggestion) `undoDownNs` is cleared only in onUndo; a press that slides off UNDO leaves it set, and a later non-touch click (keyboard, accessibility) is timed at that old press and refused with the error cue. Reset it on ACTION_CANCEL and when a new UNDO is shown (safe side, rare) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:207`

## Implementation
**Status**: complete
**When**: 2026-11-26 22:23 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**PR**: #36 at `f435b62`
**Addressed**: Local Review at `46796b3` (2026-11-26 22:12 -05:00)
**Commits**: dd5e0ab, 216503a, c230eb4, 60d1ead, f435b62

### Actions
- [x] (must-fix, host-decided, owner may veto) An mtime_approx copy now stores its DOS date/time read as if UTC (zip_dos_seconds), uncertainty 26 h, so its time and copy id are the same on every PC and an upsert never changes them; zip_entry_time_key and the 4-tuple plumbing removed (redundant). Test imports one zip under TZ UTC0, JST-9 and EST5EDT with an exact copy 20 h after the stored time: same stored time, flag (tie, flagged), refs row and refs_copies rows each time (the old PC-zone reading in JST would clear it) — `analysis/qgis/logger_session.py` zip_entry_stamp, `analysis/qgis/import_sessions.py` _copy_id
- [x] (suggestion) Seed from an older gpkg only `void = 1 OR void_copy_mtime IS NOT NULL`; test that an untimed void 0 is not seeded and a later surely-newer un-flag clears a seeded copy (timed: past 26 h, within is a tie; untimed: any known time) — `analysis/qgis/import_sessions.py` ensure_schema
- [x] (suggestion) zip_zone_offset: the corrected events.csv mtime must be within -2..+10 s of its last wall_ms (was 120 s; the logger flushes every events.csv line, so its mtime is that line's), else None. A stale mtime still landing in that window of a 15 min multiple (about 1 in 75 random gaps) cannot be told from events.csv; documented as a limit in the docstring and logger/README.md. Test: stale gap 2x15 min + 60 s gives None, + 5 s is accepted (the limit) — `analysis/qgis/logger_session.py` zip_zone_offset
- [x] (suggestion) A 0x5455 time carries a 1 s uncertainty (EXT_S); test a folder copy cleared at T+0.2 s ties a zipped flag at T+0.7 s (stored T), 2 s apart does not — `analysis/qgis/logger_session.py` zip_entry_stamp, refs_csv_dated
- [x] (suggestion) undoDownNs reset on ACTION_CANCEL, on ACTION_UP outside the button plus touch slop, and when a new UNDO is shown with no press held (a held press keeps its earlier press-down, so it still cannot undo). No JVM test (MainActivity) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java`

### Checks
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 156, OK (skipped=5)
- `python3 -m unittest discover icecube/tests`: Ran 94, OK
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: BUILD OK, app-debug.apk 22:21 (C: 112.6 GB free before)
- Not pushed; the host owns the push.
