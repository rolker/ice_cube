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

PR B (part 2 of 2, revised 2026-11-26 after PR A merged and again after plan review at
7bfe31d): the phone UI plus the items PR A deferred. Written against main (5da4114):
`Survey.voidCapture`, `unvoidCapture`, `movePhotos`, `planPhoto`/`photoTaken`, `VoidRule`
(`undoOpen`, `UNDO_WINDOW_MS`, `UNDO_ARM_MS`), `Survey.Notice` (already carries `at`, the
capture row), `MapView.drawCaptures`, `logger_session.zip_entry_mtime`,
`soundings.clist_windows`, `import_sessions.import_refs_csv`. Keep-and-flag holds
throughout: nothing in PR B deletes a row, a photo or a file.
Files: `Survey.java`, `core/VoidRule.java`, `core/PhotoRule.java`, `core/ShareZip.java`,
`MainActivity.java`, `MapView.java`, `logger/README.md`, `logger/app/build.gradle`,
`analysis/qgis/logger_session.py`, `import_sessions.py`, `soundings.py`, their tests.

10. **Undo notice (includes the old step 17)** — `Survey.Notice` gets one field, `boolean
    undoable`, set only by a capture's own notice (single surface/object capture, tilt end,
    ref end); the row to undo is `Notice.at` (its `n` and `elapsedNs`), so no row data is
    copied. `photoTaken`, "Photo on" and the other notices leave it false, so a photo notice
    never offers UNDO of the capture it landed on. One clock: the bar and `Survey` both use
    `elapsedRealtimeNanos` through the existing `VoidRule.undoOpen(at.elapsedNs, now)`
    (8 s, `UNDO_WINDOW_MS`; the notice's display expiry `NOTICE_MS` = 10 s may stay on
    uptime, cosmetic). A ref's notice is made by `poleCheck`, async and after
    `RefsStore.readAll`, so its UNDO can show with under 8 s left; accepted, the window is
    counted from the row's own `elapsedNs`, not from when the notice appears. Taps are
    ignored for `UNDO_ARM_MS` (750 ms) after UNDO first appears (the activity stamps the
    first frame that shows it), via a pure `VoidRule.armed(shownNs, nowNs)`. A newer notice
    replaces this one and ends the UNDO. A tap calls `voidCapture(n, "accidental", "undo")`
    with toast and cue; `Survey` re-checks the window and refuses with "Too late" outside
    it. The 2026-11-26 case (photo 3 s after an object capture) is not undoable from the
    bar by design; it goes through Recent captures (README says so). Test: `VoidRuleTest`
    for `undoOpen` edges, a negative difference, and `armed`; the bar wiring is on the phone
    checklist (step 20).
11. **Recent captures dialog** — MENU "Recent captures": the last 10 non-photo rows
    (`Survey.recent(10)`, new, synchronized, newest first: label+n, class, age, flagged
    mark and reason). Tapping a live one opens the reason picker (`VoidRule.REASONS`, plus
    free text through `VoidRule.reason`) and calls `voidCapture(n, reason, "list")`; tapping
    a flagged one offers Un-flag (`unvoidCapture`). Disabled with a short message while a ref
    or tilt set runs. A flagged capture is flagged only, never deleted, and the reason is
    kept. A voided capture's running ref or tilt is handled by the existing rule (the
    reason decides whether its stop is kept, in the importer). Map-tap flagging stays
    dropped (not planned). `info` and the REC chip already count live captures
    (`Survey.captures()`); add "1 flagged" to `info` from the same rows. Test: `recent` list
    logic as a pure `VoidRule.recent(rows, k)` in `VoidRuleTest`.
12. **PHOTO target line** — PHOTO's second line from `PhotoRule.attachTo(lastCapture, now)`
    (new `Survey.photoTarget()` returns `S12 (14 s)` or "own row"; refreshed by the
    screen's 1 s tick): `-> S12 (14 s)` or `-> own row`. UI only. Test: a pure
    `PhotoRule.targetText(...)` in `PhotoRuleTest` (live capture, stale, none, flagged last
    capture falls back).
13. **MapView drawing (flagged captures)** — `drawCaptures` currently draws every
    `isCapture` row as live. Flagged rows (`p.voided`) are drawn as a small grey cross (no
    fill, label `S12` in grey with a strike or `x`), never as the class symbol and never
    with its camera icon; photo rows made by a move (`note` starts `photos of voided`) keep
    the camera icon. Flagged rows stay out of the coverage overlay (already, via
    `Coverage.feeds`) and out of any "nearest capture" use. A toggle is not added. Test: the
    class-to-glyph choice is extracted as a pure `MapView`-free `VoidRule.glyph(row)`
    (LIVE, FLAGGED, PHOTO) in `VoidRuleTest`; the drawing is checked on the phone. The
    README/log screenshot, if wanted, is a field session shown in the local frame with no
    basemap (cropped to local axes), taken from the phone; a synthetic session is also
    possible: `analysis/qgis/tests/synth.py` makes one, and the checklist (step 20) says
    how it gets onto the phone. The screenshot is optional, never a merge condition.
14. **Camera-target fallback — dropped, lock-in test only.** The plan review found the
    premise wrong on main: `PhotoRule.attachTo(PointRow, photoNs)` already returns -1
    unless `0 <= photoNs - last.elapsedNs <= 60 s`, and `photoTaken` (Survey.java:643)
    passes `p.pressNs`, so the re-ask already applies the 60 s window at the press, the
    same rule `planPhoto` uses; and a flag cannot happen while the camera is in front
    (Recent captures and UNDO live in `MainActivity`). No `replanAttach`, no
    `PhotoRule.retarget`. Add a `PhotoRuleTest` lock-in: a flagged planned capture, an older
    live capture inside the window from the press attaches, outside the window gives -1
    (own row). The only real residue, the file name number, is step 15.
15. **Photo file-name number** — `planPhoto` names the file `photo_<attach|counter+1>_...`;
    after a move or a re-target the number in the name no longer matches the row that holds
    the photo. Decision: the name is a unique, time-ordered label and the row's `photo`
    column is the link, so files are never renamed (a rename would break `events.csv`
    lines, shares and the importer's lookup). Document it in the README (Photos) and the
    importer's docstring. The importer does parse the name and the original event today:
    `logger_session.load_session` (about lines 603-617) takes a photo's `n` from `by_file`
    (the `photo` column), else the `photo file=... attached to|own row n=` event, else
    `PHOTO_RE`'s number from the name, and `attached` from the original event only, so a
    photo moved by a void to a new `photo` row keeps `attached="capture"` and the old `n`
    when the column and the event disagree. Fix: when a row's `photo` column lists the file,
    the column's row is the only source of `n` and of `attached` (`"own row"` when that row's
    class is `photo`, else `"capture"`); the event and the name are used only for an orphan
    file (no row lists it). The `photo moved` events stay unread. Test:
    `analysis/qgis/tests/test_import.py` with a moved-to-own-row photo (name number and
    event say capture n=3, column says the `photo` row n=7): `n` 7, `attached` "own row".
16. **False `error:` event when a flag lands as a ref finishes** — cause confirmed in code:
    `onEpoch` queues `poleCheck` (which does the refs.csv append) after its synchronized
    block (`Survey.java:236-238`, `run(() -> poleCheck(f))` on the single-thread executor),
    so a `voidCapture` in that gap queues `setVoid` first, finds no row (holding 0) and logs
    an `error:` for a ref that is then appended with the right state (the row is written
    from `r`, which already carries `voided`; only the event is false). `finishTilt` is a
    synchronized method that already queues `poleCheck` under the lock (`:425-428`), so only
    the occupation path needs the change. Fix, and only this: move that `run(() ->
    poleCheck(f))` inside the synchronized block of `onEpoch`, so the io order is always
    append then setVoid. `VoidRule.refsVoidEvent` is left alone: `holding == 0` stays an
    error, because when `RefsStore.append` threw it is a true error, and "pending"
    treatment would hide it. The gap is microseconds and a ref's UNDO notice is set only
    by `poleCheck` after the append, so the UI cannot reproduce it and no Survey-free
    sequencer is built; the comment on the moved line and in `VoidRuleTest` states the
    reasoning (keeps `refsVoidEvent` holding-0 error test as is).
17. **Undo window time source — folded into step 10** (one clock, `elapsedRealtimeNanos`
    via `VoidRule.undoOpen`; `MainActivity` has no undo yet so there is nothing to
    reconcile).
18. **F1: zip DOS times in the importing PC's zone** — the importer's fallback in
    `zip_entry_mtime` reads the DOS date/time in the PC's zone, so a refs.csv copy zipped
    on a phone in another zone misorders and the newest-copy rule can pick the wrong void
    flag. Two parts.
    (a) Fix at the source: `ShareZip.addFile` writes the zip extended timestamp (extra
    0x5455, flags 1, 4-byte little-endian UTC mtime) on every entry. The extra is built by
    hand with a pure, tested `ShareZip.extendedTimestamp(long epochSeconds)` and set with
    `ZipEntry.setExtra`, so the bytes do not depend on the platform (the JVM unit tests run
    on the desktop JDK's `ZipOutputStream`, which says nothing about Android's libcore, so
    `setLastModifiedTime` is not relied on even though minSdk is 26). Tests: `ShareZipTest`
    (the bytes of `extendedTimestamp`; the extra round-trips through `ZipInputStream`; a
    Python reader in `test_import.py` reads it with `zip_entry_mtime`). Phone check
    (step 20): share a zip from the phone, read `info.extra` with the importer's
    `zip_entry_mtime`, expect the file's UTC mtime.
    (b) Old zips (0.8.x, 0.9.0-before-this) have only DOS times. Each logger zip carries
    `events.csv` whose last line has `wall_ms` (UTC) and whose DOS entry time is that
    file's mtime in the phone's zone; the difference between the two, rounded to 15 min,
    is that zip's zone offset. `logger_session` computes the offset per zip and corrects
    the DOS times of that zip's entries by it (`zip_entry_mtime` takes an optional
    `zone_offset_s`). A zip with no usable `events.csv` (absent, empty, no `wall_ms`, or an
    offset beyond +-14 h) keeps the PC-zone reading and is marked `mtime_approx`.
    Comparison of copies (`import_refs_csv` newest-copy rule and the stored-flag SQL
    alike): a copy with 0x5455 compares by that UTC time; a corrected DOS time compares as
    UTC seconds; the mixed case (one copy with 0x5455, one DOS-only) compares the
    extended time against the corrected DOS time, and when either side is `mtime_approx`
    the comparison is a tie only if the two times are within 26 h (the largest zone
    offset), else the later one wins. A tie, as before, lets flagged win.
    (c) The same rule goes into `import_sessions.import_refs_csv`'s stored-flag UPDATE
    (the `void_copy_mtime` comparison, about lines 405-412): the stored time is the
    corrected one, `void_copy_mtime`'s tie branch is applied only under the same
    `mtime_approx` condition, so a re-run decides as a first run does.
    (d) The import report lists only the rows whose flag a tie decided (session, ref
    number, which way), not every row of tied copies.
    Tests in `analysis/qgis/tests/test_import.py` (synthetic zips from `synth.py` with
    DOS times in a simulated zone): extended-timestamp zip orders correctly across a zone;
    DOS-only zip with `events.csv` is corrected by the 15-min-rounded offset; a zip with
    no `events.csv` within 26 h is a tie decided flagged and reported; the mixed case;
    and the un-flag case: flag, export, un-flag, export within 26 h, both zips with
    `events.csv`, the later un-flagged copy wins (not a tie) and a re-run keeps it.
19. **F2: real PHOTO in the same second as a voided capture** — `clist_windows` skips any
    live photo row whose `utc` (1 s) equals a voided capture's, so a real PHOTO pressed in
    the same second loses its window. Require the note too: skip only a photo row with the
    same `utc` and a `note` that equals `"photos of voided n=<n>"` exactly, for an n whose
    capture is voided (an exact comparison, or a regex ending at the string end, so `n=1`
    never matches `n=12`). `note` is in the importer's `captures` table (import_sessions
    columns) but not in `soundings`' clist query: add `note` to the `cols` list and select
    in `soundings.py` (about lines 627-634) so it reaches the clist dict. A user-edited
    note on the moved row (the `note` column is a preserved user column) just loses the
    skip, which is the safe side. Test in `analysis/qgis/tests/test_soundings.py`: a real
    photo row at the same second keeps its window; the moved-photo row at that second
    still does not; `n=1` vs `n=12` do not cross.
20. **Docs, version and phone try-out** — `logger/README.md`: Main screen (Undo bar and its
    8 s, Recent captures, the PHOTO target line, flagged drawing as a grey cross), Photos
    (file name number is not the row number, step 15), the zip timestamp (step 18);
    `docs/log.md` one entry. `logger/app/build.gradle`: versionName 0.9.0, versionCode 13.
    Tests are the JVM unit tests (`./gradlew test --max-workers=4`) and `python -m pytest
    analysis/qgis/tests`. The repo has JVM JUnit tests only (no Robolectric), so the UI is
    checked only on the phone (adb install, per the logger README), and the PR text
    reports against this list:
    - UNDO shows after surface, object, tilt and ref notices, never on a "Photo on" notice.
    - A double tap or held press in the first 750 ms does nothing.
    - UNDO goes at 8 s. Screen off for more than 8 s, then on: no UNDO, or a "Too late"
      refusal.
    - A newer notice removes UNDO.
    - Recent captures is disabled during a ref or tilt; lists 10 rows, newest first;
      free-text reasons with a comma come out clean (in refs.csv and the import); Un-flag
      works.
    - `info` shows "1 flagged"; the REC count drops.
    - A flagged capture draws as a grey cross with no camera; a moved photo row keeps its
      camera.
    - The coverage overlay recovers.
    - The PHOTO line counts up, switches to "own row" after 60 s and after flagging the
      last capture.
    - A shared zip carries 0x5455 (step 18a phone check).
    - A flagged ref is skipped by the next pole check.
    Putting a synthetic session on the phone (only if a screenshot of one is wanted): run
    `analysis/qgis/tests/synth.py` to write a session folder to a scratch directory, then
    `adb push` it into the logger's session folder per the logger README; otherwise take
    the screenshot from a field session and crop it to local axes with no basemap. Nothing
    from either goes into the repo except such a screenshot.

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
| `.../MainActivity.java`, `MapView.java`, `Survey.java`, `core/VoidRule.java`, `core/PhotoRule.java`, `core/ShareZip.java` (PR B) | Undo notice (`undoable` flag, arm delay, one clock), 0x5455 written by hand, Recent captures dialog, flagged drawing, PHOTO target line, pole-check ordering (move inside the lock), zip extended timestamp |
| `analysis/qgis/logger_session.py`, `import_sessions.py`, `soundings.py`, tests (PR B) | F1 per-zip zone offset and tie rule (also in the stored-flag SQL), photo `n`/`attached` from the column, F2 exact note match (`note` added to the clist query) |
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
- Step 16's cause is confirmed by reading the code but cannot be reproduced from the UI; the fix is one line and the reasoning is in the comment.
- Step 14 is dropped (the 60 s window already applies at the press); say if a different rule (nearest earlier live, no window) is wanted for a photo whose target was flagged.

## Estimated Scope

PR A delivered (#33). PR B: about 12 files (UI, photo/undo fixes, zip timestamp, importer fixes, docs, version 0.9.0).
