"""fetch_parcels.py on synthetic data (no network): field filtering that never keeps owner
names or addresses, Esri ring conversion, the touching test for the neighbour ring, the
NAD83 State Plane -> our frame transform and the direction of the datum shift (the same
move as the imagery), and the GeoJSON feature. Made-up points in the NH State Plane zone,
not any site. Run: python3 -m unittest discover -s analysis/qgis/tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy as np
    import fetch_imagery as fi
    import fetch_parcels as fp
except ImportError:   # numpy missing
    np = fi = fp = None

try:
    import rasterio
except ImportError:
    rasterio = None

from local_frame import LocalFrame  # noqa: E402

ORIGIN = (43.5, -71.5, 50.0)   # a made-up point inside the NH State Plane zone
SHIFT = (0.561, -1.193)        # NAD83 minus our frame, metres east, north


def square(x0, y0, s, clockwise=True):
    r = [[x0, y0], [x0, y0 + s], [x0 + s, y0 + s], [x0 + s, y0], [x0, y0]]
    return r if clockwise else r[::-1]


@unittest.skipIf(np is None, "numpy not installed")
class FieldsTest(unittest.TestCase):
    LAYER = ["OBJECTID", "Shape", "PID", "TownID", "Town", "DisplayId", "SLU", "SLUC",
             "StreetAddress", "Name", "OwnerName", "MailingAddress", "Shape_Area"]

    def test_requested_fields_never_personal(self):
        got = fp.allowed_fields(["pid", "Town", "Name", "StreetAddress", "OwnerName", "SLU", "Nope"],
                                self.LAYER)
        self.assertEqual(got, ["PID", "Town", "SLU"])
        self.assertEqual(fp.allowed_fields(fp.DEFAULT_FIELDS, self.LAYER),
                         ["PID", "DisplayId", "TownID", "Town", "SLU", "SLUC", "Shape_Area"])

    def test_returned_owner_fields_dropped(self):
        attrs = {"PID": "001-002-003", "Town": "Somewhere", "Name": "A PERSON",
                 "StreetAddress": "1 SOME RD", "OwnerName": "X", "Shape_Area": 10.0}
        clean = fp.clean_attributes(attrs, ["PID", "Town", "Name", "StreetAddress", "Shape_Area"])
        self.assertEqual(clean, {"PID": "001-002-003", "Town": "Somewhere", "Shape_Area": 10.0})

    def test_lot_label(self):
        self.assertEqual(fp.lot_label("004-012-007"), ("4", "12-7"))
        self.assertEqual(fp.lot_label("004-012-000"), ("4", "12"))
        self.assertEqual(fp.lot_label("004-012-"), ("4", "12"))
        self.assertEqual(fp.lot_label(None), ("", ""))


@unittest.skipIf(np is None, "numpy not installed")
class GeometryTest(unittest.TestCase):
    def test_rings_winding_and_holes(self):
        polys = fp.esri_rings_to_polygons([square(0, 0, 10), square(2, 2, 2, clockwise=False),
                                           square(20, 0, 5)])
        self.assertEqual(len(polys), 2)
        self.assertEqual(len(polys[0]), 2)                    # outer + hole
        self.assertGreater(fp.ring_area(polys[0][0]), 0)      # GeoJSON: outer counter-clockwise
        self.assertLess(fp.ring_area(polys[0][1]), 0)         # hole clockwise
        self.assertAlmostEqual(abs(fp.ring_area(polys[1][0])), 25.0)

    def test_touch(self):
        a = fp.esri_rings_to_polygons([square(0, 0, 10)])
        b = fp.esri_rings_to_polygons([square(10, 3, 4)])     # shares part of an edge, no shared vertex
        c = fp.esri_rings_to_polygons([square(12, 0, 4)])     # 2 units away
        self.assertTrue(fp.polygons_touch(a, b, 0.1))
        self.assertTrue(fp.polygons_touch(b, a, 0.1))
        self.assertFalse(fp.polygons_touch(a, c, 1.0))
        self.assertTrue(fp.polygons_touch(a, c, 2.5))


@unittest.skipIf(np is None, "numpy not installed")
class TransformTest(unittest.TestCase):
    def setUp(self):
        self.fr = LocalFrame(*ORIGIN)
        self.sp = fi.nh_state_plane()

    def native_of_local(self, x, y):
        """State Plane coordinates of local (x, y) with no datum difference."""
        return self.sp.forward(*fi.local_to_geodetic(self.fr, np.array([x]), np.array([y])))

    def local(self, lat, lon):
        return np.array(self.fr.enu(float(lat[0]), float(lon[0]), ORIGIN[2])[:2])

    def test_no_shift_round_trip(self):
        sx, sy = self.native_of_local(30.0, -40.0)
        lat, lon = fp.native_to_ours(self.fr, self.sp, sx, sy, (0.0, 0.0))
        np.testing.assert_allclose(self.local(lat, lon), [30.0, -40.0], atol=2e-3)

    def test_shift_direction_matches_imagery(self):
        """A NAD83 vertex read as ours at local q belongs at q - shift (E -0.561, N +1.193):
        the same move fetch_imagery.py gives the images."""
        sx, sy = self.native_of_local(30.0, -40.0)
        lat, lon = fp.native_to_ours(self.fr, self.sp, sx, sy, SHIFT)
        np.testing.assert_allclose(self.local(lat, lon), [30.0 - 0.561, -40.0 + 1.193], atol=2e-3)
        # and the imagery: the image content for local p sits at native(p + shift); a parcel
        # vertex at that native point therefore lands back on p
        ix, iy = fi.local_to_source(self.fr, self.sp, 5.0, 7.0, SHIFT)
        lat, lon = fp.native_to_ours(self.fr, self.sp, np.atleast_1d(ix), np.atleast_1d(iy), SHIFT)
        np.testing.assert_allclose(self.local(lat, lon), [5.0, 7.0], atol=2e-3)

    def test_feature(self):
        ring_local = [(0, 0), (0, 20), (30, 20), (30, 0), (0, 0)]   # clockwise, 600 m2
        ring = []
        for x, y in ring_local:
            sx, sy = self.native_of_local(x, y)
            ring.append([float(sx[0]), float(sy[0])])
        f = {"attributes": {"PID": "001-002-003", "Town": "Somewhere", "SLU": "11",
                            "Name": "A PERSON", "StreetAddress": "1 SOME RD"},
             "geometry": {"rings": [ring]}}
        s = {"fields": ["PID", "Town", "SLU"], "pid_field": "PID", "datum_shift": SHIFT,
             "stroke": "#808080", "source_name": "Test parcels", "frame_name": "our frame"}
        ft = fp.to_feature(f, "core", s, self.fr, self.sp, fi.US_FT, "2000-01-01")
        p = ft["properties"]
        self.assertEqual(ft["geometry"]["type"], "Polygon")
        self.assertEqual((p["pid"], p["label"], p["map"], p["lot"], p["kind"], p["group"]),
                         ("001-002-003", "001-002-003", "1", "2-3", "parcel", "core"))
        self.assertAlmostEqual(p["area_m2"], 600.0, delta=0.5)
        text = repr(ft)
        self.assertNotIn("PERSON", text)
        self.assertNotIn("SOME RD", text)
        self.assertIn("datum-shifted", p["source"])
        lon, lat = ft["geometry"]["coordinates"][0][0]
        x, y, _ = self.fr.enu(lat, lon, ORIGIN[2])
        self.assertAlmostEqual(x, 0 - 0.561, delta=0.003)
        self.assertAlmostEqual(y, 0 + 1.193, delta=0.003)


@unittest.skipIf(np is None or rasterio is None, "numpy or rasterio not installed")
class WriteTest(unittest.TestCase):
    def test_deflate_and_jpeg(self):
        fr = LocalFrame(*ORIGIN)
        arr = (np.random.default_rng(1).random((3, 300, 400)) * 255).astype(np.uint8)
        mask = np.ones((300, 400), bool)
        mask[:, :50] = False
        with tempfile.TemporaryDirectory() as d:
            for comp in ("deflate", "jpeg"):
                p = Path(d) / f"{comp}.tif"
                fi.write_geotiff(p, arr, mask, -10.0, 20.0, 0.15, fr.wkt2(), False, comp)
                with rasterio.open(p) as ds:
                    self.assertEqual(ds.compression.name.lower(), comp)
                    self.assertTrue(ds.overviews(1))
                    m = ds.dataset_mask()
                    self.assertEqual(int(m[:, :50].max()), 0)
                    self.assertEqual(int(m[:, 60:].min()), 255)


if __name__ == "__main__":
    unittest.main()
