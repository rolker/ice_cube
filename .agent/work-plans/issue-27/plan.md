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

PR A (data model and readers), DELIVERED as PR #33 (merged, 6a9a9fe); kept as the record, in this order:

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
   `captures` table the importer writes). Keep and flag (owner's veto of 11-26, as in
   multibeam processing: flagged data is never deleted): the importer keeps rows with
   `void=1` and writes `void`, `void_reason` to the gpkg (captures and refs; a re-import
   updates the flag both ways), and the per-session report counts the flagged rows.
   `soundings.py` keeps them as rows with flag `void:<reason>` and use 0 (no site, no
   capture window, no anchor join) unless `[soundings] include_voided`; `export_labels`
   leaves them out unless `--include-voided`; `build_project` draws them apart. A file
   with no `void` column reads as not voided. Test in
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

PR B (part 2 of 2, revised 2026-11-26 after PR A merged): the phone UI plus the items PR A
deferred. Everything below is written against the code now on main (`Survey.voidCapture`,
`unvoidCapture`, `movePhotos`, `planPhoto`/`photoTaken`, `VoidRule`, `Survey.Notice`,
`MapView.drawCaptures`, `logger_session.zip_entry_mtime`, `soundings.clist_windows`).
Files: `Survey.java`, `core/VoidRule.java`, `core/PhotoRule.java`, `core/ShareZip.java`,
`MainActivity.java`, `MapView.java`, `logger/README.md`, `logger/app/build.gradle`,
`analysis/qgis/logger_session.py`, `analysis/qgis/soundings.py`, their tests. Steps 10 to
12 are the original PR B scope; 13 to 19 are the deferred items.

10. **Undo notice** — `Survey.Notice` gets `undoN` and `undoCaptureNs` (the capture row's
    `elapsedNs`), set only by a capture's own notice (single surface/object capture, tilt
    end, ref end in `Survey`; `photoTaken` and the others leave them unset, so a photo
    notice never offers UNDO of the capture it landed on). The bar shows `UNDO` while
    `VoidRule.undoLeftMs(captureNs, nowNs) > 0` (8 s; the notice stays `NOTICE_MS` = 10 s).
    Taps on the bar are ignored for `UNDO_ARM_MS` after UNDO first appears (the activity
    stamps the first frame that shows it), so the second tap of a double tap or a press in
    progress cannot undo. A newer notice replaces this one and ends the UNDO. A tap calls
    `voidCapture(n, "accidental", "undo")` with toast and cue; `Survey` re-checks the window.
    The 2026-11-26 case (photo 3 s after an object capture) is not undoable from the bar by
    design; it goes through Recent captures (README says so). Test: `VoidRuleTest` for
    `undoLeftMs` and the arm rule (a pure `VoidRule.armed(shownNs, nowNs)`); the bar wiring
    is tried on the phone.
11. **Recent captures dialog** — MENU "Recent captures": the last 10 non-photo rows
    (`Survey.recent(10)`, new, synchronized, newest first: label+n, class, age, flagged
    mark and reason). Tapping a live one opens the reason picker (`VoidRule.REASONS`, plus
    free text through `VoidRule.reason`) and calls `voidCapture(n, reason, "list")`; tapping
    a flagged one offers Un-flag (`unvoidCapture`). Disabled with a short message while a ref
    or tilt set runs. Map-tap flagging stays dropped unless `MapView`'s hit test turns out
    trivial (not planned). `info` and the REC chip already count live captures
    (`Survey.captures()`); add "1 flagged" to `info` from the same rows. Test: `recent` list
    logic as a pure `VoidRule.recent(rows, k)` in `VoidRuleTest`.
12. **PHOTO target line** — PHOTO's second line from `PhotoRule.attachTo(lastCapture, now)`
    (new `Survey.photoTarget()` returns `S12 (14 s)` or "own row"; the label refreshes with
    the screen's 1 s tick): `-> S12 (14 s)` or `-> own row`. UI only. Test: a pure
    `PhotoRule.targetText(...)` in `PhotoRuleTest` (live capture, stale, none, flagged last
    capture falls back).
13. **MapView drawing (flagged captures)** — `drawCaptures` currently draws every
    `isCapture` row as live. Flagged rows (`p.voided`) are drawn as a small grey cross (no
    fill, label `S12` in grey with a strike or `x`), never as the class symbol and never
    with its camera icon; photo rows made by a move (`note` starts `photos of voided`) keep
    the camera icon. Flagged rows stay out of the coverage overlay (already, via
    `Coverage.feeds`) and out of any "nearest capture" use. A toggle is not added. Test: the
    class-to-glyph choice is extracted as a pure `MapView`-free `VoidRule.glyph(row)`
    (LIVE, FLAGGED, PHOTO) in `VoidRuleTest`; the drawing itself is checked on the phone
    (and by a screenshot of a synthetic session, local frame only, no basemap).
14. **Camera-target fallback (Survey.java:643)** — `photoTaken` asks again with
    `PhotoRule.attachTo(lastCapture, p.pressNs)` when the planned capture was flagged while
    the camera was open, but `lastCapture` is the newest live capture, which can be a
    different (older) one and is then attached even when the 60 s window from the press has
    long passed, and the photo file name still carries the planned number (see 15). Fix:
    re-plan in one place, `Survey.replanAttach(p)`, which returns the live row only if
    `PhotoRule.attachTo` accepts it at `pressNs`, else null (own `photo` row, as for a stale
    one); the notice text says where it went. Test: `PhotoRuleTest` (flagged between press
    and return: older live capture inside the window, outside the window, none) driven
    through a pure helper `PhotoRule.retarget(planned, last, pressNs)`.
15. **Photo file-name number** — `planPhoto` names the file `photo_<attach|counter+1>_...`;
    after a move or a re-target the number in the name no longer matches the row that holds
    the photo. Decision: the name is a unique, time-ordered label and the row's `photo`
    column is the link, so files are never renamed (a rename would break `events.csv`
    lines, shares and the importer's lookup). Document it in the README (Photos) and the
    importer's docstring; the importer already joins by the column, not the name. Check
    `logger_session.photo_names` and `soundings` do not parse `n` out of the name (a test
    with a moved photo whose name number differs from its row). If a parse is found, switch
    it to the column.
16. **False `error:` event when a flag lands as a ref finishes** — `refsVoid` queues
    `RefsStore.setVoid` on the io thread, and `poleCheck` (which does the refs.csv append)
    is queued from `onEpoch`/`tiltEnd` after the lock is released, so a flag taken in that
    gap can queue `setVoid` first, find no row (holding 0) and log an `error:` line for a
    ref that is then appended unflagged-looking (the row is written from `r` at append time,
    which already carries `voided`, so the data is right and only the event is false).
    Fix: queue `poleCheck` inside the same synchronized block that adds the row, so the
    io order is always append then setVoid; and make `VoidRule.refsVoidEvent` treat
    `holding == 0` for a row that is not yet appended as "pending", not an error (the
    append already wrote its state). First reproduce in a unit test of `refsVoidEvent`
    and of the ordering, by a small `Survey`-free sequencer if the Android types block a
    direct test; otherwise state the reasoning in the test comment. If the cause turns out
    different when read in code, record it in Implementation Notes.
17. **Undo window time source** — the bar's `untilMs` uses `SystemClock.uptimeMillis` (stops
    in deep sleep) while `voidCapture` re-checks with `elapsedRealtimeNanos` against the
    row's `elapsedNs`; a phone that slept between a capture and the tap disagrees with
    itself. Make `elapsedRealtimeNanos` the only clock for the undo: `Notice` carries
    `undoCaptureNs` (step 10), the bar and `Survey` both call `VoidRule.undoLeftMs`;
    `NOTICE_MS` display expiry may stay on uptime (cosmetic). Test: `VoidRuleTest` (window
    edges, a negative difference, same inputs the bar and `Survey` use).
18. **F1: zip DOS times in the importing PC's zone** — the importer's fallback in
    `zip_entry_mtime` reads the DOS date/time in the PC's zone, so a refs.csv copy zipped
    on a phone in another zone misorders and the newest-copy rule can pick the wrong void
    flag. Fix at the source: `ShareZip.addFile` writes the zip extended timestamp (extra
    0x5455, flags 1, 4-byte UTC mtime) on every entry (`ZipEntry.setLastModifiedTime`
    writes it under `java.util.zip` on Android API 26+; check minSdk and otherwise append
    the extra bytes by hand with a tested `ShareZip.extendedTimestamp(long)`), which the
    importer already prefers. For zips made by 0.8.x and 0.9.0-before-this (no 0x5455) treat
    copies whose DOS times differ by less than the largest zone offset (26 h) as ties, so
    the safe side (voided) wins, and report them flagged in the import report. Tests:
    `ShareZipTest` (the extra field bytes round trip through `java.util.zip.ZipInputStream`
    and a Python reader), `analysis/qgis/tests/test_import.py` (an extended-timestamp zip
    orders correctly across a simulated zone; a DOS-only zip within 26 h is a tie).
19. **F2: real PHOTO in the same second as a voided capture** — `clist_windows` skips any
    live photo row whose `utc` (1 s) equals a voided capture's, so a real PHOTO pressed in
    the same second loses its window. Require the note too: skip only a photo row with the
    same `utc` and a `note` starting `photos of voided n=<n>` where n is that voided capture
    (`logger_session` already reads `note`; confirm it reaches the clist dict, add it if
    not). Test in `analysis/qgis/tests/test_soundings.py`: a real photo row at the same
    second keeps its window; the moved-photo row at that second still does not.
20. **Docs and version** — `logger/README.md`: Main screen (Undo bar and its 8 s, Recent
    captures, the PHOTO target line, flagged drawing as a grey cross), Photos (file name
    number is not the row number, step 15), the zip timestamp (step 18); `docs/log.md` one
    entry. `logger/app/build.gradle`: versionName 0.9.0, versionCode 13. Tests are the
    JVM unit tests (`./gradlew test --max-workers=4`) and `python -m pytest
    analysis/qgis/tests`; the UI is tried on the phone (adb install, per the logger README)
    before merge, and the PR text says what was and was not tried there.

PR split: PR A (steps 1 to 9) is delivered as #33 (merged, 6a9a9fe). PR B (steps 10 to 20)
is about 12 files; the owner reviews the UI on the phone, the importer fixes by tests.

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
| `.../MainActivity.java`, `MapView.java`, `Survey.java`, `core/VoidRule.java`, `core/PhotoRule.java`, `core/ShareZip.java` (PR B) | Undo notice (undoN, undoCaptureNs, arm delay, one clock), Recent captures dialog, flagged drawing, PHOTO target line, re-target fallback, pole-check ordering, zip extended timestamp |
| `analysis/qgis/logger_session.py`, `soundings.py`, tests (PR B) | F1 zip tie rule, F2 photo-row note match |
| `logger/app/src/test/.../core/` | PointRowTest, VoidRuleTest (new), PhotoRuleTest, PoleCheckTest, CoverageTest, CoverageWorkerTest |
| `analysis/qgis/logger_session.py`, `import_sessions.py`, `tests/` | keep voided rows with void, void_reason columns; count per session |
| `analysis/qgis/soundings.py`, `export_labels.py`, `build_project.py`, plugin | flag void rows out of use (include_voided to use them), export filter and count, flagged drawing and map tip |
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
| Importer keeps and flags voided rows | `soundings.py`, `export_labels.py`, `build_project.py` read the gpkg `void` column | Yes |

## Open Questions

- Voiding a photo-less row only changes a flag, but a voided ref's `refs.csv` row is
  rewritten in place (the one non-append write to that file): acceptable?
- No earlier valid capture: photos go to a new `photo` row at the voided row's position
  (planned) versus staying on the voided row. Confirm.
- Re-attach has no 60 s limit on void (planned). Confirm.
- Map-tap flagging only if the hit test is simple; the MENU list is the committed path.
- Importer keeps voided rows with a flag; nothing is deleted (owner's veto of 11-26 of
  the earlier host decision to drop them).
- Version 0.9.0 (host decision), bumped in PR B (step 20).
- Step 15 keeps photo file names as written (no rename): confirm.
- Step 16's cause is a reading of the code, not yet reproduced; the step starts by checking it.

## Estimated Scope

PR A delivered (#33). PR B: about 12 files (UI, photo/undo fixes, zip timestamp, importer fixes, docs, version 0.9.0).
