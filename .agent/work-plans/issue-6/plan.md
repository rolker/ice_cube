# Plan: Logger 0.7.0: live coverage overlay (#6) and Import… in MENU > Layers (#8)

## Issues

- https://github.com/rolker/ice_cube/issues/6 (coverage overlay)
- https://github.com/rolker/ice_cube/issues/8 (import layer files from the Layers menu)

One PR, logger 0.7.0, closes both. #8 is its own section and its own commit.

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

Layer files are imported only from Settings > Map > Import layer files…
(`SettingsActivity.pickLayers` / `onActivityResult` / `importOne`), while MENU > Layers…
(`MainActivity.pickLayers`) only switches them on and off (#8).

## Owner decisions (settled 2026-11-15)

1. Sigma target: a setting, default 3 cm.
2. Capture radius and reach: settings, defaults 0.75 m and 2.0 m.
3. One disagreement (flags > 0) turns a node red.
4. Float and `fix_suspect` captures are left out.
5. Per-capture sigmas: port the `analysis/qgis/soundings.py` per-sounding budget
   (`sigma_terms`), not a fixed floor. Ground term is a new logger setting.
6. A 1.5 cm pass-wander term goes into the node sigma before the target test; it is a
   setting (Settings > Coverage, default 0.015 m), not a constant.
7. This session only; earlier sessions are not loaded.
8. The red fringe at the outer edge of the walked area is acceptable for now.
9. Single captures: pole tilt 1 deg (`tilt_bubble_deg`, as `soundings.py` does).
10. Tilt captures: the rule below (tilt solution's `tilt_sd_*` in place of the pole tilt
    term, tilt 0, tip sink and ground kept). `soundings.py` does not get the rule in this
    PR (follow-up #9); the README notes the temporary difference.
11. Review findings applied: the clear-radius example is about 0.28 m; the Layers dialog
    reopens only after the import report is dismissed; the icecube ->
    `analysis/qgis/soundings.py` import is documented in the generator's docstring and
    in `.agents/README.md`'s test notes.

## Approach: coverage overlay (#6)

1. **`core/Coverage.java` (pure Java, no Android).** Sparse grid `HashMap<Long, Node>`,
   node (i, j) at (i*cell, j*cell) in the map frame (anchored at the origin, so a node
   keeps its place as the area grows). `Node` holds W, WZ, n, flags, maxNr and an
   `inArea` mark. `add(x, y, z, sz, sh)` ports the `grid_surface` inner loop with one
   pass and sb = 0: v = sz^2 + (slope*(d + sh))^2 for each node within
   `captureRadius`; the inconsistency test (|z - z_node| > k*sqrt(1/W + v)) runs before
   fusion; then W += 1/v, WZ += z/v. Constants mirrored from `icecube.example.toml`:
   slope 0.05, k_sigma 3.0.
2. **Status.** Node sigma for display = sqrt(1/W + w^2) with w = `PASS_WANDER_M` =
   the pass-wander setting, default 0.015 m (decision 6; the same value as
   `soundings.py`'s logger-source `prior_m` default, the expected RTK wander per pass,
   which the live view cannot solve).
   `status(node)`: NO_DATA (n = 0) or DISAGREE (flags > 0) red; THIN (sigma > target)
   amber; OK clear. The wander term is applied only in `status`, not inside the fusion,
   so the parity vectors stay a pure `nodes.py` comparison.
   Effect at the defaults: a node is clear when 1/W <= (3^2 - 1.5^2) cm^2, i.e. node
   sigma before wander <= 2.6 cm. One lawn shot (sigma_z about 2.1 cm, sigma_xy about
   3.3 cm) clears nodes within about 0.28 m of it; a node 0.5 m from the only shot is
   amber.
3. **Per-capture sigma: port of `soundings.sigma_terms` (`core/SoundingSigma.java`, pure
   Java).** Same terms and constants as `soundings.DEFAULTS["sigma"]` and `settle_s`:

   ```
   rtk    = max(floor 0.01, sd_u)                 (no GST input: see below)
   age    = age_rate 0.001 * max(0, age_s - age_free_s 2.0)
   settle = settle_extra 0.08 * (1 - fix_age_s / settle_s 90)^2  if fix_age_s < 90
   pole   = pole_m * (1 - cos(tilt)) + tip_sink 0.01
   ground = Settings coverage ground sigma (default 0.015 m)
   sigma_z  = sqrt(rtk^2 + age^2 + settle^2 + pole^2 + ground^2)
   h        = max(floor_h 0.01, hypot(sd_e, sd_n) / sqrt 2)
   sigma_xy = hypot(h, pole_m * sin(tilt))
   ```

   Inputs from the in-memory `PointRow` (the points.csv columns `sd_u_m`, `sd_e_m`,
   `sd_n_m`, `age_s`, `fix_age_s`, `pole_m`, `method`, `tilt_sd_*_m`). A missing value
   (NaN) is treated as `soundings.py` treats None: sd 0 (so the floor holds), age 0,
   no settle term when `fix_age_s` is missing (rows from before 0.6).
   - **No GST on the phone.** `soundings.py` also takes the GST height sigma from the
     receiver log (`gst_u`, `gst_h`); the logger does not parse GST, so that input is
     skipped. The logger's sigma can therefore be lower than `soundings.py`'s for the
     same capture when GST exceeds the 1 s scatter; the README says so.
   - **Tilt angle for single captures.** Checked in `soundings.py`: logger captures use
     `tilt_bubble_deg` = 1.0 deg (the pole is on a bubble); `tilt_unknown_deg` = 5.0 is
     used only for pole stops found in the log without an IMU plumb check (which are
     flagged `plumb_unverified` and left out). The port follows `soundings.py`: 1.0 deg
     for single captures (pole term 0.3 mm at 1.8 m; 5 deg would give 6.8 mm)
     (decision 9).
   - **Tilt captures.** Checked: `soundings.py` has no tilt-capture branch (it reads the
     gpkg `captures` table without the `tilt_*` columns and gives every capture the
     bubble pole term). Proposed for the logger, expressed through the same function so
     the reference stays `sigma_terms`: sd_u = `tilt_sd_u_m`, sd_h = hypot(`tilt_sd_e_m`,
     `tilt_sd_n_m`)/sqrt 2, tilt 0 (the solution already places the tip, so no plumb
     term; tip sink and ground stay), age and fix age from the row (decision 10;
     `soundings.py` follows in #9).
   - **Fix age source.** The logger's `fix_age_s` is its own count from the GGA stream;
     `soundings.py` recomputes it from gps.ubx. Small differences are expected and
     noted in the README; the formula is the same.
4. **Area rule.** Each sounding also marks every node within `reach` of it as in-area
   (created with n = 0 if new). A node is drawn only when in-area; in-area with no
   sounding within `captureRadius` is NO_DATA (red). Defaults 0.75 m and 2.0 m, so a gap
   between passes wider than 1.5 m shows a red strip. The outer edge of the walked area
   has a red fringe (reach - radius = 1.25 m wide), read as "the survey ends here"
   (accepted, decision 8).
5. **Which captures feed it.** Rows with class `surface`, fix 4 and not `fix_suspect`,
   method single or tilt (decision 4). z = h_ground in the map frame (via
   `LocalFrame.toEnu`), sz and sh from `SoundingSigma`.
6. **Updates.** `MainActivity.refresh()` keeps a `Coverage` and feeds rows with n above
   the last one fed (per capture, no extra service work). It rebuilds from `rows()` when
   the map frame, the session folder, or a coverage setting (including the ground sigma)
   changes. Activity recreation rebuilds from `rows()`; a new session starts empty.
   Earlier sessions are not loaded (decision 7).
7. **Bound.** `MAX_NODES` = 250,000 (6 ha at 0.5 m; about 25 MB worst case). Past it,
   new nodes are not created, existing ones still update, and the legend reads
   `coverage: area limit`. Per capture the work is (2*ceil(reach/cell)+1)^2 = 81 node
   lookups at the defaults; a rebuild of 2000 captures is about 160k, on the UI thread.
8. **Drawing (`MapView`).** One ARGB `Bitmap`, one pixel per node over the in-area bbox
   (side capped at 2048 nodes, regrown by copy), repainted only at nodes a capture
   touched, drawn under the trail and captures with filtering off and the map's
   transform. Red 0x66 alpha, amber 0x55 alpha, clear transparent, so layers show
   through. Two legend rows (`no data / disagree`, `σ > target`).
9. **Toggle.** MENU > `Coverage` checkbox item (on by default, remembered in prefs,
   `Settings.COVERAGE_ON`), applied at once.
10. **Settings > Coverage** (new header after Survey), as `Limit` text fields:
    sigma target cm (0.5 to 50, default 3.0), grid spacing m (0.1 to 5, default 0.5),
    capture radius m (0.1 to 10, default 0.75), reach m (0.1 to 20, default 2.0), ground
    sigma cm (0 to 10, default 1.5; help text: lawn 1.5 cm, about 0 on ice or a hard
    surface), pass wander cm (0 to 10, default 1.5). Reach below the capture radius is accepted and simply hides gaps (the
    README says so).
11. **Parity vectors.** `icecube/logger_vectors.py` writes
    `logger/app/src/test/resources/coverage_vectors.json` (`.json` is not gitignored),
    regenerated with `python3 -m icecube.logger_vectors --out <path>`. Two parts, all
    synthetic, plain local metres:
    - **`sigma` cases**: rows of (method, sd_u, sd_e, sd_n, age_s, fix_age_s, pole_m,
      tilt_sd_e/n/u, ground) run through `soundings.sigma_terms` (imported from
      `analysis/qgis/soundings.py` via `sys.path`, standard library only) with
      `soundings.DEFAULTS`; expected sigma_z, sigma_xy and each term. Cases: floor only;
      sd_u above the floor; correction age below and above `age_free_s`; fix age 0, 45 s
      and past `settle_s`; missing fix age; a long pole; ground 0 and 0.015; a tilt
      capture (tilt 0, tilt_sd_*); missing sd values.
    - **`nodes` cases**: soundings with their sigmas run through `nodes.grid_surface` with
      one pass, sb = 0, x0 = y0 = multiples of cell: one sounding; two consistent
      overlapping strips; a 20 cm step that flags; soundings at varied distances for the
      slope term; soundings whose sz/sh come from the `sigma` budget (settle-inflated and
      plain) so the two parts chain. Expected per node i, j, z, sigma, n, flags.
    The generator imports `analysis/qgis/soundings.py` through `sys.path` (a new
    icecube -> analysis dependency, standard library only); its docstring and
    `.agents/README.md`'s test notes say so.
    The generator pins the budget constants it reads from `soundings.DEFAULTS` into the
    JSON, and `SoundingSigmaTest` checks the Java constants against them, so a change in
    `soundings.py`'s defaults fails the Java tests until both sides agree.
12. **Docs and version.** Logger 0.6.1 -> 0.7.0 (`versionCode` 9): README title,
    Settings list, MENU, map legend, a new "Coverage" section (rule, colours, what feeds
    it, the per-capture sigma budget and its source, no GST, the tilt-capture rule, the
    pass-wander term, that it ignores pass bias so it is still optimistic against a full
    `icecube` run, that node positions match a run only when its extent is a multiple
    of the cell). `docs/howto/ice-cube.md` node-estimator section, the `nodes.py`
    docstring and the `soundings.py` `sigma_terms` docstring: one line each saying the
    logger mirrors it and the vectors must be regenerated when it changes.

## Approach: Import… in MENU > Layers (#8)

Own commit, before the version bump commit.

1. **`LayerImport.java` (Android, package `rink.icecube.logger`).** Move the picker
   intent, `importOne`, `displayName` and the background-thread report out of
   `SettingsActivity` unchanged: `pickerIntent(Context)` and
   `run(Activity, Intent data, Runnable after)` (copies to `Layers.importDir`, same
   parse check, same name sanitising and same-name replacement, same report lines and
   "Import layers" dialog, then `Layers.reload()` and `after`). Same checks, same errors.
2. **SettingsActivity** keeps its Import layer files… button and calls `LayerImport`
   (behaviour unchanged).
3. **MainActivity.** The Layers dialog gets a neutral button **Import…** (the bottom of
   the dialog); the "No layer files" dialog gets the same button instead of only the
   pointer to Settings. A new request code `REQ_IMPORT_LAYERS` in `onActivityResult`
   hands the result to `LayerImport.run`, whose `after` reloads `files`, calls
   `applyLayers()` and reopens the Layers dialog so the new layers are there at once;
   `after` runs when the import report is dismissed, so the dialogs do not stack.
4. **Nice to have, included because cheap.** The picker opens where the last import came
   from: the last picked document URI is kept in prefs (`Settings.LAST_LAYER_IMPORT_URI`)
   and passed as `DocumentsContract.EXTRA_INITIAL_URI` (API 26, our minSdk); pickers
   that ignore it simply open at their default. The per-file summary and errors show in
   the same report dialog from either entry point, so the Layers menu gets them too.
5. **Docs.** README MENU line (Layers…: checkboxes and Import…) and the Map section.

## Commits

1. Plan (this file).
2. `SoundingSigma` + vectors generator (sigma part) + tests (#6).
3. `Coverage` + vectors (nodes part) + tests (#6).
4. Settings, MainActivity feed/toggle, MapView overlay (#6).
5. `LayerImport` and Import… in MENU > Layers (#8).
6. Docs and version 0.7.0 (#6, #8).

## Files to Change

| File | Change |
|------|--------|
| `logger/app/src/main/java/rink/icecube/logger/core/Coverage.java` | New: estimator, area rule, status with pass wander |
| `logger/app/src/main/java/rink/icecube/logger/core/SoundingSigma.java` | New: port of `soundings.sigma_terms` |
| `logger/app/src/main/java/rink/icecube/logger/core/Limit.java` | Six coverage limits |
| `logger/app/src/main/java/rink/icecube/logger/Settings.java` | Keys, getters, COVERAGE_ON, LAST_LAYER_IMPORT_URI |
| `logger/app/src/main/java/rink/icecube/logger/SettingsActivity.java` | Coverage header and fields; import via LayerImport |
| `logger/app/src/main/java/rink/icecube/logger/LayerImport.java` | New: shared picker and import flow (#8) |
| `logger/app/src/main/java/rink/icecube/logger/MainActivity.java` | Own the Coverage, feed/rebuild in refresh, MENU toggle; Import… in Layers (#8) |
| `logger/app/src/main/java/rink/icecube/logger/MapView.java` | Bitmap overlay, legend rows |
| `logger/app/build.gradle` | 0.7.0, versionCode 9 |
| `logger/app/src/test/java/rink/icecube/logger/core/CoverageTest.java` | New tests |
| `logger/app/src/test/java/rink/icecube/logger/core/SoundingSigmaTest.java` | New tests |
| `logger/app/src/test/resources/coverage_vectors.json` | New, generated |
| `icecube/logger_vectors.py` | New generator |
| `icecube/tests/test_logger_vectors.py` | New: committed file matches regeneration |
| `logger/README.md`, `docs/howto/ice-cube.md`, `icecube/nodes.py`, `analysis/qgis/soundings.py` (docstrings) | Docs |

## Prior Art

`icecube/nodes.py` (ported, one-pass subset); `analysis/qgis/soundings.py`
`sigma_terms` (ported, without GST); CUBE (Calder & Mayer 2003) and the coverage
displays of hydrographic acquisition packages, which colour cells by node uncertainty
against a target, the same idea. Existing logger patterns reused: `Limit` settings,
`core/` pure-Java classes with JVM tests, org.json already a test dependency, the
existing Settings import flow (moved, not rewritten). Rejected: running it in the service
(the frame lives in the activity), a dense array grid (grows badly with walked area), a
fixed 2 cm sz floor (superseded by decision 5).

## Serves

Roadmap goal (season's rink surveyed with the logger), lines "The footprint ... ground
under it surveyed" and "Ice growth ... reported"; the project keeps no design document.

## Design Picture

No design document in this project; `logger/README.md` and `docs/howto/ice-cube.md`
describe the parts and are updated in this PR.

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Only what's needed | One-pass subset only; no pass bias, no hypotheses, no cross-session store; #8 moves existing code rather than adding a second import path |
| Test what breaks | Parity vectors for both the per-capture budget and the node update catch drift on either side; area/status tests catch the rule |
| A change includes its consequences | README, how-to, nodes.py and soundings.py docstrings, version in the same PR |
| Public repo rule | Synthetic local-metre vectors only; no site values |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0013 progress vocabulary | Yes | Plan Authored / Plan Review entries via review_progress.sh |
| Others | No | Project code only |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `nodes.py` update rule | `coverage_vectors.json`, `Coverage.java` | Yes (test fails until regenerated) |
| `soundings.py` `sigma_terms` or its defaults | `coverage_vectors.json`, `SoundingSigma.java` | Yes (Python test fails until regenerated; Java constant check fails until ported) |
| Logger settings | README Settings list | Yes |
| Logger version | README title, build.gradle | Yes |
| Settings import flow | MENU > Layers import (shared `LayerImport`) | Yes |

## Tests

- `SoundingSigmaTest` (JVM): every `sigma` vector case, each term, sigma_z and sigma_xy
  to 1e-12; Java constants equal the pinned `soundings.DEFAULTS` values.
- `CoverageTest` (JVM, `./gradlew test`): every `nodes` vector case, z and sigma to
  1e-9, n and flags exact, nodes absent from the vector have n = 0; NO_DATA in-area node
  between two strips 3 m apart; no node beyond reach; status thresholds at target with
  the default 1.5 cm wander (a node at 2.59 cm clear, 2.61 cm amber); incremental feed equals a
  rebuild; node cap stops growth without breaking updates; float and `fix_suspect` rows
  not fed.
- `icecube/tests/test_logger_vectors.py` (`python3 -m unittest discover icecube/tests`):
  regenerating in memory equals the committed JSON.
- #8: `LayerImport` is Android-bound (content resolver), so no JVM test; the existing
  `GeoJson`/`Layers` tests still cover parsing and names. Manual: import from MENU >
  Layers, the new layer is listed and drawn at once; import a non-GeoJSON file, the
  report says why; import from Settings > Map still works.
- Manual on the phone: walk two passes with a gap, see red then amber then clear; a
  capture right after a fix is amber (settle term).

## Open Questions

None; settled by the owner (decisions 9 to 11).

## Estimated Scope

Single PR, about 1100 lines (Java ~600 with tests, Python ~200, docs ~200, #8 ~100 of
it mostly moved).
