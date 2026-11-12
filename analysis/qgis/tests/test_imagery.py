"""fetch_imagery.py transform math (no network, no rasterio): the transverse Mercator against
EPSG's worked example and pyproj, the State Plane and UTM definitions, the datum shift
direction, the local frame round trip, grids and resampling. Synthetic points only: EPSG's
documented example (Great Britain), made-up points near 0 N 0 E, and generic points in the
State Plane zone that are not any site.
Run: python3 -m unittest discover -s analysis/qgis/tests

numpy is required (skipped without it); the pyproj checks run only where pyproj is installed."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy as np
    import fetch_imagery as fi
except ImportError:   # numpy missing
    np = fi = None

try:
    import pyproj
except ImportError:
    pyproj = None

from local_frame import LocalFrame  # noqa: E402

ORIGIN = (0.00031, 0.00047, 21.5)   # made up, as in test_local_frame


@unittest.skipIf(np is None, "numpy not installed")
class TransverseMercatorTest(unittest.TestCase):
    def test_epsg_guidance_note_example(self):
        """EPSG Guidance Note 7-2, transverse Mercator example (OSGB 1936 / British National
        Grid): 50d30'N 0d30'E -> E 577274.99, N 69740.50 m (to the note's 2 decimals)."""
        tm = fi.TransverseMercator(6377563.396, 1 / 299.3249646, 49.0, -2.0, 0.9996012717,
                                   400000.0, -100000.0)
        e, n = tm.forward(50.5, 0.5)
        self.assertAlmostEqual(float(e), 577274.99, delta=0.02)
        self.assertAlmostEqual(float(n), 69740.50, delta=0.02)
        lat, lon = tm.inverse(e, n)
        self.assertAlmostEqual(float(lat), 50.5, places=9)
        self.assertAlmostEqual(float(lon), 0.5, places=9)

    def test_state_plane_definition(self):
        """NH State Plane: natural origin maps to (984250 ftUS, 0), units are US survey feet,
        the central meridian is a straight north line and k0 sets the scale there."""
        sp = fi.nh_state_plane()
        x, y = sp.forward(42.5, -71.6666666666667)
        self.assertAlmostEqual(float(x), 300000.0 / fi.US_FT, places=4)
        self.assertAlmostEqual(float(x), 984250.0, places=3)
        self.assertAlmostEqual(float(y), 0.0, places=4)
        _, y1 = sp.forward(42.51, -71.6666666666667)
        x2, _ = sp.forward(42.51, -71.6666666666667)
        self.assertAlmostEqual(float(x2), 984250.0, places=4)
        # 0.01 deg of latitude at 42.5 N is 1111.0 m of meridian (GRS80), times k0
        self.assertAlmostEqual(float(y1) * fi.US_FT, 1111.0 * 0.999966667, delta=0.5)

    def test_round_trip_arrays(self):
        sp = fi.nh_state_plane()
        lat = np.array([[42.7, 43.2], [44.4, 45.1]])
        lon = np.array([[-72.4, -71.0], [-71.9, -70.9]])
        x, y = sp.forward(lat, lon)
        self.assertEqual(x.shape, (2, 2))
        la, lo = sp.inverse(x, y)
        self.assertLess(np.abs(la - lat).max(), 1e-9)
        self.assertLess(np.abs(lo - lon).max(), 1e-9)

    @unittest.skipIf(pyproj is None, "pyproj not installed")
    def test_against_pyproj(self):
        """Same-datum projection only (NAD83(HARN) geographic -> EPSG:3445, NAD83 -> UTM 19N)."""
        from pyproj import Transformer
        lat = np.array([42.75, 43.3, 44.2, 45.0, 42.9])
        lon = np.array([-72.5, -71.0, -71.6666666666667, -70.9, -72.1])
        for wkid, src, proj in ((3445, 4152, fi.nh_state_plane()), (26919, 4269, fi.utm_north(19))):
            x, y = proj.forward(lat, lon)
            X, Y = Transformer.from_crs(src, wkid, always_xy=True).transform(lon, lat)
            self.assertLess(np.abs(x - X).max(), 1e-4, wkid)
            self.assertLess(np.abs(y - Y).max(), 1e-4, wkid)

    def test_known_references(self):
        for wkid in (3445, 3437, 102710, 26919):
            self.assertIn(wkid, fi.KNOWN_SR)
        self.assertEqual(fi.KNOWN_SR[3445]().unit, fi.US_FT)
        self.assertEqual(fi.KNOWN_SR[26919]().unit, 1.0)


@unittest.skipIf(np is None, "numpy not installed")
class LocalFrameTest(unittest.TestCase):
    def setUp(self):
        self.fr = LocalFrame(*ORIGIN, name="T")

    def test_vectorised_matches_scalar(self):
        xs = np.array([0.0, 35.2, -80.0, 120.5])
        ys = np.array([0.0, -12.0, 64.1, 99.9])
        lat, lon = fi.local_to_geodetic(self.fr, xs, ys)
        for i in range(4):
            la, lo, _ = self.fr.geodetic(xs[i], ys[i], 0.0)
            self.assertAlmostEqual(float(lat[i]), la, places=11)
            self.assertAlmostEqual(float(lon[i]), lo, places=11)

    def test_round_trip(self):
        lat, lon = fi.local_to_geodetic(self.fr, np.array([12.34]), np.array([-56.78]))
        x, y, _ = self.fr.enu(float(lat[0]), float(lon[0]), ORIGIN[2])
        self.assertAlmostEqual(x, 12.34, places=3)   # z = 0 on the tangent plane: < 1 mm
        self.assertAlmostEqual(y, -56.78, places=3)

    def test_datum_shift_direction(self):
        """Image datum minus our frame = (+0.561 E, -1.193 N): the image content for local p
        is at p + shift in the image datum, i.e. the image is moved by -shift; an extra
        shift moves it the other way."""
        utm = fi.utm_north(31)   # zone 31 holds 0 E: a projection without site meaning
        x0, y0 = utm.forward(*fi.local_to_geodetic(self.fr, np.array(5.0), np.array(7.0)))
        x1, y1 = fi.local_to_source(self.fr, utm, 5.0, 7.0, datum_shift=(0.561, -1.193))
        self.assertAlmostEqual(float(x1 - x0), 0.561, delta=0.002)
        self.assertAlmostEqual(float(y1 - y0), -1.193, delta=0.002)
        x2, y2 = fi.local_to_source(self.fr, utm, 5.0, 7.0, datum_shift=(0.561, -1.193),
                                    extra_shift=(0.561, -1.193))
        self.assertAlmostEqual(float(x2 - x0), 0.0, delta=0.002)
        self.assertAlmostEqual(float(y2 - y0), 0.0, delta=0.002)


@unittest.skipIf(np is None, "numpy not installed")
class GridTest(unittest.TestCase):
    def test_tiles_cover_within_limits(self):
        t = fi.tiles(9000, 5000, 4000, 4000)
        self.assertEqual(len(t), 6)
        self.assertTrue(all(w <= 4000 and h <= 4000 for _, _, w, h in t))
        self.assertEqual(sum(w * h for _, _, w, h in t), 9000 * 5000)

    def test_output_grid_snaps(self):
        xl, yt, nc, nr = fi.output_grid((-10.07, -5.01, 20.02, 9.99), 0.3)
        self.assertAlmostEqual(xl, -10.2)
        self.assertAlmostEqual(yt, 10.2)
        self.assertEqual((nc, nr), (101, 51))

    def test_bilinear(self):
        src = np.arange(16, dtype=np.uint8).reshape(1, 4, 4) * 10
        valid = np.ones((4, 4), bool)
        out, ok = fi.bilinear(src, valid, np.array([0.5, 2.0, 3.5]), np.array([0.0, 1.5, 0.0]))
        self.assertAlmostEqual(float(out[0, 0]), 5.0)
        self.assertAlmostEqual(float(out[0, 1]), 80.0)
        self.assertEqual(list(ok), [True, True, False])   # 3.5 has no right neighbour

    def test_resample_moves_image_by_minus_datum_shift(self):
        """A bright dot at local (0, 0) in a synthetic 'NAD83' image shows up at -shift
        after resampling: the copy lands in our frame."""
        fr = LocalFrame(*ORIGIN, name="T")
        utm = fi.utm_north(31)
        shift = (0.6, -1.2)
        cx, cy = utm.forward(*fi.local_to_geodetic(fr, np.array(0.0), np.array(0.0)))
        p = 0.1
        xmin, ymax = float(cx) - 5.0, float(cy) + 5.0
        src = np.zeros((1, 100, 100), np.uint8)
        src[0, 50, 50] = 255           # the pixel whose centre is (xmin + 5.05, ymax - 5.05)
        valid = np.ones((100, 100), bool)
        out, ok, (xl, yt) = fi.resample(fr, utm, src, valid, xmin, ymax, p, (-3, -3, 3, 3), p,
                                        datum_shift=shift)
        r, c = np.unravel_index(np.argmax(out[0]), out[0].shape)
        x = xl + (c + 0.5) * p
        y = yt - (r + 0.5) * p
        self.assertAlmostEqual(x, 0.05 - shift[0], delta=0.11)
        self.assertAlmostEqual(y, -0.05 - shift[1], delta=0.11)
        self.assertTrue(ok.all())

    def test_source_window_snapped(self):
        fr = LocalFrame(*ORIGIN, name="T")
        utm = fi.utm_north(31)
        w = fi.source_window(fr, utm, (-50, -40, 60, 70), 0.5, (100000.25, 0.25), (0, 0), (0, 0))
        for v, g in zip(w, (100000.25, 0.25, 100000.25, 0.25)):
            self.assertAlmostEqual(((v - g) / 0.5) % 1.0, 0.0, places=6)
        self.assertGreater(w[2] - w[0], 110)
        self.assertLess(w[2] - w[0], 115)


@unittest.skipIf(np is None, "numpy not installed")
class MetadataTest(unittest.TestCase):
    def test_years(self):
        self.assertEqual(fi.years_from_name("NH_2021_2022_6in_RGB"), [2021, 2022])
        self.assertEqual(fi.years_from_name("Coastal2016_1ft_RGB"), [2016])
        self.assertEqual(fi.years_from_name("NH_DOQs_92_98"), [1992, 1998])

    def test_pixel_and_rule(self):
        m = {"spatialReference": {"wkid": 3445}, "pixelSizeX": 0.5, "pixelType": "U8"}
        self.assertAlmostEqual(fi.output_pixel_m(m), 0.15)
        self.assertIsNone(fi.pick_rendering_rule(m, None))
        dem = {"pixelType": "F32", "rasterFunctionInfos": [{"name": "None"}, {"name": "Shaded Relief"}]}
        self.assertEqual(fi.pick_rendering_rule(dem, None), "Shaded Relief")
        self.assertIsNone(fi.pick_rendering_rule(dem, ""))      # configured "" = raw pixels

    def test_settings_priority(self):
        class A:
            gpkg = out_dir = service = datasets = bbox = extent_layer = margin = None
            datum_shift = None
            shift = [["X", "0.1", "-0.2"]]
        cfg = {"season": {"gpkg": "q/s.gpkg"},
               "imagery": {"datum_shift_m": [0.5, -1.0], "extra_shift_m": {"X": [9, 9], "Y": [1, 2]}}}
        s = fi.settings(A(), cfg, Path("/base"))
        self.assertEqual(s["datum_shift"], (0.5, -1.0))
        self.assertEqual(s["extra_shift"], {"X": (0.1, -0.2), "Y": (1.0, 2.0)})
        self.assertEqual(Path(s["out_dir"]), Path("/base/q/imagery"))
        self.assertEqual(s["margin_m"], 30.0)


if __name__ == "__main__":
    unittest.main()
