# Plan: Steep-bank relief for the node rebuild: a gradient from the neighbourhood, not the node's own split

## Issue

https://github.com/rolker/ice_cube/issues/29

## Context

#24 (PR #28) made `icecube/nodes.py` (`rebuild`, `NodeStore`, `grid_surface`) and the logger's
`core/Coverage.java` a median-first rebuild with outlier bins and the R4 colour rule. Nodes still red
afterwards are almost all on a steep bank (10-16 cm of relief inside the 0.75 m capture disc, beyond
the 0.05 m/m allowance), so the up- and down-slope captures land in outlier bins. A plane fitted to
the node's own entries is circular (the first pass already split the far soundings off) and, fitted
to all entries, absorbs a +15 cm edge outlier on flat ground in 262 of 400 trials (76 without a plane).
The gradient has to come from outside the node's own split: its neighbours.

## Approach

Two stages, the same definition in Python and Java, held by parity vectors.

1. **Entries carry geometry.** Entry gains `dx, dy` (sounding minus node centre, m) and `sz2`
   (`sz^2`, so the slope-free variance is `sz2`). Batch `grid_surface` never folds, so PR A's
   `plane_vectors` cover unfolded entries only. The folded aggregate exists only in `NodeStore`
   (Coverage.java's mirror) and is PR B's: first moments `WDX = sum w*dx`, `WDY = sum w*dy` next to
   `W`, `WZ`, entering a re-reduction as a pseudo-entry at offset `(WDX/W, WDY/W)` (exact for a linear
   reduction; its slope term cannot be removed, so conservative). PR A does not define or test it.
2. **Pass 1 (unchanged).** Every node is rebuilt as today; keep each node's main `z`, `W`, `n_in`,
   status (red when `n_out > 0` and `n_in < 2*n_out`).
3. **Neighbourhood gradient** for each node red after pass 1 (only those; clear and amber nodes are never
   touched, which keeps flat ground at baseline): weighted least-squares plane `z = a + gx*X + gy*Y`
   through the main `z` of the 8 neighbours at their grid offsets, weights `W_main`. The node itself is
   excluded. The fit gives `a`, its variance `var_a`, and the gradient covariance.
4. **Gates.** The plane path stays off, and the node keeps its pass-1 result, unless all hold. Each
   threshold is a config key (item 6):
   - at least `plane_min_neighbours` (5) usable neighbours (non-empty, main `n_in >= plane_min_nin`, 2).
     Survey-edge nodes with fewer than 8 neighbours work if they still reach the minimum and span at
     least 2 distinct `dx` and 2 distinct `dy`; otherwise off.
   - Red neighbours are used at `plane_red_weight` (0.25); empty ones not used. At least
     `plane_min_clear` of the usable are non-red. Default is not fixed at 3: the review estimates that,
     with 242 of 400 nodes red at 0.30 m/m, a min of 3 fails about a third of bank nodes
     (P(at most 2 of 8 non-red) about 0.31). Start at 2, tune on the replays with the red weight, and
     pin the 0.3 m/m case with a seed that holds across 1 to 3.
   - **Planarity:** weighted RMS residual in neighbour sigmas at most `plane_planarity` (own key,
     starts at `k_sigma`).
   - **Significance:** `|g|` exceeds the node's slope allowance (as raised by `slope_from_dem` where
     that is on).
   - **DEM gate (batch, `slope_from_dem` only):** `|g| <= plane_dem_cap * s_dem + plane_dem_floor`,
     with `s_dem` the raw DEM slope. `topo.slope_array` returns the clamped allowance
     `min(cap, max(floor, factor*s))`, not the slope, so `topo.py` also passes the raw `slope_field`
     sample to `nodes.grid_surface` as a new array. With a global `slope` (no `slope_from_dem`) no raw
     slope exists and the DEM gate is skipped; the other gates still apply.
5. **Pass 2.** Reduce each entry to the node centre, `z' = z - (gx*dx + gy*dy)`, with `v'` = `sz2` plus
   the `sh` term, slope-allowance term dropped, plus `(sigma_g * d)^2` from the fit covariance. Run the
   full rebuild path (`_robust`, so Eeg's cull is included and `n_out'` compares like with like).
   **Accept pass 2 only if both hold:** (i) the node leaves red, or `n_out' < n_out` strictly; and
   (ii) `|z'_main - a| <= k_sigma * sqrt(var_a + sigma'^2)`, with `a` the neighbour plane's intercept
   at the node (an independent prediction) and `sigma'` the pass-2 main's sigma. Otherwise keep pass 1.
   This stops a switched or worse height passing on a count tie. One plane pass only (a node cleared
   in the sweep is not used as a neighbour in the same sweep).
6. **Config** (`icecube.example.toml`, and `topo.py` reads them): `plane` (comparison switch),
   `plane_min_neighbours`, `plane_min_nin`, `plane_min_clear`, `plane_red_weight`, `plane_planarity`,
   `plane_dem_cap`, `plane_dem_floor`. Defaults tuned on the private replays, counts only.
7. **Outputs.** `Surface` dataclass gains `planar` (bool per node) and `gradient` (magnitude);
   `np.savez` calls in `icecube/cli.py:126` and `icecube/topo.py:366` write both, so the inspector
   can read them; `report.py` shows the planar count and gradient.

### Two PRs (decided: batch first, logger after)

- **PR A (first): Python batch.** `nodes.py` entries and `plane_pass`, `grid_surface` wiring, `topo.py`
  config and raw DEM slope, `Surface` and `.npz` outputs, report column, docs, the pinned tests below, and a new `plane_vectors` section (unfolded entries, no aggregate) generated by
  `logger_vectors.py`, additive so the logger's existing parity test keeps passing. The replays run in
  batch, so the thresholds settle there before anything is ported.
- **PR B (follow-up, issue stays open):** the Java port in `Coverage.java`: entry offsets, aggregate
  moments, ring re-reduction (a capture touches 9 nodes; pass 2 of a node needs its 8 neighbours' pass 1,
  so the 5x5 ring of 25 is re-reduced), consuming PR A's vectors. Why split: the logger change sits on the
  per-capture hot path and the repaint, needs its own timing check, and should port a settled definition.
  Port notes from PR A's review: a neighbour's centroid is the w-weighted mean (dx, dy) over its
  **main's members** only (`nodes._centroid`), not over all its entries as the `WDX/W` aggregate
  moments above read; with an aggregate in the main, the aggregate counts at `(WDX/W, WDY/W)` with its
  W, and an aggregate outside the main does not count. The fit gate is conditioning, not grid span:
  `sigma_g` = sqrt of the larger eigenvalue of the scaled `C_g`, closed form
  (`nodes._sigma_g_max`), against `plane_max_sigma_g`; every continuous gate fails closed on a
  non-finite value (port the `!(x <= limit)` form), and the DEM gate does not apply on the phone.
  From the second review round: conditioning also needs a Cholesky factorisation of `N = A'WA` to
  succeed and `var_a > 0`, `C_g` positive definite (`nodes._pos_def`), else code 9; a directional
  variance `u'C_g u` that is not positive fails closed (code 9), never clamped to 0. After the pass-2
  rebuild, the accepted node's `sigma'^2` gains `d_bar'C_g d_bar`, `d_bar` the pass-2 main's
  weighted centroid (`cx, cy` from `_centroid`; the gradient error is common to all reduced
  entries, so the per-entry `d'C_g d` alone averages down). The agreement test does not use
  that output sigma: its variance is noise-only, `var_a + 1/W' + d_bar'C_g d_bar +
  2 d_bar'cov(a, g)` (`cov_ag` from `plane_fit`), the pass bias left out. From the third
  round: conditioning runs first on the unscaled `(A'WA)^-1` (before beta is solved), then
  planarity, then conditioning again on the covariance scaled by `max(1, chi^2)`. A NaN
  limit fails a gate; an infinite `planarity` or `max_sigma_g` switches it off. In batch an
  infinite DEM slope fails the DEM gate and a NaN one (outside the DEM or by a NoData hole)
  skips it. `plane_pass` counts a node's `n_in` as `len(entries) - n_out`, which counts a folded
  aggregate as one; on the phone use the members' `cnt` sums, as `NodeStore.outputs` does.

## Files to Change

| File | Change |
|------|--------|
| `icecube/nodes.py` | entry `dx, dy, sz2`; `plane_pass`; `Surface.planar`/`gradient` (aggregate moments are PR B) |
| `icecube/topo.py` | gridding settings; raw DEM slope to `grid_surface`; `planar`/`gradient` in `.npz` (line ~366) |
| `icecube/cli.py` | `planar`/`gradient` in `np.savez` (line ~126) |
| `icecube/logger_vectors.py` | generate `plane_vectors` |
| `icecube/report.py` | planar count and gradient column |
| `icecube/tests/test_icecube.py`, `test_logger_vectors.py` | pinned cases below |
| `icecube.example.toml`, `docs/howto/ice-cube.md` | settings and description |
| (PR B) `Coverage.java`, `logger/README.md`, `coverage_vectors.json` | port, ring re-reduction |

## Tests and acceptance (pinned)

All through `grid_surface` on a synthetic block of at least 5x5 nodes, neighbours built from soundings
(never hand-set mains), so shared soundings feed neighbours as in production.

- **Bank clears:** true gradient 0.2 and 0.3 m/m, 8 soundings per disc, sz 2 cm, slope 0.05, k 3: the
  centre is red after pass 1 and not red after the plane pass. Repeated at a survey edge (5 neighbours)
  and with one red neighbour; 0.3 m/m seeded to hold across `plane_min_clear` 1 to 3.
- **Wrong cluster keeps pass 1:** a bank node plus a tight off-plane cluster where pass 2 would promote
  that cluster with no rise in `n_out`: acceptance rule (ii) fails and the result equals pass 1.
- **Flat edge outlier:** seeded Monte Carlo, 400 trials, flat 5x5 block, one +15 cm capture at the
  centre node's disc edge (also feeding its neighbours). Assert `planar` false at every node in every
  trial, and `absorbed_plane == absorbed_baseline` (the `plane = false` run in the same test). The
  baseline is recorded on first run at grid level (the single-node 76/400 may not reproduce) and then
  asserted as a value so drift shows.
- **Gate cases:** fewer than 5 usable, all-red neighbours, a non-planar ridge, no `slope_from_dem`
  (DEM gate skipped, others on): result equals pass 1 or the stated behaviour.
- **Replay acceptance (private, counts only), batch:** bank nodes cleared, flat-ground outliers absorbed
  none, on 11-21 and 11-26; clear nodes turned amber versus global slope 0.10 (321 on 11-26), expected 0.
  Thresholds (`plane_min_clear`, red weight, planarity, DEM floor) tuned here.

## Prior Art

#24's plan (plane to the node's own members, rejected by its review) and the issue's table. CUBE uses
neighbours only for its queue and median filters. A plane over neighbours is ordinary local trend removal
in gridding; nothing new is imported.

## Serves

Roadmap line "`icecube` runs on logger sessions in the project's local frame" and the estimator tracking
the rink through the season; the project keeps no goals or design document, so none.

## Design Picture

No change to the design picture (the project has no design document). The logger's re-reduction cost
grows from 9 to 25 nodes per capture; noted in `logger/README.md` in PR B.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo | Pinned cases synthetic, local coordinates; replays report counts only |
| Scripts take values from config | New settings in `icecube.example.toml`, none hardcoded |
| Rink clearance is not a rule | Not touched |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| none (project has no ADRs) | No | n/a |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| entry and aggregate format | parity vectors both sides, `logger_vectors.py` | Yes (A generates, B consumes) |
| rebuild outputs | `report.py`, `docs/howto/ice-cube.md`, `logger/README.md` | Yes |
| ring 9 to 25 nodes | logger timing on the phone | Yes, PR B |

## Open Questions

- Decided by the host: thresholds tuned on the replays (counts only); `slope_from_dem` as gate first,
  ridge prior only if needed; batch first, logger ring in PR B (issue stays open between).
- None open.

## Estimated Scope

Two PRs: A (Python batch, vectors, docs), then B (logger port and ring).
