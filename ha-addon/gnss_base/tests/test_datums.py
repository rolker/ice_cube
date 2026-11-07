"""The datums module: frame transformations, their sign conventions and the expected
offset of the reference check. Synthetic points only (an ocean point, the Alaskan and
Australian coasts, a generic New England latitude), nowhere near any real site.

Run from ha-addon/gnss_base with: python -m unittest tests.test_datums
"""
import math
import unittest

from gnss_base import datums
from gnss_base.datums import (FRAMES, GRS80, WGS84, Helmert, decimal_year, ecef_to_enu,
                              ecef_to_geodetic, expected_offset_enu, geodetic_to_ecef,
                              parse_epoch, parse_frame, transform)

# What the earlier private script (numpy, the same NGS and IERS parameter sets) printed
# as "ITRF2020 - NAD83(2011) dE dN dU" for a point given in NAD83(2011) geodetic
# coordinates on GRS80: (lat, lon, height, epoch) -> (dE, dN, dU) in metres, 3 decimals.
SCRIPT_VALUES = [
    ((45.0, -70.0, 100.0, 2026.85), (-0.555, +1.250, -1.119)),
    ((45.0, -70.0, 100.0, 2010.0), (-0.259, +1.122, -1.131)),
    ((45.0, -70.0, 100.0, 1997.0), (-0.030, +1.023, -1.140)),
    ((0.0, 0.0, 0.0, 2026.85), (+2.233, +0.970, -1.009)),
    ((-33.9, 151.2, 50.0, 2020.5), (-1.407, +1.652, +1.198)),
    ((43.0, -71.0, 50.0, 2026.85), (-0.560, +1.194, -1.182)),
    ((60.0, -150.0, 0.0, 2026.85), (-1.548, -0.432, +0.453)),
]


class Geodetic(unittest.TestCase):
    def test_round_trip_and_ellipsoids(self):
        for lat, lon, height in ((0.0, 0.0, 0.0), (43.0, -71.0, 50.0), (-33.9, 151.2, 50.0),
                                 (89.9, 10.0, -100.0), (-60.0, -179.9, 3000.0)):
            for ellipsoid in (GRS80, WGS84):
                xyz = geodetic_to_ecef(lat, lon, height, ellipsoid)
                back = ecef_to_geodetic(*xyz, ellipsoid=ellipsoid)
                self.assertAlmostEqual(back[0], lat, places=9)
                self.assertAlmostEqual(back[1], lon, places=9)
                self.assertAlmostEqual(back[2], height, places=5)
        # The two ellipsoids differ by a tenth of a millimetre at most.
        xyz = geodetic_to_ecef(43.0, -71.0, 50.0, GRS80)
        self.assertLess(abs(ecef_to_geodetic(*xyz, ellipsoid=WGS84)[2] - 50.0), 0.0002)

    def test_enu_is_a_rotation(self):
        origin = geodetic_to_ecef(43.0, -71.0, 50.0)
        up = geodetic_to_ecef(43.0, -71.0, 51.0)
        self.assertAlmostEqual(ecef_to_enu(origin, up)[2], 1.0, places=6)
        self.assertLess(math.hypot(*ecef_to_enu(origin, up)[:2]), 1e-6)
        north = geodetic_to_ecef(43.00001, -71.0, 50.0)
        e, n, u = ecef_to_enu(origin, north)
        self.assertGreater(n, 1.0)
        self.assertLess(abs(e), 1e-6)
        self.assertLess(abs(u), 1e-4)
        east = geodetic_to_ecef(43.0, -70.99999, 50.0)
        self.assertGreater(ecef_to_enu(origin, east)[0], 0.5)
        self.assertEqual(ecef_to_enu(origin, origin), (0.0, 0.0, 0.0))


class Transformations(unittest.TestCase):
    def test_reproduces_the_private_script(self):
        """The script started from a NAD83(2011) point, found the ITRF2020 coordinate
        whose transformation gives it, and printed ITRF2020 minus NAD83(2011) in ENU;
        the module's offset is the station's frame minus ours: the same numbers, the
        other sign."""
        for (lat, lon, height, epoch), (de, dn, du) in SCRIPT_VALUES:
            nad = geodetic_to_ecef(lat, lon, height)
            ours = datums.nad83_2011_to_itrf2020(nad, epoch)
            back = datums.itrf2020_to_nad83_2011(ours, epoch)
            self.assertLess(math.dist(back, nad), 1e-6, "the inverse is exact")
            offset = expected_offset_enu(ours, "NAD83(2011)", epoch)
            for got, want in zip(offset, (-de, -dn, -du)):
                self.assertAlmostEqual(got, want, delta=0.0006, msg=((lat, lon, epoch), offset))
            # Both transform directions in one call.
            self.assertLess(math.dist(transform(nad, "NAD83(2011)", "ITRF2020", epoch), ours),
                            1e-9)

    def test_sign_in_new_england(self):
        """At a New England latitude the station's NAD83(2011) frame is east, south and up
        of ITRF2020 (about +0.56, -1.19, +1.18 m in 2026), which is the sign the
        reference check measured against a real network."""
        ours = geodetic_to_ecef(43.0, -71.0, 50.0)
        e, n, u = expected_offset_enu(ours, "NAD83(2011)", 2026.85)
        self.assertTrue(0.5 < e < 0.65 and -1.3 < n < -1.1 and 1.05 < u < 1.3, (e, n, u))

    def test_identity_and_small_itrf_differences(self):
        ours = geodetic_to_ecef(43.0, -71.0, 50.0)
        self.assertEqual(expected_offset_enu(ours, "ITRF2020", 2026.85), (0.0, 0.0, 0.0))
        self.assertEqual(transform(ours, "ITRF2020", "ITRF2020", 2026.85), tuple(ours))
        self.assertLess(math.dist(transform(ours, "NAD83(2011)", "NAD83(2011)", 2026.85), ours),
                        1e-6, "to ITRF2020 and back")
        for frame in ("ITRF2014", "ITRF2008"):
            offset = expected_offset_enu(ours, frame, 2026.85)
            self.assertLess(max(abs(v) for v in offset), 0.02, (frame, offset))
            self.assertGreater(max(abs(v) for v in offset), 0.0005, (frame, offset))
        # ITRF2020 -> ITRF2008 at its own epoch is the translation plus the scale.
        t = (0.0002, 0.0010, 0.0033)
        image = datums.itrf2020_to_itrf2008(ours, 2015.0)
        for got, v, ti in zip(image, ours, t):
            self.assertAlmostEqual(got, ti + (1 - 0.29e-9) * v, places=7)
        # The same frame as ours in any spelling is the zero offset.
        self.assertEqual(expected_offset_enu(ours, "NAD83(2011)", 2026.85, "NAD83(2011)"),
                         (0.0, 0.0, 0.0))

    def test_helmert_conventions(self):
        """A pure rotation about Z moves a point on the X axis towards +Y in the IERS
        convention and towards -Y in the NGS one; a translation is just added; the rates
        apply from the reference epoch."""
        x = (6_378_137.0, 0.0, 0.0)
        iers = Helmert("t", 2000.0, (0, 0, 0), (0, 0, 0), (0, 0, 1000.0), (0, 0, 0), 0, 0, +1, "")
        ngs = Helmert("t", 2000.0, (0, 0, 0), (0, 0, 0), (0, 0, 1000.0), (0, 0, 0), 0, 0, -1, "")
        self.assertAlmostEqual(iers.forward(x, 2000.0)[1], 6_378_137.0 * 1000.0 * datums.MAS)
        self.assertAlmostEqual(ngs.forward(x, 2000.0)[1], -6_378_137.0 * 1000.0 * datums.MAS)
        moving = Helmert("t", 2000.0, (1.0, 2.0, 3.0), (0.1, 0.0, -0.1), (0, 0, 0), (0, 0, 0),
                         1000.0, 0.0, +1, "")
        self.assertEqual(moving.at(2010.0)[0], (2.0, 2.0, 2.0))
        self.assertAlmostEqual(moving.forward(x, 2010.0)[0], 2.0 + 6_378_137.0 * (1 + 1e-6))
        self.assertLess(math.dist(moving.inverse(moving.forward(x, 2010.0), 2010.0), x), 1e-9)
        for parameters in (datums.ITRF2020_TO_ITRF2014, datums.ITRF2020_TO_ITRF2008,
                           datums.ITRF2008_TO_NAD83_2011):
            self.assertTrue(parameters.source, parameters.name)
            self.assertIn(parameters.sign, (+1, -1))
        self.assertEqual(datums.ITRF2008_TO_NAD83_2011.sign, -1, "HTDP convention")
        self.assertEqual(datums.ITRF2020_TO_ITRF2008.sign, +1, "IERS convention")

    def test_frames_and_bad_names(self):
        self.assertEqual(parse_frame("NAD83(2011)"), "NAD83(2011)")
        self.assertEqual(parse_frame(" nad83 (2011) "), "NAD83(2011)")
        self.assertEqual(parse_frame("itrf2020"), "ITRF2020")
        self.assertEqual(parse_frame(""), "")
        self.assertEqual(parse_frame("  "), "")
        self.assertEqual(parse_frame(None), "")
        for bad in ("NAD83", "ITRF2000", "WGS84", "ETRS89", "NAD83(2011) epoch 2010", 42):
            with self.assertRaises(ValueError) as caught:
                parse_frame(bad, "reference_frame")
            self.assertIn("reference_frame must be", str(caught.exception))
            self.assertNotIn("WGS84", str(caught.exception).replace(", ".join(FRAMES), ""))
        with self.assertRaises(ValueError):
            transform((1.0, 2.0, 3.0), "WGS84", "ITRF2020", 2020.0)
        with self.assertRaises(ValueError):
            transform((1.0, 2.0, 3.0), "ITRF2020", "", 2020.0)
        self.assertEqual(FRAMES, ("ITRF2020", "ITRF2014", "ITRF2008", "NAD83(2011)"))

    def test_parse_epoch(self):
        self.assertIsNone(parse_epoch(""))
        self.assertIsNone(parse_epoch(None))
        self.assertIsNone(parse_epoch("  "))
        self.assertEqual(round(parse_epoch("2026-11-06"), 2), 2026.85)
        self.assertEqual(parse_epoch(" 2026.85 "), 2026.85)
        self.assertEqual(parse_epoch("2010"), 2010.0)
        for bad in ("yesterday", "2026-13-01", "06/11/2026", "nan", "inf", "1900", "2200", 42):
            with self.assertRaises(ValueError) as caught:
                parse_epoch(bad, "base_epoch")
            self.assertIn("base_epoch must be", str(caught.exception))
            self.assertNotIn(str(bad), str(caught.exception).replace("YYYY", ""))

    def test_decimal_year(self):
        self.assertAlmostEqual(decimal_year(1_793_491_200.0), 2026 + 304 / 365, places=9)  # Nov 1
        self.assertEqual(round(decimal_year(1_793_491_200.0 + 5 * 86400), 2), 2026.85)  # Nov 6
        self.assertAlmostEqual(decimal_year(1_767_225_600.0), 2026.0, places=9)   # Jan 1 2026
        self.assertAlmostEqual(decimal_year(1_767_225_600.0 - 86400), 2025 + 364 / 365)
        self.assertAlmostEqual(decimal_year(1_735_689_600.0 - 1), 2025 - 1 / (366 * 86400),
                               places=9, msg="2024 has 366 days")


if __name__ == "__main__":
    unittest.main()
