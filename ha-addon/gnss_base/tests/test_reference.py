"""Stage 6: the reference check (RTK static against a CORS station with a second rtkrcv).

Run from ha-addon/gnss_base with: python -m unittest tests.test_reference

Localhost only, against the fake rtkrcv (tests/fake_rtkrcv.py) and the fake NTRIP
caster (tests/fake_caster.py); the real rtkrcv only in the smoke test, skipped unless a
binary is found (`RTKRCV` environment variable, or rtkrcv-ex / rtkrcv on PATH). No real
CORS is contacted, no real position is used: the positions are synthetic, on the
equator, and the login is a made-up sentinel that must appear nowhere but in the
request's Authorization header.
"""
import ast
import asyncio
import json
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
from gnss_base.__main__ import (DEFAULTS, OVERRIDES, all_reference_settings, load_settings,
                                reference_settings)
from gnss_base.datums import decimal_year, expected_offset_enu
from gnss_base.frames import crc24q
from gnss_base.hub import RtcmHub, Secret
from gnss_base.measured import (CONFIG_NAME, MeasuredSettings, OutputLines, RtkrcvRunner,
                                parse_solution)
from gnss_base.ntripclient import build_gga
from gnss_base.reference import (COMPARE_FRESH_S, CORRECTIONS_STALE_S, GAP_RESTART_S, NO_FIX_RED_S,
                                 OUR_FRAME_UNKNOWN, SOLUTION_STALE_S, STATION_FRAME_UNKNOWN,
                                 ReferenceCheck, ReferenceSettings, SessionStats,
                                 StationDecoder, build_config, compare, compare_verdict,
                                 login_clash, parse_gga_position, parse_reset_utc,
                                 seconds_to_reset, verdict)
from tests.fake_caster import FakeCaster
from tests.test_app import BitWriter
from tests.test_measured import FAKE, ROOT, eph, rtcm

SENTINEL = "Ref-S3ntinel-pw-5b2c"   # a made-up password: nowhere but in rtkrcv's config
GOLDEN = ROOT / "tests" / "reference_golden.conf"
# Made-up positions on the equator: ours at 0/0, the CORS 0.225 deg north (about 25 km).
OURS = rtcmdec.llh_to_ecef(0.0, 0.0, 0.0)
CORS = rtcmdec.llh_to_ecef(0.225, 0.0, 10.0)


def settings(**fields):
    # A closed local port: a started client gets "connection refused", never a lookup.
    base = dict(enabled=True, host="127.0.0.1", port=1, mountpoint="MP_1", user="user",
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


def arp_frame_df021(x, y, z, itrf):
    """A 1005 frame with a non-zero ITRF realization year field (DF021)."""
    bits = BitWriter().u(12, 1005).u(12, 7).u(6, itrf).u(1, 1).u(1, 1).u(1, 1).u(1, 0)
    bits.s(38, round(x * 10000)).u(1, 0).u(1, 0)
    bits.s(38, round(y * 10000)).u(2, 0)
    bits.s(38, round(z * 10000))
    return bits.frame()


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
                     "inpstr1-format =rtcm3", "inpstr2-type   =tcpcli",
                     "inpstr2-path   =127.0.0.1:40003",
                     "inpstr2-format =rtcm3",
                     "outstr1-type   =tcpcli", "outstr1-path   =127.0.0.1:40002",
                     "outstr1-format =llh", "logstr2-type   =off", "misc-reconnect =5000",
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
                       "ant2-anttype   =*", "est-stec", "kinematic", "rcvantfile",
                       "ntripcli", "nmeareq", SENTINEL, "user", "MP_1", "0600"):
            self.assertNotIn(absent, text, "no credential or caster in rtkrcv's config")
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

    def test_fields_that_would_break_the_request(self):
        cases = ((dict(user="a:b"), "reference_user has a ':'"),
                 (dict(host="h/x"), "reference_host has a '/'"),
                 (dict(host="h:2101"), "reference_host has a ':'"),
                 (dict(mountpoint="M P"), "reference_mountpoint has a space"),
                 (dict(mountpoint="M/P"), "reference_mountpoint has a '/'"),
                 (dict(mountpoint="M?x"), "reference_mountpoint has a '?'"),
                 (dict(password=Secret("p w")), "reference_password has a space"),
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
        # A password with ':', '@', '/' or '#' is fine: it only goes through base64.
        self.assertEqual(settings(password=Secret("a:b@c/d#e")).problem(), "")
        self.assertEqual(settings(user="a@b").problem(), "", "an e-mail address as user")
        self.assertEqual(settings(elevation_mask=None).problem(),
                         "measured_elevation_mask must be a whole number from 0 to 90")
        self.assertEqual(settings(snr_mask=61).problem(),
                         "measured_snr_mask must be a whole number from 0 to 60")

    def test_problems_never_quote_the_password(self):
        for password in (SENTINEL + " x", "é" + SENTINEL, "x" * 256 + SENTINEL):
            problem = settings(password=Secret(password)).problem()
            self.assertTrue(problem)
            self.assertNotIn(SENTINEL, problem)
        self.assertNotIn(SENTINEL, repr(settings()))
        self.assertNotIn(SENTINEL, str(settings()))

    def test_frames(self):
        """0.6.2: the frames are declared by name; nothing else is accepted, and an
        empty reference_frame means unknown (no expected offset)."""
        self.assertEqual(settings().station_frame(), "")
        self.assertEqual(settings().our_frame(), "ITRF2020")
        self.assertEqual(settings(frame="nad83 (2011)").station_frame(), "NAD83(2011)")
        self.assertEqual(settings(frame="itrf2014").station_frame(), "ITRF2014")
        self.assertEqual(settings(base_frame="").our_frame(), "")
        names = "ITRF2020, ITRF2014, ITRF2008, NAD83(2011)"
        self.assertEqual(settings(frame="WGS84").problem(),
                         f"reference_frame must be one of {names}, or empty")
        self.assertEqual(settings(frame="0.56 -1.20 1.18").problem(),
                         f"reference_frame must be one of {names}, or empty")
        self.assertEqual(settings(base_frame="NAD83").problem(),
                         f"base_frame must be one of {names}, or empty")
        self.assertEqual(settings(frame="NAD83(2011)", base_frame="ITRF2020").problem(), "")
        self.assertIsNone(settings().our_epoch())
        self.assertAlmostEqual(settings(base_epoch="2026-11-06").our_epoch(), 2026.85, places=2)
        self.assertEqual(settings(base_epoch=" 2026.85 ").our_epoch(), 2026.85)
        self.assertEqual(settings(base_epoch="yesterday").problem(),
                         "base_epoch must be a date YYYY-MM-DD or a decimal year, or empty")
        self.assertEqual(settings(base_epoch="1950").problem(),
                         "base_epoch must be a year between 1990 and 2100")

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
                                       "reference_frame": "NAD83(2011)",
                                       "measured_snr_mask": 38}))
        loaded = load_settings(["--options", str(options)])
        made = reference_settings(loaded)
        self.assertEqual((made.enabled, made.host, made.port, made.mountpoint, made.user,
                          made.password.reveal(), made.frame, made.base_frame, made.base_epoch,
                          made.reset_utc, made.binary, made.work_dir, made.elevation_mask,
                          made.snr_mask),
                         (True, "h", 2101, "MP", "u", SENTINEL, "NAD83(2011)", "ITRF2020", "",
                          "00:00", "rtkrcv-ex", folder / "reference", 15, 38))
        self.assertNotIn("reference_password", loaded, "the password is not kept around")
        self.assertEqual(made.problem(), "")
        loaded = load_settings(["--options", str(options), "--no-reference",
                                "--reference-host", " h2 ", "--reference-port", "2102",
                                "--reference-mountpoint", "M2", "--reference-user", "u2",
                                "--reference-frame", " itrf2014 ", "--base-frame", "",
                                "--base-epoch", "2026-11-06",
                                "--reference-reset-utc", "", "--data-dir", "/x"])
        made = reference_settings(loaded)
        self.assertEqual((made.enabled, made.host, made.port, made.mountpoint, made.user,
                          made.station_frame(), made.our_frame(), round(made.our_epoch(), 2),
                          made.reset_seconds(), made.work_dir),
                         (False, "h2", 2102, "M2", "u2", "ITRF2014", "", 2026.85, None,
                          Path("/x/reference")))
        self.assertEqual(reference_settings(load_settings(
            ["--options", str(options), "--reference-frame", "ETRS89"])).problem(),
            "reference_frame must be one of ITRF2020, ITRF2014, ITRF2008, NAD83(2011), or empty")
        self.assertTrue(reference_settings(load_settings(
            ["--options", str(options), "--reference"])).enabled)
        with self.assertRaises(SystemExit):
            load_settings(["--reference-password", "x"])
        for key in ("reference_enabled", "reference_host", "reference_port",
                    "reference_mountpoint", "reference_user", "reference_password",
                    "reference_frame", "reference_reset_utc", "base_frame", "base_epoch"):
            self.assertIn(key, DEFAULTS)
            self.assertEqual(key in OVERRIDES, key != "reference_password", key)
        self.assertNotIn("reference_expected_enu", DEFAULTS, "removed in 0.6.2")
        self.assertEqual((DEFAULTS["reference_enabled"], DEFAULTS["reference_host"],
                          DEFAULTS["reference_port"], DEFAULTS["reference_mountpoint"],
                          DEFAULTS["reference_user"], DEFAULTS["reference_password"],
                          DEFAULTS["reference_frame"], DEFAULTS["reference_reset_utc"],
                          DEFAULTS["base_frame"], DEFAULTS["base_epoch"]),
                         (False, "", 2101, "", "", "", "", "00:00", "ITRF2020", ""))
        # Defaults alone: off, and not configured if turned on.
        made = reference_settings(load_settings(["--options", str(folder / "none.json")]))
        self.assertFalse(made.enabled)
        self.assertEqual(made.problem(),
                         "set reference_host, reference_mountpoint, reference_user")
        # A null option (Home Assistant can send one) is an empty string, not "None".
        options.write_text(json.dumps({"reference_host": None, "reference_user": None,
                                       "reference_frame": None, "base_frame": None}))
        made = reference_settings(load_settings(["--options", str(options)]))
        self.assertEqual((made.host, made.user, made.frame, made.base_frame), ("", "", "", ""))


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


class Decoder(unittest.TestCase):
    """The station decoder: the stream as the NTRIP client receives it, decoded."""

    def setUp(self):
        self.now = 1000.0
        self.seen = []
        self.decoder = StationDecoder(on_frame=lambda t, n: self.seen.append((t, n)),
                                      clock=lambda: self.now)

    def test_decodes_position_descriptors_and_msm(self):
        self.decoder.feed(b"junk" + arp_frame(*CORS, antenna_height=0.0) + frame_1033()
                          + rtcm(1074, 40) + rtcm(1084, 40))
        snap = self.decoder.snapshot()
        self.assertEqual((snap["frames"], snap["msm_frames"], snap["unknown_bytes"],
                          snap["decode_errors"]), (4, 2, 4, 0))
        position = snap["position"]
        self.assertEqual((position["message"], position["lat"], position["lon"],
                          position["height_m"], position["antenna_height_m"],
                          position["itrf_year"]),
                         (1006, 0.225, 0.0, 10.0, 0.0, 0))
        self.assertEqual(snap["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(snap["descriptors"]["receiver"], "LEICA GR30")
        self.assertEqual(self.seen, [(1000.0, 1006), (1000.0, 1033), (1000.0, 1074),
                                     (1000.0, 1084)])
        self.assertEqual((snap["age_s"], snap["msm_age_s"]), (0.0, 0.0))
        self.assertAlmostEqual(self.decoder.position["ecef"][0], CORS[0], places=3)
        self.assertGreater(snap["bytes"], 100)

    def test_a_zero_position_is_an_error_not_a_position(self):
        self.decoder.feed(arp_frame(0.0, 0.0, 0.0) + rtcm(1033, 8))
        snap = self.decoder.snapshot()
        self.assertIsNone(snap["position"])
        self.assertEqual(snap["decode_errors"], 2)
        self.assertEqual(snap["frames"], 2)

    def test_a_raising_callback_does_not_stop_the_decoder(self):
        def boom(now, number):
            raise RuntimeError("the check failed")

        self.decoder._on_frame = boom
        with self.assertLogs("gnss_base", "ERROR"):
            self.decoder.feed(rtcm(1074, 40) + rtcm(1084, 40))
        self.assertEqual((self.decoder.frames, self.decoder.msm_frames,
                          self.decoder.callback_errors), (2, 2, 2))

    def test_a_new_connection_starts_a_new_parse(self):
        whole = arp_frame(*CORS)
        self.decoder.feed(whole[:10])
        self.assertEqual(self.decoder.frames, 0)
        self.decoder.reset()
        self.decoder.feed(whole)
        self.assertEqual(self.decoder.frames, 1)
        self.assertIsNotNone(self.decoder.position)
        # Without a reset, the rest of the cut frame is junk and the whole one still parses.
        self.decoder.feed(whole[:10])
        self.decoder.feed(whole)
        self.assertEqual(self.decoder.frames, 2)


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
        # Fixes, but no expected offset (a frame not declared): no colour from it.
        self.assertEqual(verdict(None, 10.0), (None, "no residual: no expected offset"))
        self.assertEqual(verdict(None, 10.0, no_expected=STATION_FRAME_UNKNOWN),
                         (None, "no residual: station frame unknown: set reference_frame"))
        self.assertEqual(verdict(None, NO_FIX_RED_S + 1, no_expected=STATION_FRAME_UNKNOWN),
                         ("bad", "no fixed solution for 30 min"))
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
        self.pending = False    # an accepted restart, until the test clears it
        self.started = False
        self.last_exit = None
        self.output = OutputLines()

    def state(self):
        return self.state_value

    def running_s(self):
        return self.running

    def request_restart(self, reason):
        self.requests.append(reason)
        self.pending = self.pending or self.accept
        return self.accept

    def restart_pending(self):
        return self.pending

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


def _enu_to_ecef_delta(origin, enu):
    """The ECEF difference that is `enu` (east, north, up) at `origin`: the transpose of
    the rotation in rtcm.ecef_to_enu."""
    import math
    lat, lon, _ = rtcmdec.ecef_to_llh(*origin)
    lat, lon = math.radians(lat), math.radians(lon)
    e, n, u = enu
    sin_lat, cos_lat, sin_lon, cos_lon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    return (-sin_lon * e - sin_lat * cos_lon * n + cos_lat * cos_lon * u,
            cos_lon * e - sin_lat * sin_lon * n + cos_lat * sin_lon * u,
            cos_lat * n + sin_lat * u)


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
        """The NTRIP client's state first (until it streams), then rtkrcv's, then the
        corrections' and the solution's."""
        check = self.check()
        for client_state in ("connecting", "sourcetable", "login refused",
                             "mountpoint not found", "unsuitable mountpoint", "no data",
                             "backoff"):
            check.client.state, check.client.reason = client_state, "why"
            self.assertEqual(check.state(), client_state)
            self.assertEqual(check.snapshot()["reason"], "why")
        # The client's task died (cannot happen while the app runs): said, never silent.
        check.client.died, check.client.reason = True, "stopped"
        self.assertEqual(check.state(), "client stopped")
        self.assertEqual(check.snapshot()["reason"], "stopped")
        check.client.died = False
        check.client.state, check.client.reason = "connected", ""
        for runner_state in ("not available", "idle", "restarting"):
            self.stub.state_value = runner_state
            self.assertEqual(check.state(), runner_state)
        self.stub.state_value = "starting"
        self.stub.running = None
        self.assertEqual(check.state(), "starting", "between two processes")
        self.stub.running = 5.0
        self.assertEqual(check.state(), "starting", "no station data yet")
        check.station._frame(arp_frame(*CORS))
        self.assertEqual(check.state(), "no corrections", "station data without observations")
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(check.state(), "starting", "corrections, no solution yet")
        check._solution(solution(quality=2), "line")
        self.assertEqual(check.state(), "float")
        check._solution(solution(quality=1), "line")
        self.assertEqual(check.state(), "fixed")
        check._solution(solution(quality=5), "line")
        self.assertEqual(check.state(), "single")
        self.now += SOLUTION_STALE_S + 1
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(check.state(), "stale")
        self.now += CORRECTIONS_STALE_S + 1
        check._solution(solution(quality=1), "line")
        self.assertEqual(check.state(), "no corrections", "before stale or fixed")
        self.assertEqual(self.lines, ["line"] * 4)

    def test_offsets_session_and_residual(self):
        """The station declared in our own frame (ITRF2020): the expected offset is
        zero, and the residual is the session mean itself."""
        check = self.check(frame="ITRF2020")
        check._session_start()
        check.station._frame(arp_frame(*CORS, antenna_height=0.0))
        check.station._frame(frame_1033())
        self.assertEqual(check.state(), "no corrections", "a stream without MSM")
        check.station._frame(rtcm(1074, 40))
        east = 1e-7 * 111_319.49   # metres per 1e-7 degree of longitude at the equator
        for i in range(10):
            check._solution(solution(lon=1e-7 if i % 2 else 0.0, height=0.0 + 0.02 * (i % 2)),
                            "line")
            self.now += 1
        check._solution(solution(quality=2, height=1.0), "line")
        snap = check.snapshot()
        self.assertEqual(snap["state"], "float")
        self.assertEqual(snap["mountpoint"], "MP_1")
        self.assertEqual(snap["baseline_km"], round(abs(
            __import__("math").dist(OURS, CORS)) / 1000, 3))
        self.assertAlmostEqual(snap["baseline_km"], 24.88, delta=0.05)
        self.assertEqual(snap["solution"]["offset_enu"][2], 1.0)
        self.assertEqual(snap["solution"]["quality"], 2)
        self.assertEqual(snap["solution"]["differential_age_s"], 1.0)
        session = snap["session"]
        self.assertEqual((session["epochs"], session["counts"]["fixed"],
                          session["counts"]["float"], session["fixed_share"]),
                         (11, 10, 1, round(10 / 11, 3)))
        self.assertAlmostEqual(session["mean_enu"][0], east / 2, places=3)
        self.assertAlmostEqual(session["mean_enu"][1], 0.0, places=3)
        self.assertAlmostEqual(session["mean_enu"][2], 0.01, places=4)
        self.assertAlmostEqual(session["std_enu"][0], east / 2, places=3)
        self.assertAlmostEqual(session["std_enu"][2], 0.01, places=4)
        self.assertEqual(session["started_utc"], "2027-01-01T01:00:00+00:00")
        self.assertEqual((session["first_fix_age_s"], session["last_fix_age_s"]), (10.0, 1.0))
        self.assertEqual(snap["recent"]["kind"], "fixed")
        self.assertEqual(snap["recent"]["count"], 10)
        self.assertAlmostEqual(snap["residual_enu"][2], 0.01, places=4)
        self.assertAlmostEqual(snap["residual_enu"][0], east / 2, places=3)
        self.assertEqual(snap["verdict"], "good")
        self.assertEqual(snap["expected_enu"], [0.0, 0.0, 0.0])
        self.assertEqual((snap["station_frame"], snap["station_frame_source"],
                          snap["station_df021"], snap["base_frame"], snap["epoch"],
                          snap["no_expected"]),
                         ("ITRF2020", "reference_frame", 0, "ITRF2020", 2027.0, None))
        self.assertEqual(snap["corrections"]["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(snap["corrections"]["position"]["antenna_height_m"], 0.0)
        self.assertEqual(snap["base_ecef"], [round(v, 4) for v in OURS])
        self.assertEqual((snap["sessions"], snap["resets"], snap["gap_restarts"]), (1, 0, 0))
        self.assertEqual(snap["next_reset_s"], 23 * 3600)
        header = check.header()
        self.assertIn("RTK static against MP_1", header)
        self.assertIn("expected offset E N U +0.000 +0.000 +0.000 m (computed, station frame "
                      "minus ours)", header)
        self.assertIn("our frame ITRF2020 (base_frame) epoch 2027.00 (today), station frame "
                      "ITRF2020 (from reference_frame; stream DF021 0)", header)
        self.assertIn(f"CORS 1005 ECEF {CORS[0]:.4f} {CORS[1]:.4f} {CORS[2]:.4f} antenna height 0.0000", header)
        self.assertIn(f"our 1005 ECEF {OURS[0]:.4f}", header)
        self.assertIn("time GPST", header)
        json.dumps(snap, allow_nan=False)
        # Red once the residual is large, or there has been no fix for 30 minutes.
        check._session_start()
        for _ in range(3):
            check._solution(solution(height=0.5), "line")
        self.assertEqual(check.snapshot()["verdict"], "bad")
        self.assertIn("more than 20 cm", check.snapshot()["verdict_reason"])
        check._session_start()
        check._solution(solution(height=0.12), "line")
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
        check._solution(solution(height=0.0), "line")
        self.now += NO_FIX_RED_S + 60
        self.assertEqual(check.snapshot()["verdict"], "bad")
        check._session_start()
        previous = check.snapshot()["previous_session"]
        self.assertEqual((previous["verdict"], previous["verdict_reason"]),
                         ("bad", "no fixed solution for 31 min"))
        # Our 1005 changing while no solution comes is noticed by the snapshot itself,
        # and the new statistics' age starts over (not red at once).
        check._session_start()
        check._solution(solution(height=0.0), "line")
        self.now += NO_FIX_RED_S - 60
        self.assertEqual(check.snapshot()["verdict"], "good")
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 2.0)}
        snap = check.snapshot()
        self.assertEqual((snap["verdict"], snap["previous_session"]["ended_because"],
                          snap["session"]["epochs"]),
                         (None, "our 1005 position changed", 0))
        self.now += 120
        self.assertIsNone(check.snapshot()["verdict"], "the new statistics are 2 min old")

    def test_computed_offset_for_a_nad83_station(self):
        """The station declared NAD83(2011): the expected offset is the transformation
        at our antenna and today's epoch (datums.py, tested on its own), the residual
        is the mean minus it, and the log header and card say where it came from."""
        check = self.check(frame="NAD83(2011)")
        check._session_start()
        epoch = decimal_year(self.wall)
        expected = expected_offset_enu(OURS, "NAD83(2011)", epoch)
        self.assertGreater(max(abs(v) for v in expected), 0.5, "a metre or two at the equator")
        snap = check.snapshot()
        self.assertEqual(snap["expected_enu"], [round(v, 4) for v in expected])
        self.assertEqual((snap["station_frame"], snap["epoch"], snap["no_expected"]),
                         ("NAD83(2011)", round(epoch, 2), None))
        # A solution exactly at the expected offset: a zero residual, green.
        point = rtcmdec.llh_to_ecef(0.0, 0.0, 0.0)
        lat, lon, height = rtcmdec.ecef_to_llh(*(
            p + d for p, d in zip(point, _enu_to_ecef_delta(OURS, expected))))
        check._solution(solution(lat=lat, lon=lon, height=height), "line")
        snap = check.snapshot()
        for got, want in zip(snap["residual_enu"], (0.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want, places=3)
        self.assertEqual(snap["verdict"], "good")
        self.assertIn("station frame NAD83(2011) (from reference_frame; stream DF021 unknown)",
                      check.header())
        self.assertIn(f"epoch {epoch:.2f}", check.header())
        self.assertIn(" ".join(f"{v:+.3f}" for v in expected) + " m (computed", check.header())
        # The epoch moves with the wall clock: a year on, the offset for the SAME
        # ITRF2020 coordinate has changed by about the plate's motion (2 to 3 cm, the
        # NAD83 rotation rates), which is why the coordinate must be kept current.
        self.wall += 365 * 86400
        later = check.snapshot()["expected_enu"]
        drift = max(abs(a - b) for a, b in zip(later, expected))
        self.assertTrue(0.01 < drift < 0.05, drift)
        # With base_epoch set, the epoch is the coordinate's, whatever the date.
        pinned = self.check(frame="NAD83(2011)", base_epoch="2026-11-06")
        snap = pinned.snapshot()
        self.assertEqual((snap["epoch"], snap["epoch_source"]), (2026.85, "base_epoch"))
        self.assertEqual(snap["expected_enu"],
                         [round(v, 4) for v in expected_offset_enu(OURS, "NAD83(2011)",
                                                                   pinned.epoch())])
        self.assertIn("epoch 2026.85 (base_epoch)", pinned.header())

    def test_unknown_station_frame_shows_the_raw_offset_only(self):
        """The design rule: no guessing. Without reference_frame there is no expected
        offset, no residual and no residual colour, whatever the stream's DF021 says
        (RTCM reserves that field); the no-fix rule still applies."""
        check = self.check()
        check._session_start()
        check.station._frame(arp_frame_df021(*CORS, itrf=7))
        self.assertEqual(check.station.position["itrf_year"], 7)
        check.station._frame(rtcm(1074, 40))
        for _ in range(3):
            check._solution(solution(height=0.3), "line")
        snap = check.snapshot()
        self.assertEqual((snap["station_frame"], snap["station_frame_source"],
                          snap["station_df021"], snap["expected_enu"], snap["residual_enu"],
                          snap["no_expected"]),
                         (None, None, 7, None, None, STATION_FRAME_UNKNOWN))
        self.assertEqual(snap["session"]["mean_enu"], [0.0, 0.0, 0.3], "the raw offset")
        self.assertEqual((snap["verdict"], snap["verdict_reason"]),
                         (None, "no residual: station frame unknown: set reference_frame"))
        self.assertEqual(snap["corrections"]["position"]["itrf_year"], 7)
        header = check.header()
        self.assertIn("station frame unknown (set reference_frame; stream DF021 7)", header)
        self.assertIn("expected offset E N U none (computed", header)
        # The previous session keeps "no residual" and no colour.
        check._session_start()
        previous = check.snapshot()["previous_session"]
        self.assertEqual((previous["residual_enu"], previous["verdict"], previous["mean_enu"]),
                         (None, None, [0.0, 0.0, 0.3]))
        # 30 min without a fix is red whatever the frames.
        check._solution(solution(quality=2), "line")
        self.now += NO_FIX_RED_S + 1
        self.assertEqual(check.snapshot()["verdict"], "bad")
        # Our own frame not declared: the same, with its own reason.
        check = self.check(frame="NAD83(2011)", base_frame="")
        check._session_start()
        check._solution(solution(), "line")
        snap = check.snapshot()
        self.assertEqual((snap["base_frame"], snap["expected_enu"], snap["no_expected"]),
                         (None, None, OUR_FRAME_UNKNOWN))
        self.assertIn("our frame unknown (base_frame)", check.header())

    def test_without_our_1005_offsets_wait(self):
        self.base = None
        check = self.check(frame="ITRF2020")
        check._solution(solution(), "line")
        snap = check.snapshot()
        self.assertIsNone(snap["solution"]["offset_enu"])
        self.assertIsNone(snap["session"]["mean_enu"])
        self.assertIsNone(snap["baseline_km"])
        self.assertEqual(snap["session"]["counts"]["fixed"], 1)
        self.assertIn("our 1005 ECEF unknown", check.header())
        self.assertEqual((snap["expected_enu"], snap["no_expected"]),
                         (None, "our 1005 position is not known yet"))
        self.base = {"ecef": OURS}
        check._solution(solution(), "line")
        self.assertEqual(check.snapshot()["solution"]["offset_enu"][2], 3.0)

    def test_a_changed_1005_restarts_the_statistics(self):
        check = self.check(frame="ITRF2020")
        check._session_start()
        self.assertIsNone(check.snapshot()["previous_session"])
        for _ in range(5):
            check._solution(solution(height=0.0), "line")
        self.assertEqual(check.stats.fixed_count, 5)
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 1.0)}
        check._solution(solution(height=0.0), "line")
        snap = check.snapshot()
        self.assertEqual(snap["session"]["counts"]["fixed"], 1)
        previous = snap["previous_session"]
        self.assertEqual((previous["fixed"], previous["epochs"], previous["verdict"],
                          previous["ended_because"], previous["residual_enu"],
                          previous["mean_enu"]),
                         (5, 5, "good", "our 1005 position changed", [0.0, 0.0, 0.0],
                          [0.0, 0.0, 0.0]))
        self.assertEqual(previous["started_utc"], "2027-01-01T01:00:00+00:00")
        self.assertEqual(snap["solution"]["offset_enu"][2], -1.0)
        self.assertEqual(snap["base_changed_age_s"], 0.0)
        self.base = {"ecef": rtcmdec.llh_to_ecef(0.0, 0.0, 1.0001)}
        check._solution(solution(height=0.0), "line")
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
        check.station._frame(rtcm(1074, 40))
        self.now += GAP_RESTART_S
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(self.stub.requests, [], "a gap of exactly the limit is not over it")
        self.now += GAP_RESTART_S + 1
        check.station._frame(arp_frame(*CORS))
        check.station._frame(frame_1033())
        self.assertEqual(self.stub.requests, [], "1005 and 1033 are not corrections")
        self.assertEqual(check.state(), "no corrections")
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(self.stub.requests, ["corrections resumed after a 5 min gap"])
        self.assertEqual(check.gap_restarts, 1)
        self.stub.accept = False
        self.now += GAP_RESTART_S + 1
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(check.gap_restarts, 1, "not counted when the runner declined")
        self.stub.accept = True
        # The station's position: the first one of a session is the reference; a
        # change of more than 0.5 mm ends the session; the same one again does not.
        check.station._frame(arp_frame(*CORS))
        check.station._frame(arp_frame(*CORS))
        self.assertEqual(check.station_changes, 0)
        moved = rtcmdec.llh_to_ecef(0.225, 0.0, 10.01)
        check.station._frame(arp_frame(*moved))
        self.assertEqual((check.station_changes, self.stub.requests[-1]),
                         (1, "the station's 1005 position changed"))
        check._session_start()
        self.assertEqual(check.sessions, 2)
        self.now += GAP_RESTART_S + 1
        check.station._frame(rtcm(1074, 40))
        self.assertEqual(len(self.stub.requests), 3, "the first frame of a session is no gap")

    def test_snapshot_shape(self):
        check = self.check()
        snap = check.snapshot()
        for key in ("enabled", "state", "reason", "host", "port", "mountpoint", "user_set",
                    "station_changes", "previous_session",
                    "password_set", "expected_enu", "reset_utc", "next_reset_s", "base_ecef",
                    "station_frame", "station_frame_source", "station_df021", "base_frame",
                    "epoch", "epoch_source", "no_expected", "client", "cors_feed", "raw_errors",
                    "base_changed_age_s", "baseline_km", "corrections", "solution", "session",
                    "recent", "residual_enu", "verdict", "verdict_reason", "sessions", "resets",
                    "gap_restarts", "pid", "running_s", "restarts", "restarts_today",
                    "planned_restarts", "cpu_percent", "rss_bytes", "rtkrcv_version",
                    "feed", "solutions", "ephemerides", "error", "output", "last_exit",
                    "retry_in_s"):
            self.assertIn(key, snap, key)
        self.assertEqual((snap["user_set"], snap["password_set"], snap["host"], snap["port"]),
                         (True, True, "127.0.0.1", 1))
        self.assertEqual(snap["client"]["state"], "off", "not started")
        self.assertEqual(snap["client"]["mountpoint"], "MP_1")
        self.assertNotIn(SENTINEL, json.dumps(snap))
        self.assertEqual(snap["output"], [])
        # The station's bytes go to the raw log, the decoder and rtkrcv's second input;
        # a new connection (the client's connect callback) starts a new parse.
        raw = []
        check._on_raw = raw.append
        check._on_data(arp_frame(*CORS)[:10])
        check.client._on_connect()
        check._on_data(arp_frame(*CORS))
        self.assertEqual(b"".join(raw), arp_frame(*CORS)[:10] + arp_frame(*CORS))
        self.assertEqual(check.station.frames, 1)
        self.assertEqual(check.cors_feed.frames_sent, 0, "rtkrcv not connected: nothing sent")
        check._on_raw = mock.Mock(side_effect=OSError("disk"))
        with self.assertLogs("gnss_base", "ERROR"):
            check._on_data(rtcm(1074, 40))
        self.assertEqual((check.raw_errors, check.station.frames), (1, 2))


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


class WithFakeCaster(unittest.IsolatedAsyncioTestCase):
    """The check with the fake caster and the fake rtkrcv: the station's bytes reach
    the raw log, the decoder and rtkrcv's second input; a stream without observations
    is "no corrections", not a hang; a refused login is the headline; and rtkrcv's
    config holds no credential."""

    async def asyncSetUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.caster = FakeCaster(data=arp_frame(*CORS) + frame_1033(), period=0.05,
                                 login=("user", SENTINEL))
        self.port = await self.caster.start()
        self.addAsyncCleanup(self.caster.stop)

    def check(self, hub, raw, **fields):
        made = ReferenceCheck(
            settings(binary=str(FAKE), work_dir=self.folder / "w", host="127.0.0.1",
                     port=self.port, **fields),
            hub, lambda: {"ecef": OURS}, on_raw=raw.append, poll_s=0.05, hang_s=0.5)
        self.addAsyncCleanup(made.stop)
        return made

    async def test_station_stream_without_observations_is_no_corrections_not_a_hang(self):
        record = self.folder / "record.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal",
                                          "FAKE_RTKRCV_RECORD": str(record),
                                          "FAKE_RTKRCV_SOLUTION": "0 0 3.0 5 20"}):
            hub = RtcmHub()
            raw = []
            check = self.check(hub, raw)
            check.start()
            for prn in range(1, 6):
                hub.publish(eph(prn))
            deadline = asyncio.get_running_loop().time() + 10
            while check.stats.counts["single"] < 5 or not check.cors_feed.frames_sent:
                self.assertLess(asyncio.get_running_loop().time(), deadline, check.snapshot())
                hub.publish(rtcm(1074, 40))
                hub.publish(eph(1))
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.8)   # longer than hang_s, with MSM still arriving
            hub.publish(rtcm(1074, 40))
            snap = check.snapshot()
            self.assertEqual(snap["state"], "no corrections", snap)
            self.assertEqual(snap["client"]["state"], "connected")
            self.assertEqual((snap["restarts"], snap["planned_restarts"]), (0, 0))
            self.assertIsNotNone(snap["corrections"]["position"], "1005 came, MSM did not")
            self.assertIsNone(snap["corrections"]["msm_age_s"])
            self.assertEqual(snap["corrections"]["descriptors"]["antenna"], "GPPNULLANTENNA")
            self.assertEqual(snap["session"]["counts"]["fixed"], 0)
            self.assertEqual(snap["recent"]["kind"], "all")
            self.assertIsNone(snap["verdict"])
            self.assertTrue(b"".join(raw).startswith(self.caster.data), "the raw log gets it all")
            self.assertGreater(snap["cors_feed"]["frames_sent"], 0)
            self.assertEqual(snap["cors_feed"]["connected"], True)
            self.assertEqual(self.caster.most_open, 1)
            config = (self.folder / "w" / CONFIG_NAME).read_text()
            self.assertNotIn(SENTINEL, config)
            self.assertNotIn("ntripcli", config)
            self.assertIn(f"inpstr2-path   =127.0.0.1:{check.cors_feed.port}\n", config)
            self.assertNotEqual(stat.S_IMODE((self.folder / "w" / CONFIG_NAME).stat().st_mode)
                                & 0o077, 0, "no longer a private file: nothing secret in it")
            await check.stop()
            exit_line = json.loads(record.read_text().splitlines()[-1])
            self.assertEqual(exit_line["event"], "exit")
            self.assertGreater(exit_line["received2"], 0, "rtkrcv read the station's bytes")
            self.assertNotIn(SENTINEL, json.dumps(snap))

    async def test_refused_login_is_the_headline(self):
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal"}):
            hub = RtcmHub()
            raw = []
            check = self.check(hub, raw, password=Secret("wrong"))
            check.start()
            hub.publish(rtcm(1074, 40))
            deadline = asyncio.get_running_loop().time() + 10
            while check.state() != "login refused":
                self.assertLess(asyncio.get_running_loop().time(), deadline, check.snapshot())
                await asyncio.sleep(0.05)
            snap = check.snapshot()
            self.assertIn("Login refused by the caster (HTTP/1.1 401 Unauthorized)", snap["reason"])
            self.assertEqual(snap["client"]["retry_in_s"], 300)
            self.assertEqual(raw, [], "nothing logged")
            self.assertEqual(self.caster.stream_connections, 0)
            self.assertNotIn("wrong", json.dumps(snap))
            # The sourcetable was read, and says what the mountpoint is.
            self.assertEqual(snap["client"]["sourcetable"]["format"], "RTCM 3.2")


# ---------------------------------------------------------------- 0.7.0: two references

def settings2(**fields):
    """The second check's settings: the options' prefix is reference2."""
    return settings(**(dict(prefix="reference2", user="user2") | fields))


class SecondReferenceOptions(unittest.TestCase):
    def test_messages_name_the_second_checks_options(self):
        self.assertEqual(settings2(host="", mountpoint="", user="").problem(),
                         "set reference2_host, reference2_mountpoint, reference2_user")
        cases = ((dict(port=0), "reference2_port must be 1 to 65535"),
                 (dict(mountpoint="M P"), "reference2_mountpoint has a space"),
                 (dict(password=Secret("p w")), "reference2_password has a space"),
                 (dict(frame="WGS84"), "reference2_frame must be one of ITRF2020, ITRF2014, "
                                       "ITRF2008, NAD83(2011), or empty"),
                 (dict(reset_utc="25:00"),
                  "reference2_reset_utc must be HH:MM (UTC, 24 h) or empty"),
                 (dict(gga_position="north"),
                  'reference2_gga_position must be "lat,lon" in decimal degrees, or empty'),
                 *((dict(expect_station=bad), "reference2_expect_station must be a whole "
                   "number from 0 to 4095, or empty") for bad in (-1, 4096, True, 7.0, "x")))
        for fields, expected in cases:
            self.assertEqual(settings2(**fields).problem(), expected, fields)
        self.assertEqual(settings2().problem(), "")
        for good in (None, 0, 4095):
            self.assertEqual(settings2(expect_station=good).problem(), "", good)
        self.assertEqual(settings2(frame="nad83(2011)").station_frame(), "NAD83(2011)")
        check = ReferenceCheck(settings2(), RtcmHub(), lambda: None, label="reference check 2")
        self.assertEqual(check.no_expected(), "station frame unknown: set reference2_frame")
        self.assertIn("set reference2_frame", check.header())
        snap = check.snapshot()
        self.assertEqual((snap["option"], snap["station_frame_source"]), ("reference2", None))
        self.assertIn("reference2_frame", verdict(None, 1.0, no_expected=check.no_expected())[1])

    def test_gga_position(self):
        self.assertIsNone(parse_gga_position(""))
        self.assertIsNone(parse_gga_position("  "))
        self.assertEqual(parse_gga_position("12.5,-45.25"), (12.5, -45.25))
        self.assertEqual(parse_gga_position(" -0.5 , +170 "), (-0.5, 170.0))
        self.assertEqual(parse_gga_position(".5,1."), (0.5, 1.0))
        for bad in ("12.5", "12.5;-45", "12.5,-45,3", "a,b", "12.5 -45", "1e3,2", "nan,0",
                    "12,5,-45"):
            with self.assertRaises(ValueError, msg=bad) as caught:
                parse_gga_position(bad, "reference2_gga_position")
            self.assertIn("reference2_gga_position", str(caught.exception))
            self.assertNotIn(bad, str(caught.exception), "the message names the option only")
        for bad in ("91,0", "-90.5,0", "0,180.5", "0,-181"):
            with self.assertRaises(ValueError) as caught:
                parse_gga_position(bad)
            self.assertIn("latitude from -90 to 90", str(caught.exception))
        self.assertEqual(settings(gga_position="12.5,-45.25").problem(), "")
        self.assertEqual(settings(gga_position="999,0").problem(),
                         "reference_gga_position must have a latitude from -90 to 90 and a "
                         "longitude from -180 to 180")
        # A bad value stops that check only: it is "not configured" and nothing starts.
        check = ReferenceCheck(settings2(gga_position="x"), RtcmHub(), lambda: None)
        check.runner = StubRunner()
        self.assertEqual(check.state(), "not configured")
        self.assertIn("reference2_gga_position", check.snapshot()["reason"])

    def test_options_defaults_overrides_and_the_login_clash(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        options = folder / "options.json"
        options.write_text(json.dumps({
            "reference_enabled": True, "reference_host": "h", "reference_mountpoint": "MP",
            "reference_user": "u", "reference_password": SENTINEL,
            "reference2_enabled": True, "reference2_host": "h2", "reference2_port": 2102,
            "reference2_mountpoint": "NEAREST", "reference2_user": "u2",
            "reference2_password": SENTINEL + "2", "reference2_frame": "NAD83(2011)",
            "reference2_reset_utc": "06:30", "reference2_send_gga": True,
            "reference2_gga_position": "12.5,-45.25", "reference2_expect_station": 1234}))
        loaded = load_settings(["--options", str(options)])
        first, second = all_reference_settings(loaded)
        self.assertEqual((first.prefix, first.send_gga, first.gga_position, first.work_dir),
                         ("reference", False, "", folder / "reference"))
        self.assertEqual((second.enabled, second.host, second.port, second.mountpoint,
                          second.user, second.password.reveal(), second.frame,
                          second.reset_utc, second.send_gga, second.gga_latlon(),
                          second.prefix, second.work_dir, second.base_frame),
                         (True, "h2", 2102, "NEAREST", "u2", SENTINEL + "2", "NAD83(2011)",
                          "06:30", True, (12.5, -45.25), "reference2",
                          folder / "reference2", "ITRF2020"))
        self.assertEqual((first.problem(), second.problem()), ("", ""))
        self.assertEqual((first.expect_station, second.expect_station), (None, 1234))
        self.assertNotIn("reference2_password", loaded, "the password is not kept around")
        for key, default in (("reference_send_gga", False), ("reference_gga_position", ""),
                             ("reference2_enabled", False), ("reference2_host", ""),
                             ("reference2_port", 2101), ("reference2_mountpoint", ""),
                             ("reference2_user", ""), ("reference2_password", ""),
                             ("reference2_frame", ""), ("reference2_reset_utc", "00:00"),
                             ("reference2_send_gga", False), ("reference2_gga_position", ""),
                             ("reference_expect_station", None),
                             ("reference2_expect_station", None)):
            self.assertEqual(DEFAULTS[key], default, key)
            self.assertEqual(key in OVERRIDES, not key.endswith("password"), key)
        loaded = load_settings(["--options", str(options), "--no-reference2",
                                "--reference2-host", "h3", "--reference2-port", "2103",
                                "--no-reference2-send-gga", "--reference-send-gga",
                                "--reference2-gga-position", "",
                                "--reference-expect-station", "42"])
        first, second = all_reference_settings(loaded)
        self.assertEqual((first.expect_station, second.expect_station), (42, 1234))
        for raw, want in (("", None), (" 17 ", 17), (None, None), ("x", "x")):
            loaded = load_settings(["--options", str(options)])
            loaded["reference_expect_station"] = raw
            self.assertEqual(reference_settings(loaded).expect_station, want, raw)
        self.assertEqual((second.enabled, second.host, second.port, second.send_gga,
                          second.gga_position, first.send_gga), (False, "h3", 2103, False, "",
                                                                 True))
        with self.assertRaises(SystemExit):
            load_settings(["--reference2-password", "x"])
        # The same caster and user twice: the second is blocked, the first runs.
        options.write_text(json.dumps({
            "reference_enabled": True, "reference_host": "Caster.example",
            "reference_mountpoint": "A", "reference_user": "u",
            "reference2_enabled": True, "reference2_host": "caster.example",
            "reference2_mountpoint": "B", "reference2_user": "u"}))
        first, second = all_reference_settings(load_settings(["--options", str(options)]))
        self.assertEqual(first.problem(), "")
        self.assertEqual(second.problem(), "reference2 uses the same caster and user as "
                         "reference: one login allows one connection; use another login")
        self.assertEqual(login_clash(settings(), settings(prefix="reference2", enabled=False)), "")
        self.assertEqual(login_clash(settings(), settings2()), "", "another user")
        # Defaults alone: the second check is off.
        first, second = all_reference_settings(load_settings(
            ["--options", str(folder / "none.json")]))
        self.assertEqual((first.enabled, second.enabled), (False, False))


class StationIdentity(unittest.TestCase):
    """A nearest-station mountpoint can switch stations: a new station ID (or
    position) restarts rtkrcv, is logged once, and is on the card."""

    def setUp(self):
        self.now = 1000.0
        self.wall = 1_798_761_600.0

    def check(self, **fields):
        made = ReferenceCheck(settings2(**fields), RtcmHub(), lambda: {"ecef": OURS},
                              clock=lambda: self.now, wall=lambda: self.wall,
                              label="reference check 2")
        self.stub = made.runner = StubRunner()
        return made

    def test_a_new_station_id_is_a_station_change(self):
        from tests.test_app import arp_frame as make
        check = self.check()
        check._session_start()
        check.station._frame(make(*CORS, station=7))
        check.station._frame(frame_1033(station=7))
        snap = check.snapshot()
        self.assertEqual((snap["station_id"], snap["station_changed"]), (7, None))
        self.assertEqual(snap["corrections"]["descriptors"]["receiver"], "LEICA GR30")
        self.now += 60
        with self.assertLogs("gnss_base", "WARNING") as logs:
            check.station._frame(make(*CORS, station=12))
        self.assertEqual(self.stub.requests, ["the station changed (ID 7 to 12)"])
        self.assertEqual(check.station_changes, 1)
        text = "\n".join(logs.output)
        self.assertIn("reference check 2: station changed: ID 7 to 12, 0.000 km", text)
        self.assertNotIn(f"{CORS[0]:.4f}", text, "no position in the log")
        self.now += 30
        snap = check.snapshot()
        self.assertEqual(snap["station_id"], 12)
        self.assertEqual(snap["station_changed"], {"utc": "2027-01-01T00:00:00+00:00",
                                                   "from_id": 7, "to_id": 12, "moved_m": 0.0,
                                                   "age_s": 30.0})
        self.assertIsNone(snap["corrections"]["descriptors"], "the old station's 1033 is gone")
        # The same station again: no new change, no new log.
        with self.assertNoLogs("gnss_base", "WARNING"):
            check.station._frame(make(*CORS, station=12))
        self.assertEqual(len(self.stub.requests), 1)
        # Across sessions too: a station that moved while rtkrcv restarted is seen.
        check._session_start()
        moved = rtcmdec.llh_to_ecef(0.5, 0.0, 10.0)
        with self.assertLogs("gnss_base", "WARNING") as logs:
            check.station._frame(make(*moved, station=12))
        self.assertIn("ID 12 to 12, 30.", "\n".join(logs.output))
        self.assertEqual(len(self.stub.requests), 1, "a new session's first 1005 is its reference")
        self.assertEqual(check.snapshot()["station_changed"]["from_id"], 12)

    def test_gga_position_from_our_1005_or_the_option(self):
        check = self.check(send_gga=True)
        lat, lon, height = check._gga_position()
        self.assertAlmostEqual(lat, 0.0, places=9)
        self.assertAlmostEqual(height, 0.0, places=3)
        self.assertEqual(check.snapshot()["gga_source"], "our 1005")
        check = self.check(send_gga=True, gga_position="12.5,-45.25")
        self.assertEqual(check._gga_position(), (12.5, -45.25, 0.0))
        self.assertEqual(check.snapshot()["gga_source"], "reference2_gga_position")
        self.assertIsNone(self.check().snapshot()["gga_source"])
        nowhere = ReferenceCheck(settings2(send_gga=True), RtcmHub(), lambda: None)
        self.assertIsNone(nowhere._gga_position())

    def test_gga_position_follows_the_current_1005(self):
        """S4: the current 1005 first, even while no solution has updated the check's
        own copy; the last one seen only when there is none now."""
        current = {"ecef": OURS}
        check = ReferenceCheck(settings2(send_gga=True), RtcmHub(), lambda: current,
                               clock=lambda: self.now, wall=lambda: self.wall)
        check.runner = StubRunner()
        check._check_base(self.now)
        self.assertEqual(check._base_ecef, OURS)
        current = {"ecef": CORS}       # a new fixed position; no solution since
        lat, lon, height = check._gga_position()
        self.assertAlmostEqual(lat, 0.225, places=7)
        self.assertEqual(check._base_ecef, OURS, "the offsets' copy is not touched here")
        current = None                 # no 1005 now: the last one seen
        lat, lon, height = check._gga_position()
        self.assertAlmostEqual(lat, 0.0, places=7)


class Comparison(unittest.TestCase):
    """The Reference comparison card's numbers and colours."""

    def setUp(self):
        self.now = 1000.0

    def pair(self, first_frame="ITRF2020", second_frame="ITRF2020"):
        checks = []
        for fields in (dict(frame=first_frame), dict(prefix="reference2", user="u2",
                                                     frame=second_frame)):
            made = ReferenceCheck(settings(**fields), RtcmHub(), lambda: {"ecef": OURS},
                                  clock=lambda: self.now)
            made.runner = StubRunner()
            made._session_start()
            made.client.state = "connected"   # streaming: the comparison is current
            checks.append(made)
        return checks

    @staticmethod
    def feed(check, enu, count=3):
        point = [p + d for p, d in zip(OURS, _enu_to_ecef_delta(OURS, enu))]
        lat, lon, height = rtcmdec.ecef_to_llh(*point)
        for _ in range(count):
            check._solution(solution(lat=lat, lon=lon, height=height), "line")

    def test_differences_and_colours(self):
        first, second = self.pair()
        result = compare(first, second)
        self.assertEqual((result["shown"], result["reason"]),
                         (False, "no session mean of fixed epochs yet: reference, reference2"))
        self.feed(first, (0.10, -0.20, 0.30))
        self.assertIn("reference2", compare(first, second)["reason"])
        self.feed(second, (0.12, -0.21, 0.35))
        result = compare(first, second)
        self.assertTrue(result["shown"])
        for got, want in zip(result["first_minus_ours"], (0.10, -0.20, 0.30)):
            self.assertAlmostEqual(got, want, places=3)
        for got, want in zip(result["second_minus_ours"], (0.12, -0.21, 0.35)):
            self.assertAlmostEqual(got, want, places=3)
        for got, want in zip(result["second_minus_first"], (0.02, -0.01, 0.05)):
            self.assertAlmostEqual(got, want, places=3)
        self.assertEqual((result["residual"], result["verdict"], result["first_frame"],
                          result["second_frame"], result["first_fixed"]),
                         (True, "good", "ITRF2020", "ITRF2020", 3))
        self.assertEqual(result["verdict_reason"],
                         "within 3 cm horizontal and 6 cm up (ITRF2020)")
        json.dumps(result, allow_nan=False)

    def test_stale_without_streaming_or_a_recent_fix(self):
        """S2: a verdict only while both stream and each has a fix in the last 30 min;
        otherwise the numbers stay, marked stale, with each side's last-fix age."""
        first, second = self.pair()
        self.feed(first, (0.0, 0.0, 0.0))
        self.feed(second, (0.01, 0.0, 0.0))
        self.assertEqual(compare(first, second)["verdict"], "good")
        second.client.state = "login refused"
        result = compare(first, second)
        self.assertEqual((result["shown"], result["stale"], result["verdict"],
                          result["residual"]), (True, True, None, False))
        self.assertEqual(result["stale_reason"], "reference2 is not streaming (login refused)")
        self.assertTrue(result["verdict_reason"].startswith("stale, no verdict: "))
        self.assertAlmostEqual(result["second_minus_first"][0], 0.01, places=3)
        second.client.state = "connected"
        second.client.died = True
        self.assertTrue(compare(first, second)["stale"], "a stopped client is not streaming")
        second.client.died = False
        # Streaming, but the first's last fix is more than 30 min old.
        self.now += COMPARE_FRESH_S + 1
        self.feed(second, (0.01, 0.0, 0.0))
        result = compare(first, second)
        self.assertEqual((result["stale"], result["verdict"]), (True, None))
        self.assertEqual(result["stale_reason"], "reference has no fix in the last 30 min")
        self.assertEqual((result["first_last_fix_age_s"], result["second_last_fix_age_s"]),
                         (COMPARE_FRESH_S + 1, 0.0))
        self.feed(first, (0.0, 0.0, 0.0))
        self.assertEqual((compare(first, second)["stale"], compare(first, second)["verdict"]),
                         (False, "good"))

    def test_stale_while_rtkrcv_restarts(self):
        """R2-3: a nearest-station switch while both stream asks rtkrcv for a new
        session; until it has restarted, the session mean is the old station's, so
        no verdict under the new station's ID. Also none while no rtkrcv runs."""
        from tests.test_app import arp_frame as make
        first, second = self.pair()
        for check in (first, second):
            check.station._frame(make(*CORS, station=3))
        self.feed(first, (0.0, 0.0, 0.0))
        self.feed(second, (0.01, 0.0, 0.0))
        self.assertEqual(compare(first, second)["verdict"], "good")
        with self.assertLogs("gnss_base", "WARNING"):
            second.station._frame(make(*CORS, station=12))
        self.assertTrue(second.runner.pending, "the switch asked for a restart")
        result = compare(first, second)
        self.assertEqual((result["second_station_id"], result["stale"], result["verdict"],
                          result["residual"]), (12, True, None, False))
        self.assertEqual(result["stale_reason"],
                         "reference2's rtkrcv is restarting (a new session)")
        # Between the old rtkrcv's exit and the new one's start: no process.
        second.runner.pending = False
        second.runner.running = None
        self.assertEqual(compare(first, second)["verdict"], None)
        self.assertTrue(compare(first, second)["stale"])
        # The new session: no mean yet, so nothing shown, and no old colour.
        second.runner.running = 0.0
        second._session_start()
        result = compare(first, second)
        self.assertEqual((result["shown"], result["verdict"]), (False, None))
        self.feed(second, (0.02, 0.0, 0.0))
        result = compare(first, second)
        self.assertEqual((result["second_station_id"], result["stale"], result["verdict"]),
                         (12, False, "good"))

    def test_station_ids_and_a_wrong_station(self):
        """S3: both station IDs on the card; a station other than the expected one
        takes the colour off the check and the comparison, logged once per change."""
        from tests.test_app import arp_frame as make
        first, second = self.pair()
        second.settings.expect_station = 7
        for check, station in ((first, 3), (second, 7)):
            check.station._frame(make(*CORS, station=station))
        self.feed(first, (0.0, 0.0, 0.0))
        self.feed(second, (0.01, 0.0, 0.0))
        result = compare(first, second)
        self.assertEqual((result["first_station_id"], result["second_station_id"],
                          result["station_mismatch"], result["verdict"]), (3, 7, None, "good"))
        self.assertIsNone(second.snapshot()["station_mismatch"])
        self.assertEqual(second.snapshot()["expect_station"], 7)
        with self.assertLogs("gnss_base", "WARNING") as logs:
            second.station._frame(make(*CORS, station=12))
        self.assertEqual(sum("wrong station (got 12, expected 7) (reference2_expect_station)"
                             in line for line in logs.output), 1)
        with self.assertNoLogs("gnss_base", "WARNING"):
            second.station._frame(make(*CORS, station=12))   # the same: not again
        snap = second.snapshot()
        self.assertEqual(snap["station_mismatch"], "wrong station (got 12, expected 7)")
        self.assertIsNone(snap["verdict"])
        self.assertIn("wrong station (got 12, expected 7)", snap["verdict_reason"])
        result = compare(first, second)
        self.assertEqual((result["station_mismatch"], result["verdict"], result["residual"]),
                         ("reference2: wrong station (got 12, expected 7)", None, False))
        self.assertTrue(result["shown"])
        with self.assertLogs("gnss_base", "WARNING") as logs:
            second.station._frame(make(*CORS, station=13))
        self.assertIn("got 13, expected 7", "\n".join(logs.output), "a new change is logged")
        with self.assertLogs("gnss_base", "INFO") as logs:
            second.station._frame(make(*CORS, station=7))
        self.assertIn("the expected station 7 again", "\n".join(logs.output))
        self.assertIsNone(second.snapshot()["station_mismatch"])
        self.assertEqual(first.wrong_station(), "", "no expectation: any station")

    def test_verdict_limits(self):
        self.assertEqual(compare_verdict([0.03, 0.0, 0.06])[0], "good", "the limits count")
        self.assertEqual(compare_verdict([0.025, 0.025, 0.0])[0], "warn", "3.5 cm horizontal")
        self.assertEqual(compare_verdict([0.0, 0.0, -0.07])[0], "warn")
        self.assertEqual(compare_verdict([0.1, 0.0, 0.1])[0], "warn")
        self.assertEqual(compare_verdict([0.08, 0.08, 0.0])[0], "bad", "11 cm horizontal")
        self.assertEqual(compare_verdict([0.0, 0.0, 0.11]),
                         ("bad", "more than 10 cm apart"))

    def test_frames_not_declared_or_different_raw_only(self):
        for frames, reason in (
                (("ITRF2020", ""), "no residual: station frame unknown: set reference2_frame"),
                (("", ""), "no residual: station frame unknown: set reference_frame, "
                           "reference2_frame"),
                (("NAD83(2011)", "ITRF2020"),
                 "no residual: the frames differ (NAD83(2011), ITRF2020), so the difference "
                 "includes the transformation between them")):
            first, second = self.pair(*frames)
            self.feed(first, (0.5, -1.2, 1.2))
            self.feed(second, (0.0, 0.0, 0.0))
            result = compare(first, second)
            self.assertTrue(result["shown"], frames)
            self.assertEqual((result["residual"], result["verdict"], result["verdict_reason"]),
                             (False, None, reason), frames)
            self.assertAlmostEqual(result["second_minus_first"][2], -1.2, places=3)

    def test_not_shown_when_off_or_from_another_1005(self):
        first, second = self.pair()
        self.feed(first, (0.0, 0.0, 0.0))
        self.feed(second, (0.0, 0.0, 0.0))
        second._base_ecef = tuple(v + 0.01 for v in OURS)
        self.assertEqual(compare(first, second)["reason"],
                         "the two sessions are not from the same 1005 position of ours")
        off = ReferenceCheck(settings2(enabled=False), RtcmHub(), lambda: None)
        self.assertEqual(compare(first, off)["reason"], "reference2 is off or not configured")


class TwoChecks(unittest.IsolatedAsyncioTestCase):
    """Two checks side by side against two fake casters and the fake rtkrcv: each its
    own client, rtkrcv, work folder and state; a refused login on one leaves the other
    streaming; the second sends a GGA to its nearest-station mountpoint only."""

    async def test_two_independent_checks(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        first_caster = FakeCaster(data=arp_frame(*CORS) + frame_1033(), period=0.05,
                                  login=("user", SENTINEL))
        second_caster = FakeCaster(data=arp_frame(*CORS) + frame_1033(), period=0.05,
                                   login=("user2", SENTINEL),
                                   mountpoints={"NEAREST": {"nmea": "1"}})
        ports = [await first_caster.start(), await second_caster.start()]
        self.addAsyncCleanup(first_caster.stop)
        self.addAsyncCleanup(second_caster.stop)
        with mock.patch.dict(os.environ, {"FAKE_RTKRCV_PERIOD": "0.05",
                                          "FAKE_RTKRCV_MODE": "normal",
                                          "FAKE_RTKRCV_SOLUTION": "0 0 3.0 5 20"}):
            hub = RtcmHub()
            first = ReferenceCheck(
                settings(binary=str(FAKE), work_dir=folder / "reference", port=ports[0],
                         password=Secret("wrong")),
                hub, lambda: {"ecef": OURS}, poll_s=0.05, hang_s=0.5)
            second = ReferenceCheck(
                settings2(binary=str(FAKE), work_dir=folder / "reference2", port=ports[1],
                          mountpoint="NEAREST", send_gga=True, gga_position="12.5,-45.25"),
                hub, lambda: {"ecef": OURS}, poll_s=0.05, hang_s=0.5,
                label="reference check 2")
            self.addAsyncCleanup(first.stop)
            self.addAsyncCleanup(second.stop)
            with self.assertLogs("gnss_base", "INFO") as logs:
                first.start()
                second.start()
                deadline = asyncio.get_running_loop().time() + 10
                while (first.state() != "login refused" or second.stats.counts["single"] < 3
                       or not second_caster.uplink):
                    self.assertLess(asyncio.get_running_loop().time(), deadline,
                                    (first.snapshot()["state"], second.snapshot()["state"]))
                    hub.publish(rtcm(1074, 40))
                    hub.publish(eph(1))
                    await asyncio.sleep(0.05)
            one, two = first.snapshot(), second.snapshot()
            self.assertEqual(one["state"], "login refused")
            self.assertIn("check reference_user and reference_password", one["reason"])
            self.assertEqual(two["client"]["state"], "connected")
            self.assertEqual(two["state"], "no corrections", "streaming, no MSM from it")
            self.assertEqual((two["option"], two["send_gga"], two["gga_source"]),
                             ("reference2", True, "reference2_gga_position"))
            self.assertEqual(two["station_id"], 7)
            self.assertGreaterEqual(two["client"]["gga_sent"], 1)
            # The configured position, the system clock's time (not compared), a valid
            # checksum.
            sentence = second_caster.uplink[0].decode()
            self.assertEqual(sentence.split(",")[2:6], ["1230.00000", "N", "04515.00000", "W"])
            self.assertEqual(sentence.split(",")[6:14],
                             build_gga(12.5, -45.25, 0.0, 0.0).decode().split(",")[6:14])
            body, _, checksum = sentence.strip().partition("*")
            value = 0
            for char in body[1:]:
                value ^= ord(char)
            self.assertEqual(checksum, f"{value:02X}")
            self.assertEqual(first_caster.uplink, [], "no GGA to the first caster")
            self.assertEqual(first_caster.stream_connections, 0)
            self.assertTrue((folder / "reference2" / CONFIG_NAME).exists())
            self.assertNotEqual(first.runner.snapshot()["pid"], second.runner.snapshot()["pid"])
            self.assertEqual([s.name for s in hub._subscribers].count("reference check 2"), 1)
            text = "\n".join(logs.output)
            self.assertIn("NTRIP client (reference2): connected", text)
            self.assertIn("NTRIP client: Login refused", text, "the first logs as before")
            for secret in (SENTINEL, "GPGGA", "1230.00000", "wrong"):
                self.assertNotIn(secret, text, secret)
                self.assertNotIn(secret, json.dumps(one) + json.dumps(two), secret)


def find_rtkrcv():
    return (os.environ.get("RTKRCV") or shutil.which("rtkrcv-ex") or shutil.which("rtkrcv"))


@unittest.skipUnless(find_rtkrcv(), "no rtkrcv binary (set RTKRCV or put rtkrcv-ex on PATH)")
class RealRtkrcv(unittest.IsolatedAsyncioTestCase):
    """Smoke test with the real rtkrcv: it takes our reference config (every option
    name and value), connects to the feed, solution and station servers (the app's NTRIP
    client points at a closed local port, so it never reaches a real caster), and exits
    with code 0 on SIGTERM."""

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
        # rtkrcv connects both input streams at start, the station's one included.
        while not (runner.feed.connections and runner.solutions.connections
                   and check.cors_feed.connections and runner.rss_bytes):
            self.assertLess(loop.time(), deadline, check.snapshot())
            await asyncio.sleep(0.1)
        client = check.snapshot()["client"]
        self.assertIn(client["state"], ("connecting", "sourcetable", "backoff"), client)
        if client["state"] == "backoff":
            self.assertIn("Connection failed", client["reason"])
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
        sample = arp_frame(*CORS, antenna_height=0.0) + frame_1033() + rtcm(1074, 40)
        caster = FakeCaster(data=sample, period=0.2, login=("user", SENTINEL))
        caster_port = caster.run_in_thread()
        self.addCleanup(caster.stop_thread)
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
            "reference_host": "127.0.0.1", "reference_port": caster_port,
            "reference_mountpoint": "MP_1",
            "reference_user": "user", "reference_password": SENTINEL,
            "reference_frame": "ITRF2020"}))
        env = dict(os.environ, FAKE_RTKRCV_PERIOD="0.2", FAKE_RTKRCV_MODE="normal",
                   FAKE_RTKRCV_SOLUTION="0 0 0.01 1 25")
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
        self.assertEqual(reference["client"]["state"], "connected")
        self.assertEqual(reference["client"]["sourcetable"]["mountpoint"], "MP_1")
        self.assertEqual([p for p, _ in caster.requests][:2], ["/", "/MP_1"])
        self.assertIn("authorization", caster.requests[1][1])
        self.assertEqual(caster.most_open, 1)
        self.assertEqual(reference["solution"]["offset_enu"], [0.0, 0.0, 0.01])
        self.assertEqual(reference["corrections"]["descriptors"]["antenna"], "GPPNULLANTENNA")
        self.assertEqual(reference["log"]["folder"], "reference")
        self.assertEqual((reference["station_frame"], reference["expected_enu"]),
                         ("ITRF2020", [0.0, 0.0, 0.0]))
        # The CORS stream as received is on disk, and on the Log files table.
        (raw,) = (directory / "logs" / "reference" / "raw").glob("*.rtcm3")
        self.assertTrue(raw.read_bytes().startswith(sample), "the stream as sent")
        self.assertGreater(raw.stat().st_size, len(sample), "and on")
        self.assertGreater(reference["raw_log"]["bytes_today"], 0)
        self.assertIn(f"reference/raw/{raw.name}",
                      [f["file"] for f in status["storage"]["files"]])
        self.assertIn(raw.name, [f["file"] for f in reference["raw_log"]["files"]])
        self.assertEqual(status["measured"]["state"], "running", "the other instance too")
        config = directory / "reference" / CONFIG_NAME
        self.assertNotIn(SENTINEL, config.read_text())
        self.assertNotIn("ntripcli", config.read_text())
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


class EndToEndTwo(unittest.TestCase):
    """0.7.0: the whole app with both checks, the second on a nearest-station (nmea 1)
    mountpoint with the GGA uplink and our 1005 as its position: both cards and the
    comparison reach `api/status`, each check has its own logs, and neither password nor
    the GGA appears in the add-on log or the page's data."""

    def test_app_runs_two_reference_checks(self):
        from tests.test_app import SimulatedReceiver, free_port
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        sample = arp_frame(*CORS, antenna_height=0.0) + frame_1033() + rtcm(1074, 40)
        casters = [FakeCaster(data=sample, period=0.2, login=("user", SENTINEL)),
                   FakeCaster(data=sample, period=0.2, login=("user2", SENTINEL + "2"),
                              mountpoints={"NEAREST": {"nmea": "1"}})]
        ports = [caster.run_in_thread() for caster in casters]
        for caster in casters:
            self.addCleanup(caster.stop_thread)
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
            "min_free_gb": 0, "caster_enabled": False,
            "reference_enabled": True, "reference_host": "127.0.0.1",
            "reference_port": ports[0], "reference_mountpoint": "MP_1",
            "reference_user": "user", "reference_password": SENTINEL,
            "reference_frame": "ITRF2020",
            "reference2_enabled": True, "reference2_host": "127.0.0.1",
            "reference2_port": ports[1], "reference2_mountpoint": "NEAREST",
            "reference2_user": "user2", "reference2_password": SENTINEL + "2",
            "reference2_frame": "ITRF2020", "reference2_send_gga": True}))
        env = dict(os.environ, FAKE_RTKRCV_PERIOD="0.2", FAKE_RTKRCV_MODE="normal",
                   FAKE_RTKRCV_SOLUTION="0 0 0.01 1 25")
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
                # The second client waits for our 1005 (the GGA's position) first:
                # "no position", then a new look after 10 s.
                if (status["reference_comparison"]["shown"]
                        and status["reference2"]["client"]["state"] == "connected"
                        and status["reference2"]["state"] == "fixed"
                        and min(status[k]["session"]["epochs"]
                                for k in ("reference", "reference2")) >= 3):
                    break
        finally:
            process.terminate()
            app_output = process.communicate(timeout=15)[0].decode(errors="replace")
            receiver.stop()
        self.assertEqual(process.returncode, 0, app_output)
        one, two = status["reference"], status["reference2"]
        self.assertEqual((one["state"], two["state"]), ("fixed", "fixed"), (one, two))
        self.assertEqual((one["option"], two["option"]), ("reference", "reference2"))
        self.assertEqual(two["client"]["sourcetable"]["nmea"], "1")
        self.assertEqual(two["gga_source"], "our 1005")
        self.assertGreaterEqual(two["client"]["gga_sent"], 1)
        self.assertEqual(one["client"]["gga_sent"], 0)
        self.assertEqual(two["station_id"], 7)
        self.assertEqual([m["mountpoint"] for m in two["client"]["mountpoints"]], ["NEAREST"])
        self.assertTrue(two["client"]["mountpoints"][0]["accepted"])
        # The GGA carried our 1005 (synthetic, at 0/0) to the second caster only.
        self.assertTrue(casters[1].uplink)
        self.assertIn(b",0000.00000,N,00000.00000,E,1,", casters[1].uplink[0])
        self.assertEqual(casters[0].uplink, [])
        comparison = status["reference_comparison"]
        self.assertEqual((comparison["shown"], comparison["residual"], comparison["verdict"]),
                         (True, True, "good"))
        self.assertEqual(comparison["second_minus_first"], [0.0, 0.0, 0.0])
        for secret in (SENTINEL, "GPGGA", "0000.00000"):
            self.assertNotIn(secret, text, secret)
            self.assertNotIn(secret, app_output, secret)
        self.assertIn("NTRIP client (reference2): connected", app_output)
        logs = directory / "logs"
        (llh,) = (logs / "reference2").glob("*.llh")
        self.assertIn("RTK static against NEAREST", llh.read_text().splitlines()[0])
        self.assertIn("from reference2_frame", llh.read_text().splitlines()[0])
        (raw,) = (logs / "reference2" / "raw").glob("*.rtcm3")
        self.assertTrue(raw.read_bytes().startswith(sample))
        self.assertIn(f"reference2/raw/{raw.name}", [f["file"] for f in status["storage"]["files"]])
        self.assertTrue(list((logs / "reference").glob("*.llh")))
        self.assertTrue((directory / "reference2" / "rtkrcv.nav").exists(), "SIGTERM, cache saved")
        for path in [*logs.rglob("*"), *(directory / "reference2").rglob("*")]:
            if path.is_file() and path.suffix in (".llh", ".pos", ".conf", ".jsonl"):
                self.assertNotIn(SENTINEL, path.read_text(errors="replace"), path)


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
                     "Station frame", '"unknown: set "+o+"_frame"', "Our frame", "set base_frame",
                     "computed, station frame minus ours", "reserved by RTCM",
                     "CORS raw log", '<section class="wide"><h2>Reference check',
                     "never guessed from the stream", "Caster connection", "Sourcetable entry",
                     '"login refused":"Login refused by the caster"',
                     '"mountpoint not found":"Mountpoint not found"', "retry in",
                     "Session mean ± std (fixed)", "Mean ± std, 10 min", "Fixed share",
                     "rtkrcv CPU", "rtkrcv memory", "not applied; see the README",
                     "Previous session", "previous session's residual",
                     "password ${r.password_set?\"set\":\"not set\"}"):
            self.assertIn(text, html, text)
        card = html[html.index("function renderReference"):html.index("function render(s)")]
        self.assertNotIn("innerHTML", card, "values reach the card through dl, which escapes")
        self.assertIn("$(id+\"out\").textContent=out", card)
        self.assertIn("clientBusy||wrong?null:r.verdict", card,
                      "no residual colour while not connected, or on a wrong station")
        self.assertIn("r.station_mismatch", card)
        self.assertIn(':red?"bad":live&&wrong?"warn":', card,
                      "a red state (refused login, missing mountpoint, client stopped) "
                      "wins over the wrong station's amber")
        self.assertIn("live&&wrong&&!clientBusy?", card,
                      "no 'wrong station' in the headline while the client is not connected")
        cmp = html[html.index("function renderComparison"):html.index("async function tick")]
        for text in ("c.stale?", "c.station_mismatch?", "!c.station_mismatch&&!c.stale&&c.residual",
                     "first_station_id", "second_station_id", "first_last_fix_age_s",
                     "second_last_fix_age_s"):
            self.assertIn(text, cmp, text)
        states = html[html.index("const REF_STATES"):html.index("function renderReference")]
        for state in ("not configured", "connecting", "no corrections", "stale", "fixed",
                      "float", "single", "starting", "idle", "restarting", "not available",
                      "sourcetable", "login refused", "mountpoint not found",
                      "unsuitable mountpoint", "no data", "backoff"):
            self.assertIn(f"{state}:" if " " not in state else f'"{state}":', states, state)
        import re
        self.assertEqual(len(re.findall(r"<section[ >]", html)), html.count("</section>"))

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
                     '  reference_reset_utc: "00:00"\n',
                     "  base_frame: ITRF2020\n",
                     "  base_frame: list(ITRF2020|ITRF2014|ITRF2008|NAD83(2011))?\n",
                     '  base_epoch: ""\n', "  base_epoch: str\n",
                     "  reference_enabled: bool\n", "  reference_host: str\n",
                     "  reference_port: port\n", "  reference_mountpoint: str\n",
                     "  reference_user: str\n", "  reference_password: password\n",
                     "  reference_frame: list(NAD83(2011)|ITRF2020|ITRF2014|ITRF2008)?\n",
                     "  reference_reset_utc: match("):
            self.assertIn(line, text, line)
        self.assertNotIn("reference_expected_enu", text)
        self.assertIn('version: "0.7.0"', text)
        self.assertNotIn('  reference_frame: ""\n', text, "optional: absent until chosen")
        import re
        pattern = re.search(r"reference_reset_utc: match\((.*)\)\n", text).group(1)
        for good in ("", "7:05", "00:00", "23:59", "09:30"):
            self.assertTrue(re.fullmatch(pattern, good), good)
        for bad in ("24:00", "7:5", "12:60", "noon", "1:00:00"):
            self.assertFalse(re.fullmatch(pattern, bad), bad)
        self.assertEqual(__version__, "0.7.0")
        dockerfile = (ROOT / "Dockerfile").read_text()
        for text in ("FROM ubuntu:24.04 AS rtklib", "FROM ubuntu:24.04\n",
                     "RTKLIB_TAG=v2.5.1",
                     "RTKLIB_COMMIT=62d4677ed8425a4e2748c6d390b500d1afb493fc",
                     "rev-parse HEAD", "app/consapp/rtkrcv/gcc", "/usr/local/bin/rtkrcv-ex",
                     "rtklibexplorer/RTKLIB.git"):
            self.assertIn(text, dockerfile, text)
        self.assertNotIn(" rtklib ", dockerfile, "Ubuntu's rtklib package is not installed")
        self.assertNotIn("gfortran", dockerfile)
        self.assertFalse((ROOT / "build.yaml").exists(), "0.6.1: the base is in the Dockerfile")
        readme = (ROOT / "README.md").read_text()
        for text in ("reference_frame", "base_frame", "reference_reset_utc", "GPPNULLANTENNA",
                     "solution minus our", "rtkrcv-ex", "v2.5.1", "false fix",
                     "## Reference check", "DF021", "all bits should be set to zero",
                     "reference/raw/YYYY-MM-DD.rtcm3", "station's frame minus ours",
                     "Pearson & Snay", "no-store", "ntripclient", "Authorization",
                     "one connection at a time", "tests.test_ntripclient", "base_epoch",
                     "hide private mountpoints"):
            self.assertIn(text, readme, text)
        for stale in ("stays in rtkrcv's private config file", "each rtkrcv restart leaves"):
            self.assertNotIn(stale, readme, stale)
        self.assertNotIn("reference_expected_enu", readme.split("## Changes")[0],
                         "the option is gone; only the changelog may name it")
        self.assertNotIn(SENTINEL, readme)

    def test_shutdown_stops_both_instances_then_the_logs(self):
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        # 0.7.0: every reference check, then their logs.
        self.assertIn("x.stop() for x in (rtkrcv, *references)", source)
        self.assertIn("(solutions, *reference_logs)", source)
        self.assertIn("log, errors, solutions, *reference_logs,\n            raw_logs=[x for x in cors_logs if x is not None]", source)
        self.assertIn("cors_log.close()", source)


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
