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
