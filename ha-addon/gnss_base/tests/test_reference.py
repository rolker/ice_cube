"""Stage 6: the reference check (RTK static against a CORS station with a second rtkrcv).

Run from ha-addon/gnss_base with: python -m unittest tests.test_reference

Localhost only, against the fake rtkrcv (tests/fake_rtkrcv.py); the real one only in the
smoke test, skipped unless a binary is found (`RTKRCV` environment variable, or
rtkrcv-ex / rtkrcv on PATH). No real CORS is contacted, no real position is used: the
positions are synthetic, on the equator, and the login is a made-up sentinel that must
appear nowhere but in rtkrcv's config file.
"""
import ast
import asyncio
import io
import json
import logging
import os
import pty
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tty
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from gnss_base import __version__
from gnss_base import rtcm as rtcmdec
from gnss_base.__main__ import DEFAULTS, OVERRIDES, load_settings, reference_settings
from gnss_base.frames import crc24q
from gnss_base.hub import RtcmHub, Secret
from gnss_base.measured import (CONFIG_NAME, MeasuredSettings, OutputLines, RtkrcvRunner,
                                SolutionLog, parse_solution)
from gnss_base.reference import (CORRECTIONS_STALE_S, GAP_RESTART_S, NO_FIX_RED_S,
                                 SOLUTION_STALE_S, STARTING_S, ReferenceCheck,
                                 ReferenceSettings, SessionStats, TapServer, build_config,
                                 parse_expected_enu, parse_reset_utc, seconds_to_reset,
                                 verdict)
from tests.test_measured import FAKE, ROOT, eph, rtcm

SENTINEL = "Ref-S3ntinel-pw-5b2c"   # a made-up password: nowhere but in rtkrcv's config
GOLDEN = ROOT / "tests" / "reference_golden.conf"
# Made-up positions on the equator: ours at 0/0, the CORS 0.225 deg north (about 25 km).
OURS = rtcmdec.llh_to_ecef(0.0, 0.0, 0.0)
CORS = rtcmdec.llh_to_ecef(0.225, 0.0, 10.0)


def settings(**fields):
    base = dict(enabled=True, host="cors.example", port=2101, mountpoint="MP_1", user="user",
                password=Secret(SENTINEL))
    base.update(fields)
    return ReferenceSettings(**base)


def frame(payload):
    """A whole RTCM frame around `payload` (message number in its first 12 bits)."""
    head = bytes([0xD3, len(payload) >> 8, len(payload) & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


def arp_frame(x, y, z, antenna_height=None):
    from tests.test_app import arp_frame as make
    return make(x, y, z, antenna_height=antenna_height)


def frame_1033(antenna="GPPNULLANTENNA", receiver="LEICA GR30", firmware="4.31",
               antenna_serial="", receiver_serial="123", setup=0, station=7):
    def text(value):
        data = value.encode("ascii")
        return bytes([len(data)]) + data

    payload = (bytes([1033 >> 4, ((1033 & 15) << 4) | (station >> 8), station & 255])
               + text(antenna) + bytes([setup]) + text(antenna_serial) + text(receiver)
               + text(firmware) + text(receiver_serial))
    return frame(payload)


class Config(unittest.TestCase):
    def test_golden_file_and_settled_spellings(self):
        text = build_config(40001, 40002, 40003, settings())
        self.assertEqual(text, GOLDEN.read_text(), "tests/reference_golden.conf differs")
        for line in ("inpstr1-type   =tcpcli", "inpstr1-path   =127.0.0.1:40001",
                     "inpstr1-format =rtcm3", "inpstr2-type   =ntripcli",
                     f"inpstr2-path   =user:{SENTINEL}@cors.example:2101/MP_1",
                     "inpstr2-format =rtcm3", "inpstr2-nmeareq=off",
                     "outstr1-type   =tcpcli", "outstr1-path   =127.0.0.1:40002",
                     "outstr1-format =llh", "logstr2-type   =tcpcli",
                     "logstr2-path   =127.0.0.1:40003", "misc-reconnect =5000",
                     "pos1-posmode   =static", "pos1-frequency =l1+l2",
                     "pos1-ionoopt   =brdc", "pos1-tropopt   =saas", "pos1-sateph    =brdc",
                     "pos1-elmask    =15", "pos1-snrmask_r =on", "pos1-snrmask_b =off",
                     "pos1-snrmask_L1=35,35,35,35,35,35,35,35,35",
                     "pos1-snrmask_L2=35,35,35,35,35,35,35,35,35", "pos1-navsys    =45",
                     "pos1-posopt5   =on", "pos1-dynamics  =off",
                     "pos2-armode    =fix-and-hold", "pos2-gloarmode =off",
                     "pos2-bdsarmode =on", "pos2-arfilter  =on", "pos2-arthres   =3",
                     "pos2-arthresmin=3", "pos2-arthresmax=3", "pos2-arthres1  =0.1",
                     "pos2-varholdamb=0.1", "pos2-gainholdamb=0.01", "pos2-minfixsats=4",
                     "pos2-minholdsats=5", "pos2-mindropsats=10", "pos2-slipthres =0.05",
                     "pos2-rejphase  =2", "pos2-rejcode   =10", "pos2-maxage    =30",
                     "pos2-arelmask  =15", "pos2-elmaskhold=15",
                     "out-solformat  =llh", "out-solstatic  =all", "out-outhead    =off",
                     "ant1-postype   =single", "ant1-anttype   =", "ant1-antdelu   =0",
                     "ant2-postype   =rtcm", "ant2-anttype   =", "ant2-antdelu   =0",
                     "out-outsingle  =on"):
            self.assertIn(line + "\n", text, line)
        for absent in ("l1+2", "static-start", "rejgdop", "rejionno", "autocal",
                       "ant2-anttype   =*", "est-stec", "kinematic", "rcvantfile"):
            self.assertNotIn(absent, text)
        names = [line.split("=")[0].strip() for line in text.splitlines()
                 if line and not line.startswith("#")]
        self.assertEqual(len(names), len(set(names)), "an option is given twice")

    def test_masks_and_snr_off(self):
        text = build_config(1, 2, 3, settings(elevation_mask=10, snr_mask=0))
        for line in ("pos1-elmask    =10", "pos2-arelmask  =10", "pos2-elmaskhold=10",
                     "pos1-snrmask_r =off", "pos1-snrmask_L1=0,0,0,0,0,0,0,0,0"):
            self.assertIn(line + "\n", text)

    def test_bad_values_are_refused_without_the_password(self):
        for kwargs in (dict(elevation_mask=91), dict(snr_mask=-1), dict(snr_mask=None),
                       dict(elevation_mask=True)):
            with self.assertRaises(ValueError) as caught:
                build_config(1, 2, 3, settings(**kwargs))
            self.assertNotIn(SENTINEL, str(caught.exception))
        for ports in ((0, 2, 3), (1, 70000, 3), (1, 2, "3")):
            with self.assertRaises(ValueError):
                build_config(*ports, settings())


class Settings(unittest.TestCase):
    def test_missing_options(self):
        self.assertEqual(settings(host="", mountpoint="", user="").problem(),
                         "set reference_host, reference_mountpoint, reference_user")
        self.assertEqual(settings(user="").problem(), "set reference_user")
        self.assertEqual(settings().problem(), "")
        self.assertEqual(settings(password=Secret("")).problem(), "",
                         "an empty password is allowed (a caster may want none)")

    def test_fields_that_would_break_the_ntrip_path(self):
        cases = ((dict(user="a:b"), "reference_user has a ':'"),
                 (dict(user="a@b"), "reference_user has a '@'"),
                 (dict(host="h/x"), "reference_host has a '/'"),
                 (dict(host="h:2101"), "reference_host has a ':'"),
                 (dict(mountpoint="M P"), "reference_mountpoint has a space"),
                 (dict(mountpoint="M/P"), "reference_mountpoint has a '/'"),
                 (dict(password=Secret("p w")), "reference_password has a space"),
                 (dict(password=Secret("p#w")),
                  "reference_password has a '#' (rtkrcv reads it as a comment)"),
                 (dict(password=Secret("péw")),
                  "reference_password has a character that is not printable ASCII"),
                 (dict(password=Secret("p\tw")),
                  "reference_password has a character that is not printable ASCII"),
                 (dict(password=Secret("x" * 256)),
                  "reference_password is longer than 255 characters"),
                 (dict(port=0), "reference_port must be 1 to 65535"),
                 (dict(port=70000), "reference_port must be 1 to 65535"),
                 (dict(port="2101"), "reference_port must be 1 to 65535"),
                 (dict(port=True), "reference_port must be 1 to 65535"))
        for fields, expected in cases:
            self.assertEqual(settings(**fields).problem(), expected, fields)
        # A password with ':' , '@' or '/' is fine: rtkrcv splits the path at the last
        # '@' and the first ':' before it.
        self.assertEqual(settings(password=Secret("a:b@c/d")).problem(), "")
        # Each part fits, the whole path would not (rtkrcv's buffer is 1024 bytes).
        long = settings(host="h" * 255, mountpoint="m" * 255, user="u" * 255,
                        password=Secret("p" * 255))
        self.assertEqual(long.problem(),
                         "the login, host and mountpoint together are too long for rtkrcv")
        self.assertEqual(settings(elevation_mask=None).problem(),
                         "measured_elevation_mask must be a whole number from 0 to 90")
        self.assertEqual(settings(snr_mask=61).problem(),
                         "measured_snr_mask must be a whole number from 0 to 60")

    def test_problems_never_quote_the_password(self):
        for password in (SENTINEL + " x", SENTINEL + "#", "é" + SENTINEL,
                         "x" * 256 + SENTINEL):
            problem = settings(password=Secret(password)).problem()
            self.assertTrue(problem)
            self.assertNotIn(SENTINEL, problem)
        self.assertNotIn(SENTINEL, repr(settings()))
        self.assertNotIn(SENTINEL, str(settings()))

    def test_expected_offset(self):
        self.assertEqual(parse_expected_enu("0 0 0"), (0.0, 0.0, 0.0))
        self.assertEqual(parse_expected_enu("0.56, -1.20, 1.18"), (0.56, -1.2, 1.18))
        self.assertEqual(parse_expected_enu("+0.56 -1.2 +1.18"), (0.56, -1.2, 1.18))
        self.assertEqual(parse_expected_enu([1, 2, 3]), (1.0, 2.0, 3.0))
        for bad in ("1 2", "a b c", "nan 0 0", "1 2 3 4", "", "0 0 1e9"):
            with self.assertRaises(ValueError):
                parse_expected_enu(bad)
        self.assertEqual(settings(expected_enu="1 2").problem(),
                         "reference_expected_enu needs three numbers: east north up, metres")
        self.assertEqual(settings(expected_enu="0.56 -1.20 1.18").expected(),
                         (0.56, -1.2, 1.18))

    def test_reset_time(self):
        self.assertIsNone(parse_reset_utc(""))
        self.assertIsNone(parse_reset_utc("  "))
        self.assertEqual(parse_reset_utc("00:00"), 0)
        self.assertEqual(parse_reset_utc("7:30"), 27000)
        self.assertEqual(parse_reset_utc("23:59"), 86340)
        for bad in ("24:00", "12:60", "noon", "12", "12:3", "12:30:00"):
            with self.assertRaises(ValueError):
                parse_reset_utc(bad)
        self.assertEqual(settings(reset_utc="25:00").problem(),
                         "reference_reset_utc must be HH:MM (UTC, 24 h) or empty")
        ten = 1_798_761_600.0 + 10 * 3600          # 2027-01-01 10:00:00 UTC
        self.assertEqual(seconds_to_reset(ten, 0), 14 * 3600)
        self.assertEqual(seconds_to_reset(ten, 10 * 3600), 0)
        self.assertEqual(seconds_to_reset(ten, 10 * 3600 - 60), 86340)
        self.assertEqual(seconds_to_reset(ten + 0.5, 10 * 3600 + 1), 0.5)
        self.assertIsNone(seconds_to_reset(ten, None))

    def test_options_and_overrides(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        options = folder / "options.json"
        options.write_text(json.dumps({"reference_enabled": True, "reference_host": "h",
                                       "reference_mountpoint": "MP", "reference_user": "u",
                                       "reference_password": SENTINEL,
                                       "reference_expected_enu": "0.5 -1.2 1.2",
                                       "measured_snr_mask": 38}))
        loaded = load_settings(["--options", str(options)])
        made = reference_settings(loaded)
        self.assertEqual((made.enabled, made.host, made.port, made.mountpoint, made.user,
                          made.password.reveal(), made.expected(), made.reset_utc,
                          made.binary, made.work_dir, made.elevation_mask, made.snr_mask),
                         (True, "h", 2101, "MP", "u", SENTINEL, (0.5, -1.2, 1.2), "00:00",
                          "rtkrcv-ex", folder / "reference", 15, 38))
        self.assertNotIn("reference_password", loaded, "the password is not kept around")
        self.assertEqual(made.problem(), "")
        loaded = load_settings(["--options", str(options), "--no-reference",
                                "--reference-host", " h2 ", "--reference-port", "2102",
                                "--reference-mountpoint", "M2", "--reference-user", "u2",
                                "--reference-expected-enu", "1 2 3",
                                "--reference-reset-utc", "", "--data-dir", "/x"])
        made = reference_settings(loaded)
        self.assertEqual((made.enabled, made.host, made.port, made.mountpoint, made.user,
                          made.expected(), made.reset_seconds(), made.work_dir),
                         (False, "h2", 2102, "M2", "u2", (1.0, 2.0, 3.0), None,
                          Path("/x/reference")))
        self.assertTrue(reference_settings(load_settings(
            ["--options", str(options), "--reference"])).enabled)
        with self.assertRaises(SystemExit):
            load_settings(["--reference-password", "x"])
        for key in ("reference_enabled", "reference_host", "reference_port",
                    "reference_mountpoint", "reference_user", "reference_password",
                    "reference_expected_enu", "reference_reset_utc"):
            self.assertIn(key, DEFAULTS)
            self.assertEqual(key in OVERRIDES, key != "reference_password", key)
        self.assertEqual((DEFAULTS["reference_enabled"], DEFAULTS["reference_host"],
                          DEFAULTS["reference_port"], DEFAULTS["reference_mountpoint"],
                          DEFAULTS["reference_user"], DEFAULTS["reference_password"],
                          DEFAULTS["reference_expected_enu"], DEFAULTS["reference_reset_utc"]),
                         (False, "", 2101, "", "", "", "0 0 0", "00:00"))
        # Defaults alone: off, and not configured if turned on.
        made = reference_settings(load_settings(["--options", str(folder / "none.json")]))
        self.assertFalse(made.enabled)
        self.assertEqual(made.problem(),
                         "set reference_host, reference_mountpoint, reference_user")
        # A null option (Home Assistant can send one) is an empty string, not "None".
        options.write_text(json.dumps({"reference_host": None, "reference_user": None}))
        made = reference_settings(load_settings(["--options", str(options)]))
        self.assertEqual((made.host, made.user), ("", ""))


class Decode1033(unittest.TestCase):
    def test_descriptors(self):
        decoded = rtcmdec.decode_1033(frame_1033())
        self.assertEqual(decoded, {"station_id": 7, "antenna": "GPPNULLANTENNA",
                                   "antenna_setup": 0, "antenna_serial": "",
                                   "receiver": "LEICA GR30", "firmware": "4.31",
                                   "receiver_serial": "123"})
        self.assertEqual(rtcmdec.decode_1033(frame_1033("TRM57971.00     NONE"))["antenna"],
                         "TRM57971.00     NONE")

    def test_bad_frames(self):
        with self.assertRaises(ValueError):
            rtcmdec.decode_1033(rtcm(1005))
        too_long = frame(bytes([1033 >> 4, (1033 & 15) << 4, 7, 40]) + b"x" * 40)
        with self.assertRaises(ValueError):
            rtcmdec.decode_1033(too_long)
        cut = frame(bytes([1033 >> 4, (1033 & 15) << 4, 7, 5]) + b"ab")
        with self.assertRaises(ValueError):
            rtcmdec.decode_1033(cut)
        odd = frame(bytes([1033 >> 4, (1033 & 15) << 4, 7, 3]) + b"a\x01\xff"
                    + bytes([0, 0, 0, 0, 0]))
        self.assertEqual(rtcmdec.decode_1033(odd)["antenna"], "a??")


class Tap(unittest.IsolatedAsyncioTestCase):
    """The tap server: rtkrcv's copy of the CORS stream, decoded."""

    async def asyncSetUp(self):
        self.now = 1000.0
        self.seen = []
        self.tap = TapServer(on_frame=lambda t, n: self.seen.append((t, n)), clock=lambda: self.now)
        self.port = await self.tap.start()

    async def asyncTearDown(self):
        await self.tap.stop()

    async def send(self, data, close=True):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(data)
        await writer.drain()
        await asyncio.sleep(0.1)
        if close:
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.05)
        return reader, writer

    async def test_decodes_position_descriptors_and_msm(self):
        await self.send(b"junk" + arp_frame(*CORS, antenna_height=0.0) + frame_1033()
                        + rtcm(1074, 40) + rtcm(1084, 40))
        snap = self.tap.snapshot()
        self.assertEqual((snap["frames"], snap["msm_frames"], snap["unknown_bytes"],
                          snap["decode_errors"], snap["connections"]), (4, 2, 4, 0, 1))
        position = snap["position"]
        self.assertEqual((position["message"], position["lat"], position["lon"],
                          position["height_m"], position["antenna_height_m"]),
                         (1006, 0.225, 0.0, 10.0, 0.0))
        self.assertEqual(snap["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(snap["descriptors"]["receiver"], "LEICA GR30")
        self.assertEqual(self.seen, [(1000.0, 1006), (1000.0, 1033), (1000.0, 1074),
                                     (1000.0, 1084)])
        self.assertEqual((snap["age_s"], snap["msm_age_s"]), (0.0, 0.0))
        self.assertAlmostEqual(self.tap.position["ecef"][0], CORS[0], places=3)

    async def test_a_zero_position_is_an_error_not_a_position(self):
        await self.send(arp_frame(0.0, 0.0, 0.0) + rtcm(1033, 8))
        snap = self.tap.snapshot()
        self.assertIsNone(snap["position"])
        self.assertEqual(snap["decode_errors"], 2)
        self.assertEqual(snap["frames"], 2)

    async def test_a_raising_callback_does_not_end_the_tap(self):
        def boom(now, number):
            raise RuntimeError("the check failed")

        self.tap._on_frame = boom
        with self.assertLogs("gnss_base", "ERROR"):
            await self.send(rtcm(1074, 40) + rtcm(1084, 40), close=False)
        self.assertEqual((self.tap.frames, self.tap.msm_frames, self.tap.callback_errors),
                         (2, 2, 2))
        self.assertTrue(self.tap.connected)

    async def test_a_new_connection_starts_a_new_parse(self):
        whole = arp_frame(*CORS)
        await self.send(whole[:10])
        self.assertEqual(self.tap.frames, 0)
        await self.send(whole)
        self.assertEqual((self.tap.frames, self.tap.connections), (1, 2))
        self.assertIsNotNone(self.tap.position)


class Stats(unittest.TestCase):
    def test_session_and_recent(self):
        stats = SessionStats(recent_s=600)
        stats.reset(1_798_761_600.0)
        now = 0.0
        for i in range(100):
            stats.add(now, 2, (0.6 + 0.01 * (i % 2), -1.2, 1.3))   # floats, converging
            now += 1
        for i in range(400):
            stats.add(now, 1, (0.53 + (0.004 if i % 2 else -0.004), -1.15, 1.20 + 0.01 * (i % 3)))
            now += 1
        stats.add(now, 5, (3.0, 3.0, 3.0))   # a single, not in the means
        stats.add(now, 1, None)              # no reference yet: counted only
        self.assertEqual(stats.counts, {"fixed": 401, "float": 100, "single": 1, "other": 0})
        self.assertEqual((stats.epochs, stats.fixed_count), (502, 400))
        mean, std = stats.session_mean()
        self.assertAlmostEqual(mean[0], 0.53, places=6)
        self.assertAlmostEqual(mean[1], -1.15, places=9)
        self.assertAlmostEqual(mean[2], 1.20 + 0.01 * (133 + 2 * 133) / 400, places=6)
        self.assertAlmostEqual(std[0], 0.004, places=6)
        self.assertAlmostEqual(std[1], 0.0, places=9)
        self.assertEqual((stats.first_fix, stats.last_fix), (100.0, 499.0))
        recent = stats.recent(now)
        self.assertEqual((recent["kind"], recent["count"], recent["window_s"]),
                         ("fixed", 400, 600))
        self.assertAlmostEqual(recent["e_mean_m"], 0.53, places=3)
        # Ten minutes later only the single is left in the window: "all".
        recent = stats.recent(now + 600)
        self.assertEqual((recent["kind"], recent["count"]), ("all", 1))
        self.assertEqual(recent["e_mean_m"], 3.0)
        self.assertIsNone(stats.recent(now + 601))
        self.assertIsNotNone(stats.session_mean(), "the session keeps its fixed epochs")
        stats.reset(2.0)
        self.assertEqual((stats.epochs, stats.session_mean(), stats.recent(now),
                          stats.started_wall), (0, None, None, 2.0))

    def test_other_qualities(self):
        stats = SessionStats()
        stats.reset(0.0)
        for quality in (4, 6, 3):
            stats.add(0.0, quality, (0.0, 0.0, 0.0))
        self.assertEqual(stats.counts["other"], 3)
        self.assertIsNone(stats.session_mean())
        self.assertEqual(stats.recent(0.0)["kind"], "all")

    def test_verdict(self):
        self.assertEqual(verdict(None, None), (None, "no fixed solution this session yet"))
        self.assertEqual(verdict([0.03, 0.03, 0.08], 10.0)[0], "good")
        self.assertEqual(verdict([0.05, 0.0, 0.10], 0.0)[0], "good", "the limits count")
        self.assertEqual(verdict([0.04, 0.04, 0.0], 10.0)[0], "warn", "5.7 cm horizontal")
        self.assertEqual(verdict([0.0, 0.0, 0.11], 10.0)[0], "warn")
        self.assertEqual(verdict([0.2, 0.0, 0.2], 10.0)[0], "warn")
        self.assertEqual(verdict([0.15, 0.15, 0.0], 10.0)[0], "bad", "21 cm horizontal")
        self.assertEqual(verdict([0.0, 0.0, -0.21], 10.0)[0], "bad")
        self.assertEqual(verdict([0.0, 0.0, 0.0], NO_FIX_RED_S + 1),
                         ("bad", "no fixed solution for 30 min"))
        self.assertEqual(verdict([0.0, 0.0, 0.0], NO_FIX_RED_S)[0], "good")
        self.assertEqual(verdict(None, None, NO_FIX_RED_S), (None, "no fixed solution this session yet"))
        self.assertEqual(verdict(None, None, NO_FIX_RED_S + 60),
                         ("bad", "no fixed solution for 31 min"))
        self.assertIn("5 cm horizontal and 10 cm up", verdict([0, 0, 0], 0)[1])
        self.assertIn("within 20 cm", verdict([0.1, 0, 0], 0)[1])
        self.assertIn("more than 20 cm", verdict([0.3, 0, 0], 0)[1])


class StubRunner:
    """Stands in for RtkrcvRunner in the state machine tests."""

    def __init__(self):
        self.state_value = "running"
        self.running = 100.0
        self.requests = []
        self.accept = True
        self.started = False
        self.last_exit = None
        self.output = OutputLines()

    def state(self):
        return self.state_value

    def running_s(self):
        return self.running

    def request_restart(self, reason):
        self.requests.append(reason)
        return self.accept

    def start(self):
        self.started = True

    async def stop(self):
        pass

    def snapshot(self):
        return dict.fromkeys(("pid", "running_s", "last_solution_age_s", "retry_in_s",
                              "last_exit", "stop_exit_code", "cpu_percent", "rss_bytes",
                              "rtkrcv_version", "error", "reason"),
                             None) | {"restarts": 0, "restarts_today": 0, "planned_restarts": 0,
                                      "elevation_mask": 15, "snr_mask": 35, "feed": {},
                                      "solutions": {}, "ephemerides": {}}


def solution(lat=0.0, lon=0.0, height=3.0, quality=1, satellites=25, age=1.0, ratio=3.5):
    return {"gpst": "2026-01-01T00:00:00", "lat": lat, "lon": lon, "height_m": height,
            "quality": quality, "satellites": satellites, "sigma_n_m": 0.01,
            "sigma_e_m": 0.01, "sigma_u_m": 0.02, "age_s": age, "ratio": ratio}


class StateMachine(unittest.TestCase):
    """ReferenceCheck's states, offsets and restarts, with a stub runner and clocks."""

    def setUp(self):
        self.now = 1000.0
        self.wall = 1_798_761_600.0 + 3600
        self.base = {"ecef": OURS}
        self.lines = []

    def check(self, **fields):
        made = ReferenceCheck(settings(**fields), RtcmHub(), lambda: self.base,
                              on_solution=lambda s, line: self.lines.append(line),
                              version="t", clock=lambda: self.now, wall=lambda: self.wall)
        self.stub = made.runner = StubRunner()
        return made

    def test_off_and_not_configured(self):
        self.assertEqual(self.check(enabled=False).state(), "off")
        snap = self.check(enabled=False).snapshot()
        self.assertEqual((snap["state"], snap["enabled"], snap["reason"]), ("off", False, None))
        check = self.check(user="")
        self.assertEqual(check.state(), "not configured")
        self.assertEqual(check.snapshot()["reason"], "set reference_user")
        asyncio.run(self._start(check))
        self.assertFalse(self.stub.started, "nothing runs while not configured")
        check = self.check(enabled=False, reset_utc="99:99")
        self.assertEqual(check.state(), "off", "bad options do not matter while off")

    async def _start(self, check):
        with self.assertLogs("gnss_base", "INFO") if check.problem else mock.MagicMock():
            check.start()
        await check.stop()

    def test_states_in_order(self):
        check = self.check()
        for runner_state in ("not available", "idle", "restarting"):
            self.stub.state_value = runner_state
            self.assertEqual(check.state(), runner_state)
        self.stub.state_value = "starting"
        self.stub.running = None
        self.assertEqual(check.state(), "starting", "between two processes")
        self.stub.running = 5.0
        self.assertEqual(check.state(), "starting")
        self.stub.running = STARTING_S
        self.assertEqual(check.state(), "connecting", "no CORS data since the start")
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(check.state(), "starting", "corrections, no solution yet")
        check._solution(solution(quality=2), "line")
        self.assertEqual(check.state(), "float")
        check._solution(solution(quality=1), "line")
        self.assertEqual(check.state(), "fixed")
        check._solution(solution(quality=5), "line")
        self.assertEqual(check.state(), "single")
        self.now += SOLUTION_STALE_S + 1
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(check.state(), "stale")
        self.now += CORRECTIONS_STALE_S + 1
        check._solution(solution(quality=1), "line")
        self.assertEqual(check.state(), "no corrections", "before stale or fixed")
        self.assertEqual(self.lines, ["line"] * 4)

    def test_offsets_session_and_residual(self):
        check = self.check(expected_enu="0 0 3")
        check._session_start()
        check.tap._frame(arp_frame(*CORS, antenna_height=0.0))
        check.tap._frame(frame_1033())
        self.assertEqual(check.state(), "no corrections", "a stream without MSM")
        check.tap._frame(rtcm(1074, 40))
        east = 1e-7 * 111_319.49   # metres per 1e-7 degree of longitude at the equator
        for i in range(10):
            check._solution(solution(lon=1e-7 if i % 2 else 0.0, height=3.0 + 0.02 * (i % 2)),
                            "line")
            self.now += 1
        check._solution(solution(quality=2, height=4.0), "line")
        snap = check.snapshot()
        self.assertEqual(snap["state"], "float")
        self.assertEqual(snap["mountpoint"], "MP_1")
        self.assertEqual(snap["baseline_km"], round(abs(
            __import__("math").dist(OURS, CORS)) / 1000, 3))
        self.assertAlmostEqual(snap["baseline_km"], 24.88, delta=0.05)
        self.assertEqual(snap["solution"]["offset_enu"][2], 4.0)
        self.assertEqual(snap["solution"]["quality"], 2)
        self.assertEqual(snap["solution"]["differential_age_s"], 1.0)
        session = snap["session"]
        self.assertEqual((session["epochs"], session["counts"]["fixed"],
                          session["counts"]["float"], session["fixed_share"]),
                         (11, 10, 1, round(10 / 11, 3)))
        self.assertAlmostEqual(session["mean_enu"][0], east / 2, places=3)
        self.assertAlmostEqual(session["mean_enu"][1], 0.0, places=3)
        self.assertAlmostEqual(session["mean_enu"][2], 3.01, places=4)
        self.assertAlmostEqual(session["std_enu"][0], east / 2, places=3)
        self.assertAlmostEqual(session["std_enu"][2], 0.01, places=4)
        self.assertEqual(session["started_utc"], "2027-01-01T01:00:00+00:00")
        self.assertEqual((session["first_fix_age_s"], session["last_fix_age_s"]), (10.0, 1.0))
        self.assertEqual(snap["recent"]["kind"], "fixed")
        self.assertEqual(snap["recent"]["count"], 10)
        self.assertAlmostEqual(snap["residual_enu"][2], 0.01, places=4)
        self.assertAlmostEqual(snap["residual_enu"][0], east / 2, places=3)
        self.assertEqual(snap["verdict"], "good")
        self.assertEqual(snap["expected_enu"], [0.0, 0.0, 3.0])
        self.assertEqual(snap["corrections"]["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(snap["corrections"]["position"]["antenna_height_m"], 0.0)
        self.assertEqual(snap["base_ecef"], [round(v, 4) for v in OURS])
        self.assertEqual((snap["sessions"], snap["resets"], snap["gap_restarts"]), (1, 0, 0))
        self.assertEqual(snap["next_reset_s"], 23 * 3600)
        header = check.header()
        self.assertIn("RTK static against MP_1", header)
        self.assertIn("expected offset E N U +0.000 +0.000 +3.000 m (solution minus our 1005)", header)
        self.assertIn(f"CORS 1005 ECEF {CORS[0]:.4f} {CORS[1]:.4f} {CORS[2]:.4f} antenna height 0.0000", header)
        self.assertIn(f"our 1005 ECEF {OURS[0]:.4f}", header)
        self.assertIn("time GPST", header)
        json.dumps(snap, allow_nan=False)
        # Red once the residual is large, or there has been no fix for 30 minutes.
        check._session_start()
        for _ in range(3):
            check._solution(solution(height=3.5), "line")
        self.assertEqual(check.snapshot()["verdict"], "bad")
        self.assertIn("more than 20 cm", check.snapshot()["verdict_reason"])
        check._session_start()
        check._solution(solution(height=3.12), "line")
        self.assertEqual(check.snapshot()["verdict"], "warn")
        self.now += NO_FIX_RED_S + 1
        snap = check.snapshot()
        self.assertEqual((snap["verdict"], snap["verdict_reason"]),
                         ("bad", "no fixed solution for 30 min"))
        # A session that never fixed is red after 30 minutes too.
        check._session_start()
        check._solution(solution(quality=2), "line")
        self.assertIsNone(check.snapshot()["verdict"])
        self.now += NO_FIX_RED_S + 1
        self.assertEqual(check.snapshot()["verdict"], "bad")
        # A session kept as "previous" keeps its red: it ended 31 min after its last fix.
        check._session_start()
        check._solution(solution(height=3.0), "line")
        self.now += NO_FIX_RED_S + 60
        self.assertEqual(check.snapshot()["verdict"], "bad")
        check._session_start()
        previous = check.snapshot()["previous_session"]
        self.assertEqual((previous["verdict"], previous["verdict_reason"]),
                         ("bad", "no fixed solution for 31 min"))
        # Our 1005 changing while no solution comes is noticed by the snapshot itself,
        # and the new statistics' age starts over (not red at once).
        check._session_start()
        check._solution(solution(height=3.0), "line")
        self.now += NO_FIX_RED_S - 60
        self.assertEqual(check.snapshot()["verdict"], "good")
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 2.0)}
        snap = check.snapshot()
        self.assertEqual((snap["verdict"], snap["previous_session"]["ended_because"],
                          snap["session"]["epochs"]),
                         (None, "our 1005 position changed", 0))
        self.now += 120
        self.assertIsNone(check.snapshot()["verdict"], "the new statistics are 2 min old")

    def test_without_our_1005_offsets_wait(self):
        self.base = None
        check = self.check()
        check._solution(solution(), "line")
        snap = check.snapshot()
        self.assertIsNone(snap["solution"]["offset_enu"])
        self.assertIsNone(snap["session"]["mean_enu"])
        self.assertIsNone(snap["baseline_km"])
        self.assertEqual(snap["session"]["counts"]["fixed"], 1)
        self.assertIn("our 1005 ECEF unknown", check.header())
        self.base = {"ecef": OURS}
        check._solution(solution(), "line")
        self.assertEqual(check.snapshot()["solution"]["offset_enu"][2], 3.0)

    def test_a_changed_1005_restarts_the_statistics(self):
        check = self.check(expected_enu="0 0 3")
        check._session_start()
        self.assertIsNone(check.snapshot()["previous_session"])
        for _ in range(5):
            check._solution(solution(), "line")
        self.assertEqual(check.stats.fixed_count, 5)
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 1.0)}
        check._solution(solution(), "line")
        snap = check.snapshot()
        self.assertEqual(snap["session"]["counts"]["fixed"], 1)
        previous = snap["previous_session"]
        self.assertEqual((previous["fixed"], previous["epochs"], previous["verdict"],
                          previous["ended_because"], previous["residual_enu"],
                          previous["mean_enu"]),
                         (5, 5, "good", "our 1005 position changed", [0.0, 0.0, 0.0],
                          [0.0, 0.0, 3.0]))
        self.assertEqual(previous["started_utc"], "2027-01-01T01:00:00+00:00")
        self.assertEqual(snap["solution"]["offset_enu"][2], 2.0)
        self.assertEqual(snap["base_changed_age_s"], 0.0)
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 1.0001)}
        check._solution(solution(), "line")
        self.assertEqual(check.stats.fixed_count, 2, "a 0.1 mm rounding is no change")
        self.stub.last_exit = {"code": 0, "reason": "restarted: daily reset", "ran_s": 1.0}
        check._session_start()
        self.assertEqual(check.snapshot()["previous_session"]["ended_because"],
                         "restarted: daily reset")
        self.assertEqual(check.snapshot()["previous_session"]["fixed"], 2)
        check._session_start()
        self.assertEqual(check.snapshot()["previous_session"]["fixed"], 2,
                         "a session without a fix does not replace the kept one")

    def test_a_gap_in_corrections_starts_a_new_session(self):
        check = self.check()
        check._session_start()
        check.tap._frame(rtcm(1074, 40))
        self.now += GAP_RESTART_S
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(self.stub.requests, [], "a gap of exactly the limit is not over it")
        self.now += GAP_RESTART_S + 1
        check.tap._frame(arp_frame(*CORS))
        check.tap._frame(frame_1033())
        self.assertEqual(self.stub.requests, [], "1005 and 1033 are not corrections")
        self.assertEqual(check.state(), "no corrections")
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(self.stub.requests, ["corrections resumed after a 5 min gap"])
        self.assertEqual(check.gap_restarts, 1)
        self.stub.accept = False
        self.now += GAP_RESTART_S + 1
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(check.gap_restarts, 1, "not counted when the runner declined")
        self.stub.accept = True
        # The station's position: the first one of a session is the reference; a
        # change of more than 0.5 mm ends the session; the same one again does not.
        check.tap._frame(arp_frame(*CORS))
        check.tap._frame(arp_frame(*CORS))
        self.assertEqual(check.station_changes, 0)
        moved = rtcmdec.llh_to_ecef(0.225, 0.0, 10.01)
        check.tap._frame(arp_frame(*moved))
        self.assertEqual((check.station_changes, self.stub.requests[-1]),
                         (1, "the station's 1005 position changed"))
        check._session_start()
        self.assertEqual(check.sessions, 2)
        self.now += GAP_RESTART_S + 1
        check.tap._frame(rtcm(1074, 40))
        self.assertEqual(len(self.stub.requests), 3, "the first frame of a session is no gap")

    def test_snapshot_shape(self):
        check = self.check()
        snap = check.snapshot()
        for key in ("enabled", "state", "reason", "host", "port", "mountpoint", "user_set",
                    "station_changes", "previous_session",
                    "password_set", "expected_enu", "reset_utc", "next_reset_s", "base_ecef",
                    "base_changed_age_s", "baseline_km", "corrections", "solution", "session",
                    "recent", "residual_enu", "verdict", "verdict_reason", "sessions", "resets",
                    "gap_restarts", "pid", "running_s", "restarts", "restarts_today",
                    "planned_restarts", "cpu_percent", "rss_bytes", "rtkrcv_version",
                    "feed", "solutions", "ephemerides", "error", "output", "last_exit",
                    "retry_in_s"):
            self.assertIn(key, snap, key)
        self.assertEqual((snap["user_set"], snap["password_set"], snap["host"], snap["port"]),
                         (True, True, "cors.example", 2101))
        self.assertNotIn(SENTINEL, json.dumps(snap))
        self.assertEqual(snap["output"], [])
        self.stub.output.feed(f"path={SENTINEL}\n".encode())
        self.assertEqual(check.snapshot()["output"], [f"path={SENTINEL}"],
                         "the stub has no scrub; the real runner's does (Redaction)")

    def test_console_output_never_shows_a_piece_of_the_password(self):
        secret = Secret(SENTINEL)
        lines = OutputLines(secret=secret)
        # An unfinished line is not shown at all (a read can end inside the password).
        lines.feed(b"rtkrcv> stream path=user:" + SENTINEL.encode()[:9])
        self.assertEqual(lines.snapshot(), [])
        lines.feed(SENTINEL.encode()[9:] + b"@host\r\n")
        self.assertEqual(lines.snapshot(), ["rtkrcv> stream path=user:***@host"])
        # A line cut for length: the cut lands well before the end, and a trailing
        # beginning of the password is removed from the kept part.
        lines = OutputLines(secret=secret)
        filler = b"x" * (OutputLines.LONG_LINE - 5)
        lines.feed(filler + b"user:" + SENTINEL.encode()[:4])
        lines.feed(SENTINEL.encode()[4:] + b"@host\n")
        text = " | ".join(lines.snapshot())
        for piece in (SENTINEL, SENTINEL[:4], SENTINEL[4:], SENTINEL[:2]):
            self.assertNotIn(piece, text, piece)
        self.assertEqual(len(lines.lines), 2, "the cut part, then the rest")
        # An escape sequence cut short just before the password cannot eat its start.
        lines = OutputLines(secret=secret)
        lines.feed(b"path=\x1b[" + SENTINEL.encode() + b"@host\n")
        self.assertEqual(lines.snapshot(), ["path=[***@host"])
        lines.feed(b"a\x1b[0")
        lines.feed(b"m" + SENTINEL.encode() + b"\n")
        self.assertEqual(lines.snapshot()[-1], "a***")
        # Without a secret (the measured instance) the tail still shows, as before.
        plain = OutputLines()
        plain.feed(b"rtkrcv> ")
        self.assertEqual(plain.snapshot(), ["rtkrcv>"])
        self.assertEqual(secret.strip_prefix("abc" + SENTINEL[:5]), "abc")
        self.assertEqual(secret.strip_prefix("abc"), "abc")
        self.assertEqual(secret.strip_prefix(SENTINEL[:3]), "")
        self.assertEqual(Secret("").strip_prefix("abc"), "abc")


class ResetTimer(unittest.IsolatedAsyncioTestCase):
    async def test_restart_at_the_reset_time(self):
        start = time.monotonic()
        day = 1_798_761_600.0
        check = ReferenceCheck(settings(reset_utc="00:00"), RtcmHub(), lambda: None,
                               wall=lambda: day - 0.3 + (time.monotonic() - start))
        stub = check.runner = StubRunner()
        check.start()
        self.assertTrue(stub.started)
        self.assertIsNotNone(check._reset_task)
        try:
            await asyncio.sleep(0.8)
            self.assertEqual(stub.requests, ["daily reset"])
            self.assertEqual(check.resets, 1)
            await asyncio.sleep(2.0)
            self.assertEqual(stub.requests, ["daily reset"], "once a day")
            self.assertAlmostEqual(check.seconds_to_reset(), 86400 - 3, delta=1)
        finally:
            await check.stop()
        self.assertIsNone(check._reset_task)

    async def test_a_clock_set_forward_does_not_fire(self):
        day = 1_798_761_600.0
        clock = {"wall": day - 3000.0}
        check = ReferenceCheck(settings(reset_utc="00:00"), RtcmHub(), lambda: None,
                               wall=lambda: clock["wall"])
        stub = check.runner = StubRunner()
        check.start()
        try:
            await asyncio.sleep(0.1)
            sleeping = check._reset_task
            self.assertFalse(sleeping.done())
            # The clock jumps four hours forward (NTP after a boot) while the loop
            # sleeps: when it wakes it finds the reset time long past, and waits again.
            clock["wall"] = day + 4 * 3600
            with mock.patch("asyncio.sleep", wraps=asyncio.sleep):
                pass
            # Wake the loop by cancelling its sleep and letting it recompute: simplest is
            # to cancel the task and run one iteration by hand.
            sleeping.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sleeping
            remaining = check.seconds_to_reset()
            self.assertEqual(remaining, 20 * 3600)
            check._reset_task = None
            check.start()
            await asyncio.sleep(0.2)
            self.assertEqual(stub.requests, [], "no spurious reset after the jump")
        finally:
            await check.stop()

    async def test_no_timer_without_a_reset_time(self):
        check = ReferenceCheck(settings(reset_utc=""), RtcmHub(), lambda: None)
        check.runner = StubRunner()
        check.start()
        self.assertIsNone(check._reset_task)
        self.assertIsNone(check.snapshot()["next_reset_s"])
        await check.stop()


class PlannedRestart(unittest.IsolatedAsyncioTestCase):
    """RtkrcvRunner.request_restart against the fake rtkrcv: a new process at once,
    the old one ended with SIGTERM (its cache written), no exit backoff."""

    async def test_request_restart(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        record = folder / "record.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_RECORD": str(record),
                                          "FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal"}):
            hub = RtcmHub()
            starts = []
            got = []
            runner = RtkrcvRunner(MeasuredSettings(binary=str(FAKE), work_dir=folder / "w"),
                                  hub, lambda s, line: got.append(s), restart_s=5.0,
                                  stop_timeout_s=1.0, poll_s=0.05, on_start=lambda: starts.append(1))
            delays = []
            runner._sleep = lambda seconds: delays.append(seconds) or asyncio.sleep(seconds)
            self.addAsyncCleanup(runner.stop)
            self.assertFalse(runner.request_restart("nothing runs"))
            runner.start()
            hub.publish(rtcm(1074, 40))
            await self._until(lambda: len(got) >= 2)
            first = runner.snapshot()["pid"]
            self.assertTrue(runner.request_restart("daily reset"))
            self.assertFalse(runner.request_restart("again"), "one at a time")
            await self._until(lambda: runner.runs == 2 and runner.snapshot()["pid"] not in (None, first))
            await self._until(lambda: len(got) >= 4)
            self.assertEqual((runner.planned_restarts, runner.restarts, starts, delays),
                             (1, 0, [1, 1], []))
            self.assertEqual(runner.last_exit["reason"], "restarted: daily reset")
            self.assertEqual(runner.last_exit["code"], 0, "SIGTERM, not killed")
            self.assertEqual(runner.state(), "running")
            events = [json.loads(line)["event"] for line in record.read_text().splitlines()]
            self.assertEqual(events, ["start", "exit", "start"])
            self.assertTrue((folder / "w" / "rtkrcv.nav").exists())
            self.assertEqual(runner.snapshot()["planned_restarts"], 1)

    async def _until(self, check, timeout=10.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            self.assertLess(asyncio.get_running_loop().time(), deadline, "timed out")
            await asyncio.sleep(0.02)


class NoConsole(unittest.IsolatedAsyncioTestCase):
    """A build that answers --version (RTKLIB-EX) runs with -nc and no pseudo-terminal;
    one that does not (2.4.3 b34) gets the pseudo-terminal console."""

    async def test_rtklib_ex_runs_without_a_console(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        record = folder / "record.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_RECORD": str(record),
                                          "FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal",
                                          "FAKE_RTKRCV_VERSION": "EX 2.5.1"}):
            hub = RtcmHub()
            got = []
            runner = RtkrcvRunner(MeasuredSettings(binary=str(FAKE), work_dir=folder / "w"),
                                  hub, lambda s, line: got.append(s), poll_s=0.05,
                                  stop_timeout_s=1.0)
            self.addAsyncCleanup(runner.stop)
            runner.start()
            hub.publish(rtcm(1074, 40))
            deadline = asyncio.get_running_loop().time() + 10
            while len(got) < 2:
                self.assertLess(asyncio.get_running_loop().time(), deadline, runner.snapshot())
                await asyncio.sleep(0.02)
            snap = runner.snapshot()
            self.assertEqual((snap["console"], snap["rtkrcv_version"]), ("none", "EX 2.5.1"))
            start = json.loads(record.read_text().splitlines()[0])
            self.assertEqual(start["argv"][:1], ["-nc"])
            self.assertNotIn("-d", start["argv"])
            self.assertEqual((start["console"], start["tty"]), (False, None))
            await runner.stop()
            self.assertEqual(runner.stop_exit_code, 0)
            self.assertTrue((folder / "w" / "rtkrcv.nav").exists())

    async def test_b34_keeps_the_console(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        record = folder / "record.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_RECORD": str(record),
                                          "FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal"}):
            os.environ.pop("FAKE_RTKRCV_VERSION", None)
            hub = RtcmHub()
            got = []
            runner = RtkrcvRunner(MeasuredSettings(binary=str(FAKE), work_dir=folder / "w"),
                                  hub, lambda s, line: got.append(s), poll_s=0.05,
                                  stop_timeout_s=1.0)
            self.addAsyncCleanup(runner.stop)
            runner.start()
            hub.publish(rtcm(1074, 40))
            deadline = asyncio.get_running_loop().time() + 10
            while len(got) < 2:
                self.assertLess(asyncio.get_running_loop().time(), deadline, runner.snapshot())
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            snap = runner.snapshot()
            self.assertEqual((snap["console"], snap["rtkrcv_version"]), ("pty", "2.4.3 b34"))
            start = json.loads(record.read_text().splitlines()[0])
            self.assertEqual(start["argv"][:3], ["-s", "-o", str(folder / "w" / CONFIG_NAME)])
            self.assertEqual((start["console"], start["tty"]), (True, True))

    async def test_a_missing_binary_is_not_available(self):
        from gnss_base.measured import probe_version
        self.assertIsNone(await probe_version(str(Path(tempfile.gettempdir()) / "no-such-rtkrcv")))
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_VERSION": "EX 2.5.1"}):
            self.assertEqual(await probe_version(str(FAKE)), "EX 2.5.1")
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_VERSION": ""}):
            self.assertIsNone(await probe_version(str(FAKE)))


class NoCorrections(unittest.IsolatedAsyncioTestCase):
    """rtkrcv keeps sending single solutions while the CORS stream is silent (it is
    configured with out-outsingle=on): the state is "no corrections", the singles are
    counted, and the runner does not take the quiet stream for a hang."""

    async def test_silent_tap_is_no_corrections_not_a_hang(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        sample = folder / "cors.rtcm3"
        sample.write_bytes(arp_frame(*CORS) + frame_1033())
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal",
                                          "FAKE_RTKRCV_TAP_FILE": str(sample),
                                          "FAKE_RTKRCV_TAP_SILENT": "1",
                                          "FAKE_RTKRCV_SOLUTION": "0 0 3.0 5 20"}):
            hub = RtcmHub()
            check = ReferenceCheck(settings(binary=str(FAKE), work_dir=folder / "w"),
                                   hub, lambda: {"ecef": OURS}, poll_s=0.05, hang_s=0.5)
            self.addAsyncCleanup(check.stop)
            check.start()
            for prn in range(1, 6):
                hub.publish(eph(prn))
            deadline = asyncio.get_running_loop().time() + 10
            while check.stats.counts["single"] < 5:
                self.assertLess(asyncio.get_running_loop().time(), deadline, check.snapshot())
                hub.publish(rtcm(1074, 40))
                hub.publish(eph(1))
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.8)   # longer than hang_s, with MSM still arriving
            hub.publish(rtcm(1074, 40))
            snap = check.snapshot()
            self.assertEqual(snap["state"], "no corrections", snap)
            self.assertEqual((snap["restarts"], snap["planned_restarts"]), (0, 0))
            self.assertIsNotNone(snap["corrections"]["position"], "1005 came, MSM did not")
            self.assertIsNone(snap["corrections"]["msm_age_s"])
            self.assertEqual(snap["session"]["counts"]["fixed"], 0)
            self.assertEqual(snap["recent"]["kind"], "all")
            self.assertIsNone(snap["verdict"])


class Redaction(unittest.IsolatedAsyncioTestCase):
    """The whole check against the fake rtkrcv, which echoes its NTRIP path (login
    included) to its console and stdout: the password reaches the config file (mode
    0600) and nowhere else."""

    async def test_password_only_in_the_config_file(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        sample = folder / "cors.rtcm3"
        sample.write_bytes(arp_frame(*CORS, antenna_height=0.0) + frame_1033())
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal",
                                          "FAKE_RTKRCV_ECHO_PATH": "1",
                                          "FAKE_RTKRCV_TAP_FILE": str(sample),
                                          "FAKE_RTKRCV_SOLUTION": "0 0 3.0 1 25"}):
            hub = RtcmHub()
            lines = []
            log = SolutionLog(folder / "reference", lambda: check.header(), suffix=".llh")
            check = ReferenceCheck(
                settings(binary=str(FAKE), work_dir=folder / "reference-work",
                         expected_enu="0 0 3"),
                hub, lambda: {"ecef": OURS},
                on_solution=lambda s, line: (lines.append(line), log.write(line)),
                version="t", poll_s=0.05, stats_s=0.2)
            self.addAsyncCleanup(check.stop)
            with self.assertLogs("gnss_base", "INFO"):
                check.start()
                hub.publish(rtcm(1074, 40))
                deadline = asyncio.get_running_loop().time() + 15
                while check.state() != "fixed" or len(lines) < 3:
                    self.assertLess(asyncio.get_running_loop().time(), deadline,
                                    check.snapshot())
                    hub.publish(rtcm(1074, 40))
                    await asyncio.sleep(0.1)
            snap = check.snapshot()
            texts = [json.dumps(snap), json.dumps(check.runner.snapshot()),
                     "\n".join(check.runner.output.lines), check.header(),
                     repr(vars(check)), repr(vars(check.runner)), output.getvalue()]
            for text in texts:
                self.assertNotIn(SENTINEL, text)
            self.assertTrue(any("path=user:***@cors.example:2101/MP_1" in line
                                for line in check.runner.output.lines),
                            check.runner.output.lines)
            config = folder / "reference-work" / CONFIG_NAME
            self.assertIn(SENTINEL, config.read_text(), "rtkrcv reads it from the file")
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(snap["state"], "fixed")
            self.assertEqual(snap["corrections"]["descriptors"]["receiver"], "LEICA GR30")
            self.assertAlmostEqual(snap["baseline_km"], 24.88, delta=0.05)
            self.assertEqual(snap["solution"]["offset_enu"], [0.0, 0.0, 3.0])
            self.assertEqual(snap["verdict"], "good")
            self.assertEqual(snap["rtkrcv_version"], "2.4.3 b34")
            self.assertGreaterEqual(snap["corrections"]["msm_frames"], 1)
            self.assertNotIn("*", snap["corrections"]["position"].__repr__())
            await check.stop()
            await log.flush()
            (llh,) = (folder / "reference").glob("*.llh")
            text = llh.read_text()
            self.assertNotIn(SENTINEL, text)
            self.assertTrue(text.startswith("% gnss_base t, rtkrcv 2.4.3 b34, RTK static against MP_1, our 1005 ECEF"), text[:120])
            self.assertIn("CORS 1005 ECEF", text.splitlines()[0])
            self.assertEqual(parse_solution(text.splitlines()[-1])[1], None)
            self.assertEqual(check.runner.stop_exit_code, 0)

    async def test_a_config_write_error_names_the_file_not_the_login(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "work").write_text("a file where the folder should be")
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_MODE": "normal"}):
            check = ReferenceCheck(settings(binary=str(FAKE), work_dir=folder / "work"),
                                   RtcmHub(), lambda: None, restart_s=0.1, restart_max_s=0.1,
                                   poll_s=0.05)
            self.addAsyncCleanup(check.stop)
            with self.assertLogs("gnss_base", "WARNING") as logs:
                check.start()
                check.runner.hub.publish(rtcm(1074, 40))
                deadline = asyncio.get_running_loop().time() + 5
                while check.runner.last_exit is None:
                    self.assertLess(asyncio.get_running_loop().time(), deadline)
                    await asyncio.sleep(0.05)
            reason = check.runner.last_exit["reason"]
            self.assertIn("could not write its config", reason)
            self.assertIn("FileExistsError", reason)
            self.assertNotIn(SENTINEL, reason + "\n".join(logs.output) + json.dumps(check.snapshot()))


def find_rtkrcv():
    return (os.environ.get("RTKRCV") or shutil.which("rtkrcv-ex") or shutil.which("rtkrcv"))


@unittest.skipUnless(find_rtkrcv(), "no rtkrcv binary (set RTKRCV or put rtkrcv-ex on PATH)")
class RealRtkrcv(unittest.IsolatedAsyncioTestCase):
    """Smoke test with the real rtkrcv: it takes our reference config (every option
    name and value), connects to the feed, solution and tap servers (the NTRIP client
    points at a closed local port, so it never reaches a real caster), and exits with
    code 0 on SIGTERM."""

    async def test_smoke(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        hub = RtcmHub()
        check = ReferenceCheck(settings(binary=find_rtkrcv(), work_dir=folder,
                                        host="127.0.0.1", port=closed),
                               hub, lambda: None, stats_s=0.5)
        self.addAsyncCleanup(check.stop)
        check.start()
        hub.publish(rtcm(1074, 40))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15
        runner = check.runner
        # The tap connects only once CORS bytes arrive (rtkrcv opens a log stream's
        # TCP client at its first write), so with no CORS it stays unconnected.
        while not (runner.feed.connections and runner.solutions.connections
                   and runner.rss_bytes):
            self.assertLess(loop.time(), deadline, check.snapshot())
            await asyncio.sleep(0.1)
        self.assertIn(check.state(), ("starting", "connecting"))
        self.assertEqual(check.tap.connections, 0)
        version = runner.output.version
        self.assertTrue(version and (version.startswith("2.4.3") or version.startswith("EX")),
                        version)
        await asyncio.sleep(1)
        self.assertNotIn(SENTINEL, "\n".join(runner.output.lines))
        for line in runner.output.lines:
            self.assertNotIn("invalid option", line, runner.output.lines)
        await check.stop()
        self.assertEqual(runner.last_exit, None, runner.last_exit)
        self.assertEqual(runner.stop_exit_code, 0, "exited on SIGTERM, not killed")
        self.assertTrue((folder / "rtkrcv.nav").exists())


class EndToEnd(unittest.TestCase):
    """The whole app with a simulated receiver and the fake rtkrcv standing in for both
    instances: the reference card reaches `api/status` without the password, its log
    is written, and the app still stops within Home Assistant's 10 s."""

    def test_app_runs_the_reference_check(self):
        from tests.test_app import SimulatedReceiver, free_port
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        sample = directory / "cors.rtcm3"
        sample.write_bytes(arp_frame(*CORS, antenna_height=0.0) + frame_1033())
        master, slave = pty.openpty()
        tty.setraw(master)
        tty.setraw(slave)
        by_id = directory / "by-id"
        by_id.mkdir()
        (by_id / "usb-test_USB_Dual_Serial_0-if00").symlink_to(os.ttyname(slave))
        port = free_port()
        options = directory / "options.json"
        options.write_text(json.dumps({
            "device": os.ttyname(slave), "baud": 460800, "log_dir": str(directory / "logs"),
            "min_free_gb": 0, "caster_enabled": False, "reference_enabled": True,
            "reference_host": "cors.example", "reference_mountpoint": "MP_1",
            "reference_user": "user", "reference_password": SENTINEL,
            "reference_expected_enu": "0 0 3"}))
        env = dict(os.environ, FAKE_RTKRCV_PERIOD="0.2", FAKE_RTKRCV_MODE="normal",
                   FAKE_RTKRCV_ECHO_PATH="1", FAKE_RTKRCV_TAP_FILE=str(sample),
                   FAKE_RTKRCV_SOLUTION="0 0 3.0 1 25")
        process = subprocess.Popen(
            [sys.executable, "-m", "gnss_base", "--options", str(options), "--host",
             "127.0.0.1", "--port", str(port), "--allow-any-client", "--by-id-dir",
             str(by_id), "--rtkrcv", str(FAKE)],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        receiver = SimulatedReceiver(master)
        stream = arp_frame(*OURS) + rtcm(1074, 40)
        status = text = None
        try:
            receiver.start()
            deadline = time.time() + 30
            while time.time() < deadline:
                receiver.send(stream)
                time.sleep(0.2)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                                timeout=5) as reply:
                        text = reply.read().decode()
                except OSError:
                    continue
                status = json.loads(text)
                reference = status["reference"]
                if reference["state"] == "fixed" and reference["session"]["epochs"] >= 3:
                    break
        finally:
            stopping = time.time()
            process.terminate()
            app_output = process.communicate(timeout=15)[0].decode(errors="replace")
            took = time.time() - stopping
            receiver.stop()
        self.assertLess(took, 10)
        self.assertEqual(process.returncode, 0, app_output)
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn(SENTINEL, app_output)
        reference = status["reference"]
        self.assertEqual(reference["state"], "fixed", reference)
        self.assertEqual((reference["mountpoint"], reference["password_set"],
                          reference["verdict"]), ("MP_1", True, "good"))
        self.assertEqual(reference["solution"]["offset_enu"], [0.0, 0.0, 3.0])
        self.assertEqual(reference["corrections"]["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(reference["log"]["folder"], "reference")
        self.assertEqual(status["measured"]["state"], "running", "the other instance too")
        config = directory / "reference" / CONFIG_NAME
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertIn(SENTINEL, config.read_text())
        self.assertTrue((directory / "reference" / "rtkrcv.nav").exists(), "SIGTERM, cache saved")
        (llh,) = (directory / "logs" / "reference").glob("*.llh")
        lines = llh.read_text().splitlines()
        self.assertTrue(lines[0].startswith(f"% gnss_base {__version__}, rtkrcv 2.4.3 b34, "
                                            "RTK static against MP_1"), lines[0])
        self.assertNotIn(SENTINEL, llh.read_text())
        self.assertGreaterEqual(len(lines), 4)
        self.assertEqual(parse_solution(lines[-1])[1], None)
        (pos,) = (directory / "logs" / "measured").glob("*.pos")
        self.assertTrue(pos.read_text().startswith("% gnss_base"))


class Page(unittest.TestCase):
    def test_reference_card(self):
        html = (ROOT / "gnss_base" / "static" / "index.html").read_text()
        for text in ('id="ref"', 'id="refd"', 'id="refmp"', 'id="refnote"', 'id="refout"',
                     "renderReference(s.reference)", "Reference check",
                     "A fixed solution can still be a false fix",
                     "Static mode smears an antenna move into a slow drift",
                     "the Measured position card catches a move within seconds",
                     "solution minus our RTCM 1005 position", "within 5 cm horizontal",
                     "Residual (mean − expected)", "Expected offset", "Baseline",
                     "Session mean ± std (fixed)", "Mean ± std, 10 min", "Fixed share",
                     "rtkrcv CPU", "rtkrcv memory", "not applied; see the README",
                     "Previous session", "previous session's residual",
                     "password ${r.password_set?\"set\":\"not set\"}"):
            self.assertIn(text, html, text)
        card = html[html.index("function renderReference"):html.index("function render(s)")]
        self.assertNotIn("innerHTML", card, "values reach the card through dl, which escapes")
        self.assertIn("$(\"refout\").textContent=out", card)
        states = html[html.index("const REF_STATES"):html.index("function renderReference")]
        for state in ("not configured", "connecting", "no corrections", "stale", "fixed",
                      "float", "single", "starting", "idle", "restarting", "not available"):
            self.assertIn(f"{state}:" if " " not in state else f'"{state}":', states, state)
        self.assertEqual(html.count("<section>"), html.count("</section>") - html.count('<section class="wide">'))

    def test_within_5_cm_rule_is_the_apps(self):
        html = (ROOT / "gnss_base" / "static" / "index.html").read_text()
        self.assertIn("r.verdict", html, "the page shows the app's verdict, it does not judge")
        card = html[html.index("function renderReference"):html.index("function render(s)")]
        self.assertNotIn("0.05", card)
        self.assertNotIn("0.2", card)


class Packaging(unittest.TestCase):
    def test_options_schema_and_image(self):
        text = (ROOT / "config.yaml").read_text()
        for line in ("  reference_enabled: false\n", '  reference_host: ""\n',
                     "  reference_port: 2101\n", '  reference_mountpoint: ""\n',
                     '  reference_user: ""\n', '  reference_password: ""\n',
                     '  reference_expected_enu: "0 0 0"\n', '  reference_reset_utc: "00:00"\n',
                     "  reference_enabled: bool\n", "  reference_host: str\n",
                     "  reference_port: port\n", "  reference_mountpoint: str\n",
                     "  reference_user: str\n", "  reference_password: password\n",
                     "  reference_expected_enu: str\n", "  reference_reset_utc: match("):
            self.assertIn(line, text, line)
        self.assertIn('version: "0.6.0"', text)
        import re
        pattern = re.search(r"reference_reset_utc: match\((.*)\)\n", text).group(1)
        for good in ("", "7:05", "00:00", "23:59", "09:30"):
            self.assertTrue(re.fullmatch(pattern, good), good)
        for bad in ("24:00", "7:5", "12:60", "noon", "1:00:00"):
            self.assertFalse(re.fullmatch(pattern, bad), bad)
        self.assertEqual(__version__, "0.6.0")
        dockerfile = (ROOT / "Dockerfile").read_text()
        for text in ("ARG BUILD_FROM", "FROM ${BUILD_FROM} AS rtklib", "FROM ${BUILD_FROM}\n",
                     "RTKLIB_TAG=v2.5.1",
                     "RTKLIB_COMMIT=62d4677ed8425a4e2748c6d390b500d1afb493fc",
                     "rev-parse HEAD", "app/consapp/rtkrcv/gcc", "/usr/local/bin/rtkrcv-ex",
                     "rtklibexplorer/RTKLIB.git"):
            self.assertIn(text, dockerfile, text)
        self.assertNotIn(" rtklib ", dockerfile, "Ubuntu's rtklib package is not installed")
        self.assertNotIn("gfortran", dockerfile)
        build = (ROOT / "build.yaml").read_text()
        self.assertIn("aarch64: ubuntu:24.04", build)
        self.assertIn("amd64: ubuntu:24.04", build)
        readme = (ROOT / "README.md").read_text()
        for text in ("reference_expected_enu", "reference_reset_utc", "GPPNULLANTENNA",
                     "solution minus our", "rtkrcv-ex", "v2.5.1", "false fix",
                     "## Reference check"):
            self.assertIn(text, readme, text)
        self.assertNotIn(SENTINEL, readme)

    def test_shutdown_stops_both_instances_then_the_logs(self):
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        self.assertIn("x.stop() for x in (rtkrcv, reference)", source)
        self.assertIn("reference_log", source)
        self.assertIn("housekeeping(log, errors, solutions, reference_log)", source)


class NoPathToTheReceiver(unittest.TestCase):
    def test_reference_imports_nothing_that_reaches_the_receiver(self):
        tree = ast.parse((ROOT / "gnss_base" / "reference.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "") + ":" + ",".join(a.name for a in node.names))
        text = " ".join(imported)
        for forbidden in ("commands", "control", "receiver", "serial", "web", "status"):
            self.assertNotRegex(text, rf"\b{forbidden}\b", f"reference imports {forbidden}")


if __name__ == "__main__":
    unittest.main()
