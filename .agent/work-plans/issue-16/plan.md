# Plan: Logger: voice notes attached to captures

## Issue

https://github.com/rolker/ice_cube/issues/16

## Context

The logger attaches photos to captures (PhotoRule 60 s window, own `photo` row otherwise) and
moves them when a capture is flagged (VoidRule, #27). It has no spoken note. Host decisions
(Issue Review + Checkpoint): a NOTE button (tap start, tap stop, vibration and tone), mono AAC
m4a ~32 kbps, 60 s cap with auto-stop cue, attach by PhotoRule's window and own-row fallback,
notes move with photos when a capture is flagged, RECORD_AUDIO while the activity is visible,
audio only in the session folder and shared zip. No speech-to-text in this PR.

## Approach

1. **`NoteRule` (new, `core/`)** — pure logic: `attachTo` delegates to `PhotoRule.attachTo` (same window and
   live-row rule, no duplicated code), `targetText`, `fileName(n, utc)` -> `note_<n>_<utc>.m4a`,
   `add(list, name)` (';' separated), `MAX_MS = 60_000`, `BITRATE = 32_000`.
2. **`PointRow`** — new column `voice` appended after `void_reason` (HEADER, `COLUMNS` 30, `REFS_HEADER`
   follows). `parse` reads it only when the row has the column, so 0.9/0.10 rows load with `voice = ""`.
   Appended last (not after `photo`) so positional parsing of old rows stays valid.
3. **`VoidRule`** — a voided row's `voice` entries move with its photos: same target
   (`photoTarget`), same own-row fallback (a class `photo` row, which then holds only a note); extend the
   move helper so both columns move in one event.
4. **`Survey`** — `planNote()` / `noteTaken()` / `noteCancelled()` mirroring `planPhoto` / `photoTaken` /
   `photoCancelled`, creating `notes/`, writing the `voice` column, an `events.csv` line
   (`note file=notes/... attached to n=` / `own row n=`), and `moveNotes` inside the existing flag path.
5. **`NoteRecorder` (new, Android)** — wraps `MediaRecorder` (MPEG_4/AAC, mono, 32 kbps, 16 kHz, max
   duration 60 s via `setMaxDuration`, `OnInfoListener` for auto-stop); zero-length or failed file is deleted.
6. **`MainActivity`** — NOTE button beside PHOTO (second line shows the target like PHOTO, then elapsed
   seconds while recording); tap start, tap stop; `Feedback` vibration and tone at start, stop and auto-stop;
   runtime `RECORD_AUDIO` request on first tap; stop and finalise the note in `onPause` (foreground
   only, no use from the service); keep the pending note across recreation like `photoPath`. No volume-key
   or PHOTO long-press use. A capture mark while recording is still allowed; the note attaches by the
   time NOTE was pressed.
7. **Manifest** — `RECORD_AUDIO`; `<uses-feature android:name="android.hardware.microphone" android:required="false"/>`.
8. **`Sharer` / `ShareZip`** — include `notes/` (one level down, like `photos/`); m4a STORED. Zip size is
   about 0.25 MB per 60 s note.
9. **Importer** — `logger_session.py`: parse the `voice` column (absent in old sessions), `note_names()`,
   `extract_note()`, pass `voice` through to captures/photos/refs; `import_sessions.py`: add the `voice`
   column to the GeoPackage tables, copy files to `notes/<session>/<file>` beside `photos/` (local data
   directory, gitignored like photos), `--no-photos` also skips them; `build_project.py`: show the file
   name in the form and a `file:///` link in the map tip so QGIS can play it.
10. **Docs** — `logger/README.md`: NOTE button, column, `notes/` folder, flagging moves notes, zip
    contents, privacy line (notes may contain names; never commit them). Bump `versionName`/`versionCode`.
11. **Tests (JUnit, synthetic data only)** — `NoteRuleTest` (window edge, voided last row, no capture,
    file name, add); `PointRowTest` (round trip with `voice`, quoting, old 17/27/29-column rows parse);
    `VoidRuleTest` (notes move with photos, own-row fallback, no time limit); `ShareZipTest` (`notes/`
    included and STORED). Importer: pytest with a generated fixture session holding a tiny placeholder
    `.m4a` byte file, checking column and copied path. Recording, permission and button UI are not unit
    testable: a manual device check (record, auto-stop at 60 s, flag a capture, zip, import) goes in the PR.

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/NoteRule.java` | new |
| `.../core/PointRow.java`, `.../core/VoidRule.java` | `voice` column; notes move on flag |
| `.../Survey.java` | plan/taken/cancelled note, move on flag (shared with #15, #35) |
| `.../MainActivity.java` | NOTE button, permission, recording state (shared with #15, #35) |
| `.../NoteRecorder.java`, `.../Sharer.java`, `.../core/ShareZip.java`, `Feedback.java` | recorder; zip `notes/`; cue |
| `logger/app/src/main/AndroidManifest.xml`, `logger/app/build.gradle`, `logger/README.md` | permission, version, docs |
| `analysis/qgis/logger_session.py`, `import_sessions.py`, `build_project.py` | pass `voice` through, copy audio, QGIS link |
| `logger/app/src/test/.../core/*Test.java`, `analysis/` importer test | tests above |

## Prior Art

PhotoRule, `planPhoto`/`photoTaken`, VoidRule photo move, ShareZip and the importer's photo path are
the template; all reused, nothing parallel. MediaRecorder is the platform recorder and the only
option that shares nothing with SpeechRecognizer, which is why STT is out. Rejected: PHOTO long-press
(accidental starts with gloves), volume keys (already used), separate `.3gp`/`.wav` (larger or
lower quality for speech).

## Serves

Project has no goals document or design document: none. Serves the logger's field-capture purpose
(winter gloved operation).

## Design Picture

No change to the design picture (the project keeps no design document). The `points.csv` column contract and
zip contents change; `logger/README.md` documents them and is updated in the same PR.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| A change includes its consequences | Flag/undo, importer, zip, README and old-session loading all in the plan |
| Test what breaks | Pure rules unit-tested; recorder/UI manual check stated |
| Only what's needed | No STT, no transcription; one new column |
| Small steps | Lands after #15 and #35 to avoid conflicts |
| Leave a trail; start limits strict | Audio stays in the session folder and zip; README privacy note; tests use no real audio |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| Workspace ADRs | No | Project-repo feature; no workspace infrastructure changes |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `points.csv` columns | `PointRow`, importer, README, any `icecube/` session reader (check #20) | Yes; `icecube/` check at implementation |
| Flag/undo (#27) | `VoidRule`, `Survey`, events, README | Yes |
| Zip contents | `Sharer`, `ShareZip`, test | Yes |
| #15/#35 changes in `Survey`/`MainActivity` | Rebase and re-read before starting | Yes (sequencing) |

## Open Questions

- Does #15 or #35 add a `points.csv` column too? Then `voice` goes after theirs and `COLUMNS` follows at implementation.
- Is desktop (local, never online) transcription wanted later? Separate issue if so.

## Estimated Scope

Single PR, after #15 and #35.
