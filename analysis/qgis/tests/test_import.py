"""Session import on synthetic sessions (made-up positions near 0 N 0 E): tables, photos,
idempotent re-import keeping user columns, older formats, layers and the label export.
Run: python3 -m unittest discover -s analysis/qgis/tests"""
import contextlib
import csv
import math
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_labels  # noqa: E402
import gpkg_writer as gw  # noqa: E402
import import_sessions as imp  # noqa: E402
import synth  # noqa: E402
from logger_session import find_sessions, load_session  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        imp.main([str(a) for a in argv])
    return out.getvalue()


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.gpkg = self.d / "out" / "season.gpkg"
        self.base = [str(v) for v in synth.BASE]

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql, *p):
        con = sqlite3.connect(self.gpkg)
        try:
            return con.execute(sql, p).fetchall()
        finally:
            con.close()

    def counts(self):
        return {t: self.q(f"SELECT count(*) FROM {t}")[0][0]
                for t in ("sessions", "captures", "photos", "refs", "tracks", "track_points")}

    # -- 0.5 zip
    def test_v05_zip(self):
        z = synth.write_zip(self.d / "IceCubeLogger_2026-12-01_10-00-00.zip", synth.session_v05(),
                            refs=synth.refs_csv())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        c = self.counts()
        self.assertEqual((c["sessions"], c["captures"], c["photos"]), (1, 4, 2))
        self.assertEqual(c["refs"], 2)              # this session's ref + one from refs.csv
        self.assertEqual(c["track_points"], 60)     # 1 Hz from 4 Hz
        self.assertEqual(c["tracks"], 3)            # fixed, float, fixed again
        self.assertEqual(sorted(r[0] for r in self.q("SELECT fix FROM tracks")), [4, 4, 5])

        n, cls, photo, pc, h, e, nn, u = self.q(
            "SELECT n, class, photo, photo_count, h_ground, x_m, y_m, z_m FROM captures WHERE n=2")[0]
        self.assertEqual((cls, photo, pc), ("object", "photos/2026-12-01_10-00-00/photo_2_20261201T100010Z.jpg", 1))
        self.assertAlmostEqual(e, 3.0, delta=0.01)
        self.assertAlmostEqual(nn, 2.0, delta=0.01)
        self.assertAlmostEqual(u, 0.1, places=3)    # origin = base here (no [site.origin])
        blob = self.q("SELECT geom FROM captures WHERE n=2")[0][0]
        self.assertAlmostEqual(gw.struct.unpack_from("<d", blob, 29)[0], h)   # Z = ground height
        self.assertTrue((self.gpkg.parent / photo).is_file())

        ref = self.q("SELECT pauses, pole_check, diff_last_m, visits, last_visit, pole_ok, source "
                     "FROM refs WHERE session='2026-12-01_10-00-00' AND n=3")[0]
        self.assertEqual(ref, (1, "pole check OK (±0.4 cm vs last visit, -1.0 cm vs mean of 2 visits)",
                               -0.004, 2, "2026-11-30_09-00-00/5", 1, "points.csv"))
        sites = self.q("SELECT site FROM refs ORDER BY session")
        self.assertEqual(sites[0], sites[1])       # 10 cm apart: same mark
        s = self.q("SELECT format, app, link, caster_mount, base_station, pole_heights, caster_lost, "
                   "n_surface, n_object, n_ref, n_photo, snapshot FROM sessions")[0]
        self.assertEqual(s, ("points.csv", "0.5", "bluetooth", "TEST01", "7", "1.800, 1.800 at 10:00:09Z",
                             1, 1, 1, 1, 1, 0))
        meta = dict(self.q("SELECT key, value FROM icecube_meta"))
        self.assertTrue(meta["local_proj"].startswith("+proj=ortho +lat_0=0.0001 +lon_0=0.0002 "))
        self.assertTrue(meta["local_wkt"].startswith('PROJCRS["Ice Cube local ENU (origin base)"'))
        self.assertEqual((meta["origin_name"], meta["base_source"]), ("base", "command line"))
        for t in ("photos", "refs", "track_points"):
            self.assertEqual(self.q(f"SELECT count(*) FROM {t} WHERE x_m IS NULL"), [(0,)], t)
        self.assertEqual(self.q("SELECT organization FROM gpkg_spatial_ref_sys WHERE srs_id=?",
                                imp.LOCAL_SRS_ID), [("ICECUBE",)])
        self.assertEqual(self.q("PRAGMA integrity_check"), [("ok",)])

    # -- idempotence
    def test_reimport_keeps_user_columns(self):
        files = synth.session_v05()
        z = synth.write_zip(self.d / "s.zip", files)
        run(z, "--gpkg", self.gpkg)
        first = self.counts()
        fids = self.q("SELECT session, n, fid FROM captures ORDER BY n")
        con = gw.connect(self.gpkg)                 # what QGIS does when a label is typed in
        con.execute("UPDATE captures SET note='spigot, 3 cm high', label='spigot' WHERE n=2")
        con.execute("UPDATE photos SET note='blurry' WHERE n=4")
        con.execute("UPDATE sessions SET note='first walk'")
        con.commit()
        con.close()

        out = run(z, "--gpkg", self.gpkg)
        self.assertIn("unchanged, skipped", out)
        run(z, "--gpkg", self.gpkg, "--force")
        self.assertEqual(self.counts(), first)
        self.assertEqual(self.q("SELECT session, n, fid FROM captures ORDER BY n"), fids)
        self.assertEqual(self.q("SELECT note, label FROM captures WHERE n=2"), [("spigot, 3 cm high", "spigot")])
        self.assertEqual(self.q("SELECT note FROM photos WHERE n=4"), [("blurry",)])
        self.assertEqual(self.q("SELECT note FROM sessions"), [("first walk",)])

        # the session grew (a later share of the same session): one new row, notes kept
        lat, lon, h = synth.offset(9.0, 9.0, 1.8)
        extra = f"5,surface,1,2026-12-01T10:01:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},4,5,0,0,0,1.0,,"
        z2 = synth.write_zip(self.d / "IceCubeLogger_2026-12-01_10-00-00.zip", synth.session_v05(extra_rows=[extra]))
        z.unlink()
        run(z2, "--gpkg", self.gpkg)
        self.assertEqual(self.counts()["captures"], 5)
        self.assertEqual(self.q("SELECT label FROM captures WHERE n=2"), [("spigot",)])
        self.assertEqual(self.q("SELECT count(*) FROM tracks")[0][0], first["tracks"])

    def test_snapshot_does_not_replace_full_import(self):
        full = synth.write_zip(self.d / "full.zip", synth.session_v05())
        run(full, "--gpkg", self.gpkg)
        snap = synth.session_v05()
        snap["2026-12-01_10-00-00/SNAPSHOT.txt"] = b"snapshot"
        sz = synth.write_zip(self.d / "snap.zip", snap)
        self.assertIn("snapshot of a session already imported", run(sz, "--gpkg", self.gpkg))
        # both in one input folder: the full one wins
        self.assertFalse(find_sessions([self.d])[0].snapshot)

    # -- older formats
    def test_v03_control_marks(self):
        root = synth.write_dir(self.d / "in", synth.session_v03())
        run(root.parent, "--gpkg", self.gpkg, "--base", *self.base)
        rows = self.q("SELECT n, class, pole_m, fix, n_epochs, h_ant, h_ground, x_m, y_m FROM captures ORDER BY n")
        self.assertEqual([r[:5] for r in rows], [(1, "control", 1.8, 4, 5), (2, "control", 1.6, 4, 5)])
        for n, _, pole, _, _, h_ant, h_ground, e, nn in rows:
            self.assertAlmostEqual(h_ant - h_ground, pole, places=6)
            self.assertAlmostEqual(nn, 1.0, delta=0.01)
        # mark 1 at 10.1 s: last epoch 10.0 s, the 1 s window 9.0-10.0 s, mean at 9.5 s
        self.assertAlmostEqual(rows[0][7], 0.5 * 9.5, delta=0.01)
        self.assertEqual(self.q("SELECT format, app, n_control FROM sessions"), [("control events", "0.3", 2)])

    def test_v02_track_only_and_name_without_zip(self):
        z = synth.write_zip(self.d / "session-09-00-00", synth.session_v02())   # no .zip extension
        run(self.d, "--gpkg", self.gpkg)
        c = self.counts()
        self.assertEqual((c["sessions"], c["captures"]), (1, 0))
        self.assertEqual(c["tracks"], 2)            # split at the 10 s gap
        self.assertIn("2 unnumbered control marks", self.q("SELECT remarks FROM sessions")[0][0])
        self.assertTrue(z.exists())

    def test_load_session_from_folder_matches_zip(self):
        files = synth.session_v05()
        root = synth.write_dir(self.d / "dir", files)
        z = synth.write_zip(self.d / "z.zip", files)
        a = load_session(find_sessions([root])[0])
        b = load_session(find_sessions([z])[0])
        self.assertEqual([c["n"] for c in a.captures], [c["n"] for c in b.captures])
        self.assertEqual(len(a.track), len(b.track))
        self.assertEqual(a.photos, b.photos)

    # -- origin
    def test_origin_from_config_and_change_recomputes(self):
        z = synth.write_zip(self.d / "IceCubeLogger_2026-12-01_10-00-00.zip", synth.session_v05())
        cfg = self.d / "site.toml"
        def write_cfg(e, n, u, name):
            lat, lon, h = synth.offset(e, n, u)
            cfg.write_text(f'[season]\nname = "s"\ngpkg = "out/season.gpkg"\n[site]\n'
                           f'base = [{synth.BASE[0]}, {synth.BASE[1]}, {synth.BASE[2]}]\n'
                           f'[site.origin]\nname = "{name}"\nlat = {lat:.10f}\nlon = {lon:.10f}\n'
                           f'h = {h:.5f}\nsource = "made up"\n')
        write_cfg(0.0, 0.0, 0.0, "B0")
        run(z, "--config", cfg)
        x0, y0, z0 = self.q("SELECT x_m, y_m, z_m FROM captures WHERE n=2")[0]
        con = gw.connect(self.gpkg)
        con.execute("UPDATE captures SET note='kept', label='spigot' WHERE n=2")
        con.commit()
        con.close()
        write_cfg(1.0, -2.0, -0.5, "G")             # move the origin 1 m E, 2 m S, 0.5 m down
        out = run(z, "--config", cfg)
        self.assertIn("recomputed for all rows", out)
        self.assertIn("ENU about G (made up)", out)
        x1, y1, z1 = self.q("SELECT x_m, y_m, z_m FROM captures WHERE n=2")[0]
        self.assertAlmostEqual(x1 - x0, -1.0, delta=0.001)
        self.assertAlmostEqual(y1 - y0, 2.0, delta=0.001)
        self.assertAlmostEqual(z1 - z0, 0.5, delta=0.001)
        tp = self.q("SELECT x_m FROM track_points ORDER BY utc LIMIT 1")[0][0]
        self.assertIsNotNone(tp)
        self.assertEqual(self.q("SELECT note, label FROM captures WHERE n=2"), [("kept", "spigot")])
        meta = dict(self.q("SELECT key, value FROM icecube_meta"))
        self.assertEqual((meta["origin_name"], meta["origin_source"]), ("G", "made up"))
        self.assertEqual(meta["local_crs_name"], "Ice Cube local ENU (origin G)")
        self.assertAlmostEqual(float(meta["base_x_m"]), -1.0, delta=0.001)   # base kept separately
        self.assertEqual(self.q("SELECT srs_name FROM gpkg_spatial_ref_sys WHERE srs_id=?", imp.LOCAL_SRS_ID),
                         [("Ice Cube local ENU (origin G)",)])
        self.assertNotIn("recomputed", run(z, "--config", cfg, "--force"))   # same origin: no recompute

    def test_schema1_columns_migrated(self):
        z = synth.write_zip(self.d / "s.zip", synth.session_v05())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        con = gw.connect(self.gpkg)                 # make it look like a schema 1 gpkg
        con.execute("ALTER TABLE captures ADD COLUMN e_m REAL")
        con.execute("UPDATE icecube_meta SET value='1' WHERE key='schema_version'")
        con.commit()
        con.close()
        self.assertIn("recomputed", run(z, "--gpkg", self.gpkg, "--base", *self.base))
        self.assertNotIn("e_m", [r[1] for r in self.q("PRAGMA table_info(captures)")])

    # -- layers, config and export
    def test_reimport_keeps_topo_tables(self):
        """An import replaces the map layers but must not drop layer_topo_* (topo_to_gpkg)."""
        synth.layers_geojson(self.d / "plan.geojson")
        synth.write_zip(self.d / "sessions" / "a.zip", synth.session_v05())
        cfg = self.d / "site.toml"
        cfg.write_text('[season]\nname = "test-season"\ngpkg = "out/season.gpkg"\n'
                       'input_dir = "sessions"\nlayers = ["plan.geojson"]\n')
        run("--config", cfg)
        con = gw.connect(self.gpkg)
        con.execute("CREATE TABLE layer_topo_contours (fid INTEGER PRIMARY KEY, level REAL)")
        con.execute("INSERT INTO layer_topo_contours (level) VALUES (1.5)")
        con.execute("INSERT INTO gpkg_contents (table_name, data_type, identifier) "
                    "VALUES ('layer_topo_contours', 'attributes', 'layer_topo_contours')")
        con.commit()
        con.close()
        run("--config", cfg, "--force")
        self.assertEqual(self.q("SELECT level FROM layer_topo_contours"), [(1.5,)])
        names = [r[0] for r in self.q("SELECT table_name FROM gpkg_contents WHERE table_name LIKE 'layer_%' ORDER BY 1")]
        self.assertEqual(names, ["layer_plan_lines", "layer_plan_points", "layer_plan_polygons",
                                 "layer_topo_contours"])

    def test_layers_config_and_export(self):
        lay = synth.layers_geojson(self.d / "plan.geojson")
        synth.write_zip(self.d / "sessions" / "a.zip", synth.session_v05())
        cfg = self.d / "site.toml"
        cfg.write_text('[season]\nname = "test-season"\ngpkg = "out/season.gpkg"\n'
                       'input_dir = "sessions"\nlayers = ["plan.geojson"]\n')
        out = run("--config", cfg)
        self.assertIn("base point in plan.geojson", out)          # frame from the layer's base
        tables = [r[0] for r in self.q("SELECT table_name FROM gpkg_contents WHERE table_name LIKE 'layer_%' ORDER BY 1")]
        self.assertEqual(tables, ["layer_plan_lines", "layer_plan_points", "layer_plan_polygons"])
        self.assertEqual(self.q("SELECT z FROM gpkg_geometry_columns WHERE table_name='layer_plan_points'"), [(1,)])
        self.assertEqual(self.q("SELECT z, geometry_type_name FROM gpkg_geometry_columns "
                                "WHERE table_name='layer_plan_polygons'"), [(0, "MULTIPOLYGON")])
        self.assertEqual(self.q("SELECT fill_rgba, stroke_rgba FROM layer_plan_polygons"),
                         [("21,101,192,51", "21,101,192,255")])
        self.assertEqual(self.q("SELECT label, status, uncertainty_m, measured_local_en_m, neighbour "
                                "FROM layer_plan_points WHERE label='A'"),
                         [("A", "predicted", 0.5, "[1.0, 2.0]", "true")])
        self.assertEqual(dict(self.q("SELECT key, value FROM icecube_meta"))["season"], "test-season")
        e = self.q("SELECT x_m FROM captures WHERE n=1")[0][0]
        self.assertAlmostEqual(e, 2.0, delta=0.01)
        x, y = self.q("SELECT x_m, y_m FROM layer_plan_points WHERE kind='base'")[0]
        self.assertAlmostEqual(math.hypot(x, y), 0.0, places=6)

        con = gw.connect(self.gpkg)
        con.execute("UPDATE captures SET label='deck' WHERE n=1")
        con.commit()
        con.close()
        csv_path = self.d / "labels.csv"
        n = export_labels.export(self.gpkg, csv_path)
        self.assertEqual(n, 3)                                    # photo rows are not exported
        rows = list(csv.DictReader(csv_path.read_text().splitlines()))
        self.assertEqual(rows[0]["Remarks"], "surface deck")
        self.assertEqual(rows[0]["label"], "deck")
        self.assertTrue(rows[0]["Time"].endswith(" UTC"))
        self.assertEqual(export_labels.export(self.gpkg, csv_path, labelled_only=True), 1)

        # re-run with a changed layer list replaces the layer tables
        shutil.copy(lay, self.d / "other.geojson")
        run("--config", cfg, "--layers", self.d / "other.geojson")
        tables = [r[0] for r in self.q("SELECT table_name FROM gpkg_contents WHERE table_name LIKE 'layer_%'")]
        self.assertTrue(all(t.startswith("layer_other_") for t in tables))

    def test_icecube_reads_the_export(self):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
            from icecube import io as icio
        except ImportError:   # numpy missing
            self.skipTest("icecube needs numpy")
        synth.write_zip(self.d / "a.zip", synth.session_v05())
        run(self.d / "a.zip", "--gpkg", self.gpkg)
        export_labels.export(self.gpkg, self.d / "c.csv")
        pts = icio.read_survey(dict(id="s", path=str(self.d / "c.csv"), reader="swmaps_csv",
                                    exclude_remarks=["ref"]), icio.Site(zone=31, origin=(500000.0, 0.0)))
        self.assertEqual(len(pts), 2)
        self.assertEqual(sorted(pts.remark), ["object", "surface"])

    def test_opens_with_gdal(self):
        """Optional: with pyogrio (GDAL) installed, every table opens with the right type."""
        try:
            import pyogrio
        except ImportError:
            self.skipTest("pyogrio not installed (optional validation)")
        synth.layers_geojson(self.d / "plan.geojson")
        synth.write_zip(self.d / "a.zip", synth.session_v05(), refs=synth.refs_csv())
        run(self.d / "a.zip", "--gpkg", self.gpkg, "--layers", self.d / "plan.geojson")
        layers = dict(pyogrio.list_layers(self.gpkg))
        self.assertEqual(layers["captures"], "Point Z")
        self.assertEqual(layers["tracks"], "LineString Z")
        self.assertEqual(layers["layer_plan_polygons"], "MultiPolygon")
        self.assertIsNone(layers["sessions"])
        info = pyogrio.read_info(self.gpkg, layer="captures")
        self.assertEqual((info["features"], info["crs"]), (4, "EPSG:4326"))


if __name__ == "__main__":
    unittest.main()
