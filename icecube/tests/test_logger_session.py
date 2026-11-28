"""The logger_session reader (#20) on made-up Ice Cube Logger sessions near 0 N 0 E
(analysis/qgis/tests/synth.py); no real site. Run: python3 -m unittest discover icecube/tests"""
import contextlib
import io as _io
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np

from icecube import cli, io

QGIS = Path(__file__).resolve().parents[2] / "analysis" / "qgis"
sys.path.insert(0, str(QGIS))
sys.path.insert(0, str(QGIS / "tests"))

import gpkg_writer as gw  # noqa: E402
import import_sessions as imp  # noqa: E402
import soundings as snd  # noqa: E402
import synth  # noqa: E402
import test_soundings  # noqa: E402  (make_session: a ref, pole stops, 4 Hz GGA)

NAME = "2026-12-01_10-00-00"
COLS_06 = synth.POINTS_HDR_06.split(",")
COLS_11 = COLS_06 + ["void", "void_reason", "fix_start", "float_spell_s", "suspect_reason", "fix_young"]
SITE = io.Site(origin_geodetic=synth.BASE)


def fix_at(s):
    """Single 0-5 s, fix 5-200 s (stretch A), float 200-260 s (a 60 s spell), fix 260-460 s
    (stretch B, suspect: float spell), float 460-463 s, fix 463-700 s (stretch C)."""
    if s < 5:
        return 1
    if 200 <= s < 260 or 460 <= s < 463:
        return 5
    return 4


# n, seconds, ground z (m), void reason (None: not voided); positions on the walk line
CAPTURES = [(1, 30, 0.10, None),       # A, fix age 25 s: settle-inflated
            (2, 180, 0.20, None),      # A, fix age 175 s
            (3, 190, 0.30, "moved"),   # A, voided
            (4, 400, 0.40, None),      # B, after a 60 s float spell: left out
            (5, 650, 0.50, None)]      # C, fix age 187 s


def ground(s, z=0.0):
    """lat, lon, h_ground of the walk (east 0.5 m/s, 1 m north of the base) at s seconds."""
    return synth.offset(0.5 * s, 1.0, z)


def stop_walk(s):
    """Walk seconds of the stop=True session at s seconds: still 500-530 s (a ref) and
    600-606 s (a pole stop)."""
    for a, b, before in ((500, 530, 0), (600, 606, 30)):
        if s < a:
            return s - before
        if s < b:
            return a - before
    return s - 36


def points_csv(cols=COLS_11, captures=CAPTURES, walk=lambda s: s):
    lines = [",".join(cols)]
    for n, s, z, void, *cls in captures:
        lat, lon, h = ground(walk(s), z)
        t = synth.START + timedelta(seconds=s)
        ref = cls == ["ref"]
        v = {"n": n, "class": "ref" if ref else "surface", "elapsed_ns": synth.T0_NS + int(s * 1e9),
             "utc": t.strftime("%Y-%m-%dT%H:%M:%S.00Z"), "lat": f"{lat:.9f}", "lon": f"{lon:.9f}",
             "h_ant_ellips": f"{h + 1.8:.4f}", "pole_m": "1.800", "h_ground_ellips": f"{h:.4f}",
             "fix": 4, "n_epochs": 100 if ref else 5, "sd_e_m": "0.0020", "sd_n_m": "0.0020",
             "sd_u_m": "0.0030", "age_s": "1.0", "fix_age_s": "999", "fix_jump_m": "", "fix_suspect": 0,
             "method": "single", "void": 1 if void else 0, "void_reason": void or "", "fix_young": 0}
        lines.append(",".join(str(v.get(c, "")) for c in cols))
    return "\n".join(lines) + "\n"


def session_files(cols=COLS_11, raw=True, app="0.11.0", name=NAME, stop=False):
    """{relative path: bytes} of a session folder: 1 Hz GGA on the walk with the fix
    timeline of fix_at, the RTCM 1005 of the made-up base in events.csv. With `stop`, 4 Hz
    GGA, the walk of stop_walk with a ref capture (n 6) at the end of its 30 s stand, and
    plumb gravity at 100 Hz from 490 s to 615 s (the IMU plumb check)."""
    ev = synth.events([(1, "gps connected bluetooth Test-Rover secure gps_offset=0"),
                       (2, "base station=7 type=1005 ecef=6378167.0;22.264;11.057 h=none"),
                       (699, "note end")], app=app)
    walk = stop_walk if stop else (lambda s: s)
    caps = CAPTURES + [(6, 529.75, 0.0, None, "ref")] if stop else CAPTURES
    files = {f"{name}/events.csv": ev.encode(), f"{name}/points.csv": points_csv(cols, caps, walk).encode()}
    if raw:
        ep = []
        for k in range(2800 if stop else 700):
            s = k / 4 if stop else k
            lat, lon, h = ground(walk(s), 1.8)
            ep.append((s, lat, lon, h, fix_at(s)))
        ubx, chunks = synth.write_ubx(ep)
        files[f"{name}/gps.ubx"] = ubx
        files[f"{name}/gps_chunks.csv"] = chunks.encode()
        if stop:
            imu = ["sensor,event_ns,x,y,z"] + [f"grav,{synth.T0_NS + k * 10_000_000},0.0,0.0,9.81"
                                               for k in range(49000, 61500)]
            files[f"{name}/imu.csv"] = ("\n".join(imu) + "\n").encode()
    return files


def quiet(fn, *a, **k):
    with contextlib.redirect_stderr(_io.StringIO()) as err:
        out = fn(*a, **k)
    return out, err.getvalue()


class TestLoggerSessionReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, path, **kw):
        return quiet(io.read_logger_session, path, SITE, "s1", **kw)

    def test_session_reads_with_the_soundings_rules(self):
        ses = synth.write_dir(self.d / "in", session_files())
        pts, _ = self.read(ses)
        sids = list(pts.remark)
        self.assertEqual(sids, [f"s1:{NAME}:c1", f"s1:{NAME}:c2", f"s1:{NAME}:c5"])   # 3 voided, 4 float spell
        # positions in the site's frame as they come (origin = the made-up base)
        self.assertAlmostEqual(pts.x[0], 15.0, delta=0.01)
        self.assertAlmostEqual(pts.y[0], 1.0, delta=0.01)
        self.assertAlmostEqual(pts.z[0], 0.10, delta=0.002)
        # sigma is soundings.sigma_terms for the same inputs; c1 (fix age 25 s) inflated
        sg = snd.DEFAULTS["sigma"]
        for k, age in ((0, 25.0), (1, 175.0)):
            want = snd.sigma_terms(sg, sd_u=0.003, sd_h=snd._sd_h(0.002, 0.002), age=1.0, fix_age=age,
                                   settle_s=snd.DEFAULTS["settle_s"], pole_m=1.8, tilt_deg=sg["tilt_bubble_deg"])
            self.assertAlmostEqual(pts.sz[k], want["sigma_z"], places=9)
            self.assertAlmostEqual(pts.sh[k], want["sigma_xy"], places=9)
        self.assertGreater(pts.sz[0], pts.sz[1])
        # passes are the fix stretches: A (c1, c2) and C (c5)
        self.assertEqual(pts.pass_names, [f"{NAME}/s000", f"{NAME}/s002"])
        self.assertEqual(list(pts.pass_id), [0, 0, 1])
        self.assertEqual(set(pts.group), {NAME})

    def test_voided_and_float_spell_rows(self):
        ses = synth.write_dir(self.d / "in", session_files())
        pts, _ = self.read(ses, include_voided=True)
        self.assertIn(f"s1:{NAME}:c3", list(pts.remark))
        self.assertNotIn(f"s1:{NAME}:c4", list(pts.remark))       # float spell: still out

    def test_old_session_without_the_0_11_columns(self):
        ses = synth.write_dir(self.d / "in", session_files(cols=COLS_06, app="0.6"))
        pts, _ = self.read(ses)
        self.assertEqual(len(pts), 4)                             # nothing voided in a 0.6 file
        self.assertEqual(list(pts.pass_id), [0, 0, 0, 1])

    def test_zip_equals_folder(self):
        files = session_files()
        a, _ = self.read(synth.write_dir(self.d / "dir", files))
        b, _ = self.read(synth.write_zip(self.d / "zip" / "s.zip", files))
        for k in ("x", "y", "z", "sz", "sh", "pass_id"):
            np.testing.assert_array_equal(getattr(a, k), getattr(b, k))
        self.assertEqual(a.pass_names, b.pass_names)

    def test_session_filter_and_folder_of_sessions(self):
        root = self.d / "in"
        synth.write_dir(root, session_files())
        synth.write_dir(root, session_files(name="2026-12-02_10-00-00"))
        both, _ = self.read(root)
        self.assertEqual(set(both.group), {NAME, "2026-12-02_10-00-00"})
        one, _ = self.read(root, sessions=[NAME])
        self.assertEqual(set(one.group), {NAME})
        with self.assertRaises(ValueError):
            self.read(root, sessions=["2026-12-09_10-00-00"])

    def test_points_csv_only_falls_back_to_the_loggers_columns(self):
        ses = synth.write_dir(self.d / "in", session_files(raw=False))
        pts, err = self.read(ses)
        self.assertIn("no raw epochs", err)
        self.assertEqual(len(pts), 4)                             # the logger said nothing suspect
        self.assertEqual(len(pts.pass_names), 4)                  # one pass per capture

    def test_base_sources(self):
        ses = synth.write_dir(self.d / "in", session_files())
        a, _ = self.read(ses)                                     # RTCM 1005 in the session
        b, _ = self.read(ses, base=synth.BASE)
        np.testing.assert_allclose(a.z, b.z, atol=1e-6)
        no_base = synth.write_dir(self.d / "nb", {k.replace(NAME, "2026-12-03_10-00-00"): v
                                                  for k, v in session_files().items()})
        ev = no_base / "events.csv"
        ev.write_text("\n".join(x for x in ev.read_text().splitlines() if "type=1005" not in x) + "\n")
        with self.assertRaises(ValueError):
            self.read(no_base)

    def test_utm_only_site_raises(self):
        ses = synth.write_dir(self.d / "in", session_files())
        with self.assertRaises(ValueError):
            io.read_logger_session(ses, io.Site(), "s1")

    def test_read_survey_defaults_from_file(self):
        synth.write_dir(self.d / "in", session_files())
        scfg = {"id": "s1", "reader": "logger_session", "path": "in"}
        pts, _ = quiet(io.read_survey, scfg, SITE, self.d)
        self.assertTrue(scfg["pass_rule"]["from_file"])
        self.assertEqual(len(pts), 3)
        scfg = {"id": "s1", "reader": "logger_session", "path": "in", "pass_rule": {"from_file": False}}
        quiet(io.read_survey, scfg, SITE, self.d)
        self.assertFalse(scfg["pass_rule"]["from_file"])          # a user value wins

    def test_pass_rule_written_by_the_user_is_kept_whole(self):
        synth.write_dir(self.d / "in", session_files())
        scfg = {"id": "s1", "reader": "logger_session", "path": "in", "pass_rule": {"gap_s": 5.0}}
        quiet(io.read_survey, scfg, SITE, self.d)
        self.assertEqual(scfg["pass_rule"], {"gap_s": 5.0})

    def test_gps_ubx_without_gga_falls_back_to_the_loggers_columns(self):
        files = session_files()
        junk = b"\xb5\x62\x02\x15\x04\x00\x01\x02\x03\x04\x00\x00"
        files[f"{NAME}/gps.ubx"] = junk
        files[f"{NAME}/gps_chunks.csv"] = f"elapsed_ns,byte_offset,length\n{synth.T0_NS},0,{len(junk)}\n".encode()
        pts, err = self.read(synth.write_dir(self.d / "in", files))
        self.assertIn(f"no raw epochs for session(s) {NAME}", err)
        self.assertEqual(len(pts), 4)
        self.assertEqual(len(pts.pass_names), 4)                  # one pass per capture

    def test_first_1005_as_base_warns(self):
        _, err = self.read(synth.write_dir(self.d / "in", session_files()))
        self.assertIn("first RTCM 1005", err)
        _, err = self.read(self.d / "in" / NAME, base=synth.BASE)
        self.assertNotIn("first RTCM 1005", err)

    def test_z0_is_subtracted(self):
        ses = synth.write_dir(self.d / "in", session_files())
        a, _ = self.read(ses)
        b, _ = quiet(io.read_logger_session, ses, io.Site(origin_geodetic=synth.BASE, z0=0.25), "s1")
        np.testing.assert_allclose(b.z, a.z - 0.25, atol=1e-9)

    def test_base_history_needs_the_current_base(self):
        ses = synth.write_dir(self.d / "in", session_files())
        lat, lon, h = synth.BASE
        (self.d / "hist.csv").write_text("start_utc,end_utc,lat,lon,h\n"
                                         f"2026-11-01T00:00:00Z,2026-12-31T00:00:00Z,{lat},{lon},{h - 0.05}\n")
        with self.assertRaises(ValueError):
            self.read(ses, base_history="hist.csv", base_dir=self.d)
        a, _ = self.read(ses, base=synth.BASE)
        b, _ = self.read(ses, base=synth.BASE, base_history="hist.csv", base_dir=self.d)
        np.testing.assert_allclose(b.z, a.z + 0.05, atol=0.002)   # moved from the older base

    def test_kinds_with_stop_adds_pole_stops(self):
        ses = synth.write_dir(self.d / "in", session_files(stop=True))
        caps, _ = self.read(ses, base=synth.BASE)
        both, _ = self.read(ses, base=synth.BASE, kinds=("capture", "stop"))
        stops = [r for r in both.remark if r.split(":")[-1].startswith("p")]
        self.assertTrue(stops, list(both.remark))
        self.assertEqual(len(both), len(caps) + len(stops))
        self.assertFalse([r for r in caps.remark if r.split(":")[-1].startswith("p")])
        k = list(both.remark).index(stops[0])
        self.assertAlmostEqual(both.x[k], 285.0, delta=0.05)     # 0.5 m/s x 570 s of walking

    def _season(self, ses, name="season.gpkg"):
        gpkg = self.d / name
        with contextlib.redirect_stdout(_io.StringIO()):
            imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
        return gpkg

    def test_anchor_names_without_a_season_are_the_surveys_own(self):
        ses = test_soundings.make_session(self.d / "in")
        pts, _ = self.read(ses, base=synth.BASE)
        names = {a for a in pts.anchor if a}
        self.assertTrue(names)
        self.assertTrue(all(a.startswith("s1:") for a in names), names)

    def test_anchor_names_follow_the_season_sites(self):
        """With base_from_gpkg the ref takes the season's site: stored for a session the
        season holds, by update_local's rule for one it does not; a ref at no season site
        keeps a survey-own name."""
        first = test_soundings.make_session(self.d / "a")
        gpkg = self._season(first)
        con = sqlite3.connect(gpkg)
        site = con.execute("SELECT site FROM refs WHERE session=?", (first.name,)).fetchone()[0]
        con.close()
        self.assertIsNotNone(site)
        pts, _ = self.read(first, base_from_gpkg=str(gpkg))
        self.assertIn(f"site{site}", set(pts.anchor))
        other = test_soundings.make_session(self.d / "b", name="2026-12-02_10-00-00")
        pts, _ = self.read(other, base_from_gpkg=str(gpkg))        # not in the season: seed rule
        self.assertIn(f"site{site}", set(pts.anchor))
        con = gw.connect(gpkg)                                    # (the gpkg triggers need ST_ functions)
        con.execute("UPDATE refs SET lat = lat + 1.0 / 110574.0")  # the season's mark 1 m north
        con.commit()
        con.close()
        pts, _ = self.read(other, base_from_gpkg=str(gpkg))
        names = {a for a in pts.anchor if a}
        self.assertTrue(names)
        self.assertTrue(all(a.startswith("s1:") for a in names), names)

    def test_base_from_gpkg(self):
        ses = synth.write_dir(self.d / "in", session_files())
        gpkg = self._season(ses)
        a, _ = self.read(ses, base=synth.BASE)
        odd = self.d / "a#b?c%d.gpkg"                               # escaped in the SQLite URI
        odd.write_bytes(gpkg.read_bytes())
        b, err = self.read(ses, base_from_gpkg=str(odd))
        np.testing.assert_allclose(b.z, a.z, atol=1e-6)
        self.assertNotIn("first RTCM 1005", err)
        b, _ = self.read(ses, base_from_gpkg=odd.name, base_dir=self.d)   # relative to base_dir
        np.testing.assert_allclose(b.z, a.z, atol=1e-6)
        with self.assertRaisesRegex(ValueError, "no such file"):
            self.read(ses, base_from_gpkg=str(self.d / "missing.gpkg"))
        (self.d / "text.gpkg").write_text("not a database\n")
        with self.assertRaisesRegex(ValueError, "not a season GeoPackage"):
            self.read(ses, base_from_gpkg=str(self.d / "text.gpkg"))
        con = gw.connect(gpkg)
        con.execute("DELETE FROM icecube_meta WHERE key='base_h'")
        con.commit()
        con.close()
        with self.assertRaisesRegex(ValueError, "base_h"):
            self.read(ses, base_from_gpkg=str(gpkg))

    def test_end_to_end_run_next_to_the_season_table(self):
        """`python -m icecube run` with a logger_session survey and the season soundings
        table of the same session: the growth between them is zero."""
        ses = synth.write_dir(self.d / "in", session_files())
        gpkg = self.d / "season.gpkg"
        with contextlib.redirect_stdout(_io.StringIO()):
            imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
            snd.build({"season": {"gpkg": str(gpkg)},
                       "soundings": {"sources": [{"id": "logger", "type": "logger",
                                                  "session_paths": [str(ses)]}]}},
                      self.d, log=lambda *a: None)
        o = ", ".join(repr(v) for v in synth.BASE)
        (self.d / "cfg.toml").write_text(f"""
[site]
origin_geodetic = [{o}]
[defaults]
sigma_z = 0.01
sigma_h = 0.01
wander_prior = 0.02
[grid]
cell = 2.0
capture_radius = 1.5
[output]
dir = "out"
[[surveys]]
id = "season"
date = "2026-12-01"
frame = "local"
reader = "soundings"
path = "season.gpkg"
[surveys.pass_rule]
from_file = true
[[surveys]]
id = "check"
date = "2026-12-02"
frame = "local"
reader = "logger_session"
path = "in"
sessions = ["{NAME}"]
[[growth]]
from = "season"
to = "check"
""")
        with contextlib.redirect_stdout(_io.StringIO()), contextlib.redirect_stderr(_io.StringIO()):
            rc = cli.main(["run", str(self.d / "cfg.toml")])
        self.assertEqual(rc, 0)
        g = np.load(self.d / "out" / "growth_season_check.npz")
        m = g["mask"] & np.isfinite(g["dz"])
        self.assertTrue(m.any())
        self.assertLess(float(np.max(np.abs(g["dz"][m]))), 1e-6)
        self.assertTrue(math.isfinite(float(np.mean(g["dz"][m]))))


if __name__ == "__main__":
    unittest.main()
