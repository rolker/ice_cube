"""Synthetic tests for the topography additions: per-survey and common priors, anchors, the
DEM soundings, ESRI ASCII and contours, the soundings reader, and a full run + validation at
a made-up site near 0 N 0 E. Nothing refers to a real place.
Run: python3 -m unittest discover icecube/tests
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from icecube import cli, crossover, dem, export, io, nodes, passes
from icecube.tests.test_icecube import make_crossing_survey, make_survey, surface_fn

ORIGIN = (0.0002, 0.0003, 25.0)        # made-up geodetic origin near 0 N 0 E


def with_passes(p, ids):
    return p.replace(pass_id=np.asarray(ids, int))


class TestPriorsAndAnchors(unittest.TestCase):
    def test_common_bias_recovered(self):
        ref = make_crossing_survey("ref", [0.0] * 6, seed=1)
        other = make_crossing_survey("other", [0.11, 0.09, 0.12, 0.10, 0.08, 0.10], seed=2)
        other.prior, other.common_prior = 0.03, 0.07
        res = crossover.adjust([ref, other], prior=0.015)
        self.assertAlmostEqual(res.common["other"]["bias"], 0.10, delta=0.01)
        self.assertTrue(res.common["other"]["separable"])
        ob = [res.bias_of("other", k) for k in range(6)]
        self.assertAlmostEqual(float(np.mean(ob)), 0.10, delta=0.01)
        # the reference keeps its small prior: biases near zero
        self.assertLess(max(abs(res.bias_of("ref", k)) for k in range(6)), 0.01)

    def test_common_bias_reaches_isolated_pass(self):
        ref = make_crossing_survey("ref", [0.0] * 6, seed=3)
        other = make_crossing_survey("other", [0.10] * 6, seed=4)
        far = make_survey("far", [0.10], nlines=1, offset_x=100.0, seed=5)       # no overlap with anything
        both = io.Points.concat([other, far.replace(pass_id=far.pass_id + 6, survey="other")])
        both.common_prior, both.prior = 0.07, 0.03
        res = crossover.adjust([ref, both], prior=0.015)
        k = res.pass_keys.index(("other", 6))
        self.assertFalse(res.separable[k])
        self.assertAlmostEqual(res.bias[k], 0.10, delta=0.01)            # the survey's common bias
        self.assertGreater(res.sigma[k], 0.03 - 1e-9)                     # with its own pass prior on top

    def test_anchor_pairs_only_on_the_mark(self):
        # two passes visit a mark 0.40 m above the ground beside it; pass b reads 3 cm high
        def survey(name, dz, pid):
            x = np.r_[np.zeros(4), np.full(4, 1.0)]
            z = np.r_[np.full(4, 0.40), np.zeros(4)] + dz
            p = io._make(name, None, x, np.zeros(8), z, sz=np.full(8, 0.005), sh=np.full(8, 0.005), frame="A")
            return p.replace(pass_id=np.full(8, pid), anchor=np.array(["m1"] * 4 + [""] * 4, dtype=object))
        a, b = survey("a", 0.0, 0), survey("b", 0.03, 0)
        res = crossover.adjust([a, b], prior=0.05, min_pairs=1)
        self.assertEqual(int(res.pair_anchor.sum()), 1)
        self.assertEqual(res.n_obs, 2)                                    # one mark pair, one ground pair
        self.assertAlmostEqual(res.bias_of("b", 0) - res.bias_of("a", 0), 0.03, delta=0.003)
        # without the anchor id the mark top would pair with the ground beside it: dropped by max_dz
        a2, b2 = a.replace(anchor=np.full(8, "", dtype=object)), b.replace(anchor=np.full(8, "", dtype=object))
        res2 = crossover.adjust([a2, b2], prior=0.05, min_pairs=1, radius=1.0)
        self.assertGreater(res2.n_cut, 0)


class TestDem(unittest.TestCase):
    def grid(self):
        xs = np.arange(0, 40.0, 2.0); ys = np.arange(0, 30.0, 2.0)
        X, Y = np.meshgrid(xs, ys)
        Z = 10.0 + 0.01 * X
        Z[:, xs >= 30] += 0.4 * (X[:, xs >= 30] - 30)          # a steep bank on the east side
        return xs, ys, Z

    def test_sigma_classes_tiles_and_tie(self):
        xs, ys, Z = self.grid()
        p = dem.dem_soundings(xs, ys, Z, tie=-10.0, sigma_open=0.1, sigma_rough=0.2, tile_m=10.0)
        self.assertEqual(len(p), Z.size)
        self.assertAlmostEqual(float(np.min(p.z)), 0.0, places=6)
        steep = p.x >= 32
        self.assertTrue(np.all(p.sz[steep] == 0.2))
        self.assertTrue(np.all(p.sz[(p.x > 2) & (p.x < 26) & (p.y > 2) & (p.y < 26)] == 0.1))
        self.assertEqual(len(np.unique(p.pass_id)), 4 * 3)
        self.assertEqual(len(p.pass_names), 12)

    def test_inflate_where_rtk_disagrees(self):
        xs, ys, Z = self.grid()
        p = dem.dem_soundings(xs, ys, Z)
        rtk = io._make("r", None, np.array([10.1, 10.2, 20.0, 20.1]), np.array([10.0, 10.1, 20.0, 20.0]),
                       np.array([10.1 + 0.5, 10.1 + 0.5, 10.2, 10.2]), frame="")
        p2, nbad, diff = dem.inflate_where_rtk_disagrees(p, [rtk], radius=1.0, dz=0.2, sigma_bad=0.3)
        self.assertEqual(nbad, 1)
        k = int(np.argmin(np.hypot(p.x - 10, p.y - 10)))
        self.assertEqual(p2.sz[k], 0.3)
        self.assertAlmostEqual(diff[int(np.argmin(np.hypot(p.x - 20, p.y - 20)))], 0.0, places=6)

    def test_slope_field_and_bilinear(self):
        xs, ys, Z = self.grid()
        f = dem.slope_field(xs, ys, Z)
        self.assertAlmostEqual(float(f(10.0, 10.0)), 0.01, places=6)
        self.assertTrue(np.isnan(dem.bilinear(xs, ys, Z, -5.0, 3.0)))


class TestExport(unittest.TestCase):
    def test_asc_round_trip(self):
        g = nodes.Grid(x0=-2.0, y0=3.0, cell=0.5, nx=4, ny=3)
        A = np.arange(12, dtype=float).reshape(3, 4); A[1, 2] = np.nan
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.asc"
            export.write_asc(p, g, A)
            xs, ys, B = dem.read_grid(p)
        np.testing.assert_allclose(xs, g.xs); np.testing.assert_allclose(ys, g.ys)
        np.testing.assert_allclose(B, A, equal_nan=True)

    def test_contours_of_a_plane_and_a_saddle(self):
        g = nodes.Grid(x0=0.0, y0=0.0, cell=1.0, nx=11, ny=6)
        X, Y = np.meshgrid(g.xs, g.ys)
        levels = export.contours(g, 0.1 * X, 0.25)
        lev = dict(levels)
        self.assertIn(0.5, lev)
        (line,) = lev[0.5]
        a = np.array(line)
        np.testing.assert_allclose(a[:, 0], 5.0, atol=1e-9)
        self.assertAlmostEqual(a[:, 1].min(), 0.0); self.assertAlmostEqual(a[:, 1].max(), 5.0)
        # a saddle: segments are produced, none cross the centre cell twice
        S = np.array([[1.0, 0.0], [0.0, 1.0]])
        segs = export.contour_segments(np.array([0.0, 1.0]), np.array([0.0, 1.0]), S, 0.5)
        self.assertEqual(len(segs), 2)


class TestSoundingsReader(unittest.TestCase):
    def test_csv_reader_and_file_passes(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "s.csv"
            cols = list(io.SOUNDINGS_COLS)
            rows = [dict(sid="a:1", source="a", session="s1", kind="capture", **{"class": "ground"}, use=1, t_unix=1.0,
                         x_m=0.0, y_m=0.0, z_m=1.0, sigma_z=0.02, sigma_xy=0.02, pass_id="s1/s000", prior_m=0.015,
                         common_prior_m=0.0, anchor="", fix=4),
                    dict(sid="a:2", source="a", session="s1", kind="capture", **{"class": "anchor"}, use=1, t_unix=2.0,
                         x_m=1.0, y_m=0.0, z_m=1.5, sigma_z=0.01, sigma_xy=0.02, pass_id="s1/s001", prior_m=0.015,
                         common_prior_m=0.0, anchor="site1", fix=4),
                    dict(sid="a:3", source="a", session="s1", kind="stop", **{"class": "ground"}, use=0, t_unix=3.0,
                         x_m=2.0, y_m=0.0, z_m=1.0, sigma_z=0.02, sigma_xy=0.02, pass_id="s1/s001", prior_m=0.015,
                         common_prior_m=0.0, anchor="", fix=4),
                    dict(sid="b:1", source="b", session="s2", kind="point", **{"class": "ground"}, use=1, t_unix=4.0,
                         x_m=0.0, y_m=0.0, z_m=1.1, sigma_z=0.02, sigma_xy=0.02, pass_id="s2/g000", prior_m=0.04,
                         common_prior_m=0.07, anchor="", fix=4)]
            with open(p, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
            a = io.read_soundings(p, "a", sources=["a"])
            self.assertEqual(len(a), 2)
            self.assertEqual(list(a.anchor), ["", "site1"])
            self.assertEqual(a.pass_names, ["s1/s000", "s1/s001"])
            self.assertAlmostEqual(a.prior, 0.015)
            b = io.read_soundings(p, "b", sources=["b"])
            self.assertAlmostEqual(b.common_prior, 0.07)
            seg, infos = passes.segment(a, {"from_file": True})
            self.assertEqual(sorted(seg.pass_id.tolist()), [0, 1])
            self.assertEqual(len(infos), 2)


def make_site(d: Path):
    """A made-up 30 x 20 m yard near 0 N 0 E: soundings CSV (two sessions on the home base,
    one on another network reading 10 cm high), a DEM npz (in local metres), a mask, regions."""
    rng = np.random.default_rng(7)
    site = io.Site(origin_geodetic=ORIGIN)
    truth = lambda x, y: 0.03 * x - 0.02 * y + 0.2 * np.sin(x / 6.0)  # noqa: E731
    rows = []
    def add(source, session, x, y, pid, prior, common, offset, sz=0.015):
        for k in range(len(x)):
            rows.append(dict(sid=f"{session}:{len(rows)}", source=source, session=session, kind="capture",
                             **{"class": "ground"}, use=1, t_unix=float(len(rows)), x_m=x[k], y_m=y[k],
                             z_m=truth(x[k], y[k]) + offset + rng.normal(0, sz), sigma_z=sz, sigma_xy=0.02,
                             pass_id=f"{session}/{pid[k]}", prior_m=prior, common_prior_m=common, anchor="", fix=4))
    for session, step, pid0 in (("s1", 0.0, "a"), ("s2", 0.5, "b")):
        xs = np.arange(2.0 + step, 28.0, 1.0); ys = np.arange(2.0, 18.0, 2.0)
        X, Y = np.meshgrid(xs, ys)
        add("home", session, X.ravel(), Y.ravel(), [f"{pid0}{int(v // 6)}" for v in Y.ravel()], 0.015, 0.0, 0.0)
    xs = np.arange(1.0, 29.0, 1.5); ys = np.arange(3.0, 17.0, 3.0)
    X, Y = np.meshgrid(xs, ys)
    add("net", "s3", X.ravel(), Y.ravel(), [f"g{int(v // 8)}" for v in X.ravel()], 0.03, 0.07, 0.10)
    with open(d / "soundings.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(io.SOUNDINGS_COLS)); w.writeheader(); w.writerows(rows)
    gx = np.arange(-20.0, 50.0, 2.0); gy = np.arange(-20.0, 40.0, 2.0)
    GX, GY = np.meshgrid(gx, gy)
    np.savez(d / "dem.npz", xs=gx, ys=gy, Z=truth(GX, GY) + 50.0 + rng.normal(0, 0.05, GX.shape))
    def ring(pts):
        out = []
        for x, y in pts:
            la, lo, _ = _geodetic(site, x, y)
            out.append([lo, la])
        return [out + [out[0]]]
    feat = lambda name, kind, pts: {"type": "Feature", "properties": {"name": name, "kind": kind},  # noqa: E731
                                    "geometry": {"type": "Polygon", "coordinates": ring(pts)}}
    (d / "masks.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        feat("shed", "structure", [(20, 8), (24, 8), (24, 11), (20, 11)])]}))
    (d / "regions.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        feat("lot", "", [(0, 0), (30, 0), (30, 20), (0, 20)]), feat("rink", "", [(5, 5), (15, 5), (15, 12), (5, 12)])]}))
    return truth


def _geodetic(site, x, y):
    """Inverse of Site.local for z = 0 (Newton on io.enu), good to well under a millimetre."""
    lat0, lon0, h0 = site.origin_geodetic
    la, lo = lat0 + y / 110574.0, lon0 + x / 111320.0
    for _ in range(5):
        e, n, _ = io.enu(la, lo, h0, lat0, lon0, h0)
        la += (y - n) / 110574.0; lo += (x - e) / 111320.0
    return la, lo, h0


CFG = """
[site]
origin_geodetic = [{lat}, {lon}, {h}]
[defaults]
wander_prior = 0.015
slope = 0.10
[output]
dir = "out"
[[masks]]
path = "masks.geojson"
kinds = ["structure"]
[[grids]]
name = "lot"
cell = 1.0
capture_radius = 1.5
extent_polygon = {{ path = "regions.geojson", name = "lot" }}
margin = 2.0
lot = true
slope_from_dem = {{ survey = "dem", factor = 0.5, floor = 0.05 }}
contour_interval = 0.25
[[grids]]
name = "rink"
cell = 0.5
capture_radius = 0.75
extent_polygon = {{ path = "regions.geojson", name = "rink" }}
margin = 1.0
contour_interval = 0.1
outline = {{ path = "regions.geojson", name = "rink" }}
[[surveys]]
id = "home"
surface = "ground"
frame = "L"
date = "2026-12-01"
reader = "soundings"
path = "soundings.csv"
sources = ["home"]
[surveys.pass_rule]
from_file = true
[[surveys]]
id = "net"
surface = "ground"
frame = "L"
date = "2026-11-01"
reader = "soundings"
path = "soundings.csv"
sources = ["net"]
[surveys.pass_rule]
from_file = true
[[surveys]]
id = "dem"
surface = "ground"
frame = "L"
date = "2022-01-01"
reader = "dem"
path = "dem.npz"
tie = -50.0
capture_radius = 1.5
sigma_h = 0.3
check_rtk = true
[surveys.pass_rule]
from_file = true
[[fill]]
surface = "ground"
grid = "rink"
outline = {{ path = "regions.geojson", name = "rink" }}
percentile = 95.0
margin = 0.10
"""


class TestTopoRun(unittest.TestCase):
    def test_run_and_validate(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            truth = make_site(d)
            (d / "cfg.toml").write_text(CFG.format(lat=ORIGIN[0], lon=ORIGIN[1], h=ORIGIN[2]))
            (d / "local.prj").write_text('LOCAL_CS["test"]')
            cfg = cli.load_config(d / "cfg.toml")
            cfg["output"]["prj"] = "local.prj"
            res = cli.run(cfg, log=lambda *a: None)
            out = d / "out"
            for name in ("z_ground_lot.asc", "z_ground_lot.prj", "sigma_ground_rink.asc", "count_ground_rink.asc",
                         "flags_ground_lot.asc", "contours_ground_lot.geojson", "points_ground.csv",
                         "crossover_ground.md", "z_ground_rink.png", "summary.md"):
                self.assertTrue((out / name).is_file(), name)
            # the other network's common 10 cm is found
            self.assertAlmostEqual(res["surfaces"]["ground"]["common"]["net"]["bias"], 0.10, delta=0.015)
            s = np.load(out / "surface_ground_rink.npz")
            X, Y = np.meshgrid(s["x0"] + np.arange(s["nx"]) * s["cell"], s["y0"] + np.arange(s["ny"]) * s["cell"])
            ok = np.isfinite(s["z"])
            err = (s["z"] - truth(X, Y))[ok]
            self.assertLess(np.sqrt(np.mean(err**2)), 0.03)
            self.assertGreater(np.mean(np.abs(err) < 3 * s["sigma"][ok]), 0.95)
            lot = np.load(out / "surface_ground_lot.npz")
            self.assertTrue(lot["mask"].any())
            self.assertTrue(np.all(np.isnan(lot["z"][lot["mask"]])))
            fill = res["grids"]["ground_rink"]["fill"]
            self.assertAlmostEqual(fill["area_m2"], 70.0, delta=6.0)
            self.assertGreater(fill["volume_m3"], 0.0)
            geo = json.loads((out / "contours_ground_lot.geojson").read_text())
            self.assertGreater(len(geo["features"]), 3)
            v = cli.main(["validate", str(d / "cfg.toml"), "--grid", "lot"])
            self.assertEqual(v, 0)
            val = json.loads((out / "validation.json").read_text())["ground"]
            self.assertEqual(set(val["loo"]), {"s1", "s2", "s3"})
            s3 = val["loo"]["s3"]
            self.assertAlmostEqual(s3["median_cm"], 10.0, delta=2.0)    # the network offset shows when left out
            self.assertLess(val["loo"]["s1"]["z_rms"], 2.0)
            self.assertIn("lot", val["coverage"])


if __name__ == "__main__":
    unittest.main()
