---
issue: 15
---

# Issue #15 — Logger: retune the false-fix guard (start-of-session false alarms, a missed false fix)

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:45 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #15

### Scope Assessment

**Well-scoped?** Mostly, but large and still carrying open decisions. The body (2026-11-15 evidence) has since been widened by three comments (11-21 float-start false fix and settle; 11-26 ~120 s re-fix settle). Together that is: (a) start-jump reference (float-only, unknown when no float); (b) a float-spell rule (fix starting after a long float is suspect whatever the jump); (c) a young-fix window, now 90 s or 120 s; (d) a separate height-component test; (e) a "validated" mark from occupying a known point; (f) extra cues (sats, ratio/GST, correction age; still-pole height drift); (g) retuning on private sessions. One PR is feasible only if (a), (b), (c) and the column additions come first; (d)-(g) should be split out or explicitly deferred in the plan.
**Right repo?** Yes: `logger/` (FixGuard, Survey, PointRow, Settings, README); the overlay consumer is in `core/Coverage.java`.
**Serves**: the owner's survey-readiness for winter ice accumulation (ice growth is a few cm between sessions, so false fixes and session offsets dominate the error budget). Project design/goals document: none.
**Design picture?** No change to the logger's structure. It does change the documented "False-fix guard" behaviour and the CSV schema (`logger/README.md`, False-fix guard and points.csv sections).
**Dependencies**: #23 (pole check; a validated mark in (e) shares its "robust reference" logic, so do not build two reference rules); #24 and #29 (closed; they consume `fix_age_s`/`fix_suspect`, and the overlay replays show 37 of 58 red nodes from young fixes, so the settle choice feeds the coverage overlay); #27 and #9 (merged; no conflict, but the new columns must not break the `void`/`flag_reason` export or the tilt rows). Not blocked by any of them.

### Principle Alignment

No project principles document exists, so the checks are the project's CLAUDE.md rules and the issue's own constraints.

| Principle | Status | Notes |
|---|---|---|
| Public repo: no coordinates, private data stays private | Watch | Retuning uses private sessions. Tests and the README may carry only relative numbers (jump in m, age in s, height offsets in cm) and synthetic epoch sequences. Fixtures built from real sessions would leak positions. |
| Keep older sessions readable | Action needed | Keep `fix_age_s`, `fix_jump_m`, `fix_suspect` as they are and append new columns after them (the issue says so). Check that `PointRow`, `ShareZip` and the Python readers (`analysis/`, `icecube/`) tolerate extra columns, and that a row with a blank/unknown jump reads as "unknown", not as 0 or suspect. |
| Settle time must match the evidence | Action needed | The 10 s default catches little. Evidence: 7.9 cm rms under 90 s, ~3 cm after 150 s (11-21), and one re-fix still +6..+9 cm at 95-113 s (11-26). The plan must decide, with the owner, between a hard refusal and a warn-and-record rule, and the numbers (90 vs 120 s). The existing settle refusal blocks capture outright, which in the field means standing and waiting 2 minutes after every drop. |
| Unknown is not suspect, and not clean | Action needed | With no recent float epoch the jump is unknown. Comment 2 shows a start-of-session stretch flagged at 2.33 m was fine, and the 11-15 miss had a 0.75 m jump. Define the float-spell rule so it cannot be satisfied by absence of data: how long a float counts as "a while", and what happens to a stretch that starts after single/DGPS only. Add the "not confirmed by a ref" clause carefully: a stretch cannot be validated by a ref taken inside it. |
| Height handled separately | Watch | False fixes are worst in height. A separate threshold needs a reference height (a previous float average has a poor height itself), so it may belong to the validated-mark logic rather than the start-jump rule. |
| Validated mark vs pole check (#23) | Watch | Do not give two different notions of "agrees with an earlier visit". Reuse one robust reference (median of visits, skipping failed visits) for the validated mark and the pole check, or order the work so #23 lands the reference first. |
| Test with synthetic sequences | Watch | FixGuard has unit-test seams (`FixGuardTest`-style, `PointRowTest`). The three field cases (start-of-session jump from single, float-spell start at 0.48 m, re-fix settle) should each be a synthetic epoch-sequence test. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no `docs/decisions/`. |

### Consequences

- Update `logger/README.md` (False-fix guard, Settings defaults, points.csv columns, notice text) and the logger version/changelog.
- New columns appended in `PointRow`, the share zip and anything in `analysis/` or `icecube/` that reads `points.csv` by position.
- New settings go in the Settings screen and the README Settings list; `config.example.toml` only if an analysis script gains a setting.
- If captures are fed to the overlay with age-based sigma, #20's sigma rule and `core/SoundingSigma` are affected: say whether this issue changes the overlay's feed or only records age and flags.

### Recommendations

- Split by value. First PR: float-only start jump with "unknown" when no float epoch, a float-spell suspect rule, a young-fix warning and record (age already logged), appended columns, synthetic tests, README. Leave the height-component test, the validated mark, the extra cues (satellites, ratio/GST, correction age) and the retune for follow-up issues unless the owner wants them now.
- Ask the owner before planning: should a young fix (under 90 or 120 s) refuse the capture as today, or capture with an amber warning and a recorded age? This sets the field routine for every session.
- Treat the false-fix risk as asymmetric for ice work: prefer flagging a good stretch (cost: a re-shoot) to missing a bad one (cost: a session offset of decimetres). The float-spell rule should err toward suspect.
- Make the retune evidence-based: replay the 11-11, 11-15, 11-21 and 11-26 sessions offline through the new FixGuard, report only relative counts (flagged, missed, false alarms per session), and keep the data out of the repo.
- Ship the guard changes before the first ice-surveying session this winter, and say in the plan which analysis weights (`fix_age_s`) the processing side applies.

### Actions
- [ ] Keep `fix_age_s`, `fix_jump_m`, `fix_suspect` as they are and append new columns after them (the issue says so). Check that `PointRow`, `ShareZip` and the Python readers (`analysis/`, `icecube/`) tolerate extra columns, and that a row with a blank/unknown jump reads as "unknown", not as 0 or suspect.
- [ ] The 10 s default catches little. Evidence: 7.9 cm rms under 90 s, ~3 cm after 150 s (11-21), and one re-fix still +6..+9 cm at 95-113 s (11-26). The plan must decide, with the owner, between a hard refusal and a warn-and-record rule, and the numbers (90 vs 120 s).
- [ ] With no recent float epoch the jump is unknown. Define the float-spell rule so it cannot be satisfied by absence of data: how long a float counts as "a while", and what happens to a stretch that starts after single/DGPS only. A stretch cannot be validated by a ref taken inside it.
- [ ] Split by value. First PR: float-only start jump with "unknown" when no float epoch, a float-spell suspect rule, a young-fix warning and record (age already logged), appended columns, synthetic tests, README. Leave the height-component test, the validated mark, the extra cues (satellites, ratio/GST, correction age) and the retune for follow-up issues unless the owner wants them now.
- [ ] Ask the owner before planning: should a young fix (under 90 or 120 s) refuse the capture as today, or capture with an amber warning and a recorded age? This sets the field routine for every session.
- [ ] Treat the false-fix risk as asymmetric for ice work: prefer flagging a good stretch (cost: a re-shoot) to missing a bad one (cost: a session offset of decimetres). The float-spell rule should err toward suspect.
- [ ] Make the retune evidence-based: replay the 11-11, 11-15, 11-21 and 11-26 sessions offline through the new FixGuard, report only relative counts (flagged, missed, false alarms per session), and keep the data out of the repo.
- [ ] Ship the guard changes before the first ice-surveying session this winter, and say in the plan which analysis weights (`fix_age_s`) the processing side applies.
