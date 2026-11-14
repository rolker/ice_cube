"""GeoPackage writer: header, WKB, core tables, SRS, upserts and the RTree.
Run: python3 -m unittest discover -s analysis/qgis/tests"""
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gpkg_writer as gw  # noqa: E402
from local_frame import LocalFrame, ecef_to_geodetic, geodetic_to_ecef  # noqa: E402


class TestGeometry(unittest.TestCase):
    def test_point_header_and_wkb(self):
        b = gw.point(1.5, -2.25, 30.0)
        self.assertEqual(b[:2], b"GP")
        self.assertEqual(b[2], 0)                      # version 1.0 header
        self.assertEqual(b[3], 0x01)                   # little endian, no envelope, not empty
        self.assertEqual(struct.unpack_from("<i", b, 4)[0], 4326)
        bo, code = struct.unpack_from("<BI", b, 8)
        self.assertEqual((bo, code), (1, 1001))       # ISO WKB PointZ
        self.assertEqual(struct.unpack_from("<ddd", b, 13), (1.5, -2.25, 30.0))
        self.assertEqual(len(b), 8 + 5 + 24)
        self.assertEqual(struct.unpack_from("<I", gw.point(1, 2), 9)[0], 1)   # 2D Point

    def test_envelope_for_lines_and_polygons(self):
        b = gw.gpkg_geometry("LINESTRING", [(0, 0, 1), (2, -1, 1), (1, 3, 1)], 4326, z=True)
        self.assertEqual(b[3], 0x03)                   # envelope type 1 (xy)
        self.assertEqual(struct.unpack_from("<dddd", b, 8), (0, 2, -1, 3))
        self.assertEqual(struct.unpack_from("<I", b, 41)[0], 1002)
        srs, env, empty, off = gw.parse_header(b)
        self.assertEqual((srs, env, empty, off), (4326, (0, 2, -1, 3), False, 40))
        mp = gw.gpkg_geometry("MULTIPOLYGON", [[[(0, 0), (4, 0), (4, 5), (0, 0)]],
                                               [[(10, 10), (11, 10), (11, 11), (10, 10)]]], 4326)
        self.assertEqual(gw.envelope(mp), (0, 11, 0, 11))
        self.assertEqual(struct.unpack_from("<BII", mp, 40), (1, 6, 2))
        pts, end = gw._wkb_xy(mp, 40)
        self.assertEqual(len(pts), 8)
        self.assertEqual(end, len(mp))

    def test_point_envelope_from_wkb(self):
        self.assertEqual(gw.envelope(gw.point(3, 4, 5)), (3, 3, 4, 4))
        self.assertIsNone(gw.envelope(None))


class TestPackage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.gpkg"

    def tearDown(self):
        self.tmp.cleanup()

    def test_core_tables_and_pragmas(self):
        gp = gw.GeoPackage(self.path)
        gp.ensure_feature_table("pts", [("name", "TEXT"), ("note", "TEXT")], "POINT", z=True,
                                unique=("name",))
        gp.ensure_attributes_table("tab", [("k", "TEXT")])
        gp.close()
        con = sqlite3.connect(self.path)
        self.assertEqual(con.execute("PRAGMA application_id").fetchone()[0], 0x47504B47)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 10300)
        self.assertEqual(sorted(r[0] for r in con.execute("SELECT srs_id FROM gpkg_spatial_ref_sys")),
                         [-1, 0, 4326])
        self.assertEqual(con.execute("SELECT data_type, srs_id FROM gpkg_contents WHERE table_name='pts'")
                         .fetchone(), ("features", 4326))
        self.assertEqual(con.execute("SELECT data_type FROM gpkg_contents WHERE table_name='tab'")
                         .fetchone(), ("attributes",))
        self.assertEqual(con.execute("SELECT column_name, geometry_type_name, srs_id, z, m FROM "
                                     "gpkg_geometry_columns").fetchall(), [("geom", "POINT", 4326, 1, 0)])
        self.assertEqual(con.execute("SELECT extension_name FROM gpkg_extensions").fetchall(),
                         [("gpkg_rtree_index",)])
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_extra_srs(self):
        fr = LocalFrame(0.0001, 0.0002, 30.0)
        gp = gw.GeoPackage(self.path)
        gp.add_srs(100001, "local", "ICECUBE", 1, fr.wkt1())
        gp.add_srs(100001, "local again", "ICECUBE", 1, fr.wkt1(), wkt2=fr.wkt2())   # replace
        gp.close()
        con = sqlite3.connect(self.path)
        rows = con.execute("SELECT srs_name, definition, definition_12_063 FROM gpkg_spatial_ref_sys "
                           "WHERE srs_id=100001").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "local again")
        self.assertIn('PROJECTION["Orthographic"]', rows[0][1])
        self.assertTrue(rows[0][2].startswith('PROJCRS["Ice Cube local ENU (origin origin)"'))
        self.assertEqual(con.execute("SELECT definition_12_063 FROM gpkg_spatial_ref_sys WHERE srs_id=4326")
                         .fetchone()[0], "undefined")
        self.assertIn(("gpkg_spatial_ref_sys", "definition_12_063", "gpkg_crs_wkt"),
                      con.execute("SELECT table_name, column_name, extension_name FROM gpkg_extensions").fetchall())
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        gw.GeoPackage(self.path).close()    # reopening (core rows re-upserted) still works

    def test_point_xyz_and_drop_columns(self):
        self.assertEqual(gw.point_xyz(gw.point(1.5, -2.5, 3.0)), (1.5, -2.5, 3.0))
        self.assertEqual(gw.point_xyz(gw.point(1.5, -2.5)), (1.5, -2.5, None))
        self.assertIsNone(gw.point_xyz(None))
        gp = gw.GeoPackage(self.path)
        gp.ensure_feature_table("pts", [("a", "REAL"), ("e_m", "REAL")], "POINT", z=True)
        gp.insert("pts", [{"a": 1.0, "e_m": 2.0, "geom": gw.point(0, 0, 0)}])
        self.assertEqual(gp.drop_columns("pts", ["e_m", "nope"]), [])
        self.assertNotIn("e_m", gp.columns("pts"))
        gp.close()

    def test_upsert_preserves_user_columns_and_rtree(self):
        gp = gw.GeoPackage(self.path)
        gp.ensure_feature_table("pts", [("name", "TEXT"), ("v", "REAL"), ("note", "TEXT")], "POINT",
                                z=True, unique=("name",))
        gp.upsert("pts", [{"name": "a", "v": 1.0, "note": None, "geom": gw.point(1, 1, 0)},
                          {"name": "b", "v": 2.0, "note": None, "geom": None}], key=("name",),
                  preserve=("note",))
        gp.con.execute("UPDATE pts SET note='mine' WHERE name='a'")
        gp.upsert("pts", [{"name": "a", "v": 3.0, "note": None, "geom": gw.point(5, 6, 0)},
                          {"name": "b", "v": 4.0, "note": None, "geom": gw.point(7, 8, 0)},
                          {"name": "c", "v": 5.0, "note": "new", "geom": gw.point(9, 9, 0)}],
                  key=("name",), preserve=("note",))
        gp.update_extent("pts")
        gp.commit()
        rows = gp.con.execute("SELECT fid, name, v, note FROM pts ORDER BY name").fetchall()
        # existing rows keep their fid; AUTOINCREMENT may skip numbers for the new one
        self.assertEqual(rows[:2], [(1, "a", 3.0, "mine"), (2, "b", 4.0, None)])
        self.assertEqual(rows[2][1:], ("c", 5.0, "new"))
        fid_c = rows[2][0]
        rt = gp.con.execute("SELECT id, minx, maxx, miny, maxy FROM rtree_pts_geom ORDER BY id").fetchall()
        self.assertEqual(rt, [(1, 5, 5, 6, 6), (2, 7, 7, 8, 8), (fid_c, 9, 9, 9, 9)])
        self.assertEqual(gp.con.execute("SELECT min_x, min_y, max_x, max_y FROM gpkg_contents "
                                        "WHERE table_name='pts'").fetchone(), (5, 6, 9, 9))
        gp.con.execute("DELETE FROM pts WHERE name='c'")
        gp.con.execute("UPDATE pts SET geom=NULL WHERE name='b'")
        self.assertEqual(gp.con.execute("SELECT id FROM rtree_pts_geom").fetchall(), [(1,)])
        gp.close()

    def test_new_columns_added_to_existing_table(self):
        gp = gw.GeoPackage(self.path)
        gp.ensure_feature_table("pts", [("name", "TEXT")], "POINT", unique=("name",))
        gp.insert("pts", [{"name": "x", "geom": gw.point(0, 0)}])
        gp.close()
        gp = gw.GeoPackage(self.path)
        gp.ensure_feature_table("pts", [("name", "TEXT"), ("extra", "REAL")], "POINT", unique=("name",))
        self.assertEqual(gp.columns("pts"), ["fid", "geom", "name", "extra"])
        self.assertEqual(gp.con.execute("SELECT count(*) FROM gpkg_contents").fetchone()[0], 1)
        gp.drop_table("pts")
        self.assertEqual(gp.tables(), [])
        gp.close()


class TestFrame(unittest.TestCase):
    def test_ecef_round_trip_and_enu(self):
        for lat, lon, h in [(0.0001, 0.0002, 30.0), (-33.5, 151.2, -20.0), (60.0, -10.0, 1000.0)]:
            got = ecef_to_geodetic(*geodetic_to_ecef(lat, lon, h))
            self.assertAlmostEqual(got[0], lat, places=10)
            self.assertAlmostEqual(got[1], lon, places=10)
            self.assertAlmostEqual(got[2], h, places=4)
        fr = LocalFrame(0.0, 0.0, 10.0)
        e, n, u = fr.enu(0.001, 0.001, 12.0)
        self.assertAlmostEqual(n, 0.001 * 110574.27, delta=0.05)   # meridian arc at the equator
        self.assertAlmostEqual(e, 0.001 * 111319.49, delta=0.05)
        self.assertAlmostEqual(u, 2.0 - (e * e + n * n) / (2 * 6371000), delta=0.0005)   # curvature drop


if __name__ == "__main__":
    unittest.main()
