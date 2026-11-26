"""Test vectors for the Ice Cube Logger's live coverage overlay (logger 0.7, issue #6; 0.8, #24).

    python3 -m icecube.logger_vectors --out logger/app/src/test/resources/coverage_vectors.json

Expects: nothing but this repository (all inputs are synthetic, plain local metres).
Produces: one JSON file that the logger's JVM tests (SoundingSigmaTest, CoverageTest)
check the Java ports against. Without --out it prints the JSON.

Part `sigma`: capture rows run through `sigma_terms` of `analysis/qgis/soundings.py`
with `soundings.DEFAULTS` (and a ground sigma per case, the logger setting). That module
is imported through `sys.path` (an icecube -> analysis/qgis dependency; it uses the
standard library only). The inputs are picked by `soundings.capture_sigma_inputs`, the
rule soundings.py applies to logger captures: a single capture takes the bubble tilt
(`tilt_bubble_deg`); a tilt capture takes its own `tilt_sd_*` as sd_u and sd_h and tilt
0, or the single rule when any tilt sd is missing. The budget constants used are pinned
in `sigma_defaults`.

Part `nodes`: synthetic soundings (x, y, z, sz, sh, id) fed in order through
`LiveCoverage` (below, the Python twin of core/Coverage.java: node (i, j) at
(i * cell, j * cell), each fed node a `nodes.NodeStore`, i.e. the median-first rebuild
with count promotion and the fold at `nodes.CAP`), with reach 0. Expected per node with
soundings: z, sigma, n, n_in, n_out, n_alt, z_alt (null when none), strength, max_nr.
Soundings with a `sigma_input` take sz and sh from the `sigma` budget above, so the two
parts chain. `nodes_defaults` pins the slope, k_sigma, capture radius, cap and outvote
factor.

Part `sequences`: soundings fed one at a time with status params (cell, capture radius,
reach, target, wander); after each, the in-area nodes whose status changed (new ones
included), as "i j status" with status one of no_data (an enclosed gap), fringe,
disagree, thin, ok. Applied in turn they give every in-area node's status after every
capture; an in-area node never goes back to none. This pins clearing and flicker.

Guard: the generator refuses a vector where Java and Python could round to different
decisions: a sounding within 1e-6 m of a node's capture circle or reach, a test statistic
within 1e-6 of k_sigma, a weighted-median sum within 1e-9 (relative) of W/2 but not on it
(an exact half comes from equal weights and is the pinned lower-median rule), a status
sigma within 1e-9 of the target.

Regenerate whenever `soundings.sigma_terms` or its defaults change;
`icecube/tests/test_logger_vectors.py` fails until the committed file matches.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "logger" / "app" / "src" / "test" / "resources" / "coverage_vectors.json"


def _soundings():
    qgis = str(REPO / "analysis" / "qgis")
    if qgis not in sys.path:
        sys.path.insert(0, qgis)
    import soundings  # noqa: E402  (analysis/qgis/soundings.py, standard library only)
    return soundings


# method, sd_u, sd_e, sd_n, age_s, fix_age_s, pole_m, tilt_sd_e, tilt_sd_n, tilt_sd_u, ground
SIGMA_CASES = [
    ("floor_only", "single", 0.004, 0.003, 0.003, 1.0, 200.0, 1.8, None, None, None, 0.015),
    ("sd_u_above_floor", "single", 0.023, 0.012, 0.018, 1.0, 200.0, 1.8, None, None, None, 0.015),
    ("age_below_free", "single", 0.008, 0.005, 0.005, 1.9, 200.0, 1.8, None, None, None, 0.015),
    ("age_above_free", "single", 0.008, 0.005, 0.005, 12.5, 200.0, 1.8, None, None, None, 0.015),
    ("fix_age_0", "single", 0.008, 0.005, 0.005, 1.0, 0.0, 1.8, None, None, None, 0.015),
    ("fix_age_45", "single", 0.008, 0.005, 0.005, 1.0, 45.0, 1.8, None, None, None, 0.015),
    ("fix_age_past_settle", "single", 0.008, 0.005, 0.005, 1.0, 90.0, 1.8, None, None, None, 0.015),
    ("fix_age_missing", "single", 0.008, 0.005, 0.005, 1.0, None, 1.8, None, None, None, 0.015),
    ("long_pole", "single", 0.008, 0.005, 0.005, 1.0, 200.0, 4.5, None, None, None, 0.015),
    ("ground_0", "single", 0.008, 0.005, 0.005, 1.0, 200.0, 1.8, None, None, None, 0.0),
    ("tilt_capture", "tilt", 0.03, 0.02, 0.02, 1.4, 150.0, 1.8, 0.006, 0.009, 0.012, 0.015),
    ("tilt_capture_settling", "tilt", 0.03, 0.02, 0.02, 3.0, 30.0, 2.0, 0.004, 0.003, 0.005, 0.0),
    ("missing_sd", "single", None, None, None, None, 200.0, 1.8, None, None, None, 0.015),
    ("tilt_missing_sd", "tilt", 0.023, 0.012, 0.018, 1.0, 200.0, 1.8, None, None, None, 0.015),
    ("missing_pole", "single", 0.008, 0.005, 0.005, 1.0, 200.0, None, None, None, None, 0.015),
]
SIGMA_KEYS = ("method", "sd_u", "sd_e", "sd_n", "age_s", "fix_age_s", "pole_m",
              "tilt_sd_e", "tilt_sd_n", "tilt_sd_u", "ground")
TERM_KEYS = ("sigma_rtk", "sigma_age", "sigma_settle", "sigma_pole", "sigma_ground", "sigma_z", "sigma_xy")


def logger_sigma(inp, sm=None):
    """The logger's per-capture budget for one input dict (keys SIGMA_KEYS), through
    soundings.sigma_terms. Returns the dict of terms."""
    sm = sm or _soundings()
    sg = dict(sm.DEFAULTS["sigma"], ground=inp["ground"])
    sd_u, sd_h, tilt = sm.capture_sigma_inputs(sg, inp["method"], inp["sd_u"], inp["sd_e"], inp["sd_n"],
                                               inp["tilt_sd_u"], inp["tilt_sd_e"], inp["tilt_sd_n"])
    t = sm.sigma_terms(sg, sd_u=sd_u, sd_h=sd_h, age=inp["age_s"], fix_age=inp["fix_age_s"],
                       settle_s=sm.DEFAULTS["settle_s"], pole_m=inp["pole_m"], tilt_deg=tilt)
    return {k: t[k] for k in TERM_KEYS}


def sigma_part(sm):
    out = []
    for case in SIGMA_CASES:
        inp = dict(zip(SIGMA_KEYS, case[1:]))
        out.append({"name": case[0], "input": inp, "expected": logger_sigma(inp, sm)})
    return out


def _plane(x, y):
    return 0.30 + 0.02 * x - 0.01 * y


STATUS = ("none", "no_data", "fringe", "disagree", "thin", "ok")


class LiveNode:
    __slots__ = ("i", "j", "store", "in_area", "quad")

    def __init__(self, i, j):
        self.i, self.j = i, j
        self.store = None
        self.in_area = False
        self.quad = 0


class LiveCoverage:
    """The Python twin of core/Coverage.java (add, status) for the vectors: a sparse grid;
    each capture feeds the nodes within the capture radius (a `nodes.NodeStore` each,
    rebuilt on every feed) and marks those within reach as in the area, setting a quadrant
    bit (capture minus node, x >= 0 is bit 0, y >= 0 bit 1). Status: none (not in the
    area), no_data (no sounding, all four quadrants marked: an enclosed gap), fringe (no
    sounding, the area's edge), disagree (n_out > 0 and n_in < OUTVOTE * n_out), thin
    (sqrt(1/W + wander^2) > target), ok."""

    def __init__(self, cell, rad, reach, target=0.03, wander=0.015, k_sigma=3.0, slope=0.05, cap=None):
        from . import nodes
        self.cell, self.rad, self.reach, self.target, self.wander = cell, rad, reach, target, wander
        self.k_sigma, self.slope = k_sigma, slope
        self.cap = nodes.CAP if cap is None else cap
        self.nodes = {}
        self.trace = []

    def add(self, x, y, z, sz, sh, id):
        from . import nodes
        cell, rad, reach = self.cell, self.rad, self.reach
        r = max(rad, reach)
        i0, i1 = math.ceil((x - r) / cell), math.floor((x + r) / cell)
        j0, j1 = math.ceil((y - r) / cell), math.floor((y + r) / cell)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                d = math.hypot(i * cell - x, j * cell - y)
                assert abs(d - rad) > 1e-6 and (reach == 0 or abs(d - reach) > 1e-6), ("on a circle", x, y, i, j)
                feeds, marks = d <= rad, d <= reach
                if not (feeds or marks):
                    continue
                nd = self.nodes.get((i, j))
                if nd is None:
                    nd = self.nodes[(i, j)] = LiveNode(i, j)
                if feeds:
                    s = self.slope * (d + sh)
                    if nd.store is None:
                        nd.store = nodes.NodeStore(self.k_sigma, self.cap)
                    nd.store.add(z, sz * sz + s * s, id, self.trace)
                if marks:
                    nd.in_area = True
                    nd.quad |= 1 << ((1 if x - i * cell >= 0 else 0) | (2 if y - j * cell >= 0 else 0))

    def status(self, nd):
        from . import nodes
        if nd is None or not nd.in_area:
            return "none"
        if nd.store is None:
            return "no_data" if nd.quad == 15 else "fringe"
        o = nd.store.outputs()
        if o["n_out"] > 0 and o["n_in"] < nodes.OUTVOTE * o["n_out"]:
            return "disagree"
        ds = math.sqrt(1.0 / o["w"] + self.wander * self.wander)
        assert abs(ds - self.target) > 1e-9, ("status sigma on the target", nd.i, nd.j)
        return "thin" if ds > self.target else "ok"


def _check_trace(name, trace, k_sigma):
    """An exact half (2*cum == W) is allowed: it comes from equal weights, which are equal in
    both ports; a near half is not."""
    for t in trace:
        if t[0] == "nr":
            assert abs(t[1] - k_sigma) > 1e-6, (name, "test statistic on k", t)
        else:
            assert t[1] == 0.0 or abs(t[1]) > 1e-9 * t[2], (name, "weighted median sum near W/2", t)


def _rows(soundings):
    rows = []
    for k, s in enumerate(soundings):
        r = dict(s) if isinstance(s, dict) else dict(zip(("x", "y", "z", "sz", "sh", "id"), s))
        r.setdefault("id", 100 + k)
        rows.append(r)
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids), "ids must be unique"
    return rows


def _nodes_cases(sm):
    """(name, cell, capture_radius, soundings); a sounding is [x, y, z, sz, sh]
    or [x, y, z, sz, sh, id], or a dict with x, y, z and sigma_input (sz, sh from
    logger_sigma). Ids default to 100 + index."""
    cases = [("one_sounding", 0.5, 0.75, [[0.13, 0.21, 1.0, 0.02, 0.03]])]
    a = [[0.13 + 0.5 * k, 0.07, _plane(0.13 + 0.5 * k, 0.07) + 0.004 * (-1) ** k, 0.021, 0.033] for k in range(9)]
    b = [[0.31 + 0.5 * k, 0.83, _plane(0.31 + 0.5 * k, 0.83) - 0.003 * (-1) ** k, 0.021, 0.033] for k in range(9)]
    cases.append(("two_overlapping_strips", 0.5, 0.75, a + b))
    low = [[0.11 + 0.3 * k, 0.12, 0.50, 0.012, 0.012] for k in range(4)]
    high = [[0.26 + 0.3 * k, 0.19, 0.70, 0.012, 0.012] for k in range(4)]
    cases.append(("step_20cm_split", 0.5, 0.75, low + high))
    cases.append(("varied_distances", 0.5, 0.75, [
        [0.04, 0.03, 0.10, 0.015, 0.02], [0.37, -0.11, 0.12, 0.015, 0.02], [-0.58, 0.29, 0.09, 0.02, 0.05],
        [0.66, 0.44, 0.13, 0.018, 0.01], [-0.21, -0.63, 0.11, 0.03, 0.04]]))
    cases.append(("fine_grid_negative", 0.25, 0.6, [
        [-3.07, -2.11, -0.40, 0.02, 0.03], [-2.83, -2.02, -0.41, 0.02, 0.03], [-2.61, -1.94, -0.38, 0.025, 0.03]]))
    base = dict(zip(SIGMA_KEYS, ("single", 0.006, 0.004, 0.005, 1.2, 200.0, 1.8, None, None, None, 0.015)))
    chain = []
    for k, fa in enumerate((200.0, 10.0, 60.0, 200.0)):
        inp = dict(base, fix_age_s=fa)
        x, y = 0.17 + 0.4 * k, 0.22 + 0.1 * k
        chain.append({"x": x, "y": y, "z": _plane(x, y), "sigma_input": inp})
    tilt = dict(base, method="tilt", tilt_sd_e=0.007, tilt_sd_n=0.005, tilt_sd_u=0.011)
    chain.append({"x": 0.71, "y": 0.58, "z": _plane(0.71, 0.58) + 0.01, "sigma_input": tilt})
    for c in chain:
        t = logger_sigma(c["sigma_input"], sm)
        c["sz"], c["sh"] = t["sigma_z"], t["sigma_xy"]
    cases.append(("sigma_budget_chain", 0.5, 0.75, chain))
    # #24: an early bad capture (+12 cm) then good ones: the bad one ends in the outlier bin
    good = [[0.12, 0.09, 0.301, 0.012, 0.012], [0.41, 0.16, 0.297, 0.012, 0.012], [0.23, 0.38, 0.304, 0.012, 0.012],
            [-0.08, 0.27, 0.299, 0.012, 0.012]]
    cases.append(("early_bad_then_good", 0.5, 0.75, [[0.21, 0.17, 0.42, 0.012, 0.012]] + good))
    cases.append(("late_outlier_then_agreeing", 0.5, 0.75,
                  good[:3] + [[0.18, 0.22, 0.43, 0.012, 0.012]] + [[0.33, 0.04, 0.302, 0.012, 0.012],
                                                                     [0.07, 0.31, 0.298, 0.012, 0.012], good[3]]))
    # count promotion: a heavy sounding on node (0, 0) is the weighted median; three light
    # ones farther out, 20 cm higher, agree with each other and take over by count
    cases.append(("count_promotion", 0.5, 0.75, [
        [0.0, 0.0, 0.10, 0.004, 0.003, 1], [0.52, 0.11, 0.30, 0.03, 0.02, 2], [-0.47, 0.18, 0.302, 0.03, 0.02, 3],
        [0.09, -0.55, 0.297, 0.03, 0.02, 4]]))
    # equal weights (one spot): the median is the lower one, the pair is 1:1
    cases.append(("equal_weight_pair", 0.5, 0.6, [[0.0, 0.0, 0.30, 0.01, 0.0, 5], [0.0, 0.0, 0.10, 0.01, 0.0, 9]]))
    # equidistant from the median: the lower id is fed first and joins the main
    cases.append(("equidistant_pair_id_tie", 0.5, 0.6, [
        [0.0, 0.0, 0.0, 0.04, 0.0, 20], [0.0, 0.0, 0.125, 0.04, 0.0, 12], [0.0, 0.0, -0.125, 0.04, 0.0, 7]]))
    cases.append(("chain_0_10_20cm", 0.5, 0.6, [
        [0.0, 0.0, 0.0, 0.03, 0.0, 3], [0.0, 0.0, 0.10, 0.03, 0.0, 1], [0.0, 0.0, 0.20, 0.03, 0.0, 2]]))
    # over the cap: 40 captures around node (0, 0), every fifth one 15 cm high
    over = []
    for k in range(40):
        ang = 2.399963 * k
        rr = 0.05 + 0.25 * ((k * 7) % 11) / 10
        z = 0.20 + 0.003 * math.sin(1.7 * k) + (0.15 if k % 5 == 3 else 0.0)
        over.append([round(rr * math.cos(ang), 4), round(rr * math.sin(ang), 4), round(z, 4), 0.012, 0.012])
    cases.append(("over_cap_40", 0.5, 0.75, over))
    # past the cap at one spot: 31 low then 34 high take over (the aggregate leaves the main
    # and is dropped); 40 low then 36 high cannot (the main is the aggregate alone and the
    # oldest outliers drop)
    def spot(n, z0, k0):
        return [[0.07, 0.04, round(z0 + 0.001 * ((k * 7) % 5), 4), round(0.01 + 0.000137 * (k0 + k), 6), 0.01, k0 + k]
                for k in range(n)]
    cases.append(("over_cap_takeover", 0.5, 0.75, spot(31, 0.20, 0) + spot(34, 0.50, 31)))
    cases.append(("over_cap_drop", 0.5, 0.75, spot(40, 0.20, 0) + spot(36, 0.50, 40)))
    return cases


def _grid_case(name, cell, rad, soundings):
    rows = _rows(soundings)
    cov = LiveCoverage(cell, rad, 0.0)
    for r in rows:
        cov.add(r["x"], r["y"], r["z"], r["sz"], r["sh"], r["id"])
    _check_trace(name, cov.trace, cov.k_sigma)
    out = []
    for (i, j), nd in sorted(cov.nodes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if nd.store is None:
            continue
        o = nd.store.outputs()
        out.append({"i": i, "j": j, "z": o["z"], "sigma": o["sigma"], "n": o["n"], "n_in": o["n_in"],
                    "n_out": o["n_out"], "n_alt": o["n_alt"], "z_alt": None if math.isnan(o["z_alt"]) else o["z_alt"],
                    "strength": o["strength"], "max_nr": o["max_nr"]})
    return {"name": name, "cell": cell, "capture_radius": rad, "soundings": rows, "nodes": out}


def nodes_part(sm):
    return [_grid_case(*c) for c in _nodes_cases(sm)]


SEQ_PARAMS = {"cell": 0.5, "capture_radius": 0.75, "reach": 2.0, "target": 0.03, "wander": 0.015}


def _sequence_cases():
    """(name, soundings): fed one at a time with SEQ_PARAMS."""
    out = []
    # a +12 cm capture after one good one turns nodes red (1:1); agreeing captures clear them
    s = [[0.1, 0.1, 0.301, 0.008, 0.008], [0.35, 0.3, 0.42, 0.008, 0.008], [0.62, 0.21, 0.297, 0.008, 0.008],
         [0.3, 0.7, 0.303, 0.008, 0.008], [0.4, 0.4, 0.299, 0.008, 0.008], [0.2, 0.35, 0.302, 0.008, 0.008]]
    out.append(("late_outlier_clears", s))
    # an early bad capture is outvoted once two good ones arrive
    out.append(("early_bad_outvoted", [[0.1, 0.1, 0.43, 0.008, 0.008], [0.3, 0.2, 0.302, 0.008, 0.008],
                                       [0.2, 0.3, 0.298, 0.008, 0.008], [0.15, 0.05, 0.301, 0.008, 0.008]]))
    # flicker: membership follows the set (1:1 red, 2:1 the other way, 3:2 red again)
    out.append(("flicker", [[0.1, 0.1, 0.30, 0.008, 0.008], [0.12, 0.08, 0.50, 0.008, 0.008],
                            [0.08, 0.12, 0.502, 0.008, 0.008], [0.11, 0.11, 0.301, 0.008, 0.008],
                            [0.09, 0.09, 0.299, 0.008, 0.008]]))
    # two strips 3 m apart: the gap between them is enclosed (no_data), the outer edge fringe
    strips = [[0.25 * k + 0.03, 0.02, 0.10, 0.01, 0.01] for k in range(0, 17, 2)]
    strips += [[0.25 * k + 0.03, 3.02, 0.10, 0.01, 0.01] for k in range(0, 17, 2)]
    out.append(("strips_gap_and_fringe", strips))
    return out


def _sequence(name, soundings):
    p = SEQ_PARAMS
    rows = _rows(soundings)
    cov = LiveCoverage(p["cell"], p["capture_radius"], p["reach"], p["target"], p["wander"])
    steps = []
    last = {}
    for r in rows:
        cov.add(r["x"], r["y"], r["z"], r["sz"], r["sh"], r["id"])
        now = {k: cov.status(nd) for k, nd in cov.nodes.items() if nd.in_area}
        steps.append([f"{i} {j} {st}" for (i, j), st in sorted(now.items(), key=lambda kv: (kv[0][1], kv[0][0]))
                      if last.get((i, j)) != st])
        last = now
    _check_trace(name, cov.trace, cov.k_sigma)
    return {"name": name, "params": p, "soundings": rows, "steps": steps}


def sequences_part():
    return [_sequence(*c) for c in _sequence_cases()]


def build():
    """The whole vectors document (a dict)."""
    sm = _soundings()
    sg = sm.DEFAULTS["sigma"]
    return {
        "about": "Generated by python3 -m icecube.logger_vectors; synthetic local metres. Do not edit.",
        "sigma_defaults": {k: sg[k] for k in ("floor", "floor_h", "age_free_s", "age_rate", "settle_extra",
                                              "tip_sink", "tilt_bubble_deg")} | {"settle_s": sm.DEFAULTS["settle_s"]},
        "sigma": sigma_part(sm),
        "nodes_defaults": _nodes_defaults(),
        "nodes": nodes_part(sm),
        "sequences": sequences_part(),
    }


def _nodes_defaults():
    import inspect
    from . import nodes
    p = inspect.signature(nodes.grid_surface).parameters
    return {k: p[k].default for k in ("capture_radius", "slope", "k_sigma")} | {"cap": nodes.CAP,
                                                                                 "outvote": nodes.OUTVOTE}


def render():
    """The JSON text exactly as committed."""
    return json.dumps(build(), indent=1) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help=f"output file (default: print); the logger tests read {DEFAULT_OUT.relative_to(REPO)}")
    a = ap.parse_args(argv)
    text = render()
    if a.out:
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
