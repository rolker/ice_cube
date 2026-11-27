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
