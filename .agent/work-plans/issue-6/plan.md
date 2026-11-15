# Plan: Logger: live coverage overlay from an incremental CUBE node estimator

## Issue

https://github.com/rolker/ice_cube/issues/6

## Context

`icecube/nodes.py` fuses soundings into grid nodes after the field work. The logger
(Java, 0.6.1) shows this session's captures on a local ENU map (`MapView`, frame from
`MapOrigin`/`LocalFrame` in `MainActivity`) but says nothing about where the surface is
still thin. Settled with the owner: a light port of the node estimator runs in the logger,
fed by each Surface capture, and draws red / amber / clear cells on the map. Sigma target
and grid spacing (default 0.5 m) are logger settings. No crossover adjustment, no object
or ref captures. `icecube` writes test vectors and the Java tests check against them.

The logger has no session resume: every start opens a new folder, and `Survey` holds the
session's rows in memory (`Survey.rows()`), which `MainActivity.refresh()` passes to the
map on every tick. So "per session" needs no persistence of its own.

## Approach

1. **`core/Coverage.java` (pure Java, no Android).** Sparse grid `HashMap<Long, Node>`,
   node (i, j) at (i*cell, j*cell) in the map frame (anchored at the origin, so a node
   keeps its place as the area grows). `Node` holds W, WZ, n, flags, maxNr and an
   `inArea` mark. `add(x, y, z, sz, sh)` ports the `grid_surface` inner loop with one
   pass and sb = 0: v = sz^2 + (slope*(d + sh))^2 for each node within
   `captureRadius`; the inconsistency test (|z - z_node| > k*sqrt(1/W + v)) runs before
   fusion; then W += 1/v, WZ += z/v. Constants mirrored from `icecube.example.toml`:
   slope 0.05, k_sigma 3.0. `status(node)`: NO_DATA (n = 0) or DISAGREE (flags > 0) red,
   THIN (sigma > target) amber, OK clear. Sigma = sqrt(1/W).
2. **Area rule.** Each sounding also marks every node within `reach` of it as in-area
   (created with n = 0 if new). A node is drawn only when in-area; in-area with no
   sounding within `captureRadius` is NO_DATA (red). Proposed defaults: capture radius
   0.75 m (icecube's), reach 2.0 m, so a gap between passes wider than 1.5 m shows a red
   strip, and thin coverage at 0.75 m from the nearest sounding shows amber (sigma about
   4 cm there from the slope term). Side effect: the outer edge of the walked area always
   has a red fringe (reach - radius = 1.25 m wide), read as "the survey ends here".
3. **Which captures feed it.** Rows with class `surface`, fix 4 and not `fix_suspect`,
   single or tilt method. z = h_ground in the map frame (via `LocalFrame.toEnu`).
   Sigmas from the row as the logger has them, with floors: sz = max(0.02, sd_u)
   (tilt: tilt_sd_u_m), sh = max(0.01, hypot(sd_e, sd_n)/sqrt 2) (tilt: tilt_sd_e/n).
   The 0.02 m floor stands in for `soundings.py`'s rtk floor + ground + tip-sink terms
   (sqrt(1^2 + 1.5^2 + 1^2) cm); without it a 1 s average's mm scatter makes one shot
   "clear". Constants in `Coverage`, named and commented.
4. **Updates.** `MainActivity.refresh()` keeps a `Coverage` and feeds rows with n above
   the last one fed (per capture, no extra service work). It rebuilds from `rows()` when
   the map frame, the session folder, or a coverage setting changes. Activity
   recreation rebuilds from `rows()`; a new session starts empty. Earlier sessions are
   not loaded (open question).
5. **Bound.** `MAX_NODES` = 250,000 (6 ha at 0.5 m; about 25 MB worst case). Past it,
   new nodes are not created, existing ones still update, and the legend reads
   `coverage: area limit`. Per capture the work is (2*ceil(reach/cell)+1)^2 = 81 node
   lookups at the defaults; a rebuild of 2000 captures is about 160k, on the UI thread.
6. **Drawing (`MapView`).** One ARGB `Bitmap`, one pixel per node over the in-area bbox
   (side capped at 2048 nodes, regrown by copy), repainted only at nodes a capture
   touched, drawn under the trail and captures with filtering off and the map's
   transform. Red 0x66 alpha, amber 0x55 alpha, clear transparent, so layers show
   through. Two legend rows (`no data / disagree`, `σ > target`).
7. **Toggle.** MENU > `Coverage` checkbox item (on by default, remembered in prefs,
   `Settings.COVERAGE_ON`), applied at once.
8. **Settings > Coverage** (new header after Survey), as `Limit` text fields:
   sigma target cm (0.5 to 50, default 3.0), grid spacing m (0.1 to 5, default 0.5),
   capture radius m (0.1 to 10, default 0.75), reach m (0.1 to 20, default 2.0). Reach
   below the capture radius is accepted and simply hides gaps (the README says so).
9. **Parity vectors.** `icecube/logger_vectors.py`: a few synthetic cases in plain local
   metres (one sounding; two consistent overlapping strips; a 20 cm step that flags;
   soundings at varied distances for the slope term; a sigma-floor case), each run
   through `nodes.grid_surface` with one pass, sb = 0, x0 = y0 = multiples of cell,
   written as JSON (soundings in order; per node i, j, z, sigma, n, flags) to
   `logger/app/src/test/resources/coverage_vectors.json` (`.json` is not gitignored).
   `python3 -m icecube.logger_vectors --out <path>` regenerates it.
10. **Docs and version.** Logger 0.6.1 -> 0.7.0 (`versionCode` 9): README title,
    Settings list, MENU, map legend, a new "Coverage" section (rule, colours, what feeds
    it, sigma floors, that it ignores pass bias so it is optimistic against a full
    `icecube` run, that node positions match a run only when its extent is a multiple
    of the cell). `docs/howto/ice-cube.md` node-estimator section and the `nodes.py`
    docstring: one line each saying the logger mirrors the one-pass update and the
    vectors must be regenerated when it changes.

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/Coverage.java` | New: estimator, area rule, status |
| `logger/app/src/main/java/rink/icecube/logger/core/Limit.java` | Four coverage limits |
| `logger/app/src/main/java/rink/icecube/logger/Settings.java` | Keys, getters, COVERAGE_ON |
| `logger/app/src/main/java/rink/icecube/logger/SettingsActivity.java` | Coverage header and fields |
| `logger/app/src/main/java/rink/icecube/logger/MainActivity.java` | Own the Coverage, feed/rebuild in refresh, MENU toggle |
| `logger/app/src/main/java/rink/icecube/logger/MapView.java` | Bitmap overlay, legend rows |
| `logger/app/build.gradle` | 0.7.0, versionCode 9 |
| `logger/app/src/test/java/rink/icecube/logger/core/CoverageTest.java` | New tests |
| `logger/app/src/test/resources/coverage_vectors.json` | New, generated |
| `icecube/logger_vectors.py` | New generator |
| `icecube/tests/test_logger_vectors.py` | New: committed file matches regeneration |
| `logger/README.md`, `docs/howto/ice-cube.md`, `icecube/nodes.py` (docstring) | Docs |

## Prior Art

`icecube/nodes.py` (ported, one-pass subset); `analysis/qgis/soundings.py` sigma budget
(floors taken as constants, not ported); CUBE (Calder & Mayer 2003) and the coverage
displays of hydrographic acquisition packages, which colour cells by node uncertainty
against a target, the same idea. Existing logger patterns reused: `Limit` settings,
`core/` pure-Java classes with JVM tests, org.json already a test dependency. Rejected:
running it in the service (the frame lives in the activity) and a dense array grid
(grows badly with walked area).

## Serves

Roadmap goal (season's rink surveyed with the logger), lines "The footprint ... ground
under it surveyed" and "Ice growth ... reported"; the project keeps no design document.

## Design Picture

No design document in this project; `logger/README.md` and `docs/howto/ice-cube.md`
describe the parts and are updated in this PR.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Only what's needed | One-pass subset only; no pass bias, no hypotheses, no cross-session store |
| Test what breaks | Parity vectors catch drift on both sides; area/status tests catch the rule |
| A change includes its consequences | README, how-to, nodes.py docstring, version in the same PR |
| Public repo rule | Synthetic local-metre vectors only; no site values |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0013 progress vocabulary | Yes | Plan Authored entry via review_progress.sh |
| Others | No | Project code only |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `nodes.py` update rule | `coverage_vectors.json`, `Coverage.java` | Yes (test fails until regenerated) |
| Logger settings | README Settings list | Yes |
| Logger version | README title, build.gradle | Yes |

## Tests

- `CoverageTest` (JVM, `./gradlew test`): every vector case, z and sigma to 1e-9, n and
  flags exact, nodes absent from the vector have n = 0; NO_DATA in-area node between two
  strips 3 m apart; no node beyond reach; status thresholds at target; incremental feed
  equals a rebuild; node cap stops growth without breaking updates.
- `icecube/tests/test_logger_vectors.py` (`python3 -m unittest discover icecube/tests`):
  regenerating in memory equals the committed JSON.
- Manual on the phone: walk two passes with a gap, see red then amber then clear.

## Open Questions

- Sigma target default: 3 cm proposed.
- Expose capture radius and reach as settings (proposed), or fix them at 0.75 / 2.0 m?
- Red if `flags > 0` (one 3-sigma disagreement), or require 2?
- Float and fix-suspect captures: left out (proposed), or fed with their own sigmas?
- The 0.02 m sz floor in place of the soundings.py budget: acceptable?
- Optimistic sigma (no pass bias): add a fixed pass-wander term (e.g. 1.5 cm) in
  quadrature before comparing with the target?
- Load earlier sessions' points.csv (e.g. same day) into the overlay, or this session only?
- The red outer fringe: acceptable, or limit the area to inside a layer polygon later?

## Estimated Scope

Single PR, about 800 lines (Java ~450 with tests, Python ~150, docs ~150).
