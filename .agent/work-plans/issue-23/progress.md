---
issue: 23
---

# Issue #23 — Logger: pole check against a robust reference, with placement and steadiness cues

## Issue Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)

**Issue**: #23

### Scope Assessment

**Well-scoped?** Not yet as one PR. The issue now carries four things: the robust reference, the placement and steadiness cues, the "validated by a known point" mark moved here from #15, and (per the owner's comment) board-top session check points tied to stable refs. Suggest two PRs: (A) robust reference plus placement and steadiness cues, plus the shared reference rule; (B) the validated mark and board-top checks, built on A.
**Right repo?** Yes. All of it is in `logger/` (pole check, `refs.csv`) and, for the validated mark, possibly the `icecube/` reader.
**Serves**: none written down for the project (no goals document found in the checkout); the practical goal is trustworthy survey checks before the winter ice survey. Design section: none.
**Design picture?** No change to a design document (the project has none). The shared reference rule is a design decision and should be written once, in `logger/README.md` "Refs and the pole check", so #15 and #23 do not describe it twice.
**Dependencies**: #15 (in implementation on `feature/issue-15`): `PoleCheck.matches` will skip young refs (`fix_young`), `refs.csv` and `points.csv` gain columns, settle moves to 120 s. #23 implementation must start after #15 merges and extend those rows, not a parallel schema change. #27 (merged): voided refs are already skipped by `PoleCheck.matches`; the robust reference must keep skipping them.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Small steps; step back when they stop converging | Action needed | Split into PR A and PR B as above; the issue's "To decide in the plan" list has eight items and the board-top idea adds a ninth axis. |
| A change includes its consequences | Watch | README "Refs and the pole check" text (compares to "most recent" and "mean of all") and `PoleCheckTest` become untrue; update in the same PR. Older `refs.csv` rows must still read (issue already says so). |
| Keep one current design | Watch | One reference rule shared by the fix-stretch "validated" mark and the ref check; define it once. |
| Only what's needed | Watch | Board-top checks need a named problem: frost heave means a board top is not a fixed point, so it needs its own history, not the ref rule. Keep it out of PR A. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no ADRs or principles file; nothing triggered. |

### Consequences

- `logger/README.md` pole check section, settings text, and `PoleCheckTest` change with the reference rule.
- `refs.csv`/`points.csv` columns added by #15 are the base; any new column (for example a validated flag) must follow the same header-upgrade path and stay readable by `analysis/` and `icecube/` readers.
- Tuning needs the private 2026-11-11, -15 and -21 sessions; only relative numbers may go in the public repo.

### Recommendations

- Start implementation only after #15 merges; plan against its final `PoleCheck` and column set.
- Split into PR A (median or passing-visit reference, difference to last visit kept as information, placement cue about 2 cm, steadiness cue about 1.5 cm, tolerance applied to the robust reference) and PR B (validated mark for fix stretches, board-top check points).
- Decide in the plan how a robust reference behaves with 1 and 2 earlier visits (median of two is the mean; a bad first visit has no defence), and skip voided and young refs when forming it.
- Treat board tops as their own mark class compared against their own robust history, with a drift cue against the stable refs, never as references for the ref check.
- Write the reference rule once in the README and have #15's follow-up text point to it.

### Actions
- [ ] Split into PR A and PR B as described under Scope Assessment; the issue's decision list has eight items plus board tops.
- [ ] Start implementation only after #15 merges; plan against its final `PoleCheck` and column set.
- [ ] Split into PR A (median or passing-visit reference, difference to last visit kept as information, placement cue about 2 cm, steadiness cue about 1.5 cm, tolerance applied to the robust reference) and PR B (validated mark for fix stretches, board-top check points).
- [ ] Decide in the plan how a robust reference behaves with 1 and 2 earlier visits (median of two is the mean; a bad first visit has no defence), and skip voided and young refs when forming it.
- [ ] Treat board tops as their own mark class compared against their own robust history, with a drift cue against the stable refs, never as references for the ref check.
- [ ] Write the reference rule once in the README and have #15's follow-up text point to it.

## Checkpoint
**Status**: complete
**When**: 2026-11-27 17:52 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (mechanism; owner asked to orchestrate in parallel while he is busy: "Orchestrate the issues while I'm busy and run them in parallel if practical")
**After**: issue-actions
**Decision**: proceed

Host decisions (owner may veto): two PRs on this issue: PR A robust reference + placement and steadiness cues (extends #15's PoleCheck and columns; implementation after #15 merges); PR B the "validated by a known point" mark and board-top check points. Board tops: an existing ref mark gets a kind, chosen when the mark is first shot in Ref mode ("fixed ref" default, or "check point (may move)"), stored with the mark; check points get their own robust history and a drift cue against the fixed refs and are never references for fixed refs. Few earlier visits: plan states the rule (1 visit: compare with it, flagged as a single-visit reference; 2: mean, with a disagreement cue if they differ by more than the tolerance; 3+: median). Shared reference rule written once in logger/README.md.

## Plan Authored
**Status**: complete
**When**: 2026-11-27 17:54 -05:00
**By**: Claude Code Agent (claude-sonnet-5-5)
**Plan**: `.agent/work-plans/issue-23/plan.md` at `5928531`

Two PRs. PR A (detailed): reference = median of earlier non-voided, non-young visits (1 visit single, 2 mean with disagreement flag, 3+ median), tolerance applied to it, last-visit difference kept, placement (2 cm) and steadiness (1.5 cm) cues as settings, one reference rule in the README, no new row columns. PR B (outline): validated mark and check-point kind chosen in Ref mode. Implementation waits for #15 to merge.

## Plan Review
**Status**: complete
**When**: 2026-11-27 17:55 -05:00
**By**: Claude Code Agent (claude-opus-5-5)
**Verdict**: ready

**Issue**: #23 — Logger: pole check against a robust reference, with placement and steadiness cues
**Plan**: `.agent/work-plans/issue-23/plan.md` at `5928531`
**Branch**: `feature/issue-23`

Reviewed against main (`b208c31`: `PoleCheck.java`, `Survey.poleCheck`, `Limit`, `TiltSet`, README "Refs and the pole check") and #15's plan (read-only). Host decisions on the planner's open questions (owner may veto): 1 amber when two earlier visits disagree; 2 the 2 cm / 1.5 cm limits are settings, 0 = off; 3 a mark's kind is fixed at its first shot; 4 an earlier visit that failed its own check stays in the reference; 5 re-check #15's names after it merges.

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Split into PR A (detailed) and PR B (outline) as the Issue Review asked; PR A is about 7 files plus tests, no schema change. |
| Issue alignment | Good | Covers the reference height (median; mean-of-passing rejected with a reason), last-visit difference kept, placement and steadiness cues, tolerance on the robust reference, tuning on the three sessions (relative numbers only), old refs.csv rows readable. The record and validated-mark items go to PR B. Serves: "Survey-readiness for cm-level ice accumulation: trustworthy pole checks before the winter ice survey. Project has no goals or design document: none." |
| File targeting | Good | Matches the code: `PoleCheck.check` returns null on a first visit and `Survey.poleCheck` builds the notice, so the separate `Cues` path in step 5 is needed for first-visit steadiness. Pole tolerance lives in `SurveyLimits`, the new limits as `Limit` instances is consistent with the other typed settings. |
| Consequences | Good | README section, `PoleCheckTest`, Settings list and notices covered; `matches` stays the single reference-set filter (voided from #27, young from #15). |
| Principle alignment | Good | Public-repo rule respected (synthetic tests, offline replay, relative numbers); one rule written once in the README; small steps. |
| ADR compliance | Good | Project has no ADRs; none triggered. |
| ROS conventions | N/A | No ROS code. |

### Findings

None block implementation; all can be settled while implementing.

1. **[Tests]** The "bad middle visit" test says the +7 cm visit "fails against a 3-visit reference". With visits 0, 0 before it, the +7 visit is checked against the PAIR (mean of two agreeing visits), not a median. Reword the case (+7 fails red against an agreeing pair) and add a separate median case with three earlier visits.
2. **[Issue alignment] Tilt refs.** A ref can come from a tilt capture (`finishTilt` runs `poleCheck` for a REF tilt row), and that row's `sdU` is the RMS of the per-lean scatters (`TiltSet` ~line 279), not the scatter over a held occupation. Say in the plan and README whether the steadiness cue applies to occupation refs only (`method == OCCUPATION`) or also to tilt refs; tilt already has its own lean-scatter gate.
3. **[Consequences] Notice and alarm details.** Settle and write down in the README: for a doubtful-pair failure, which cue plays (not `POLE_ALARM`) and whether `poleWarning` (the persistent warnings bar) is set; how a passing single-visit check shows ("amber-leaning info" is ambiguous: green with the "single-visit reference" text, or amber); and the order when a cue or doubtful reference meets #15's young or suspect amber on the same notice (one amber notice listing each reason).
4. **[Issue alignment] Even counts.** For 4 or more visits with an even count the median is the mean of the middle two; state it in the README rule so the docs and code agree.
5. **[Tuning] Expected outcome.** Step 8 says "72 and 127 warn". Under the new rule 127 should fail red and 72 is expected amber by the placement cue (its difference to the reference is likely within 3 cm). Write the expected colour per ref so the replay is a real check.
6. **[Plan hygiene]** The plan's Open Questions are now host decisions (above). Record them as a Decisions section in the plan when implementation starts, as #15's plan does, and re-check `fixYoung`/`fix_young` and the column count against #15's merged code.

### Summary

The plan is ready for PR A once #15 merges. The reference rule, cues and settings fit the current code; the findings are small details to settle and document during implementation.

### Recommended Actions

- [ ] Reword the bad-middle-visit test (PAIR, not median) and add a 3-visit median case.
- [ ] Decide whether the steadiness cue applies to tilt refs; document it.
- [ ] Specify cue, warnings bar and colour precedence for the doubtful-pair, single-visit and young/suspect combinations in the README.
- [ ] State the even-count median in the README rule.
- [ ] Give the expected colour per ref (72, 127, 128, 329) for the step 8 replay.
- [ ] Move the Open Questions to a Decisions section and re-check #15's merged names before step 1.
