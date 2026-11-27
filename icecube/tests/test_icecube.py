"""Synthetic-data tests. All coordinates here are made up (a flat patch at the
equator in UTM zone 31, or plain local metres); nothing refers to a real site.
Run: python3 -m unittest discover icecube/tests
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from icecube import crossover, growth, io, nodes, passes, cli

# a synthetic site: the equator on the central meridian of zone 31
SITE = io.Site(zone=31, origin=(500000.0, 0.0), z0=0.0)
LAT0, LON0 = 0.0, 3.0
RNG = np.random.default_rng(1)


def local_points(survey, x, y, z, t=None, sz=None, sh=None, quality=None, remark=None, frame="A", date="2026-01-01"):
    return io._make(survey, t, x, y, z, sz=sz, sh=sh, quality=quality, remark=remark, frame=frame, date=date)


def surface_fn(x, y):
    """A gently tilted, slightly curved surface, metres."""
    return 0.02 * x + 0.01 * y + 0.002 * (x - 5) ** 2


def lawnmower(nlines=6, length=10.0, spacing=1.0, step=0.25, x0=0.0, y0=0.0):
    """Points along parallel lines, each line = one pass; returns x, y, line index."""
    xs, ys, line = [], [], []
    for k in range(nlines):
        s = np.arange(0, length + 1e-9, step)
        if k % 2:
            s = s[::-1]
        xs.append(x0 + s); ys.append(np.full(len(s), y0 + k * spacing)); line.append(np.full(len(s), k))
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(line)


class TestUTM(unittest.TestCase):
    def test_central_meridian_and_equator(self):
        x, y = io.utm_forward(0.0, 3.0, 31)
        self.assertAlmostEqual(float(x), 500000.0, places=6)
        self.assertAlmostEqual(float(y), 0.0, places=6)

    def test_meridian_arc(self):
        # northing on the central meridian = k0 * meridian arc, by numeric integration
        a, f = 6378137.0, 1 / 298.257222101
        e2 = f * (2 - f)
        lat = np.linspace(0, np.radians(40.0), 400001)
        M = np.trapz(a * (1 - e2) / (1 - e2 * np.sin(lat) ** 2) ** 1.5, lat)
        _, y = io.utm_forward(40.0, 3.0, 31)
        self.assertAlmostEqual(float(y), 0.9996 * M, places=4)

    def test_round_trip(self):
        la = np.array([0.0, 10.0, 40.0, 60.0, -30.0]); lo = np.array([3.0, 4.5, 1.2, 5.9, 0.1])
        x, y = io.utm_forward(la, lo, 31)
        la2, lo2 = io.utm_inverse(x, y, 31)
        self.assertLess(np.abs(la2 - la).max() * 111000, 1e-6)
        self.assertLess(np.abs(lo2 - lo).max() * 111000, 1e-6)

    def test_scale_near_origin(self):
        # 100 m east along the equator is 100 m * k0 on the grid at the central meridian
        a = 6378137.0
        dlon = np.degrees(100.0 / a)
        x0, _ = io.utm_forward(0.0, 3.0, 31); x1, _ = io.utm_forward(0.0, 3.0 + dlon, 31)
        self.assertAlmostEqual(float(x1 - x0), 99.96, places=3)


class TestENUSite(unittest.TestCase):
    def test_origin_is_zero_and_axes(self):
        site = io.Site.from_config({"site": {"origin_geodetic": [0.0, 0.0, 10.0]}})
        x, y, z = site.local(np.array([0.0, 0.001, 0.0]), np.array([0.0, 0.0, 0.001]), np.array([10.0, 10.0, 12.5]))
        np.testing.assert_allclose([x[0], y[0], z[0]], 0.0, atol=1e-6)
        self.assertAlmostEqual(y[1], 110.574, delta=0.5)   # meridian arc at the equator
        self.assertAlmostEqual(x[1], 0.0, delta=1e-6)
        self.assertAlmostEqual(x[2], 111.32 * (1 + 10 / 6378137.0), delta=0.5)
        self.assertAlmostEqual(z[2], 2.5 - 111.32 ** 2 / (2 * 6378137.0), delta=1e-4)   # curvature drop

    def test_z_is_up_and_scalar_height(self):
        site = io.Site.from_config({"site": {"origin_geodetic": [45.0, -70.0, 30.0]}})
        x, y, z = site.local(np.array([45.0]), np.array([-70.0]), 31.25)
        self.assertAlmostEqual(float(z[0]), 1.25, places=7)

    def test_both_origins_refused(self):
        with self.assertRaises(ValueError):
            io.Site.from_config({"site": {"origin": [1.0, 2.0], "origin_geodetic": [0.0, 0.0, 0.0]}})


class TestReaders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _lonlat(self, x, y):
        la, lo = io.utm_inverse(500000.0 + np.asarray(x, float), np.asarray(y, float), 31)
        return lo, la

    def test_geojson_points_and_polygon(self):
        lo, la = self._lonlat([1.0, 2.0, 3.0], [1.0, 1.0, 2.0])
        feats = [{"type": "Feature", "properties": {"REMARKS": r},
                  "geometry": {"type": "Point", "coordinates": [float(o), float(a), 10.0 + k]}}
                 for k, (o, a, r) in enumerate(zip(lo, la, ["", "ref", ""]))]
        plo, pla = self._lonlat([0, 4, 4, 0], [0, 0, 3, 3])
        ring = [[float(o), float(a), 9.0] for o, a in zip(plo, pla)]
        ring.append(ring[0])
        feats.append({"type": "Feature", "properties": {"NAME": "rink"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
        p = self.dir / "s.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        pts = io.read_swmaps_geojson(p, SITE, "s")
        self.assertEqual(len(pts), 3)
        np.testing.assert_allclose(pts.x, [1, 2, 3], atol=1e-6)
        np.testing.assert_allclose(pts.z, [10, 11, 12])
        self.assertEqual(list(pts.remark), ["", "ref", ""])
        self.assertTrue(np.all(np.isnan(pts.t)) and np.all(np.isnan(pts.sz)))
        poly = io.read_swmaps_geojson(p, SITE, "s", geometry="polygon", names=["rink"])
        self.assertEqual(len(poly), 4)  # closing repeat dropped
        self.assertEqual(set(poly.remark), {"rink"})
        ring_xy = io.read_polygon_outline(p, SITE, "rink")
        np.testing.assert_allclose(ring_xy, [[0, 0], [4, 0], [4, 3], [0, 3]], atol=1e-6)
        # filters through read_survey
        got = io.read_survey(dict(id="s", path=str(p), exclude_remarks=["REF"]), SITE)
        self.assertEqual(len(got), 2)
        with self.assertRaises(ValueError):
            io.read_polygon_outline(p, SITE, "nope")

    def test_swmaps_csv(self):
        lo, la = self._lonlat([1.0, 2.0], [1.0, 1.0])
        p = self.dir / "s.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ID", "Remarks", "Time", "Latitude", "Longitude", "Elevation", "Fix ID",
                        "Horizontal Accuracy", "Vertical Accuracy"])
            w.writerow([1, "", "10/18/2026 14:10:51.500 EDT", la[0], lo[0], 5.0, 4.0, 0.01, 0.012])
            w.writerow([2, "deck", "10/18/2026 14:11:01.000 EDT", la[1], lo[1], 5.5, 5.0, 0.02, 0.03])
        pts = io.read_swmaps_csv(p, SITE, "s")
        self.assertEqual(len(pts), 2)
        self.assertAlmostEqual(pts.t[1] - pts.t[0], 9.5)
        np.testing.assert_allclose(pts.sz, [0.012, 0.03])
        self.assertEqual(list(pts.quality), [4, 5])
        got = io.read_survey(dict(id="s", path=str(p), reader="swmaps_csv", keep_quality=[4]), SITE)
        self.assertEqual(len(got), 1)

    def test_swmaps_time_ampm_and_failure(self):
        a = io._parse_swmaps_time("10/18/2026 2:10:51.500 PM EDT")
        b = io._parse_swmaps_time("10/18/2026 14:10:51.500 EDT")
        self.assertAlmostEqual(a, b)
        self.assertAlmostEqual(io._parse_swmaps_time("10/18/2026 2:10:51.500 p.m. EDT"), b)
        self.assertTrue(np.isnan(io._parse_swmaps_time("10/18/2026 2:10:51.500 XX EDT")))
        self.assertTrue(np.isnan(io._parse_swmaps_time("yesterday")))
        lo, la = self._lonlat([1.0], [1.0])
        p = self.dir / "bad.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Remarks", "Time", "Latitude", "Longitude", "Elevation", "Fix ID", "Vertical Accuracy"])
            w.writerow(["", "not a time", la[0], lo[0], 5.0, 4.0, 0.0])
        with self.assertRaises(ValueError):
            io.read_swmaps_csv(p, SITE, "s")
        # a CSV without a Time column loads untimed
        p2 = self.dir / "notime.csv"
        with open(p2, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Remarks", "Latitude", "Longitude", "Elevation"])
            w.writerow(["", la[0], lo[0], 5.0])
        pts = io.read_swmaps_csv(p2, SITE, "s")
        self.assertEqual(len(pts), 1)
        self.assertTrue(np.isnan(pts.t[0]))

    def test_set_override_parser(self):
        cfg = {"grid": {"cell": 0.5}, "surveys": [{"id": "a"}]}
        cli.apply_override(cfg, "grid.cell=0.25")
        cli.apply_override(cfg, "outline.name=rink")
        cli.apply_override(cfg, 'output.dir="out dir"')
        self.assertEqual(cfg["grid"]["cell"], 0.25)
        self.assertEqual(cfg["outline"]["name"], "rink")
        self.assertEqual(cfg["output"]["dir"], "out dir")
        with self.assertRaises(SystemExit):
            cli.apply_override(cfg, "surveys.0.id=b")
        with self.assertRaises(SystemExit):
            cli.apply_override(cfg, "novalue")

    def test_sigma_floor(self):
        p = local_points("s", [0.0], [0.0], [0.0], sz=[0.0], sh=[np.nan]).with_defaults(0.015, 0.01)
        self.assertGreater(p.sz[0], 0)
        self.assertEqual(p.sh[0], 0.01)

    def test_enu_csv(self):
        p = self.dir / "e.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["utc", "quality", "E_m", "N_m", "U_m", "gst_sig_lat", "gst_sig_lon", "gst_sig_alt"])
            w.writerow(["23:59:59.75", 4, 1.0, 2.0, 3.0, 0.01, 0.01, 0.02])
            w.writerow(["00:00:00.00", 4, 1.0, 2.0, 3.0, 0.01, 0.01, 0.02])
        pts = io.read_enu_csv(p, "e", date="2026-11-04", offset=(1, 1, 1), pole_height=1.8)
        self.assertAlmostEqual(pts.t[1] - pts.t[0], 0.25)  # midnight rollover
        np.testing.assert_allclose([pts.x[0], pts.y[0], pts.z[0]], [2.0, 3.0, 4.0 - 1.8])
        self.assertAlmostEqual(pts.sh[0], 0.01)

    def test_concat_refuses_frames(self):
        a = local_points("a", [0.0], [0.0], [0.0], frame="A")
        b = local_points("a", [1.0], [0.0], [0.0], frame="B")
        with self.assertRaises(ValueError):
            io.Points.concat([a, b])


class TestPasses(unittest.TestCase):
    def test_gap_jump_marker(self):
        t = np.array([0, 1, 2, 300, 301, 302, 303, 304.0])
        x = np.array([0, 0.5, 1, 1, 1.5, 2, 10, 10.5])
        p = local_points("s", x, np.zeros(8), np.zeros(8), t=t, remark=["", "", "", "", "", "", "", "ref"])
        out, infos = passes.segment(p, dict(gap_s=120, jump_m=3, markers=["ref"]))
        self.assertEqual(list(out.pass_id), [0, 0, 0, 1, 1, 1, 2, 3])
        self.assertEqual(len(infos), 4)
        self.assertEqual(infos[0].n, 3)

    def test_untimed_sequence_jump(self):
        x = np.array([0, 0.5, 1, 5, 5.5])
        p = local_points("s", x, np.zeros(5), np.zeros(5))
        out, infos = passes.segment(p, dict(jump_m=3.0))
        self.assertEqual(list(out.pass_id), [0, 0, 0, 1, 1])

    def test_fixed_only_settle_max(self):
        t = np.arange(0, 400, 1.0)
        q = np.full(400, 4); q[100:110] = 5
        p = local_points("s", np.zeros(400), np.zeros(400), np.zeros(400), t=t, quality=q)
        out, infos = passes.segment(p, dict(gap_s=None, jump_m=None, fixed_only=True, settle_s=20, max_s=100))
        # first pass 0..99 -> settle drops 0..19 -> 80 points; then 110..399 cut at 100 s pieces
        self.assertTrue(np.all(out.quality == 4))
        self.assertEqual(infos[0].n, 80)
        # second real pass 110..399: settle drops 110..129, then 100 s pieces 130-229, 230-329, 330-399
        self.assertEqual([i.n for i in infos], [80, 100, 100, 70])

    def test_min_points(self):
        x = np.array([0, 0.5, 10.0])
        p = local_points("s", x, np.zeros(3), np.zeros(3))
        out, infos = passes.segment(p, dict(jump_m=3.0, min_points=2))
        self.assertEqual(len(out), 2)
        self.assertEqual(len(infos), 1)


def make_survey(name, biases, noise=0.005, nlines=6, offset_x=0.0, frame="A", seed=0):
    rng = np.random.default_rng(seed)
    x, y, line = lawnmower(nlines=nlines, x0=offset_x)
    z = surface_fn(x, y) + np.array(biases)[line] + rng.normal(0, noise, len(x))
    t = np.arange(len(x)) * 1.0 + line * 500.0
    p = local_points(name, x, y, z, t=t, sz=np.full(len(x), noise), sh=np.full(len(x), 0.005), frame=frame)
    p, infos = passes.segment(p, dict(gap_s=120, jump_m=None))
    assert len(infos) == nlines, len(infos)
    return p


def make_crossing_survey(name, biases, noise=0.005, frame="A", seed=0):
    """Lines along x (passes 0..n-1) and lines along y crossing them (passes n..2n-1)."""
    rng = np.random.default_rng(seed)
    nl = len(biases) // 2
    x1, y1, l1 = lawnmower(nlines=nl, length=10.0, spacing=2.0)
    y2, x2, l2 = lawnmower(nlines=nl, length=10.0, spacing=2.0)
    x = np.r_[x1, x2]; y = np.r_[y1, y2]; line = np.r_[l1, l2 + nl]
    z = surface_fn(x, y) + np.array(biases)[line] + rng.normal(0, noise, len(x))
    t = np.arange(len(x)) * 1.0 + line * 500.0
    p = local_points(name, x, y, z, t=t, sz=np.full(len(x), noise), sh=np.full(len(x), 0.005), frame=frame)
    p, infos = passes.segment(p, dict(gap_s=120, jump_m=None))
    assert len(infos) == 2 * nl
    return p


class TestCrossover(unittest.TestCase):
    def test_recovers_biases(self):
        true = [0.02, -0.015, 0.03, -0.01, 0.0, 0.025]
        p = make_crossing_survey("s", true)
        res = crossover.adjust([p], radius=0.5, prior=0.03, slope=0.05)
        self.assertTrue(res.separable.all())
        self.assertEqual(res.components, 1)
        # only differences are observable: compare after removing the mean
        est = res.bias - res.bias.mean(); tru = np.array(true) - np.mean(true)
        self.assertLess(np.abs(est - tru).max(), 0.006)
        self.assertLess(res.rms_after, res.rms_before / 3)
        corrected, sb = crossover.apply(p, res)
        self.assertEqual(len(sb), len(p))
        self.assertTrue(np.all(sb > 0))
        resid = corrected.z - surface_fn(corrected.x, corrected.y)
        self.assertLess(resid.std(), 0.008)

    def test_no_invented_drift_on_bias_free_session(self):
        p = make_crossing_survey("s", [0.0] * 8, noise=0.01, seed=3)
        res = crossover.adjust([p], radius=0.5, prior=0.03, slope=0.05)
        self.assertTrue(np.all(np.abs(res.bias) < 2 * res.sigma + 0.002))
        self.assertLess(np.abs(res.bias).max(), 0.01)
        self.assertLess(res.chi2_dof, 3.0)

    def test_not_separable_held_at_zero(self):
        # parallel lines 1 m apart never overlap within 0.5 m: nothing to solve
        p = make_survey("s", [0.02, -0.02, 0.01, 0.0, 0.0, 0.03])
        res = crossover.adjust([p], radius=0.4, prior=0.03)
        self.assertEqual(res.n_obs, 0)
        self.assertFalse(res.separable.any())
        np.testing.assert_array_equal(res.bias, 0.0)
        np.testing.assert_allclose(res.sigma, 0.03)
        self.assertEqual(res.components, len(res.bias))
        # a pass with few weak overlaps is shrunk toward the prior, not inflated
        p2 = make_crossing_survey("s", [0.0, 0.0, 0.0, 0.05, 0.0, 0.0])
        res2 = crossover.adjust([p2], radius=0.5, prior=0.03)
        self.assertLessEqual(np.abs(res2.bias_raw).max(), 0.05 + 0.01)

    def test_outlier_pairs_dropped(self):
        p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=4)
        # a 0.6 m step in one y-line: not the same surface, must not become a bias
        z = p.z.copy(); z[(p.pass_id == 5) & (p.y > 5)] += 0.6
        p.z = z
        res = crossover.adjust([p], radius=0.5, prior=0.03, max_dz=0.25)
        self.assertGreater(res.n_cut, 0)
        self.assertLess(np.abs(res.bias).max(), 0.01)
        # a 10 cm blunder on one cell is caught by the residual test instead
        p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=4)
        z = p.z.copy(); z[(p.pass_id == 5) & (np.abs(p.y - 4) < 0.3)] += 0.10
        p.z = z
        res = crossover.adjust([p], radius=0.5, prior=0.03, max_dz=0.25, reject_sigma=4.0)
        self.assertGreater(res.n_rejected, 0)
        self.assertLess(np.abs(res.bias).max(), 0.012)

    def test_min_pairs(self):
        p = make_crossing_survey("s", [0.0, 0.0, 0.0, 0.03, 0.0, 0.0])
        res = crossover.adjust([p], radius=0.5, prior=0.03, min_pairs=1)
        res2 = crossover.adjust([p], radius=0.5, prior=0.03, min_pairs=10**6)
        self.assertTrue(res.separable.any())
        self.assertFalse(res2.separable.any())
        np.testing.assert_array_equal(res2.bias, 0.0)

    def test_separable_ratio_branch(self):
        # two passes, a single pair 0.4 m apart: posterior sigma stays near the prior
        a = local_points("s", [0.0, 0.4], [0.0, 0.0], [0.0, 0.03], sz=[0.02, 0.02], sh=[0.0, 0.0])
        a.pass_id = np.array([0, 1])
        res = crossover.adjust([a], radius=0.5, prior=0.03, slope=0.05, min_pairs=1, separable_ratio=0.8)
        self.assertEqual(res.n_obs, 1)
        self.assertGreater(res.sigma[0], 0.8 * 0.03 - 1e-9)
        self.assertFalse(res.separable.any())
        np.testing.assert_array_equal(res.bias, 0.0)
        np.testing.assert_allclose(res.sigma, 0.03)      # held at zero -> sigma is the prior
        self.assertNotEqual(res.bias_raw[0], 0.0)
        res2 = crossover.adjust([a], radius=0.5, prior=0.03, slope=0.05, min_pairs=1, separable_ratio=0.95)
        self.assertTrue(res2.separable.all())

    def test_pass_cell_variance_floor(self):
        n = 90
        a = local_points("s", np.zeros(n), np.zeros(n), np.zeros(n), sz=np.full(n, 0.015), sh=np.zeros(n))
        a.pass_id = np.zeros(n, int)
        _, _, _, _, v, _, _, cnt = crossover.pass_cells(a, 0.5)
        self.assertEqual(cnt[0], n)
        self.assertAlmostEqual(v[0], 0.015**2)   # not 0.015^2 / 90

    def test_empty_survey_refused(self):
        a = local_points("s", [], [], [])
        with self.assertRaises(ValueError):
            crossover.adjust([a])

    def test_refuses_frames(self):
        a = make_survey("a", [0.0] * 6, frame="A")
        b = make_survey("b", [0.0] * 6, frame="B")
        with self.assertRaises(ValueError):
            crossover.adjust([a, b])

    def test_two_surveys_joint(self):
        a = make_survey("a", [0.02] * 6, frame="A")
        b = make_survey("b", [-0.02] * 6, frame="A", offset_x=0.0, seed=5)
        res = crossover.adjust([a, b], radius=0.5, prior=0.03)
        self.assertEqual(len(res.pass_keys), 12)
        d = res.bias_of("a", 0) - res.bias_of("b", 0)
        self.assertAlmostEqual(d, 0.04, delta=0.01)


class TestNodes(unittest.TestCase):
    def test_propagation_single_sounding(self):
        g = nodes.Grid.from_extent(0, 1, 0, 1, 0.5)
        p = local_points("s", [0.0], [0.0], [1.0], sz=[0.01], sh=[0.02])
        p.pass_id[:] = 0
        s = nodes.grid_surface([p], [np.array([0.0])], g, capture_radius=0.6, slope=0.1)
        # node (0,0): d=0 -> sigma^2 = 0.01^2 + (0.1*(0+0.02))^2
        self.assertAlmostEqual(s.sigma[0, 0], np.sqrt(0.01**2 + 0.002**2))
        # node (0.5,0): d=0.5 -> sigma^2 = 0.01^2 + (0.1*0.52)^2
        self.assertAlmostEqual(s.sigma[0, 1], np.sqrt(0.01**2 + 0.052**2))
        self.assertTrue(np.isnan(s.z[1, 1]))  # d=0.707 > 0.6
        self.assertEqual(s.n[0, 0], 1)
        self.assertEqual(s.npass[0, 0], 1)

    def test_bias_variance_does_not_average_down(self):
        g = nodes.Grid.from_extent(0, 0, 0, 0, 0.5)
        n = 50
        p = local_points("s", np.zeros(n), np.zeros(n), np.ones(n), sz=np.full(n, 0.01), sh=np.zeros(n))
        p.pass_id[:] = 0
        s = nodes.grid_surface([p], [np.full(n, 0.03)], g, capture_radius=0.1, slope=0.0)
        self.assertAlmostEqual(s.sigma[0, 0], np.sqrt(0.01**2 / n + 0.03**2))
        # two passes with equal weight: bias variance halves
        p.pass_id[: n // 2] = 1
        s2 = nodes.grid_surface([p], [np.full(n, 0.03)], g, capture_radius=0.1, slope=0.0)
        self.assertAlmostEqual(s2.sigma[0, 0], np.sqrt(0.01**2 / n + 0.03**2 / 2))
        self.assertEqual(s2.npass[0, 0], 2)
        self.assertAlmostEqual(s.datum_sigma, 0.03)
        self.assertAlmostEqual(s2.datum_sigma, np.sqrt(0.03**2 / 2))

    def test_heights_within_sigma_and_no_flags(self):
        p = make_crossing_survey("s", [0.0] * 8, noise=0.01, seed=7)
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        s = nodes.grid_surface([p], [np.full(len(p), 0.005)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0)
        X, Y = np.meshgrid(g.xs, g.ys)
        m = s.n > 0
        err = (s.z - surface_fn(X, Y))[m]
        self.assertGreater(np.mean(np.abs(err) < 3 * s.sigma[m]), 0.95)
        # no outlier bins on clean data; Eeg (scale-free) may cull about one in 2500
        self.assertEqual(int(s.n_out.sum()), int(s.n_eeg.sum()))
        self.assertLessEqual(int(s.n_eeg.sum()), 0.002 * int(s.n.sum()))

    def test_running_mode_reproduces_0_7(self):
        """robust = false against values frozen from the 0.7 code (planted +10 cm second surface)."""
        p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=8)
        z = p.z.copy(); z[p.pass_id >= 4] += 0.10
        p.z = z
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        s = nodes.grid_surface([p], [np.full(len(p), 0.005)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0,
                               robust=False)
        self.assertAlmostEqual(float(np.nansum(s.z)), 68.20080096364538, places=10)
        self.assertAlmostEqual(float(np.nansum(s.sigma)), 3.5054049127039137, places=12)
        self.assertEqual((int(s.n.sum()), int(s.n_out.sum()), int((s.n_out > 0).sum()), int(s.npass.sum())),
                         (2526, 203, 77, 462))
        self.assertAlmostEqual(float(s.maxnr.sum()), 622.6319765174471, places=9)
        for (a, b), (zz, sg, n, fl, mr) in {
                (0, 0): (0.09491911672797482, 0.0048001760964672645, 8, 2, 10.76339142508154),
                (4, 4): (0.13244037868372738, 0.004647292496715224, 14, 4, 9.42463145566523),
                (12, 8): (0.18848020583204925, 0.004647292496715224, 14, 4, 9.715424586020244)}.items():
            self.assertAlmostEqual(s.z[a, b], zz, places=13)
            self.assertAlmostEqual(s.sigma[a, b], sg, places=14)
            self.assertEqual((s.n[a, b], s.n_out[a, b]), (n, fl))
            self.assertAlmostEqual(s.maxnr[a, b], mr, places=11)

    def test_flag_fires_on_second_surface(self):
        """robust = false (the 0.7 counter, now in n_out)."""
        p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=8)
        # plant a second surface: the y-lines are 10 cm higher
        z = p.z.copy(); z[p.pass_id >= 4] += 0.10
        p.z = z
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        s = nodes.grid_surface([p], [np.full(len(p), 0.005)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0, robust=False)
        self.assertGreater(int((s.n_out > 0).sum()), 10)
        # the two-surface nodes are where lines cross
        X, Y = np.meshgrid(g.xs, g.ys)
        on_cross = (np.abs(X % 2) < 1e-9) & (np.abs(Y % 2) < 1e-9) & (X <= 6) & (Y <= 6)
        self.assertGreater(np.mean(s.n_out[on_cross & (s.n > 0)] > 0), 0.8)
        self.assertGreater(s.maxnr.max(), 3.0)
        from icecube import report
        self.assertIn("| east (m) |", report.table_outliers(s))

    def test_flag_within_one_pass(self):
        # a 5 cm step inside one pass: the shared bias cancels, only noise counts
        g = nodes.Grid.from_extent(0, 0, 0, 0, 0.5)
        p = local_points("s", [0.0, 0.0], [0.0, 0.0], [0.0, 0.05], t=[0.0, 1.0], sz=[0.01, 0.01], sh=[0.0, 0.0])
        p.pass_id[:] = 0
        s = nodes.grid_surface([p], [np.full(2, 0.03)], g, capture_radius=0.1, slope=0.0, k_sigma=3.0, robust=False)
        self.assertEqual(s.n_out[0, 0], 1)
        self.assertAlmostEqual(s.maxnr[0, 0], 0.05 / np.sqrt(2 * 0.01**2))
        # the same step from a different pass is inside 3 sigma once both biases count
        p.pass_id = np.array([0, 1])
        s2 = nodes.grid_surface([p], [np.full(2, 0.03)], g, capture_radius=0.1, slope=0.0, k_sigma=3.0, robust=False)
        self.assertEqual(s2.n_out[0, 0], 0)
        self.assertAlmostEqual(s2.maxnr[0, 0], 0.05 / np.sqrt(2 * 0.01**2 + 2 * 0.03**2))

    def test_detection_floor_at_real_bias_sigma(self):
        # with sb = 3 cm (last season's held-at-zero passes) a 10 cm second surface is
        # below the k*sqrt(2)*sb ~ 13 cm floor; a 20 cm one is seen
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        X, Y = np.meshgrid(g.xs, g.ys)
        on_cross = (np.abs(X % 2) < 1e-9) & (np.abs(Y % 2) < 1e-9) & (X <= 6) & (Y <= 6)
        for plant, expect in ((0.10, False), (0.20, True)):
            p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=9)
            z = p.z.copy(); z[p.pass_id >= 4] += plant
            p.z = z
            s = nodes.grid_surface([p], [np.full(len(p), 0.03)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0, robust=False)
            frac = np.mean(s.n_out[on_cross & (s.n > 0)] > 0)
            self.assertEqual(frac > 0.8, expect, (plant, frac))
            if not expect:
                self.assertLess(frac, 0.2)

    def test_points_outside_grid_counted(self):
        g = nodes.Grid.from_extent(0, 1, 0, 1, 0.5)
        p = local_points("s", [0.0, 50.0], [0.0, 50.0], [0.0, 0.0], sz=[0.01, 0.01], sh=[0.0, 0.0])
        p.pass_id[:] = 0
        s = nodes.grid_surface([p], [np.zeros(2)], g, capture_radius=0.6)
        self.assertEqual(s.n_outside, 1)
        self.assertEqual(int(s.n.sum()), 3)  # (0,0), (0.5,0), (0,0.5)
        self.assertAlmostEqual(s.nodes_per_sounding, 3.0)

    def test_refuses_frames_and_missing_sigma(self):
        a = make_survey("a", [0.0] * 6, frame="A"); b = make_survey("b", [0.0] * 6, frame="B")
        g = nodes.Grid.from_extent(0, 10, 0, 6, 0.5)
        with self.assertRaises(ValueError):
            nodes.grid_surface([a, b], [np.zeros(len(a)), np.zeros(len(b))], g)
        c = local_points("c", [0.0], [0.0], [0.0]); c.pass_id[:] = 0
        with self.assertRaises(ValueError):
            nodes.grid_surface([c], [np.zeros(1)], g)


def one_node(zs, szs, passes=None, sb=None, t=None):
    """A single node at the origin fed by soundings on it (slope 0: v = sz^2)."""
    n = len(zs)
    g = nodes.Grid.from_extent(0, 0, 0, 0, 0.5)
    p = local_points("s", np.zeros(n), np.zeros(n), zs, t=t, sz=szs, sh=np.zeros(n))
    p.pass_id = np.zeros(n, int) if passes is None else np.asarray(passes)
    sbs = np.zeros(n) if sb is None else np.asarray(sb, float)
    return p, sbs, g


def node0(zs, szs, **kw):
    eeg = kw.pop("eeg", 30.0)
    p, sbs, g = one_node(zs, szs, **kw)
    s = nodes.grid_surface([p], [sbs], g, capture_radius=0.1, slope=0.0, eeg_quotient_limit=eeg)
    return {k: getattr(s, k)[0, 0] for k in ("z", "sigma", "n", "n_out", "n_alt", "z_alt", "strength", "n_eeg", "maxnr")}


class TestRobustNodes(unittest.TestCase):
    """The median-first rebuild with outlier bins (issue #24), batch mode."""

    def test_late_outlier_is_binned_and_does_not_pull(self):
        good = [0.100, 0.104, 0.097, 0.102, 0.099]
        o = node0(good + [0.25], [0.01] * 6)
        self.assertAlmostEqual(o["z"], np.mean(good), places=12)
        self.assertAlmostEqual(o["sigma"], 0.01 / np.sqrt(5), places=12)
        self.assertEqual((o["n"], o["n_out"], o["n_alt"]), (6, 1, 1))
        self.assertAlmostEqual(o["z_alt"], 0.25)
        self.assertAlmostEqual(o["strength"], 0.0)          # 5 - 5/1
        self.assertGreater(o["maxnr"], 3.0)

    def test_arrival_order_does_not_matter(self):
        rng = np.random.default_rng(3)
        n = 40
        x, y = rng.uniform(0, 3, n), rng.uniform(0, 3, n)
        z = 0.02 * x + rng.normal(0, 0.006, n)
        z[[0, 1, 2]] += [0.15, -0.12, 0.2]            # early outliers
        g = nodes.Grid.from_extent(0, 3, 0, 3, 0.5)
        out = []
        for perm in (np.arange(n), rng.permutation(n), rng.permutation(n)):
            p = local_points("s", x[perm], y[perm], z[perm], sz=np.full(n, 0.01), sh=np.full(n, 0.01))
            p.pass_id[:] = 0
            out.append(nodes.grid_surface([p], [np.zeros(n)], g, capture_radius=0.75))
        for s in out[1:]:
            np.testing.assert_allclose(s.z, out[0].z, rtol=0, atol=1e-15)
            np.testing.assert_allclose(s.sigma, out[0].sigma, rtol=0, atol=1e-15)
            np.testing.assert_array_equal(s.n_out, out[0].n_out)
        self.assertGreater(int(out[0].n_out.sum()), 0)

    def test_two_passes_within_the_detection_floor_blend(self):
        # sb = 3 cm on two passes: a 10 cm offset is inside k * sqrt(2 sb^2 + ...) and blends;
        # 20 cm is outside and the smaller pass goes to the outlier bin
        for dz, out in ((0.10, 0), (0.20, 2)):
            o = node0([0.0, 0.002, -0.001, dz, dz + 0.001], [0.01] * 5, passes=[0, 0, 0, 1, 1], sb=[0.03] * 5)
            self.assertEqual(o["n_out"], out, dz)
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        X, Y = np.meshgrid(g.xs, g.ys)
        on_cross = (np.abs(X % 2) < 1e-9) & (np.abs(Y % 2) < 1e-9) & (X <= 6) & (Y <= 6)
        for plant, expect in ((0.10, False), (0.20, True)):
            p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=9)
            z = p.z.copy(); z[p.pass_id >= 4] += plant
            p.z = z
            s = nodes.grid_surface([p], [np.full(len(p), 0.03)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0)
            frac = np.mean(s.n_out[on_cross & (s.n > 0)] > 0)
            self.assertEqual(frac > 0.8, expect, (plant, frac))

    def test_dem_samples_do_not_outvote_rtk(self):
        # 3 RTK shots (1 cm) and 10 DEM samples (15 cm) 60 cm higher: by count the DEM would
        # win; promotion by weight keeps the RTK
        o = node0([0.0, 0.004, -0.003] + [0.6] * 10, [0.01] * 3 + [0.15] * 10, passes=[0] * 3 + [1] * 10)
        self.assertAlmostEqual(o["z"], 0.001 / 3, places=12)
        self.assertEqual((o["n_out"], o["n_alt"]), (10, 10))
        self.assertEqual(o["strength"], 5.0 - 3 / 10)

    def test_two_pass_crossing_heavier_pass_wins(self):
        # a dense pass (12 epochs) and a sparse one (4) 15 cm apart, no bias: the sparse one is
        # the outlier bin whatever the order, and the node keeps the dense pass's height
        dense = [0.0 + 0.001 * (k % 3) for k in range(12)]
        sparse = [0.15, 0.151, 0.149, 0.15]
        for zs, ps in ((dense + sparse, [0] * 12 + [1] * 4), (sparse + dense, [1] * 4 + [0] * 12)):
            o = node0(zs, [0.01] * 16, passes=ps)
            self.assertAlmostEqual(o["z"], np.mean(dense), places=12)
            self.assertEqual(o["n_out"], 4)

    def test_weight_promotion(self):
        # the weighted median is the middle sounding; both sides weigh more and the
        # earlier-created one (lower id at the equal distance) becomes the main
        o = node0([-0.2, 0.0, 0.2], [0.008, 0.0095, 0.008])
        self.assertAlmostEqual(o["z"], -0.2, places=12)
        self.assertEqual((o["n_out"], o["n_alt"]), (2, 1))

    def test_eeg(self):
        # pessimistic sigmas (3 cm) and a 6 cm outlier inside the 3-sigma test: Eeg culls it
        good = [0.100, 0.104, 0.097, 0.102, 0.099, 0.101, 0.098, 0.103, 0.100, 0.102, 0.099]
        o = node0(good + [0.16], [0.03] * 12)
        self.assertEqual((o["n_eeg"], o["n_out"], o["n_alt"]), (1, 1, 0))
        self.assertAlmostEqual(o["z"], np.mean(good), places=12)
        self.assertEqual(node0(good + [0.16], [0.03] * 12, eeg=0)["n_out"], 0)
        self.assertEqual(node0(good[:9] + [0.16], [0.03] * 10)["n_eeg"], 0)   # under EEG_MIN_N members
        # identical heights plus one 1 mm off, and all identical: kept (the denominator guard)
        self.assertEqual(node0([0.1] * 11 + [0.101], [0.01] * 12)["n_eeg"], 0)
        self.assertEqual(node0([0.1] * 12, [0.01] * 12)["n_eeg"], 0)
        # equal weights: CUBE's quotient d^2 / (ssd_k - d^2 / (n - 1))
        z = np.array(good + [0.16])
        e = [nodes.entry(float(v), 0.03 ** 2, k) for k, v in enumerate(z)]
        n1 = len(z) - 1
        ssd = np.sum((z - z.mean()) ** 2)
        d2 = (z[-1] - z.mean()) ** 2
        q_cube = d2 / (n1 * ssd / (n1 * n1 - 1) - d2 / (n1 - 1))
        self.assertEqual(nodes.eeg_cull(e, list(range(len(z))), q_cube * 0.999), [len(z) - 1])
        self.assertEqual(nodes.eeg_cull(e, list(range(len(z))), q_cube * 1.001), [])


def bank_block(g, seed, n=7, per_disc=8, sz=0.02, sh=0.01, extra=None, ymin=None, ny=None):
    """Soundings at random on a block of n x n nodes (n x `ny` when given: n columns along x,
    ny rows along y; cell 0.5 m, node (0, 0) at the origin), `per_disc` per 0.75 m capture
    disc on average, z = g*x plus N(0, sz) noise; `extra` (x, y, z) arrays appended; `ymin`
    drops soundings below it (a survey edge)."""
    ny = n if ny is None else ny
    rng = np.random.default_rng(seed)
    lo, hi, hiy = -0.75, (n - 1) * 0.5 + 0.75, (ny - 1) * 0.5 + 0.75
    m = int(round(per_disc / (np.pi * 0.75 ** 2) * (hi - lo) * (hiy - lo)))
    x = rng.uniform(lo, hi, m)
    y = rng.uniform(lo, hiy, m)
    z = g * x + rng.normal(0, sz, m)
    if extra is not None:
        x, y, z = np.r_[x, extra[0]], np.r_[y, extra[1]], np.r_[z, extra[2]]
    if ymin is not None:
        k = y >= ymin
        x, y, z = x[k], y[k], z[k]
    p = local_points("b", x, y, z, t=np.arange(len(z), dtype=float), sz=np.full(len(z), sz), sh=np.full(len(z), sh))
    p.pass_id = np.zeros(len(z), int)
    return p, nodes.Grid(0.0, 0.0, 0.5, n, ny)


def bank_run(p, grid, sb=0.0, **kw):
    kw.setdefault("slope", 0.05)
    return nodes.grid_surface([p], [np.full(len(p), float(sb))], grid, capture_radius=0.75, k_sigma=3.0, **kw)


def red_of(s):
    return (s.n_out > 0) & (s.n - s.n_out < nodes.OUTVOTE * s.n_out)


def red_neighbours(s, a, b):
    r = red_of(s)
    return sum(int(r[a + da, b + db]) for da in (-1, 0, 1) for db in (-1, 0, 1)
               if (da or db) and 0 <= a + da < r.shape[0] and 0 <= b + db < r.shape[1])


SAME = ("z", "sigma", "n", "n_out", "n_alt", "strength", "n_eeg", "maxnr")


class TestPlanePass(unittest.TestCase):
    """Issue #29: a gradient from the neighbours' pass-1 mains, pass 2 on red nodes only.
    Every block is built from soundings (shared between neighbouring nodes as in production);
    the seeds were picked so each case is what it is named for, and the asserts say so."""

    C = (3, 3)   # the centre node of the 7 x 7 block

    def assert_pass1(self, s, s0, at):
        for k in SAME:
            self.assertTrue(np.array_equal(getattr(s, k)[at], getattr(s0, k)[at]), k)
        self.assertFalse(s.planar[at])

    def test_entries_carry_geometry_and_pass2_variance(self):
        e = nodes.entry(0.5, 0.01, 3, dx=0.2, dy=-0.1, sz2=0.0004, sh=0.02)
        self.assertEqual(e[7:], (0.2, -0.1, 0.0004, 0.02))
        self.assertEqual(nodes.entry(0.5, 0.01, 3)[7:], (0.0, 0.0, 0.01, 0.0))
        cov = np.array([[1e-4, 2e-5], [2e-5, 4e-4]])
        (r,) = nodes.reduce_to_plane([e], 0.3, -0.4, cov)
        self.assertAlmostEqual(r[0], 0.5 - (0.3 * 0.2 + 0.4 * 0.1), places=12)
        vg = 0.04 * 1e-4 - 2 * 0.02 * 2e-5 + 0.01 * 4e-4
        self.assertAlmostEqual(r[1], 0.0004 + (0.5 * 0.02) ** 2 + vg, places=15)   # sz2 + (|g| sh)^2 + d'C d
        self.assertEqual(r[7:], e[7:])

    def test_bank_clears(self):
        for g, seed, clears in ((0.2, 0, (2,)), (0.3, 0, (1, 2, 3))):
            p, grid = bank_block(g, seed)
            s0 = bank_run(p, grid, plane=False)
            self.assertTrue(red_of(s0)[self.C], g)
            for mc in clears:
                s = bank_run(p, grid, plane_min_clear=mc)
                self.assertFalse(red_of(s)[self.C], (g, mc))
                self.assertTrue(s.planar[self.C])
                self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
                self.assertAlmostEqual(s.gradient[self.C], g, delta=0.05)
                self.assertAlmostEqual(s.z[self.C], g * 1.5, delta=0.03)   # the bank's height at the node
            # only nodes red after pass 1 are touched; none turns red
            self.assertFalse(np.any(red_of(s) & ~red_of(s0)))
            for k in SAME:
                np.testing.assert_array_equal(getattr(s, k)[~red_of(s0)], getattr(s0, k)[~red_of(s0)])
        self.assertEqual(red_neighbours(s0, *self.C), 3)   # the 0.3 case has red neighbours

    def test_accepted_sigma_carries_the_common_gradient_error(self):
        # the gradient error is common to every reduced entry: the output sigma'^2 is the
        # pass-2 rebuild's plus d_bar'C_g d_bar (d_bar the pass-2 main's centroid)
        from unittest import mock
        seen = []
        real_reduce, real_robust = nodes.reduce_to_plane, nodes._robust

        def reduce(es, gx, gy, cov_g):
            seen.append(["cov", cov_g])
            return real_reduce(es, gx, gy, cov_g)

        def robust(*a, **k):
            o = real_robust(*a, **k)
            if seen and len(seen[-1]) == 2:
                seen[-1].append(o)
            return o
        p, grid = bank_block(0.3, 0)
        with mock.patch.object(nodes, "reduce_to_plane", reduce), mock.patch.object(nodes, "_robust", robust):
            s = bank_run(p, grid)
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
        z_c = float(s.z[self.C])
        (_, cg, o2), = [q for q in seen if q[2]["z"] == z_c]
        d = np.array([o2["cx"], o2["cy"]])
        vbar = float(d @ cg @ d)
        self.assertGreater(vbar, 0.0)
        self.assertAlmostEqual(s.sigma[self.C] ** 2, o2["sigma"] ** 2 + vbar, places=12)

    def test_masked_nodes_are_not_tried_or_neighbours(self):
        p, grid = bank_block(0.3, 0)
        self.assertEqual(bank_run(p, grid).plane_code[self.C], nodes.PLANE_ACCEPTED)
        m = np.zeros((grid.ny, grid.nx), bool)
        m[self.C] = True
        self.assertEqual(bank_run(p, grid, node_mask=m).plane_code[self.C], nodes.PLANE_NOT_TRIED)
        m[:] = False
        m[2, 2:5] = m[3, 2] = True    # 4 of the 8 neighbours masked: 4 usable, under 5
        self.assertEqual(bank_run(p, grid, node_mask=m).plane_code[self.C], nodes.PLANE_FEW)
        with self.assertRaises(ValueError):
            bank_run(p, grid, node_mask=np.zeros((2, 2), bool))

    def test_topo_mask_orientation_on_a_non_square_grid(self):
        # 9 columns (x) by 5 rows (y), masks drawn as polygons through topo.grid_surface. The
        # target node (row 2, col 3) at (1.5, 1.0) passes unmasked; masking 4 of its 8
        # neighbours (row 1, cols 2-4, and row 2, col 2) leaves 4 usable, PLANE_FEW. The
        # transpose of that (5, 9) mask is (9, 5): a transposed mask anywhere fails the
        # shape check, and the masked nodes must be exactly these (row, col).
        from icecube import topo
        p, grid = bank_block(0.3, 0, n=9, ny=5)
        T = (2, 3)
        sq = lambda x0, x1, y0, y1: np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])

        def run(masks):
            ctx = topo.Context(cfg={}, base=None, site=None, surveys={"b": (p, [], {})}, masks=masks)
            return topo.grid_surface(ctx, {}, grid, "s", [p], [np.zeros(len(p))], (0.05, None))
        s, m = run([])
        self.assertEqual(s.z.shape, (5, 9))
        self.assertFalse(m.any())
        self.assertEqual(s.plane_code[T], nodes.PLANE_ACCEPTED)
        s, m = run([("row1", sq(0.75, 2.25, 0.25, 0.75)), ("left", sq(0.75, 1.25, 0.75, 1.25))])
        self.assertEqual(sorted(map(tuple, np.argwhere(m))), [(1, 2), (1, 3), (1, 4), (2, 2)])
        self.assertTrue(np.all(np.isnan(s.z[m])))
        self.assertTrue(np.all(s.plane_code[m] == nodes.PLANE_NOT_TRIED))
        self.assertEqual(s.plane_code[T], nodes.PLANE_FEW)
        with self.assertRaises(ValueError):
            bank_run(p, grid, node_mask=m.T)

    def test_bank_clears_with_one_red_neighbour(self):
        p, grid = bank_block(0.2, 33)
        s0 = bank_run(p, grid, plane=False)
        self.assertTrue(red_of(s0)[self.C])
        self.assertEqual(red_neighbours(s0, *self.C), 1)
        self.assertTrue(bank_run(p, grid).planar[self.C])

    def test_bank_clears_at_a_survey_edge(self):
        E = (0, 3)   # bottom row: 5 neighbours, no soundings below y = -0.25
        for g, seed in ((0.2, 14), (0.3, 2)):
            p, grid = bank_block(g, seed, ymin=-0.25)
            s0 = bank_run(p, grid, plane=False)
            self.assertTrue(red_of(s0)[E], g)
            s = bank_run(p, grid)
            self.assertTrue(s.planar[E], g)
            self.assertFalse(red_of(s)[E], g)

    def test_fringe_bunched_centroids_are_ill_conditioned(self):
        # a survey cut at y = 0: the bottom-row node's neighbours hold members only inside the
        # survey, so their centroids bunch in y and the gradient is not pinned. Without the
        # conditioning gate pass 2 is accepted 6 cm off the bank with a 5 cm sigma.
        E = (0, 3)
        p, grid = bank_block(0.3, 29, ymin=0.0)
        s0 = bank_run(p, grid, plane=False)
        self.assertTrue(red_of(s0)[E])
        loose = bank_run(p, grid, plane_max_sigma_g=1e9)
        self.assertEqual(loose.plane_code[E], nodes.PLANE_ACCEPTED)
        self.assertGreater(abs(loose.z[E] - 0.45), 0.05)
        s = bank_run(p, grid)
        self.assertEqual(s.plane_code[E], nodes.PLANE_ILL_CONDITIONED)
        self.assert_pass1(s, s0, E)

    def test_conditioning_fails_closed(self):
        nbrs = [(0.5 * k, 0.0, 0.1 * k, 1e4, False) for k in range(6)]   # collinear: singular
        self.assertEqual(nodes.plane_fit(nbrs, 5, 2, 0.25, 3.0, 0.08)[0], nodes.PLANE_ILL_CONDITIONED)
        nbrs = [(x, y, 0.3 * x, 1e4, False) for x in (-0.5, 0.0, 0.5) for y in (-0.5, 0.5)]
        self.assertEqual(nodes.plane_fit(nbrs, 5, 2, 0.25, 3.0, 0.08)[0], 0)
        self.assertEqual(nodes.plane_fit(nbrs, 5, 2, 0.25, 3.0, float("nan"))[0], nodes.PLANE_ILL_CONDITIONED)
        bad = nbrs[:-1] + [(0.5, 0.5, float("nan"), 1e4, False)]
        self.assertNotEqual(nodes.plane_fit(bad, 5, 2, 0.25, 3.0, 0.08)[0], 0)
        self.assertTrue(np.isnan(nodes._sigma_g_max(float("nan"), 0.0, 1.0)))
        self.assertAlmostEqual(nodes._sigma_g_max(4.0, 0.0, 1.0), 2.0)
        self.assertAlmostEqual(nodes._sigma_g_max(2.0, 1.0, 2.0), np.sqrt(3.0))
        # an infinite limit still fails a non-finite value: collinear centroids (no proper
        # covariance) and an overflowing chi stop at their gates
        inf = float("inf")
        coll = [(0.5 * k, 0.0, 0.1 * k, 1e4, False) for k in range(6)]
        self.assertEqual(nodes.plane_fit(coll, 5, 2, 0.25, inf, inf)[0], nodes.PLANE_ILL_CONDITIONED)
        huge = nbrs[:-1] + [(0.5, 0.5, 1e300, 1e4, False)]
        with np.errstate(over="ignore"):
            code, fit = nodes.plane_fit(huge, 5, 2, 0.25, inf, inf)
        self.assertEqual(code, nodes.PLANE_NOT_PLANAR)
        self.assertEqual(fit["chi"], inf)
        self.assertFalse(nodes._pos_def(np.diag([1e-4, 1.0, -1e-9])))
        self.assertFalse(nodes._pos_def(np.diag([0.0, 1e-4, 1e-4])))
        self.assertTrue(nodes._pos_def(np.diag([1e-4, 1e-4, 1e-4])))

    def test_collinear_centroids_never_pass(self):
        # centroids on one random line through the node: N is singular up to roundoff, and a
        # plain inverse returned code 0 on 121 of these 500 (an indefinite C_g whose larger
        # eigenvalue passed the conditioning gate). Conditioning is checked on the unscaled
        # covariance before beta is solved, so every one stops there, never at planarity
        # (when planarity came first, 67 of the 500 stopped at PLANE_NOT_PLANAR on roundoff)
        rng = np.random.default_rng(0)
        codes = []
        for _ in range(500):
            th, t = rng.uniform(0, np.pi), rng.uniform(-0.6, 0.6, 8)
            nbrs = [(float(u * np.cos(th)), float(u * np.sin(th)),
                     float(0.3 * u * np.cos(th) + rng.normal(0, 0.01)), float(rng.uniform(1e3, 1e4)), False)
                    for u in t]
            codes.append(nodes.plane_fit(nbrs, 5, 2, 0.25, 3.0, 0.08)[0])
        self.assertEqual(set(codes), {nodes.PLANE_ILL_CONDITIONED})

    def test_collinear_code_does_not_depend_on_summation_order(self):
        # the collinear plane vector's centre node, its neighbours in 200 random orders:
        # always PLANE_ILL_CONDITIONED (the Java port sums in its own order)
        from unittest import mock
        from icecube import logger_vectors as lv
        (name, snd), = [c for c in lv._plane_cases() if c[0] == "collinear_ill_conditioned"]
        real, rng, codes = nodes.plane_fit, np.random.default_rng(5), []

        def shuffled(nbrs, *a):
            for _ in range(200):
                order = rng.permutation(len(nbrs))
                codes.append(real([nbrs[i] for i in order], *a)[0])
            return real(nbrs, *a)
        with mock.patch.object(nodes, "plane_fit", shuffled):
            lv._plane_case(name, snd)
        self.assertTrue(codes)
        self.assertEqual(set(codes), {nodes.PLANE_ILL_CONDITIONED})

    def test_wrong_cluster_keeps_pass1(self):
        # a 0.3 m/m bank plus a tight cluster 15 cm above it around the node: pass 2 makes
        # the cluster the main with fewer outliers (rule i holds), but its height disagrees
        # with the neighbour plane at the node (rule ii), so pass 1 stands
        rng = np.random.default_rng(1011)
        cx, cy = rng.uniform(1.25, 1.75, 10), rng.uniform(1.25, 1.75, 10)
        p, grid = bank_block(0.3, 11, extra=(cx, cy, 0.3 * cx + 0.15 + rng.normal(0, 0.005, 10)))
        s0 = bank_run(p, grid, plane=False)
        s = bank_run(p, grid)
        self.assertTrue(red_of(s0)[self.C])
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_OFF_PLANE)
        self.assert_pass1(s, s0, self.C)
        # a pass bias is shared by the node and its neighbours, so it cancels in the
        # agreement test: with sb > 0 the cluster still disagrees with the plane
        for sb in (0.01, 0.02, 0.03):
            s0, s = bank_run(p, grid, sb=sb, plane=False), bank_run(p, grid, sb=sb)
            self.assertTrue(red_of(s0)[self.C], sb)
            self.assertEqual(s.plane_code[self.C], nodes.PLANE_OFF_PLANE, sb)
            self.assert_pass1(s, s0, self.C)

    def test_flat_edge_outlier_is_not_absorbed(self):
        # 400 trials: a flat 5 x 5 block, one +15 cm capture on the edge of the centre
        # node's disc (feeding its neighbours too). The plane pass changes nothing.
        rng = np.random.default_rng(29)
        absorbed = [0, 0]
        for k in range(400):
            th = rng.uniform(0, 2 * np.pi)
            p, grid = bank_block(0.0, 10000 + k, n=5,
                                 extra=([1.0 + 0.7 * np.cos(th)], [1.0 + 0.7 * np.sin(th)], [0.15]))
            for j, pl in enumerate((False, True)):
                s = bank_run(p, grid, plane=pl)
                absorbed[j] += int(s.n_out[2, 2] == 0)
                if pl:
                    self.assertFalse(s.planar.any(), k)
        self.assertEqual(absorbed[1], absorbed[0])
        self.assertEqual(absorbed[0], 5)   # recorded on the first run (grid level), so drift shows

    def test_gate_too_few_neighbours(self):
        p, grid = bank_block(0.3, 0)
        s0, s = bank_run(p, grid, plane=False), bank_run(p, grid)
        self.assertTrue(red_of(s0)[0, 0])   # a corner: 3 neighbours
        self.assertEqual(s.plane_code[0, 0], nodes.PLANE_FEW)
        self.assert_pass1(s, s0, (0, 0))

    def test_gate_all_red_neighbours(self):
        # flat ground, every sounding doubled 20 cm up: every node red, no clear neighbour
        p, grid = bank_block(0.0, 0)
        n = len(p)
        q = local_points("b", np.r_[p.x, p.x + 0.01], np.r_[p.y, p.y], np.r_[p.z, p.z + 0.2],
                         t=np.arange(2 * n, dtype=float), sz=np.r_[p.sz, p.sz], sh=np.r_[p.sh, p.sh])
        q.pass_id = np.zeros(2 * n, int)
        s0, s = bank_run(q, grid, plane=False), bank_run(q, grid)
        self.assertTrue(red_of(s0).all())
        self.assertTrue((s.plane_code == nodes.PLANE_FEW_CLEAR)[1:-1, 1:-1].all())
        self.assert_pass1(s, s0, self.C)

    def test_gate_ridge_is_not_planar(self):
        # z = 0.4 |x - 1.5|: the centre column is the ridge line
        rng = np.random.default_rng(503)
        p, grid = bank_block(0.0, 3)
        p.z = 0.4 * np.abs(p.x - 1.5) + rng.normal(0, 0.02, len(p))
        s0, s = bank_run(p, grid, plane=False), bank_run(p, grid)
        self.assertTrue(red_of(s0)[self.C])
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_NOT_PLANAR)
        self.assert_pass1(s, s0, self.C)

    def test_dem_gate(self):
        p, grid = bank_block(0.3, 0)
        shape = (grid.ny, grid.nx)
        # slope as an array, no raw DEM slope (no slope_from_dem): the DEM gate is skipped
        s = bank_run(p, grid, slope=np.full(shape, 0.05))
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
        # a DEM that says flat: |g| over dem_cap * 0 + dem_floor
        s0 = bank_run(p, grid, plane=False)
        s = bank_run(p, grid, raw_slope=np.zeros(shape))
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_DEM)
        self.assert_pass1(s, s0, self.C)
        # a DEM that agrees: accepted
        s = bank_run(p, grid, raw_slope=np.full(shape, 0.3))
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
        # no DEM slope at the node (outside the DEM, NaN): the DEM gate is skipped, the others apply
        s = bank_run(p, grid, raw_slope=np.full(shape, np.nan))
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
        rs = np.zeros(shape)
        rs[self.C] = np.nan      # elsewhere flat: only the node's own slope is read
        s = bank_run(p, grid, raw_slope=rs)
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_ACCEPTED)
        # only NaN skips the gate: an infinite DEM slope fails it, as any non-finite limit
        rs[self.C] = np.inf
        s = bank_run(p, grid, raw_slope=rs)
        self.assertEqual(s.plane_code[self.C], nodes.PLANE_DEM)
        self.assert_pass1(s, s0, self.C)

    def test_running_mode_ignores_plane(self):
        p, grid = bank_block(0.3, 0)
        s = bank_run(p, grid, robust=False)
        self.assertFalse(s.planar.any())
        self.assertFalse(s.plane_code.any())


class TestNodeStore(unittest.TestCase):
    """The logger's capped store (Coverage.java's twin)."""

    def test_count_promotion(self):
        st = nodes.NodeStore()
        st.add(0.0, 0.005 ** 2, 1)
        for k in range(3):
            st.add(0.2 + 0.001 * k, 0.03 ** 2 + 0.03 ** 2, 2 + k)
        o = st.outputs()
        self.assertEqual((o["n_in"], o["n_out"]), (3, 1))
        self.assertAlmostEqual(o["z"], 0.201, places=12)

    def test_equal_pair_takes_the_lower(self):
        st = nodes.NodeStore()
        st.add(0.3, 0.0001, 7)
        st.add(0.1, 0.0001, 9)
        o = st.outputs()
        self.assertEqual((o["z"], o["n_in"], o["n_out"]), (0.1, 1, 1))

    def test_fold_and_drop(self):
        st = nodes.NodeStore(cap=4)
        for k in range(6):
            st.add(0.01 * (k % 2), 0.01 ** 2, k)
        self.assertEqual((len(st.entries), st.agg_n, st.n_dropped, st.n), (4, 2, 0, 6))
        o = st.outputs()
        self.assertEqual((o["n"], o["n_in"], o["n_out"]), (6, 6, 0))
        self.assertAlmostEqual(o["z"], 0.005, places=12)
        # a run of outliers: the main's stored members fold, then (the main being the
        # aggregate alone) the oldest outliers drop; past the cap a side can no longer
        # outgrow the aggregate, so the height stays and n_out grows: the node goes red
        for k in range(6, 16):
            st.add(0.5, 0.01 ** 2, k)
        o = st.outputs()
        self.assertEqual((o["n"], o["n_in"], o["n_out"], o["n_alt"]), (16, 6, 10, 4))
        self.assertEqual((len(st.entries), st.agg_n, st.n_dropped), (4, 6, 6))
        self.assertAlmostEqual(o["z"], 0.005, places=12)
        self.assertLess(o["n_in"], nodes.OUTVOTE * o["n_out"])
        # while the aggregate is still small, a side that outgrows it takes over, and the
        # next fold drops the aggregate (its members count as outliers from then on)
        st = nodes.NodeStore(cap=4)
        for k, z in enumerate([0.0] * 3 + [0.5] * 8):
            st.add(z, 0.01 ** 2, k)
            if k == 5:
                self.assertEqual((st.agg_n, st.n_dropped, st.outputs()["z"]), (2, 0, 0.0))
        o = st.outputs()
        self.assertEqual((o["n"], o["n_in"], o["n_out"], st.n_dropped, st.agg_n), (11, 8, 3, 2, 5))
        self.assertAlmostEqual(o["z"], 0.5, places=12)


class TestGrowth(unittest.TestCase):
    def _surf(self, name, dz, frame="A", seed=0):
        p = make_crossing_survey(name, [0.0] * 8, noise=0.005, frame=frame, seed=seed)
        p.z = p.z + dz
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        return nodes.grid_surface([p], [np.full(len(p), 0.005)], g, capture_radius=0.75, name=name, frame=frame)

    def test_difference_and_volume(self):
        a = self._surf("a", 0.0); b = self._surf("b", 0.05, seed=2)
        gr = growth.difference(a, b)
        st = gr.stats()
        self.assertAlmostEqual(st["mean"], 0.05, delta=0.005)
        self.assertGreater(st["sigma_median"], 0.005)
        vol = growth.volume(gr.dz, gr.mask, a.grid.cell)
        self.assertAlmostEqual(vol, 0.05 * gr.mask.sum() * 0.25, delta=0.05 * gr.mask.sum() * 0.25 * 0.1)

    def test_refuses_frames_unless_offset(self):
        a = self._surf("a", 0.0, frame="A"); b = self._surf("b", 1.2, frame="B", seed=2)
        with self.assertRaises(ValueError):
            growth.difference(a, b)
        gr = growth.difference(a, b, offset=1.2)
        self.assertAlmostEqual(gr.stats()["mean"], 0.0, delta=0.005)

    def test_mask_fill_relief(self):
        a = self._surf("a", 0.0)
        ring = np.array([[2, 2], [8, 2], [8, 8], [2, 8]], float)
        m = a.inside_mask(ring)
        X, Y = np.meshgrid(a.grid.xs, a.grid.ys)
        self.assertTrue(m[(X == 5) & (Y == 5)].all())
        self.assertFalse(m[(X == 0) & (Y == 0)].any())
        fill, level, vol = growth.fill_to_level(a, m & (a.n > 0), level=None, margin=0.05)
        self.assertGreater(vol, 0)
        self.assertTrue(np.nanmin(fill) >= 0)
        fill2, level2, _ = growth.fill_to_level(a, m & (a.n > 0), level=level + 0.1)
        self.assertAlmostEqual(np.nanmax(fill2) - np.nanmax(fill), 0.1)
        r = growth.relief(a, m & (a.n > 0))
        self.assertGreater(r["range"], 0.1)

    def test_stats_include_datum(self):
        a = self._surf("a", 0.0); b = self._surf("b", 0.05, seed=2)
        a.datum_sigma = 0.03; b.datum_sigma = 0.04
        gr = growth.difference(a, b)
        st = gr.stats()
        self.assertAlmostEqual(st["sigma_of_mean"], np.sqrt(st["sigma_of_mean_noise"]**2 + 0.05**2))
        self.assertAlmostEqual(st["sigma_of_wmean"], np.sqrt(st["sigma_of_wmean_noise"]**2 + 0.05**2))
        sig = gr.sigma[gr.mask]
        self.assertAlmostEqual(st["sigma_of_mean_noise"], np.sqrt(gr.node_factor * np.sum(sig**2)) / len(sig))
        self.assertGreater(gr.node_factor, 1.0)
        ring = np.array([[2, 2], [8, 2], [8, 8], [2, 8]], float)
        p = make_crossing_survey("p", [0.0] * 8)
        r = growth.point_relief([p], ring)
        self.assertGreater(r["n"], 0)
        self.assertLess(r["n"], len(p))

    def test_point_in_polygon(self):
        ring = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
        self.assertTrue(growth.point_in_polygon([0.5], [0.5], ring)[0])
        self.assertFalse(growth.point_in_polygon([1.5], [0.5], ring)[0])


class TestCli(unittest.TestCase):
    def test_end_to_end(self):
        tmp = tempfile.TemporaryDirectory()
        d = Path(tmp.name)
        try:
            # two synthetic surveys as SW Maps GeoJSON at the synthetic site, b 4 cm above a
            def write(name, dz, seed):
                p = make_crossing_survey(name, [0.01, -0.01, 0.0, 0.02, -0.02, 0.0], seed=seed)
                la, lo = io.utm_inverse(500000.0 + p.x, p.y, 31)
                feats = [{"type": "Feature", "properties": {"REMARKS": ""},
                          "geometry": {"type": "Point", "coordinates": [float(o), float(a), float(z + dz)]}}
                         for o, a, z in zip(lo, la, p.z)]
                pla, plo = io.utm_inverse(500000.0 + np.array([1, 9, 9, 1.0]), np.array([1, 1, 9, 9.0]), 31)
                feats.append({"type": "Feature", "properties": {"NAME": "rink"},
                              "geometry": {"type": "Polygon", "coordinates": [[[float(o), float(a), 0.0] for o, a in zip(plo, pla)]]}})
                (d / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
            write("a", 0.0, 1); write("b", 0.04, 2)
            (d / "cfg.toml").write_text(f"""
[site]
utm_zone = 31
origin = [500000.0, 0.0]
[defaults]
sigma_z = 0.005
sigma_h = 0.005
wander_prior = 0.03
[grid]
cell = 0.5
capture_radius = 0.75
[outline]
path = "a.geojson"
name = "rink"
[output]
dir = "out"
[[surveys]]
id = "a"
date = "2026-01-01"
frame = "A"
path = "a.geojson"
[surveys.pass_rule]
jump_m = 1.5
[[surveys]]
id = "b"
date = "2026-02-01"
frame = "A"
path = "b.geojson"
[surveys.pass_rule]
jump_m = 1.5
[[growth]]
from = "a"
to = "b"
[[fill]]
surface = "a"
""")
            rc = cli.main(["run", str(d / "cfg.toml")])
            self.assertEqual(rc, 0)
            out = d / "out"
            self.assertTrue((out / "summary.md").exists())
            self.assertTrue((out / "growth_a_b.png").exists())
            self.assertTrue((out / "z_a.png").exists())
            txt = (out / "summary.md").read_text()
            self.assertIn("Growth a -> b", txt)
            g = np.load(out / "growth_a_b.npz")
            self.assertAlmostEqual(float(np.mean(g["dz"][g["mask"]])), 0.04, delta=0.01)
            # the untimed survey was segmented by sequence jumps: lines end 10 m apart
            self.assertIn("| a | 5 |", (out / "passes_a.md").read_text())
            # node heights are within sigma of the planted surface (biases removed by the crossover)
            sa = np.load(out / "surface_a.npz")
            self.assertEqual(sa["planar"].shape, sa["z"].shape)
            X, Y = np.meshgrid(sa["x0"] + np.arange(sa["nx"]) * sa["cell"], sa["y0"] + np.arange(sa["ny"]) * sa["cell"])
            m = sa["n"] > 0
            err = np.abs(sa["z"] - surface_fn(X, Y))[m]
            self.assertGreater(np.mean(err < 3 * sa["sigma"][m]), 0.95)
            # --set overrides a config value (arguments take priority)
            rc = cli.main(["run", str(d / "cfg.toml"), "--out", str(d / "out3"), "--set", "grid.cell=1.0",
                           "--set", "defaults.wander_prior=0.02"])
            self.assertEqual(rc, 0)
            self.assertEqual(float(np.load(d / "out3" / "surface_a.npz")["cell"]), 1.0)
            self.assertIn("prior 2.0 cm", (d / "out3" / "summary.md").read_text())
            # frame mismatch in a growth pair is refused
            cfg = cli.load_config(d / "cfg.toml")
            cfg["surveys"][1]["frame"] = "B"
            with self.assertRaises(ValueError):
                cli.run(cfg, str(d / "out2"), log=lambda *a: None)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
