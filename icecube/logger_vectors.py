"""Test vectors for the Ice Cube Logger's live coverage overlay (logger 0.7, issue #6).

    python3 -m icecube.logger_vectors --out logger/app/src/test/resources/coverage_vectors.json

Expects: nothing but this repository (all inputs are synthetic, plain local metres).
Produces: one JSON file that the logger's JVM tests (SoundingSigmaTest, CoverageTest)
check the Java ports against. Without --out it prints the JSON.

Part `sigma`: capture rows run through `sigma_terms` of `analysis/qgis/soundings.py`
with `soundings.DEFAULTS` (and a ground sigma per case, the logger setting). That module
is imported through `sys.path` (an icecube -> analysis/qgis dependency; it uses the
standard library only). The logger's rule is applied here before the call: a single
capture takes the bubble tilt (`tilt_bubble_deg`); a tilt capture takes its own
`tilt_sd_*` as sd_u and sd_h and tilt 0 (soundings.py has no tilt-capture rule yet,
issue #9). The budget constants used are pinned in `sigma_defaults`.

Part `nodes`: synthetic soundings (x, y, z, sz, sh) run through `nodes.grid_surface` with
one pass and no pass bias (sb = 0), on a grid whose origin is a multiple of the cell and
which covers every node within the capture radius of every sounding, so node (i, j) here
is the logger's node (i, j) at (i * cell, j * cell). Expected per node with soundings:
z, sigma, n, flags, max_nr. Soundings with a `sigma_input` take sz and sh from the
`sigma` budget above, so the two parts chain. `nodes_defaults` pins grid_surface's
slope and k_sigma defaults.

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
    ("missing_pole", "single", 0.008, 0.005, 0.005, 1.0, 200.0, None, None, None, None, 0.015),
]
SIGMA_KEYS = ("method", "sd_u", "sd_e", "sd_n", "age_s", "fix_age_s", "pole_m",
              "tilt_sd_e", "tilt_sd_n", "tilt_sd_u", "ground")
TERM_KEYS = ("sigma_rtk", "sigma_age", "sigma_settle", "sigma_pole", "sigma_ground", "sigma_z", "sigma_xy")


def _sd_h(e, n):
    return math.hypot(e or 0.0, n or 0.0) / math.sqrt(2)


def logger_sigma(inp, sm=None):
    """The logger's per-capture budget for one input dict (keys SIGMA_KEYS), through
    soundings.sigma_terms. Returns the dict of terms."""
    sm = sm or _soundings()
    sg = dict(sm.DEFAULTS["sigma"], ground=inp["ground"])
    if inp["method"] == "tilt":
        sd_u, sd_h, tilt = inp["tilt_sd_u"], _sd_h(inp["tilt_sd_e"], inp["tilt_sd_n"]), 0.0
    else:
        sd_u, sd_h, tilt = inp["sd_u"], _sd_h(inp["sd_e"], inp["sd_n"]), sg["tilt_bubble_deg"]
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


def _nodes_cases(sm):
    """(name, cell, capture_radius, soundings); a sounding is [x, y, z, sz, sh] or a dict
    with x, y, z and sigma_input (sz, sh from logger_sigma)."""
    cases = [("one_sounding", 0.5, 0.75, [[0.13, 0.21, 1.0, 0.02, 0.03]])]
    a = [[0.13 + 0.5 * k, 0.07, _plane(0.13 + 0.5 * k, 0.07) + 0.004 * (-1) ** k, 0.021, 0.033] for k in range(9)]
    b = [[0.31 + 0.5 * k, 0.83, _plane(0.31 + 0.5 * k, 0.83) - 0.003 * (-1) ** k, 0.021, 0.033] for k in range(9)]
    cases.append(("two_overlapping_strips", 0.5, 0.75, a + b))
    low = [[0.11 + 0.3 * k, 0.12, 0.50, 0.012, 0.012] for k in range(4)]
    high = [[0.26 + 0.3 * k, 0.19, 0.70, 0.012, 0.012] for k in range(4)]
    cases.append(("step_20cm_flags", 0.5, 0.75, low + high))
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
    return cases


def _grid_case(name, cell, rad, soundings):
    import numpy as np
    from . import io, nodes
    rows = [s if isinstance(s, dict) else dict(zip(("x", "y", "z", "sz", "sh"), s)) for s in soundings]
    x = np.array([r["x"] for r in rows]); y = np.array([r["y"] for r in rows])
    i0, i1 = math.floor((x.min() - rad) / cell), math.ceil((x.max() + rad) / cell)
    j0, j1 = math.floor((y.min() - rad) / cell), math.ceil((y.max() + rad) / cell)
    grid = nodes.Grid(i0 * cell, j0 * cell, cell, i1 - i0 + 1, j1 - j0 + 1)
    for r in rows:   # no sounding on the capture circle of a node (float ties would decide it)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                assert abs(math.hypot(i * cell - r["x"], j * cell - r["y"]) - rad) > 1e-6, (name, r)
    p = io._make("vectors", None, x, y, [r["z"] for r in rows], sz=[r["sz"] for r in rows],
                 sh=[r["sh"] for r in rows], frame="local")
    p.pass_id = np.zeros(len(rows), int)
    s = nodes.grid_surface([p], [np.zeros(len(rows))], grid, capture_radius=rad)
    out = []
    for jj in range(grid.ny):
        for ii in range(grid.nx):
            if s.n[jj, ii] > 0:
                out.append({"i": i0 + ii, "j": j0 + jj, "z": float(s.z[jj, ii]), "sigma": float(s.sigma[jj, ii]),
                            "n": int(s.n[jj, ii]), "flags": int(s.flags[jj, ii]), "max_nr": float(s.maxnr[jj, ii])})
    return {"name": name, "cell": cell, "capture_radius": rad, "soundings": rows, "nodes": out}


def nodes_part(sm):
    return [_grid_case(*c) for c in _nodes_cases(sm)]


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
    }


def _nodes_defaults():
    import inspect
    from . import nodes
    p = inspect.signature(nodes.grid_surface).parameters
    return {k: p[k].default for k in ("capture_radius", "slope", "k_sigma")}


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
