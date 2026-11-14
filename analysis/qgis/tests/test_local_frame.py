"""Local ENU frame and display CRS, with a made-up origin near 0 N 0 E (never a real site).
Run: python3 -m unittest discover -s analysis/qgis/tests

The pyproj checks run only where pyproj is installed (it is not needed by the scripts)."""
import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_frame import A, E2, LocalFrame, geodetic_to_ecef  # noqa: E402

try:
    import pyproj
except ImportError:
    pyproj = None

ORIGIN = (0.00031, 0.00047, 21.5)   # made up: lat, lon (degrees), ellipsoidal h (m)


class TestENU(unittest.TestCase):
    def setUp(self):
        self.fr = LocalFrame(*ORIGIN, name="T", source="test")

    def test_origin_is_zero(self):
        for v in self.fr.enu(*ORIGIN):
            self.assertAlmostEqual(v, 0.0, places=7)

    def test_straight_up(self):
        x, y, z = self.fr.enu(ORIGIN[0], ORIGIN[1], ORIGIN[2] + 5.0)
        self.assertAlmostEqual(x, 0.0, places=7)
        self.assertAlmostEqual(y, 0.0, places=7)
        self.assertAlmostEqual(z, 5.0, places=7)

    def test_known_offset_along_parallel(self):
        """Same lat and h, dlon east: closed form on the ellipsoid."""
        lat, lon, h = ORIGIN
        dlon = 0.0009
        nu = A / math.sqrt(1 - E2 * math.sin(math.radians(lat)) ** 2)
        r = (nu + h) * math.cos(math.radians(lat))         # radius of the parallel
        x, y, z = self.fr.enu(lat, lon + dlon, h)
        a = math.radians(dlon)
        self.assertAlmostEqual(x, r * math.sin(a), places=6)
        self.assertAlmostEqual(z, -r * (1 - math.cos(a)) * math.cos(math.radians(lat)), places=6)
        self.assertAlmostEqual(y, -r * (1 - math.cos(a)) * -math.sin(math.radians(lat)), places=6)
        self.assertAlmostEqual(x, 100.19, delta=0.01)       # about 100 m east

    def test_round_trip(self):
        for e, n, u in [(100.0, 0.0, 0.0), (-70.25, 70.5, 5.0), (30.0, -40.0, -6.0), (0.001, 0.002, 0.003)]:
            lat, lon, h = self.fr.geodetic(e, n, u)
            got = self.fr.enu(lat, lon, h)
            for g, w in zip(got, (e, n, u)):
                self.assertAlmostEqual(g, w, places=6)     # micrometres

    def test_distance_is_ecef_distance(self):
        p = self.fr.geodetic(12.0, -8.0, 1.5)
        q = self.fr.geodetic(-3.0, 40.0, -2.0)
        a, b = self.fr.enu(*p), self.fr.enu(*q)
        xa, xb = geodetic_to_ecef(*p), geodetic_to_ecef(*q)
        self.assertAlmostEqual(math.dist(a, b), math.dist(xa, xb), places=7)

    def test_key_changes_with_origin(self):
        self.assertEqual(self.fr.key(), LocalFrame(*ORIGIN).key())
        self.assertNotEqual(self.fr.key(), LocalFrame(ORIGIN[0] + 1e-7, ORIGIN[1], ORIGIN[2]).key())


class TestCRS(unittest.TestCase):
    def setUp(self):
        self.fr = LocalFrame(*ORIGIN, name="T")

    def test_wkt2_text(self):
        w = self.fr.wkt2()
        self.assertTrue(w.startswith('PROJCRS["Ice Cube local ENU (origin T)",BASEGEOGCRS["WGS 84"'))
        self.assertIn('METHOD["Orthographic",ID["EPSG",9840]]', w)
        self.assertIn('PARAMETER["Latitude of natural origin",0.00031,', w)
        self.assertIn('PARAMETER["Longitude of natural origin",0.00047,', w)
        self.assertIn('AXIS["easting (X)",east', w)
        self.assertIn('AXIS["northing (Y)",north', w)
        self.assertEqual(w.count("["), w.count("]"))
        self.assertIsNone(re.search(r"\de-\d", w))          # no exponents in numbers
        self.assertIn("+proj=ortho +lat_0=0.00031 +lon_0=0.00047", self.fr.proj_string())
        self.assertTrue(self.fr.proj_string().endswith("+type=crs"))
        self.assertIn('PROJECTION["Orthographic"]', self.fr.wkt1())

    @unittest.skipIf(pyproj is None, "pyproj not installed")
    def test_wkt_with_proj(self):
        to = lambda d: pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_user_input(d), always_xy=True)
        for d in (self.fr.wkt2(), self.fr.wkt1(), self.fr.proj_string(), self.fr.tmerc_proj_string()):
            t = to(d)
            x, y = t.transform(ORIGIN[1], ORIGIN[0])
            self.assertLess(math.hypot(x, y), 1e-6)
            for e, n, u in [(100.0, 0.0, 0.0), (0.0, 100.0, 0.0), (-70.0, 70.0, 5.0), (30.0, -40.0, -6.0)]:
                lat, lon, h = self.fr.geodetic(e, n, u)
                px, py = t.transform(lon, lat)
                ex, ey, _ = self.fr.enu(lat, lon, h)
                self.assertLess(math.hypot(px - ex, py - ey), 0.001)   # < 1 mm within 100 m
        c = pyproj.CRS.from_wkt(self.fr.wkt2())
        self.assertEqual(c.name, "Ice Cube local ENU (origin T)")
        self.assertEqual(c.coordinate_operation.method_name, "Orthographic")
        self.assertEqual([a.direction for a in c.axis_info], ["east", "north"])

    @unittest.skipIf(pyproj is None, "pyproj not installed")
    def test_orthographic_is_enu_on_the_ellipsoid(self):
        """At h = 0 (origin and point) the orthographic x, y are the exact ENU x, y."""
        fr0 = LocalFrame(ORIGIN[0], ORIGIN[1], 0.0)
        t = pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_wkt(fr0.wkt2()), always_xy=True)
        for e, n in [(100.0, 0.0), (-300.0, 400.0)]:
            lat, lon, _ = fr0.geodetic(e, n, 0.0)
            px, py = t.transform(lon, lat)
            ex, ey, _ = fr0.enu(lat, lon, 0.0)
            self.assertLess(math.hypot(px - ex, py - ey), 1e-6)


if __name__ == "__main__":
    unittest.main()
