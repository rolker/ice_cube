---
issue: 16
---

# Issue #16 — Logger: voice notes attached to captures

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #16

### Scope Assessment

**Well-scoped?** Yes, with a sequencing note. One PR is feasible: record button, `notes/` folder, one new `points.csv` column, zip inclusion, importer pass-through. Speech-to-text is a separate, optional layer and should be split off if it is wanted (see Recommendations).
**Right repo?** Yes. Logger app under `logger/`, plus the importer under `analysis/qgis/`.
**Serves**: none written down for the project (no goals document found); it serves the logger's field-capture purpose, and the owner's "winter gloved ops" request. Project design section: none.
**Design picture?** No change to a design document (the project has none for this). It does change the `points.csv` column contract and the session zip contents, which `logger/README.md` documents, so that README is the document to update.
**Dependencies**: should be implemented after #15 (in implementation) and #35 (auto capture, next): all three touch `Survey` and `MainActivity`, and #35 changes when captures happen, which changes what "a note right after a capture" attaches to. #27 (flag/undo) is merged and matters: see Consequences.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Put each thing at the level it applies to | OK | Voice notes belong to the logger and the importer, not to the rink; nothing site-specific is needed. |
| Test what breaks | Watch | Attach rule (recent capture vs own row), CSV column and zip inclusion are pure logic: follow the PhotoRule/PointRow/ShareZip test pattern. Recording and permission UI are not unit-testable; say so in the plan. |
| A change includes its consequences | Action needed | The new column must be handled by flag/undo (#27): when a capture is flagged, photos move to the nearest earlier good capture. The plan must say what happens to notes on a flagged row (move like photos, or stay), and the README, importer and tests must match. |
| Only what's needed | Watch | On-device speech-to-text is a second feature with its own failure modes. Do not bundle it unless the owner wants it. |
| Small steps; step back when they stop converging | Watch | Land after #15 and #35 to avoid conflicts in the same files; keep STT out of the first PR. |
| Leave a trail; start limits strict | OK | Privacy limit starts strict: audio only in the session folder and zip, never the repo (CLAUDE.md public-repo rule). |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| Workspace ADRs (0004, 0005, 0013 etc.) | No | Logger feature in a project repo; no workspace infrastructure, hooks or skills change. |

### Consequences

- New `points.csv` column after `photo`: update `PointRow`, its tests, the README column description, and every reader (`analysis/qgis/logger_session.py`, `import_sessions.py`, and anything in `icecube/` that reads sessions, e.g. #20) so old sessions without the column still load.
- `Sharer`/ShareZip must include `notes/` and its test must cover it; zip size grows (m4a at about 1 MB/min at 64 kbps AAC, much less at speech bitrates), so size the cap.
- Manifest needs `RECORD_AUDIO` (runtime permission). The logger runs a foreground service of type connected device; check whether recording from the activity while the screen is on is enough, since Android 11+ restricts background mic use. Prefer recording only while the activity is visible.
- Volume keys are already used by MARK/long-press gestures (MainActivity); a new gesture must not collide with them and must not fire a capture by accident with gloves.
- Importer: GeoPackage gets the note file name; QGIS playback needs the path resolved relative to the session folder (as for photos).
- Tests and fixtures must use synthetic or silent audio, never real recordings; the repo must contain no real voice notes.

### Recommendations

- Sequence implementation after #15 and #35; review again for conflicts at that time.
- Decide the control: a dedicated NOTE button (tap to start, tap to stop, with a clear cue and vibration) is the most glove-safe; a long-press of PHOTO risks accidental starts; avoid volume keys (already overloaded).
- Audio format: AAC in m4a via MediaRecorder at mono, speech bitrate (about 24-32 kbps), with a hard cap such as 60 s and an auto-stop with a cue.
- Treat speech-to-text as optional and offline-only: Android's SpeechRecognizer generally cannot share the microphone with a MediaRecorder, so it cannot run on the same recording, and offline model availability varies by phone and language pack. Recommended default: keep audio as the record and leave the `note` column for typed or later desktop transcription (an optional importer-side step, run locally, never an online service). Only add on-device STT if the owner asks.
- Define attach rule as for photos (PhotoRule): reuse the same time window and the same own-row-with-position fallback, and reuse PhotoRule's logic rather than duplicating it.
- Privacy: audio lives only in the session folder and the shared zip; confirm `.gitignore` and the importer never copy audio into the repo, and say in the README that notes may contain names.
- Define behaviour on flag/undo for notes on a flagged capture, in the same way as for photos.

### Actions
- [ ] The new column must be handled by flag/undo (#27): when a capture is flagged, photos move to the nearest earlier good capture. The plan must say what happens to notes on a flagged row (move like photos, or stay), and the README, importer and tests must match.
- [ ] Sequence implementation after #15 and #35; review again for conflicts at that time.
- [ ] Decide the control: a dedicated NOTE button (tap to start, tap to stop, with a clear cue and vibration) is the most glove-safe; a long-press of PHOTO risks accidental starts; avoid volume keys (already overloaded).
- [ ] Audio format: AAC in m4a via MediaRecorder at mono, speech bitrate (about 24-32 kbps), with a hard cap such as 60 s and an auto-stop with a cue.
- [ ] Treat speech-to-text as optional and offline-only; default to keeping audio as the record and leaving `note` for typed or later desktop transcription; add on-device STT only if the owner asks.
- [ ] Define attach rule as for photos (PhotoRule): reuse the same time window and the same own-row-with-position fallback, and reuse PhotoRule's logic rather than duplicating it.
- [ ] Privacy: audio lives only in the session folder and the shared zip; confirm `.gitignore` and the importer never copy audio into the repo, and say in the README that notes may contain names.
- [ ] Define behaviour on flag/undo for notes on a flagged capture, in the same way as for photos.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism; owner asked to orchestrate in parallel while busy)
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): a dedicated NOTE button (tap to start, tap to stop, vibration and tone), not the volume keys or a PHOTO long-press; mono AAC m4a ~32 kbps, auto-stop at 60 s with a cue; attach by PhotoRule's window and own-row fallback; a flagged capture's notes move like its photos (consistency with #27); RECORD_AUDIO, foreground only; older sessions read fine; audio only in the session folder and shared zip, README privacy note. Speech-to-text is NOT in the first PR; whether it is wanted at all (and if a later local desktop transcription is acceptable) is an owner question, asked at the hand-back. Implementation after #15 and #35.

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:58 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-16/plan.md` at `459f04c`

NOTE button records mono AAC m4a (60 s cap) into notes/, attached via PhotoRule's window, new trailing voice column, notes move with photos on flag, zip and importer carry them. No speech-to-text. Implement after #15 and #35 (shared Survey, MainActivity).

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:54 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: ready

**Issue**: #16 — Logger: voice notes attached to captures
**Plan**: `.agent/work-plans/issue-16/plan.md` at `459f04c`
**Branch**: `feature/issue-16`

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | One PR: one new pure rule, one column, a recorder, zip and importer pass-through. Sequenced after #15 and #35. |
| Issue alignment | Good | Every Issue Review action and Checkpoint decision is covered (NOTE button, AAC 32 kbps, 60 s cap, PhotoRule reuse, move on flag, foreground-only mic, privacy note, no STT). Serves: "Project has no goals document or design document: none. Serves the logger's field-capture purpose (winter gloved operation)." |
| File targeting | Good | Right files. `ShareZip` already treats `.m4a` as STORED, so that change is the `notes/` listing only. Missing: `.gitignore`, `docs/howto/qgis.md`, existing importer tests (findings 1, 2, 4). |
| Consequences | Needs work | Importer `SCHEMA_VERSION` bump not planned; a note-only own row has knock-on effects (finding 3). All settleable during implementation. |
| Principle alignment | Good | Reuse over duplication, tests for pure logic, manual device check stated; privacy limit starts strict, but see finding 1. |
| ADR compliance | Good | No workspace ADR triggered (project feature). |
| ROS conventions | N/A | Android app and Python importer. |

Host decisions taken as given (owner may veto): `voice` goes after whatever #15 (`fix_start`, `float_spell_s`, `suspect_reason`, `fix_young`, COLUMNS 29 -> 33) and #35 add, `COLUMNS` recomputed at implementation; #20's icecube reader carries or ignores `voice` (note only; no current `icecube/` code reads `points.csv`); speech-to-text out (owner question pending).

### Findings

1. **[Principle alignment / privacy]** `.gitignore` ignores `*.jpg` and `*.csv` but not `*.m4a`, and the importer copies notes into `notes/<session>/` beside `photos/`. The plan says "gitignored like photos", but photos are ignored by extension, not by folder. Add `*.m4a` (with a comment like the photos one), and generate the placeholder `.m4a` in the test at run time (tmp_path), never commit it. Small; do it in the PR.
2. **[Consequences]** `analysis/qgis/import_sessions.py` keeps a `SCHEMA_VERSION` with a history entry per change, so a re-import picks up new columns (#15 takes it 4 -> 5). Adding `voice` to captures/photos/refs needs the next bump and history line, and `docs/howto/qgis.md` (importer tables, logger versions) needs the `voice` column and `notes/` folder. Settle at implementation.
3. **[Consequences]** Own-row fallback makes a class `photo` row that holds only a note (no photo). Check what that does to `VoidRule.glyph` (camera icon), `liveCount`/`n_photo` counts, `VoidRule.photoRowFor` (note text "photos of voided n="), and the importer's photos table (one row per photo file: a note-only row has none, so it must still appear somewhere, e.g. via captures or its own handling). Decide at implementation (a test for each behaviour chosen); not blocking.
4. **[File targeting]** Importer tests already exist: `analysis/qgis/tests/test_import.py` with the `synth.py` session generator. Extend those rather than a new fixture. Minor.

### Summary

The plan is complete and reuses the photo path throughout; it is ready for implementation once #15 and #35 land. The four findings are small and can be settled in the PR, the `.gitignore` one being the one not to forget in a public repo.

### Recommended Actions

- [ ] Add `*.m4a` to `.gitignore`; test audio generated at run time only (finding 1).
- [ ] Bump importer `SCHEMA_VERSION` after #15's, with its history line; update `docs/howto/qgis.md` (finding 2).
- [ ] Decide and test how a note-only own row shows on the map, in counts and in the importer (finding 3).
- [ ] Extend `analysis/qgis/tests/test_import.py` / `synth.py` for the `voice` column and `notes/` copy (finding 4).

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implementation is held until #15 and #35 merge (shared Survey/MainActivity/PointRow); then rebase on main. Settle during implementation the Plan Review at ab68ef9's items: .gitignore *.m4a (test audio created at run time, none committed); importer SCHEMA_VERSION bump after #15's with its history line, docs/howto/qgis.md voice column and notes/ folder; a note-only own row: decide icon, counts, photoRowFor text and the photos table, each with a test; extend test_import.py and synth.py.

## Implementation
**Status**: complete
**When**: 2026-11-27 22:30 -05:00
**By**: Claude Code Agent (claude-opus)
**Branch**: feature/issue-16 at 3bfb5bb
**Plan**: `.agent/work-plans/issue-16/plan.md` at `459f04c`

### What was done

- `1c1a376` .gitignore: `*.m4a` with a privacy comment (first, before any audio code).
- `5f35e7a` core: `NoteRule` (delegates attachTo/targetText/add to PhotoRule; fileName, MAX_MS 60 s, MIN_MS 0.5 s, 32 kbps / 16 kHz, keep(), recordingText()); `PointRow` trailing `voice` column (COLUMNS 34, new V11_COLUMNS 33); `VoidRule.moveAttachments`/`moveEvent` (photos and notes move together, one event line) and `photoRowFor` copies `voice` and names what it holds; `soundings.py` MOVED_PHOTO_NOTE accepts the three notes.
- `348759c` app 0.14.0 (build 18): `NoteRecorder` (MediaRecorder MPEG_4/AAC mono, setMaxDuration + info listener), `Survey` planNote/noteTaken/noteCancelled/noteTarget and moveAttachments in the flag path, NOTE button with target and `STOP 0:12/1:00` line, RECORD_AUDIO asked on first tap, onPause stops and keeps the note, three new Feedback cues, manifest permission and optional microphone feature, Sharer comments.
- `fbea08c` importer schema 7 (SCHEMA_APP 7: 0.14): shared `_attachments` helper for photos and notes in `logger_session.py` (column decides n, event only for orphans), `file_names`/`extract_file` generalised with photo/note wrappers, captures `voice` + `voice_count`, photos and refs `voice`, sessions `n_notes`, notes copied to `notes/<session>/` (skipped by `--no-photos`), `build_project.py` map-tip link per note.
- `3bfb5bb` docs: logger README (NOTE, Voice notes section with privacy note, column, folder, flag, zip, cues, events), `docs/howto/qgis.md` (tables, schema 7, map tips).

### Re-check of merged code (adaptations to the plan)

- PointRow after #15: 33 columns; `voice` is column 34 (plan said "after void_reason", COLUMNS 30). The last parse block now reads fix_young with `i++` under a new `V11_COLUMNS` and voice after it.
- Importer schema was 6 (#23 PR A), so this is 7; `icecube/io.py` calls `import_session(..., extract_photos=False)` and is unaffected (notes follow the same flag). No icecube code reads `voice`.
- Auto capture (#35) needs nothing: notes attach by `lastCapture` like photos; an auto capture is a normal surface row. The position latch is not touched by notes.
- Sharer.filesIn already zips every folder one level down and ShareZip already STOREs `.m4a`: only comments and a test changed (plan step 8 was a no-op).
- Plan said keep the pending note across recreation like `photoPath`: not done. Rotation does not recreate the screen (configChanges) and onPause always ends a recording first, so nothing is pending at a recreation.
- The own-row position is averaged at the press (kept in NotePlan), not at the stop, since a 60 s note outlasts the 10 s epoch buffer.
- Added beyond the plan: a 0.5 s minimum (a double tap; MediaRecorder.stop throws on a very short take), and the soundings.py note regex (found while settling finding 3).

### Plan Review findings settled

1. `*.m4a` gitignored; test audio is placeholder bytes made at run time (synth.M4A), none committed.
2. Schema 7 with history line and SCHEMA_APP entry; qgis.md updated.
3. Note-only own row: stays class `photo`; camera glyph (VoidRule.glyph PHOTO), not in liveCount/lastLive, counted in `n_photo` (own rows) and in `n_notes`, not in the photos table (one row per photo file) but in captures with `voice`; flag-made row note is `notes of voided n=` / `photos and notes of voided n=` (`photos of voided n=` unchanged for photos only). Tests: VoidRuleTest.noteOnlyOwnRow, notesMoveWithPhotos, test_voice_notes_import, test_soundings.
4. Extended `synth.py` (`with_notes`) and `test_import.py` (3 tests).

### Tests

- `python3 -m unittest discover -s analysis/qgis/tests`: 178 OK (5 skipped, QGIS).
- `python3 -m unittest discover icecube/tests`: 130 OK.
- `./gradlew testDebugUnitTest assembleDebug --max-workers 4`: pass (C: had 109 GB free). New NoteRuleTest; PointRowTest, VoidRuleTest, ShareZipTest extended.
- APK `logger/app/build/outputs/apk/debug/app-debug.apk`: versionName 0.14.0, versionCode 18, RECORD_AUDIO declared. Not installed.

### Needs a phone check

Record and stop; auto-stop at 60 s with its cue; the permission prompt on first tap and the denied path; NOTE onto a capture and an own row; flag a capture with a note (moved, events line); PHOTO while recording (stops and keeps the note); the five-button row in portrait (text fit of `STOP 0:12/1:00` and `→ S12 (14 s)`); a share zip with `notes/`, then import and play a note from the QGIS map tip (array_foreach expression not run in tests).

### Deferred

- Speech-to-text (owner question pending).
- No map mark for a capture that has notes (only own rows show the camera).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-27 22:36 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: changes-requested

**Branch**: feature/issue-16 at `979b59c`
**Base**: main
**Depth**: Standard (reason: 26 files, +1157 -168, app and importer together)
**Must-fix**: 1 | **Suggestions**: 5
**Round**: 1 | **Ship**: continue — round 1: 1 must-fix; first round always re-reviews after fixes

Verified: importer unittest 178 OK (5 skipped); Gradle testDebugUnitTest 37 suites, 0 failures (C: 109 GB free); no audio tracked, `*.m4a` ignored. No Python or Java linter installed (static analysis not run). Outside reviewers: gemini and codex completed; a fresh Claude adversarial agent also ran.

### Findings
- [x] (must-fix) Finish a note that is still recording before the session stops (REC confirm, `Control.stopAll`) and before a share/snapshot zip. As it stands the note gets no row and no events.csv line, and a snapshot zip holds a partial .m4a. The importer then attaches the orphan by the n in its file name, and for a note planned as its own row that is `counter+1`, which a MARK during the recording may have taken, so it lands on the wrong capture. Fix: call `finishNote(false)` in onRec's stop action and before `Sharer.share`. (codex, gemini, Claude adversarial) — `logger/app/src/main/java/rink/icecube/logger/MainActivity.java:944`
- [x] (suggestion) The name-collision loop builds each candidate from the previous one (`_2_3.m4a`), and `NOTE_RE` allows only one `_\d+`. Build every candidate from the base name. It is reachable when the GPS tod stalls. — `logger/app/src/main/java/rink/icecube/logger/Survey.java:1110`
- [x] (suggestion) Race at the 60 s cap: a STOP tap or onPause after the recorder has stopped itself, but before MAX_DURATION_REACHED is delivered, makes `stop()` throw with `capped` still false. The full note is then deleted. Treat `elapsedMs() >= MAX_MS` as capped. — `logger/app/src/main/java/rink/icecube/logger/NoteRecorder.java:88`
- [ ] (suggestion) There is no `OnErrorListener`. If recording fails midway (mic taken, media server died), STOP keeps counting until it is tapped. Finish the note (as failed) on error. — `logger/app/src/main/java/rink/icecube/logger/NoteRecorder.java:52`
- [ ] (suggestion) VOICE_TIP puts `@project_folder` and the path into a `file:///` href without encoding. A Drive path with a space ("My Drive") should be checked by hand: does the link open in the Windows QGIS map tip? — `analysis/qgis/build_project.py:154`
- [ ] (suggestion) The docstring says the `voice` column lists `notes/<file>.m4a`, but points.csv holds bare file names. — `analysis/qgis/logger_session.py:7`
