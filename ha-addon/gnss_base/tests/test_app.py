"""Run from ha-addon/gnss_base with: python -m unittest tests.test_app"""
import asyncio
import base64
import contextlib
import gzip
import hashlib
import json
import math
import os
import re
import pty
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import unittest
import urllib.error
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from gnss_base import commands, control, profile, rawlog, receiver
from gnss_base import rtcm as rtcmdec
from gnss_base.__main__ import ErrorReporter, check_device, housekeeping, load_settings
from gnss_base.commands import (CommandLog, CommandRunner, PendingStore, PortClosed, checksum,
                                prepare)
from gnss_base.frames import FrameParser, crc24q, nmea_checksum, rtcm_type
from gnss_base.rawlog import GB, RawLog, compress, utc_day
from gnss_base.receiver import ChunkQueue, SerialReader
from gnss_base.status import (RECENTRE_SAMPLES, TRACK_POINTS, TRACK_WINDOW_S, LocalFrame,
                              Status, parse_coordinate)
from gnss_base.web import STATIC, create_app

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400


def strict_json(body):
    """Parse JSON, failing on NaN or infinity as a browser would."""
    def refuse(constant):
        raise ValueError(f"not strict JSON: {constant}")

    return json.loads(body, parse_constant=refuse)


def nmea(body):
    return f"${body}*{nmea_checksum(body.encode()):02X}\r\n".encode()


def rtcm(number, size=19):
    payload = bytes([number >> 4, (number & 15) << 4]) + bytes(size - 2)
    head = bytes([0xD3, size >> 8, size & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


# Fake positions: no real location in tests.
GGA = nmea("GNGGA,120000.00,0000.0000,N,00000.0000,E,1,32,0.39,0.0,M,0.0,M,,")
GSV = nmea("GPGSV,1,1,02,01,45,090,48,02,30,180,41,1")
SVIN = nmea("PQTMSVINSTATUS,1,1000,1,,,42,300,1.0,2.0,3.0,1.25")
GST = nmea("GNGST,120000.00,1.0,0.5,0.4,10.0,0.012,0.009,0.020")


def gga(lat, lon, quality=4, altitude=0.0, separation=0.0):
    """A GGA sentence for a synthetic position in decimal degrees."""
    def field(value, width):
        whole = int(abs(value))
        return f"{whole:0{width}d}{(abs(value) - whole) * 60:011.8f}"

    body = (f"GNGGA,120000.00,{field(lat, 2)},{'N' if lat >= 0 else 'S'},"
            f"{field(lon, 3)},{'E' if lon >= 0 else 'W'},{quality},32,0.39,"
            f"{altitude},M,{separation},M,,")
    return nmea(body)


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def housekeep_once(log):
    """Run one housekeeping pass. True if the loop was still alive after it."""
    async def one_pass():
        task = asyncio.create_task(housekeeping(log, ErrorReporter()))
        await asyncio.sleep(0.3)
        alive = not task.done()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return alive

    with mock.patch("gnss_base.__main__.HOUSEKEEPING_S", 3600):
        return asyncio.run(one_pass())


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)


class Frames(unittest.TestCase):
    def test_crc24q_known_value(self):
        self.assertEqual(crc24q(b"123456789"), 0xCDE703)

    def test_mixed_stream_split_across_reads(self):
        stream = GGA + rtcm(1005) + b"\x00junk" + rtcm(1077, 200) + SVIN
        parser = FrameParser()
        frames = []
        for index in range(0, len(stream), 7):
            frames += parser.feed(stream[index:index + 7])
        self.assertEqual([k for k, _ in frames], ["nmea", "rtcm", "rtcm", "nmea"])
        self.assertEqual(rtcm_type(frames[1][1]), 1005)
        self.assertEqual(rtcm_type(frames[2][1]), 1077)
        self.assertEqual(parser.unknown_bytes, 5)

    def test_corrupt_frames_rejected(self):
        bad_rtcm = bytearray(rtcm(1005))
        bad_rtcm[5] ^= 1
        parser = FrameParser()
        frames = parser.feed(GGA.replace(b"N", b"S", 1) + bytes(bad_rtcm) + GGA)
        self.assertEqual(len(frames), 1)
        self.assertGreaterEqual(parser.bad_frames, 2)

    def test_stray_start_byte_does_not_stall(self):
        parser = FrameParser()
        frames = parser.feed(b"$" + bytes(600) + GGA)
        self.assertEqual(len(frames), 1)

    # Review round 2, must-fix 6
    def test_rtcm_too_short_for_a_message_number_is_not_a_frame(self):
        empty = bytes.fromhex("d3000047ea4b")  # length 0, valid CRC
        one = bytes.fromhex("d30001000a188d")  # length 1, valid CRC
        parser = FrameParser()
        frames = parser.feed(empty + one + rtcm(1005))
        self.assertEqual([rtcm_type(f) for _, f in frames], [1005])
        self.assertEqual(parser.unknown_bytes, len(empty + one))
        self.assertEqual(parser.bad_frames, 0)

    # Review round 2, must-fix 7
    def test_nmea_needs_printable_body_and_two_hex_digits(self):
        def line(body, checksum=None):
            checksum = checksum or f"{nmea_checksum(body):02X}".encode()
            return b"$" + body + b"*" + checksum + b"\r\n"

        body = b"GPTXT,01,01,02,hi"
        three_digits = line(body, f"0{nmea_checksum(body):02X}".encode())
        with_tab = line(b"GPTXT,01,01,02,a\tb")
        with_dollar = line(b"GPTXT,01,01,02,a$b")
        for case in (three_digits, with_tab, with_dollar):
            parser = FrameParser()
            frames = parser.feed(case + GGA)
            self.assertEqual([k for k, _ in frames], ["nmea"], case)
            self.assertTrue(frames[0][1].startswith("GNGGA"), case)
        parser = FrameParser()
        parser.feed(three_digits + with_tab)
        self.assertEqual(parser.bad_frames, 0)

    # Review round 2, must-fix 7
    def test_rtcm_after_stray_dollar_found_without_waiting(self):
        parser = FrameParser()
        frames = parser.feed(b"$GP" + rtcm(1005))
        self.assertEqual([rtcm_type(f) for _, f in frames], [1005])
        self.assertEqual(parser.bad_frames, 0)


class Tracking(unittest.TestCase):
    def feed(self, status, data):
        for kind, payload in FrameParser().feed(data):
            status.add_frame(kind, payload)

    def test_status_fields_and_position(self):
        # Positions are shown on purpose now: the page is behind the HA login.
        status = Status()
        self.feed(status, GGA + GSV + SVIN + rtcm(1005))
        snap = status.snapshot()
        self.assertEqual(snap["fix"]["name"], "Standalone")
        self.assertEqual(snap["fix"]["satellites"], 32)
        self.assertEqual((snap["fix"]["lat"], snap["fix"]["lon"], snap["fix"]["height_m"]),
                         (0.0, 0.0, 0.0))
        self.assertIsNone(snap["fix"]["lat_sigma_m"])
        self.assertEqual(snap["signals"][0]["strongest"], 48)
        self.assertEqual(snap["signals"][0]["signal"], "1")
        survey = dict(snap["survey_in"])
        survey.pop("age_s")
        self.assertEqual(survey, {"state": "In progress", "valid": 1, "observations": 42,
                                  "target": 300, "accuracy_m": 1.25})
        self.assertEqual(snap["rtcm"]["1005"]["count"], 1)
        self.assertNotIn("points", json.dumps(snap), "the history belongs in api/track only")

    def test_gst_sigmas_join_the_fix_and_expire(self):
        clock = Clock()
        status = Status(clock=clock)
        self.feed(status, GGA + GST)
        fix = status.snapshot()["fix"]
        self.assertEqual((fix["lat_sigma_m"], fix["lon_sigma_m"], fix["height_sigma_m"]),
                         (0.012, 0.009, 0.020))
        clock.now += 6
        self.assertIsNone(status.snapshot()["fix"]["lat_sigma_m"])
        self.feed(status, nmea("GNGST,120000.00,,,,,,,"))
        self.assertIsNone(status.snapshot()["fix"]["height_sigma_m"])

    def test_gga_hemispheres_and_ellipsoidal_height(self):
        status = Status()
        self.feed(status, nmea(
            "GNGGA,120000.00,0030.0000,S,00100.0000,W,4,32,0.39,10.0,M,-2.5,M,,"))
        fix = status.snapshot()["fix"]
        self.assertEqual((fix["lat"], fix["lon"], fix["height_m"]), (-0.5, -1.0, 7.5))
        self.assertEqual(fix["name"], "RTK fixed")

    def test_gga_without_a_usable_position(self):
        status = Status()
        cases = [
            "GNGGA,120000.00,0030.0000,N,00100.0000,E,0,00,99.9,10.0,M,0.0,M,,",  # no fix
            "GNGGA,120000.00,,,,,1,04,9.9,,M,,M,,",                               # empty
            "GNGGA,120000.00,0030.0000,X,00100.0000,E,1,04,9.9,10.0,M,0.0,M,,",   # bad N/S
            "GNGGA,120000.00,0030.0000,N,00100.0000,E,1,04,9.9,,M,0.0,M,,",       # no altitude
        ]
        for body in cases:
            self.feed(status, nmea(body))
            fix = status.snapshot()["fix"]
            self.assertEqual((fix["lat"], fix["lon"], fix["height_m"]), (None, None, None), body)
        self.assertEqual(status.track()["points"], [])

    # Review 0.2.0, must-fix 7
    def test_negative_quality_is_no_fix(self):
        status = Status()
        self.feed(status, gga(0.5, 1.0, quality=-1))
        fix = status.snapshot()["fix"]
        self.assertEqual((fix["lat"], fix["lon"], fix["height_m"]), (None, None, None))
        self.assertEqual(fix["quality"], -1)
        self.assertEqual(status.track()["points"], [])

    # Review 0.2.1, must-fix 1
    def test_implausible_heights_are_no_position(self):
        status = Status()
        for altitude, separation in [(100_001.0, 0.0), (0.0, -100_001.0), (60_000.0, 60_000.0),
                                     (8e307, 8e307), (-8e307, 8e307)]:
            self.feed(status, gga(0.5, 1.0, altitude=altitude, separation=separation))
            fix = status.snapshot()["fix"]
            self.assertEqual((fix["lat"], fix["lon"], fix["height_m"]), (None, None, None),
                             (altitude, separation))
        self.assertEqual(status.track()["points"], [])
        self.feed(status, gga(0.5, 1.0, altitude=99_000.0, separation=-1_000.0))
        self.assertEqual(status.snapshot()["fix"]["height_m"], 98_000.0)

    def test_empty_geoid_separation_counts_as_zero(self):
        status = Status()
        self.feed(status, nmea("GNGGA,120000.00,0030.0000,N,00100.0000,E,1,32,0.39,10.0,M,,M,,"))
        self.assertEqual(status.snapshot()["fix"]["height_m"], 10.0)

    def test_values_age_out_when_data_stops(self):
        clock = Clock()
        status = Status(clock=clock)
        status.add_bytes(1000)
        self.feed(status, GGA)
        self.assertGreater(status.snapshot()["bytes_per_s"], 0)
        clock.now += 30
        snap = status.snapshot()
        self.assertEqual(snap["bytes_per_s"], 0)
        self.assertEqual(snap["fix"]["age_s"], 30)


class Coordinates(unittest.TestCase):
    def test_hemispheres(self):
        self.assertAlmostEqual(parse_coordinate("1230.0000", "N"), 12.5, places=12)
        self.assertAlmostEqual(parse_coordinate("1230.0000", "S"), -12.5, places=12)
        self.assertAlmostEqual(parse_coordinate("04515.0000", "E"), 45.25, places=12)
        self.assertAlmostEqual(parse_coordinate("12330.0000", "W"), -123.5, places=9)

    def test_edge_cases(self):
        self.assertEqual(parse_coordinate("0000.0000", "S"), 0.0)
        self.assertEqual(str(parse_coordinate("0000.0000", "W")), "0.0", "no negative zero")
        self.assertAlmostEqual(parse_coordinate("1230", "N"), 12.5, places=12)
        self.assertEqual(parse_coordinate("9000.0000", "N"), 90.0)
        self.assertEqual(parse_coordinate("18000.0000", "W"), -180.0)
        self.assertAlmostEqual(parse_coordinate("0059.9999999", "N"), 59.9999999 / 60, places=12)
        for value, hemisphere in [("", "N"), ("1230.038", ""), ("1230.038", "X"),
                                  ("1230.038", "n"), ("12.30", "N"), ("1260.0000", "N"),
                                  ("9000.0001", "S"), ("18000.0001", "E"), ("-1230.0", "N"),
                                  ("12a0.0", "N"), ("1230.0e1", "N"), ("1230.", "N"),
                                  (" 1230.0", "N")]:
            result = parse_coordinate(value, hemisphere)
            if value == "1230.":  # a trailing point is harmless
                self.assertAlmostEqual(result, 12.5)
            else:
                self.assertIsNone(result, (value, hemisphere))


class LocalCoordinates(unittest.TestCase):
    def test_known_offsets_at_the_equator(self):
        frame = LocalFrame(0.0, 0.0, 0.0)
        east, north, up = frame.to_enu(1e-5, 0.0, 0.0)
        # WGS84 meridian radius at the equator a(1 - e^2) = 6335439.327 m
        self.assertAlmostEqual(north, 1.105743, places=6)
        self.assertAlmostEqual(east, 0.0)
        east, north, up = frame.to_enu(0.0, 1e-5, 2.5)
        self.assertAlmostEqual(east, 1.113195, places=6)  # equatorial radius a = 6378137 m
        self.assertEqual(up, 2.5)

    def test_known_offsets_at_sixty_degrees(self):
        # One degree at 60 degrees latitude: 111.412 km north, 55.800 km east.
        frame = LocalFrame(60.0, 10.0, 100.0)
        east, north, up = frame.to_enu(60.0 + 1e-5, 10.0 + 1e-5, 99.0)
        self.assertAlmostEqual(north, 1.11412, places=5)
        self.assertAlmostEqual(east, 0.55800, places=5)
        self.assertAlmostEqual(up, -1.0)

    def test_round_trip_and_antimeridian(self):
        frame = LocalFrame(-0.5, 179.99999, 0.0)
        east, _, _ = frame.to_enu(-0.5, -179.99999, 0.0)
        self.assertAlmostEqual(east, 2.2264, places=3)
        lat, lon, height = frame.from_enu(3.0, -4.0, 0.25)
        back = frame.to_enu(lat, lon, height)
        for got, want in zip(back, (3.0, -4.0, 0.25)):
            self.assertAlmostEqual(got, want, places=6)  # micrometres


class History(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.status = Status(clock=self.clock)

    def feed(self, data):
        for kind, payload in FrameParser().feed(data):
            self.status.add_frame(kind, payload)

    def test_one_sample_per_second(self):
        for step in (0.0, 0.3, 0.6, 0.9):
            self.clock.now = 1_800_000_000.0 + step
            self.feed(GGA)
        self.assertEqual(len(self.status.track()["points"]), 1)
        self.clock.now += 0.2  # into the next second
        self.feed(GGA)
        self.assertEqual(len(self.status.track()["points"]), 2)

    def test_bounded_to_two_hours(self):
        for _ in range(TRACK_POINTS + 100):
            self.feed(GGA)
            self.clock.now += 1
        track = self.status.track()
        self.assertEqual(len(track["points"]), TRACK_POINTS)
        self.assertLessEqual(track["points"][0][0], track["window_s"])
        self.assertEqual(track["points"][-1][0], 1.0)

    def test_old_samples_age_out(self):
        self.feed(GGA)
        self.clock.now += 3 * 3600
        track = self.status.track()
        self.assertEqual((track["points"], track["reference"]), ([], None))

    def test_only_fixes_with_quality_above_zero(self):
        self.feed(gga(0.001, 0.001, quality=0))
        self.clock.now += 1
        self.feed(gga(0.001, 0.001, quality=5))
        points = self.status.track()["points"]
        self.assertEqual([p[4] for p in points], [5])

    def test_offsets_are_from_the_median(self):
        # Synthetic positions north of 0/0: 0, 1, 2, 3 cm and one 10 m outlier.
        step = 0.01 / LocalFrame(0, 0, 0).to_enu(1, 0, 0)[1]  # degrees per centimetre
        for index, extra in enumerate([0, 1, 2, 3, 1000]):
            self.feed(gga(extra * step, 0.0, altitude=float(index)))
            self.clock.now += 1
        track = self.status.track()
        self.assertEqual(track["fields"], ["age_s", "e", "n", "u", "quality"])
        self.assertAlmostEqual(track["reference"]["lat"], 2 * step, places=9)
        self.assertEqual(track["reference"]["lon"], 0.0)
        self.assertEqual(track["reference"]["height_m"], 2.0)
        self.assertEqual([p[2] for p in track["points"]], [-0.02, -0.01, 0.0, 0.01, 9.98])
        self.assertEqual([p[3] for p in track["points"]], [-2.0, -1.0, 0.0, 1.0, 2.0])
        self.assertEqual([p[0] for p in track["points"]], [5.0, 4.0, 3.0, 2.0, 1.0])

    def feed_seconds(self, count, lat, lon):
        for _ in range(count):
            self.feed(gga(lat, lon))
            self.clock.now += 1

    # Review 0.2.0, must-fix 1: only a sustained move restarts the history
    def test_a_far_move_restarts_the_history(self):
        self.feed_seconds(10, 0.0, 0.0)
        self.feed_seconds(RECENTRE_SAMPLES - 1, 0.0, 0.05)  # about 5.6 km east
        track = self.status.track()
        self.assertEqual(len(track["points"]), 10, "not yet: these could be outliers")
        self.assertEqual(track["reference"]["lon"], 0.0)
        self.feed_seconds(1, 0.0, 0.05)
        track = self.status.track()
        self.assertEqual(len(track["points"]), RECENTRE_SAMPLES, "the move's fixes are kept")
        self.assertAlmostEqual(track["reference"]["lon"], 0.05, places=9)
        self.assertEqual(track["points"][0][0], RECENTRE_SAMPLES)
        self.assertEqual(track["outliers_dropped"], 0)

    # Review 0.2.0, must-fix 1
    def test_a_single_far_fix_is_dropped_and_the_history_kept(self):
        self.feed_seconds(10, 0.0, 0.0)
        self.feed_seconds(1, 0.0, 0.05)
        self.feed_seconds(10, 0.0, 0.0)
        track = self.status.track()
        self.assertEqual(len(track["points"]), 20)
        self.assertEqual(track["reference"]["lon"], 0.0)
        self.assertEqual(max(abs(p[1]) for p in track["points"]), 0.0)
        self.assertEqual(track["outliers_dropped"], 1)

    # Review 0.2.0, must-fix 1
    def test_far_fixes_that_disagree_are_no_move(self):
        self.feed_seconds(10, 0.0, 0.0)
        for index in range(2 * RECENTRE_SAMPLES):
            self.feed_seconds(1, 0.0, 0.05 if index % 2 else -0.05)
        self.feed_seconds(1, 0.0, 0.0)
        track = self.status.track()
        self.assertEqual(len(track["points"]), 11)
        self.assertEqual(track["outliers_dropped"], 2 * RECENTRE_SAMPLES)

    def feed_offsets(self, centre, offsets):
        """One fix a second at east/north offsets in metres from centre (lat, lon)."""
        frame = LocalFrame(*centre, 0.0)
        for east, north in offsets:
            lat, lon, _ = frame.from_enu(east, north, 0.0)
            self.feed(gga(lat, lon))
            self.clock.now += 1

    # Review 0.2.1, suggestion 4
    def test_far_fixes_spread_over_two_km_are_no_move(self):
        self.feed_seconds(10, 0.0, 0.0)
        # All within 1 km of the first held fix, but about 2 km across.
        spread = [(0.0, 0.0)] + [(900 * math.cos(k * 2.4), 900 * math.sin(k * 2.4))
                                 for k in range(1, 2 * RECENTRE_SAMPLES)]
        self.feed_offsets((0.0, 0.05), spread)
        self.feed_seconds(1, 0.0, 0.0)
        track = self.status.track()
        self.assertEqual(len(track["points"]), 11)
        self.assertEqual(track["reference"]["lon"], 0.0)
        self.assertEqual(track["outliers_dropped"], len(spread))

    # Review 0.2.1, suggestion 4
    def test_a_tight_far_cluster_is_a_move(self):
        self.feed_seconds(10, 0.0, 0.0)
        cluster = [(20 * math.cos(k * 2.4), 20 * math.sin(k * 2.4))
                   for k in range(RECENTRE_SAMPLES)]
        self.feed_offsets((0.0, 0.05), cluster)
        track = self.status.track()
        self.assertEqual(len(track["points"]), RECENTRE_SAMPLES)
        self.assertAlmostEqual(track["reference"]["lon"], 0.05, places=3)
        self.assertEqual(track["outliers_dropped"], 0)

    # Review 0.2.1, must-fix 1: corrupt heights that get past the GGA checks
    def test_track_without_a_finite_reference_or_offset_is_well_formed(self):
        for heights in ([0.0, 1.6e308, 1.6e308, 1.6e308],          # median overflows
                        [0.0, -1.6e308, 1.6e308, 1.6e308, 1.6e308]):  # an offset overflows
            status = Status(clock=self.clock)
            for height in heights:
                status._add_track(self.clock.now, 0.0, 0.0, height, 4)
                self.clock.now += 1
            for since in (None, f"{status._history_id}:1"):
                track = status.track(since)
                self.assertEqual((track["reference"], track["reference_offset"],
                                  track["cursor"], track["points"], track["full"]),
                                 (None, None, None, [], True), heights)
                strict_json(json.dumps(track, allow_nan=False))

    # Review 0.2.1, suggestion 5
    def test_track_reports_the_oldest_age(self):
        self.assertIsNone(self.status.track()["oldest_age_s"])
        self.feed_seconds(5, 0.0, 0.0)
        cursor = self.status.track()["cursor"]
        self.feed_seconds(1, 0.0, 0.0)
        self.assertEqual(self.status.track()["oldest_age_s"], 6.0)
        self.assertEqual(self.status.track(cursor)["oldest_age_s"], 6.0)
        self.clock.now += TRACK_WINDOW_S - 3.5
        newer = self.status.track(cursor)
        self.assertEqual((newer["oldest_age_s"], len(newer["points"])), (TRACK_WINDOW_S - 0.5, 1))

    # Review 0.2.0, must-fix 3
    def test_history_uses_the_monotonic_clock(self):
        wall, monotonic = Clock(), Clock(5000.0)

        def tick():
            wall.now += 1
            monotonic.now += 1

        with mock.patch("time.time", wall), mock.patch("time.monotonic", monotonic):
            status = Status()
            for kind, payload in FrameParser().feed(gga(0.0, 0.0)):
                status.add_frame(kind, payload)
            tick()
            for kind, payload in FrameParser().feed(gga(0.0, 0.0)):
                status.add_frame(kind, payload)
            wall.now -= 3600  # the wall clock steps back an hour
            tick()
            for kind, payload in FrameParser().feed(gga(0.0, 0.0)):
                status.add_frame(kind, payload)
            tick()
            track = status.track()
            self.assertEqual([p[0] for p in track["points"]], [3.0, 2.0, 1.0])
            self.assertEqual(status.snapshot()["fix"]["age_s"], 1.0)
            wall.now -= 3 * 3600
            monotonic.now += TRACK_WINDOW_S - 1.5
            self.assertEqual([p[0] for p in status.track()["points"]], [TRACK_WINDOW_S - 0.5])
            monotonic.now += 1
            self.assertEqual(status.track()["points"], [])

    # Review 0.2.0, suggestion 4
    def test_since_returns_only_newer_points(self):
        self.feed_seconds(5, 0.0, 0.0)
        full = self.status.track()
        self.assertTrue(full["full"])
        self.assertEqual(len(full["points"]), 5)
        step = 0.01 / LocalFrame(0, 0, 0).to_enu(1, 0, 0)[1]  # degrees per centimetre
        for index in range(3):
            self.feed(gga((index + 1) * step, 0.0))
            self.clock.now += 1
        newer = self.status.track(full["cursor"])
        self.assertFalse(newer["full"])
        self.assertEqual([p[0] for p in newer["points"]], [3.0, 2.0, 1.0])
        self.assertNotEqual(newer["cursor"], full["cursor"])
        # Offsets from the fixed origin agree across calls although the median moved.
        everything = self.status.track()
        self.assertEqual(len(everything["points"]), 8)

        def from_origin(response, point):
            return [round(v + o, 4) for v, o in zip(point[1:4], response["reference_offset"])]

        self.assertEqual([from_origin(newer, p) for p in newer["points"]],
                         [from_origin(everything, p) for p in everything["points"][5:]])
        self.assertEqual(self.status.track(newer["cursor"])["points"], [])
        for stale in (None, "", "junk", "x:1", full["cursor"].split(":")[0] + ":999",
                      full["cursor"] + "0" * 3, full["cursor"].replace(":", ":-")):
            again = self.status.track(stale)
            self.assertTrue(again["full"], stale)
            self.assertEqual(len(again["points"]), 8, stale)
        # A restarted app, or a move, never continues an old cursor.
        restarted = Status(clock=self.clock)
        for kind, payload in FrameParser().feed(gga(0.0, 0.0)):
            restarted.add_frame(kind, payload)
        self.assertTrue(restarted.track(newer["cursor"])["full"])
        self.feed_seconds(RECENTRE_SAMPLES, 0.0, 0.05)
        moved = self.status.track(newer["cursor"])
        self.assertTrue(moved["full"])
        self.assertEqual(len(moved["points"]), RECENTRE_SAMPLES)


def serve(app, *paths):
    """GET each path from the app; return a list of (status, body bytes)."""
    async def fetch():
        async with TestClient(TestServer(app)) as client:
            results = []
            for path in paths:
                reply = await client.get(path)
                results.append((reply.status, await reply.read()))
            return results

    return asyncio.run(fetch())


class Api(unittest.TestCase):
    def test_track_endpoint_shape(self):
        clock = Clock()
        status = Status(clock=clock)
        for _ in range(3):
            for kind, payload in FrameParser().feed(GGA + GST):
                status.add_frame(kind, payload)
            clock.now += 1
        app = create_app(status.snapshot, (), get_track=status.track)
        (code, body), (scode, sbody) = serve(app, "/api/track", "/api/status")
        self.assertEqual((code, scode), (200, 200))
        track = json.loads(body)
        self.assertEqual(set(track), {"window_s", "max_points", "fields", "reference",
                                      "reference_offset", "cursor", "full", "outliers_dropped",
                                      "oldest_age_s", "points"})
        self.assertEqual(track["reference"], {"lat": 0.0, "lon": 0.0, "height_m": 0.0})
        self.assertEqual(track["points"], [[3.0, 0.0, 0.0, 0.0, 1], [2.0, 0.0, 0.0, 0.0, 1],
                                           [1.0, 0.0, 0.0, 0.0, 1]])
        fix = json.loads(sbody)["fix"]
        self.assertEqual((fix["lat"], fix["lon"], fix["height_m"]), (0.0, 0.0, 0.0))
        self.assertNotIn("points", sbody.decode())

    # Review 0.2.0, must-fix 2
    def test_non_finite_numbers_never_reach_the_json(self):
        clock = Clock()
        status = Status(clock=clock)
        sentences = [
            gga(0.5, 1.0),
            nmea("GNGGA,120000.00,0030.0000,N,00100.0000,E,4,32,nan,nan,M,0.0,M,,"),
            nmea("GNGGA,120000.00,0030.0000,N,00100.0000,E,4,32,inf,1.0,M,inf,M,,"),
            nmea("GNGGA,120000.00,0030.0000,N,00100.0000,E,4,32,-inf,-inf,M,1.0,M,,"),
            nmea("GNGGA,120000.00,0030.0000,N,00100.0000,E,4,32,NaN,1e308,M,1e308,M,,"),
            nmea("GNGST,120000.00,1.0,0.5,0.4,10.0,nan,inf,-inf"),
            nmea("PQTMSVINSTATUS,1,1000,1,,,42,300,1.0,2.0,3.0,nan"),
        ]
        for sentence in sentences:
            for kind, payload in FrameParser().feed(sentence):
                status.add_frame(kind, payload)
            clock.now += 1
        app = create_app(status.snapshot, (), get_track=status.track)

        def strict(constant):
            raise ValueError(f"not strict JSON: {constant}")

        results = serve(app, "/api/status", "/api/track")
        self.assertEqual([code for code, _ in results], [200, 200])
        snap, track = (json.loads(body, parse_constant=strict) for _, body in results)
        fix = snap["fix"]
        self.assertEqual((fix["lat"], fix["lon"], fix["height_m"], fix["hdop"]),
                         (None, None, None, None))
        self.assertEqual((fix["lat_sigma_m"], fix["lon_sigma_m"], fix["height_sigma_m"]),
                         (None, None, None))
        self.assertIsNone(snap["survey_in"]["accuracy_m"])
        self.assertEqual(len(track["points"]), 1)

    # Review 0.2.0, suggestion 4
    def test_track_since_over_http(self):
        clock = Clock()
        status = Status(clock=clock)
        for _ in range(3):
            for kind, payload in FrameParser().feed(GGA):
                status.add_frame(kind, payload)
            clock.now += 1
        app = create_app(status.snapshot, (), get_track=status.track)
        cursor = status.track()["cursor"]
        (_, full), (_, newer), (_, bad) = serve(
            app, "/api/track", "/api/track?since=" + cursor, "/api/track?since=%00%ff")
        full, newer, bad = json.loads(full), json.loads(newer), json.loads(bad)
        self.assertEqual((full["full"], len(full["points"])), (True, 3))
        self.assertEqual((newer["full"], newer["points"]), (False, []))
        self.assertEqual((bad["full"], len(bad["points"])), (True, 3))

    # Review 0.2.1, must-fix 1
    def test_huge_finite_heights_never_break_the_track(self):
        clock = Clock()
        status = Status(clock=clock)
        for _ in range(2):
            for kind, payload in FrameParser().feed(gga(0.5, 1.0, altitude=8e307,
                                                        separation=8e307)):
                status.add_frame(kind, payload)
            clock.now += 1
        guarded = Status(clock=clock)  # corrupt heights stored despite the GGA checks
        for height in (0.0, 1.6e308, 1.6e308, 1.6e308):
            guarded._add_track(clock.now, 0.0, 0.0, height, 4)
            clock.now += 1
        results = serve(create_app(status.snapshot, (), get_track=status.track),
                        "/api/status", "/api/track")
        results += serve(create_app(guarded.snapshot, (), get_track=guarded.track),
                         "/api/track", "/api/track?since=x:1")
        self.assertEqual([code for code, _ in results], [200] * 4)
        snap, *tracks = (strict_json(body) for _, body in results)
        self.assertIsNone(snap["fix"]["height_m"])
        for track in tracks:
            self.assertEqual((track["reference"], track["points"]), (None, []))

    # Review 0.2.1, suggestion 3
    def test_over_long_cursor_is_a_full_response(self):
        clock = Clock()
        status = Status(clock=clock)
        for _ in range(3):
            for kind, payload in FrameParser().feed(GGA):
                status.add_frame(kind, payload)
            clock.now += 1
        history = status.track()["cursor"].split(":")[0]
        app = create_app(status.snapshot, (), get_track=status.track)
        results = serve(app, f"/api/track?since={history}:{'9' * 5000}",
                        f"/api/track?since={history}:{'0' * 21}1",
                        f"/api/track?since={history}:{'0' * 19}1")
        self.assertEqual([code for code, _ in results], [200] * 3)
        long, padded, short = (strict_json(body) for _, body in results)
        self.assertEqual((long["full"], len(long["points"])), (True, 3))
        self.assertEqual((padded["full"], len(padded["points"])), (True, 3))
        self.assertEqual((short["full"], len(short["points"])), (False, 2))

    def test_track_and_static_files_need_ingress(self):
        app = create_app(lambda: {}, get_track=lambda since=None: {})
        results = serve(app, "/api/track", "/vendor/leaflet/leaflet.js", "/track.js")
        self.assertEqual([code for code, _ in results], [403, 403, 403])

    def test_track_script_is_served(self):
        app = create_app(lambda: {}, ())
        ((code, body),) = serve(app, "/track.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, (STATIC / "track.js").read_bytes())

    def test_vendored_leaflet_is_served(self):
        app = create_app(lambda: {}, ())
        (code, body), (css, _) = serve(app, "/vendor/leaflet/leaflet.js",
                                       "/vendor/leaflet/leaflet.css")
        self.assertEqual((code, css), (200, 200))
        self.assertIn(b"Leaflet 1.9.4", body[:200])

    # Review 0.2.0, suggestion 8: the paths reach the server exactly as written
    def test_vendor_paths_cannot_leave_the_vendor_folder(self):
        paths = ["/vendor/%2e%2e/web.py", "/vendor/%2e%2e/%2e%2e/web.py",
                 "/vendor/leaflet/%2e%2e/%2e%2e/web.py",
                 "/vendor/leaflet/%2e%2e/%2e%2e/%2e%2e/web.py",
                 "/vendor/..%2fweb.py", "/vendor/..%2f..%2fweb.py",
                 "/vendor/leaflet/..%2f..%2f..%2fweb.py", "/vendor/%2e%2e%2f%2e%2e%2fweb.py",
                 "/vendor/..%5c..%5cweb.py", "/vendor/%2e%2e/index.html",
                 "/vendor/leaflet/%2e%2e/%2e%2e/index.html", "/vendor/..%2findex.html"]
        seen = []

        @web.middleware
        async def record(request, handler):
            seen.append(request.raw_path)
            return await handler(request)

        app = create_app(lambda: {}, ())
        app.middlewares.append(record)
        web_source = (ROOT / "gnss_base" / "web.py").read_bytes()
        page = (STATIC / "index.html").read_bytes()

        async def fetch():
            async with TestClient(TestServer(app)) as client:
                root = str(client.make_url("/")).rstrip("/")
                results = []
                for path in paths:
                    async with client.session.get(URL(root + path, encoded=True)) as reply:
                        results.append((reply.status, await reply.read()))
                return results

        results = asyncio.run(fetch())
        self.assertEqual(seen, paths, "the client must not normalise the paths")
        for path, (code, body) in zip(paths, results):
            self.assertTrue(400 <= code < 500, (path, code))
            self.assertNotIn(b"def create_app", body, path)
            self.assertNotIn(web_source[:200], body, path)
            self.assertNotIn(page[:200], body, path)


class Page(unittest.TestCase):
    ALLOWED_URLS = {"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                    "https://www.openstreetmap.org/copyright"}

    def setUp(self):
        self.html = (STATIC / "index.html").read_text()

    def test_leaflet_loaded_by_relative_paths(self):
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.found = []

            def handle_starttag(self, tag, attrs):
                self.found += [value for name, value in attrs if name in ("src", "href")]

        parser = Links()
        parser.feed(self.html)
        self.assertIn("vendor/leaflet/leaflet.js", parser.found)
        self.assertIn("vendor/leaflet/leaflet.css", parser.found)
        for link in parser.found:
            self.assertNotRegex(link, r"^(/|[a-z]+:)", "page links must be relative for Ingress")
            self.assertTrue((STATIC / link).is_file(), link)
        for url in re.findall(r"""fetch\(["']([^"']+)""", self.html):
            self.assertFalse(url.startswith("/"), url)

    def test_only_absolute_urls_are_osm_tiles_and_attribution(self):
        self.assertEqual(set(re.findall(r"""(?:https?:)?//[^\s"'<>)]+""", self.html)),
                         self.ALLOWED_URLS)
        self.assertIn("OpenStreetMap</a> contributors", self.html)

    # Review 0.2.1, suggestions 5 and 6
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_track_merging_under_node(self):
        self.assertIn('<script src="track.js"></script>', self.html)
        self.assertNotIn("function mergeTrack", self.html, "one copy, in track.js")
        result = subprocess.run(["node", str(ROOT / "tests" / "track_test.js")],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("\nok ") + result.stdout.startswith("ok "), 6,
                         result.stdout)

    def test_receiver_script_is_served_and_uses_relative_urls(self):
        script = (STATIC / "control.js").read_text()
        self.assertIn('<script src="control.js"></script>', self.html)
        urls = re.findall(r"""(?:fetch|post)\(["']([^"']+)""", script)
        self.assertEqual(set(urls), {"api/commands", "api/command", "api/command/confirm",
                                     "api/profile/check", "api/profile/apply",
                                     "api/steps/base-mode", "api/steps/survey-in",
                                     "api/steps/fixed"})
        self.assertIsNone(re.search(r"(?:https?:)?//", script.replace("// ", "")))
        # Every value put into innerHTML goes through esc.
        for template in re.findall(r"innerHTML\s*=([^;]+)", script):
            for value in re.findall(r"\$\{([^}]*)\}", template):
                self.assertTrue(value.startswith("esc(") or value.startswith("x.note ?")
                                or value.startswith("STATUS_CLASS"), value)
        ((code, body),) = serve(create_app(lambda: {}, ()), "/control.js")
        self.assertEqual((code, body), (200, script.encode()))

    # Stage 2 review round 1, fix 5
    def test_confirmation_starts_on_cancel_and_send_waits_a_second(self):
        script = (STATIC / "control.js").read_text()
        opener = script[script.index("function openConfirm"):script.index("function closeConfirm")]
        self.assertIn('$("cno").focus()', opener)
        self.assertNotIn('$("cyes").focus()', script)
        self.assertIn('$("cyes").disabled = true', opener)
        self.assertIn("const ARM_MS = 1000;", script)
        send = script[script.index('$("cyes").onclick'):]
        self.assertIn("performance.now() < pending.armed", send.split("\n", 2)[1])

    def test_vendored_files_match_the_notice(self):
        folder = STATIC / "vendor" / "leaflet"
        notice = (folder / "NOTICE").read_text()
        self.assertIn("1.9.4", notice)
        self.assertIn("BSD 2-Clause", notice)
        self.assertIn("BSD 2-Clause", (folder / "LICENSE").read_text())
        for name in ("leaflet.js", "leaflet.css"):
            digest = hashlib.sha512((folder / name).read_bytes()).digest()
            self.assertIn("sha512-" + base64.b64encode(digest).decode(), notice, name)


class Logging(TempDir):
    def log(self, clock, free=lambda: 100 * GB, min_free_gb=0):
        log = RawLog(self.dir, min_free_gb, clock=clock, disk_free=free)
        self.addCleanup(log.close)
        return log

    def test_day_rollover_and_compression(self):
        clock = Clock()
        log = self.log(clock)
        first = utc_day(clock.now)
        log.write(b"day one")
        self.assertEqual(log.pending_days(), [])
        clock.now += DAY
        log.write(b"day two")
        pending = log.pending_days()
        self.assertEqual([p.stem for p in pending], [first])
        log.close()
        result = compress(pending[0], disk_free=lambda: 100 * GB)
        self.assertEqual(gzip.open(result).read(), b"day one")
        self.assertFalse(pending[0].exists())

    def test_restart_does_not_compress_today(self):
        clock = Clock()
        log = self.log(clock)
        log.write(b"before restart")
        log.close()
        restarted = self.log(clock)
        self.assertEqual(restarted.pending_days(), [])
        restarted.write(b", after")
        restarted.close()
        self.assertEqual(log.path_for(utc_day(clock.now)).read_bytes(), b"before restart, after")

    def test_compress_never_overwrites(self):
        path = self.dir / "2026-01-01.raw"
        path.write_bytes(b"first")
        one = compress(path, disk_free=lambda: 100 * GB)
        path.write_bytes(b"second")
        two = compress(path, disk_free=lambda: 100 * GB)
        self.assertNotEqual(one, two)
        self.assertEqual(gzip.open(one).read(), b"first")
        self.assertEqual(gzip.open(two).read(), b"second")

    def test_pause_at_limit_counts_dropped_then_resumes(self):
        clock = Clock()
        free = {"bytes": 5 * GB}
        log = self.log(clock, free=lambda: free["bytes"], min_free_gb=1)
        log.write(b"kept")
        free["bytes"] = 1024
        clock.now += 61
        log.write(b"dropped")
        self.assertTrue(log.paused)
        self.assertEqual(log.dropped_bytes, 7)
        free["bytes"] = 5 * GB
        clock.now += 61
        log.write(b"again")
        log.close()
        self.assertEqual(log.path_for(utc_day(clock.now)).read_bytes(), b"keptagain")

    def test_usage_file_once_an_hour(self):
        clock = Clock()
        log = self.log(clock)
        log.record_usage()
        log.record_usage()
        clock.now += 3601
        log.record_usage()
        self.assertEqual(len(log.usage_path.read_text().splitlines()), 3)

    # Review round 1, finding 1. The file is now unbuffered (round 2), so the
    # failure comes from write rather than flush.
    def test_failed_write_does_not_jam_logging(self):
        clock = Clock()
        log = self.log(clock)
        log.write(b"a")
        real = log._file
        broken = mock.Mock(wraps=real)
        broken.write.side_effect = OSError(28, "No space left on device")

        def close_then_fail():
            # Release the real handle (as CPython does even when close's flush
            # fails), but still report the error to the log.
            real.close()
            raise OSError(28, "No space left on device")

        broken.close.side_effect = close_then_fail
        log._file = broken
        clock.now += 6
        log.write(b"b")
        self.assertIn("No space", log.write_error)
        self.assertIsNone(log._file)
        self.assertEqual(log.dropped_bytes, 1)
        log.write(b"c")
        # Review round 2, must-fix 5: the failed close stays visible until a
        # successful write that follows a successful close.
        self.assertIn("closing", log.write_error)
        log.close()
        log.write(b"d")
        self.assertEqual(log.write_error, "")
        log.close()
        self.assertEqual(log.path_for(utc_day(clock.now)).read_bytes(), b"acd")

    # Review round 1, finding 2
    def test_housekeeping_error_survives_successful_writes(self):
        log = self.log(Clock())
        log.compress_failed(self.dir / "2026-01-01.raw", OSError("disk"))
        log.write(b"fine")
        self.assertIn("compress 2026-01-01.raw", log.snapshot()["last_error"])
        self.assertTrue(log.has_compress_failures())

    # Review round 1, finding 3
    def test_finished_day_compresses_when_data_stops(self):
        clock = Clock()
        log = self.log(clock)
        log.write(b"last bytes before midnight")
        clock.now += DAY
        self.assertEqual(log.pending_days(), [])
        log.tidy()
        self.assertEqual(len(log.pending_days()), 1)

    def test_finished_day_compresses_while_paused(self):
        clock = Clock()
        free = {"bytes": 5 * GB}
        log = self.log(clock, free=lambda: free["bytes"], min_free_gb=1)
        log.write(b"data")
        free["bytes"] = 1024
        clock.now += DAY
        log.write(b"more")
        self.assertTrue(log.paused)
        self.assertEqual(len(log.pending_days()), 1)

    # Review round 1, findings 4 and 5
    def test_compress_refuses_without_room_and_cleans_up(self):
        path = self.dir / "2026-01-01.raw"
        path.write_bytes(os.urandom(3 * 1024 * 1024))
        with self.assertRaises(OSError):
            compress(path, disk_free=lambda: 1024)
        self.assertTrue(path.exists())
        calls = {"n": 0}

        def shrinking():
            calls["n"] += 1
            return 100 * GB if calls["n"] < 3 else 0

        with self.assertRaises(OSError):
            compress(path, disk_free=shrinking)
        self.assertTrue(path.exists())
        self.assertEqual(list(self.dir.glob("*.gz*")), [])

    def test_compress_verifies_and_syncs(self):
        path = self.dir / "2026-01-01.raw"
        data = os.urandom(2 * 1024 * 1024)
        path.write_bytes(data)
        with mock.patch("gnss_base.rawlog.os.fsync", wraps=os.fsync) as fsync:
            result = compress(path, disk_free=lambda: 100 * GB)
        self.assertGreaterEqual(fsync.call_count, 3)
        self.assertEqual(gzip.open(result).read(), data)
        with mock.patch("gnss_base.rawlog._gzip_size", return_value=999):
            path.write_bytes(b"x")
            with self.assertRaises(OSError):
                compress(path, disk_free=lambda: 100 * GB)
        self.assertTrue(path.exists())

    def test_failed_compress_waits_before_retry(self):
        clock = Clock()
        log = self.log(clock)
        old = self.dir / "raw" / "2026-01-01.raw"
        old.write_bytes(b"x")
        log.compress_failed(old, OSError("busy"))
        self.assertEqual(log.pending_days(), [])
        clock.now += 3601
        self.assertEqual(log.pending_days(), [old])

    # Review round 1, findings 11 and 12
    def test_disk_check_failure_is_contained_and_refreshed_when_idle(self):
        clock = Clock()
        free = {"value": 100 * GB}

        def disk():
            if isinstance(free["value"], Exception):
                raise free["value"]
            return free["value"]

        log = self.log(clock, free=disk)
        free["value"] = OSError("gone")
        clock.now += 61
        log.write(b"still logged")
        self.assertIn("disk check", log.snapshot()["last_error"])
        free["value"] = 42 * GB
        log.tidy()
        self.assertEqual(log.free_bytes, 42 * GB)

    # Review round 2, must-fix 2
    def test_disk_check_error_survives_housekeeping_and_keeps_state(self):
        clock = Clock()
        free = {"value": 512 * 1024 ** 2}

        def disk():
            if isinstance(free["value"], Exception):
                raise free["value"]
            return free["value"]

        log = self.log(clock, free=disk, min_free_gb=1)
        self.assertTrue(log.paused)
        free["value"] = OSError("statvfs failed")
        with self.assertLogs("gnss_base", "ERROR") as logged:
            self.assertTrue(housekeep_once(log))
        self.assertIn("disk check: statvfs failed", log.snapshot()["last_error"])
        self.assertIn("disk check", "\n".join(logged.output))
        self.assertTrue(log.paused)
        self.assertEqual(log.free_bytes, 512 * 1024 ** 2)
        free["value"] = 5 * GB
        housekeep_once(log)
        self.assertEqual(log.snapshot()["last_error"], "")
        self.assertFalse(log.paused)

    # Review round 2, must-fix 3
    def test_housekeeping_survives_unexpected_errors(self):
        log = self.log(Clock())
        old = self.dir / "raw" / "2026-01-01.raw"
        old.write_bytes(b"x")
        with mock.patch("gnss_base.__main__.compress", side_effect=ValueError("boom")), \
                self.assertLogs("gnss_base", "ERROR"):
            self.assertTrue(housekeep_once(log))
        self.assertIn("boom", log.housekeeping_error)
        self.assertTrue(old.exists())
        with mock.patch.object(log, "tidy", side_effect=RuntimeError("odd")), \
                self.assertLogs("gnss_base", "ERROR"):
            self.assertTrue(housekeep_once(log))
        self.assertIn("housekeeping: odd", log.housekeeping_error)

    # Review round 2, must-fix 3
    def test_corrupt_read_back_is_an_oserror_and_cleans_up(self):
        path = self.dir / "2026-01-01.raw"
        path.write_bytes(b"data")
        for error in (EOFError("truncated"), zlib.error("bad data")):
            with mock.patch("gnss_base.rawlog._gzip_size", side_effect=error):
                with self.assertRaisesRegex(OSError, "verification failed"):
                    compress(path, disk_free=lambda: 100 * GB)
            self.assertEqual(path.read_bytes(), b"data")
            self.assertEqual(list(self.dir.glob("*.gz*")), [])

    # Review round 2, must-fix 4
    def test_clock_step_back_does_not_compress_later_days(self):
        clock = Clock()
        log = self.log(clock)
        log.write(b"today")
        clock.now += DAY
        log.write(b"tomorrow")
        clock.now -= DAY
        log.tidy()  # closes the "future" file, as it is no longer today
        self.assertEqual(log.pending_days(), [])

    # Review round 2, must-fix 4; since 0.2.2 the late bytes are kept (0.2.1, must-fix 2)
    def test_write_never_reopens_a_day_being_compressed(self):
        clock = Clock()
        log = self.log(clock)
        log.write(b"old day")
        yesterday = log.path_for(utc_day(clock.now))
        clock.now += DAY
        log.write(b"new day")
        log.start_compress(yesterday)
        clock.now -= DAY  # the clock steps back while yesterday is compressed
        log.write(b"late")
        self.assertEqual(yesterday.read_bytes(), b"old day")
        log.end_compress(yesterday)
        log.write(b"!")
        self.assertEqual(yesterday.read_bytes(), b"old day")
        self.assertEqual(log.dropped_bytes, 0)

    # Review 0.2.1, must-fix 2
    def test_bytes_for_a_day_being_compressed_go_to_a_late_file(self):
        clock = Clock(1_793_491_200.0 + 3600)  # 2026-11-01 01:00 UTC
        log = self.log(clock)
        log.write(b"old day")
        day = utc_day(clock.now)
        self.assertEqual(day, "2026-11-01")
        yesterday = log.path_for(day)
        clock.now += DAY
        log.write(b"new day")
        log.tidy()
        self.assertEqual(log.pending_days(), [yesterday])
        self.assertTrue(log.start_compress(yesterday))
        clock.now -= DAY  # the clock steps back while yesterday is compressed
        log.write(b"late ")
        late = self.dir / "raw" / "2026-11-01-late-1.raw"
        self.assertEqual(late.read_bytes(), b"late ")
        self.assertEqual(yesterday.read_bytes(), b"old day")
        self.assertEqual((log.dropped_bytes, log.write_error), (0, ""))
        compressed = compress(yesterday, disk_free=lambda: 100 * GB)
        log.compress_done(yesterday)
        log.end_compress(yesterday)
        log.write(b"bytes")
        log.tidy()
        # Still 2026-11-01 by the clock: neither file is finished.
        self.assertEqual(log.pending_days(), [])
        self.assertFalse(log.start_compress(late))
        self.assertLess("2026-11-01-late-1", "2026-11-02")
        self.assertGreaterEqual("2026-11-01-late-1", "2026-11-01")
        clock.now += DAY
        log.write(b" and today")
        self.assertEqual(log.pending_days(), [late])
        self.assertTrue(log.start_compress(late))
        # A .gz of the same name already there is never overwritten.
        (self.dir / "raw" / "2026-11-01-late-1.raw.gz").write_bytes(b"earlier")
        late_gz = compress(late, disk_free=lambda: 100 * GB)
        log.end_compress(late)
        self.assertEqual(gzip.open(compressed).read(), b"old day")
        self.assertEqual(gzip.open(late_gz).read(), b"late bytes")
        self.assertEqual((self.dir / "raw" / "2026-11-01-late-1.raw.gz").read_bytes(), b"earlier")
        log.close()
        self.assertEqual(log.path_for("2026-11-02").read_bytes(), b"new day and today")
        self.assertEqual(log.dropped_bytes, 0)
        self.assertEqual(log.pending_days(), [])

    # Review round 2, must-fix 4
    def test_compress_keeps_a_source_that_changed(self):
        path = self.dir / "2026-01-01.raw"
        path.write_bytes(b"data")
        real = rawlog._gzip_size

        def then_append(partial):
            size = real(partial)
            with open(path, "ab") as stream:
                stream.write(b" and more")
            return size

        with mock.patch("gnss_base.rawlog._gzip_size", then_append):
            with self.assertRaisesRegex(OSError, "changed"):
                compress(path, disk_free=lambda: 100 * GB)
        self.assertEqual(path.read_bytes(), b"data and more")
        self.assertEqual(list(self.dir.glob("*.gz*")), [])

    # Review round 2, must-fix 5
    def test_failed_close_at_rollover_stays_visible_and_delays_compression(self):
        class FailingClose:
            def __init__(self, real):
                self.real = real

            def write(self, data):
                return self.real.write(data)

            @property
            def closed(self):
                return self.real.closed

            def close(self):
                self.real.close()
                raise OSError(5, "Input/output error")

        clock = Clock()
        log = self.log(clock)
        log.write(b"day one")
        first = log.path_for(utc_day(clock.now))
        log._file = FailingClose(log._file)
        clock.now += DAY
        log.write(b"day two")
        log.write(b"more")
        self.assertIn("closing", log.write_error)
        self.assertIn("Input/output error", log.snapshot()["last_error"])
        self.assertEqual(log.pending_days(), [])
        log.tidy()  # the next housekeeping pass confirms the file is closed
        self.assertEqual(log.pending_days(), [first])
        log.close()  # a successful close, then a successful write, clears it
        log.write(b"!")
        self.assertEqual(log.write_error, "")

    # Review round 2, suggestion
    def test_failure_for_a_vanished_file_is_forgotten(self):
        log = self.log(Clock())
        log.compress_failed(self.dir / "raw" / "2026-01-01.raw", OSError("busy"))
        log.pending_days()
        self.assertFalse(log.has_compress_failures())

    # Review round 1, finding 13
    def test_vanished_file_does_not_break_status(self):
        log = self.log(Clock())
        log.write(b"x")
        real_stat = Path.stat

        def flaky(path, *args, **kwargs):
            if path.name.endswith(".raw") and path.parent.name == "raw":
                raise FileNotFoundError(path)
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", flaky):
            snapshot = log.snapshot()
        self.assertEqual(snapshot["files"], [])

    # Review round 3, suggestion: the clock steps back after pending_days()
    def test_start_compress_refuses_a_day_reopened_after_listing(self):
        clock = Clock()
        log = self.log(clock)
        first_day = clock.now
        log.write(b"day one")
        clock.now += DAY
        log.write(b"day two")
        log.tidy()
        pending = log.pending_days()
        self.assertEqual(len(pending), 1)
        clock.now = first_day  # clock steps back: day one is today again
        log.write(b" more")
        self.assertFalse(log.start_compress(pending[0]))
        clock.now = first_day + 2 * DAY
        log.tidy()
        self.assertTrue(log.start_compress(pending[0]))
        log.end_compress(pending[0])


class DeviceGuard(TempDir):
    """Review round 1, finding 6: resolve the real device, never trust text."""

    def make(self, name, target):
        by_id = self.dir / "by-id"
        by_id.mkdir(exist_ok=True)
        (by_id / name).symlink_to(target)
        return str(by_id)

    def setUp(self):
        super().setUp()
        self.acm = self.dir / "ttyACM0"
        self.zigbee = self.dir / "ttyUSB0"
        self.acm.touch()
        self.zigbee.touch()
        self.by_id = self.make("usb-1a86_USB_Dual_Serial_123-if00", self.acm)
        self.make("usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_456-if00-port0", self.zigbee)

    def check(self, device, match="USB_Dual_Serial"):
        return check_device({"device": str(device), "device_match": match}, self.by_id)

    def test_plain_tty_name_of_receiver_accepted(self):
        self.assertEqual(self.check(self.acm), "")

    def test_by_id_path_accepted(self):
        self.assertEqual(self.check(Path(self.by_id) / "usb-1a86_USB_Dual_Serial_123-if00"), "")

    def test_zigbee_refused_even_with_misleading_link(self):
        misleading = self.dir / "USB_Dual_Serial_lookalike"
        misleading.symlink_to(self.zigbee)
        self.assertIn("not opened", self.check(self.zigbee))
        self.assertIn("not opened", self.check(misleading))

    def test_empty_match_refused(self):
        self.assertIn("empty", self.check(self.acm, match=""))

    def test_missing_device_and_none_selected(self):
        self.assertIn("does not exist", self.check(self.dir / "ttyACM9"))
        self.assertIn("No device", check_device({"device": "", "device_match": "x"}, self.by_id))


class Settings(unittest.TestCase):
    """Review round 1, finding 16: arguments take priority over options."""

    def test_arguments_override_options(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as stream:
            json.dump({"device_match": "from_options", "min_free_gb": 10}, stream)
        self.addCleanup(os.unlink, stream.name)
        settings = load_settings(["--options", stream.name, "--device-match", "cli",
                                  "--min-free-gb", "2.5"])
        self.assertEqual(settings["device_match"], "cli")
        self.assertEqual(settings["min_free_gb"], 2.5)


class Reader(unittest.TestCase):
    # Review round 1, finding 7
    def test_reader_survives_non_oserror_and_retries(self):
        states = []
        attempts = {"n": 0}

        def failing(*_args, **_kwargs):
            attempts["n"] += 1
            raise termios.error(5, "Input/output error")

        with mock.patch.object(receiver.serial, "Serial", failing), \
                mock.patch.object(receiver, "RETRY_S", 0.01):
            reader = SerialReader("/dev/null", 460800, lambda d: None,
                                  lambda c, e: states.append((c, e)), check=lambda: "")
            reader.start()
            time.sleep(0.2)
            reader.stop()
        self.assertGreater(attempts["n"], 1)
        self.assertTrue(states and states[0][0] is False and "error" in states[0][1])

    def run_reader(self, check):
        states, opened = [], []

        def fake_serial(*_args, **_kwargs):
            opened.append(True)
            raise OSError("no such port")

        with mock.patch.object(receiver.serial, "Serial", fake_serial), \
                mock.patch.object(receiver, "RETRY_S", 0.01):
            reader = SerialReader("/dev/null", 460800, lambda d: None,
                                  lambda c, e: states.append((c, e)), check=check)
            reader.start()
            time.sleep(0.2)
            reader.stop()
        return states, opened

    # Review round 2, must-fix 1
    def test_refused_device_is_refused_on_every_retry(self):
        calls = []

        def refuse():
            calls.append(True)
            return "Device /dev/ttyUSB0 (zigbee) does not match 'x', so it was not opened."

        states, opened = self.run_reader(refuse)
        self.assertEqual(opened, [])
        self.assertGreater(len(calls), 1)
        self.assertTrue(states and all(s == (False, refuse()) for s in states))

    # Review round 2, must-fix 1
    def test_missing_device_is_checked_again_before_opening(self):
        calls = []

        def appears_later():
            calls.append(True)
            return "Device /dev/x does not exist." if len(calls) < 3 else ""

        states, opened = self.run_reader(appears_later)
        self.assertEqual(states[0], (False, "Device /dev/x does not exist."))
        self.assertTrue(opened)
        self.assertGreaterEqual(len(calls), 3 + len(opened) - 1)

    # Review round 2, suggestion
    def test_closed_queue_delivers_nothing(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        received = []
        queue = ChunkQueue(loop, received.append)
        queue.put(b"late")
        queue.close()
        queue.put(b"later")
        loop.run_until_complete(asyncio.sleep(0))
        queue.drain()
        self.assertEqual(received, [])

    # Review round 1, finding 9
    def test_queue_is_bounded_and_counts_drops(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        received = []
        queue = ChunkQueue(loop, received.append, limit=10)
        thread = threading.Thread(target=lambda: [queue.put(b"12345") for _ in range(4)])
        thread.start()
        thread.join()
        loop.run_until_complete(asyncio.sleep(0))
        self.assertEqual(b"".join(received), b"1234512345")
        self.assertEqual(queue.dropped_bytes, 10)


# ---------------------------------------------------------------- stage 2 helpers

FIRMWARE = "LG290P03AANR01A03S"  # the example version string in the specification


def simulated_reply(body):
    """What a simulated LG290P answers to a command body: synthetic values only."""
    fields = body.split(",")
    name = fields[0]
    fixed = {"PQTMVERNO": f"PQTMVERNO,{FIRMWARE},2024/04/30,10:53:07",
             "PQTMCFGUART,R": "PQTMCFGUART,OK,1,460800,8,0,1,0",
             "PQTMCFGRCVRMODE,R": "PQTMCFGRCVRMODE,OK,2",
             "PQTMCFGRTCM,R": "PQTMCFGRTCM,OK,4,0,-90.0,07,06,1,0"}
    if body == "PQTMLSTMSG":
        return ["PQTMLSTMSG,OK,1,1,GGA,1", "PQTMLSTMSG,OK,1,1,RTCM3-107X,1,0", "PQTMLSTMSG,OK,End"]
    if body in fixed:
        return [fixed[body]]
    if name == "PQTMCFGMSGRATE" and fields[1:2] == ["R"]:
        if "PQTMANTENNASTATUS" in fields:
            return ["PQTMCFGMSGRATE,ERROR,1"]
        # MSM messages report an offset; every rate reads 0 (off), except GPS MSM.
        rate = "1" if fields[4] == "RTCM3-107X" else "0"
        tail = [rate] + fields[5:] + (["0"] if fields[4].endswith("X") else [])
        return ["PQTMCFGMSGRATE,OK," + ",".join(fields[2:5] + tail)]
    if fields[1:2] == ["W"] or name == "PQTMSAVEPAR":
        return [f"{name},OK"]
    if name == "PQTMSRR":
        return []
    return [f"{name},ERROR,3"]


def sentence_body(sentence):
    """The body of one complete sentence, checking its checksum."""
    text = sentence.decode("ascii")
    assert text.startswith("$") and text.endswith("\r\n"), text
    body, _, given = text[1:-2].rpartition("*")
    assert given == checksum(body), text
    return body


class FakePort:
    """Stands in for SerialReader.write: records each body and delivers the replies
    on the event loop, as the frame parser would. `delay` delays the replies."""

    def __init__(self, loop, deliver, answer=simulated_reply, delay=0.0):
        self.loop, self.deliver, self.answer, self.delay = loop, deliver, answer, delay
        self.written = []
        self.events = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise PortClosed("The receiver is not connected.")
        body = sentence_body(data)
        self.written.append(body)
        self.events.append(("write", body))
        for reply in self.answer(body):
            self.loop.call_soon_threadsafe(self._later, reply)

    def _later(self, reply):
        self.loop.call_later(self.delay, self._deliver, reply)

    def _deliver(self, reply):
        self.events.append(("reply", reply))
        self.deliver(reply)


class BitWriter:
    """Builds RTCM3 payloads field by field, for synthetic test frames."""

    def __init__(self):
        self.value = 0
        self.size = 0

    def u(self, bits, value):
        assert 0 <= value < 1 << bits, (bits, value)
        self.value = (self.value << bits) | value
        self.size += bits
        return self

    def s(self, bits, value):
        assert -(1 << (bits - 1)) <= value < 1 << (bits - 1)
        return self.u(bits, value & ((1 << bits) - 1))

    def frame(self):
        """The whole frame: preamble, length, payload padded to bytes, CRC-24Q."""
        pad = -self.size % 8
        payload = (self.value << pad).to_bytes((self.size + pad) // 8, "big")
        head = bytes([0xD3, len(payload) >> 8, len(payload) & 255]) + payload
        return head + crc24q(head).to_bytes(3, "big")


def arp_frame(x, y, z, station=7, antenna_height=None):
    """A 1005 (or with antenna_height, 1006) frame for an ECEF position in metres."""
    bits = BitWriter().u(12, 1006 if antenna_height is not None else 1005).u(12, station)
    bits.u(6, 0).u(1, 1).u(1, 1).u(1, 1).u(1, 0)
    bits.s(38, round(x * 10000)).u(1, 0).u(1, 0)
    bits.s(38, round(y * 10000)).u(2, 0)
    bits.s(38, round(z * 10000))
    if antenna_height is not None:
        bits.u(16, round(antenna_height * 10000))
    return bits.frame()


def msm_frame(number, cells, multiple=False):
    """An MSM frame. `cells` maps (satellite, signal id) to the raw CNR field. Every
    other field gets a busy bit pattern, so a decoder reading the wrong offset fails."""
    msm = number % 10
    satellites = sorted({sat for sat, _ in cells})
    signals = sorted({sig for _, sig in cells})
    bits = BitWriter().u(12, number).u(12, 4095).u(30, 123456).u(1, int(multiple))
    bits.u(3, 5).u(7, 0).u(2, 1).u(2, 2).u(1, 1).u(3, 3)
    bits.u(64, sum(1 << (64 - sat) for sat in satellites))
    bits.u(32, sum(1 << (32 - sig) for sig in signals))
    present = [(sat, sig) for sat in satellites for sig in signals if (sat, sig) in cells]
    for sat in satellites:
        for sig in signals:
            bits.u(1, int((sat, sig) in cells))

    def fill(width, count):
        for index in range(count):
            bits.u(width, ((1 << width) - 1) ^ (index & 1))

    n, c = len(satellites), len(present)
    sat_fields = {4: (8, 10), 5: (8, 4, 10, 14), 6: (8, 10), 7: (8, 4, 10, 14)}[msm]
    for width in sat_fields:
        fill(width, n)
    before, cnr_bits, after = {4: ((15, 22, 4, 1), 6, ()), 5: ((15, 22, 4, 1), 6, (15,)),
                               6: ((20, 24, 10, 1), 10, ()),
                               7: ((20, 24, 10, 1), 10, (15,))}[msm]
    for width in before:
        fill(width, c)
    for key in present:
        bits.u(cnr_bits, cells[key])
    for width in after:
        fill(width, c)
    return bits.frame()


def run_async(coroutine_function):
    return asyncio.run(coroutine_function())


def post_json(url, body, headers=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers=dict({"Content-Type": "application/json"},
                                                  **(headers or {})))
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as error:
        with error:
            return json.load(error)


class SimulatedReceiver:
    """The receiver's side of a pty: answers commands with `simulated_reply`, and
    sends whatever the test gives it. Everything sent is kept, in order."""

    def __init__(self, master):
        self.master = master
        self.lock = threading.Lock()
        self.sent = b""
        self.heard = []
        self.bad_lines = []
        self._buffer = b""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        os.close(self.master)

    def send(self, data):
        with self.lock:
            view = memoryview(data)
            while view:
                view = view[os.write(self.master, view):]
            self.sent += data

    def _run(self):
        while not self._stop.is_set():
            if not select.select([self.master], [], [], 0.1)[0]:
                continue
            try:
                self._buffer += os.read(self.master, 4096)
            except OSError:
                return
            *lines, self._buffer = self._buffer.split(b"\r\n")
            for line in lines:
                try:
                    body = sentence_body(line + b"\r\n")
                except (AssertionError, UnicodeDecodeError):
                    self.bad_lines.append(line)
                    continue
                self.heard.append(body)
                for reply in simulated_reply(body):
                    self.send(nmea(reply))


# ---------------------------------------------------------------- stage 2 tests

class Checksums(unittest.TestCase):
    def test_checksums_match_the_specification_examples(self):
        for body, expected in [("PQTMVERNO", "58"), ("PQTMSAVEPAR", "5A"),
                               ("PQTMRESTOREPAR", "13"), ("PQTMSRR", "4B"),
                               ("PQTMCFGRCVRMODE,W,2", "29"), ("PQTMCFGRCVRMODE,R", "32"),
                               ("PQTMCFGSVIN,R", "26"), ("PQTMCFGRTCM,W,4,0,-90.0,07,06,1,0", "3B"),
                               ("PQTMCFGMSGRATE,W,RTCM3-107X,1,0", "2F"),
                               ("PQTMCFGUART,W,1,115200", "05")]:
            self.assertEqual(checksum(body), expected, body)

    def test_sentence_is_built_from_the_normalised_body(self):
        prepared = prepare(" $pqtmcfgrcvrmode , r ")
        self.assertEqual(prepared.sentence, b"$PQTMCFGRCVRMODE,R*32\r\n")
        self.assertEqual(prepared.shown, "$PQTMCFGRCVRMODE,R*32")
        self.assertEqual(prepare("$PQTMVERNO*58").sentence, b"$PQTMVERNO*58\r\n")

    def test_bad_input_is_refused(self):
        for text in ["", "   ", "$", "PQTMVERNO*59", "PQTMVERNO*5", "PQTMVERNO*ZZ",
                     "PQTMVERNO\r\nPQTMSRR", "PQTMVERNO\nPQTMSRR", "PQTM$VERNO", "PQTMVERNO;SRR",
                     "PQTMVERNÖ", "P" * 200, ",R", 42, None]:
            prepared = prepare(text)
            self.assertEqual(prepared.kind, "blocked", text)
            self.assertTrue(prepared.reason, text)

    def test_a_given_checksum_must_match_what_was_typed(self):
        self.assertEqual(prepare("$PQTMCFGSVIN,R*26").kind, "query")
        self.assertEqual(prepare("$pqtmverno*78").kind, "query")  # checksum of lower case
        self.assertEqual(prepare("$pqtmverno*58").kind, "blocked")


class SafetyRules(unittest.TestCase):
    def kind(self, text, current="1"):
        return prepare(text, current).kind

    def test_factory_reset_is_blocked_in_any_spelling(self):
        for text in ["PQTMRESTOREPAR", "$PQTMRESTOREPAR*13", "pqtmrestorepar", " PQTM RESTOREPAR ",
                     "PQTMRESTOREPAR,1", "pQtMrEsToRePaR\t"]:
            prepared = prepare(text, "1")
            self.assertEqual(prepared.kind, "blocked", text)
            self.assertIn("Factory reset", prepared.reason)

    def test_firmware_and_bootloader_commands_are_blocked(self):
        for text in ["PQTMFWUPGRADE", "PQTMBOOTLOADER", "PQTMUPGRADE,1", "pqtmflash", "PQTMOTA",
                     "PQTMERASE,ALL", "PQTMDOWNLOAD"]:
            self.assertEqual(self.kind(text), "blocked", text)

    def test_uart_writes_that_could_touch_uart1_or_this_port_are_blocked(self):
        for text in ["PQTMCFGUART,W,115200",                 # the current interface
                     "pqtmcfguart,w,115200",
                     "PQTMCFGUART, W, 115200 ",
                     "PQTMCFGUART,W,115200,8,0,1,0",         # current, all parameters
                     "PQTMCFGUART,W,1,115200",
                     "PQTMCFGUART,W,1,115200,8,0,1,0",
                     "$PQTMCFGUART,W,1,115200*05",
                     "PQTMCFGUART,W,01,115200", "PQTMCFGUART,W,2,115201",
                     "PQTMCFGUART,W,2", "PQTMCFGUART,W,2,115200,8", "PQTMCFGUART,W",
                     "PQTMCFGUART,X,2,115200", "PQTMCFGUART", "PQTMCFGUART,R,1,2",
                     "PQTMCFGUART,W,4,115200"]:
            self.assertEqual(self.kind(text), "blocked", text)
        # The port this app uses is protected too, whichever it is.
        self.assertEqual(self.kind("PQTMCFGUART,W,2,115200", current="2"), "blocked")
        # If the app does not know its port yet, every UART write is blocked.
        self.assertEqual(self.kind("PQTMCFGUART,W,2,115200", current=None), "blocked")
        # Another port, named by index, with the app on UART1: confirm.
        self.assertEqual(self.kind("PQTMCFGUART,W,2,115200"), "confirm")
        self.assertEqual(self.kind("pqtmcfguart,w,3,9600,8,0,1,0"), "confirm")
        self.assertEqual(self.kind("PQTMCFGUART,W,3,115200", current="2"), "confirm")

    def test_protocol_writes_that_could_touch_uart1_or_this_port_are_blocked(self):
        for text in ["PQTMCFGPROT,W,1,1,00000007,00000007", "pqtmcfgprot,w,1,1,1,1",
                     "PQTMCFGPROT,W,1,1,7,7", "PQTMCFGPROT,W,2,2,7,7", "PQTMCFGPROT,W,1,2,7",
                     "PQTMCFGPROT,W,1,2", "PQTMCFGPROT,W,7,7", "PQTMCFGPROT,W", "PQTMCFGPROT",
                     "PQTMCFGPROT,R"]:
            self.assertEqual(self.kind(text), "blocked", text)
        self.assertEqual(self.kind("PQTMCFGPROT,W,1,2,7,7", current="2"), "blocked")
        self.assertEqual(self.kind("PQTMCFGPROT,W,1,2,7,7", current=None), "blocked")
        self.assertEqual(self.kind("PQTMCFGPROT,W,1,2,00000007,00000005"), "confirm")
        self.assertEqual(self.kind("PQTMCFGPROT,R,1,1"), "query")

    def test_queries_run_and_everything_else_needs_confirmation(self):
        queries = ["PQTMVERNO", "$PQTMVERNO*58", "pqtmcfgrcvrmode,r", "PQTMCFGSVIN,R",
                   "PQTMCFGMSGRATE,R,1,1,RTCM3-107X", "PQTMCFGMSGRATE,R,PQTMEPE,2",
                   "PQTMCFGUART,R", "PQTMCFGUART,R,1", "PQTMLSTMSG", "PQTMLSTMSG,1,1",
                   "PQTMUNIQID", "PQTMSN", "PQTMCFGRTCM,R"]
        changes = ["PQTMSAVEPAR", "PQTMSRR", "PQTMCOLD", "PQTMCFGRCVRMODE,W,2",
                   "PQTMCFGSVIN,W,1,300,0,0,0,0", "PQTMCFGMSGRATE,W,1,1,GGA,1",
                   "PQTMCFGMSGRATE,W,GGA,1", "PQTMCLRMSG", "PQTMGNSSSTOP",
                   "PQTMFOO,R",              # unknown PQTM command, even with R
                   "PQTMCFGRCVRMODE,R2", "PQTMCFGRCVRMODE,RW", "PQTMVERNO,1",
                   "PQTMLSTMSG,1", "PQTMLSTMSG,A,B", "GPGGA", "PAIR062,0,1"]
        for text in queries:
            self.assertEqual(self.kind(text), "query", text)
        for text in changes:
            self.assertEqual(self.kind(text), "confirm", text)


class Pending(unittest.TestCase):
    def test_pending_ids_work_once_and_expire(self):
        clock = Clock(100.0)
        store = PendingStore(clock=clock, ttl=60)
        command = prepare("PQTMCFGRCVRMODE,W,2", "1")
        shown = store.add([command], "Title", warnings=["careful"])
        self.assertEqual(shown["commands"], ["$PQTMCFGRCVRMODE,W,2*29"])
        self.assertEqual((shown["title"], shown["warnings"], shown["expires_s"]),
                         ("Title", ["careful"], 60))
        clock.now += 59
        self.assertEqual(store.take(shown["pending"])["commands"], [command])
        self.assertIsNone(store.take(shown["pending"]), "an id works once")
        late = store.add([command], "Title")["pending"]
        clock.now += 60.5
        self.assertIsNone(store.take(late), "expired")
        for junk in (None, 3, "", "nope", ["x"]):
            self.assertIsNone(store.take(junk))
        ids = {store.add([command], "t")["pending"] for _ in range(100)}
        self.assertEqual(len(ids), 100)
        self.assertLessEqual(len(store._items), commands.MAX_PENDING)


class Runner(TempDir):
    def runner(self, loop, answer=simulated_reply, delay=0.0):
        holder = {}
        port = FakePort(loop, lambda reply: holder["runner"].on_nmea(reply), answer, delay)
        holder["runner"] = CommandRunner(port.write, CommandLog(self.dir / "commands.jsonl"))
        return holder["runner"], port

    def test_ok_error_and_timeout(self):
        def quiet(body):
            return [] if body.startswith("PQTMCFGRCVRMODE") else simulated_reply(body)

        async def go():
            runner, port = self.runner(asyncio.get_running_loop(), quiet)
            ok, = await runner.run([prepare("PQTMVERNO")], {"name": "a"}, "test")
            err, = await runner.run([prepare("PQTMFOO,1")], None, "test")
            with mock.patch.object(commands, "TIMEOUT_S", 0.2):
                silent, = await runner.run([prepare("PQTMCFGRCVRMODE,W,2")], None, "test")
            listed, = await runner.run([prepare("PQTMLSTMSG")], None, "test")
            return ok, err, silent, listed

        ok, err, silent, listed = run_async(go)
        self.assertEqual((ok["outcome"], ok["replies"]),
                         ("ok", [f"$PQTMVERNO,{FIRMWARE},2024/04/30,10:53:07"]))
        self.assertEqual((err["outcome"], err["error_code"], err["error"]),
                         ("error", "3", "Unsupported command"))
        self.assertEqual(silent["outcome"], "timeout")
        self.assertEqual(listed["outcome"], "ok")
        self.assertEqual(len(listed["replies"]), 3)

    def test_replies_are_matched_by_name_and_shape(self):
        async def go():
            loop = asyncio.get_running_loop()

            def answer(body):
                return ["GNGGA,1,2,3", "PQTMCFGRTCM,OK,4", "PQTMVERNO,X,1,2"]

            runner, _ = self.runner(loop, answer)
            return await runner.run([prepare("PQTMVERNO")], None, "test")

        result, = run_async(go)
        self.assertEqual(result["replies"], ["$PQTMVERNO,X,1,2"])

    def test_commands_are_serialised(self):
        async def go():
            runner, port = self.runner(asyncio.get_running_loop(), delay=0.05)
            batches = [[prepare("PQTMVERNO")], [prepare("PQTMCFGRCVRMODE,R")],
                       [prepare("PQTMCFGRTCM,R"), prepare("PQTMCFGUART,R")]]
            results = await asyncio.gather(*(runner.run(b, None, "test") for b in batches))
            return port, results

        port, results = run_async(go)
        self.assertTrue(all(r["outcome"] == "ok" for batch in results for r in batch))
        kinds = [kind for kind, _ in port.events]
        self.assertEqual(kinds, ["write", "reply"] * 4, port.events)
        self.assertEqual(port.written, ["PQTMVERNO", "PQTMCFGRCVRMODE,R", "PQTMCFGRTCM,R",
                                        "PQTMCFGUART,R"])

    def test_a_batch_stops_at_the_first_failure_and_blocked_is_never_sent(self):
        async def go():
            runner, port = self.runner(asyncio.get_running_loop())
            results = await runner.run([prepare("PQTMCFGRCVRMODE,W,2"), prepare("PQTMRESTOREPAR"),
                                        prepare("PQTMSAVEPAR")], None, "test")
            return port, results

        port, results = run_async(go)
        self.assertEqual([r["outcome"] for r in results], ["ok", "blocked", "not sent"])
        self.assertEqual(port.written, ["PQTMCFGRCVRMODE,W,2"])
        entries = [json.loads(line) for line in
                   (self.dir / "commands.jsonl").read_text().splitlines()]
        self.assertEqual([e["outcome"] for e in entries], ["ok", "blocked", "not sent"])

    def test_a_closed_or_lost_port_fails_cleanly(self):
        async def go():
            loop = asyncio.get_running_loop()
            runner, port = self.runner(loop, answer=lambda body: [])
            port.closed = True
            closed, = await runner.run([prepare("PQTMVERNO")], None, "test")
            port.closed = False
            loop.call_later(0.1, runner.port_lost)
            lost, = await runner.run([prepare("PQTMVERNO")], None, "test")
            after, = await runner.run([prepare("PQTMSRR")], None, "test")
            return closed, lost, after

        closed, lost, after = run_async(go)
        self.assertEqual((closed["outcome"], closed["error"]),
                         ("error", "The receiver is not connected."))
        self.assertEqual(lost["outcome"], "error")
        self.assertIn("port closed", lost["error"])
        self.assertEqual((after["outcome"], after["note"]),
                         ("ok", "Sent. This command has no reply."))

    def test_the_command_log(self):
        user = {"id": "abc", "name": "admin", "display_name": "Admin"}

        async def go():
            runner, _ = self.runner(asyncio.get_running_loop())
            await runner.run([prepare("PQTMVERNO")], user, "console")
            await runner.run([prepare("pqtmrestorepar")], user, "console")
            await runner.run([prepare("PQTMFOO")], None, "console")

        run_async(go)
        lines = (self.dir / "commands.jsonl").read_text().splitlines()
        entries = [json.loads(line) for line in lines]
        self.assertEqual(len(entries), 3)
        first, blocked, failed = entries
        self.assertRegex(first["utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual((first["user"], first["command"], first["outcome"], first["source"]),
                         (user, "$PQTMVERNO*58", "ok", "console"))
        self.assertEqual(first["replies"], [f"$PQTMVERNO,{FIRMWARE},2024/04/30,10:53:07"])
        self.assertEqual((blocked["outcome"], blocked["command"]),
                         ("blocked", "$PQTMRESTOREPAR*13"))
        self.assertIn("Factory reset", blocked["error"])
        self.assertEqual((failed["outcome"], failed["error_code"]), ("error", "3"))
        log = CommandLog(self.dir / "commands.jsonl")
        self.assertEqual([e["outcome"] for e in log.tail(2)], ["blocked", "error"])
        with open(self.dir / "commands.jsonl", "a") as stream:
            stream.write("not json\n")
        self.assertEqual(len(log.tail(20)), 3)
        self.assertEqual(CommandLog(self.dir / "missing.jsonl").tail(), [])

    def logged(self):
        return [json.loads(line) for line in
                (self.dir / "commands.jsonl").read_text().splitlines()]

    # Stage 2 review round 1, fix 1: the probe
    def test_a_late_read_reply_cannot_complete_a_write(self):
        def answer(body):
            if body == "PQTMCFGRCVRMODE,R":
                return []
            if body == "PQTMCFGRCVRMODE,W,2":  # the late answer to the read, then the write's
                return ["PQTMCFGRCVRMODE,OK,2", "PQTMCFGRCVRMODE,ERROR,1"]
            return simulated_reply(body)

        async def go():
            runner, port = self.runner(asyncio.get_running_loop(), answer)
            with mock.patch.object(commands, "TIMEOUT_S", 0.1), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.05):
                read, = await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test")
                batch = await runner.run([prepare(b) for b in (
                    "PQTMCFGRCVRMODE,W,2", "PQTMSAVEPAR", "PQTMSRR")], None, "step")
            return read, batch, port.written

        read, batch, written = run_async(go)
        self.assertEqual(read["outcome"], "timeout")
        self.assertEqual([r["outcome"] for r in batch], ["error", "not sent", "not sent"])
        self.assertEqual((batch[0]["error_code"], batch[0]["replies"]),
                         ("1", ["$PQTMCFGRCVRMODE,ERROR,1"]))
        self.assertEqual(batch[0]["ignored_replies"], ["$PQTMCFGRCVRMODE,OK,2"])
        self.assertEqual(written, ["PQTMCFGRCVRMODE,R", "PQTMCFGRCVRMODE,W,2"])
        # Fix 6: the commands not sent are in the log, with the reason.
        unsent = [e for e in self.logged() if e["outcome"] == "not sent"]
        self.assertEqual([e["command"] for e in unsent], ["$PQTMSAVEPAR*5A", "$PQTMSRR*4B"])
        self.assertTrue(all("$PQTMCFGRCVRMODE,W,2*29 ended error" in e["error"] for e in unsent))

    def test_after_a_timeout_the_same_name_waits_out_late_replies(self):
        async def go():
            loop = asyncio.get_running_loop()
            runner = None

            def answer(body):
                if body == "PQTMCFGRCVRMODE,R":  # answered late, as ERROR
                    loop.call_soon_threadsafe(loop.call_later, 0.15, port._deliver,
                                              "PQTMCFGRCVRMODE,ERROR,2")
                    return []
                return simulated_reply(body)

            runner, port = self.runner(loop, answer)
            with mock.patch.object(commands, "TIMEOUT_S", 0.1), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.3):
                read, = await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test")
                other, = await runner.run([prepare("PQTMVERNO")], None, "test")
                write, = await runner.run([prepare("PQTMCFGRCVRMODE,W,2")], None, "test")
            return read, other, write, port.events

        read, other, write, events = run_async(go)
        self.assertEqual((read["outcome"], other["outcome"]), ("timeout", "ok"))
        self.assertEqual((write["outcome"], write["replies"]), ("ok", ["$PQTMCFGRCVRMODE,OK"]))
        late = events.index(("reply", "PQTMCFGRCVRMODE,ERROR,2"))
        self.assertLess(late, events.index(("write", "PQTMCFGRCVRMODE,W,2")))
        # Another name is not held up.
        self.assertLess(events.index(("write", "PQTMVERNO")), late)

    def test_reply_shapes(self):
        def shape(body, reply):
            return commands.reply_shape(body, reply.split(","))

        cases = [
            ("PQTMCFGRCVRMODE,W,2", "PQTMCFGRCVRMODE,OK", "ok"),
            ("PQTMCFGRCVRMODE,W,2", "PQTMCFGRCVRMODE,OK,2", None),
            ("PQTMCFGRCVRMODE,W,2", "PQTMCFGRCVRMODE,ERROR,1", "error"),
            ("PQTMCFGRCVRMODE,W,2", "PQTMCFGRCVRMODE,ERROR", None),
            ("PQTMCFGRCVRMODE,R", "PQTMCFGRCVRMODE,OK", None),
            ("PQTMCFGRCVRMODE,R", "PQTMCFGRCVRMODE,OK,2", "ok"),
            ("PQTMCFGRCVRMODE,R", "PQTMCFGRCVRMODE,ERROR,3", "error"),
            ("PQTMSAVEPAR", "PQTMSAVEPAR,OK", "ok"),
            ("PQTMSAVEPAR", "PQTMSAVEPAR,OK,1", None),
            ("PQTMCFGUART,R", "PQTMCFGUART,OK,1,460800,8,0,1,0", "ok"),
            ("PQTMCFGUART,R,2", "PQTMCFGUART,OK,2,115200,8,0,1,0", "ok"),
            ("PQTMCFGUART,R,2", "PQTMCFGUART,OK,1,460800,8,0,1,0", None),
            ("PQTMCFGPROT,R,1,2", "PQTMCFGPROT,OK,1,2,00000007,00000007", "ok"),
            ("PQTMCFGPROT,R,1,2", "PQTMCFGPROT,OK,1,1,00000007,00000007", None),
            ("PQTMCFGSAT,R,1,1", "PQTMCFGSAT,OK,1,01,FFFFFFFF", "ok"),
            ("PQTMCFGMSGRATE,R,GGA", "PQTMCFGMSGRATE,OK,GGA,1", "ok"),
            ("PQTMCFGMSGRATE,R,PQTMEPE,2", "PQTMCFGMSGRATE,OK,PQTMEPE,1,2", "ok"),
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-1005", "PQTMCFGMSGRATE,OK,1,1,RTCM3-1005,1", "ok"),
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-1005", "PQTMCFGMSGRATE,OK,1,1,RTCM3-1006,1", None),
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-107X", "PQTMCFGMSGRATE,OK,1,2,RTCM3-107X,1,0", None),
            ("PQTMCFGMSGRATE,R,1,1,0AB2,1", "PQTMCFGMSGRATE,OK,1,1,0AB2,1,1", "ok"),
            # Round 2, fix 4: the port fields of the reply are optional.
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-1005", "PQTMCFGMSGRATE,OK,RTCM3-1005,1", "ok"),
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-1005", "PQTMCFGMSGRATE,OK,RTCM3-1006,1", None),
            ("PQTMCFGMSGRATE,R,1,1,RTCM3-107X", "PQTMCFGMSGRATE,OK,RTCM3-107X,1,0", "ok"),
            ("PQTMCFGMSGRATE,R,GGA", "PQTMCFGMSGRATE,OK,1,1,GGA,1", "ok"),
            ("PQTMCFGMSGRATE,R,GGA", "PQTMCFGMSGRATE,OK,1,1,RMC,1", None),
            ("PQTMCFGMSGRATE,R,GGA", "PQTMCFGMSGRATE,OK,GGA", None),
            ("PQTMCFGMSGRATE,R,GGA", "PQTMCFGMSGRATE,OK,RMC,1", None),
            ("PQTMCFGMSGRATE,R", "PQTMCFGMSGRATE,OK,,1", None),
            ("PQTMVERNO", f"PQTMVERNO,{FIRMWARE},2024/04/30,10:53:07", "ok"),
            ("PQTMVERNO", "PQTMVERNO,OK", None),
            ("PQTMUNIQID", "PQTMUNIQID,OK,16,81D62010EE0AF375BDF5952CDC3757A1", "ok"),
            ("PQTMLSTMSG", "PQTMLSTMSG,OK,1,1,GGA,1", "part"),
            ("PQTMLSTMSG", "PQTMLSTMSG,OK,End", "ok"),
            ("PQTMLSTMSG,1,2", "PQTMLSTMSG,OK,1,1,GGA,1", None),
            ("PQTMCFGRTCM,R", "PQTMVERNO,X", None),
        ]
        for body, reply, expected in cases:
            self.assertEqual(shape(body, reply), expected, (body, reply))

    # Fix 3
    def test_a_write_that_times_out_holds_back_the_next_write(self):
        release = threading.Event()
        written = []

        async def go():
            loop = asyncio.get_running_loop()
            holder = {}

            def write(data):
                body = sentence_body(data)
                if body == "PQTMCFGRCVRMODE,W,2":
                    release.wait(5)
                written.append(body)
                for reply in simulated_reply(body):
                    loop.call_soon_threadsafe(holder["runner"].on_nmea, reply)

            runner = holder["runner"] = CommandRunner(write, CommandLog(self.dir / "c.jsonl"))
            try:
                with mock.patch.object(commands, "WRITE_TIMEOUT_S", 0.1), \
                        mock.patch.object(commands, "LATE_GRACE_S", 0.01):
                    # Still writing when the next command waits: it is refused, unsent.
                    first = await runner.run([prepare("PQTMCFGRCVRMODE,W,2"),
                                              prepare("PQTMSAVEPAR")], None, "test")
                    refused, = await runner.run([prepare("PQTMVERNO")], None, "test")
                    loop.call_later(0.05, release.set)
                    # Finishes while the next waits: that one goes out after it.
                    after, = await runner.run([prepare("PQTMVERNO")], None, "test")
            finally:
                release.set()
            return first, refused, after

        first, refused, after = run_async(go)
        self.assertEqual([r["outcome"] for r in first], ["timeout", "not sent"])
        self.assertIn("may have been sent", first[0]["error"])
        self.assertEqual(refused["outcome"], "not sent")
        self.assertIn("has not finished", refused["error"])
        self.assertEqual(after["outcome"], "ok")
        self.assertEqual(written, ["PQTMCFGRCVRMODE,W,2", "PQTMVERNO"])

    # Fix 4, in the runner: the rules are checked again before each command.
    def test_a_recheck_stops_the_rest_of_a_batch(self):
        answers = iter(["", "the port changed"])

        async def go():
            runner, port = self.runner(asyncio.get_running_loop())
            results = await runner.run([prepare("PQTMSAVEPAR"), prepare("PQTMCFGRCVRMODE,W,2"),
                                        prepare("PQTMSRR")], None, "test",
                                       recheck=lambda item: next(answers))
            return results, port.written

        results, written = run_async(go)
        self.assertEqual([r["outcome"] for r in results], ["ok", "not sent", "not sent"])
        self.assertEqual(results[1]["error"], "the port changed")
        self.assertEqual(written, ["PQTMSAVEPAR"])
        self.assertEqual([e["outcome"] for e in self.logged()], ["ok", "not sent", "not sent"])

    # Stage 2 review round 2, fix 1: the recheck comes after every wait.
    def test_the_recheck_runs_after_the_waits_just_before_the_write(self):
        state = {"reason": "", "asked": []}

        def answer(body):
            return [] if body == "PQTMCFGRCVRMODE,R" else simulated_reply(body)

        def recheck(item):
            state["asked"].append(item.body)
            return state["reason"]

        async def go():
            loop = asyncio.get_running_loop()
            runner, port = self.runner(loop, answer)
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.3):
                await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test")
                loop.call_later(0.1, state.__setitem__, "reason", "changed while waiting")
                results = await runner.run([prepare("PQTMCFGRCVRMODE,W,2"),
                                            prepare("PQTMSAVEPAR")], None, "test",
                                           recheck=recheck)
            return results, port.written

        results, written = run_async(go)
        self.assertEqual([r["outcome"] for r in results], ["not sent", "not sent"])
        self.assertEqual(results[0]["error"], "changed while waiting")
        self.assertEqual(state["asked"], ["PQTMCFGRCVRMODE,W,2"])
        self.assertEqual(written, ["PQTMCFGRCVRMODE,R"])

    def test_a_port_loss_while_a_batch_waits_stops_changes_but_not_queries(self):
        def answer(body):
            return [] if body.startswith("PQTMCFGRCVRMODE") else simulated_reply(body)

        async def go():
            loop = asyncio.get_running_loop()
            runner, port = self.runner(loop, answer)
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.2):
                await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test")
                loop.call_later(0.05, runner.port_lost)
                change = await runner.run([prepare("PQTMCFGRCVRMODE,W,2")], None, "test")
                await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test")
                loop.call_later(0.05, runner.port_lost)
                query = await runner.run([prepare("PQTMCFGRCVRMODE,R")], None, "test",
                                         stop_on_failure=False)
            return change, query, port.written

        change, query, written = run_async(go)
        self.assertEqual(change[0]["outcome"], "not sent")
        self.assertIn("port closed while this batch waited", change[0]["error"])
        self.assertEqual(query[0]["outcome"], "timeout")
        self.assertEqual(written, ["PQTMCFGRCVRMODE,R"] * 3)

    # Fix 2: a write that is cancelled is logged and still waited for.
    def test_cancelling_a_batch_logs_it_and_holds_back_the_next_write(self):
        release = threading.Event()
        written = []

        async def go():
            loop = asyncio.get_running_loop()
            holder = {}

            def write(data):
                body = sentence_body(data)
                if body == "PQTMCFGRCVRMODE,W,2":
                    release.wait(5)
                written.append(body)
                for reply in simulated_reply(body):
                    loop.call_soon_threadsafe(holder["runner"].on_nmea, reply)

            log = CommandLog(self.dir / "commands.jsonl")
            runner = holder["runner"] = CommandRunner(write, log)
            try:
                with mock.patch.object(commands, "WRITE_TIMEOUT_S", 0.2), \
                        mock.patch.object(commands, "LATE_GRACE_S", 0.01):
                    batch = asyncio.ensure_future(runner.run(
                        [prepare("PQTMCFGRCVRMODE,W,2"), prepare("PQTMSAVEPAR")], None, "step"))
                    queued = asyncio.ensure_future(runner.run([prepare("PQTMVERNO")], None, "q"))
                    await asyncio.sleep(0.05)
                    batch.cancel()
                    queued.cancel()
                    for task in (batch, queued):
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    refused, = await runner.run([prepare("PQTMVERNO")], None, "test")
                    loop.call_later(0.05, release.set)
                    after, = await runner.run([prepare("PQTMVERNO")], None, "test")
                    # The cancelled write's OK came late: it does not answer this one.
                    again, = await runner.run([prepare("PQTMCFGRCVRMODE,W,2")], None, "test")
            finally:
                release.set()
            return refused, after, again

        refused, after, again = run_async(go)
        self.assertEqual(refused["outcome"], "not sent")
        self.assertIn("has not finished", refused["error"])
        self.assertEqual(after["outcome"], "ok")
        self.assertEqual((again["outcome"], again["replies"]), ("ok", ["$PQTMCFGRCVRMODE,OK"]))
        self.assertEqual(written, ["PQTMCFGRCVRMODE,W,2", "PQTMVERNO", "PQTMCFGRCVRMODE,W,2"])
        entries = self.logged()
        self.assertEqual([(e["source"], e["outcome"]) for e in entries[:3]],
                         [("step", "cancelled"), ("step", "not sent"), ("q", "not sent")])
        self.assertIn("may have been sent", entries[0]["error"])
        self.assertEqual(entries[1]["error"], commands.STOPPED)

    # Fix 3: a late reply is owed to the command that timed out.
    def test_a_late_reply_cannot_answer_a_later_command_of_the_same_name(self):
        async def go():
            loop = asyncio.get_running_loop()
            saves = []

            def answer(body):
                if body == "PQTMSAVEPAR":
                    saves.append(body)
                    if len(saves) == 1:  # answered after the grace period
                        loop.call_soon_threadsafe(loop.call_later, 0.45, port._deliver,
                                                  "PQTMSAVEPAR,OK")
                    return []  # the second is never answered
                return simulated_reply(body)

            runner, port = self.runner(loop, answer)
            with mock.patch.object(commands, "SAVE_TIMEOUT_S", 0.2), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.1):
                survey = await runner.run([prepare("PQTMSAVEPAR")], None, "survey")
                base = await runner.run([prepare(b) for b in (
                    "PQTMCFGRCVRMODE,W,2", "PQTMSAVEPAR", "PQTMSRR")], None, "base")
            return survey, base, port.written

        survey, base, written = run_async(go)
        self.assertEqual(survey[0]["outcome"], "timeout")
        self.assertEqual([r["outcome"] for r in base], ["ok", "timeout", "not sent"])
        self.assertEqual(base[1]["ignored_replies"], ["$PQTMSAVEPAR,OK"])
        self.assertIn("still owed a reply", base[1]["error"])
        self.assertNotIn("PQTMSRR", written)

    def test_owed_replies_expire_and_a_restart_forgets_them(self):
        clock = Clock()

        def answer(body):
            return [] if body == "PQTMSAVEPAR" else simulated_reply(body)

        async def go():
            loop = asyncio.get_running_loop()
            runner, port = self.runner(loop, answer)
            runner._clock = clock
            with mock.patch.object(commands, "SAVE_TIMEOUT_S", 0.05), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.0):
                await runner.run([prepare("PQTMSAVEPAR")], None, "test")
                runner.on_nmea("PQTMSAVEPAR,OK")  # settles it
                taken = runner._owed_now("PQTMSAVEPAR")
                await runner.run([prepare("PQTMSAVEPAR")], None, "test")
                clock.now += commands.OWED_S + 1
                expired = runner._owed_now("PQTMSAVEPAR")
                await runner.run([prepare("PQTMSAVEPAR")], None, "test")
                runner.receiver_restarted()
                forgotten = runner._owed_now("PQTMSAVEPAR")
            return taken, expired, forgotten

        self.assertEqual(run_async(go), ([], [], []))

    def test_a_late_answer_to_the_same_query_is_taken(self):
        async def go():
            loop = asyncio.get_running_loop()
            calls = []

            def answer(body):
                calls.append(body)
                if body == "PQTMVERNO" and len(calls) == 1:
                    return []
                return simulated_reply(body)

            runner, port = self.runner(loop, answer)
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.0):
                first, = await runner.run([prepare("PQTMVERNO")], None, "test")
                second, = await runner.run([prepare("PQTMVERNO")], None, "test")
                await asyncio.sleep(0.01)
            return first, second, runner._owed_now("PQTMVERNO")

        first, second, owed = run_async(go)
        self.assertEqual((first["outcome"], second["outcome"]), ("timeout", "ok"))
        self.assertEqual(len(owed), 1)  # the first is still owed its reply

    # Fix 5: a stuck write stops every batch at once, queries included.
    def test_a_stuck_write_stops_a_whole_batch_of_queries_at_once(self):
        release = threading.Event()

        async def go():
            runner = CommandRunner(lambda data: release.wait(5),
                                   CommandLog(self.dir / "commands.jsonl"))
            try:
                with mock.patch.object(commands, "WRITE_TIMEOUT_S", 0.1):
                    await runner.run([prepare("PQTMCFGRCVRMODE,W,2")], None, "test")
                    start = time.monotonic()
                    results = await runner.run([prepare("PQTMVERNO")] * 15, None, "check",
                                               stop_on_failure=False)
                    return results, time.monotonic() - start
            finally:
                release.set()

        results, elapsed = run_async(go)
        self.assertEqual({r["outcome"] for r in results}, {"not sent"})
        self.assertTrue(all("has not finished" in r["error"] for r in results))
        self.assertLess(elapsed, 0.5)


class Profiles(unittest.TestCase):
    def test_the_shipped_profile(self):
        items = profile.load()
        by_label = {item.label: item for item in items}
        mode = by_label["Receiver mode: base station"]
        self.assertEqual((mode.query, mode.set, mode.restart),
                         ("PQTMCFGRCVRMODE,R", "PQTMCFGRCVRMODE,W,2", True))
        rtcm_item = by_label["RTCM: MSM type, elevation mask, ephemeris output"]
        self.assertEqual(rtcm_item.set, "PQTMCFGRTCM,W,4,0,-90.0,07,06,1,0")
        msm = by_label["RTCM3-107X"]
        self.assertEqual((msm.query, msm.set), ("PQTMCFGMSGRATE,R,1,1,RTCM3-107X",
                                                "PQTMCFGMSGRATE,W,1,1,RTCM3-107X,1,0"))
        svin = by_label["PQTMSVINSTATUS"]
        self.assertEqual((svin.query, svin.set, svin.base_only),
                         ("PQTMCFGMSGRATE,R,1,1,PQTMSVINSTATUS,1",
                          "PQTMCFGMSGRATE,W,1,1,PQTMSVINSTATUS,1,1", True))
        for name in ("RTCM3-1005", "RTCM3-108X", "RTCM3-109X", "RTCM3-112X", "RTCM3-1230",
                     "RTCM3-1019", "RTCM3-1020", "RTCM3-1042", "RTCM3-1046", "GST",
                     "PQTMANTENNASTATUS"):
            self.assertIn(name, by_label)
        for item in items:
            self.assertEqual(prepare(item.query, "1").kind, "query", item.query)
            self.assertEqual(prepare(item.set, "1").kind, "confirm", item.set)
        text = profile.DEFAULT_PATH.read_text()
        self.assertNotIn("PQTMCFGSVIN", text.replace("(PQTMCFGSVIN)", ""),
                         "the base position never goes in the profile")
        self.assertIsNone(re.search(r"\d{4,}\.\d", text), "no coordinates in the profile")

    def test_comparing_replies(self):
        setting = profile.parse('[[setting]]\ncommand = "PQTMCFGRTCM"\n'
                                'value = [4, 0, -90.0, 7, 6, 1, 0]\n')[0]
        self.assertEqual(setting.compare("4,0,-90.0,07,06,1,0".split(",")),
                         ("match", "4,0,-90.0,07,06,1,0"))
        self.assertEqual(setting.compare("7,0,-90.0,07,06,1,0".split(","))[0], "differ")
        self.assertEqual(setting.compare("4,0,-90.0,07,06,1".split(","))[0], "differ")
        self.assertEqual(setting.compare("4,0,-90.0,07,06,1,0,9".split(","))[0], "differ")
        self.assertEqual(setting.compare("4,0,nan,07,06,1,0".split(","))[0], "differ")
        message, = profile.parse('[port]\ntype = 1\nid = 2\n[[message]]\nname = "RTCM3-109X"\n'
                                 'rate = 1\noffset = 0\n')
        self.assertEqual(message.query, "PQTMCFGMSGRATE,R,1,2,RTCM3-109X")
        self.assertEqual(message.compare(["1", "2", "RTCM3-109X", "1", "0"])[0], "match")
        self.assertEqual(message.compare(["RTCM3-109X", "1", "0"])[0], "match")
        self.assertEqual(message.compare(["1", "2", "RTCM3-109X", "0", "0"])[0], "differ")
        self.assertEqual(message.compare(["1", "2", "RTCM3-109X", "1"])[0], "differ")
        self.assertEqual(message.compare(["1", "2", "RTCM3-108X", "1", "0"])[0], "differ")
        plain, = profile.parse('[[message]]\nname = "RTCM3-1005"\nrate = 1\n')
        self.assertEqual(plain.compare(["1", "1", "RTCM3-1005", "1"])[0], "match")

    def test_bad_profiles_are_refused(self):
        for text in ["", "not toml [", "[[setting]]\ncommand = 'PQTMCFGRTCM'\n",
                     "[[setting]]\ncommand = 'PQTMSRR'\nvalue = [1]\n",
                     "[[setting]]\ncommand = 'PQTMCFGRTCM,W'\nvalue = [1]\n",
                     "[[setting]]\ncommand = 'PQTMCFGRTCM'\nvalue = ['1,2']\n",
                     "[[setting]]\ncommand = 'PQTMCFGRTCM'\nvalue = [true]\n",
                     "[[message]]\nname = 'GGA'\nrate = 2000\n",
                     "[[message]]\nname = 'GGA;X'\nrate = 1\n",
                     "[[message]]\nname = 'GGA'\nrate = 1\noffset = 0\nversion = 1\n",
                     "[port]\ntype = 2\nid = 1\n[[message]]\nname = 'GGA'\nrate = 1\n",
                     "[extra]\n[[message]]\nname = 'GGA'\nrate = 1\n"]:
            with self.assertRaises(profile.ProfileError, msg=text):
                profile.parse(text)


class Rtcm(unittest.TestCase):
    def test_1005_round_trip_on_the_equator(self):
        frame = arp_frame(6378137.0 + 12.3456, 0.0, 0.0, station=42)
        (kind, payload), = FrameParser().feed(frame)
        self.assertEqual((kind, rtcm_type(payload)), ("rtcm", 1005))
        message = rtcmdec.decode_1005(payload)
        self.assertEqual((message["station_id"], message["systems"], message["antenna_height_m"]),
                         (42, ["GPS", "GLONASS", "Galileo"], None))
        x, y, z = message["ecef"]
        self.assertAlmostEqual(x, 6378149.3456, places=4)
        lat, lon, height = rtcmdec.ecef_to_llh(x, y, z)
        self.assertAlmostEqual(lat, 0.0, places=12)
        self.assertAlmostEqual(lon, 0.0, places=12)
        self.assertAlmostEqual(height, 12.3456, places=4)

    def test_1005_and_1006_round_trip_at_synthetic_positions(self):
        # Synthetic points, including negative ECEF values (two's complement fields).
        for lat, lon, height in [(45.0, 90.0, 100.0), (-33.5, -120.25, -20.0),
                                 (75.0, 179.9, 5000.0), (-60.0, 10.0, 0.0)]:
            x, y, z = rtcmdec.llh_to_ecef(lat, lon, height)
            message = rtcmdec.decode_1005(arp_frame(x, y, z, antenna_height=1.5))
            self.assertEqual((message["message"], message["antenna_height_m"]), (1006, 1.5))
            back = rtcmdec.ecef_to_llh(*message["ecef"])
            self.assertAlmostEqual(back[0], lat, places=8)
            self.assertAlmostEqual(back[1], lon, places=8)
            self.assertAlmostEqual(back[2], height, places=3)
        pole = rtcmdec.ecef_to_llh(0.0, 0.0, 6356752.3142 + 1.0)
        self.assertAlmostEqual(pole[0], 90.0, places=9)
        self.assertAlmostEqual(pole[2], 1.0, places=3)

    def test_crc_protects_the_synthetic_frames(self):
        frame = bytearray(arp_frame(6378137.0, 0.0, 0.0))
        frame[8] ^= 0x10
        parser = FrameParser()
        self.assertEqual(parser.feed(bytes(frame)), [])
        self.assertEqual(parser.bad_frames, 1)
        msm = msm_frame(1077, {(1, 2): 700})
        self.assertEqual([k for k, _ in FrameParser().feed(msm)], ["rtcm"])

    def test_msm4_masks_and_cnr(self):
        cells = {(3, 2): 45, (3, 15): 38, (17, 2): 41, (32, 22): 0, (64, 2): 30}
        message = rtcmdec.decode_msm(msm_frame(1074, cells, multiple=True))
        self.assertEqual((message["system"], message["msm"], message["station_id"],
                          message["epoch"], message["multiple"]), ("GPS", 4, 4095, 123456, True))
        self.assertEqual(message["satellites"], [3, 17, 32, 64])
        self.assertEqual(message["signals"], [2, 15, 22])
        self.assertEqual(message["cells"], [(3, 2, 45.0), (3, 15, 38.0), (17, 2, 41.0),
                                            (32, 22, None), (64, 2, 30.0)])

    def test_msm7_extended_cnr_and_other_types(self):
        cells = {(5, 2): 45 * 16 + 3, (5, 8): 600, (24, 8): 1}
        message = rtcmdec.decode_msm(msm_frame(1087, cells))
        self.assertEqual((message["system"], message["msm"]), ("GLONASS", 7))
        self.assertEqual(message["cells"], [(5, 2, 45.1875), (5, 8, 37.5), (24, 8, 0.0625)])
        for number, scale in [(1095, 1.0), (1126, 0.0625), (1124, 1.0), (1097, 0.0625)]:
            decoded = rtcmdec.decode_msm(msm_frame(number, {(1, 2): 40, (2, 14): 33}))
            self.assertEqual([c[2] for c in decoded["cells"]], [40 * scale, 33 * scale], number)
        self.assertEqual(rtcmdec.decode_msm(msm_frame(1124, {(1, 2): 40}))["system"], "BeiDou")

    def test_truncated_or_impossible_msm_is_refused(self):
        frame = msm_frame(1077, {(1, 2): 700, (2, 2): 600})
        payload = frame[3:-3][:-8]
        head = bytes([0xD3, 0, len(payload)]) + payload
        short = head + crc24q(head).to_bytes(3, "big")
        with self.assertRaises(ValueError):
            rtcmdec.decode_msm(short)
        with self.assertRaises(ValueError):
            rtcmdec.decode_msm(arp_frame(1.0, 2.0, 3.0))
        # 9 satellites and 8 signals declare 72 cells, more than the 64 allowed.
        bits = BitWriter().u(12, 1074).u(12, 0).u(30, 0).u(1, 0).u(18, 0)
        bits.u(64, (1 << 64) - (1 << 55)).u(32, (1 << 32) - (1 << 24)).u(72, 0)
        with self.assertRaises(ValueError):
            rtcmdec.decode_msm(bits.frame())

    def test_signal_names(self):
        self.assertEqual(rtcmdec.signal_name("GPS", 2), "L1 C/A (1C)")
        self.assertEqual(rtcmdec.signal_name("GPS", 15), "L2C M (2S)")
        self.assertEqual(rtcmdec.signal_name("GPS", 22), "L5 I (5I)")
        self.assertEqual(rtcmdec.signal_name("GPS", 30), "L1C D (1S)")
        self.assertEqual(rtcmdec.signal_name("GLONASS", 8), "G2 C/A (2C)")
        self.assertEqual(rtcmdec.signal_name("Galileo", 2), "E1 C (1C)")
        self.assertEqual(rtcmdec.signal_name("Galileo", 22), "E5a I (5I)")
        self.assertEqual(rtcmdec.signal_name("Galileo", 14), "E5b I (7I)")
        self.assertEqual(rtcmdec.signal_name("BeiDou", 2), "B1I (2I)")
        self.assertEqual(rtcmdec.signal_name("BeiDou", 14), "B2I (7I)")
        self.assertEqual(rtcmdec.signal_name("BeiDou", 8), "B3I (6I)")
        self.assertEqual(rtcmdec.signal_name("BeiDou", 30), "B1C D (1D)")
        self.assertEqual(rtcmdec.signal_name("BeiDou", 22), "B2a D (5D)")
        self.assertEqual(rtcmdec.signal_name("GPS", 5), "signal 5")


class RtcmStatus(unittest.TestCase):
    def feed(self, status, data):
        for kind, payload in FrameParser().feed(data):
            status.add_frame(kind, payload)

    def test_base_position_from_1005(self):
        clock = Clock()
        status = Status(clock=clock)
        self.feed(status, arp_frame(6378137.0 + 3.0, 0.0, 0.0, station=9))
        clock.now += 2
        base = status.snapshot()["base_position"]
        self.assertEqual((base["lat"], base["lon"], base["height_m"], base["station_id"],
                          base["message"], base["age_s"]), (0.0, 0.0, 3.0, 9, 1005, 2.0))
        self.assertEqual(status.snapshot()["fix"], {}, "a 1005 position is not a GGA fix")
        self.feed(status, arp_frame(0.0, 0.0, 0.0))  # no position yet: ignored
        self.assertEqual(status.snapshot()["base_position"]["height_m"], 3.0)
        self.assertEqual(status.rtcm_decode_errors, 1)
        strict_json(json.dumps(status.snapshot(), allow_nan=False))

    def test_signals_from_msm_when_gsv_is_absent_or_stale(self):
        clock = Clock()
        status = Status(clock=clock)
        self.feed(status, msm_frame(1074, {(1, 2): 44, (2, 2): 40, (3, 2): 36, (1, 15): 30}))
        self.feed(status, msm_frame(1127, {(20, 2): 47 * 16 + 8}))
        rows = status.snapshot()["signals"]
        self.assertEqual(rows, [
            {"constellation": "BeiDou", "signal": "B1I (2I)", "satellites": 1, "strongest": 47.5,
             "median": 47.5, "source": "RTCM MSM7"},
            {"constellation": "GPS", "signal": "L1 C/A (1C)", "satellites": 3, "strongest": 44.0,
             "median": 40.0, "source": "RTCM MSM4"},
            {"constellation": "GPS", "signal": "L2C M (2S)", "satellites": 1, "strongest": 30.0,
             "median": 30.0, "source": "RTCM MSM4"}])
        msm = status.snapshot()["msm"]
        self.assertEqual(msm["GPS"]["satellites"], 3)
        self.assertEqual(msm["GPS"]["signals"], ["L1 C/A (1C)", "L2C M (2S)"])
        self.assertEqual((msm["BeiDou"]["satellites"], msm["BeiDou"]["message"]), (1, 1127))
        # Fresh GSV wins for its constellation; MSM fills in the others.
        self.feed(status, GSV)
        sources = {(r["constellation"], r["source"]) for r in status.snapshot()["signals"]}
        self.assertEqual(sources, {("GPS", "GSV"), ("BeiDou", "RTCM MSM7")})
        clock.now += 11
        self.assertEqual(status.snapshot()["signals"], [])
        self.assertEqual(status.snapshot()["msm"], {})

    def test_survey_mean_from_svinstatus(self):
        clock = Clock()
        status = Status(clock=clock)
        x, y, z = rtcmdec.llh_to_ecef(0.0, 0.0, 7.0)
        self.feed(status, nmea(f"PQTMSVINSTATUS,1,1000,2,,01,300,300,{x:.4f},{y:.4f},{z:.4f},0.5"))
        snap = status.snapshot()
        self.assertEqual((snap["survey_mean"]["lat"], snap["survey_mean"]["height_m"]), (0.0, 7.0))
        self.assertEqual(status.survey_mean()["state"], "Finished (valid)")
        self.assertEqual(status.survey_mean()["valid"], 2)
        for bad in ("0,0,0", "1e308,1e308,1e308", "nan,1,1", ",,"):
            self.feed(status, nmea(f"PQTMSVINSTATUS,1,1000,1,,01,20,300,{bad},0.5"))
            self.assertIsNone(status.snapshot()["survey_mean"], bad)
            strict_json(json.dumps(status.snapshot(), allow_nan=False))


class ApiCommands(TempDir):
    """The receiver API over aiohttp, with a fake port that answers like a receiver."""

    def exchange(self, steps, status=None, answer=simulated_reply, current_uart="1"):
        """Run `steps(client, port, controller)` against a test server."""
        async def go():
            loop = asyncio.get_running_loop()
            holder = {}
            port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer)
            controller = control.Controller(port.write, self.dir / "commands.jsonl",
                                            profile.load(), status)
            controller.current_uart = current_uart
            holder["c"] = controller
            app = create_app(lambda: {"receiver": controller.snapshot()}, (), control=controller)
            async with TestClient(TestServer(app)) as client:
                return await steps(client, port, controller)

        return asyncio.run(go())

    @staticmethod
    async def post(client, path, body, **kwargs):
        reply = await client.post(path, json=body, **kwargs)
        return reply.status, strict_json(await reply.text())

    def test_query_runs_and_change_waits_for_confirmation(self):
        user = {"X-Remote-User-Id": "u1", "X-Remote-User-Name": "admin",
                "X-Remote-User-Display-Name": "<b>Admin</b>"}

        async def steps(client, port, _controller):
            out = {}
            out["query"] = await self.post(client, "/api/command", {"command": "pqtmverno"},
                                           headers=user)
            out["stage"] = await self.post(client, "/api/command",
                                           {"command": "$PQTMCFGRCVRMODE,W,2*29"}, headers=user)
            out["written_before"] = list(port.written)
            out["confirm"] = await self.post(client, "/api/command/confirm",
                                             {"id": out["stage"][1]["pending"]}, headers=user)
            out["again"] = await self.post(client, "/api/command/confirm",
                                           {"id": out["stage"][1]["pending"]})
            out["bogus"] = await self.post(client, "/api/command/confirm", {"id": "x" * 24})
            out["blocked"] = await self.post(client, "/api/command",
                                             {"command": "PQTMCFGUART,W,460800"})
            out["history"] = await (await client.get("/api/commands")).json()
            out["status"] = await (await client.get("/api/status")).json()
            out["written"] = list(port.written)
            return out

        out = self.exchange(steps)
        code, query = out["query"]
        self.assertEqual((code, query["outcome"]), (200, "ok"))
        self.assertEqual(out["status"]["receiver"]["firmware"], FIRMWARE)
        code, stage = out["stage"]
        self.assertEqual((code, stage["commands"]), (200, ["$PQTMCFGRCVRMODE,W,2*29"]))
        self.assertTrue(stage["warnings"])
        self.assertEqual(out["written_before"], ["PQTMVERNO"])
        code, confirm = out["confirm"]
        self.assertEqual((code, confirm["outcome"]), (200, "ok"))
        self.assertEqual(out["again"][0], 400)
        self.assertIn("expired", out["again"][1]["error"])
        self.assertEqual(out["bogus"][0], 400)
        code, blocked = out["blocked"]
        self.assertEqual((code, blocked["outcome"]), (200, "blocked"))
        self.assertIn("UART", blocked["reason"])
        self.assertEqual(out["written"], ["PQTMVERNO", "PQTMCFGRCVRMODE,W,2"])
        entries = out["history"]["entries"]
        self.assertEqual([e["outcome"] for e in entries], ["ok", "ok", "blocked"])
        self.assertEqual(entries[0]["user"], {"id": "u1", "name": "admin",
                                              "display_name": "<b>Admin</b>"})
        self.assertEqual(entries[1]["source"], "confirmed (console)")
        self.assertEqual(entries[2]["user"], {})

    def test_requests_must_be_json_objects(self):
        async def steps(client, port, _controller):
            results = []
            for kwargs in ({"data": "command=PQTMSRR",
                            "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
                           {"data": "PQTMSRR", "headers": {"Content-Type": "text/plain"}},
                           {"data": "{", "headers": {"Content-Type": "application/json"}},
                           {"data": "[1]", "headers": {"Content-Type": "application/json"}},
                           {"data": json.dumps({"command": 5}),
                            "headers": {"Content-Type": "application/json"}},
                           {"data": json.dumps({"command": "x" * 9000}),
                            "headers": {"Content-Type": "application/json"}}):
                reply = await client.post("/api/command", **kwargs)
                results.append(reply.status)
            get = await client.get("/api/command")
            return results, get.status, port.written

        codes, get_code, written = self.exchange(steps)
        self.assertEqual(codes[:5], [415, 415, 400, 400, 400])
        self.assertEqual(codes[5], 413)
        self.assertEqual(get_code, 405)
        self.assertEqual(written, [])

    def test_receiver_routes_need_ingress(self):
        app = create_app(lambda: {}, control=object())

        async def go():
            async with TestClient(TestServer(app)) as client:
                posts = ("/api/command", "/api/command/confirm", "/api/steps/base-mode")
                codes = [(await client.post(p, json={})).status for p in posts]
                for path in ("/api/commands", "/control.js"):
                    codes.append((await client.get(path)).status)
                return codes

        self.assertEqual(asyncio.run(go()), [403] * 5)

    def test_profile_check_and_apply_sends_only_differences_then_saves(self):
        async def steps(client, port, controller):
            check = await self.post(client, "/api/profile/check", {})
            port.written.clear()
            apply = await self.post(client, "/api/profile/apply", {})
            staged_written = list(port.written)
            done = await self.post(client, "/api/command/confirm", {"id": apply[1]["pending"]})
            return check, apply, staged_written, done, controller.snapshot()

        check, apply, staged_written, done, snap = self.exchange(steps)
        rows = {r["label"]: r for r in check[1]["profile"]}
        self.assertEqual(rows["Receiver mode: base station"]["status"], "match")
        self.assertEqual(rows["RTCM3-107X"]["status"], "match")
        self.assertEqual(rows["RTCM3-1005"]["status"], "differ")
        self.assertEqual(rows["RTCM3-1005"]["actual"], "0")
        self.assertEqual(rows["PQTMANTENNASTATUS"]["status"], "unsupported")
        self.assertTrue(all(prepare(body).kind == "query" for body in staged_written))
        differ = [r["set"] for r in check[1]["profile"] if r["status"] == "differ"]
        self.assertEqual(apply[1]["commands"],
                         [prepare(b).shown for b in differ] + ["$PQTMSAVEPAR*5A"])
        self.assertNotIn("$PQTMCFGRCVRMODE,W,2*29", apply[1]["commands"])
        self.assertEqual(done[1]["outcome"], "ok")
        self.assertEqual(len(done[1]["results"]), len(differ) + 1)
        self.assertEqual(snap["mode"], "Base station")
        self.assertEqual(snap["profile"]["counts"]["differ"], len(differ))

    def test_nothing_to_apply(self):
        def all_match(body):
            for item in profile.load():
                if item.query == body:
                    if item.message:
                        return [f"PQTMCFGMSGRATE,OK,1,1,{item.message}," + ",".join(item.expect)]
                    return [f"{item.name},OK," + ",".join(item.expect)]
            return simulated_reply(body)

        async def steps(client, _port, _controller):
            return await self.post(client, "/api/profile/apply", {})

        code, reply = self.exchange(steps, answer=all_match)
        self.assertEqual((code, reply["nothing"]), (200, True))

    def test_guided_steps(self):
        status = Status()
        x, y, z = rtcmdec.llh_to_ecef(0.5, 1.0, 20.0)

        async def steps(client, port, _controller):
            out = {"base": await self.post(client, "/api/steps/base-mode", {})}
            out["svin"] = await self.post(client, "/api/steps/survey-in", {})
            out["svin_120"] = await self.post(client, "/api/steps/survey-in", {"count": 120})
            out["svin_bad"] = [await self.post(client, "/api/steps/survey-in", {"count": c})
                               for c in (0, 86401, "300", 1.5, True)]
            out["no_mean"] = await self.post(client, "/api/steps/fixed", {"source": "survey"})
            for valid in (1, 2):
                for kind, payload in FrameParser().feed(nmea(
                        f"PQTMSVINSTATUS,1,1000,{valid},,01,20,300,{x:.4f},{y:.4f},{z:.4f},1.2")):
                    status.add_frame(kind, payload)
                key = "mean" if valid == 2 else "unfinished"
                out[key] = await self.post(client, "/api/steps/fixed", {"source": "survey"})
            out["llh"] = await self.post(client, "/api/steps/fixed",
                                         {"source": "llh", "lat": "0.5", "lon": 1, "height": 20})
            out["ecef"] = await self.post(client, "/api/steps/fixed",
                                          {"source": "ecef", "x": x, "y": y, "z": z})
            out["bad"] = [await self.post(client, "/api/steps/fixed", body) for body in (
                {"source": "ecef", "x": 1, "y": 2, "z": 3},
                {"source": "llh", "lat": 91, "lon": 0, "height": 0},
                {"source": "llh", "lat": 0, "lon": 0, "height": 100000},
                {"source": "llh", "lat": "nan", "lon": 0, "height": 0},
                {"source": "ecef", "x": "1e400", "y": 0, "z": 0},
                {"source": "other"})]
            out["written"] = list(port.written)
            return out

        out = self.exchange(steps, status=status)
        self.assertEqual(out["base"][1]["commands"], ["$PQTMCFGRCVRMODE,W,2*29", "$PQTMSAVEPAR*5A",
                                                      "$PQTMSRR*4B"])
        self.assertTrue(any("restart" in w for w in out["base"][1]["warnings"]))
        self.assertEqual(out["svin"][1]["commands"][0], "$PQTMCFGSVIN,W,1,300,0,0,0,0*" +
                         checksum("PQTMCFGSVIN,W,1,300,0,0,0,0"))
        self.assertIn("PQTMCFGSVIN,W,1,120,0,0,0,0", out["svin_120"][1]["commands"][0])
        self.assertEqual([code for code, _ in out["svin_bad"]], [400] * 5)
        self.assertEqual(out["no_mean"][0], 400)
        expected = f"PQTMCFGSVIN,W,2,0,0,{x:.4f},{y:.4f},{z:.4f}"
        for key in ("mean", "llh", "ecef"):
            code, reply = out[key]
            self.assertEqual(code, 200, reply)
            self.assertEqual(reply["commands"], [f"${expected}*{checksum(expected)}",
                                                 "$PQTMSAVEPAR*5A", "$PQTMSRR*4B"], key)
            self.assertTrue(any("restarts" in w for w in reply["warnings"]), key)
            details = reply["details"]
            self.assertAlmostEqual(details["lat"], 0.5, places=8)
            self.assertAlmostEqual(details["lon"], 1.0, places=8)
            self.assertAlmostEqual(details["height_m"], 20.0, places=3)
            self.assertEqual(details["ecef"], [round(v, 4) for v in (x, y, z)])
        self.assertEqual(out["unfinished"][0], 400)
        self.assertIn("not finished", out["unfinished"][1]["error"])
        self.assertFalse(any("not finished" in w for w in out["mean"][1]["warnings"]))
        self.assertEqual([code for code, _ in out["bad"]], [400] * 6)
        self.assertEqual(out["written"], [], "steps only stage; nothing is sent")


class Startup(TempDir):
    def test_startup_check_is_read_only_and_reruns_after_a_restart(self):
        async def go():
            loop = asyncio.get_running_loop()
            holder = {}
            port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply))
            controller = control.Controller(port.write, self.dir / "commands.jsonl",
                                            profile.load())
            holder["c"] = controller
            with self.assertLogs("gnss_base", "WARNING") as logged:
                controller.port_opened()
                controller.port_opened()  # once, however often
                await asyncio.sleep(0)
                await controller._startup
            first = list(port.written)
            port.written.clear()
            with self.assertLogs("gnss_base", "WARNING"):
                controller.on_nmea(f"PQTMVER,1,MODULE,{FIRMWARE},2024/04/30,10:53:07")
                await controller._startup
            await controller.stop()
            return first, list(port.written), controller.snapshot(), logged.output

        with mock.patch.object(control, "STARTUP_DELAY_S", 0.01):
            first, second, snap, logged = asyncio.run(go())
        self.assertEqual(first[:3], ["PQTMVERNO", "PQTMCFGUART,R", "PQTMCFGRCVRMODE,R"])
        self.assertEqual(len(first), 2 + len(profile.load()))
        self.assertTrue(all(prepare(body).kind == "query" for body in first + second))
        self.assertEqual(second, first)
        self.assertEqual((snap["firmware"], snap["current_uart"]), (FIRMWARE, "1"))
        self.assertIn("settings differ", "\n".join(logged))

    def test_startup_check_stops_if_the_receiver_is_silent(self):
        async def go():
            loop = asyncio.get_running_loop()
            port = FakePort(loop, lambda reply: None, answer=lambda body: [])
            controller = control.Controller(port.write, self.dir / "commands.jsonl",
                                            profile.load())
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    self.assertLogs("gnss_base", "WARNING"):
                await controller.startup_check()
            return port.written, controller.snapshot()

        written, snap = asyncio.run(go())
        self.assertEqual(written, ["PQTMVERNO"])
        self.assertIn("did not answer", snap["check_note"])


class ControllerSafety(TempDir):
    """Stage 2 review round 1, fixes 2 and 4."""

    def controller(self, loop, deliver=None, delay=0.0):
        holder = {}
        port = FakePort(loop, deliver or (lambda reply: holder["c"].on_nmea(reply)),
                        delay=delay)
        holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                         profile.load())
        return holder["c"], port

    def logged(self):
        return [json.loads(line) for line in
                (self.dir / "commands.jsonl").read_text().splitlines()]

    def test_a_port_loss_forgets_the_uart_and_refuses_staged_uart_writes(self):
        async def go():
            controller, port = self.controller(asyncio.get_running_loop())
            controller.current_uart = "3"
            staged = await controller.command("PQTMCFGUART,W,2,115200", None)
            controller.port_lost()
            out = {"uart": controller.current_uart, "staged": staged,
                   "now": await controller.command("PQTMCFGUART,W,2,115200", None)}
            with self.assertRaises(control.RequestError) as refused:
                await controller.confirm(staged["pending"], None)
            out["refused"] = str(refused.exception)
            out["written"] = list(port.written)
            await controller._queries(["PQTMCFGUART,R"], None, "test")
            out["relearned"] = controller.current_uart
            return out

        out = run_async(go)
        self.assertIn("pending", out["staged"])
        self.assertIsNone(out["uart"])
        self.assertEqual(out["now"]["outcome"], "blocked")
        self.assertIn("does not know its port", out["now"]["reason"])
        self.assertIn("Stage the change again", out["refused"])
        self.assertEqual(out["written"], [])
        self.assertEqual(out["relearned"], "1")
        entries = self.logged()
        self.assertEqual([e["outcome"] for e in entries], ["blocked", "not sent", "ok"])
        self.assertEqual(entries[1]["command"], prepare("PQTMCFGUART,W,2,115200").shown)

    def test_a_port_answer_is_not_learned_if_the_port_closed_meanwhile(self):
        async def go():
            loop = asyncio.get_running_loop()
            holder = {}

            def deliver(reply):
                holder["c"].on_nmea(reply)
                if reply.startswith("PQTMCFGUART"):  # the answer is in, then the port closes
                    holder["c"].port_lost()

            controller, _port = self.controller(loop, deliver)
            holder["c"] = controller
            (result,) = await controller._queries(["PQTMCFGUART,R"], None, "test")
            return result, controller.current_uart

        result, uart = run_async(go)
        self.assertEqual(result["outcome"], "ok")
        self.assertIsNone(uart)

    def test_confirm_sends_nothing_if_the_rules_change_while_it_waits(self):
        async def go():
            controller, port = self.controller(asyncio.get_running_loop(), delay=0.1)
            controller.current_uart = "3"
            staged = controller._stage(["PQTMCFGUART,W,2,115200", "PQTMSAVEPAR"], "t", "test")

            async def change():
                await asyncio.sleep(0.05)  # while the query holds the port
                controller.current_uart = "2"

            async def confirm():
                await asyncio.sleep(0.01)
                return await controller.confirm(staged["pending"], None)

            _query, _, done = await asyncio.gather(controller.command("PQTMVERNO", None),
                                                   change(), confirm())
            return done, port.written

        done, written = run_async(go)
        self.assertEqual(done["outcome"], "failed")
        self.assertEqual([r["outcome"] for r in done["results"]], ["not sent", "not sent"])
        self.assertIn("now blocked", done["results"][0]["error"])
        self.assertEqual(written, ["PQTMVERNO"])

    # Stage 2 review round 2, fix 1: the reproduced case.
    def test_a_port_loss_during_the_late_reply_wait_stops_a_uart_write(self):
        async def go():
            loop = asyncio.get_running_loop()
            holder = {}

            def answer(body):
                return [] if body.startswith("PQTMCFGUART,R") else simulated_reply(body)

            port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer=answer)
            controller = holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                                          profile.load())
            controller.current_uart = "3"
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    mock.patch.object(commands, "LATE_GRACE_S", 0.4):
                staged = controller._stage(["PQTMCFGUART,W,2,115200"], "t", "test")
                timed_out = await controller.command("PQTMCFGUART,R,1", None)
                loop.call_later(0.1, controller.port_lost)  # during the wait
                done = await controller.confirm(staged["pending"], None)
            return timed_out, done, port.written

        timed_out, done, written = run_async(go)
        self.assertEqual(timed_out["outcome"], "timeout")
        self.assertEqual([r["outcome"] for r in done["results"]], ["not sent"])
        self.assertEqual(written, ["PQTMCFGUART,R,1"])

    def test_the_uart_recheck_alone_stops_a_uart_write_after_the_wait(self):
        async def go():
            loop = asyncio.get_running_loop()
            controller, port = self.controller(loop)
            controller.current_uart = "3"
            staged = controller._stage(["PQTMCFGUART,W,2,115200"], "t", "test")
            controller.runner._quiet_until["PQTMCFGUART"] = loop.time() + 0.2
            loop.call_later(0.1, setattr, controller, "current_uart", "2")
            done = await controller.confirm(staged["pending"], None)
            return done, port.written

        done, written = run_async(go)
        self.assertEqual([r["outcome"] for r in done["results"]], ["not sent"])
        self.assertIn("now blocked", done["results"][0]["error"])
        self.assertEqual(written, [])

    # Fix 6: the port is learned from a query sent after a reconnect.
    def test_a_port_answer_after_a_reconnect_is_learned(self):
        async def go():
            loop = asyncio.get_running_loop()
            controller, port = self.controller(loop, delay=0.1)
            first = asyncio.ensure_future(controller.command("PQTMVERNO", None))
            await asyncio.sleep(0.01)
            uart = asyncio.ensure_future(controller._queries(["PQTMCFGUART,R"], None, "test"))
            await asyncio.sleep(0.02)
            controller.port_lost()  # while the UART query waits its turn
            await first
            (result,) = await uart
            return result, controller.current_uart

        result, uart = run_async(go)
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(uart, "1")

    def test_a_reconnect_during_a_check_checks_again(self):
        async def go():
            loop = asyncio.get_running_loop()
            holder = {"reconnected": False}

            def reconnect():
                holder["c"].port_lost()
                holder["c"].port_opened()

            def answer(body):
                if body == "PQTMCFGUART,R" and not holder["reconnected"]:
                    holder["reconnected"] = True
                    loop.call_soon_threadsafe(reconnect)
                return simulated_reply(body)

            port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer=answer)
            controller = holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                                          profile.load())
            with self.assertLogs("gnss_base", "WARNING"):
                controller.port_opened()
                await asyncio.sleep(0)
                await controller._startup
            await controller.stop()
            return port.written, controller.current_uart

        with mock.patch.object(control, "STARTUP_DELAY_S", 0.01):
            written, uart = asyncio.run(go())
        self.assertEqual(written.count("PQTMCFGUART,R"), 2)
        self.assertEqual(written.count("PQTMVERNO"), 2)
        self.assertEqual(uart, "1")


class RestartAndRecheck(TempDir):
    """0.3.1: the survey-in and fixed steps restart the receiver; a confirmed profile
    apply is followed by one automatic profile check."""

    def controller(self, loop, answer=simulated_reply):
        holder = {}
        port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer=answer)
        controller = holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                                      profile.load())
        controller.current_uart = "1"
        return controller, port

    def test_survey_in_step_saves_then_restarts(self):
        async def go():
            controller, port = self.controller(asyncio.get_running_loop())
            staged = controller.step_survey_in(120)
            done = await controller.confirm(staged["pending"], None)
            return staged, done, port.written, controller._startup

        staged, done, written, startup = run_async(go)
        self.assertEqual(staged["commands"], [prepare("PQTMCFGSVIN,W,1,120,0,0,0,0").shown,
                                              "$PQTMSAVEPAR*5A", "$PQTMSRR*4B"])
        self.assertIn("restart", staged["title"])
        self.assertTrue(any("only after a restart" in w for w in staged["warnings"]))
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(written, ["PQTMCFGSVIN,W,1,120,0,0,0,0", "PQTMSAVEPAR", "PQTMSRR"])
        self.assertNotIn("rechecking", done, "the restart's own PQTMVER starts the check")
        self.assertIsNone(startup)

    def test_survey_in_step_does_not_restart_after_a_failure(self):
        def failing(name):
            def answer(body):
                return [f"{name},ERROR,2"] if body.startswith(name) else simulated_reply(body)
            return answer

        for name, sent, outcomes in (
                ("PQTMCFGSVIN", ["PQTMCFGSVIN,W,1,300,0,0,0,0"],
                 ["error", "not sent", "not sent"]),
                ("PQTMSAVEPAR", ["PQTMCFGSVIN,W,1,300,0,0,0,0", "PQTMSAVEPAR"],
                 ["ok", "error", "not sent"])):
            async def go():
                controller, port = self.controller(asyncio.get_running_loop(), failing(name))
                staged = controller.step_survey_in()
                return await controller.confirm(staged["pending"], None), port.written

            with self.subTest(failing=name):
                done, written = run_async(go)
                self.assertEqual(done["outcome"], "failed")
                self.assertEqual(written, sent)
                self.assertEqual([r["outcome"] for r in done["results"]], outcomes)
                self.assertIn("ended error", done["results"][2]["error"])

    def test_survey_in_step_does_not_restart_after_a_timeout(self):
        def silent_save(body):
            return [] if body == "PQTMSAVEPAR" else simulated_reply(body)

        async def go():
            controller, port = self.controller(asyncio.get_running_loop(), silent_save)
            staged = controller.step_survey_in()
            with mock.patch.object(commands, "SAVE_TIMEOUT_S", 0.05):
                return await controller.confirm(staged["pending"], None), port.written

        done, written = run_async(go)
        self.assertEqual(written, ["PQTMCFGSVIN,W,1,300,0,0,0,0", "PQTMSAVEPAR"])
        self.assertEqual([r["outcome"] for r in done["results"]], ["ok", "timeout", "not sent"])

    def test_fixed_step_saves_then_restarts(self):
        x, y, z = rtcmdec.llh_to_ecef(0.5, 1.0, 20.0)

        async def go():
            controller, port = self.controller(asyncio.get_running_loop())
            staged = controller.step_fixed({"source": "ecef", "x": x, "y": y, "z": z})
            done = await controller.confirm(staged["pending"], None)
            return staged, done, port.written

        staged, done, written = run_async(go)
        self.assertIn("restart", staged["title"])
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(written[1:], ["PQTMSAVEPAR", "PQTMSRR"])
        self.assertTrue(written[0].startswith("PQTMCFGSVIN,W,2,"))

    def test_a_confirmed_apply_checks_the_profile_once(self):
        def failing_rate(body):
            if body.startswith("PQTMCFGMSGRATE,W"):
                return ["PQTMCFGMSGRATE,ERROR,2"]
            return simulated_reply(body)

        for answer, outcome in ((simulated_reply, "ok"), (failing_rate, "failed")):
            async def go():
                loop = asyncio.get_running_loop()
                controller, port = self.controller(loop, answer)
                staged = await controller.apply_profile(None)
                before = controller.profile_checked
                port.written.clear()
                done = await controller.confirm(staged["pending"], None)
                writes = list(port.written)
                with self.assertLogs("gnss_base", "WARNING"):
                    await controller._startup
                await asyncio.sleep(0.05)
                return (done, writes, port.written[len(writes):], before,
                        controller.profile_checked, controller._startup.done())

            with self.subTest(outcome=outcome), \
                    mock.patch.object(control, "RECHECK_DELAY_S", 0.01):
                done, writes, after, before, checked, idle = run_async(go)
                self.assertEqual(done["outcome"], outcome)
                self.assertTrue(done["rechecking"])
                self.assertFalse(any(prepare(b).kind == "query" for b in writes))
                queries = [item.query for item in profile.load()]
                self.assertEqual(after, ["PQTMVERNO", "PQTMCFGUART,R"] + queries,
                                 "exactly one read-only check")
                self.assertGreater(checked, before)
                self.assertTrue(idle)

    def test_the_recheck_never_runs_alongside_another_check(self):
        async def go():
            loop = asyncio.get_running_loop()
            controller, port = self.controller(loop)
            staged = await controller.apply_profile(None)
            original, counts = controller.startup_check, {"active": 0, "peak": 0, "runs": 0}

            async def counted():
                counts["active"] += 1
                counts["runs"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
                try:
                    await original()
                finally:
                    counts["active"] -= 1

            controller.startup_check = counted
            with self.assertLogs("gnss_base", "WARNING"):
                controller.port_opened()
                while not controller._checking:
                    await asyncio.sleep(0.001)
                done = await controller.confirm(staged["pending"], None)
                controller.schedule_check()  # and again, while it runs
                await controller._startup
            await asyncio.sleep(0.05)
            return done, counts, controller._startup.done(), controller.snapshot()

        with mock.patch.object(control, "STARTUP_DELAY_S", 0.01), \
                mock.patch.object(control, "RECHECK_DELAY_S", 0.01):
            done, counts, idle, snap = asyncio.run(go())
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(counts["peak"], 1, "never two checks at once")
        self.assertEqual(counts["runs"], 2, "the running check, then one more")
        self.assertTrue(idle)
        self.assertFalse(snap["checking"])

    def test_page_shows_the_recheck_and_survey_state(self):
        script = (Path(control.__file__).parent / "static" / "control.js").read_text()
        page = (Path(control.__file__).parent / "static" / "index.html").read_text()
        self.assertIn("j.rechecking", script)
        self.assertIn("r.checking", script)
        self.assertIn("Valid=", script)
        self.assertIn("CfgDur", page)
        self.assertNotIn("restart the receiver.", page)


class ReviewFixes031(TempDir):
    """0.3.1 review: step 5 refuses an unfinished survey-in mean; a check that cannot
    complete marks the profile stale; a stale survey state is not green; the check
    holds the port from start to end."""

    def controller(self, loop, answer=simulated_reply, status=None, delay=0.0):
        holder = {}
        port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer=answer,
                        delay=delay)
        controller = holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                                      profile.load(), status)
        controller.current_uart = "1"
        return controller, port

    def test_fixed_step_refuses_an_unfinished_survey_mean(self):
        x, y, z = rtcmdec.llh_to_ecef(0.5, 1.0, 20.0)
        clock = Clock()
        status = Status(clock=clock)
        controller = control.Controller(lambda data: None, self.dir / "commands.jsonl",
                                        profile.load(), status, clock=clock)
        controller.current_uart = "1"

        def survey(valid):
            for kind, payload in FrameParser().feed(nmea(
                    f"PQTMSVINSTATUS,1,1000,{valid},,01,20,300,{x:.4f},{y:.4f},{z:.4f},1.2")):
                status.add_frame(kind, payload)

        for valid, state in (("0", "Not running"), ("1", "In progress"), ("", "Unknown"),
                             ("7", "Unknown")):
            with self.subTest(valid=valid):
                survey(valid)
                with self.assertRaises(control.RequestError) as refused:
                    controller.step_fixed({"source": "survey"})
                text = str(refused.exception)
                self.assertIn("not finished", text)
                self.assertIn(f"state: {state}", text)
                self.assertIn(f"Valid={valid or '—'}", text)
                self.assertIn("Obs 20 of CfgDur 300", text)
                self.assertIn("mean accuracy 1.2 m", text)
        survey("2")
        staged = controller.step_fixed({"source": "survey"})
        self.assertTrue(staged["commands"][0].startswith("$PQTMCFGSVIN,W,2,0,0,"))
        self.assertEqual(staged["details"]["from"], "survey")
        clock.now += control.SURVEY_MAX_AGE_S + 1  # finished, but no longer fresh
        with self.assertRaises(control.RequestError) as stale:
            controller.step_fixed({"source": "survey"})
        self.assertIn("stale", str(stale.exception))
        survey("2")
        status.add_frame("nmea", "PQTMSVINSTATUS,1,1000,2,,01,20,300,1.0,2.0,3.0,1.2")
        with self.assertRaises(control.RequestError) as implausible:
            controller.step_fixed({"source": "survey"})
        self.assertIn("not near the Earth's surface", str(implausible.exception))
        self.assertIn("pending", controller.step_fixed({"source": "ecef", "x": x, "y": y,
                                                        "z": z}), "coordinates unaffected")

    def test_a_check_that_cannot_complete_marks_the_profile_stale(self):
        silent = set()

        def answer(body):
            return [] if body in silent else simulated_reply(body)

        async def go():
            controller, port = self.controller(asyncio.get_running_loop(), answer)
            out = {}
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    self.assertLogs("gnss_base", "WARNING"):
                await controller.startup_check()
                out["fresh"] = controller.snapshot()["profile"]
                for body in ("PQTMVERNO", "PQTMCFGUART,R"):
                    silent.clear()
                    silent.add(body)
                    port.written.clear()
                    await controller.startup_check()
                    out[body] = (controller.snapshot(), list(port.written))
                silent.clear()
                await controller.startup_check()
                out["again"] = controller.snapshot()
            return out

        out = run_async(go)
        self.assertIsNone(out["fresh"]["stale"])
        rows = out["fresh"]["results"]
        self.assertEqual(len(rows), len(profile.load()))
        for body, sent in (("PQTMVERNO", ["PQTMVERNO"]),
                           ("PQTMCFGUART,R", ["PQTMVERNO", "PQTMCFGUART,R"])):
            with self.subTest(silent=body):
                snap, written = out[body]
                self.assertEqual(written, sent, "the profile queries are skipped")
                stale = snap["profile"]["stale"]
                self.assertIn(body, stale["reason"])
                self.assertGreaterEqual(stale["age_s"], 0)
                self.assertEqual(snap["profile"]["results"], rows, "kept, but marked stale")
                self.assertIn(body, snap["check_note"])
        self.assertIsNone(out["again"]["profile"]["stale"], "a full check clears it")
        self.assertEqual(out["again"]["check_note"], "")
        script = (Path(control.__file__).parent / "static" / "control.js").read_text()
        page = (Path(control.__file__).parent / "static" / "index.html").read_text()
        self.assertIn("Check failed at", script)
        self.assertNotIn("re-check failed", script)
        self.assertIn('classList.toggle("stale"', script)
        self.assertIn("table.stale{", page)

    def test_a_check_that_fails_outright_marks_the_profile_stale(self):
        async def go():
            controller, _port = self.controller(asyncio.get_running_loop())

            async def broken():
                raise OSError("boom")

            controller.startup_check = broken
            with self.assertLogs("gnss_base", "ERROR"):
                controller.schedule_check(0)
                await controller._startup
            return controller.snapshot()["profile"]["stale"]

        stale = run_async(go)
        self.assertIn("OSError", stale["reason"])

    def test_a_stale_survey_state_is_not_green(self):
        page = (Path(control.__file__).parent / "static" / "index.html").read_text()
        line = next(x for x in page.splitlines() if '$("svin").className' in x)
        self.assertLess(line.index("svinStale?"), line.index('"good"'),
                        "stale is decided before the Finished state can make it green")
        self.assertIn('svinStale?"bad"', line, "shown like a stale fix")

    def test_a_batch_confirmed_during_a_check_runs_after_it(self):
        async def go():
            controller, port = self.controller(asyncio.get_running_loop(), delay=0.01)
            staged = controller.step_base_mode()
            with self.assertLogs("gnss_base", "WARNING"):
                check = asyncio.ensure_future(controller.startup_check())
                while "PQTMVERNO" not in port.written:
                    await asyncio.sleep(0.001)
                done = await controller.confirm(staged["pending"], None)
                await check
            return done, port.written

        done, written = run_async(go)
        queries = [item.query for item in profile.load()]
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(written, ["PQTMVERNO", "PQTMCFGUART,R"] + queries +
                         ["PQTMCFGRCVRMODE,W,2", "PQTMSAVEPAR", "PQTMSRR"])

    def test_a_quiet_receiver_ends_the_check_at_the_first_timeout(self):
        queries = [item.query for item in profile.load()]
        quiet = queries[2]

        def answer(body):
            return [] if body == quiet else simulated_reply(body)

        async def go():
            controller, port = self.controller(asyncio.get_running_loop(), answer)
            staged = controller.step_base_mode()
            with mock.patch.object(commands, "TIMEOUT_S", 0.1), \
                    self.assertLogs("gnss_base", "WARNING"):
                check = asyncio.ensure_future(controller.startup_check())
                while "PQTMVERNO" not in port.written:
                    await asyncio.sleep(0.001)
                done = await controller.confirm(staged["pending"], None)
                await check
            return done, port.written, controller.snapshot()

        done, written, snap = run_async(go)
        self.assertEqual(written, ["PQTMVERNO", "PQTMCFGUART,R"] + queries[:3] +
                         ["PQTMCFGRCVRMODE,W,2", "PQTMSAVEPAR", "PQTMSRR"])
        self.assertEqual(done["outcome"], "ok")
        statuses = [row["status"] for row in snap["profile"]["results"]]
        self.assertEqual(len(statuses), len(queries))
        self.assertTrue(all(s == "error" for s in statuses[2:]))
        self.assertIn("got no answer", snap["check_note"])

    def test_the_held_port_runs_batches_only_while_held(self):
        async def go():
            loop = asyncio.get_running_loop()
            controller, port = self.controller(loop)
            runner = controller.runner
            async with runner.hold() as send:
                waiting = asyncio.ensure_future(runner.run([prepare("PQTMVERNO")]))
                (first,) = await send([prepare("PQTMCFGUART,R")])
                await asyncio.sleep(0.02)
                held_order = list(port.written)
            await waiting
            with self.assertRaises(RuntimeError):
                await send([prepare("PQTMVERNO")])
            return first, held_order, port.written

        first, held_order, written = run_async(go)
        self.assertEqual(first["outcome"], "ok")
        self.assertEqual(held_order, ["PQTMCFGUART,R"])
        self.assertEqual(written, ["PQTMCFGUART,R", "PQTMVERNO"])


class ReviewFixes031Round2(TempDir):
    """0.3.1 review, round 2: a profile check that did not get an answer to every query
    stays stale; an unverified PQTMCFGUART,R answer leaves the app's port unknown."""

    def controller(self, loop, answer=simulated_reply):
        holder = {}
        port = FakePort(loop, lambda reply: holder["c"].on_nmea(reply), answer=answer)
        controller = holder["c"] = control.Controller(port.write, self.dir / "commands.jsonl",
                                                      profile.load())
        controller.current_uart = "1"
        return controller, port

    def check(self, answer=simulated_reply, during=None):
        """A full check, then a check with `answer` (`during(controller, port, body)`
        runs as each body is written). Returns both snapshots and what was written."""
        async def go():
            controller, port = self.controller(asyncio.get_running_loop())
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    self.assertLogs("gnss_base", "WARNING"):
                await controller.startup_check()
                first = controller.snapshot()
                port.answer, port.written = answer, []
                if during is not None:
                    write = port.write

                    def hooked(data):
                        during(controller, port, sentence_body(data))
                        return write(data)
                    controller.runner._write = hooked
                await controller.startup_check()
            return first, controller.snapshot(), port.written

        return run_async(go)

    def test_a_full_check_with_an_error_reply_is_not_stale(self):
        first, again, _written = self.check()
        for snap in (first, again):
            statuses = [row["status"] for row in snap["profile"]["results"]]
            self.assertIn("unsupported", statuses, "the simulated ERROR,1 is an answer")
            self.assertNotIn("error", statuses)
            self.assertIsNone(snap["profile"]["stale"])
            self.assertEqual(snap["check_note"], "")
            self.assertEqual(snap["current_uart"], "1")

    def test_a_timeout_mid_check_leaves_the_profile_stale(self):
        quiet = profile.load()[3].query

        def answer(body):
            return [] if body == quiet else simulated_reply(body)

        _first, snap, _written = self.check(answer)
        stale = snap["profile"]["stale"]
        self.assertEqual(stale["kind"], "incomplete")
        self.assertEqual(stale["reason"], f"{prepare(quiet).shown} got no answer")
        self.assertIsInstance(stale["time"], float)
        rows = snap["profile"]["results"]
        self.assertEqual(len(rows), len(profile.load()), "the rows are kept, with outcomes")
        self.assertTrue(all(r["status"] == "error" for r in rows[3:]))
        self.assertIn("got no answer", snap["check_note"])

    def test_a_port_closed_mid_check_leaves_the_profile_stale(self):
        closing = profile.load()[2].query

        def during(controller, port, body):
            if body == closing:  # the port drops just before this query is written
                port.closed = True
                controller.port_lost()

        _first, snap, written = self.check(during=during)
        stale = snap["profile"]["stale"]
        self.assertEqual(stale["kind"], "incomplete")
        self.assertIn("port closed", stale["reason"])
        self.assertIsNone(snap["current_uart"])
        self.assertEqual(len(snap["profile"]["results"]), len(profile.load()))

    def test_an_unanswered_query_without_a_port_change_is_stale(self):
        failing = profile.load()[1].query

        def during(controller, port, body):
            port.closed = body == failing

        _first, snap, _written = self.check(during=during)
        stale = snap["profile"]["stale"]
        self.assertEqual(stale["kind"], "incomplete")
        self.assertIn("not connected", stale["reason"])

    def test_an_unverified_uart_is_forgotten_and_the_check_is_incomplete(self):
        queries = [item.query for item in profile.load()]
        for case, reply in (("error", ["PQTMCFGUART,ERROR,3"]),
                            ("malformed", ["PQTMCFGUART,OK,1,460800"]),
                            ("wrong port", ["PQTMCFGUART,OK,7,460800,8,0,1,0"]),
                            ("no reply", [])):
            with self.subTest(case):
                def answer(body, reply=reply):
                    return reply if body == "PQTMCFGUART,R" else simulated_reply(body)

                first, snap, written = self.check(answer)
                self.assertEqual(first["current_uart"], "1")
                self.assertIsNone(snap["current_uart"], "the older value is not kept")
                stale = snap["profile"]["stale"]
                self.assertIn("PQTMCFGUART,R", stale["reason"])
                self.assertIn("PQTMCFGUART,R", snap["check_note"])
                if case == "no reply":
                    self.assertEqual(stale["kind"], "failed")
                    self.assertEqual(written, ["PQTMVERNO", "PQTMCFGUART,R"])
                else:
                    self.assertEqual(stale["kind"], "incomplete")
                    self.assertIn("unverified", stale["reason"])
                    self.assertEqual(written, ["PQTMVERNO", "PQTMCFGUART,R"] + queries,
                                     "the profile is still read")

    def test_uart_writes_stay_refused_after_an_unverified_answer(self):
        async def go():
            controller, port = self.controller(asyncio.get_running_loop())
            port.answer = (lambda body: ["PQTMCFGUART,ERROR,3"] if body == "PQTMCFGUART,R"
                           else simulated_reply(body))
            with mock.patch.object(commands, "TIMEOUT_S", 0.05), \
                    self.assertLogs("gnss_base", "WARNING"):
                await controller.startup_check()
            return await controller.command("PQTMCFGUART,W,2,115200", None)

        reply = run_async(go)
        self.assertEqual(reply["outcome"], "blocked", "with port 1 known, this would confirm")
        self.assertIn("does not know its port", reply["reason"])

    def test_the_page_words_a_failed_and_an_incomplete_check(self):
        script = (Path(control.__file__).parent / "static" / "control.js").read_text()
        self.assertIn("Incomplete check at", script)
        self.assertIn("Check failed at", script)
        self.assertIn("st.time * 1000", script, "the time comes from the app")
        self.assertNotIn("st.age_s", script)


class SerialWrite(unittest.TestCase):
    def test_write_needs_an_open_port(self):
        reader = SerialReader("/dev/null", 460800, lambda d: None, lambda c, e: None,
                              check=lambda: "")
        with self.assertRaises(PortClosed):
            reader.write(b"$PQTMVERNO*58\r\n")
        port = mock.Mock()
        reader._open = port
        reader.write(b"$PQTMVERNO*58\r\n")
        port.write.assert_called_once_with(b"$PQTMVERNO*58\r\n")


class EndToEnd(unittest.TestCase):
    """Run the real program against a simulated serial port."""

    def test_reads_logs_every_byte_and_writes_only_queries_and_confirmed(self):
        """Stage 2: the app answers to a simulated receiver. It sends read-only queries
        on its own, and a change only after confirmation; a blocked command never."""
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        master, slave = pty.openpty()
        tty.setraw(master)
        tty.setraw(slave)
        by_id = directory / "by-id"
        by_id.mkdir()
        (by_id / "usb-test_USB_Dual_Serial_0-if00").symlink_to(os.ttyname(slave))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        options = directory / "options.json"
        options.write_text(json.dumps({"device": os.ttyname(slave), "baud": 460800,
                                       "log_dir": str(directory / "logs"), "min_free_gb": 0}))
        process = subprocess.Popen(
            [sys.executable, "-m", "gnss_base", "--options", str(options), "--host", "127.0.0.1",
             "--port", str(port), "--allow-any-client", "--by-id-dir", str(by_id)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        receiver = SimulatedReceiver(master)
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as reply:
                return json.load(reply)

        status = None
        try:
            receiver.start()
            stream = GGA + GST + GSV + rtcm(1005) + SVIN
            deadline = time.time() + 30
            while time.time() < deadline:
                receiver.send(stream)
                time.sleep(0.2)
                try:
                    status = get("/api/status")
                except OSError:
                    continue
                checked = status["receiver"]["profile"]["checked_age_s"] is not None
                if status["nmea"].get("GGA", 0) > 3 and status["rtcm"] and checked:
                    break
            with urllib.request.urlopen(base + "/", timeout=2) as reply:
                self.assertIn(b"GNSS Base", reply.read())
            track = get("/api/track")
            with urllib.request.urlopen(base + "/vendor/leaflet/leaflet.js", timeout=2) as reply:
                self.assertIn(b"Leaflet 1.9.4", reply.read(200))
            before = list(receiver.heard)
            staged = post_json(base + "/api/command", {"command": "pqtmcfgrcvrmode, w, 2"})
            refused = post_json(base + "/api/command", {"command": "$PQTMRESTOREPAR*13"})
            time.sleep(0.5)
            unconfirmed = list(receiver.heard)
            done = post_json(base + "/api/command/confirm", {"id": staged["pending"]})
            again = post_json(base + "/api/command/confirm", {"id": staged["pending"]})
            query = post_json(base + "/api/command", {"command": "PQTMCFGRTCM,R"})
            history = get("/api/commands")["entries"]
            status = get("/api/status")
            receiver.send(stream)
            time.sleep(1)
        finally:
            process.terminate()
            process.wait(timeout=10)
            receiver.stop()
        self.assertTrue(status and status["connected"], status and status["last_error"])
        self.assertEqual(status["fix"]["name"], "Standalone")
        self.assertEqual((status["fix"]["lat"], status["fix"]["lon"]), (0.0, 0.0))
        self.assertEqual(status["fix"]["lat_sigma_m"], 0.012)
        self.assertEqual(track["reference"], {"lat": 0.0, "lon": 0.0, "height_m": 0.0})
        self.assertGreaterEqual(len(track["points"]), 1)
        self.assertEqual(status["parser"]["bad_frames"], 0)
        # The receiver section, from the startup check
        rcv = status["receiver"]
        self.assertEqual((rcv["firmware"], rcv["mode"], rcv["current_uart"]),
                         ("LG290P03AANR01A03S", "Base station", "1"))
        results = {r["label"]: r["status"] for r in rcv["profile"]["results"]}
        self.assertEqual(results["RTCM: MSM type, elevation mask, ephemeris output"], "match")
        self.assertEqual(results["RTCM3-1005"], "differ")
        self.assertEqual(results["PQTMANTENNASTATUS"], "unsupported")
        # Nothing but queries before the confirmation; the change only after it.
        self.assertTrue(before and all(prepare(b).kind == "query" for b in before), before)
        self.assertEqual(staged["commands"], ["$PQTMCFGRCVRMODE,W,2*29"])
        self.assertNotIn("PQTMCFGRCVRMODE,W,2", unconfirmed)
        self.assertEqual(refused["outcome"], "blocked")
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(done["results"][0]["replies"], ["$PQTMCFGRCVRMODE,OK"])
        self.assertIn("expired", again["error"])
        self.assertEqual(query["result"]["replies"], ["$PQTMCFGRTCM,OK,4,0,-90.0,07,06,1,0"])
        heard = receiver.heard
        self.assertEqual(heard.count("PQTMCFGRCVRMODE,W,2"), 1)
        self.assertNotIn("PQTMRESTOREPAR", heard)
        self.assertEqual(receiver.bad_lines, [])
        for body in heard:
            self.assertTrue(prepare(body).kind == "query" or body == "PQTMCFGRCVRMODE,W,2", body)
        # Every sentence sent is in the command log, and the refusal too.
        log = [json.loads(line) for line in
               (directory / "logs" / "commands.jsonl").read_text().splitlines()]
        sent = [e["command"] for e in log if e["outcome"] not in ("blocked", "not sent")]
        self.assertEqual([c[1:].split("*")[0] for c in sent], heard)
        self.assertEqual([e["outcome"] for e in log if e["command"].startswith("$PQTMRESTORE")],
                         ["blocked"])
        self.assertEqual(history[-1]["command"], "$PQTMCFGRTCM,R*2C")
        logs = list((directory / "logs" / "raw").glob("*.raw"))
        self.assertEqual(len(logs), 1)
        logged = logs[0].read_bytes()
        # Bytes sent before the port opened are lost by the pty, not the app;
        # everything after the first logged byte must be there, in order.
        self.assertTrue(receiver.sent.endswith(logged), "log must match the stream byte for byte")
        self.assertGreater(len(logged), len(receiver.sent) // 2)

    # Review round 2, must-fix 1
    def test_receiver_missing_at_startup_is_opened_when_it_appears(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        by_id = directory / "by-id"
        by_id.mkdir()
        (by_id / "usb-test_USB_Dual_Serial_0-if00").symlink_to(os.ttyname(slave))
        device = directory / "receiver"  # not there yet
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        options = directory / "options.json"
        options.write_text(json.dumps({"device": str(device), "log_dir": str(directory / "logs"),
                                       "min_free_gb": 0}))
        process = subprocess.Popen(
            [sys.executable, "-m", "gnss_base", "--options", str(options), "--host", "127.0.0.1",
             "--port", str(port), "--allow-any-client", "--by-id-dir", str(by_id)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def wait_for(condition):
            deadline = time.time() + 15
            while time.time() < deadline:
                time.sleep(0.2)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                                timeout=2) as reply:
                        status = json.load(reply)
                except OSError:
                    continue
                if condition(status):
                    return status
            return None

        try:
            missing = wait_for(lambda s: "does not exist" in s["last_error"])
            device.symlink_to(os.ttyname(slave))
            connected = wait_for(lambda s: s["connected"])
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.assertIsNotNone(missing, "the missing receiver must be reported")
        self.assertIsNotNone(connected, "the app must keep retrying until the receiver appears")


if __name__ == "__main__":
    unittest.main()
