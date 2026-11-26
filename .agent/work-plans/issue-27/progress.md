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
