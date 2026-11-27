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
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_labels  # noqa: E402
import gpkg_writer as gw  # noqa: E402
import import_sessions as imp  # noqa: E402
import synth  # noqa: E402
from logger_session import _i, find_sessions, load_session, zip_entry_mtime  # noqa: E402


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

    def test_int_cells(self):
        """A non-finite int cell reads as missing instead of aborting the points.csv parse."""
        self.assertEqual([_i(x) for x in ("3", "3.0", "", None, "x", "nan", "inf", "-inf")],
                         [3, 3, None, None, None, None, None, None])

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

    # -- flagged (void) captures: kept, never deleted; the flag follows re-import both ways (#27)
    def test_voided_captures_kept_and_flag_updates(self):
        name = "IceCubeLogger_2026-12-01_10-00-00.zip"
        files = synth.with_void(synth.session_v05(), {1: "accidental", 3: "bad pole placement"})
        z = synth.write_zip(self.d / name, files, refs=synth.refs_csv_void(True))
        out = run(z, "--gpkg", self.gpkg)
        self.assertIn("4 captures (2 flagged void)", out)
        self.assertIn("refs.csv rows seen: 2 (1 flagged void)", out)
        self.assertEqual(self.counts()["captures"], 4)            # nothing dropped
        self.assertEqual(self.q("SELECT n, void, void_reason FROM captures ORDER BY n"),
                         [(1, 1, "accidental"), (2, 0, None), (3, 1, "bad pole placement"), (4, 0, None)])
        self.assertEqual(self.q("SELECT session, n, void, void_reason FROM refs ORDER BY session"),
                         [("2026-11-30_09-00-00", 5, 1, "moved"),
                          ("2026-12-01_10-00-00", 3, 1, "bad pole placement")])
        self.assertEqual(self.q("SELECT n_voided FROM sessions"), [(2,)])
        self.assertEqual(self.q("SELECT site FROM refs"), [(None,), (None,)])   # flagged refs: no site
        self.assertIn("2 flagged capture(s), kept with void=1: n=1, 3", self.q("SELECT remarks FROM sessions")[0][0])

        # unflagged on the phone since: a re-import clears the flags, rows and fids unchanged
        fids = self.q("SELECT n, fid FROM captures ORDER BY n")
        z.unlink()
        later = time.localtime(time.time() + 3600)[:6]     # zip times have 2 s steps: newer, not tied
        z = synth.write_zip(self.d / name, synth.with_void(synth.session_v05(), {}), refs=synth.refs_csv_void(False),
                            refs_date=later)
        summary = imp.run([str(z), "--gpkg", str(self.gpkg)], log=lambda *a: None)
        self.assertEqual((summary["added"][0]["voided"], summary["refs_csv_voided"]), (0, 0))
        self.assertEqual(self.q("SELECT n, void, void_reason FROM captures ORDER BY n"),
                         [(1, 0, None), (2, 0, None), (3, 0, None), (4, 0, None)])
        self.assertEqual(self.q("SELECT void FROM refs ORDER BY session"), [(0,), (0,)])
        sites = self.q("SELECT site FROM refs ORDER BY session")
        self.assertIsNotNone(sites[0][0])
        self.assertEqual(sites[0], sites[1])                       # unflagged: one site again
        self.assertEqual(self.q("SELECT n, fid FROM captures ORDER BY n"), fids)

        # export_labels leaves voided captures out by default and counts them
        z.unlink()
        z = synth.write_zip(self.d / name, synth.with_void(synth.session_v05(), {2: "moved"}))
        run(z, "--gpkg", self.gpkg)
        stats = {}
        self.assertEqual(export_labels.export(self.gpkg, self.d / "l.csv", stats=stats), 2)
        self.assertEqual(stats["voided"], 1)
        self.assertEqual(export_labels.export(self.gpkg, self.d / "l.csv", include_voided=True, stats=stats), 3)
        self.assertEqual((stats["voided"], stats["voided_included"]), (1, True))
        rows = {r["n"]: r for r in csv.DictReader((self.d / "l.csv").read_text().splitlines())}
        self.assertEqual((rows["2"]["void"], rows["2"]["void_reason"]), ("1", "moved"))
        # a row whose void is NULL (imported before the column was added) exports void 0
        gp = gw.GeoPackage(self.gpkg)
        gp.con.execute("UPDATE captures SET void = NULL WHERE n = 1")
        gp.close()
        export_labels.export(self.gpkg, self.d / "l.csv", include_voided=True)
        rows = {r["n"]: r for r in csv.DictReader((self.d / "l.csv").read_text().splitlines())}
        self.assertEqual(rows["1"]["void"], "0")

    def test_void_reason_stored_only_when_flagged(self):
        """A row with void=0 but a reason left over stores no void_reason, in captures and in
        refs, the same rule as import_refs_csv (#27)."""
        name = "IceCubeLogger_2026-12-01_10-00-00.zip"
        files = synth.with_void(synth.session_v05(), {})
        key = "2026-12-01_10-00-00/points.csv"
        lines = files[key].decode().splitlines()
        lines = [l[:-len(",0,")] + ",0,moved" if l.startswith(("1,", "3,")) else l for l in lines]
        files[key] = ("\n".join(lines) + "\n").encode()
        run(synth.write_zip(self.d / name, files), "--gpkg", self.gpkg)
        self.assertEqual(self.q("SELECT n, void, void_reason FROM captures WHERE n IN (1, 3) ORDER BY n"),
                         [(1, 0, None), (3, 0, None)])
        self.assertEqual(self.q("SELECT void, void_reason FROM refs WHERE n = 3"), [(0, None)])

    def test_read_refs_csv_keeps_void_past_a_short_header(self):
        """Headerless, or under a header the logger did not upgrade, a 0.9.0 row keeps its
        void column (read by the logger's column order); a pre-0.9.0 row is not voided."""
        lat, lon, h = synth.offset(0.1, 2.0, 1.8)
        base = (f"s1,5,ref,1,2026-11-30T14:00:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},"
                "4,30,0.002,0.002,0.004,1.0,,")
        v09 = base + ",,,,ref,,,,,,," + "1,moved"          # 30 columns
        old = base                                          # 18 columns, before 0.6
        for text in (v09 + "\n", "session," + synth.POINTS_HDR + "\n" + v09 + "\n"):
            r = imp.read_refs_csv(text)
            self.assertEqual(len(r), 1)
            self.assertEqual((r[0]["void"], r[0]["void_reason"], r[0]["void_known"]), (True, "moved", True))
        r = imp.read_refs_csv(old + "\n")
        self.assertEqual((r[0]["void"], r[0]["void_known"]), (False, False))

    def test_refs_csv_copies_newest_decides_void(self):
        """Copies of refs.csv that disagree: the newest decides, and an older-format copy
        (no void column) never clears a flag, whatever the input order, nor in a later run;
        nor does an older 0.9.0 copy met in a later run (void_copy_mtime)."""
        gp = gw.GeoPackage(self.d / "r.gpkg")
        imp.ensure_schema(gp)
        hdr = "session," + synth.POINTS_HDR
        lat, lon, h = synth.offset(0.1, 2.0, 1.8)
        row = (f"s1,5,ref,1,2026-11-30T14:00:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},"
               "4,30,0.002,0.002,0.004,1.0,,")
        flagged = hdr + ",void,void_reason\n" + row + ",1,moved\n"
        cleared = hdr + ",void,void_reason\n" + row + ",0,\n"
        pre09 = hdr + "\n" + row + "\n"
        void = lambda: gp.con.execute("SELECT void, void_reason FROM refs").fetchall()  # noqa: E731
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 200.0), (pre09, 100.0)]), (1, 1))
        self.assertEqual(void(), [(1, "moved")])
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 100.0), (pre09, 200.0)]), (1, 1))
        self.assertEqual(void(), [(1, "moved")])              # the newer copy has no void column
        # a later run with only the old copy: the summary count still comes from the table
        self.assertEqual(imp.import_refs_csv(gp, [(pre09, 400.0)]), (1, 1))
        self.assertEqual(void(), [(1, "moved")])              # keeps the stored flag
        self.assertEqual(imp.import_refs_csv(gp, [(cleared, 300.0), (flagged, 100.0)]), (1, 0))
        self.assertEqual(void(), [(0, None)])                 # unflagged since: newest wins
        # a later run whose only copy is an older 0.9.0 one: the stored, newer flag stays
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 250.0)]), (1, 0))
        self.assertEqual(void(), [(0, None)])
        self.assertEqual(imp.import_refs_csv(gp, [flagged]), (1, 0))   # unknown time: no change
        self.assertEqual(void(), [(0, None)])
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 500.0)]), (1, 1))
        self.assertEqual(void(), [(1, "moved")])              # a newer copy still changes it
        self.assertEqual(gp.con.execute("SELECT void_copy_mtime FROM refs").fetchall(), [(500.0,)])
        gp.close()

    def test_refs_csv_of_every_export_of_a_session_counts(self):
        """Two exports of the same session: one is imported, but the refs.csv of the other
        (here newer and flagging an earlier session's ref) still reaches the newest-copy
        rule (#27)."""
        src = self.d / "in"
        older = time.localtime(time.time() - 7200)[:6]
        synth.write_zip(src / "a.zip", synth.session_v05(), refs=synth.refs_csv_void(False), refs_date=older)
        synth.write_zip(src / "b.zip", synth.session_v05(), refs=synth.refs_csv_void(True))
        sources = find_sessions([src])
        self.assertEqual([(s.path.name, [o.path.name for o in s.others]) for s in sources], [("a.zip", ["b.zip"])])
        self.assertEqual(len(sources[0].refs_csv_copies()), 2)
        run(src, "--gpkg", self.gpkg)
        self.assertEqual(self.q("SELECT void, void_reason FROM refs WHERE session='2026-11-30_09-00-00'"),
                         [(1, "moved")])

    def test_refs_csv_copies_of_the_same_time_flagged_wins(self):
        """Copies of the same time (or of unknown time) that disagree on a ref's flag give
        flagged, in either input order, within a run and against a stored flag of that time:
        the logger's rule (VoidRule.liveRefs) (#27)."""
        gp = gw.GeoPackage(self.d / "r.gpkg")
        imp.ensure_schema(gp)
        hdr = "session," + synth.POINTS_HDR + ",void,void_reason\n"
        lat, lon, h = synth.offset(0.1, 2.0, 1.8)
        row = (f"s1,5,ref,1,2026-11-30T14:00:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},"
               "4,30,0.002,0.002,0.004,1.0,,")
        flagged, cleared = hdr + row + ",1,moved\n", hdr + row + ",0,\n"
        void = lambda: gp.con.execute("SELECT void, void_reason FROM refs").fetchall()  # noqa: E731
        for copies in ([(flagged, 100.0), (cleared, 100.0)], [(cleared, 100.0), (flagged, 100.0)],
                       [flagged, cleared], [cleared, flagged]):
            gp.con.execute("DELETE FROM refs")
            self.assertEqual(imp.import_refs_csv(gp, copies), (1, 1))
            self.assertEqual(void(), [(1, "moved")])
        # a later run: a copy of the stored flag's time does not clear it; a newer one does
        self.assertEqual(imp.import_refs_csv(gp, [(cleared, 300.0), (flagged, 300.0)]), (1, 1))
        self.assertEqual(imp.import_refs_csv(gp, [(cleared, 300.0)]), (1, 1))
        self.assertEqual(void(), [(1, "moved")])
        self.assertEqual(imp.import_refs_csv(gp, [(cleared, 301.0)]), (1, 0))
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 301.0)]), (1, 1))   # same time, flags
        gp.close()

    def test_refs_csv_flagged_count_only_rows_it_stores(self):
        """import_refs_csv counts every refs.csv row as seen, but as flagged only rows stored
        from refs.csv: a ref of an imported session follows its points.csv and is counted with
        that session's captures, so a flagged refs.csv row of such a session counts (1, 0)."""
        gp = gw.GeoPackage(self.d / "r.gpkg")
        imp.ensure_schema(gp)
        lat, lon, h = synth.offset(0.1, 2.0, 1.8)
        row = (f"s1,5,ref,1,2026-11-30T14:00:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},"
               "4,30,0.002,0.002,0.004,1.0,,")
        flagged = "session," + synth.POINTS_HDR + ",void,void_reason\n" + row + ",1,moved\n"
        gp.upsert("refs", [{"session": "s1", "n": 5, "source": "points.csv", "void": 1, "void_reason": "moved"}],
                  key=("session", "n"))
        self.assertEqual(imp.import_refs_csv(gp, [(flagged, 100.0)]), (1, 0))
        self.assertEqual(gp.con.execute("SELECT source, void FROM refs").fetchall(), [("points.csv", 1)])
        gp.close()

    def test_zip_entry_mtime_utc_and_zero_date(self):
        """A zip entry's time for ordering refs.csv copies: the UTC extended timestamp when
        present, the DOS local time otherwise, None for a zero DOS date."""
        info = zipfile.ZipInfo("refs.csv", date_time=(2026, 11, 30, 14, 0, 0))
        self.assertEqual(zip_entry_mtime(info), time.mktime((2026, 11, 30, 14, 0, 0, 0, 0, -1)))
        info.extra = (0x5455).to_bytes(2, "little") + (5).to_bytes(2, "little") + b"\x01" + \
            (1_800_000_000).to_bytes(4, "little")
        self.assertEqual(zip_entry_mtime(info), 1_800_000_000.0)
        self.assertIsNone(zip_entry_mtime(zipfile.ZipInfo("refs.csv", date_time=(1980, 0, 0, 0, 0, 0))))

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

    def test_v06_columns(self):
        """0.6 points.csv columns reach the captures table; a 0.5 session leaves them NULL."""
        z = synth.write_zip(self.d / "s.zip", synth.session_v06())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        rows = self.q("SELECT n, method, fix_suspect, fix_age_s, tilt_n, tilt_sd_e_m, tilt_sd_n_m, tilt_sd_u_m "
                      "FROM captures ORDER BY n")
        self.assertEqual(rows[0], (1, "single", 0, 200.0, None, None, None, None))
        self.assertEqual(rows[3][1:3], (None, None))                       # photo row
        self.assertEqual(rows[4], (5, "tilt", 1, 180.0, 6, 0.006, 0.008, 0.012))
        self.assertEqual(rows[5][1:], ("tilt", 1, 180.0, 6, None, None, None))
        z5 = synth.write_zip(self.d / "s5.zip", synth.session_v05("2026-12-02_10-00-00"))
        run(z5, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertEqual(self.q("SELECT count(*) FROM captures WHERE session='2026-12-02_10-00-00' AND "
                                "(method IS NOT NULL OR fix_suspect IS NOT NULL OR tilt_sd_u_m IS NOT NULL)"),
                         [(0,)])

    def _as_schema2(self):
        """Make the gpkg look like one written before schema 3: no 0.6 columns."""
        con = gw.connect(self.gpkg)
        for k, _ in imp.LOGGER_06:
            con.execute(f"ALTER TABLE captures DROP COLUMN {k}")
        con.execute("ALTER TABLE sessions DROP COLUMN schema_version")
        con.execute("UPDATE icecube_meta SET value='2' WHERE key='schema_version'")
        con.commit()
        con.close()

    def test_schema2_reread_without_force(self):
        z = synth.write_zip(self.d / "s.zip", synth.session_v06())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        con = gw.connect(self.gpkg)
        con.execute("UPDATE captures SET label='kept' WHERE n=5")
        con.commit()
        con.close()
        self._as_schema2()
        out = run(z, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertNotIn("unchanged, skipped", out)
        self.assertEqual(self.q("SELECT method, tilt_sd_u_m, label FROM captures WHERE n=5"),
                         [("tilt", 0.012, "kept")])
        self.assertEqual(dict(self.q("SELECT key, value FROM icecube_meta"))["schema_version"],
                         str(imp.SCHEMA_VERSION))
        self.assertIn("unchanged, skipped", run(z, "--gpkg", self.gpkg, "--base", *self.base))   # once only

    def test_schema2_partial_run_strands_nothing(self):
        """An upgrade run that sees only some sessions leaves the others to a later run."""
        za = synth.write_zip(self.d / "a.zip", synth.session_v06())
        zb = synth.write_zip(self.d / "b.zip", synth.session_v06("2026-12-02_10-00-00"))
        run(za, zb, "--gpkg", self.gpkg, "--base", *self.base)
        self._as_schema2()
        out = run(za, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertIn("still at an older schema", out)
        self.assertIn("2026-12-02_10-00-00", out)
        self.assertEqual(self.q("SELECT count(*) FROM captures WHERE session='2026-12-02_10-00-00' "
                                "AND method IS NOT NULL"), [(0,)])
        out = run(za, zb, "--gpkg", self.gpkg, "--base", *self.base)   # b re-read, a skipped
        self.assertIn("2026-12-01_10-00-00: unchanged, skipped", out)
        self.assertNotIn("2026-12-02_10-00-00: unchanged", out)
        self.assertNotIn("still at an older schema", out)
        self.assertNotEqual(self.q("SELECT count(*) FROM captures WHERE session='2026-12-02_10-00-00' "
                                   "AND method IS NOT NULL"), [(0,)])
        self.assertEqual(self.q("SELECT count(*) FROM sessions WHERE schema_version IS NULL "
                                "OR schema_version < ?", imp.SCHEMA_VERSION), [(0,)])

    def test_schema2_pre06_session_is_current_without_reread(self):
        """Schema 3 adds only 0.6 columns, so a 0.5 session is not stale when its source is absent."""
        z5 = synth.write_zip(self.d / "s5.zip", synth.session_v05("2026-12-02_10-00-00"))
        z6 = synth.write_zip(self.d / "s6.zip", synth.session_v06())
        run(z5, z6, "--gpkg", self.gpkg, "--base", *self.base)
        self._as_schema2()
        out = run(z6, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertNotIn("still at an older schema", out)
        self.assertEqual(self.q("SELECT count(*) FROM sessions WHERE schema_version IS NULL "
                                "OR schema_version < ?", imp.SCHEMA_VERSION), [(0,)])

    def test_schema2_unknown_app_version_is_reread(self):
        """A session with no readable app version may hold 0.6 rows: re-read, not marked current."""
        z = synth.write_zip(self.d / "s.zip", synth.session_v06())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        self._as_schema2()
        con = gw.connect(self.gpkg)
        con.execute("UPDATE sessions SET app=NULL")
        con.commit()
        con.close()
        out = run(z, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertIn("re-reading those in the inputs", out)
        self.assertNotIn("unchanged, skipped", out)
        self.assertEqual(self.q("SELECT method, tilt_sd_u_m FROM captures WHERE n=5"), [("tilt", 0.012)])

    def test_schema2_unknown_app_version_without_points_is_current(self):
        """Unknown app version but no points.csv (control events, track only): it cannot hold
        0.6 columns, so it is marked current when its source is absent, not named stale."""
        za = synth.write_zip(self.d / "a.zip", synth.session_v06())
        zb = synth.write_zip(self.d / "b.zip", synth.session_v06("2026-12-02_10-00-00"))
        run(za, zb, "--gpkg", self.gpkg, "--base", *self.base)
        for fmt in ("control events", "points.csv"):
            con = gw.connect(self.gpkg)
            con.execute("UPDATE sessions SET app=NULL, format=? WHERE session='2026-12-02_10-00-00'",
                        (fmt,))
            con.commit()
            con.close()
            self._as_schema2()
            out = run(za, "--gpkg", self.gpkg, "--base", *self.base)
            if fmt == "points.csv":
                self.assertIn("still at an older schema", out)
                self.assertIn("2026-12-02_10-00-00", out)
            else:
                self.assertNotIn("still at an older schema", out)
                self.assertEqual(self.q("SELECT schema_version FROM sessions "
                                        "WHERE session='2026-12-02_10-00-00'"), [(imp.SCHEMA_VERSION,)])

    def test_schema3_reread_fills_void_only_for_09_sessions(self):
        """Schema 4 adds the 0.9.0 void columns: a 0.9 session imported under schema 3 is
        re-read to fill them; a 0.6 session at schema 3 is marked current without a re-read,
        and a session at schema 2 still needs its 0.6 columns from a 0.6 app."""
        self.assertEqual(imp.schema_min_app(3), (0, 9))
        self.assertEqual(imp.schema_min_app(None), (0, 6))
        s9 = synth.with_void(synth.session_v06(), {5: "accidental"})
        s9["2026-12-01_10-00-00/events.csv"] = s9["2026-12-01_10-00-00/events.csv"].replace(
            b"app=0.6", b"app=0.9.0")
        z9 = synth.write_zip(self.d / "s9.zip", s9)
        z6 = synth.write_zip(self.d / "s6.zip", synth.session_v06("2026-12-02_10-00-00"))
        run(z9, z6, "--gpkg", self.gpkg, "--base", *self.base)
        con = gw.connect(self.gpkg)    # as if imported under schema 3: no void read yet
        con.execute("UPDATE captures SET void=NULL, void_reason=NULL")
        con.execute("UPDATE sessions SET schema_version=3, n_voided=NULL")
        con.execute("UPDATE icecube_meta SET value='3' WHERE key='schema_version'")
        con.commit()
        con.close()
        out = run(z9, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertNotIn("still at an older schema", out)          # the 0.6 session is current
        self.assertNotIn("unchanged, skipped", out)                # the 0.9 session is re-read
        self.assertEqual(self.q("SELECT n, void_reason FROM captures WHERE void=1"), [(5, "accidental")])
        self.assertEqual(self.q("SELECT count(*) FROM sessions WHERE schema_version < ?",
                                imp.SCHEMA_VERSION), [(0,)])
        # marked current without a re-read: none flagged (0), not unknown (NULL)
        self.assertEqual(self.q("SELECT session, n_voided FROM sessions ORDER BY session"),
                         [("2026-12-01_10-00-00", 1), ("2026-12-02_10-00-00", 0)])

    def test_schema3_gpkg_without_void_columns_upgrades(self):
        """A gpkg written under schema 3 has no void, void_reason, void_copy_mtime or n_voided
        columns: the next import adds them, re-reads the 0.9 session (n_voided, captures void),
        marks the 0.6 one current with n_voided 0, and fills a refs.csv ref's flag (#27)."""
        s9 = synth.with_void(synth.session_v06(), {5: "accidental"})
        s9["2026-12-01_10-00-00/events.csv"] = s9["2026-12-01_10-00-00/events.csv"].replace(
            b"app=0.6", b"app=0.9.0")
        z9 = synth.write_zip(self.d / "s9.zip", s9, refs=synth.refs_csv_void(True))
        z6 = synth.write_zip(self.d / "s6.zip", synth.session_v06("2026-12-02_10-00-00"))
        run(z9, z6, "--gpkg", self.gpkg, "--base", *self.base)
        con = gw.connect(self.gpkg)
        for table, cols in (("captures", ("void", "void_reason")),
                            ("refs", ("void", "void_reason", "void_copy_mtime")),
                            ("sessions", ("n_voided",))):
            for c in cols:
                con.execute(f"ALTER TABLE {table} DROP COLUMN {c}")
        con.execute("UPDATE sessions SET schema_version=3")
        con.execute("UPDATE icecube_meta SET value='3' WHERE key='schema_version'")
        con.commit()
        con.close()
        self.assertNotIn("n_voided", [r[1] for r in self.q("PRAGMA table_info(sessions)")])
        run(z9, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertEqual(self.q("SELECT session, n_voided FROM sessions ORDER BY session"),
                         [("2026-12-01_10-00-00", 1), ("2026-12-02_10-00-00", 0)])
        self.assertEqual(self.q("SELECT n, void_reason FROM captures WHERE void=1"), [(5, "accidental")])
        self.assertEqual(self.q("SELECT void, void_reason FROM refs WHERE session='2026-11-30_09-00-00'"),
                         [(1, "moved")])

    def test_schema2_upgrade_keeps_snapshot_guard(self):
        z = synth.write_zip(self.d / "full.zip", synth.session_v06())
        run(z, "--gpkg", self.gpkg, "--base", *self.base)
        self._as_schema2()
        snap = synth.session_v06()
        snap["2026-12-01_10-00-00/SNAPSHOT.txt"] = b"snapshot"
        z.unlink()
        sz = synth.write_zip(self.d / "snap.zip", snap)
        out = run(sz, "--gpkg", self.gpkg, "--base", *self.base)
        self.assertIn("snapshot of a session already imported", out)
        self.assertIn("still at an older schema", out)
        self.assertEqual(self.q("SELECT snapshot FROM sessions"), [(0,)])

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
