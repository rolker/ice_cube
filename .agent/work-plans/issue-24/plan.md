# Plan: Restore CUBE's robustness in icecube and the logger overlay

## Issue

https://github.com/rolker/ice_cube/issues/24

## Context

`icecube/nodes.py` (`grid_surface`) and its live port `logger/.../core/Coverage.java`
(0.7.0, PR #7) fuse every sounding into one running weighted mean per node. A k-sigma test
before fusion only increments `flags`; the sounding is fused anyway, and the logger draws a
node red while `flags > 0`, for the rest of the session. Two field replays (private, relative
numbers; summaries on #24):

| session | red by disagreement | later fed, still red | 0.7.0 rule | R2 ratio | R3 robust median | R4 median-first rebuild |
|---|---|---|---|---|---|---|
| 11-21 | 58 | 44 (18 with >= 3 agreeing) | 58 | 29 | 28 | not run |
| 11-26 | 53 | 40 (21 with >= 3 agreeing) | 53 | 10 | 14 | **6** (5 with a 90 s feed rule) |

Causes on 11-26: 32 bad new capture (16 in the first ~105 s after a re-fix, 16 in wander
spells inside a long fix), 3 bad earlier estimate, 14 relief on a steep bank, 4 unexplained
(also inside wander spells). On 11-21, 25 of 58 were a bad *earlier* estimate: arrival order
matters today. Everything left red under R2-R4 on 11-26 is steep-bank relief.

## Approach

The rule is the owner's proposal on #24 (R4 in the replay), plus CUBE's hypothesis count,
strength ratio and Eeg test, and a per-node plane for relief. One shared definition, two
ports (Python reference, Java live), held together by test vectors.

### A. The node rebuild (both ports, identical)

1. **Per-node store.** Each fed node keeps its contributions sorted by z: `z`, `v`
   (propagated variance as today, `sz^2 + (slope*(d + sh))^2`), the offset `(dx, dy)` of the
   sounding from the node, and the capture id. Python also keeps pass id and time.
2. **Rebuild on every new contribution** (no queue, no lag): weighted median `zm` of the
   store; feed the contributions into the sequential update in order of `|z - zm|`, ties by
   capture id (deterministic in both languages). The first one starts the main hypothesis.
   Each next one is tested as today (`|z - zhat| > k*sqrt(1/W + v)`, plus the pass-bias terms
   in Python); a pass fuses it, a failure sends it to the closest side hypothesis that passes
   the same test, or opens a new one (CUBE's intervention, without its Bayes-factor
   machinery).
3. **Disambiguation by count** (CUBE): if a side hypothesis ends with more members than the
   main one, it becomes the main. Median-first makes this rare; it handles a true 50/50 split.
4. **Outputs per node:** `z`, `sigma` (main hypothesis only: outliers no longer pull the
   height), `n` (all), `n_in`, `n_out` (all side members), `n_alt` and `z_alt` (largest side
   hypothesis), `max_nr` (worst normalised residual at the last rebuild), and CUBE's strength
   ratio `max(0, 5 - n_in / n_out)` (0 = unambiguous). The surface no longer depends on
   arrival order (below the cap).
5. **Cap and budget (logger).** Up to 32 entries per node. Past the cap the oldest inlier is
   folded into a per-node aggregate (W, WZ, count) that enters the rebuild as one weighted
   pseudo-sounding; outliers past the cap keep only their count. A global entry budget
   (1 M entries, about 24 MB) folds further nodes the same way, so memory stays bounded
   under `MAX_NODES`. Python batch is uncapped by default; `logger_vectors` passes the cap.

### B. Status and colour (logger)

6. **Red only while the outliers are competitive:** DISAGREE while `n_out > 0` and
   `n_in < 2 * n_out` (strength ratio > 3). Otherwise amber/clear from the main
   hypothesis's sigma as today. Agreeing captures now clear a node; a single bad capture
   among three good ones does not turn it red. `flags` and the sticky counter go.

### C. Relief (both ports)

7. **Local plane per node, gated.** After a first rebuild, fit a weighted plane
   `z = a + g . (dx, dy)` to the main hypothesis's members, with a ridge prior on `g`
   (sigma 0.3 m/m, so a one-line traverse fits only its along-track slope). Only when
   `|g|` exceeds the slope allowance by a clear margin (`|g| > 2*slope` and `> 2 sigma_g`)
   and the node has >= 3 members: reduce every entry to the node (`z - g . (dx, dy)`,
   variance plus `(dx,dy)' C_g (dx,dy)`) and rebuild once more. Flat ground never enters
   this path, so the R4 results there are unchanged. The global slope stays 0.05;
   `slope_from_dem` in the batch config keeps working as the per-node prior allowance.

### D. Batch extras (Python)

8. **Eeg's leave-one-out test** at finalisation (CUBE `cube_node_truncate`, quotient limit
   30) on each node's main hypothesis when it has >= 4 members; config
   `[grid] eeg_quotient_limit`, 0 = off. Not in the logger (the live rule already isolates
   outliers; Eeg's test is for nodes with very little data).
9. **Config switch** `[grid] robust = true` (false reproduces the 0.7-era running mean and
   flag counter, for comparing old runs). `flags` in `surface_*.npz` and the report tables is
   replaced by `n_out`, `n_alt`, `strength`; the flag table becomes an outlier table.

### E. Voided captures (#27 hook) and replay support

10. Entries carry the capture id. `Coverage.feeds()` takes a void set and `CoverageWorker`
    rebuilds when it changes (a full rebuild is milliseconds at session sizes, and it also
    removes the voided capture's area marks). The void flag itself and its UI are #27.
11. Log the Coverage settings and the map-origin source to `events.csv` when they take
    effect, so a session can be replayed exactly (both replays had to assume defaults).
12. Draw the 1.25 m fringe (in reach, beyond the capture radius, no data) as a thin
    neutral outline instead of red; red no-data stays for real gaps.

### F. Tests

- **Parity vectors** (`python3 -m icecube.logger_vectors`): new expected fields (`n_in`,
  `n_out`, `n_alt`, `z_alt`, `strength`, status) and cases: early bad capture then good
  ones (cause b), late outlier then three agreeing (must clear), 50/50 split, steep bank
  (plane path on), flat ground (plane path off), one-line traverse on a slope, over-cap
  node (40 contributions), exact ties. A *sequence* part records the status after each
  capture, so the Java test checks clearing live, not just the end state.
- **Python unit tests** (`icecube/tests`): permutation invariance below the cap, the
  multi-pass bias terms in the reordered test, Eeg on a planted outlier, `robust = false`
  reproduces the old outputs.
- **Java** (`CoverageTest`): vectors, cap and budget folding, void rebuild, timing bound
  (1000 captures into one 3 m patch under 1 s on the JVM).
- **Private replays, never committed.** The replay scripts in the ignored `private/`
  analysis folders already read the repo through `ICE_CUBE_REPO`; add a rule that calls the
  new `icecube.nodes` rebuild and run them locally with
  `ICE_CUBE_REPO=<worktree> python3 -I replay.py` (11-21) and `... coverage26.py` (11-26).
  Acceptance, as counts in the PR: 11-26 red <= 6 and none in the rink footprint, bank
  nodes cleared by the plane step; 11-21 red at or below R3's 28 with no flat-ground "new"
  red; node heights moved toward the good captures for the cause-b nodes.

## Files to Change

| File | Change |
|------|--------|
| `icecube/nodes.py` | per-node store and rebuild, hypotheses, plane, Eeg, new Surface fields; docstring |
| `icecube/report.py`, `icecube/cli.py`, `icecube/topo.py` | outlier table and npz fields in place of `flags` |
| `icecube/logger_vectors.py`, `logger/app/src/test/resources/coverage_vectors.json` | new cases, fields, sequence part; regenerated |
| `icecube/tests/test_icecube.py`, `test_logger_vectors.py` | tests above |
| `icecube.example.toml` | `robust`, `eeg_quotient_limit` |
| `logger/.../core/Coverage.java` | store, rebuild, cap/budget, plane, status, void set |
| `logger/.../core/CoverageWorker.java`, `CoverageRaster.java`, `MapView.java` | void rebuild, fringe colour, legend |
| `logger/.../MainActivity.java` | settings and origin to `events.csv` |
| `logger/.../core/CoverageTest.java` (+ worker/raster tests) | tests above |
| `logger/app/build.gradle`, `logger/README.md` | 0.8.0; Coverage section rewritten |
| `docs/howto/ice-cube.md` | "Where it comes from": median ordering, side hypotheses, strength, Eeg listed |

## Prior Art

CUBE (Calder's C in rolker/cube_bathymetry `original_cube/libsrc/cube/cube_node.c`): taken
are the sorted queue's purpose (robust start), the intervention-opens-a-hypothesis idea,
disambiguation by count, the strength ratio `max(0, 5 - n1/n2)` and Eeg's quotient test.
Not taken: the median queue's lag (replaced by the rebuild), West and Harrison's cumulative
Bayes factor (the k-sigma test is calibrated: no node was flagged by two good captures on
smooth ground on 11-21), IHO inflation. The replays' R3 (robust median, MAD-style split) was
the alternative; R4 did better on 11-26 and keeps the sequential update.

## Serves

Roadmap line "The footprint is chosen and the ground under it surveyed before building"
(the overlay is the field check) and "Ice growth ... is reported" (batch node heights).
No design document yet (`docs/design.md` absent; #19).

## Design Picture

No design document. The estimator's description in `docs/howto/ice-cube.md` changes in
this PR (section 1 table and the node update/test description).

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Public repo | vectors and tests synthetic; replays stay in `private/`; PR shows counts only |
| One definition, two ports | Python is the reference; Java checked by regenerated vectors |
| Settings over constants | outvote factor 2 and the cap are constants (see open questions) |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| node estimate (inliers only) | batch outputs, report tables, topo npz | Yes |
| `flags` semantics | `report.py`, `topo.py`, `cli.py`; `analysis/qgis/topo_to_gpkg.py` loads `flags_*.asc` (keep the raster name, now outlier count, or rename both) | Yes |
| Coverage status | logger README, legend | Yes |
| capture void flag | #27 UI and `points.csv` columns | No, #27 |
| fix-age feed rule | #15 (11-26: ~120 s to settle) | No, #15 |

## Open Questions (for Roland)

1. Colour rule: count **all** side members against the main (replay R4, 6 red on 11-26) or
   only the largest side hypothesis (CUBE's n2)? Plan: all.
2. Relief: reduce to a local plane (better height, more code) or only widen the per-node
   slope allowance to the fitted gradient (bank goes amber, not red)? Plan: plane, gated.
3. Batch default `robust = true`? It changes heights of existing surfaces slightly.
4. Eeg in batch on by default (limit 30)?
5. Outvote factor 2 and cap 32: constants or Settings > Coverage entries?
6. Scope: tap-a-node inspector (counts, outlier capture numbers) here (+~3 h) or a
   follow-up issue? Plan: follow-up; fringe and events logging stay here.

## Estimated Scope

Single PR, logger 0.8.0, commits in this order (agent hours):

1. Python rebuild + hypotheses + tests (3 h)
2. Plane relief, Python + tests (2 h)
3. Eeg, config, report/topo/cli fields (1.5 h)
4. Vectors: cases, sequence part, regenerate (1.5 h)
5. Java port, cap/budget, status, void hook + tests (4 h)
6. Fringe drawing, events.csv settings/origin (2 h)
7. Docs: ice-cube.md, nodes docstring, logger README, version (1 h)
8. Private replays (11-21, 11-26), counts into the PR (1.5 h)
9. `unittest discover icecube/tests`, `./gradlew assembleDebug test lint` (0.5 h)

About 17 h; the phone check of the overlay on a real session is the owner's.

## Risks

- A real second surface (slush, a board edge) is now hidden in the side hypothesis instead
  of biasing the mean: reported through `n_alt`/`z_alt` and the strength ratio, not lost.
- The plane step could absorb a real step edge; the gate and the >= 3-member rule limit it,
  and a vector covers a step.
- Float differences between ports at exact ties: stable tie-break by capture id, vector
  tolerances as today.
- Folding past the cap restores some order dependence; only beyond 32 contributions.
- R3's "new" red pairs (two captures > 3 sigma apart with nothing else) still show red
  under R4-type rules; that is the honest answer for such a node.
