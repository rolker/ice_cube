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


def make_session(root, name="2026-12-01_10-00-00"):
    """A 0.5 session with a ref (still 0-25 s), a plumb stop, a leaning stop, and a surface
    capture on a third stop; GGA + GST at 4 Hz, gravity at 100 Hz."""
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
                    f"0.0020,0.0020,0.0030,1.0,,")
    ev = synth.events([(1, "gps connected bluetooth Test-Rover"), (2, "pole_height=1.800 changed")])
    d = root / name
    d.mkdir(parents=True)
    (d / "events.csv").write_text(ev)
    (d / "gps.ubx").write_bytes(bytes(ubx))
    (d / "gps_chunks.csv").write_text("\n".join(chunks) + "\n")
    (d / "imu.csv").write_text("\n".join(imu) + "\n")
    (d / "points.csv").write_text("\n".join([synth.POINTS_HDR] + rows) + "\n")
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
