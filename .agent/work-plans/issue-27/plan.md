# Plan: Logger: undo or flag an accidental capture

## Issue

https://github.com/rolker/ice_cube/issues/27

## Context

A capture made by accident (stray tap, volume key) goes into `points.csv` like any
other and has to be dropped by hand later. The owner's comment adds a real case: an
accidental capture 3 s after an object capture took the next photo, because a photo goes
to the latest capture. Main today: `Survey` keeps `rows` and rewrites all of `points.csv`
on every change (`savePoints`, so the file is not append-only); `events.csv` is
append-only; `Coverage.feeds(PointRow)` decides what feeds the overlay and
`CoverageWorker.update` rebuilds only when the session, frame, params or a shrinking row
count change; `refs.csv` is an append-only cross-session copy of ref rows read by
`PoleCheck` (last match in file order is the "last visit"; #23 is still open and has
changed none of this on main). Logger is 0.8.1 (build 12).

## Source of truth and wording

- `events.csv` is the append-only audit trail and the source of truth: `flag n=<n>
  reason=<r> via=undo|list`, `unflag n=<n>`, `photo moved n=<from> to=<to|own row>`.
- `points.csv` is a derived, rewritten file (as the README already says). Its new
  `void` (0/1) and `void_reason` columns are written from the current state.
- On session resume the void state is not replayed from events; it is read from the
  `void` column (the file the app already reloads rows from). If the two disagree the
  column wins and a later session can replay events by hand; stated in the README.
- The issue's "keep points.csv append-only" wording is corrected in the README, not
  implemented.

## Approach

PR A (data model and readers), in this order:

1. **PointRow** — add `voided` and `voidReason` fields, both `volatile` (Survey mutates them
   while the coverage worker and `MapView` read the same row objects; `Survey.rows()`
   copies the array, not the rows); HEADER gains `void,void_reason`
   at the end (COLUMNS 27 to 29); `parse` reads them when present, so files with 27 or 17
   columns read as not voided; `toCsv` writes `1`/`0` and the reason (`text()` escaping).
   `isCapture()` unchanged (a voided capture is still a capture row and keeps its `n`,
   numbers are never reused or renumbered). Add `isLive()` = `isCapture() && !voided`.
   Tests in `PointRowTest`: round trip, 17- and 27-column rows read void=false.
2. **Reasons and window as constants** — a small `VoidRule` in `core/` (pure, testable):
   `UNDO_WINDOW_MS = 8000`, `UNDO_ARM_MS = 750`, reason list (`accidental`, `bad pole placement`, `moved`,
   `other`), reason text sanitising (no commas/newlines), and the photo-target rules
   below.
3. **Survey.void / unvoid** (synchronized, then `savePoints()` and `svc.event`):
   `voidCapture(n, reason, via)` and `unvoidCapture(n)`; refuse a photo-class row (a
   photo row is deleted by voiding its capture, not flagged; its photo moves, see 5).
   `voidCapture(n, reason, via)` with `via=undo` checks the window itself: refuse when
   now minus the row's capture time (`elapsedNs`) exceeds `UNDO_WINDOW_MS`; every `via`
   refuses an already-voided row. Recompute `lastCapture` as the latest non-voided capture and `captures` count; bump a
   `voidGeneration` counter. Voiding is refused while a ref occupation or tilt set is
   running (it has no row yet; the existing cancel is the undo for those).
4. **Ref and tilt captures** — a ref or tilt capture is one `points.csv` row (a tilt's
   leans stay in `tilt_obs.csv` keyed by `n`), so voiding is the same row-level flag:
   - tilt: `tilt_obs.csv` is not rewritten; readers drop its rows whose `n` is voided
     (documented). Tilt event lines stay as history.
   - ref: it is also a row in `refs.csv`. Add `void,void_reason` to `refs.csv` rows
     (same migration as 0.6: an older header is rewritten once) and have
     `RefsStore.setVoid(ctx, session, n, voided, reason)` rewrite the matching row
     atomically (tmp + rename), in every refs.csv under `Storage.refsFiles` (one per
     storage root; `RefsStore.readAll` reads them all) that holds the (session, n) row,
     and bumps `refsGeneration` so the earlier-refs map layer reloads. The map-layer read
     in `RefsStore` skips voided rows, so `MainActivity`/`MapView` need no change for it. `PoleCheck.matches` skips voided rows, so a voided ref is
     never a "last visit", including after restart and in later sessions, exactly with the
     file-order semantics on main. Follow-up for #23: whatever it changes in
     reference selection must keep skipping `voided` (a note on #23, no code here).
     The pole-check notice and `poleWarning` raised by a ref that is then voided are
     cleared for that ref.
5. **Photos on void** — when the voided row carries photos, move them (files stay where
   they are; only the column changes) to the nearest earlier non-voided capture
   (`isLive()`, any of surface/object/ref, lower `n`, no time window because the user has
   said the capture was a mistake). If there is none, the photos go to a new class
   `photo` row (new `n`, position copied from the voided row's fields) so they are not
   lost and not attached to a bad capture. Log `photo moved`. Unflag does not move photos
   back (stated). If the target is itself later voided, the same rule runs again.
   `PhotoRule.attachTo` gets the non-voided `lastCapture` so a photo after an undo goes to
   the earlier capture only while within the 60 s window (stale: own row, as today).
   Tests: `PhotoRuleTest` plus a new `VoidRuleTest` (nearest earlier skipping voided and
   photo rows, none available, target voided later).
6. **Coverage hook only** (`Coverage.java`, kept to this to avoid clashing with #29's
   neighbour gradient): `feeds()` also requires `!r.voided`. In `CoverageWorker.update`
   (not Coverage.java) add a `voidGeneration` argument/overload so a change forces the
   median-first rebuild that #24 already does, from `rows` with voided ones skipped; an
   unvoid also rebuilds. Tests in `CoverageTest` (voided row does not feed, a node it
   alone turned red recovers) and `CoverageWorkerTest` (void generation triggers rebuild,
   restart rebuild from file skips it).
7. **Readers** — every reader of `points.csv` on main: `analysis/qgis/logger_session.py`
   and `import_sessions.py` (the only ones in the repo; `soundings.py` reads the gpkg
   `captures` table the importer writes). The importer DROPS rows with `void=1`: they
   never reach the gpkg, so `soundings.py` needs no change; the per-session report counts
   the dropped rows. A file with no `void` column reads as not voided. Test in
   `analysis/qgis/tests/test_import.py` with a synthetic session (`synth.py`). For #20
   (icecube) and #21 (analysis) write the requirement as a comment on each issue: skip
   `void=1` by default, report the skipped count, missing column means not voided, drop
   `tilt_obs.csv` rows of voided `n`, ignore `refs.csv` rows with `void=1`.
8. **Docs** — `logger/README.md`: points.csv column table and example header, events list
   (`flag`, `unflag`, `photo moved`), Photos (re-attach rule, no valid target), refs.csv
   columns and migration, the rewrite/source-of-truth wording; `docs/howto/ice-cube.md`
   one line that the readers skip voided rows. The Main-screen text (Undo, Recent
   captures) goes in PR B with the UI.
9. **Not wired to a build here** — no CI step is added; tests are the existing JVM unit
   tests (`./gradlew test`, `--max-workers=4` if parallelism is wanted).

PR B (after A), the UI, `MainActivity`, `MapView`, `Survey.Notice`, README Main screen,
and `logger/app/build.gradle` 0.9.0 / build 13 (the first build a user can see the
change in):

10. **Undo** — `Survey.Notice` gets an explicit `undoN` and `undoUntilMs`, set only by a
    capture's own notice (single surface/object capture, tilt end, ref end). `photoTaken`
    and other notices leave them unset, so a photo notice never offers UNDO of the
    capture it landed on. The bar shows `UNDO` while `undoUntilMs` has not passed (8 s;
    the notice itself stays `NOTICE_MS` = 10 s, label gone for the last 2 s). Taps on the
    bar are ignored for `UNDO_ARM_MS` (~0.75 s) after it appears, so the second tap of a
    double tap or a press in progress cannot undo. A newer notice (next capture, photo,
    ref status) replaces the notice and so ends the UNDO. A tap calls
    `voidCapture(n, "accidental", "undo")`, with toast and cue; `Survey` re-checks the
    window. The owner's 11-26 case (photo 3 s after the object capture) is not undoable
    from the bar by design; it goes through Recent captures (README says so).
11. **Flag later** — MENU "Recent captures" (last 10 non-photo rows: number, class, age,
    voided mark); tapping one offers Flag as bad (reason picker) or Un-flag. Map-tap
    flagging only if `MapView`'s hit test is simple, else dropped. `MapView` draws voided
    captures as a grey cross, leaves them out of the overlay; `info`/counts show live
    captures ("n captures, 1 flagged").
12. **PHOTO target indicator** — `PHOTO` gets a second line from `PhotoRule.attachTo` on
    the live `lastCapture`: `-> S12 (14 s)` or `-> own row`. UI only, no data change.

PR split, decided here: PR A (steps 1 to 9) is about 12 files of data model, coverage,
refs, importer and docs, with its logic covered by JVM unit tests and no UI to judge. PR B
(steps 10 to 12) is about 5 files of phone UI, to be reviewed by trying it on the phone.
The full change was about 16 files, past the dozen the host set, and the two halves need
different review. A has no user-visible trigger on its own; that is fine because the
version bump and the UI ship together in B.

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/PointRow.java` | void, void_reason fields and columns, isLive |
| `.../core/VoidRule.java` (new) | constants, reasons, photo target choice |
| `.../core/PhotoRule.java`, `core/PoleCheck.java` | uses non-voided last capture; skips voided refs |
| `.../core/Coverage.java` | `feeds()` excludes voided only |
| `.../core/CoverageWorker.java` | void generation forces rebuild |
| `.../Survey.java` | void/unvoid, photo move, lastCapture/captures recompute, events |
| `.../RefsStore.java`, `Storage.java` | void columns, header migration, setVoid over every refs.csv, refsGeneration bump, map read skips voided |
| `.../MainActivity.java`, `MapView.java` (PR B) | Undo notice with undoN/undoUntilMs and arm delay, Recent captures dialog, flagged drawing, PHOTO target line |
| `logger/app/src/test/.../core/` | PointRowTest, VoidRuleTest (new), PhotoRuleTest, PoleCheckTest, CoverageTest, CoverageWorkerTest |
| `analysis/qgis/logger_session.py`, `import_sessions.py`, `tests/` | drop voided rows, count per session |
| `logger/README.md`, `docs/howto/ice-cube.md` | docs (A); `logger/app/build.gradle` 0.9.0 / 13 (B) |

## Prior Art

Existing: the 0.6 `refs.csv` header migration and the pole-check/coverage rebuild paths
(reused as is); the cancel path for ref and tilt (kept as the undo for a running one).
Outside practice: soft delete with a flag plus an audit event, as in survey data
collectors that mark rather than delete observations. Rejected: deleting rows (loses
history, renumbers), replaying events on load (a second source to keep in step).

## Serves

Project goal "surveyed with the logger" (`docs/roadmap.md` Direction); the project has no
design document.

## Design Picture

No change to a design document (the project has none). The points.csv and refs.csv
formats change, recorded in `logger/README.md`.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo | Fixtures synthetic; no coordinates, names or session paths in tests or docs. The 2026-11-26 case is described without values. |
| Data stays honest | Nothing is deleted; events.csv is the trail; numbers are kept. |
| Docs follow behaviour | README, howto, version in the same PR. |
| No hardcoded values | Window and reasons are constants in `VoidRule`. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project keeps no ADRs) | No | n/a |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| points.csv columns | qgis importer, #20/#21 readers, README | Yes (importer, README); #20/#21 as issue comments |
| refs.csv columns | RefsStore migration, PoleCheck, Sharer zip copy | Yes |
| Coverage feed rule | #29 edits the same file | Only `feeds()` touched; merge order to be checked |
| Pole-check "last visit" | #23 (open) | Skip rule noted on #23; no reference-selection changes here |
| Photo attach rule | PhotoRule, README Photos | Yes |
| Voiding a ref | `refsGeneration` bump, earlier-refs layer, every refs.csv root | Yes |
| Importer drops voided rows | `soundings.py` reads the gpkg only, so unchanged | Yes |

## Open Questions

- Voiding a photo-less row only changes a flag, but a voided ref's `refs.csv` row is
  rewritten in place (the one non-append write to that file): acceptable?
- No earlier valid capture: photos go to a new `photo` row at the voided row's position
  (planned) versus staying on the voided row. Confirm.
- Re-attach has no 60 s limit on void (planned). Confirm.
- Map-tap flagging only if the hit test is simple; the MENU list is the committed path.
- Importer drops voided rows (host decision, 11-26).
- Version 0.9.0 (host decision), bumped in PR B.

## Estimated Scope

Two PRs: A (data model, coverage, refs, photo move, importer, docs; about 12 files) and B
(undo and flag UI, PHOTO target line, version 0.9.0; about 5 files).
