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
a CUBE-style strength value and Eeg's test in batch. One shared definition, two ports
(Python reference, Java live), held together by test vectors.

**Revised 2026-11-26 after the plan review on PR #28** (must-fix 2-7 and the should-fixes
taken as the review proposed; owner's answers: all side members count against the main
for colour, `robust = true` and Eeg on by default in batch, factor 2 and cap 32 constants,
node inspector later). **Scope split:** the steep-bank plane step (review must-fix 1: a
gradient not taken from the node's own split, e.g. from neighbouring nodes' heights or the
`slope_from_dem` prior) moves to a follow-up issue; bank nodes may stay red until then. The
void hook stays with #27.

### A. The node rebuild (both ports, identical)

1. **Entries.** Each fed node keeps its contributions: `z`, `v` (propagated variance as
   today, `sz^2 + (slope*(d + sh))^2`), `w = 1.0 / v` (computed once, the same expression in
   both ports), and a **capture id**: `PointRow.n` in the logger
   (`Coverage.add(x, y, z, sz, sh, id)`; the five-argument form uses the feed counter), the
   time-order index in batch (`grid_surface`), an explicit `id` field in every vector
   sounding (so a permutation test carries ids with the soundings). Ids are unique within
   a node. Python batch entries also carry the pass key, `sb^2` and the time.
2. **Weighted median, pinned.** Sort the entries by `(z, id)`. `W` = the sum of `w` in that
   order, in a plain loop (no `np.sum`, `np.cumsum` or `np.median`: pairwise summation can
   differ in the last ulp and flip the exact-half case). Walk the same order with a running
   `cum += w`; `zm` is the `z` of the first entry with `2*cum >= W`. Two equal weights give
   the lower one, so the height of a 1:1 pair is the lower capture (CUBE also takes the
   shallower of an even queue, `cube_node_queue_flush_node`).
3. **Feed order:** by `(abs(z - zm), id)`. Hypotheses keep `W`, `WZ` (`WZ += w*z`), a member
   count, and in Python batch their own per-pass weights. The first entry starts the main
   hypothesis. Each next entry is tested against the **main first**:
   `nr = abs(z - WZ/W) / sqrt(1/W + v + var_bias_diff)` (Python's pass-bias term as today,
   computed from that hypothesis's own per-pass weights; 0 in the logger). It joins the
   main when `nr <= k`. Otherwise it joins the side hypothesis with the **smallest** `nr <= k`
   (ties: the earlier-created side), or opens a new side hypothesis. (CUBE picks the best
   match among all hypotheses, main included; main-first is a deliberate deviation, listed
   in the docs table.) Worst case O(n·H) per node, H the number of hypotheses; not bounded,
   since median-first keeps H small in practice.
4. **Promotion, once, after all entries are fed.** The side with the largest key (ties: the
   earlier-created) becomes the main when its key is **strictly greater** than the main's;
   the old main then becomes a side and nothing is re-tested. Key: **member count in the
   logger** (and the vectors), **summed weight `W` in batch** (`grid_surface`), so a few
   RTK shots are not outvoted by many low-weight DEM samples, and a dense 4 Hz pass wins a
   crossing by its weight, not its epoch count alone. The colour rule keeps counts.
5. **Outputs per node:** `z`, `sigma` (main only; Python adds the main's pass-bias part),
   `n` (all contributions ever, folded and dropped included), `n_in` (main count),
   `n_out = n - n_in` (all side members, plus dropped ones in the logger, plus Eeg culls in
   batch), `n_alt` / `z_alt` (largest side by count, ties earlier-created; 0 / NaN when
   none), `max_nr` = the largest `abs(z_i - z) / sqrt(1/W_main + v_i + var_bias_diff_i)` over
   **all** entries after the final rebuild (no longer a running maximum over time: the
   report's "worst" column changes meaning, said in `report.py` and the docs), and
   `strength = max(0, 5 - n_in / n_out)`, **0 when `n_out = 0`**. This is CUBE-style, not
   CUBE's ratio: CUBE divides the best hypothesis's count by the second-best's
   (`cube_node_choose_hypothesis`, `MAX_HYPOTHESIS_RATIO` 5); here all side members are in
   the denominator (the agreed R4 rule). Batch `npass` and `last_t` describe all entries.
   Side hypotheses are **outlier bins, not surfaces**: `z_alt` is reported as the outliers'
   mean, never as a layer.
6. **Batch builds the stores, then rebuilds each node once** (no per-contribution rebuilds:
   with streamed epochs or DEM samples that would be O(n^2 log n) per node in Python). Only
   the logger rebuilds on every new contribution (the vectors' sequence part does the same
   on prefixes, through the Python port of the live store).
7. **Cap and folding (logger; Python's capped store mirrors it and `logger_vectors` uses
   it).** At most `CAP = 32` stored entries per node plus one aggregate (`W`, `WZ`, count;
   it enters the rebuild as one pseudo-entry with `z = WZ/W`, `w = W`, `v = 1.0/W`,
   id `-1`, counting `count` members in `n_in` and in promotion). When an entry arrives and
   the store then holds 33: rebuild; if the main has stored (non-aggregate) members, the
   **oldest by arrival** of them is folded into the aggregate (`W += w`, `WZ += w*z`,
   `count += 1`), after first dropping the aggregate when it is not in the main (its count
   goes to `n_dropped`); if the main is the aggregate alone, the oldest stored entry (an
   outlier at that moment) is dropped and counted in `n_dropped`. Then rebuild again.
   `n_dropped` is part of `n_out` for good. Arrival order matters again past the cap only.
   No global entry budget (dropped: entries are at most ~9 per fed capture, so memory is
   bounded by captures x 9). Entries live in per-node primitive arrays allocated on the
   first feed (fringe nodes allocate nothing); the rebuild reuses scratch arrays on the
   worker thread.

### B. Status and colour (logger)

8. **Red only while the outliers are competitive:** DISAGREE while `n_out > 0` and
   `n_in < 2 * n_out` (strength > 3). Otherwise amber/clear from the main's sigma as today.
   `flags` and the sticky counter go. With few captures: one capture is amber/clear by
   sigma; two that disagree are red (1:1, the lower one is the height); three with one
   outlier are not red (2:1); two bad that agree plus one good are clear at the bad height
   (the honest majority; young fixes are #15's job). The status after k captures is a
   function of the set (below the cap), so a new capture can move the median and change
   membership: a node can flip between colours. Both go into the logger README.
9. **Fringe:** each node keeps a 4-bit mask of the quadrants (sign of capture minus node,
   `>= 0`) of the fed captures that marked it within reach. No data with all four
   quadrants is an enclosed gap (NO_DATA, red); otherwise it is the area's edge (FRINGE),
   drawn in a faint neutral grey, so the 1.25 m band outlines the area instead of reading
   as red. (The replays' "enclosed" test.) A node whose mask changes is repainted.

### C. Relief

Moved to the follow-up issue #29 (review finding 1). The global slope stays 0.05.

### D. Batch extras (Python)

10. *(Changed in implementation, see the note after this list: the minimum is 11 members
    and the guard is a floor of (1 mm)^2 on the whole denominator, not 1 on `s2_k`.)*
    **Eeg's leave-one-out test** at finalisation (after CUBE `cube_node_truncate`), on the
    main when it has >= 4 members; `eeg_quotient_limit` (default 30, 0 = off). Weighted
    form, which reduces exactly to CUBE's quotient for equal weights: for member k,
    `m_k` = the weighted mean of the others, `d_k = z_k - m_k`,
    `s2_k = sum_{i != k} w_i (z_i - m_k)^2 / (N - 2)` (the others' scatter in units of
    their stated variances), floored at 1 (the members cannot be more consistent than their
    sigmas say: this is the denominator guard; mm-quantised identical heights no longer
    give 0 or 0/0), and `q_k = d_k^2 / (s2_k * (v_k + 1/(W - w_k)))`. All members with
    `q_k >= limit` are culled at once (as CUBE does), counted in `n_out` and in a separate
    `n_eeg`, and `z`, `sigma`, `max_nr` are recomputed from the rest. Noise only (no
    pass-bias terms). Not in the logger. Expect few hits: CUBE's own note says q = 30
    rarely culls, and the k-sigma split goes first.
    *Implementation note.* A floor of 1 on `s2_k` caps `q_k` at the member's squared
    normalised residual, which the 3-sigma split already keeps under ~9-16, so Eeg could
    never cull; and with N = 4 the quotient's F(1, 2) tail makes q >= 30 about a 3 %
    event on clean data (13 culls on a clean synthetic survey). So: `q_k = d_k^2 /
    max(s2_k * (v_k + 1/(W - w_k)), (1 mm)^2)` (the 1 mm quantisation as the guard), on
    mains of at least 11 members (CUBE's median queue length); about one cull in 2500
    soundings remains on clean synthetic data.
11. **Config switch** `robust = true` in `[grid]`, and per grid in topo's `[[grids]]`
    (with `eeg_quotient_limit`), both in `icecube.example.toml` (the commented topo block
    too). `robust = false` reproduces the 0.7-era running mean and flag counter; then
    `n_out` holds the old flag count. `flags` in `surface_*.npz` and the report tables gives
    way to `n_out`, `n_alt`, `z_alt`, `strength`, `n_eeg`; the flag table becomes an outlier
    table (cli's "Flagged nodes" heading, topo's `flagged=` summary field). **The topo
    raster keeps its name `flags_*.asc`** (now the outlier count `n_out`; renaming would
    touch `analysis/qgis/topo_to_gpkg.py` and its users for no gain), said in the docs.

### E. Replay support

12. Log the Coverage settings and the map origin (values and source note) to `events.csv`
    when a rebuild starts with changed settings or frame, through `LoggerService.event`
    (which writes via `Session.event`), from a pure-Java formatter
    (`Coverage.Params.eventText(...)`) with a JVM test using made-up origins. Session files
    are private; no README example shows real values.
13. Draw the fringe as in B.9.

(The #27 void hook is out: entries carry the capture id, and #27 adds a void-set check to
the worker's existing rebuild condition.)

### F. Tests

- **Parity vectors** (`python3 -m icecube.logger_vectors`, which calls the capped Python
  store, the same fold in the same arrival order):
  - estimator cases (reach 0): `n`, `n_in`, `n_out`, `n_alt`, `z_alt`, `strength`, `z`,
    `sigma`, `max_nr`; ids in every sounding;
  - new cases: early bad capture then good ones; late outlier then three agreeing (must
    clear); a **real count promotion** (one heavy sounding on the node at z0, three light
    ones farther out at z0 + 20 cm); an **equal-weight pair** (lower-median rule); a
    **symmetric equidistant pair** (the id tie decides which joins the main); a chain
    (0, +10, +20 cm); a two-way split; an **over-cap node** (40 contributions, with
    outliers, folding and a drop);
  - a **sequence part** with status inputs (target, wander, reach >= the capture radius):
    the status of every in-area node after each capture, so the Java test checks clearing
    and flicker live, and the fringe / gap split;
  - a **near-threshold guard** in the generator (like the capture-circle assertion): no
    test statistic within 1e-6 of `k`, no `2*cum` within 1e-9 (relative) of `W` except in the
    case built for the exact half, no status sigma within 1e-9 of the target.
- **Python unit tests** (`icecube/tests`): batch permutation invariance; the multi-pass
  bias terms in the reordered test (a 10 cm offset within the detection floor blends, 20 cm
  does not); a mixed DEM + RTK node (RTK stays main by weight) and a two-pass crossing (the
  heavier pass wins; the other is in `n_out`); a weight promotion; Eeg on a planted
  outlier (culled), identical values plus one 1 mm off (kept), all identical (kept);
  `robust = false` against values **frozen from the 0.7 code** (the old `test_flag_*`
  tests move under `robust = false`); `test_topo.py` for the raster and summary fields.
- **Java** (`CoverageTest`): vectors (estimator, sequence), a permutation test (shuffle
  with ids, compare to the vector), fold parity through the over-cap vector, fringe mask
  and repaint, timing (1000 captures into one 3 m patch; a generous 5 s bound on the JVM,
  marked as a smoke bound, plus an assertion that no node stores more than 32 entries),
  the events formatter.
- **Private replays, never committed.** Run the 11-21 and 11-26 replays against the new
  Python store (`ICE_CUBE_REPO=<worktree>`), whose parity with Java the vectors pin.
  Acceptance, as counts in the PR: 11-26 red <= 6 with none in the rink footprint except
  steep-bank relief (may stay red until the follow-up); 11-21 red <= 28 with no new red on
  flat ground; node heights moved toward the good captures for the cause-b nodes.

## Files to Change

| File | Change |
|------|--------|
| `icecube/nodes.py` | rebuild, capped store, hypotheses, promotion, Eeg, `robust` switch, new Surface fields; docstring |
| `icecube/report.py`, `icecube/cli.py`, `icecube/topo.py` | outlier table, npz and summary fields in place of `flags`; `robust` / `eeg_quotient_limit` from `[grid]` and `[[grids]]` |
| `icecube/__init__.py` | module summary line |
| `icecube/logger_vectors.py`, `logger/app/src/test/resources/coverage_vectors.json` | Python port of the live coverage (area, mask, status), new cases, fields, ids, sequence part, guard; regenerated |
| `icecube/tests/test_icecube.py`, `test_logger_vectors.py`, `test_topo.py` | tests above |
| `icecube.example.toml` | `robust`, `eeg_quotient_limit` in `[grid]` and the topo `[[grids]]` block |
| `logger/.../core/Coverage.java` | store, rebuild, cap/fold, status, fringe mask, events formatter |
| `logger/.../core/CoverageRaster.java`, `MapView.java` | fringe colour, legend |
| `logger/.../MainActivity.java` | settings and origin to `events.csv` via `LoggerService.event` |
| `logger/.../core/CoverageTest.java` (+ raster test) | tests above |
| `logger/app/build.gradle`, `logger/README.md` | 0.8.0; Coverage section rewritten |
| `docs/howto/ice-cube.md` | "Where it comes from": median ordering, side bins (not surfaces), strength, Eeg, deviations; node estimator text; `flags_*.asc` meaning |

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
| Settings over constants | outvote factor 2 and the cap 32 are constants (owner's answer) |
| Only what's needed | no global entry budget, no void hook, plane step split out |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| node estimate (inliers only) | batch outputs, report tables, topo npz | Yes |
| `flags` semantics | `report.py`, `topo.py`, `cli.py`, `test_topo.py`; `flags_*.asc` keeps its name (now `n_out`), so `analysis/qgis/topo_to_gpkg.py` is unchanged | Yes |
| promotion rule | batch by weight (DEM + RTK, crossings), logger by count; tests for both | Yes |
| steep-bank relief | follow-up issue (gradient from the neighbourhood or `slope_from_dem`) | No, follow-up |
| Coverage status | logger README, legend | Yes |
| capture void flag | #27 UI and `points.csv` columns | No, #27 |
| fix-age feed rule | #15 (11-26: ~120 s to settle) | No, #15 |

## Open Questions (answered)

Owner, 2026-11-26: 1 all side members count (R4); 2 relief moved to a follow-up issue
(review finding 1); 3 batch `robust = true`; 4 Eeg on in batch (limit 30); 5 factor 2 and
cap 32 constants; 6 inspector in a follow-up.

## Estimated Scope

Single PR, logger 0.8.0, small commits in this order:

1. Python rebuild, hypotheses, promotion, capped store + tests
2. Eeg, `robust` switch, report/topo/cli fields + tests
3. Vectors: live-coverage port, cases, ids, sequence part, guard; regenerate
4. Java port: store, rebuild, fold, status, fringe mask + tests
5. Fringe drawing and legend; events.csv settings/origin
6. Docs: ice-cube.md, logger README, version 0.8.0
7. Private replays (11-21, 11-26), counts into the PR; follow-up issue for the plane step

## Risks

- A real second surface (slush, a board edge) is now hidden in the side hypothesis instead
  of biasing the mean: reported through `n_alt`/`z_alt` and the strength ratio, not lost.
- Bank nodes (steep relief beyond the 0.05 slope allowance) can stay red until the
  follow-up issue's neighbourhood gradient lands.
- Past the cap the aggregate is a heavy pseudo-sounding and is the weighted median from
  then on: a node whose first 32 inliers came from a wander spell stays anchored until a
  side outgrows it by count. Only beyond 32 contributions.
- Batch promotion by weight: at a crossing of two passes that differ beyond the detection
  floor, the pass with more weight at the node wins, and neighbouring nodes can pick
  different passes (a seam the old blended mean did not have, but now visible in `n_out`).
- Float differences between ports at exact ties: stable tie-break by capture id, vector
  tolerances as today.
- Folding past the cap restores some order dependence; only beyond 32 contributions.
- Status flicker: a new capture can change the median and the membership.
- R3's "new" red pairs (two captures > 3 sigma apart with nothing else) still show red
  under R4-type rules; that is the honest answer for such a node.
