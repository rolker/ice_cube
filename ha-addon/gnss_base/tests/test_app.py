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
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from gnss_base import rawlog, receiver
from gnss_base.__main__ import ErrorReporter, check_device, housekeeping, load_settings
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
        self.assertEqual(survey, {"state": "In progress", "observations": 42, "target": 300,
                                  "accuracy_m": 1.25})
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


class EndToEnd(unittest.TestCase):
    """Run the real program against a simulated serial port."""

    def test_reads_logs_every_byte_reports_and_stays_silent(self):
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
        sent = b""
        heard = b""
        status = None
        try:
            stream = GGA + GST + GSV + rtcm(1005) + SVIN
            deadline = time.time() + 15
            while time.time() < deadline:
                os.write(master, stream)
                sent += stream
                time.sleep(0.2)
                while select.select([master], [], [], 0)[0]:
                    heard += os.read(master, 4096)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                                timeout=2) as reply:
                        status = json.load(reply)
                except OSError:
                    continue
                if status["nmea"].get("GGA", 0) > 3 and status["rtcm"]:
                    break
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as reply:
                self.assertIn(b"GNSS Base", reply.read())
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/track",
                                        timeout=2) as reply:
                track = json.load(reply)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/vendor/leaflet/leaflet.js",
                                        timeout=2) as reply:
                self.assertIn(b"Leaflet 1.9.4", reply.read(200))
            time.sleep(1)
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.assertTrue(status and status["connected"], status and status["last_error"])
        self.assertEqual(status["fix"]["name"], "Standalone")
        self.assertEqual((status["fix"]["lat"], status["fix"]["lon"]), (0.0, 0.0))
        self.assertEqual(status["fix"]["lat_sigma_m"], 0.012)
        self.assertEqual(track["reference"], {"lat": 0.0, "lon": 0.0, "height_m": 0.0})
        self.assertGreaterEqual(len(track["points"]), 1)
        self.assertEqual(status["parser"]["bad_frames"], 0)
        self.assertEqual(heard, b"", "the app must never write to the receiver")
        logs = list((directory / "logs" / "raw").glob("*.raw"))
        self.assertEqual(len(logs), 1)
        logged = logs[0].read_bytes()
        # Bytes sent before the port opened are lost by the pty, not the app;
        # everything after the first logged byte must be there, in order.
        self.assertTrue(sent.endswith(logged), "log must match the stream byte for byte")
        self.assertGreater(len(logged), len(sent) // 2)

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
