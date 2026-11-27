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

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:30 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review needs-work; orchestrator table allows revise)
**After**: plan
**Decision**: revise

Address must-fix 1-2 and should-fix 3-5, and state minors 6-7: pass-2 acceptance only when the node leaves red (or n_out strictly drops) AND the new z agrees with the neighbour plane at the node within k_sigma*sqrt(var_a+sigma'^2), with a pinned wrong-cluster case; flat-edge Monte Carlo run through grid_surface on a >=5x5 synthetic block (planar false everywhere, absorbed = baseline, baseline recorded on first run; bank case neighbours built from soundings); config keys for every threshold (DEM-gate floor, min non-red neighbours, planarity bound, usable n_in), tuned on the replays (counts only); raw DEM slope passed through, behaviour without slope_from_dem stated; planar/gradient in Surface and the npz outputs; say whether plane_vectors cover the fold aggregate; pass 2 runs the full rebuild incl. Eeg.

## Plan Authored
**Status**: complete
**When**: 2026-11-26 18:32 -05:00
**By**: Claude Code Agent (claude-sonnet)
**Plan**: `.agent/work-plans/issue-29/plan.md` at `9a19cee`

Revision after plan review: pass 2 accepted only if the node leaves red (or n_out strictly drops) and z agrees with the neighbour-plane intercept within k_sigma*sqrt(var_a+sigma'^2); wrong-cluster case pinned. Tests run through grid_surface on a >=5x5 block built from soundings, baseline recorded on first run. Config keys for every threshold (min_clear starts at 2), raw DEM slope passed (DEM gate skipped without slope_from_dem), planar/gradient in Surface and both npz writers. plane_vectors cover unfolded entries only (aggregate is PR B); pass 2 runs full _robust incl. Eeg.

## Plan Review
**Status**: complete
**When**: 2026-11-26 18:31 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: ready

**Issue**: #29 — Steep-bank relief for the node rebuild: a gradient from the neighbourhood, not the node's own split
**Plan**: `.agent/work-plans/issue-29/plan.md` at `9a19cee`
**Branch**: `feature/issue-29`

Second review, after the revision. Host decisions taken as given (thresholds tuned on the replays, counts only; DEM gate first; batch-first split).

### Prior findings

| # | Finding | Addressed? |
|---|---|---|
| 1 (must) | Pass-2 acceptance by count only | Yes: leave red or `n_out' < n_out` strictly, AND `|z'_main - a| <= k_sigma*sqrt(var_a + sigma'^2)`; wrong-cluster case pinned |
| 2 (must) | Flat-edge Monte Carlo at grid level | Yes: through `grid_surface` on >=5x5, neighbours from soundings, `planar` false everywhere, absorbed equal to baseline recorded on first run |
| 3 (should) | Config key for every threshold | Yes: `plane_min_nin`, `plane_min_clear` (starts at 2), `plane_planarity`, `plane_dem_floor` added |
| 4 (should) | Raw DEM slope for the gate | Yes: raw `slope_field` sample passed; DEM gate skipped with global `slope` |
| 5 (should) | `planar`/gradient in `.npz` | Yes: `Surface` plus both `np.savez` writers |
| 6 (minor) | Aggregate scope | Yes: PR A unfolded entries only; moments are PR B |
| 7 (minor) | Eeg in pass 2 | Yes: full `_robust` |

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | PR A about 8 files in `icecube/` plus docs; logger port in PR B. |
| Issue alignment | Good | Issue-review actions and both prior must-fixes covered. Serves: "Roadmap line "`icecube` runs on logger sessions in the project's local frame" and the estimator tracking the rink through the season; the project keeps no goals or design document, so none." Checked: matches the issue review. |
| File targeting | Needs work (minor) | Committed `coverage_vectors.json` must be regenerated in PR A (finding 1); `topo.py` has three `grid_surface` call sites (finding 2). |
| Consequences | Good | Vectors, docs, example toml, `.npz` outputs, logger README in PR B. |
| Principle alignment | Good | Every threshold a config key; synthetic tests in local metres; replays counts only. Design picture: none, stated. Prior art: stated. |
| ADR compliance | Good | Project has no ADRs. |
| ROS conventions | N/A | Not a ROS project. |

### Findings

1. **[File targeting, should-fix] PR A must commit the regenerated `logger/app/src/test/resources/coverage_vectors.json`.** `test_logger_vectors.test_committed_file_is_current` compares the committed file to `logger_vectors.render()`, so adding `plane_vectors` (and any new `grid_surface` defaults that `_nodes_defaults` reads from the signature) fails that test until the file is regenerated. The Files table lists `coverage_vectors.json` only under PR B. `Vectors.java` reads by key, so the extra section is additive for the Java side as planned.
2. **[File targeting, minor] All three `topo.py` `grid_surface` calls need the raw slope.** Lines ~363, ~465 and ~576 each call `grid_surface(..., slope_array(...))`; the plan names only ~366. Have `slope_array` (or a sibling) return both arrays so the three paths stay alike.
3. **[Approach, minor] Entries need `sh`, and the `sh` term needs defining.** Today `v = sz^2 + (slope*(d + sh))^2`; with the slope term dropped, the plan's "the `sh` term" reads as `(|g|*sh)^2` (horizontal error times the fitted gradient), which needs `sh` per entry. Entry gains `dx, dy, sz2` only. Add `sh` (or `sh2`) and state the formula. Entry is a positional tuple (`nodes.entry`), so check `NodeStore` and `logger_vectors` unpacking when it grows.
4. **[Approach, minor] Say the plane pass is robust-mode only.** `grid_surface(robust=False)` uses `_running`; state that `plane` is ignored (or refused) there.

### Summary

The revision addresses both must-fixes and all should-fixes from the first review; the approach and tests are sound. The remaining points are small and can be handled in implementation: regenerate and commit the vectors file in PR A, wire the raw slope through all three `topo.py` paths, carry `sh` on entries, and state robust-only. Ready for implementation.

### Recommended Actions

- [ ] Regenerate and commit `coverage_vectors.json` in PR A (move it into PR A's file list).
- [ ] Pass the raw DEM slope at all three `topo.py` `grid_surface` call sites.
- [ ] Carry `sh` on entries and write the pass-2 variance formula explicitly.
- [ ] State that the plane pass applies only with `robust=True`.

## Checkpoint
**Status**: complete
**When**: 2026-11-26 18:32 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (plan review ready, no must-fix; orchestrator table allows proceed)
**After**: plan
**Decision**: proceed

Implement PR A (batch). Review notes into the handoff: regenerate and commit coverage_vectors.json in PR A (test_committed_file_is_current); pass the raw slope at all three grid_surface call sites in topo.py; entries carry sh too, with the pass-2 sh variance term written out and every tuple unpack checked; the plane pass runs only in robust mode.

## Implementation
**Status**: complete
**When**: 2026-11-26 18:45 -05:00
**By**: Claude Code Agent (claude-opus)
**Branch**: feature/issue-29 at `e966006`

PR A (batch) only; PR B (logger ring re-reduction) not started.

- `961f3f5` nodes.py: entries carry `dx, dy, sz2, sh` (positional tuple grew to 11; `Hyp.join`/`nr` read `e[:7]`; `NodeStore` aggregate gets dx = dy = 0, sz2 = v); `plane_fit`, `reduce_to_plane`, `plane_pass`, `PLANE_*` codes, `PLANE_DEFAULTS`; `grid_surface(plane=True, raw_slope=None, plane_*)`, robust mode only (ignored with `robust=False`); `Surface.planar/gradient/plane_code`. Pass-2 variance written out: `v' = sz2 + (|g|*sh)^2 + d' C_g d`. 11 pinned tests in `TestPlanePass`.
- `b2e495e` topo.py: `slope_array` returns (allowance, raw DEM slope or None), all three `grid_surface` call sites pass the raw slope; `[[grids]]`/`[grid]` read `plane` and `plane_*`; both npz writers save `planar`, `gradient`; outlier table counts planar nodes, gradient column.
- `209e53a` logger_vectors: `plane_params` + `plane_vectors` (4 cases, logger rule: count promotion, no Eeg, unfolded entries; gate-threshold guard via a `trace` on `plane_pass`); `coverage_vectors.json` regenerated (additive, 0 lines removed).
- `e966006` howto and `icecube.example.toml`.

Plan deviations (both found on the synthetic banks / private replays):
1. Neighbour heights are placed at grid offset plus their main's weighted centroid (`cx, cy` from `_robust`), not the bare grid offset. On a bank a disc's members are not centred, so the bare offsets made 341 of 935 synthetic 0.3 m/m red nodes fail planarity; with centroids 0 failed. This is the batch form of PR B's `WDX/W` moments.
2. Significance is `|g| - allowance > k_sigma * sigma_g` (along g), not `|g| > allowance`. Without it 11-21 accepted 2 nodes whose red came from fix-settling captures (fits with chi 2.92 and sigma_a 9.4 cm) and moved them 7-10 cm away from the survey model; with it both are rejected, and no synthetic bank case is lost.

Tuning (private replays, batch `grid_surface`, Coverage defaults, slope 0.05, in-area nodes; counts only; driver in private/analysis/plane-29/): defaults kept at `plane_min_clear` 2, `plane_red_weight` 0.25, `plane_planarity` = k_sigma, `plane_dem_cap` 2.0, `plane_dem_floor` 0.05. min_clear 3 left 77 of 356 interior synthetic 0.3 m/m red nodes red (vs 28 at 2) and changed nothing on the replays once the significance gate was in; red weight 0.1 and planarity 2.0-2.5 changed nothing.
- 11-26: red 6 -> 1 (the 5 bank nodes planar, heights now within 1.1 cm of the yard model, were 5-16 cm off; the bad capture at a re-fix stays red). New red 0, ok -> amber 0.
- 11-21: red 19 -> 17 (batch baseline 19, #24's logger count 18: weight vs count promotion). 12 single-line nodes fail the span gate, 2 not planar, 3 not significant; 2 accepted, both edge nodes (5 neighbours) holding earlier settling captures, moved 3-4 cm toward the model but still 9-11 cm above it. With the 90 s feed rule: 6 red -> 6 (all single-line relief, span gate). With slope_from_dem from the survey model: 14 -> 14 (those 2 caught by the DEM gate). New red 0, ok -> amber 0 in every variant.

Tests: `python3 -m unittest discover icecube/tests` 79 OK; `cd logger && ./gradlew testDebugUnitTest --max-workers 4` BUILD OK, 164 tests, 0 failures (parity test reads the regenerated vectors). Every commit with all hooks. C: free 114 GB before, 114 GB after.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 18:52 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: changes-requested

**Branch**: feature/issue-29 at `0c9e00d`
**Base**: main
**Depth**: Standard (reason: ~1000 non-generated lines across 12 files, numerical core of the estimator)
**Must-fix**: 2 | **Suggestions**: 6
**Round**: 1 | **Ship**: continue — round 1: 2 must-fix; first round always re-reviews after fixes

Specialists: static analysis (no linter installed: py_compile only; 79 tests OK), governance (public repo: no coordinates, names or paths in the diff), plan drift (two flagged deviations, recorded in Implementation), Claude adversarial, cross-model gemini (complete) and codex (complete).

Flagged deviations:
1. Neighbour at grid offset + main's weighted centroid: sound. `z()` and `_centroid` use the same weights, so on a plane the main's z lies exactly on the plane at its centroid; selection of an up/down-slope cluster moves both together, leaving only k-sigma truncation (small, red at 0.25). No new circularity beyond the shared-sounding overlap the plan review accepted. But it exposes a conditioning gap (must-fix 1): the span test still uses grid offsets.
2. Significance |g| - allowance > k_sigma*sigma_g: sound and conservative; sigma_g = sqrt(u'C_g u) is the right directional variance. Fails safe (node stays red). It stacks with max(1, chi^2) scaling, so near the planarity limit the bar is ~9 raw sigmas; watch on curved banks via plane_code counts.

### Findings
- [x] (must-fix) Span/conditioning gate checks grid offsets, not the centroid positions the fit uses; on a survey fringe the centroids collapse toward a line, C_g blows up, pass-2 variances inflate (d'C_g d) and the agreement test passes on a huge var_a. Pinned evidence: `coverage_vectors.json` wrong_cluster_keeps_pass1 node (2,-2) accepted at z 0.360 vs true 0.300, sigma 5.3 cm. Gate on the actual (X, Y) conditioning (e.g. max eigenvalue of C_g or min spread of weighted centroids), make every continuous gate fail closed on non-finite values, add a fringe case to the tests/vectors (gemini, confirmed by lead) — `icecube/nodes.py:400-425`
- [x] (must-fix) Raw DEM slope NaN (outside DEM coverage) is coerced to 0, so with slope_from_dem no node outside the DEM can ever be planar (significance needs g > floor, DEM gate needs g <= floor). Skip the DEM gate when the slope is not finite, or document and test the chosen behaviour (codex, gemini, Claude adversarial, lead) — `icecube/nodes.py:487-489`
- [x] (suggestion) Agreement test and chi/sigma_g treat neighbours as independent although neighbour discs share the centre's soundings (a and z' positively correlated, so the test is looser than k_sigma); say so in the docstring instead of calling a an independent prediction (codex, Claude adversarial) — `icecube/nodes.py:75-99`
- [x] (suggestion) Save `plane_code` in both npz writers, and mask `planar`/`gradient` where topo masks z (codex, gemini, Claude adversarial) — `icecube/topo.py:197-200,380-383`, `icecube/cli.py:128`
- [x] (suggestion) Cast `PLANE_KEYS` values from TOML (int/float) like the neighbouring keys — `icecube/topo.py:192`
- [x] (suggestion) plane_vectors cover no PLANE_NOT_PLANAR case and the flat case never reaches plane_fit; add a ridge case so PR B's port pins every non-DEM code (gemini) — `icecube/logger_vectors.py:363`
- [x] (suggestion) Docs say gradient is set where a plane passed the fit gates; it is also set on NOT_STEEP and DEM (gemini) — `docs/howto/ice-cube.md:155`
- [x] (suggestion) Note for PR B: the centroid is over the main's members (weighted by w), not over all entries as the plan's WDX/W aggregate moments read; the Java port must match — `.agent/work-plans/issue-29/plan.md`

## Implementation
**Status**: complete
**When**: 2026-11-26 18:59 -05:00
**By**: Claude Code Agent (claude-opus-5-5)

**Branch**: feature/issue-29 at `fa1519b`
**Addressed**: Local Review (Pre-Push) at `0c9e00d` (2026-11-26 18:52 -05:00)
**Commits**: 129d26b, 2b338d0, d82f669, fa1519b

### Actions
- [x] (must-fix) Conditioning gate on the centroids the fit uses: grid-offset span check replaced by sqrt(larger eigenvalue of scaled C_g) <= `plane_max_sigma_g` (default 0.08 m/m; accepted fits on test blocks and replays <= 0.06, bunched fringe fits 0.1-0.35), new code PLANE_ILL_CONDITIONED (9); every continuous gate fails closed on NaN/inf; fringe case in test_icecube and plane_vectors; wrong_cluster node (2,-2) now code 9 — `icecube/nodes.py` plane_fit/plane_pass
- [x] (must-fix) Non-finite raw DEM slope skips the DEM gate (others still apply), test and docs line — `icecube/nodes.py` plane_pass, `docs/howto/ice-cube.md`
- [x] (suggestion) Docstring notes the shared-sounding correlation of a and z' (test looser than k_sigma) and neighbour non-independence — `icecube/nodes.py` module docstring
- [x] (suggestion) plane_code saved in both npz writers; planar/gradient/plane_code masked where topo masks z — `icecube/topo.py`, `icecube/cli.py`
- [x] (suggestion) PLANE_KEYS typed, `topo.plane_kwargs` casts TOML values (shared with cli) — `icecube/topo.py`
- [x] (suggestion) Ridge case (code 4) and flat-cluster case (code 5) in plane_vectors; vectors now pin codes 1-5, 7-9 — `icecube/logger_vectors.py`
- [x] (suggestion) Docs say gradient is set from the significance gate on — `docs/howto/ice-cube.md`, `icecube/nodes.py`
- [x] (suggestion) PR B note: centroid over the main's members; conditioning gate and fail-closed form to port — `.agent/work-plans/issue-29/plan.md`

### Checks
- `python3 -m unittest discover icecube/tests`: Ran 82 tests, OK
- `./gradlew testDebugUnitTest --max-workers 4`: pass (C: 114 GB free)
- coverage_vectors.json regenerated; only plane_params and plane_vectors changed
- Private replays (counts only): 11-21 red 19 -> 17, 11-26 red 6 -> 1 (unchanged); with DEM 14 -> 14, 1 -> 1

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 19:08 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: changes-requested

**Branch**: feature/issue-29 at `28bcf3f`
**Base**: main
**Depth**: Standard (reason: ~1000 non-generated lines across 12 files, numerical core of the estimator; fix round classified on the whole diff)
**Must-fix**: 2 | **Suggestions**: 5
**Round**: 2 | **Ship**: recommended — round 2: 2 mechanical must-fix (prev 2), not rising; fix and ship rather than another full round

Specialists: static analysis (no linter installed: py_compile only; 82 tests OK), governance (public repo: nothing private in the diff; vectors synthetic), plan drift (none new; round-1 fixes match their Implementation entry), Claude adversarial (no must-fix, 3 suggestions; regenerated vectors byte-identical), cross-model gemini (complete) and codex (complete). Round-1 must-fixes (fringe conditioning gate, NaN DEM slope) verified fixed.

### Findings
- [x] (must-fix) Conditioning gate fails open on exactly collinear neighbour centroids: `np.linalg.inv` returns a non-PD Ninv (one huge negative eigenvalue), `_sigma_g_max` takes only the larger eigenvalue (passes), `sg` clamps a negative u'C_g u to 0, and reduce_to_plane can get negative v' (ValueError in Hyp.nr) or accept a garbage gradient. Lead reproduced: 30 of 500 collinear fits return code 0, all with a negative eigenvalue. Require C_g positive definite (cxx>0, cyy>0, cxx*cyy-cxy^2>0, finite) and var_a>0 before code 0 (else PLANE_ILL_CONDITIONED, e.g. via Cholesky of N), fail closed on negative u'C_g u, add a collinear case to tests/plane_vectors (gemini, confirmed by lead) — `icecube/nodes.py:429-452,512`
- [x] (must-fix) Pass 2 adds the gradient error d_i'C_g d_i to each entry as independent variance, so it averages down by 1/n, but it is common to all entries (error -dg.d_bar); sigma' understated (adversarial measured up to ~40% at edge nodes; codex: 4 cm vs 0.4 cm for one-sided soundings), making the output sigma and the agreement test too tight. Add d_bar'C_g d_bar (d_bar = pass-2 main's weighted centroid) to the accepted node's sigma^2 and to the agreement variance; port note for PR B (codex, gemini, Claude adversarial) — `icecube/nodes.py:455-466,527-535`
- [x] (suggestion) Neighbour fit weights by main's w, which excludes the pass-bias part of sigma; across a pass seam chi and sigma_g are noise-only. Weight by 1/sigma^2 or document the assumption next to the correlation caveat (codex, gemini) — `icecube/nodes.py:498`
- [x] (suggestion) topo masks after nodes.grid_surface, so masked nodes serve as plane neighbours (with masked=false their non-ground heights feed the fit). Consider passing the node mask into plane_pass to exclude them, or state it (gemini, Claude adversarial) — `icecube/topo.py:196-210`
- [x] (suggestion) Docs npz contents list omits plane_code (gemini, Claude adversarial) — `docs/howto/ice-cube.md:236`
- [x] (suggestion) grid_surface duplicates PLANE_DEFAULTS as literals for 7 of 8 plane_* args; take all from PLANE_DEFAULTS (gemini) — `icecube/nodes.py:596-600`
- [x] (suggestion) PR B note: plane_pass counts n_in as len(entries) - n_out, which undercounts folded entries (cnt > 1) if reused on aggregates (gemini) — `icecube/nodes.py:482`

## Implementation
**Status**: complete
**When**: 2026-11-26 19:14 -05:00
**By**: Claude Code Agent (claude-opus)

**Branch**: feature/issue-29 at `80ce1a2`
**Addressed**: Local Review (Pre-Push) at `28bcf3f` (2026-11-26 19:08 -05:00)
**Commits**: bab1cb0, 9f9adde, d74cb05, 5a2724a, 0e4b713, 80ce1a2

### Actions
- [x] (must-fix) Conditioning fails closed: Cholesky of A'WA, var_a > 0 and C_g positive definite (`nodes._pos_def`) required for code 0, else PLANE_ILL_CONDITIONED; non-positive u'C_g u fails closed (no clamp). Unit test of 500 random collinear fits (121 returned code 0 before, 0 now) and a `collinear_ill_conditioned` plane_vectors case — `icecube/nodes.py` plane_fit, plane_pass (bab1cb0)
- [x] (must-fix) Accepted node's sigma'^2 gains d_bar'C_g d_bar (d_bar = pass-2 main's centroid), which also feeds the agreement test; test pins it; PR B port note in plan.md — `icecube/nodes.py` plane_pass (bab1cb0, 80ce1a2)
- [x] (suggestion) Neighbour fit kept weighted by w (host decision); docstring and comment say why pass bias is left out — `icecube/nodes.py` (9f9adde)
- [x] (suggestion) topo builds the node mask before gridding; `grid_surface(node_mask=)` / `plane_pass(skip=)` keep masked nodes out of fits and untried; test — `icecube/topo.py`, `icecube/nodes.py` (d74cb05)
- [x] (suggestion) plane_code added to the docs' npz list (plus PD gate, common gradient error, masks) — `docs/howto/ice-cube.md` (5a2724a)
- [x] (suggestion) grid_surface's plane_* defaults read from PLANE_DEFAULTS — `icecube/nodes.py` (0e4b713)
- [x] (suggestion) PR B note on n_in vs folded cnt: code comment (bab1cb0) and plan.md port notes (80ce1a2)

### Checks
- `python3 -m unittest discover icecube/tests`: Ran 85 tests, OK
- coverage_vectors.json regenerated, matches `python3 -m icecube.logger_vectors`
- logger `./gradlew testDebugUnitTest --max-workers 4`: BUILD SUCCESSFUL (C: 114 GB free)
- Private replays (counts only), unchanged from round 1: 11-21 red 19 -> 17; 11-21 fix90 6 -> 6; 11-21 DEM 14 -> 14; 11-26 red 6 -> 1; 11-26 DEM 1 -> 1; new red 0, ok -> thin 0 in every variant
- Review prompt/findings scratch files removed

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-26 19:17 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: approved

**Branch**: feature/issue-29 at `ef8f74b`
**Base**: main
**Depth**: Standard (reason: round 3 convergence check per handoff)
**Must-fix**: 0 | **Suggestions**: 4
**Round**: 3 | **Ship**: recommended — no must-fix findings; remaining suggestions can be applied or tracked

Scope: round 2's findings and the fix diff 9d4c380..HEAD. Specialists: static analysis (no linter installed: py_compile clean; 85 tests OK; coverage_vectors.json byte-identical to a fresh `python3 -m icecube.logger_vectors`), governance (public repo: nothing private in the fix diff; new vector synthetic), plan drift (none; plan.md port notes match the code), Claude adversarial (no must-fix). Cross-model: skipped (round-3 convergence check, per handoff). All 7 round-2 items verified addressed: Cholesky + `_pos_def` fail closed on collinear centroids, u'C_g u <= 0 fails closed, d_bar'C_g d_bar added in the same node-centred frame and weights as the main, masked nodes skipped as tried and as neighbours (mask orientation matches the store index), docs and defaults.

### Findings
- [ ] (suggestion) Agreement variance leaves out the cross term 2 d_bar'cov(a, g) (z' - a error is -dg.d_bar - da); small for symmetric neighbours, larger at edges or with masked neighbours; cov[0, 1:] makes it exact — `icecube/nodes.py:575-576`
- [ ] (suggestion) Per-entry d_i'C_g d_i stays in v' and d_bar'C_g d_bar is added on top, so sigma'^2 is a conservative upper bound; say so in the docstring — `icecube/nodes.py:565-570`
- [ ] (suggestion) test_collinear_centroids_never_pass also accepts PLANE_NOT_PLANAR, so a regression routing collinear fits through a garbage beta into planarity would pass; assert ILL_CONDITIONED on most cases — `icecube/tests/test_icecube.py`
- [ ] (suggestion) Mask test uses a square grid and bypasses topo.grid_surface's meshgrid; a non-square grid with an asymmetric mask would catch a transposed mask — `icecube/tests/test_icecube.py`

## Checkpoint
**Status**: complete
**When**: 2026-11-26 19:19 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (pre-push review approved at round 3, no must-fix; orchestrator table allows publish)
**After**: publish
**Decision**: publish

PR A (batch) of two; Part of #29. Round-3 suggestions (agreement cross term, conservative-bound docstring, collinear test strictness, non-square mask test) left for triage.

## Integrated Review
**Status**: complete
**When**: 2026-11-26 19:20 -05:00
**By**: Claude Code Agent (claude-opus)
**Reviews-read**: none

**PR**: #34 at `6e5ed5c`
**Sources**: 1 (Local Review (Pre-Push) @ `ef8f74b`, covers `6e5ed5c` via bookkeeping; CI rollup: no checks configured; no hosted or human reviews)
**Cross-source confirmations**: 0
**CI**: all-pass (no checks configured)

No GitHub reviews, inline comments or conversation comments on the PR. The four round-3 suggestions were re-checked against the code at `6e5ed5c` (only progress.md changed since `ef8f74b`); all four still apply. None is must-fix: they tighten an error budget, a docstring and two tests.

### Findings
- [x] (suggestion, Local Review (Pre-Push) @ `ef8f74b`) Agreement gate variance `var_a + sigma'^2` omits the cross term 2 d_bar'cov(a, g): the z' - a error is -dg.d_bar - da, and plane_fit returns only var_a and cov_g, not cov[0, 1:]; return the full 3x3 (or cov[0, 1:]) and add the term — `icecube/nodes.py:577-578`
- [x] (suggestion, Local Review (Pre-Push) @ `ef8f74b`) Per-entry d_i'C_g d_i stays in v' and d_bar'C_g d_bar is added on top, so sigma'^2 is a conservative upper bound; the comment at the add does not say so — state it there or in the module docstring — `icecube/nodes.py:568-572`
- [x] (suggestion, Local Review (Pre-Push) @ `ef8f74b`) test_collinear_centroids_never_pass accepts PLANE_NOT_PLANAR too, so a regression that reaches planarity with a garbage beta passes; count codes and assert ILL_CONDITIONED on (nearly) all 500 — `icecube/tests/test_icecube.py:782-793`
- [x] (suggestion, Local Review (Pre-Push) @ `ef8f74b`) Mask test is on a square 7x7 grid and its 4-of-8 mask is symmetric under transpose (both orientations leave 4 neighbours, PLANE_FEW), and the shape-mismatch check uses (2, 2); use a non-square grid and a mask whose transpose changes the outcome — `icecube/tests/test_icecube.py:723-733`

### False positives
- none

## Checkpoint
**Status**: complete
**When**: 2026-11-26 19:24 -05:00
**By**: Claude Code (claude-opus-5-5)
**Decided-by**: host (four small suggestions; orchestrator table allows address at findings)
**After**: findings
**Decision**: address

Fix all four: agreement-gate cross term (return cov[0,1:] from plane_fit and add 2·d̄'cov(a,g)); state that sigma'^2 is a conservative upper bound; collinear test asserts PLANE_ILL_CONDITIONED on nearly all cases; mask test on a non-square grid with an asymmetric mask through topo.grid_surface.

## Implementation
**Status**: complete
**When**: 2026-11-26 19:29 -05:00
**By**: Claude Code Agent (claude-opus)

**PR**: #34 at `70293ea`
**Addressed**: Integrated Review at `6e5ed5c` (2026-11-26 19:20 -05:00)
**Commits**: 3f32ef8 (merge origin/main, #32), fac18d6, 0aedb88, 4bd1837, 70293ea

Merged origin/main (#32, issue 9): auto-merged cleanly; coverage_vectors.json regenerated from the merged generator, byte-identical to the merge result (SIGMA_CASES additions and plane_params/plane_vectors both present).

### Actions
- [x] Agreement gate adds 2 d_bar'cov(a, g); plane_fit returns cov_ag = cov[0, 1:]; module docstring updated. Vectors regenerate unchanged — `icecube/nodes.py` plane_fit/plane_pass
- [x] sigma'^2 stated as a conservative upper bound (module docstring and the comment at the add) — `icecube/nodes.py`
- [x] test_collinear_centroids_never_pass counts codes: only ILL_CONDITIONED/NOT_PLANAR, ILL_CONDITIONED >= 400 of 500 (433 now). The other 67 stop at NOT_PLANAR because planarity is gated before conditioning; all 67 also fail conditioning on the unscaled inverse, so checking conditioning first would give 500/500 (a code-order change, left out) — `icecube/tests/test_icecube.py`
- [x] Mask orientation test through topo.grid_surface on a 9 x 5 grid with an asymmetric polygon mask (target node goes ACCEPTED -> PLANE_FEW; exact masked (row, col) asserted; transposed mask raises) — `icecube/tests/test_icecube.py`

### Checks
- `python3 -m unittest discover icecube/tests`: Ran 87, OK
- `python3 -m unittest discover -s analysis/qgis/tests`: Ran 110, OK (skipped=5)
- `./gradlew testDebugUnitTest --max-workers 4`: BUILD OK (C: 113 GB free)
- Private replays (counts only), unchanged: 11-21 red 19 -> 17; 11-21 fix90 6 -> 6; 11-21 DEM 14 -> 14; 11-26 red 6 -> 1; 11-26 DEM 1 -> 1; new red 0, ok -> thin 0 in every variant

## Local Review
**Status**: complete
**When**: 2026-11-26 19:52 -05:00
**By**: Claude Code Agent (claude-opus)
**Verdict**: changes-requested

**PR**: #34 at `dc5bfb8`
**Depth**: Standard (reason: whole-PR re-review after fix round, ~1.5k hand-written lines in icecube/ plus a generated 10.7k-line vector file; no security or governance-file triggers)
**Must-fix**: 1 | **Suggestions**: 4

Specialists: static (no Python linter installed: ruff/flake8/pyflakes absent, py_compile only; 87 tests OK), governance (public-repo check clean: synthetic local metres only), plan drift (fix round matches the four triaged items), Claude adversarial, cross-model gemini + codex (both complete). The fix round itself (fac18d6..70293ea) is correct: the cross-term sign checks out (error of z' - a is -dg.d_bar + noise - da), and the two tests now pin what they claim.

### Findings
- [x] (must-fix) Agreement gate tolerance includes the node's pass-bias variance (o2 sigma = noise + var_bias + vbar) while var_a is noise-only by design; by the module's own argument the shared bias cancels in z' - a, so tol is inflated by k*sb. Reproduced: test_wrong_cluster_keeps_pass1's +15 cm cluster goes PLANE_OFF_PLANE -> PLANE_ACCEPTED (cleared, +14 cm error) with sb = 0.01, 0.02 or 0.03; every TestPlanePass test runs sb = 0. Use the noise-only variance (1/W' + vbar) in vagree, keep var_bias in the output sigma, and add a test with sb > 0 (gemini, confirmed locally) — `icecube/nodes.py:584`
- [x] (suggestion) Check conditioning on the unscaled Ninv (PD + sigma_g) before solving beta and gating planarity, so collinear fits return ILL_CONDITIONED deterministically; the committed collinear_ill_conditioned vector depends on a roundoff Cholesky failure, and 18 of 120 neighbour summation orders give PLANE_NOT_PLANAR instead, a parity hazard for the Java port (gemini) — `icecube/nodes.py:448-468`, `icecube/logger_vectors.py:404`
- [x] (suggestion) Non-finite inputs fail open at the edges: with plane_max_sigma_g = inf an indefinite covariance passes (sigma_g set to inf compares <= inf), likewise chi = inf with plane_planarity = inf; an infinite raw DEM slope skips the DEM gate (only NaN means outside cover). Return ILL_CONDITIONED directly when not _pos_def, and skip the DEM gate on NaN only (gemini, codex) — `icecube/nodes.py:461-468`, `icecube/nodes.py:562`
- [x] (suggestion) Every plane test and vector bank slopes along x only; a y-axis regression (gy sign, cy on the wrong axis) would pass. Add one bank sloping along y or diagonally on a non-square grid (Claude adversarial; a probe shows it works today) — `icecube/tests/test_icecube.py:617`, `icecube/logger_vectors.py:347`
- [x] (suggestion) table_outliers now lists planar nodes but still sorts by maxnr and cuts at 30; cleared planar nodes have low maxnr and drop off on large grids while the header counts them. Say the list is truncated or give planar nodes their own ordering (Claude adversarial) — `icecube/report.py:181`
