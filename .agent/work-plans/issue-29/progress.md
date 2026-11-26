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

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:26 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (actions are plan inputs within the issue's scope; orchestrator table allows proceed)
**After**: issue-actions
**Decision**: proceed

The three actions (explicit edge-outlier bound <= 76/400; plan the edge cases: fewer than 8 neighbours, red or empty neighbours; consider batch path first, logger ring after) go into the plan-task handoff.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:28 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-29/plan.md` at `18489fd`

Two-stage rebuild: pass 1 as today; red nodes get a weighted plane through their 8 neighbours' main z (gated on >=5 usable neighbours, >=3 non-red, planarity, gradient above the allowance), entries reduced to the node and rebuilt, kept only if not worse. Flat-edge test asserts absorbed <= no-plane baseline (76/400) in the same seeded run. Batch lands first (PR A, with additive vectors), logger ring re-reduction second (PR B).

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:29 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: needs-work

**Issue**: #29 — Steep-bank relief for the node rebuild: a gradient from the neighbourhood, not the node's own split
**Plan**: `.agent/work-plans/issue-29/plan.md` at `18489fd`
**Branch**: `feature/issue-29`

Host decisions taken as given: 0.25 red weight and planarity bound tunable on the private replays (counts only); `slope_from_dem` as a gate first, ridge prior only if needed; batch-first split (PR A batch + additive `plane_vectors`, PR B logger ring).

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | PR A is about 8 files in `icecube/` plus docs; the logger port is split out (host decision). |
| Issue alignment | Needs work | All three issue-review actions addressed (explicit bound, edge cases, split). The flat-edge test as written does not exercise the shared-sounding path (finding 2). Serves: "Roadmap line "`icecube` runs on logger sessions in the project's local frame" and the estimator tracking the rink through the season; the project keeps no goals or design document, so none." Checked: matches the issue review. |
| File targeting | Needs work | `cli.py:126` and `topo.py:366` write the surface `.npz` (the inspector's input); the new `planar`/gradient arrays need to go there (finding 5). `slope_array` returns the clamped allowance, not the raw DEM slope the gate needs (finding 4). |
| Consequences | Needs work | `.npz` outputs missing; the rest (vectors both sides, docs, example toml, logger README in PR B) covered. |
| Principle alignment | Needs work | "Scripts take values from config": some thresholds have no setting name (finding 3). Public repo: fine (synthetic tests, counts only). Design picture: none, stated. Prior art: stated. |
| ADR compliance | Good | Project has no ADRs. |
| ROS conventions | N/A | Not a ROS project. |

### Circularity check (from the #24 review)

At the batch grid (`cell = 0.5`, `capture_radius = 0.75`) a neighbour's disc shares about 58 % (edge) and 42 % (corner) of the node's disc area, so the neighbours' mains are built partly from the node's own soundings. That is not the #24 circularity: each neighbour runs its own median-first split around its own centre, and a symmetric tail cut leaves the main's mean near the centre value, so the gradient is not attenuated the way a plane through the node's own main members was. The node's own split result is never used. Residual effects, neither blocking: (a) neighbour residuals are correlated through shared soundings, so the planarity statistic is optimistic; (b) a red neighbour's main can sit off-centre when promotion picks a one-sided cluster, which the 0.25 weight covers. Verdict: avoided, but only the grid-level test in finding 2 shows it on the hazard case.

### Findings

1. **[Issue alignment, must-fix] Pass 2's acceptance rule can hide a worse height.** "Keep only if `n_out' <= n_out`" compares counts, not heights. Pass 2 changes both z (reduced) and v (slope term dropped, so the test is stricter), and batch promotion is by weight, so the main can switch to a different subset with the same or a smaller `n_out` and a z centimetres away from pass 1. It also accepts `n_out' == n_out` with the node still red. Fix: accept pass 2 only when (i) the node leaves red, or `n_out' < n_out`, and (ii) the pass-2 z agrees with the neighbour plane's intercept `a` at the node (an independent prediction) within `k_sigma * sqrt(var_a + sigma'^2)`. Otherwise keep pass 1. Add a pinned case: a bank node plus a tight off-plane cluster, where pass 2 would promote the wrong cluster with no rise in `n_out`; it must keep pass 1 (or the plane-consistent main).
2. **[Issue alignment, must-fix] The flat-edge outlier test must run at grid level.** With 0.5 m cells, a +15 cm capture at the node's disc edge lies within about 0.25 m of a neighbour and feeds up to 3-4 neighbour nodes. The hazard is that capture reaching the neighbour mains and through them the gradient. Build the Monte Carlo through `grid_surface` on a synthetic block (at least 5x5 nodes, so the centre has a full ring and the outlier feeds neighbours), not one node with hand-set neighbours. Assert `planar` false at every node in every trial, and `absorbed_plane == absorbed_baseline` (equality follows when the path stays off). The 76/400 recorded value came from a single-node check and may not reproduce at grid level: record whatever the grid-level baseline gives on first run. The bank-clears case should also build its neighbours from soundings, not set their mains directly.
3. **[Principle alignment, should-fix] Every threshold needs a setting.** The plan names `plane`, `plane_min_neighbours`, `plane_red_weight` and `plane_dem_cap`, but hardcodes the DEM gate's floor, the minimum non-red count (3), the planarity bound (reused `k_sigma`, although the host made it separately tunable) and the usable-neighbour `n_in >= 2`. Name them (for example `plane_dem_floor`, `plane_min_clear`, `plane_planarity`) in `icecube.example.toml`. Evidence that `plane_min_clear` matters: the issue's table has 242 of 400 nodes red at 0.30 m/m. Treating neighbours as independent at that rate, P(at most 2 of 8 non-red) is about 0.31, so about a third of 0.30 m/m bank nodes fail the gate by design. It needs to be tunable on the replays along with the red weight, and the pinned 0.3 m/m case needs a seed that is stable against it.
4. **[File targeting, should-fix] The DEM gate needs the raw slope.** `topo.slope_array` returns `min(cap, max(floor, factor * s))`, the allowance, not the DEM slope. Pass the raw `slope_field` sample (or both arrays) to `nodes.grid_surface`, and state that with a global `slope` (no `slope_from_dem`) the DEM gate is skipped. Note also that where `slope_from_dem` already raises the allowance on a bank, the significance gate compares against that raised allowance.
5. **[Consequences, should-fix] Surface outputs reach the `.npz` files.** Add `planar` and the gradient magnitude to the `np.savez` calls in `icecube/cli.py:126` and `icecube/topo.py:366`, and to the `Surface` dataclass, or the inspector the plan names cannot read them.
6. **[File targeting, minor] Aggregate moments are logger-only.** Batch `grid_surface` has no fold; only `NodeStore` (Coverage.java's mirror) does. Say whether PR A's `plane_vectors` exercise a folded `NodeStore` (then the pseudo-entry definition lands in PR A) or leave the moments to PR B.
7. **[Approach, minor] Say whether pass 2 runs Eeg.** Batch `_robust` applies Eeg's cull after the rebuild. State that pass 2 runs `_robust` in full, so `n_out'` includes Eeg culls and compares like with like.

### Summary

The neighbourhood gradient does avoid the #24 circularity, and the gates and split are sound. Two must-fixes before implementation. Pass 2's count-only acceptance can hide a worse or switched height: add the plane-intercept consistency check and require an actual improvement. The flat-edge outlier test has to run through `grid_surface`, so the outlier also feeds the neighbours whose mains form the gradient.

### Recommended Actions

- [ ] Pass-2 acceptance: require leaving red (or `n_out' < n_out`) and agreement of z' with the plane intercept within k sigma; add a pinned wrong-cluster case.
- [ ] Run the flat-edge Monte Carlo and the bank case through `grid_surface` on a synthetic block of at least 5x5; assert `planar` false everywhere and absorbed equal to the baseline; record the grid-level baseline.
- [ ] Name config keys for the DEM floor, minimum non-red count, planarity bound and usable `n_in` in `icecube.example.toml`.
- [ ] Pass the raw DEM slope to the gate; define the behaviour with no `slope_from_dem`.
- [ ] Add `planar` and the gradient to `Surface` and the `.npz` writers in `cli.py` and `topo.py`.
- [ ] Clarify the aggregate-moment scope in PR A and Eeg in pass 2.
