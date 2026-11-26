---
issue: 29
---

# Issue #29 — Steep-bank relief for the node rebuild: a gradient from the neighbourhood, not the node's own split

## Issue Review
**Status**: complete
**When**: 2026-11-26 12:00 -05:00
**By**: Claude Code Agent (claude-sonnet)

**Issue**: #29

### Scope Assessment

**Well-scoped?** Mostly. The problem, the rejected first design and the acceptance cases are clear. It spans two implementations (Python batch and the logger's per-capture path, with shared parity vectors), so it may be a large PR; the batch path (neighbour-block plane, `slope_from_dem` prior) could land first and the logger ring re-reduction follow.
**Right repo?** Yes. Estimator logic in `icecube/` and the logger's parity vectors, both in this repo.
**Serves**: roadmap line "`icecube` runs on logger sessions in the project's local frame" and the goal that the estimator tracks the rink through the season; the project keeps no goals or design document, so no design section applies.
**Design picture?** No change to the project's parts; the logger's re-reduction cost grows from 9 to 25 nodes per capture, worth noting but not a design change.
**Dependencies**: #24 (PR #28), merged per recent history.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Public repo (no coordinates or private data) | Watch | Replay acceptance is on private data; report counts only, and keep pinned cases synthetic and in local coordinates. |
| Scripts take values from config | Watch | New settings (plane weights, neighbour-block use, `slope_from_dem` prior weight) belong in `icecube.example.toml`, not hardcoded. |
| Rink clearance is not a rule | OK | Not touched. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| none | No | The project has no ADRs or principles document. |

### Consequences

- Update `docs/howto/ice-cube.md` and `logger/README.md` (they describe the median-first rebuild) and `icecube.example.toml` for any new setting.
- Parity vectors must change on both the Python and logger sides together.
- Entry offsets `(dx, dy)` and the fold aggregate change the stored entry or fold format; check session and cache compatibility.

### Recommendations

- State the edge-outlier test as an explicit bound, for example the 262 of 400 absorbed with a plane fitted to all entries must fall to the 76 of 400 baseline or better, so "outlier stays in `n_out`" is checkable.
- Decide the board edge cases in the plan: nodes with fewer than 8 neighbours at the survey edge, and neighbour blocks where a neighbour is itself red or empty (minimum weight or neighbour count before the plane path turns on).
- Consider splitting into batch first, then logger ring re-reduction, if the combined change is too large for one PR.

### Actions
- [ ] State the edge-outlier test as an explicit bound, for example the 262 of 400 absorbed with a plane fitted to all entries must fall to the 76 of 400 baseline or better, so "outlier stays in `n_out`" is checkable.
- [ ] Decide the board edge cases in the plan: nodes with fewer than 8 neighbours at the survey edge, and neighbour blocks where a neighbour is itself red or empty (minimum weight or neighbour count before the plane path turns on).
- [ ] Consider splitting into batch first, then logger ring re-reduction, if the combined change is too large for one PR.
