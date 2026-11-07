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
        self.assertLess(np.mean(np.abs(err) < 3 * s.sigma[m]), 1.01)
        self.assertGreater(np.mean(np.abs(err) < 3 * s.sigma[m]), 0.95)
        self.assertEqual(int(s.flags.sum()), 0)

    def test_flag_fires_on_second_surface(self):
        p = make_crossing_survey("s", [0.0] * 8, noise=0.005, seed=8)
        # plant a second surface: the y-lines are 10 cm higher
        z = p.z.copy(); z[p.pass_id >= 4] += 0.10
        p.z = z
        g = nodes.Grid.from_extent(0, 10, 0, 10, 0.5)
        s = nodes.grid_surface([p], [np.full(len(p), 0.005)], g, capture_radius=0.75, slope=0.05, k_sigma=3.0)
        self.assertGreater(int((s.flags > 0).sum()), 10)
        # the two-surface nodes are where lines cross
        X, Y = np.meshgrid(g.xs, g.ys)
        on_cross = (np.abs(X % 2) < 1e-9) & (np.abs(Y % 2) < 1e-9) & (X <= 6) & (Y <= 6)
        self.assertGreater(np.mean(s.flags[on_cross & (s.n > 0)] > 0), 0.8)
        self.assertGreater(s.maxnr.max(), 3.0)
        from icecube import report
        self.assertIn("| east (m) |", report.table_flags(s))

    def test_refuses_frames_and_missing_sigma(self):
        a = make_survey("a", [0.0] * 6, frame="A"); b = make_survey("b", [0.0] * 6, frame="B")
        g = nodes.Grid.from_extent(0, 10, 0, 6, 0.5)
        with self.assertRaises(ValueError):
            nodes.grid_surface([a, b], [np.zeros(len(a)), np.zeros(len(b))], g)
        c = local_points("c", [0.0], [0.0], [0.0]); c.pass_id[:] = 0
        with self.assertRaises(ValueError):
            nodes.grid_surface([c], [np.zeros(1)], g)


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
        st = growth.difference(a, b).stats()
        self.assertAlmostEqual(st["sigma_of_mean"], np.sqrt(st["sigma_of_mean_noise"]**2 + 0.05**2))
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
            # frame mismatch in a growth pair is refused
            cfg = cli.load_config(d / "cfg.toml")
            cfg["surveys"][1]["frame"] = "B"
            with self.assertRaises(ValueError):
                cli.run(cfg, str(d / "out2"), log=lambda *a: None)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
