"""The soundings builder on made-up data near 0 N 0 E: NMEA parsing, fix stretches and the
false-fix flag, pole stops, the IMU plumb check, the base history, sigmas, and a full build
into a GeoPackage. Run: python3 -m unittest discover -s analysis/qgis/tests"""
import contextlib
import csv
import io
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from functools import reduce
from operator import xor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gpkg_writer as gw  # noqa: E402
import import_sessions as imp  # noqa: E402
import soundings as snd  # noqa: E402
import synth  # noqa: E402
from local_frame import LocalFrame  # noqa: E402


def gst(t, sig=0.01):
    tod = t.strftime("%H%M%S.") + f"{t.microsecond // 10000:02d}"
    body = f"GNGST,{tod},0.5,0.02,0.01,45.0,{sig:.3f},{sig:.3f},{sig * 1.5:.3f}"
    return f"${body}*{reduce(xor, body.encode(), 0):02X}\r\n".encode()


def epochs_from(track):
    """Epochs (local frame about synth.BASE, pole 1.8 m) from [(s, e, n, u_antenna, fix)]."""
    data = b"".join(synth.nmea_gga(synth.START + timedelta(seconds=s), *synth.offset(e, n, u), fix=f)
                    + gst(synth.START + timedelta(seconds=s)) for s, e, n, u, f in track)
    ep = snd.read_nmea(data, synth.START)
    fr = LocalFrame(*synth.BASE)
    snd.localise(ep, fr, snd.BaseHistory(synth.BASE), lambda t, ns: 1.8)
    return ep


class TestParsing(unittest.TestCase):
    def test_nmea_gga_gst(self):
        ep = epochs_from([(k / 4, 1.0, 2.0, 1.8, 4) for k in range(8)])
        self.assertEqual(len(ep), 8)
        self.assertAlmostEqual(ep.t[1] - ep.t[0], 0.25, places=3)
        self.assertAlmostEqual(ep.x[0], 1.0, delta=0.01)
        self.assertAlmostEqual(ep.y[0], 2.0, delta=0.01)
        self.assertAlmostEqual(ep.z[0], 0.0, delta=0.005)
        self.assertAlmostEqual(ep.gst_u[0], 0.015, places=4)

    def test_swmaps_time_zone(self):
        t = snd.parse_swmaps_time("12/01/2026 05:00:00.500 EST")
        self.assertEqual(snd.iso(t), "2026-12-01T10:00:00.50Z")
        t = snd.parse_swmaps_time("12/01/2026 1:00:00 PM EDT")
        self.assertEqual(snd.iso(t), "2026-12-01T17:00:00.00Z")
        self.assertIsNone(snd.parse_swmaps_time("12/01/2026 05:00:00 XYZ"))


class TestStretchesAndStops(unittest.TestCase):
    def test_false_fix_flag(self):
        tr = [(s / 4, 3.0, 0.0, 1.8, 1) for s in range(8)]                       # single: metres off
        tr += [(2 + s / 4, 0.0, 0.0, 1.8, 4) for s in range(40)]                  # first fix
        tr += [(12 + s / 4, 0.0, 0.0, 3.8, 5) for s in range(12)]                 # float 2 m high
        tr += [(15 + s / 4, 0.0, 0.0, 1.8, 4) for s in range(40)]                 # re-fix: 2 m jump
        ep = epochs_from(tr)
        snd.fix_stretches(ep, suspect_jump_m=1.0)
        first = ep.stretch.index(0); second = ep.stretch.index(1)
        self.assertIsNone(ep.jump[first])            # after single epochs: not a false-fix sign
        self.assertFalse(ep.suspect[first])
        self.assertAlmostEqual(ep.jump[second], 2.0, delta=0.01)
        self.assertTrue(ep.suspect[second])
        self.assertAlmostEqual(ep.fix_age[second + 8], 2.0, places=2)
        self.assertEqual(ep.stretch[ep.t.index(min(t for t in ep.t if t - ep.t[0] >= 12.5))], -1)

    def test_float_spell_parity_with_logger_fixguard(self):
        """#15, the logger FixGuardTest cases in local metres: a fix after 60 s of float with a
        0.48 m step is suspect by float spell (case A); after 5 s of float it is not; after
        single epochs the 2.2 m step has no jump (case C); a step inside a fix is a jump."""
        def run(track, **kw):
            ep = epochs_from(track)
            snd.fix_stretches(ep, suspect_jump_m=1.0, **kw)
            return ep, ep.stretch.index(0)
        flt = lambda t0, s, e=0.0, n=0.0, u=1.8: [(t0 + k / 4, e, n, u, 5) for k in range(int(s * 4))]  # noqa: E731
        fix = lambda t0, s, e=0.0, n=0.0, u=1.8: [(t0 + k / 4, e, n, u, 4) for k in range(int(s * 4))]  # noqa: E731
        ep, i = run(flt(0, 60) + fix(60, 5, n=0.48))
        self.assertAlmostEqual(ep.jump[i], 0.48, delta=0.01)
        self.assertTrue(ep.suspect[i])
        self.assertEqual(ep.suspect_why[i], "float_spell")
        self.assertAlmostEqual(ep.float_spell[i], 60.0, delta=0.01)
        ep, i = run(flt(0, 60) + fix(60, 5, n=0.48), float_spell_s=0)      # rule off
        self.assertFalse(ep.suspect[i])
        ep, i = run(flt(0, 5) + fix(5, 5, n=0.48))
        self.assertFalse(ep.suspect[i])
        self.assertAlmostEqual(ep.float_spell[i], 5.0, delta=0.01)
        ep, i = run(flt(0, 60) + fix(60, 5, n=1.5))
        self.assertEqual(ep.suspect_why[i], "jump;float_spell")
        ep, i = run([(k / 4, 0.0, 0.0, 1.8, 1) for k in range(12)] + fix(3, 5, e=1.2, n=1.5, u=0.8))
        self.assertIsNone(ep.jump[i])
        self.assertFalse(ep.suspect[i])
        self.assertIsNone(ep.float_spell[i])
        ep, _ = run(flt(0, 2) + fix(2, 5) + fix(7, 5, n=1.2))              # 1.2 m step between fixed epochs
        j = ep.stretch.index(1)
        self.assertAlmostEqual(ep.jump[j], 1.2, delta=0.01)
        self.assertEqual(ep.suspect_why[j], "jump")

    def test_dropout_parity_with_logger_fixguard(self):
        """#15, FixGuardTest shortDropoutInsideAFixKeepsTheStretch and
        floatSpellCarriesOverAShortDropout: a dropout (no position, single or DGPS) of 2 s or
        less inside a fix keeps the stretch, a longer one ends it, a float epoch always ends
        it; a float spell carries over a dropout of 2 s or less before the fix."""
        def run(track):
            ep = epochs_from(track)
            for k, (_, _, _, _, f) in enumerate(track):
                if f == 0:                                     # no position
                    ep.x[k] = ep.y[k] = ep.za[k] = ep.z[k] = None
            snd.fix_stretches(ep, suspect_jump_m=1.0, float_spell_s=20.0)
            return ep
        seg = lambda t0, s, q, e=0.0, n=0.0: [(t0 + k / 4, e, n, 1.8, q) for k in range(int(s * 4))]  # noqa: E731
        for q in (0, 1, 2):
            # fix 2..7.25 s, 7 dropout epochs, fix again 2 s after the last fixed epoch
            tr = seg(0, 2, 5) + seg(2, 5.25, 4) + seg(7.25, 1.75, q, e=3.0) + seg(9, 1, 4, e=0.1)
            tr += seg(10, 3, q, e=3.0) + seg(13, 1, 4, e=0.1)   # 3.25 s since the last fix: new
            ep = run(tr)
            i = ep.t.index(min(t for t in ep.t if t - ep.t[0] >= 9 - 1e-6))
            self.assertEqual(ep.stretch[i], 0, q)
            self.assertAlmostEqual(ep.fix_age[i], 7.0, delta=0.01)
            self.assertEqual(max(ep.stretch), 1, q)
        ep = run(seg(0, 0.25, 5) + seg(0.25, 0.25, 4) + seg(0.5, 0.25, 0) + seg(0.75, 0.25, 4, n=1.2))
        self.assertEqual(max(ep.stretch), 1)                 # a jump inside the fix across a dropout
        j = ep.stretch.index(1)
        self.assertAlmostEqual(ep.jump[j], 1.2, delta=0.01)
        self.assertEqual(ep.suspect_why[j], "jump")
        ep = run(seg(0, 0.25, 4) + seg(0.25, 0.25, 5) + seg(0.5, 0.25, 4))
        self.assertEqual(max(ep.stretch), 1)                 # one float epoch ends it
        for q in (0, 1, 2):                                  # 2026-11-21 shape, one-epoch dropout
            ep = run(seg(0, 60, 5) + seg(60, 0.25, q, e=3.0) + seg(60.25, 2, 4, n=0.48))
            i = ep.stretch.index(0)
            self.assertEqual(ep.suspect_why[i], "float_spell", q)
            self.assertAlmostEqual(ep.float_spell[i], 60.25, delta=0.01)
        ep = run(seg(0, 60, 5) + seg(60, 3, 1, e=3.0) + seg(63, 2, 4, n=0.48))
        self.assertFalse(ep.suspect[ep.stretch.index(0)])     # over 2 s: the spell is over

    def test_stops(self):
        tr, s = [], 0.0
        for k in range(20):                      # still 5 s, 3 mm wobble
            tr.append((s, 0.003 * (-1) ** k, 0.0, 1.8 + 0.002 * (-1) ** k, 4)); s += 0.25
        for k in range(20):                      # walk 0.5 m/s
            tr.append((s, 0.5 * (k + 1) / 4, 0.0, 1.8, 4)); s += 0.25
        for k in range(16):                      # still 4 s
            tr.append((s, 2.5, 0.0, 1.8, 4)); s += 0.25
        for k in range(12):                      # pole bobbing: height SD 3 cm, not a stop
            tr.append((s, 4.0, 0.0, 1.8 + 0.03 * (-1) ** k, 4)); s += 0.25
        ep = epochs_from(tr)
        snd.fix_stretches(ep)
        stops = snd.find_stops(ep, [True] * len(ep))
        self.assertEqual(len(stops), 2)
        (a0, a1), (b0, b1) = stops
        self.assertGreaterEqual(ep.t[a1] - ep.t[a0], 4.0)
        self.assertAlmostEqual(sum(ep.x[i] for i in range(b0, b1 + 1)) / (b1 - b0 + 1), 2.5, delta=0.01)

    def test_gravity_plumb(self):
        ns, g = [], []
        for k in range(300):                     # 3 s at 100 Hz: 1.5 s plumb, 1.5 s tilted 4 deg
            ns.append(k * 10_000_000)
            a = math.radians(4.0) if k >= 150 else 0.0
            g.append((9.81 * math.sin(a), 0.0, 9.81 * math.cos(a)))
        d1, s1, _ = snd.gravity_window(ns, g, 0, 1_400_000_000)
        d2, s2, _ = snd.gravity_window(ns, g, 1_600_000_000, 2_990_000_000)
        self.assertAlmostEqual(snd.angle_deg(d1, d2), 4.0, places=3)
        self.assertLess(s1, 1e-6)
        _, s3, _ = snd.gravity_window(ns, g, 1_000_000_000, 2_000_000_000)
        self.assertGreater(s3, 1.0)              # a window across the lean is not stable


class TestFrameAndSigma(unittest.TestCase):
    def test_base_history_shift(self):
        fr = LocalFrame(*synth.BASE)
        old = (synth.BASE[0], synth.BASE[1], synth.BASE[2] + 0.03)       # broadcast 3 cm higher before
        t0 = synth.START.timestamp()
        hist = snd.BaseHistory(synth.BASE, [(t0 - 100, t0, *old)])
        lat, lon, h = synth.offset(5.0, 5.0, 0.0)
        x0, y0, z0 = snd.to_local(fr, lat, lon, h, hist.shift(t0 - 50))
        x1, y1, z1 = snd.to_local(fr, lat, lon, h, hist.shift(t0 + 50))
        self.assertAlmostEqual(z0 - z1, -0.03, places=4)                 # rover heights read 3 cm high then
        self.assertAlmostEqual(x0 - x1, 0.0, places=4)

    def test_capture_sigma_inputs(self):
        sg = snd.DEFAULTS["sigma"]
        h = math.hypot(0.006, 0.008) / math.sqrt(2)
        self.assertEqual(snd.capture_sigma_inputs(sg, "tilt", 0.03, 0.02, 0.02, 0.012, 0.006, 0.008),
                         (0.012, h, 0.0))
        single = (0.03, math.hypot(0.02, 0.02) / math.sqrt(2), sg["tilt_bubble_deg"])
        self.assertEqual(snd.capture_sigma_inputs(sg, "tilt", 0.03, 0.02, 0.02, 0.012, None, 0.008), single)
        for bad in (float("nan"), float("inf"), -0.006):   # non-finite or negative as missing, like SoundingSigma.java
            self.assertEqual(snd.capture_sigma_inputs(sg, "tilt", 0.03, 0.02, 0.02, 0.012, bad, 0.008), single)
        self.assertEqual(snd.capture_sigma_inputs(sg, "single", 0.03, 0.02, 0.02, 0.012, 0.006, 0.008), single)
        self.assertEqual(snd.capture_sigma_inputs(sg, None, 0.03, 0.02, 0.02), single)
        self.assertEqual(snd.capture_sigma_inputs(sg, "single", None, None, None), (None, 0.0, sg["tilt_bubble_deg"]))

    def test_app_version(self):
        self.assertGreater(snd._version("0.10"), snd._version("0.6"))
        self.assertGreaterEqual(snd._version("0.6.1-debug"), (0, 6))
        self.assertLess(snd._version(None), (0, 6))
        self.assertLess(snd._version("dev"), (0, 6))

    def test_sigma_terms(self):
        sg = snd.DEFAULTS["sigma"]
        a = snd.sigma_terms(sg, sd_u=0.002, age=1.0, fix_age=200, pole_m=1.6, tilt_deg=1.0)
        self.assertAlmostEqual(a["sigma_rtk"], 0.01)
        self.assertEqual(a["sigma_settle"], 0.0)
        self.assertAlmostEqual(a["sigma_z"], math.sqrt(0.01**2 + (1.6 * (1 - math.cos(math.radians(1))) + 0.01) ** 2
                                                       + 0.015**2), places=6)
        b = snd.sigma_terms(sg, sd_u=0.002, age=12.0, fix_age=0.0, pole_m=1.6, on_mark=True)
        self.assertAlmostEqual(b["sigma_age"], 0.01)
        self.assertAlmostEqual(b["sigma_settle"], 0.08)
        self.assertEqual(b["sigma_ground"], 0.0)
        self.assertAlmostEqual(b["sigma_xy"], math.hypot(0.01, 1.6 * math.sin(math.radians(1))), places=6)


def make_session(root, name="2026-12-01_10-00-00", tilt_sd=None):
    """A 0.5 session with a ref (still 0-25 s), a plumb stop, a leaning stop, and a surface
    capture on a third stop; GGA + GST at 4 Hz (GST height sigma 1.5 cm), gravity at 100 Hz.
    With tilt_sd (e, n, u) the points.csv has the 0.6 columns and the surface capture is a
    tilt capture with those sds."""
    plan = [(0, 25, 0.0), (30, 36, 3.0), (40, 46, 6.0), (50, 56, 9.0)]
    track = []
    for k in range(240):
        s = k / 4
        e = None
        for a, b, x in plan:
            if a <= s < b:
                e = x
        if e is None:                            # walking between stops
            prev = max((p for p in plan if p[1] <= s), key=lambda p: p[1])
            nxt = min((p for p in plan if p[0] > s), key=lambda p: p[0], default=(60, 60, 12.0))
            e = prev[2] + (nxt[2] - prev[2]) * (s - prev[1]) / (nxt[0] - prev[1])
        track.append((s, e))
    ubx, chunks = bytearray(), ["elapsed_ns,byte_offset,length"]
    for s, e in track:
        t = synth.START + timedelta(seconds=s)
        data = synth.nmea_gga(t, *synth.offset(e, 2.0, 1.8)) + gst(t)
        chunks.append(f"{synth.T0_NS + int(s * 1e9) + 50_000_000},{len(ubx)},{len(data)}")
        ubx += data
    imu = ["sensor,event_ns,x,y,z"]
    for k in range(6000):
        s = k / 100
        a = math.radians(4.0) if 40 <= s < 46 else 0.0
        imu.append(f"grav,{synth.T0_NS + int(s * 1e9)},{9.81 * math.sin(a):.5f},0.0,{9.81 * math.cos(a):.5f}")
    rows = []
    for n, cls, s, e in ((1, "ref", 24.75, 0.0), (2, "surface", 55.0, 9.0)):
        lat, lon, h = synth.offset(e, 2.0, 1.8)
        t = synth.START + timedelta(seconds=s)
        rows.append(f"{n},{cls},{synth.T0_NS + int(s * 1e9)},{t.strftime('%Y-%m-%dT%H:%M:%S.')}{t.microsecond // 10000:02d}Z,"
                    f"{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},4,{100 if cls == 'ref' else 5},"
                    f"0.0020,0.0020,0.0030,1.0,,"
                    + ("" if tilt_sd is None else ",200.0,0.01,0," + (
                        "ref,,,,,," if cls == "ref" else "tilt,5,0.005,200," + ",".join(f"{v:.4f}" for v in tilt_sd))))
    ev = synth.events([(1, "gps connected bluetooth Test-Rover"), (2, "pole_height=1.800 changed")],
                      app="0.5" if tilt_sd is None else "0.6")   # the 0.6 points.csv comes from app 0.6
    d = root / name
    d.mkdir(parents=True)
    (d / "events.csv").write_text(ev)
    (d / "gps.ubx").write_bytes(bytes(ubx))
    (d / "gps_chunks.csv").write_text("\n".join(chunks) + "\n")
    (d / "imu.csv").write_text("\n".join(imu) + "\n")
    hdr = synth.POINTS_HDR if tilt_sd is None else synth.POINTS_HDR_06
    (d / "points.csv").write_text("\n".join([hdr] + rows) + "\n")
    return d


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_end_to_end(self):
        ses = make_session(self.d / "sessions")
        gpkg = self.d / "season.gpkg"
        with contextlib.redirect_stdout(io.StringIO()):
            imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
        # another network: one point, moved by a measured shift
        lat, lon, h = synth.offset(4.0, 4.0, 0.25)
        with open(self.d / "other.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ID", "Remarks", "Time", "Latitude", "Longitude", "Elevation", "Fix ID", "Instrument Ht",
                        "Horizontal Accuracy", "Vertical Accuracy", "Averaged Count", "Start Time"])
            w.writerow(["1.0", "", "12/01/2026 04:00:00.000 EST", f"{lat:.9f}", f"{lon:.9f}", f"{h:.4f}", "4.0", "1.8",
                        "0.01", "0.012", "10.0", "12/01/2026 03:59:55.000 EST"])
            w.writerow(["2.0", "", "12/01/2026 04:00:30.000 EST", f"{lat:.9f}", f"{lon:.9f}", f"{h:.4f}", "5.0", "1.8",
                        "0.01", "0.012", "10.0", ""])
        (self.d / "corr.csv").write_text("session,n,action,x_m,y_m,z_m,sigma_z,note\n"
                                         f"{ses.name},2,replace,9.0,2.0,0.5,0.03,test\n")
        cfg = {"season": {"gpkg": str(gpkg)}, "soundings": {
            "settle_s": 5.0, "csv": "out.csv", "corrections_csv": "corr.csv",
            "sources": [{"id": "logger", "type": "logger", "session_paths": [str(ses)], "prior_m": 0.015},
                        {"id": "other", "type": "swmaps_points", "session": "other", "points_csv": ["other.csv"],
                         "shift_enu": [0.1, 0.2, -0.3], "base_history": False, "prior_m": 0.04,
                         "common_prior_m": 0.07}]}}
        rows = snd.build(cfg, self.d, log=lambda *a: None)
        caps = {r["n"]: r for r in rows if r["kind"] == "capture"}
        self.assertEqual(caps[1]["class"], "anchor")
        self.assertEqual(caps[2]["class"], "ground")
        self.assertAlmostEqual(caps[2]["z_m"], 0.5)                     # corrections hook
        self.assertIn("ppk", caps[2]["flags"])
        stops = sorted((r for r in rows if r["kind"] == "stop" and r["source"] == "logger"), key=lambda r: r["x_m"])
        near = lambda x: [r for r in stops if abs(r["x_m"] - x) < 0.1]  # noqa: E731
        self.assertTrue(near(3.0) and near(6.0) and near(9.0) and near(0.0))
        plumb = near(3.0)[0]
        self.assertEqual(plumb["use"], 1, plumb["flags"])
        self.assertLess(plumb["tilt_deg"], 0.5)
        self.assertAlmostEqual(plumb["z_m"], 0.0, delta=0.005)
        self.assertIn("not_plumb", near(6.0)[0]["flags"])
        self.assertAlmostEqual(near(6.0)[0]["tilt_deg"], 4.0, delta=0.2)
        self.assertIn("dup_capture", near(9.0)[0]["flags"])
        self.assertIn("dup_capture", near(0.0)[0]["flags"])
        other = [r for r in rows if r["source"] == "other"]
        good = [r for r in other if r["use"]]
        self.assertEqual(len(good), 1)
        self.assertIn("float", [r for r in other if not r["use"]][0]["flags"])
        self.assertAlmostEqual(good[0]["x_m"], 4.1, delta=0.002)
        self.assertAlmostEqual(good[0]["y_m"], 4.2, delta=0.002)
        self.assertAlmostEqual(good[0]["z_m"], 0.25 - 0.3, delta=0.002)   # Elevation is the ground
        self.assertEqual(good[0]["common_prior_m"], 0.07)
        con = sqlite3.connect(gpkg)
        self.assertEqual(con.execute("SELECT count(*) FROM soundings").fetchone()[0], len(rows))
        con.close()
        self.assertTrue((self.d / "out.csv").is_file())
        # rebuilding replaces the table, not appends
        snd.build(cfg, self.d, log=lambda *a: None)
        con = sqlite3.connect(gpkg)
        self.assertEqual(con.execute("SELECT count(*) FROM soundings").fetchone()[0], len(rows))
        con.close()

    def test_voided_capture_flagged_not_used_unless_included(self):
        """A capture flagged void in the logger (#27) stays a row, flag void, use 0, and is
        counted; include_voided uses it like any other."""
        ses = make_session(self.d / "sessions")
        p = ses / "points.csv"
        lines = p.read_text().splitlines()
        lines = [lines[0] + ",void,void_reason"] + [
            l + (",1,accidental" if l.startswith("2,") else ",0,") for l in lines[1:]]
        p.write_text("\n".join(lines) + "\n")
        gpkg = self.d / "season.gpkg"
        with contextlib.redirect_stdout(io.StringIO()):
            imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
        cfg = {"season": {"gpkg": str(gpkg)}, "soundings": {
            "settle_s": 5.0, "sources": [{"id": "logger", "type": "logger", "prior_m": 0.015}]}}
        logged = []
        rows = snd.build(cfg, self.d, log=logged.append, write=False)
        cap = {r["n"]: r for r in rows if r["kind"] == "capture"}
        self.assertEqual(len(cap), 2)                           # kept, not dropped
        self.assertIn("void:accidental", snd.flags_of(cap[2]))
        self.assertEqual(cap[2]["use"], 0)
        self.assertEqual(cap[1]["use"], 1)
        self.assertTrue(any("voided captures (flagged in the logger): 1, left out of use" in l for l in logged))
        cfg["soundings"]["include_voided"] = True
        rows = snd.build(cfg, self.d, log=lambda *a: None, write=False)
        cap = {r["n"]: r for r in rows if r["kind"] == "capture"}
        self.assertNotIn("void:accidental", snd.flags_of(cap[2]))
        self.assertIn("voided_included", snd.flags_of(cap[2]))   # the trace stays
        self.assertEqual(cap[2]["use"], 1)

    def _voided_stops(self, reason, include_voided=False, photo_row=False):
        """Build the make_session data with capture 2 (surface, on the stop at x = 9 m)
        voided for `reason`; returns (the stop rows at x = 9 m, the capture row 2, the log).
        photo_row adds row 3, the photo row the logger makes for a voided capture's photos
        (VoidRule.photoRowFor: class photo, the voided row's utc and position)."""
        ses = make_session(self.d / f"{reason.replace(' ', '_')}_{int(include_voided)}_{int(photo_row)}"
                           / "sessions")
        p = ses / "points.csv"
        lines = p.read_text().splitlines()
        if photo_row:
            lines.append("3,photo," + [l for l in lines if l.startswith("2,")][0][len("2,surface,"):]
                         + "photos of voided n=2")
        lines = [lines[0] + ",void,void_reason"] + [
            l + (f",1,{reason}" if l.startswith("2,") else ",0,") for l in lines[1:]]
        p.write_text("\n".join(lines) + "\n")
        gpkg = ses.parent.parent / "season.gpkg"
        with contextlib.redirect_stdout(io.StringIO()):
            imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
        cfg = {"season": {"gpkg": str(gpkg)}, "soundings": {
            "settle_s": 5.0, "include_voided": include_voided,
            "sources": [{"id": "logger", "type": "logger", "session_paths": [str(ses)], "prior_m": 0.015}]}}
        logged = []
        rows = snd.build(cfg, self.d, log=logged.append, write=False)
        stops = [r for r in rows if r["kind"] == "stop" and abs(r["x_m"] - 9.0) < 0.1]
        cap = [r for r in rows if r["kind"] == "capture" and r["n"] == 2][0]
        return stops, cap, logged

    def test_capture_voided_as_moved_flags_its_stop(self):
        """A capture voided as 'moved' (#27) also flags the pole stop around it: kept, use 0,
        flag void_stop naming the capture and reason, counted in the log; include_voided
        brings the capture back and its stop is a dup_capture as for any capture."""
        stops, cap, logged = self._voided_stops("moved")
        self.assertEqual(len(stops), 1)
        fl = snd.flags_of(stops[0])
        self.assertIn("void_stop:c2:moved", fl)
        self.assertNotIn("dup_capture", fl)
        self.assertEqual(stops[0]["use"], 0)
        self.assertTrue(snd.is_voided(stops[0]))
        self.assertIn("void:moved", snd.flags_of(cap))
        self.assertTrue(any("pole stops at captures voided (not as accidental): 1" in l for l in logged))
        stops, cap, _ = self._voided_stops("bad pole placement")
        self.assertIn("void_stop:c2:bad pole placement", snd.flags_of(stops[0]))
        self.assertEqual(stops[0]["use"], 0)
        stops, cap, _ = self._voided_stops("moved", include_voided=True)
        fl = snd.flags_of(stops[0])
        self.assertIn("dup_capture", fl)
        self.assertFalse(any(f.startswith("void_stop:") for f in fl))
        self.assertIn("voided_included", snd.flags_of(cap))
        self.assertEqual(cap["use"], 1)

    def test_capture_voided_as_accidental_leaves_its_stop(self):
        """A capture voided as 'accidental' (#27) flags only the capture: the pole stop at the
        same time is neither a dup_capture nor void_stop, and is used."""
        stops, cap, logged = self._voided_stops("accidental")
        self.assertEqual(len(stops), 1)
        fl = snd.flags_of(stops[0])
        self.assertFalse(any(f.startswith("void_stop:") or f == "dup_capture" for f in fl), fl)
        self.assertEqual(stops[0]["use"], 1, fl)
        self.assertIn("void:accidental", snd.flags_of(cap))
        self.assertEqual(cap["use"], 0)
        self.assertFalse(any("pole stops at captures voided" in l for l in logged))

    def test_capture_voided_as_accidental_with_photo_row_leaves_its_stop(self):
        """An accidental capture with photos and no earlier live capture gets a photo row at
        its time (#27); that row makes no capture window either, so the pole stop is used."""
        stops, cap, _ = self._voided_stops("accidental", photo_row=True)
        self.assertEqual(len(stops), 1)
        fl = snd.flags_of(stops[0])
        self.assertFalse(any(f.startswith("void_stop:") or f == "dup_capture" for f in fl), fl)
        self.assertEqual(stops[0]["use"], 1, fl)
        # with include_voided the voided capture's own window makes the stop a dup_capture
        stops, _, _ = self._voided_stops("accidental", include_voided=True, photo_row=True)
        self.assertIn("dup_capture", snd.flags_of(stops[0]))

    def test_photo_row_window_only_when_not_of_a_voided_capture(self):
        """A photo row makes a capture window unless it is the row a flag made for a voided
        capture's photos: same utc and a note of exactly "photos of voided n=<n>" (#27)."""
        caps = [{"n": 1, "utc": "2026-01-01T00:00:10Z", "class": "ground", "void": 1},
                {"n": 2, "utc": "2026-01-01T00:00:10Z", "class": "photo", "void": 0, "note": "photos of voided n=1"},
                {"n": 3, "utc": "2026-01-01T00:01:10Z", "class": "photo", "void": 0, "note": None}]
        t = snd.parse_iso(caps[2]["utc"])
        self.assertEqual(snd.clist_windows(caps), [(t - 1.0, t)])
        self.assertEqual(len(snd.clist_windows(caps, include_voided=True)), 2)   # the capture's own and the other photo row

    def test_real_photo_in_the_same_second_as_a_voided_capture_keeps_its_window(self):
        t0 = "2026-01-01T00:00:10Z"
        voided = {"n": 1, "utc": t0, "class": "ground", "void": 1}
        real = {"n": 2, "utc": t0, "class": "photo", "void": 0, "note": ""}
        moved = {"n": 3, "utc": t0, "class": "photo", "void": 0, "note": "photos of voided n=1"}
        t = snd.parse_iso(t0)
        self.assertEqual(snd.clist_windows([voided, real]), [(t - 1.0, t)])   # a real PHOTO
        self.assertEqual(snd.clist_windows([voided, moved]), [])              # the moved-photo row
        self.assertEqual(snd.clist_windows([voided, real, moved]), [(t - 1.0, t)])
        # n=1 never matches n=12 and the other way round; a longer note does not match
        v12 = {"n": 12, "utc": t0, "class": "photo", "void": 0, "note": "photos of voided n=12"}
        self.assertEqual(len(snd.clist_windows([voided, v12])), 1)
        v12c = {"n": 12, "utc": t0, "class": "ground", "void": 1}
        self.assertEqual(len(snd.clist_windows([v12c, moved])), 1)
        edited = dict(moved, note="photos of voided n=1 (checked)")
        self.assertEqual(len(snd.clist_windows([voided, edited])), 1)

    def test_void_stop_windows(self):
        """Only captures voided for a reason other than 'accidental' make a void-stop window,
        and none under include_voided."""
        caps = [{"n": 1, "utc": "2026-01-01T00:00:10Z", "class": "ground", "void": 1, "void_reason": "accidental"},
                {"n": 2, "utc": "2026-01-01T00:01:10Z", "class": "ref", "void": 1, "void_reason": "moved"},
                {"n": 3, "utc": "2026-01-01T00:02:10Z", "class": "ground", "void": 1, "void_reason": None},
                {"n": 4, "utc": "2026-01-01T00:03:10Z", "class": "ground", "void": 0, "void_reason": None}]
        w = snd.void_stop_windows(caps)
        self.assertEqual([x[2] for x in w], ["c2:moved", "c3:flagged"])
        t = snd.parse_iso(caps[1]["utc"])
        self.assertEqual(w[0][:2], (t - 30.0, t))
        self.assertEqual(snd.void_stop_windows(caps, include_voided=True), [])

    def test_voided_capture_makes_no_dup_capture_window(self):
        """A capture flagged void (#27) makes no capture window, so a real pole stop at the
        same time is not dropped as dup_capture; include_voided keeps its window."""
        caps = [{"utc": "2026-01-01T00:00:10Z", "class": "ground", "void": 0},
                {"utc": "2026-01-01T00:01:10Z", "class": "ref", "void": 1}]
        self.assertEqual(len(snd.clist_windows(caps)), 1)
        self.assertEqual(len(snd.clist_windows(caps, include_voided=True)), 2)
        t = snd.parse_iso(caps[0]["utc"])
        self.assertEqual(snd.clist_windows(caps), [(t - 1.0, t)])

    def test_voided_rows_make_no_site_and_skip_suspect_check(self):
        """A voided ref neither makes a site nor lets ground rows join one (#27), and a voided
        row in a suspect pass is not compared, so it cannot clear suspect_jump."""
        r = lambda **k: snd.new_row(**{"source": "s", "kind": "capture", "use": 1, "sigma_z": 0.02, **k})  # noqa: E731
        rows = [r(sid="v1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.0, pass_id="p1", flags="void:moved"),
                r(sid="g1", **{"class": "ground"}, x_m=0.02, y_m=0.0, z_m=1.0, pass_id="p2", kind="point")]
        for x in rows:
            snd.finish_use(x)
        sites, joined = snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=0.015)
        self.assertEqual((sites, joined), ([], 0))
        self.assertEqual(rows[1]["class"], "ground")
        # a voided ground capture near a site does not join it as an anchor
        rows = [r(sid="a1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.0, pass_id="p1", anchor="site1"),
                r(sid="v3", **{"class": "ground"}, x_m=0.02, y_m=0.0, z_m=1.0, pass_id="p2", flags="void:moved")]
        for x in rows:
            snd.finish_use(x)
        sites, joined = snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=0.015)
        self.assertEqual((len(sites), joined), (1, 0))
        self.assertEqual((rows[1]["class"], rows[1]["anchor"]), ("ground", None))
        # a suspect pass whose only comparable row is voided stays suspect
        rows = [r(sid="a1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.00, pass_id="p1", anchor="site1"),
                r(sid="v2", **{"class": "anchor"}, x_m=0.05, y_m=0.0, z_m=1.00, pass_id="p4",
                  flags="suspect_jump,void:moved"),
                r(sid="a3", **{"class": "anchor"}, x_m=5.0, y_m=5.0, z_m=3.0, pass_id="p4", flags="suspect_jump")]
        for x in rows:
            snd.finish_use(x)
        snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=0.015)
        ok, bad = snd.verify_suspect(rows)
        self.assertEqual((ok, bad), (0, 1))
        self.assertIn("suspect_jump", snd.flags_of(rows[2]))

    def _import(self, src):
        gpkg = self.d / "season.gpkg"
        with contextlib.redirect_stdout(io.StringIO()):
            imp.main([str(src), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                      "--origin", *map(str, synth.BASE), "--no-photos"])
        return gpkg

    def _build_logger(self, gpkg, **src):
        logged = []
        cfg = {"season": {"gpkg": str(gpkg)},
               "soundings": {"sources": [{"id": "logger", "type": "logger", **src}]}}
        rows = snd.build(cfg, self.d, log=lambda *a: logged.append(" ".join(map(str, a))), write=False)
        return {r["n"]: r for r in rows if r["kind"] == "capture"}, "\n".join(logged)

    def test_tilt_capture_sigma(self):
        """No GST (no session_paths): the tilt capture takes its own sds and tilt 0; one
        missing its tilt sds and a single capture take the single rule."""
        gpkg = self._import(synth.write_zip(self.d / "s.zip", synth.session_v06()))
        caps, logged = self._build_logger(gpkg)
        sg = snd.DEFAULTS["sigma"]
        t = caps[5]
        self.assertEqual(t["tilt_deg"], 0.0)
        self.assertAlmostEqual(t["sigma_pole"], sg["tip_sink"], places=12)
        self.assertAlmostEqual(t["sigma_rtk"], 0.012, places=12)       # tilt_sd_u, not the 4 cm scatter
        self.assertAlmostEqual(t["sigma_xy"], sg["floor_h"], places=12)  # hypot(6, 8 mm)/sqrt 2 < floor
        m = caps[6]                                                    # tilt without sds: single rule
        self.assertEqual(m["tilt_deg"], sg["tilt_bubble_deg"])
        self.assertAlmostEqual(m["sigma_rtk"], 0.04, places=12)
        self.assertGreater(m["sigma_pole"], sg["tip_sink"])
        self.assertEqual(caps[1]["tilt_deg"], sg["tilt_bubble_deg"])
        self.assertNotIn("warning", logged)

    def test_tilt_capture_gst_floors(self):
        """With GST (1.5 cm height sigma) above the tilt sd_u the GST term wins."""
        ses = make_session(self.d / "sessions", tilt_sd=(0.004, 0.004, 0.005))
        gpkg = self._import(ses)
        caps, _ = self._build_logger(gpkg, session_paths=[str(ses)])
        t = caps[2]
        self.assertEqual(t["tilt_deg"], 0.0)
        self.assertAlmostEqual(t["gst_u_m"], 0.015, places=6)
        self.assertAlmostEqual(t["sigma_rtk"], max(0.005, t["gst_u_m"]), places=12)
        self.assertAlmostEqual(t["sigma_pole"], snd.DEFAULTS["sigma"]["tip_sink"], places=12)

    def test_old_gpkg_without_tilt_columns(self):
        gpkg = self._import(synth.write_zip(self.d / "s.zip", synth.session_v06()))
        con = gw.connect(gpkg)
        for k, _ in imp.LOGGER_06:
            con.execute(f"ALTER TABLE captures DROP COLUMN {k}")
        con.commit()
        con.close()
        caps, logged = self._build_logger(gpkg)
        self.assertEqual(caps[5]["tilt_deg"], snd.DEFAULTS["sigma"]["tilt_bubble_deg"])   # single rule
        self.assertEqual(logged.count("warning"), 1)
        self.assertIn("re-import the full session sources", logged)
        self.assertNotIn("--force", logged)

    def test_empty_tilt_columns_of_new_app_warn(self):
        gpkg = self._import(synth.write_zip(self.d / "s.zip", synth.session_v06()))
        con = gw.connect(gpkg)
        con.execute("UPDATE captures SET method=NULL, tilt_sd_e_m=NULL, tilt_sd_n_m=NULL, tilt_sd_u_m=NULL")
        con.commit()
        con.close()
        _, logged = self._build_logger(gpkg)
        self.assertEqual(logged.count("warning"), 1)
        con = gw.connect(gpkg)
        con.execute("UPDATE sessions SET app='0.5'")       # older app: NULLs are expected
        con.commit()
        con.close()
        self.assertNotIn("warning", self._build_logger(gpkg)[1])

    def test_unknown_app_version_counts_as_new(self):
        """No readable app version: the session may be 0.6, so it warns while not yet re-read
        at the schema of the 0.6 columns (SCHEMA_TILT); once re-read at it or later, empty tilt
        values are what its source holds."""
        gpkg = self._import(synth.write_zip(self.d / "s.zip", synth.session_v06()))
        con = gw.connect(gpkg)
        con.execute("UPDATE sessions SET app=NULL, schema_version=NULL")     # stale
        con.execute("UPDATE captures SET method=NULL, tilt_sd_e_m=NULL, tilt_sd_n_m=NULL, tilt_sd_u_m=NULL")
        con.commit()
        con.close()
        logged = self._build_logger(gpkg)[1]
        self.assertEqual(logged.count("warning"), 1)
        self.assertIn("no method/tilt values", logged)
        con = gw.connect(gpkg)
        con.execute("UPDATE sessions SET schema_version=?", (imp.SCHEMA_TILT - 1,))   # still stale
        con.commit()
        con.close()
        self.assertEqual(self._build_logger(gpkg)[1].count("warning"), 1)
        for schema in (imp.SCHEMA_TILT, imp.SCHEMA_VERSION):   # re-read at 3: stale for 4 only
            con = gw.connect(gpkg)
            con.execute("UPDATE sessions SET schema_version=?", (schema,))
            con.commit()
            con.close()
            self.assertNotIn("warning", self._build_logger(gpkg)[1])
        con = gw.connect(gpkg)
        for k, _ in imp.LOGGER_06:
            con.execute(f"ALTER TABLE captures DROP COLUMN {k}")
        con.commit()
        con.close()
        logged = self._build_logger(gpkg)[1]
        self.assertEqual(logged.count("warning"), 1)
        self.assertIn("has no", logged)

    def test_mixed_sessions_warn_naming_the_stranded_one(self):
        """One 0.6 session with its tilt columns and one without: warn, naming only that one."""
        src = self.d / "in"
        synth.write_zip(src / "a.zip", synth.session_v06())
        synth.write_zip(src / "b.zip", synth.session_v06("2026-12-02_10-00-00"))
        gpkg = self._import(src)
        con = gw.connect(gpkg)
        con.execute("UPDATE captures SET method=NULL, tilt_sd_e_m=NULL, tilt_sd_n_m=NULL, tilt_sd_u_m=NULL "
                    "WHERE session='2026-12-02_10-00-00'")
        con.commit()
        con.close()
        logged = self._build_logger(gpkg)[1]
        self.assertEqual(logged.count("warning"), 1)
        self.assertIn("2026-12-02_10-00-00", logged)
        self.assertNotIn("2026-12-01_10-00-00", logged.split("warning")[1])

    def test_anchor_sites_and_verification(self):
        r = lambda **k: snd.new_row(**{"source": "s", "kind": "capture", "use": 1, "sigma_z": 0.02, **k})  # noqa: E731
        rows = [r(sid="a1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.00, pass_id="p1", anchor="site1"),
                r(sid="a2", **{"class": "anchor"}, x_m=0.1, y_m=0.0, z_m=1.01, pass_id="p2"),
                r(sid="g1", **{"class": "ground"}, x_m=0.05, y_m=0.02, z_m=1.005, pass_id="p3", kind="point"),
                r(sid="a3", **{"class": "anchor"}, x_m=0.05, y_m=0.05, z_m=1.02, pass_id="p4", flags="suspect_jump")]
        for x in rows:
            snd.finish_use(x)
        sites, joined = snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=0.015)
        self.assertEqual(len(sites), 1)
        self.assertEqual({x["anchor"] for x in rows}, {"site1"})
        self.assertEqual(joined, 1)
        ok, bad = snd.verify_suspect(rows)
        self.assertEqual((ok, bad), (1, 0))
        self.assertIn("suspect_verified", rows[3]["flags"])

    def test_float_spell_pass_can_be_verified(self):
        """#15: verify_suspect clears float_spell like suspect_jump; both become suspect_verified."""
        r = lambda **k: snd.new_row(**{"source": "s", "kind": "capture", "use": 1, "sigma_z": 0.02, **k})  # noqa: E731
        rows = [r(sid="a1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.00, pass_id="p1", anchor="site1"),
                r(sid="a3", **{"class": "anchor"}, x_m=0.05, y_m=0.05, z_m=1.01, pass_id="p4",
                  flags="suspect_jump,float_spell")]
        for x in rows:
            snd.finish_use(x)
        self.assertEqual(rows[1]["use"], 0)
        snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=0.015)
        ok, bad = snd.verify_suspect(rows)
        self.assertEqual((ok, bad), (1, 0))
        self.assertEqual(rows[1]["flags"], "suspect_verified")
        snd.finish_use(rows[1])
        self.assertEqual(rows[1]["use"], 1)

    def test_anchor_join_removes_configured_ground_sigma(self):
        sg = snd._merge(snd.DEFAULTS["sigma"], {"ground": 0.05})        # not the default 0.015
        terms = snd.sigma_terms(sg, sd_u=0.002, age=1.0, fix_age=200, pole_m=1.6, tilt_deg=1.0)
        want = math.sqrt(terms["sigma_z"] ** 2 - 0.05 ** 2)
        r = lambda **k: snd.new_row(**{"source": "s", "kind": "capture", "use": 1, "sigma_z": 0.02, **k})  # noqa: E731
        g = snd.new_row(source="s", kind="point", use=1, sid="g1", x_m=0.02, y_m=0.0, z_m=1.0, pass_id="p2",
                        **{"class": "ground"})
        g.update(terms)
        rows = [r(sid="a1", **{"class": "anchor"}, x_m=0.0, y_m=0.0, z_m=1.0, pass_id="p1", anchor="site1"), g]
        for x in rows:
            snd.finish_use(x)
        _, joined = snd.assign_anchors(rows, 0.3, 0.1, 0.1, ground_sigma=sg["ground"])
        self.assertEqual(joined, 1)
        self.assertEqual(g["class"], "anchor")
        self.assertEqual(g["sigma_ground"], 0.0)
        self.assertAlmostEqual(g["sigma_z"], want, places=9)
        self.assertAlmostEqual(g["sigma_z"], math.sqrt(sum(g[k] ** 2 for k in (
            "sigma_rtk", "sigma_age", "sigma_settle", "sigma_pole"))), places=9)


if __name__ == "__main__":
    unittest.main()


class TestTopoToGpkg(unittest.TestCase):
    def test_contours_and_points(self):
        import json
        import topo_to_gpkg
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            ses = make_session(d / "sessions")
            gpkg = d / "season.gpkg"
            with contextlib.redirect_stdout(io.StringIO()):
                imp.main([str(ses), "--gpkg", str(gpkg), "--base", *map(str, synth.BASE),
                          "--origin", *map(str, synth.BASE), "--no-photos"])
            run = d / "run"; run.mkdir()
            (run / "contours_g_lot.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"level": 1.0, "grid": "lot", "surface": "g", "interval": 0.25},
                 "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 1]]}},
                {"type": "Feature", "properties": {"level": 1.25, "grid": "lot", "surface": "g", "interval": 0.25},
                 "geometry": {"type": "LineString", "coordinates": [[0, 1], [1, 2]]}}]}))
            (run / "points_g.csv").write_text(
                "survey,sid,group,pass,anchor,x_m,y_m,z_m,bias_m,bias_sigma_m,z_corrected_m,sigma_z_m,sigma_xy_m\n"
                "a,a:1,s1,s1/s000,,1.0,2.0,0.10,0.02,0.01,0.08,0.02,0.02\n")
            topo_to_gpkg.load(run, gpkg, log=lambda *a: None)
            topo_to_gpkg.load(run, gpkg, log=lambda *a: None)          # replaced, not appended
            con = sqlite3.connect(gpkg)
            self.assertEqual(con.execute("SELECT count(*) FROM layer_topo_contours").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT name, stroke_rgba FROM layer_topo_contours ORDER BY level").fetchall()[0],
                             ("1.00", "60,60,60,255"))
            srs = con.execute("SELECT srs_id FROM gpkg_contents WHERE table_name='topo_points'").fetchone()[0]
            self.assertNotEqual(srs, 4326)
            row = con.execute("SELECT bias_m, z_corrected_m, session FROM topo_points").fetchone()
            self.assertEqual(row, (0.02, 0.08, "s1"))
            con.close()
