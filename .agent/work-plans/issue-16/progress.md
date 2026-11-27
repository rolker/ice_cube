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
