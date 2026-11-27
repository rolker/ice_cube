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

### PR A: delivered (#34, merged as 5da4114)

The Python batch estimator: `nodes.plane_fit`, `plane_pass`, `reduce_to_plane`, `Surface.planar/gradient/plane_code`,
the `plane_*` settings, the raw DEM slope gate in `topo.py`, the report column, docs, and the `plane_vectors`
section of `coverage_vectors.json` (9 synthetic blocks, unfolded entries, `plane_params` pinning the
thresholds). It is the definition PR B ports. Behaviour as merged (verified against `icecube/nodes.py`):

- Pass 1 as before; a node is tried only when red after pass 1. A neighbour is usable with main `n_in >= 2`;
  its point is (grid offset + its main's weighted centroid `cx, cy`, from `_centroid` over the main's members),
  weight `W_main`, red ones at 0.25.
- `plane_fit` gate order: count (`PLANE_FEW`=2), non-red count (`FEW_CLEAR`=3), Cholesky of `A'WA` and the
  unscaled `(A'WA)^-1` positive definite with `var_a > 0` and `sigma_g <= max_sigma_g` (`ILL_CONDITIONED`=9,
  `sigma_g` inf when not PD), then planarity `chi <= planarity` (`NOT_PLANAR`=4, dict carries chi and the
  unscaled `sigma_g`), then the covariance scaled by `max(1, chi^2)` again PD and `sigma_g` within the limit.
- `plane_pass`: significance `g - allowance > k*sg` (`NOT_STEEP`=5), DEM gate (6; skipped when no raw slope or
  NaN, infinite fails), `reduce_to_plane`, rebuild, `sigma'^2 += d_bar'C_g d_bar`, `NO_GAIN`=7 when still red
  and `n_out'` not lower, agreement `|z' - a| <= k*sqrt(var_a + 1/W' + vbar + 2 d_bar'cov(a,g))`
  (`OFF_PLANE`=8, noise-only, no pass bias), else accepted (1).
- Limit rules: a non-finite value or a NaN limit fails; an infinite `planarity` or `max_sigma_g` is the gate off.
  Defaults `PLANE_DEFAULTS`: min_neighbours 5, min_nin 2, min_clear 2, red_weight 0.25, planarity None (k),
  max_sigma_g 0.08, dem_cap 2.0, dem_floor 0.05. Vector codes present: 0-5, 7, 8, 9 (6 is batch-only).

### PR B: the logger port (this PR; closes #29)

**Decisions.** (1) The phone has no DEM: the allowance is the constant `SLOPE` (0.05) at every node and the
DEM gate is skipped (raw slope absent), as `plane_vectors` does. (2) Pass-1 fields (`n, nIn, nOut, w, wz,
z(), sigma()`) stay pass-1, so the `nodes` and `sequences` vectors and their tests are untouched; new final
fields hold the plane outcome: `planar`, `gradient`, `planeCode`, and final `zFinal, wFinal, sigmaFinal,
nInFinal, nOutFinal` (equal to pass 1 when not planar). Status and `displaySigma` read the final values
(sigma includes `d_bar'C_g d_bar`). (3) The overlay shows a planar node by its final status: a node cleared
by the plane pass draws as OK or THIN like any other, no new colour (the overlay says where to capture more;
a bank node that is now trusted needs nothing). Planar/gradient/code are on `Node` for tests and the events
line, not drawn. (4) A `Coverage(Params, boolean plane)` flag (default true); the existing tests and vectors
run with it false where they pin pass-1 status, and `eventText` gains `plane=1 plane_min_neighbours=5 ...
plane_max_sigma_g=0.08` so a session replays exactly. No Settings toggle. (5) Version 0.10.0, versionCode 14
(`logger/app/build.gradle`, README title and Coverage section, `docs/howto/ice-cube.md` logger paragraph;
current 0.9.0 / 13). (6) The aggregate's `sz2`, `sh` come from the exact moments `WS`, `WH`, not a simpler `1/W`
and 0. (7) No new tint or tap readout for planar nodes, no Settings toggle (always on, recorded in the events
line). (8) Memory: per-entry geometry stays as doubles (floats would break the 1e-9 parity) and there is no
per-capture fallback table; the store is bounded per node by CAP, so `manyCapturesStayBounded` stays as is.

**Folded aggregate (new definition, Python first).** A node past CAP folds members into the aggregate, so the
plane pass needs the aggregate's geometry. Add moments `WDX = sum w dx`, `WDY = sum w dy`, `WS = sum w^2 sz2`,
`WH = sum w^2 sh^2`; the aggregate enters pass 2 as an entry at `(WDX/W, WDY/W)` with `sz2 = WS/W^2`,
`sh = sqrt(WH)/W` (the exact variance of the weighted mean, slope-free; the slope term cannot be removed from
a fold, so no worse than pass 1). Pass-1 weights stay `w = W, v = 1/W`. The aggregate in the main contributes
`(WDX/W, WDY/W)` at weight `W` to the main's centroid; an aggregate outside the main does not count. Counts:
`n_in` is the main's `cnt` sum, `n_out = n - n_in` with `n_dropped` included, never `len(entries)`.

**Steps, each with its test.**

1. **Python reference, `nodes.py`.** `NodeStore.add(z, v, id, dx, dy, sz2, sh)` keeps per-entry geometry and the
   four moments (zeroed when the aggregate is dropped); `_all()` builds the geometry-carrying pseudo-entry;
   `outputs()` adds `cx, cy`; `_robust` returns `n_in = main.cnt` and `n_out = sum(cnt) - main.cnt`;
   `plane_pass` takes `n_in` from `o["n_in"]` when present (falling back to `len - n_out`) at all three
   `len(entries) - n_out` sites (the red map, the neighbour `min_nin` usability, and the NO_GAIN test on `es2`;
   `_running` has no `n_in` and keeps the fallback), and adds `n_dropped` to `n_out'` through `run`. For the
   batch this changes nothing (every batch entry has `cnt = 1`, so `main.cnt == len - n_out`): no PR A result or
   committed vector changes, and the PR body says so. Tests (`test_icecube.py`): the 94 existing tests and the
   `plane_vectors` bytes unchanged; on a noise-free linear surface a node folded past CAP gives the same reduced
   mean `z'` as the same entries unfolded, within 1e-9; the aggregate's pass-2 variance equals the stated moment
   formula `WS/W^2 + g^2 WH/W^2 + d_bar'C_g d_bar` within 1e-9 (not the unfolded result: the unfolded pass
   reweights each entry by its own slope and `d_i'C_g d_i`, so it differs by design; the folded variance is
   checked to be at least the unfolded `1/W'`); a dropped aggregate zeroes the moments; a bank whose nodes each
   exceed CAP gets the same plane code and a `z'` within 1e-9 of the unfolded block.
2. **Vectors, `logger_vectors.py`.** Keep `plane_vectors` byte-identical. Add `plane_folded_vectors`: 2 blocks
   at about 40 soundings per disc (nodes past CAP), fed in arrival order through `NodeStore`, expected per node
   as in `_plane_case` plus `cx, cy`; and `plane_sequences`: a bank block fed one capture at a time, steps
   listing the nodes whose final status changed. The expectations come from a full recompute after each
   capture (rebuild every node, then `plane_pass` over the whole grid), not from a Python twin of the ring, so a
   wrong ring rule cannot be pinned on both sides; `LiveCoverage(plane=True)` is used only to check the
   twin against that recompute. The 1e-6 relative gate-margin guard and the off-the-radius-circle rule of
   `plane_vectors` apply to the folded and sequence vectors too (absolute 1e-9 parity stays). Update the module docstring and
   `test_logger_vectors.py` (cases do what they are named for; committed file matches); regenerate
   `coverage_vectors.json`.
3. **Java, geometry in the store (`Coverage.java`).** Node arrays `edx, edy, esz2, esh` (capture minus node
   centre, sz^2, sh) and aggregate `aggWdx, aggWdy, aggWs, aggWh`; `feed` takes them; `remove`, fold, and the
   aggregate drop mirror step 1. Pass-1 `cx, cy` stored on `Node` (weighted mean over the main's members,
   aggregate at `(WDX/W, WDY/W)`). Test: with `plane=false` every existing `CoverageTest` (nodes, sequences,
   order, bounded, incremental) passes unchanged; a new test that `cx, cy` match the vectors' `cx, cy`.
   Memory: four more doubles per entry is up to 2x the store; doubles stay (no floats, no per-capture table).
   Report in the README the worst-case bytes for a full grid (`MAX_NODES` at CAP entries) and a typical session;
   `manyCapturesStayBounded` is unchanged.
4. **Java, the fit (`core/PlanePass.java`, pure).** Codes as `static final int` equal to `nodes.PLANE_*`;
   `fit(nbrs, ...)` with a 3x3 Cholesky, `posDef`, closed-form `sigmaGMax`, the three-stage conditioning and
   planarity order, gates in the `!(x <= limit)` form (NaN fails, infinity off for planarity and max_sigma_g),
   and `reduce`. Unit tests (`PlanePassTest`): each gate by hand (few, few-clear, collinear singular `A'WA`
   code 9 with `sigma_g` inf, non-PD scaled covariance, NaN and infinite limits, NaN and infinite `chi`),
   non-positive directional variance fails closed (9), weights of red neighbours.
5. **Java, the pass in `Coverage`.** `Coverage.add` feeds every node within `captureRadius` (0.75 m on a 0.5 m
   grid), which is 3x3 only when the capture is near a node and up to 4 wide at a tie (capture 0.25 m off a
   node row). The ring is the Chebyshev-1 dilation of the actual fed set (typically 25 nodes, up to 30), not a
   fixed 5x5. A node's plane result depends only on its own pass-1 state and its 8 neighbours' pass-1
   `z, w, cx, cy, n_in` and red flag (fit reach is one cell), so this is exact. Each ring node is recomputed
   from the pass-1 state of its 8 neighbours (one sweep: a neighbour cleared by its plane stays red for its
   neighbours' fits; red is the pass-1 colour). A neighbour not created (`MAX_NODES`) is absent, as in the
   Python reference (confirm in step 2's twin check). Reuses the scratch arrays (no allocation per capture; run on the worker thread). Node
   gets the final fields; `status`/`displaySigma` read them; `drainTouched` lists a node whose final status
   or colour changed even when it was not fed; `eventText` adds the plane settings. Tests: `plane=false`
   status unchanged; `incrementalEqualsRebuild` (against a full rebuild, including a tie capture with 4 nodes in a line) and `onlyChangedNodesAreTouched` repeated with `plane=true`;
   `eventText` golden string.
6. **Parity (`CoverageTest`).** `everyPlaneVector`: all 9 `plane_vectors` cases, every node: `z1`, `n_out1`,
   `code`, `planar`, `gradient` (null as NaN), final `z`, `sigma` to 1e-9, `n_in`, `n_out`; fed in identity and
   a shuffled order (below CAP the state is a function of the set); `plane_params` equal the Java constants.
   Same for `plane_folded_vectors` in arrival order, and `everyPlaneSequence` status step by step. A guard
   that the vectors together exercise codes 0-5, 7, 8, 9 so a dropped case fails the test.
7. **Timing and repaint.** `CoverageWorkerTest`: a dense bank session of 5000 captures rebuilt under a time
   bound comparable to the pre-plane run recorded in the same test (ratio, not wall seconds); the raster
   patch for a ring-touched node. Report measured per-capture cost in the PR.
8. **Docs and version.** `logger/README.md` (Coverage section: the plane pass, fed-set ring (about 25 nodes), no DEM on the
   phone, final-status overlay, events line, vectors list, 0.10.0 build 14), `docs/howto/ice-cube.md` (logger
   paragraph), `logger/app/build.gradle` (0.10.0, 14), `docs/log.md` entry. Run `./gradlew test lint` plus
   `python3 -m unittest discover icecube/tests`.

## Files to Change (PR B)

| File | Change |
|------|--------|
| `icecube/nodes.py` | `NodeStore` geometry and moments, `_robust` counts by cnt, `plane_pass` counts |
| `icecube/logger_vectors.py`, `icecube/tests/test_logger_vectors.py`, `test_icecube.py` | folded vectors, sequences, tests |
| `logger/app/src/test/resources/coverage_vectors.json` | regenerated |
| `logger/.../core/Coverage.java` | geometry store, moments, ring pass, final fields, touched, event line |
| `logger/.../core/PlanePass.java` (new), `PlanePassTest.java` (new), `CoverageTest.java`, `CoverageWorkerTest.java` | port and tests |
| `logger/app/build.gradle`, `logger/README.md`, `docs/howto/ice-cube.md`, `docs/log.md` | version and docs |

## Tests and acceptance (PR B)

JVM parity with every plane vector (steps 6), the folded and sequence vectors, gate unit tests (step 4),
unchanged pass-1 vectors, and the 94 Python tests. Replay acceptance (private, counts only) for PR B: a
logger session replayed through the Java rebuild gives the same planar counts as the batch run on the same
captures, unfolded nodes only (folded nodes may differ by the fold, count reported).

## Prior Art

#24's plan (plane to the node's own members, rejected by its review) and the issue's table. CUBE uses
neighbours only for its queue and median filters. A plane over neighbours is ordinary local trend removal
in gridding; nothing new is imported.

## Serves

Roadmap line "`icecube` runs on logger sessions in the project's local frame" and the estimator tracking
the rink through the season; the project keeps no goals or design document, so none.

## Design Picture

No change to the design picture (the project has no design document). The logger's re-reduction cost
grows from 9 to about 25 nodes (up to 30) per capture; noted in `logger/README.md` in PR B.

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
| entry and aggregate format | parity vectors both sides, `logger_vectors.py` | Yes (B: folded vectors, Python first) |
| rebuild outputs | `report.py`, `docs/howto/ice-cube.md`, `logger/README.md` | Yes |
| ring 9 to about 25 nodes | logger timing on the phone | Yes, PR B |

## Estimated Scope

Two PRs: A delivered (#34); B (this plan): reference change in `nodes.py`, vectors, Java port and ring, docs.
