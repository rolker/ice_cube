---
issue: 24
---

# Issue #24 — Restore CUBE's robustness in icecube and the logger overlay

## Plan Authored
**Status**: complete
**When**: 2026-11-26 15:04 -05:00
**By**: Claude Code (claude-opus-5-5)
**Plan**: `.agent/work-plans/issue-24/plan.md` at `31c140e`

Median-first per-node rebuild with side hypotheses and CUBE's strength ratio in nodes.py and Coverage.java (red only while outliers are not outvoted 2:1), gated local-plane relief, Eeg in batch, void hook for #27; parity vectors plus private 11-21/11-26 replays as acceptance.

## Plan Revised
**Status**: complete
**When**: 2026-11-26
**By**: Claude Code (claude-opus-5-5)
**Plan**: `.agent/work-plans/issue-24/plan.md`

Plan review on PR #28 (ready with changes) addressed: must-fix 2-7 taken as proposed (pinned weighted median and id ties with explicit capture ids; full hypothesis bookkeeping, promotion by count in the logger and by weight in batch, `max_nr` and strength defined; fold at cap 32 defined, global budget dropped; batch rebuilds each node once; Eeg weighted with a floored denominator, culls counted in `n_out`/`n_eeg`; generator guard and the missing vector cases, Java permutation test). Should-fixes: `test_topo.py`, `[[grids]]` keys, `flags_*.asc` name kept, events via `LoggerService.event`, docs on 1-3 soundings, flicker and outlier bins. Scope split (orchestrator): the steep-bank plane step (must-fix 1) goes to a follow-up issue; the void hook stays with #27.

## Implemented
**Status**: complete
**When**: 2026-11-26
**By**: Claude Code (claude-opus-5-5)

Commits: Python rebuild + NodeStore + Eeg + `robust` switch and callers; vectors from a Python twin of Coverage (rule cases, three over-cap cases, status sequences, near-threshold guard); Java port (store, fold, status, fringe mask, events formatter); fringe colour and legend; events.csv line; docs and 0.8.0. Plan deviation: Eeg's guard and minimum size (plan D.10 note). Follow-up for the plane step: #29. Tests: `python3 -m unittest discover icecube/tests` 67 OK; `./gradlew testDebugUnitTest` 152 OK, `assembleDebug lintDebug` 0 errors. Private replays (counts only): 11-26 red 6 (5 bank relief, 1 bad capture at a re-fix), none in the rink footprint, no new red; 11-21 red 18 (6 with the 90 s rule, all relief), none in the footprint, no new red, 20 of 25 cause-b nodes moved toward the good captures (2 away).
