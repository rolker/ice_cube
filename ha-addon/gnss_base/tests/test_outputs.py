"""Stage 3a: RTCM hub, output filter, local NTRIP caster and upload (RTK2go push).

Run from ha-addon/gnss_base with: python -m unittest tests.test_outputs

Every network test runs against localhost only: a fake caster in this process stands in
for RTK2go, which must never be used for testing. A guard makes any name lookup other
than localhost fail the test.
"""
import ast
import asyncio
import base64
import contextlib
import io
import json
import logging
import math
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from gnss_base import __version__, caster as caster_module
from gnss_base import upload as upload_module
from gnss_base.__main__ import DEFAULTS, Pipeline, load_settings, output_settings
from gnss_base.caster import Caster, CasterSettings, Client, parse_basic, peer_address
from gnss_base.control import Controller
from gnss_base import profile
from gnss_base.frames import crc24q, nmea_checksum
from gnss_base.hub import ConnectionLog, OutputFilter, RtcmHub, Secret, is_rtcm_frame
from gnss_base.status import Status
from gnss_base.upload import Uploader, UploadSettings, classify
from gnss_base.web import STATIC

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "S3ntinel-pw-7f3a9c"   # a made-up password that must appear nowhere but the socket
CASTER_SENTINEL = "Caster-pw-5e1d"


def rtcm(number, size=19, fill=0):
    payload = bytes([number >> 4, (number & 15) << 4]) + bytes([fill]) * (size - 2)
    head = bytes([0xD3, size >> 8, size & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


def nmea(body):
    return f"${body}*{nmea_checksum(body.encode()):02X}\r\n".encode()


class FakeTime:
    """A monotonic clock, a wall clock and a sleep that advances them. If `hub` is set,
    every sleep also marks RTCM as just received, as if the receiver kept sending."""

    def __init__(self, hub=None):
        self.now = 1000.0
        self.start_wall = 1_800_000_000.0
        self.hub = hub
        self.sleeps = []
        self.hold = False  # while set, a sleep waits instead of advancing the clock

    def clock(self):
        return self.now

    def wall(self):
        return self.start_wall + self.now - 1000.0

    async def sleep(self, seconds):
        while self.hold:
            await asyncio.sleep(0.005)
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)
        if self.hub is not None:
            self.hub.last_frame = self.now
        await asyncio.sleep(0.001)


async def until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class LocalOnly(unittest.IsolatedAsyncioTestCase):
    """Name lookups other than localhost fail: tests never reach rtk2go.com or any
    other outside host."""

    def setUp(self):
        real = socket.getaddrinfo

        def guarded(host, *args, **kwargs):
            if host not in ("127.0.0.1", "127.0.0.2", "localhost", "::1", None):
                raise AssertionError(f"test tried to resolve {host!r}")
            return real(host, *args, **kwargs)

        patcher = mock.patch("socket.getaddrinfo", guarded)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.events = ConnectionLog(self.dir / "caster.jsonl")

    def log_lines(self):
        path = self.dir / "caster.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------- hub and filter

class Hub(unittest.TestCase):
    def test_frames_reach_every_subscriber_in_order_and_a_failing_one_is_counted(self):
        hub = RtcmHub(clock=lambda: 5.0)
        got_a, got_b = [], []
        hub.subscribe("a", lambda frame, number: got_a.append(number))

        def broken(_frame, _number):
            raise RuntimeError("broken subscriber")

        hub.subscribe("broken", broken)
        hub.subscribe("b", lambda frame, number: got_b.append(frame))
        frames = [rtcm(1005), rtcm(1074, 40), rtcm(1230)]
        with self.assertLogs("gnss_base", "ERROR"):
            for frame in frames:
                hub.publish(frame)
        self.assertEqual(got_a, [1005, 1074, 1230])
        self.assertEqual(got_b, frames)
        snapshot = hub.snapshot()
        self.assertEqual(snapshot["subscriber_errors"], {"broken": 3})
        self.assertEqual((snapshot["frames"], snapshot["last_rtcm_age_s"]), (3, 0.0))

    def test_only_whole_rtcm_frames_are_published(self):
        hub = RtcmHub()
        got = []
        hub.subscribe("a", lambda frame, number: got.append(frame))
        for junk in (nmea("GNGGA,1,2"), b"", b"\xd3\x00", rtcm(1005)[:-1], b"\xd3\xff" + bytes(20),
                     "text", None):
            hub.publish(junk)
        self.assertEqual((got, hub.rejected), ([], 7))
        self.assertIsNone(hub.age_s())
        self.assertFalse(hub.flowing(10))

    def test_pipeline_publishes_checked_rtcm_after_the_log_and_never_nmea(self):
        order = []

        class Log:
            def write(self, data):
                order.append(("log", len(data)))

        hub = RtcmHub()
        hub.subscribe("t", lambda frame, number: order.append(("rtcm", number)))
        pipeline = Pipeline(Status(), Log(), hub=hub)
        bad = bytearray(rtcm(1077))
        bad[10] ^= 1  # CRC no longer matches
        data = nmea("GNGGA,1,2,3") + rtcm(1005) + bytes(bad) + nmea("PQTMVERNO,X") + rtcm(1230)
        pipeline.on_data(data)
        self.assertEqual(order, [("log", len(data)), ("rtcm", 1005), ("rtcm", 1230)])

    def test_a_failing_output_does_not_stop_logging(self):
        logged = []

        class Log:
            def write(self, data):
                logged.append(data)

        hub = RtcmHub()
        hub.subscribe("bad", mock.Mock(side_effect=OSError("boom")))
        pipeline = Pipeline(Status(), Log(), hub=hub)
        with self.assertLogs("gnss_base", "ERROR"):
            for _ in range(3):
                pipeline.on_data(rtcm(1005))
        self.assertEqual(len(logged), 3)


class Filter(unittest.TestCase):
    def test_station_messages_are_thinned_to_ten_seconds_first_at_once(self):
        clock = FakeTime()
        output = OutputFilter(clock.clock)
        passed = []
        for second in range(31):
            for number in (1005, 1033, 1074, 1084, 1094, 1124, 1230):
                if output.accept(rtcm(number)):
                    passed.append((second, number))
            if second % 7 == 0:
                if output.accept(rtcm(1019)):
                    passed.append((second, 1019))
            clock.now += 1.0
        station = [(s, n) for s, n in passed if n in (1005, 1033)]
        self.assertEqual(station, [(0, 1005), (0, 1033), (10, 1005), (10, 1033),
                                   (20, 1005), (20, 1033), (30, 1005), (30, 1033)])
        for number in (1074, 1084, 1094, 1124, 1230):
            self.assertEqual(sum(1 for _, n in passed if n == number), 31, number)
        self.assertEqual([s for s, n in passed if n == 1019], [0, 7, 14, 21, 28])
        self.assertEqual(output.thinned, 2 * 31 - 8)

    def test_a_slightly_early_station_message_still_passes(self):
        clock = FakeTime()
        output = OutputFilter(clock.clock)
        self.assertTrue(output.accept(rtcm(1005)))
        clock.now += 9.6
        self.assertTrue(output.accept(rtcm(1005)))
        clock.now += 9.0
        self.assertFalse(output.accept(rtcm(1005)))

    def test_each_output_has_its_own_filter(self):
        clock = FakeTime()
        first, second = OutputFilter(clock.clock), OutputFilter(clock.clock)
        self.assertTrue(first.accept(rtcm(1005)))
        clock.now += 3
        self.assertFalse(first.accept(rtcm(1005)))
        self.assertTrue(second.accept(rtcm(1005)), "a new client gets the station message at once")

    def test_non_rtcm_is_dropped(self):
        output = OutputFilter()
        for junk in (nmea("GNGGA,1"), nmea("PQTMSVINSTATUS,1"), b"\xd3\x00\x13", b"hello",
                     rtcm(1074) + b"x"):
            self.assertFalse(output.accept(junk), junk)
        self.assertEqual(output.dropped, 5)
        self.assertTrue(is_rtcm_frame(rtcm(4095, 100)))


class SecretValues(unittest.TestCase):
    def test_a_secret_never_prints(self):
        secret = Secret(SENTINEL)
        for text in (repr(secret), str(secret), f"{secret}", f"{secret!r}",
                     repr(UploadSettings(mountpoint="M", password=secret)),
                     repr(CasterSettings(password=secret)), "%s" % (secret,)):
            self.assertNotIn(SENTINEL, text)
        self.assertEqual(secret.reveal(), SENTINEL)
        self.assertEqual(secret.scrub(f"x {SENTINEL} y"), "x *** y")
        self.assertTrue(secret)
        self.assertFalse(Secret(""))
        with self.assertRaises(TypeError):
            import pickle
            pickle.dumps(secret)


# ---------------------------------------------------------------- caster

class FakeWriter:
    """A StreamWriter stand-in whose buffered size the test sets."""

    def __init__(self):
        self.data = bytearray()
        self.buffer = 0
        self.aborted = False
        self.transport = self

    def get_write_buffer_size(self):
        return self.buffer

    def is_closing(self):
        return self.aborted

    def write(self, data):
        self.data += data

    def abort(self):
        self.aborted = True


class CasterClient(unittest.TestCase):
    def test_a_slow_client_skips_frames_and_is_dropped_after_thirty_seconds(self):
        clock = FakeTime()
        writer = FakeWriter()
        client = Client(writer, "192.0.2.5", "agent", "Ntrip/1.0", "", clock.clock)
        client.send(rtcm(1074))
        writer.buffer = caster_module.CLIENT_BUFFER + 1
        for _ in range(5):
            client.send(rtcm(1074))
        self.assertEqual((client.frames_sent, client.frames_skipped), (1, 5))
        self.assertEqual(bytes(writer.data), rtcm(1074))
        self.assertFalse(client.stalled(clock.now))
        clock.now += 20
        self.assertFalse(client.stalled(clock.now))
        writer.buffer -= 100  # some progress: the clock starts again
        self.assertFalse(client.stalled(clock.now))
        clock.now += 29
        self.assertFalse(client.stalled(clock.now))
        clock.now += 2
        self.assertTrue(client.stalled(clock.now))

    def test_rover_gga_is_noted_and_dropped(self):
        clock = FakeTime()
        client = Client(FakeWriter(), "192.0.2.5", "", "Ntrip/1.0", "", clock.clock)
        client.received(nmea("GPGGA,120000.00,0000.00,N,00000.00,E,4,12,0.5,0,M,0,M,1.0,0000")[:20])
        self.assertIsNone(client.last_gga)
        client.received(nmea("GPGGA,120000.00,0000.00,N,00000.00,E,4,12,0.5,0,M,0,M,1.0,0000")[20:])
        self.assertEqual((client.last_gga, client.gga_quality), (clock.now, 4))
        client.received(b"x" * 100_000)  # no line end: kept short
        self.assertLessEqual(len(client._partial), caster_module.CLIENT_READ)
        self.assertEqual(client.snapshot(clock.now)["gga_quality"], 4)

    def test_basic_login_parsing(self):
        good = "Basic " + base64.b64encode(b"user:pa:ss").decode()
        self.assertEqual(parse_basic(good), ("user", "pa:ss"))
        no_colon = "Basic " + base64.b64encode(b"nocolon").decode()
        for bad in (None, "", "Bearer x", "Basic !!!", no_colon):
            self.assertIsNone(parse_basic(bad), bad)


async def ntrip_get(port, path="/BASE", login=None, headers=b"", read_until=None, timeout=3,
                    local=None):
    """Send a request (from address `local` if given); return (reader, writer, first bytes
    of the reply)."""
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, local_addr=(local, 0) if local else None)
    request = f"GET {path} HTTP/1.0\r\nUser-Agent: NTRIP TestClient/1.0\r\n".encode()
    if login is not None:
        request += b"Authorization: Basic " + base64.b64encode(login.encode()) + b"\r\n"
    writer.write(request + headers + b"\r\n")
    data = b""
    with contextlib.suppress(asyncio.TimeoutError):
        while read_until is None or read_until not in data:
            chunk = await asyncio.wait_for(reader.read(4096), timeout)
            if not chunk:
                break
            data += chunk
    return reader, writer, data


class CasterBase(LocalOnly):
    async def start(self, **options):
        self.time = FakeTime()
        self.hub = RtcmHub(clock=self.time.clock)
        settings = CasterSettings(host="127.0.0.1", port=0, **options)
        self.caster = Caster(settings, self.hub, self.events, "9.9.9", clock=self.time.clock,
                             check_s=0.02, wall=self.time.wall)
        self.assertTrue(await self.caster.start())
        self.addAsyncCleanup(self.caster.stop)
        return self.caster.port


class CasterServer(CasterBase):
    async def test_sourcetable_for_root_and_unknown_mountpoints(self):
        port = await self.start()
        self.hub.publish(rtcm(1005))
        self.hub.publish(rtcm(1074))
        self.hub.publish(rtcm(1124))
        for path in ("/", "/OTHER", "/base"):
            _, writer, data = await ntrip_get(port, path)
            writer.close()
            text = data.decode()
            self.assertTrue(text.startswith("SOURCETABLE 200 OK\r\n"), text)
            # The misc field (the last) names our position's frame and the epoch: the
            # fake wall clock is 2027-01-15 08:00 UTC, decimal year 2027.04.
            self.assertIn("STR;BASE;BASE;RTCM 3.3;1005(10),1074(1),1124(1);2;GPS+BDS;none;;"
                          "0.00;0.00;0;0;gnss_base;none;N;N;0;ITRF2020 epoch 2027.04 (today, "
                          "base_epoch not set)\r\n", text)
            # The misc text never holds ';', the STR field separator: 18 fields exactly.
            record = [line for line in text.splitlines() if line.startswith("STR;")][0]
            self.assertEqual(len(record.split(";")), 19)
            self.assertTrue(text.endswith("ENDSOURCETABLE\r\n"))
            head, _, body = data.partition(b"\r\n\r\n")
            self.assertIn(f"Content-Length: {len(body)}".encode(), head)
        self.assertEqual([e["event"] for e in self.log_lines()], ["sourcetable"] * 3)
        self.assertEqual(self.caster.snapshot()["frame"],
                         "ITRF2020 epoch 2027.04 (today, base_epoch not set)")
        # Another frame, in any spelling; none declared leaves the field empty.
        self.caster.settings.base_frame = " nad83 (2011)"
        self.assertIn(";0;NAD83(2011) epoch 2027.04 (today", self.caster.sourcetable().decode())
        self.caster.settings.base_frame = ""
        self.assertIn(";gnss_base;none;N;N;0;\r\n", self.caster.sourcetable().decode())
        self.assertIsNone(self.caster.snapshot()["frame"])
        # A wrong frame option does not stop the caster (rovers are served): the field
        # stays empty and the card says why.
        self.caster.settings.base_frame = "WGS84"
        self.assertEqual(CasterSettings(base_frame="WGS84").problem(), "")
        self.assertIn(";gnss_base;none;N;N;0;\r\n", self.caster.sourcetable().decode())
        self.assertEqual(self.caster.snapshot()["frame_error"],
                         "base_frame must be one of ITRF2020, ITRF2014, ITRF2008, NAD83(2011), "
                         "or empty")
        self.caster.settings.base_frame = "ITRF2020"
        self.caster.settings.base_epoch = "2026-11-06"
        self.assertIn(";0;ITRF2020 epoch 2026.85\r\n", self.caster.sourcetable().decode())
        self.caster.settings.base_epoch = "soon"
        self.assertIn(";0;\r\n", self.caster.sourcetable().decode())
        self.assertIn("base_epoch must be", self.caster.snapshot()["frame_error"])

    async def test_no_password_accepts_any_login_and_sends_the_filtered_stream(self):
        port = await self.start()
        streams = []
        for login in (None, "anyone:anything", ":"):
            reader, writer, data = await ntrip_get(port, login=login,
                                                   read_until=b"ICY 200 OK\r\n")
            self.assertEqual(data, b"ICY 200 OK\r\n")
            streams.append((reader, writer))
        await until(lambda: len(self.caster.clients) == 3)
        sent = [rtcm(1005), rtcm(1033, 30), rtcm(1074, 60, 1), rtcm(1230)]
        for frame in sent:
            self.hub.publish(frame)
        self.time.now += 1
        self.hub.publish(rtcm(1005))          # thinned: within 10 s
        self.hub.publish(rtcm(1074, 60, 2))
        self.time.now += 10
        self.hub.publish(rtcm(1005, 19, 3))   # passes again
        expected = b"".join(sent) + rtcm(1074, 60, 2) + rtcm(1005, 19, 3)
        for reader, writer in streams:
            got = b""
            while len(got) < len(expected):
                got += await asyncio.wait_for(reader.read(4096), 3)
            self.assertEqual(got, expected)
            writer.close()
        await until(lambda: not self.caster.clients)
        events = [e["event"] for e in self.log_lines()]
        self.assertEqual(events.count("connect"), 3)
        self.assertEqual(events.count("disconnect"), 3)
        connect = next(e for e in self.log_lines() if e["event"] == "connect")
        self.assertEqual(connect["user_agent"], "NTRIP TestClient/1.0")
        self.assertEqual(connect["ntrip_version"], "Ntrip/1.0")

    async def test_password_set_requires_the_login(self):
        port = await self.start(user="rover", password=Secret(CASTER_SENTINEL))
        handler_output = io.StringIO()
        handler = logging.StreamHandler(handler_output)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        for login in (None, "rover:BadGuess9", f"other:{CASTER_SENTINEL}", "rover:"):
            _, writer, data = await ntrip_get(port, login=login)
            writer.close()
            self.assertTrue(data.startswith(b"HTTP/1.0 401 Unauthorized\r\n"), (login, data))
        _, writer, data = await ntrip_get(port, login=f"rover:{CASTER_SENTINEL}",
                                          read_until=b"OK\r\n")
        self.assertEqual(data, b"ICY 200 OK\r\n")
        _, _, table = await ntrip_get(port, "/")
        self.assertIn(b";B;N;0;", table)
        writer.close()
        await until(lambda: not self.caster.clients)
        log_text = (self.dir / "caster.jsonl").read_text()
        self.assertEqual(log_text.count('"auth_failed"'), 4)
        for entry in self.log_lines():
            if entry["event"] == "auth_failed":
                self.assertNotIn("user", entry, "the typed user name is not logged")
        for text in (log_text, handler_output.getvalue(), json.dumps(self.caster.snapshot())):
            self.assertNotIn(CASTER_SENTINEL, text)
            self.assertNotIn("BadGuess9", text)

    async def test_a_password_with_any_user(self):
        port = await self.start(password=Secret(CASTER_SENTINEL))
        _, writer, data = await ntrip_get(port, login=f"whoever:{CASTER_SENTINEL}",
                                          read_until=b"OK\r\n")
        writer.close()
        self.assertEqual(data, b"ICY 200 OK\r\n")

    async def test_repeated_failed_logins_lock_the_address_out(self):
        port = await self.start(password=Secret(CASTER_SENTINEL))
        for _ in range(caster_module.LOCKOUT_FAILURES):
            _, writer, data = await ntrip_get(port, login="x:wrong")
            writer.close()
            self.assertIn(b"401", data)
        _, writer, data = await ntrip_get(port, login=f"x:{CASTER_SENTINEL}")
        writer.close()
        self.assertIn(b"401", data, "locked out even with the right password")
        self.time.now += caster_module.LOCKOUT_S + 1
        _, writer, data = await ntrip_get(port, login=f"x:{CASTER_SENTINEL}",
                                          read_until=b"OK\r\n")
        writer.close()
        self.assertEqual(data, b"ICY 200 OK\r\n")
        reasons = [e.get("reason") for e in self.log_lines() if e["event"] == "auth_failed"]
        self.assertEqual(reasons[-1], "locked out after repeated failed logins")

    async def test_an_ipv4_mapped_peer_shares_the_lockout_and_log_of_its_ipv4_address(self):
        real = asyncio.StreamWriter.get_extra_info
        turn = [0]

        def mapped(writer, name, default=None):
            value = real(writer, name, default)
            if name == "peername" and value:
                turn[0] += 1
                if turn[0] % 2:   # every other connection as a dual-stack socket sees it
                    return ("::ffff:" + value[0],) + tuple(value[1:]) + (0, 0)
            return value

        port = await self.start(password=Secret(CASTER_SENTINEL))
        with mock.patch.object(asyncio.StreamWriter, "get_extra_info", mapped):
            for _ in range(caster_module.LOCKOUT_FAILURES):
                _, writer, data = await ntrip_get(port, login="x:wrong")
                writer.close()
                self.assertIn(b"401", data)
            for _ in range(2):   # locked out in both forms
                _, writer, data = await ntrip_get(port, login=f"x:{CASTER_SENTINEL}")
                writer.close()
                self.assertIn(b"401", data)
        addresses = {e.get("address") for e in self.log_lines()}
        self.assertEqual(addresses, {"127.0.0.1"})

    async def test_client_cap(self):
        port = await self.start(max_clients=2)
        held = []
        for _ in range(2):
            _, writer, data = await ntrip_get(port, read_until=b"OK\r\n")
            self.assertEqual(data, b"ICY 200 OK\r\n")
            held.append(writer)
        _, writer, data = await ntrip_get(port)
        writer.close()
        self.assertTrue(data.startswith(b"HTTP/1.0 503"), data)
        held.pop().close()
        await until(lambda: len(self.caster.clients) == 1)
        _, writer, data = await ntrip_get(port, read_until=b"OK\r\n")
        self.assertEqual(data, b"ICY 200 OK\r\n")
        for w in held + [writer]:
            w.close()
        self.assertIn("caster_max_clients", self.log_lines()[2]["reason"])

    async def test_oversized_slow_and_wrong_requests_are_closed(self):
        port = await self.start()
        _, writer, data = await ntrip_get(port, headers=b"X-Pad: " + b"a" * 5000 + b"\r\n")
        writer.close()
        self.assertEqual(data, b"")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"SOURCE pw /BASE\r\n\r\n")
        self.assertIn(b"405", await asyncio.wait_for(reader.read(100), 3))
        writer.close()
        with mock.patch.object(caster_module, "HEADER_TIMEOUT_S", 0.2):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /BASE HTTP/1.0\r\n")  # and nothing more
            self.assertEqual(await asyncio.wait_for(reader.read(100), 3), b"")
            writer.close()
        reasons = [e["reason"] for e in self.log_lines()]
        self.assertEqual(reasons[0], "request header too large")
        self.assertEqual(reasons[1], "method SOURCE")
        self.assertIn("no complete request", reasons[2])
        self.assertEqual(self.caster.clients, [])

    async def test_a_stalled_client_is_dropped_and_the_others_keep_everything(self):
        port = await self.start()
        slow_reader, slow_writer, _ = await ntrip_get(port, read_until=b"OK\r\n")
        fast_reader, fast_writer, _ = await ntrip_get(port, read_until=b"OK\r\n")
        await until(lambda: len(self.caster.clients) == 2)
        slow = self.caster.clients[0]
        slow.buffered = lambda: caster_module.CLIENT_BUFFER + 1  # as if it never reads
        frames = [rtcm(1074, 50, i) for i in range(20)]
        for frame in frames:
            self.hub.publish(frame)
        self.assertEqual((slow.frames_sent, slow.frames_skipped), (0, 20))
        self.time.now += caster_module.STALL_S + 1
        await until(lambda: len(self.caster.clients) == 1)
        self.assertEqual(await asyncio.wait_for(slow_reader.read(100), 3), b"")
        self.hub.publish(rtcm(1230))
        got = b""
        expected = b"".join(frames) + rtcm(1230)
        while len(got) < len(expected):
            got += await asyncio.wait_for(fast_reader.read(4096), 3)
        self.assertEqual(got, expected)
        fast_writer.close()
        slow_writer.close()
        dropped = [e for e in self.log_lines() if e["event"] == "disconnect"][0]
        self.assertIn("stalled", dropped["reason"])
        self.assertEqual(dropped["frames_skipped"], 20)

    async def test_rover_input_is_read_and_dropped(self):
        port = await self.start()
        reader, writer, _ = await ntrip_get(port, read_until=b"OK\r\n")
        writer.write(nmea("GPGGA,120000.00,0000.00,N,00000.00,E,5,12,0.5,0,M,0,M,1.0,0000"))
        writer.write(b"\x00garbage\r\n" * 1000)
        await until(lambda: self.caster.clients and self.caster.clients[0].gga_quality == 5)
        snapshot = self.caster.snapshot()["clients"][0]
        self.assertEqual(snapshot["gga_quality"], 5)
        self.assertGreater(snapshot["bytes_received"], 10_000)
        writer.close()

    async def test_a_port_in_use_is_reported_and_the_app_goes_on(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            hub = RtcmHub()
            caster = Caster(CasterSettings(host="127.0.0.1", port=taken.getsockname()[1]),
                            hub, self.events)
            with self.assertLogs("gnss_base", "ERROR"):
                self.assertFalse(await caster.start())
            self.assertIn("Cannot listen on port", caster.snapshot()["error"])
            hub.publish(rtcm(1005))  # nothing subscribed, nothing breaks
            await caster.stop()

    async def test_disabled_or_invalid_settings_do_not_listen(self):
        caster = Caster(CasterSettings(enabled=False), RtcmHub(), self.events)
        self.assertFalse(await caster.start())
        caster = Caster(CasterSettings(mountpoint="no spaces"), RtcmHub(), self.events)
        with self.assertLogs("gnss_base", "ERROR"):
            self.assertFalse(await caster.start())
        self.assertIn("caster_mountpoint", caster.snapshot()["error"])
        # caster_max_clients: 1 to 16, as config.yaml's schema allows
        for clients, ok in ((1, True), (16, True), (0, False), (17, False), (64, False)):
            problem = CasterSettings(max_clients=clients).problem()
            self.assertEqual(problem == "", ok, clients)
            if not ok:
                self.assertEqual(problem, "caster_max_clients must be 1 to 16.")


# ---------------------------------------------------------------- upload

class FakeCaster:
    """An NTRIP caster on localhost that accepts SOURCE connections the way the test
    says: `reply` is sent after the request; `close_after` closes the connection after
    that many seconds. Records each request and every byte received, and the most
    connections open at once."""

    def __init__(self, reply=b"ICY 200 OK\r\n", close_after=None, after_ok=b""):
        self.reply = reply
        self.close_after = close_after
        self.after_ok = after_ok
        self.requests = []
        self.received = []
        self.open = 0
        self.most_open = 0
        self.closed = 0
        self.server = None
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    def kick(self):
        """Close every open connection, as a caster restart would."""
        for writer in list(self.writers):
            writer.transport.abort()

    async def stop(self):
        self.server.close()
        self.kick()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        self.writers.add(writer)
        self.open += 1
        self.most_open = max(self.most_open, self.open)
        data = bytearray()
        self.received.append(data)
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            self.requests.append(request)
            writer.write(self.reply)
            if self.reply.startswith(b"ICY 200 OK"):
                writer.write(self.after_ok)
            await writer.drain()
            loop = asyncio.get_running_loop()
            end = None if self.close_after is None else loop.time() + self.close_after
            while True:
                timeout = None if end is None else max(end - loop.time(), 0)
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                data += chunk
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        finally:
            self.open -= 1
            self.closed += 1
            self.writers.discard(writer)
            writer.close()


class UploadBase(LocalOnly):
    async def make(self, fake=None, password=SENTINEL, feed=False, **options):
        self.time = FakeTime()
        self.hub = RtcmHub(clock=self.time.clock)
        if feed:
            self.time.hub = self.hub
        self.fake = fake
        port = 9 if fake is None else await fake.start()
        if fake is not None:
            self.addAsyncCleanup(fake.stop)
        settings = UploadSettings(host="127.0.0.1", port=port, mountpoint="TESTMOUNT",
                                  password=Secret(password), style=options.pop("style", "rev1"))
        self.uploader = Uploader(settings, self.hub, self.events, version="9.9.9",
                                 clock=self.time.clock, wall=self.time.wall,
                                 sleep=self.time.sleep, rand=lambda: 0.0, poll_s=0.01,
                                 **options)
        self.addAsyncCleanup(self.uploader.stop)
        self.assertEqual(Uploader._open, 0)
        return self.uploader


class Upload(UploadBase):
    async def test_rev1_source_handshake_then_only_filtered_rtcm(self):
        fake = FakeCaster(after_ok=b"anything the caster sends is dropped\r\n" * 10)
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        uploader.start()
        await until(lambda: uploader.state == "connected")
        self.assertEqual(fake.requests, [f"SOURCE {SENTINEL} /TESTMOUNT\r\n"
                                         "Source-Agent: NTRIP gnss_base/9.9.9\r\n\r\n".encode()])
        frames = [rtcm(1005), rtcm(1074, 60, 1), rtcm(1005), rtcm(1033, 30), rtcm(1230)]
        for frame in frames:
            self.hub.publish(frame)
        expected = rtcm(1005) + rtcm(1074, 60, 1) + rtcm(1033, 30) + rtcm(1230)
        await until(lambda: len(fake.received[0]) >= len(expected))
        self.assertEqual(bytes(fake.received[0]), expected)
        snapshot = uploader.snapshot()
        self.assertEqual((snapshot["state"], snapshot["bytes_sent"], snapshot["password_set"]),
                         ("connected", len(expected), True))
        self.assertEqual([e["event"] for e in self.log_lines()], ["upload_connect"])

    async def test_bad_password_or_taken_mountpoint_waits_thirty_minutes(self):
        for reply in (b"ERROR - Bad Password\r\n", b"ERROR - Mount Point Taken or Invalid\r\n"):
            with self.subTest(reply=reply):
                fake = FakeCaster(reply=reply)
                uploader = await self.make(fake, feed=True)
                self.hub.last_frame = self.time.now
                self.time.hold = True
                with self.assertLogs("gnss_base", "WARNING"):
                    uploader.start()
                    await until(lambda: uploader.state == "waiting")
                snapshot = uploader.snapshot()
                self.assertTrue(snapshot["alert"])
                self.assertIn("Refused", snapshot["last_error"])
                self.assertEqual(snapshot["last_reply"], reply.decode().strip())
                self.assertEqual(snapshot["retry_in_s"], upload_module.REFUSED_WAIT_S)
                first = self.time.now
                self.time.hold = False
                with self.assertLogs("gnss_base", "WARNING"):
                    await until(lambda: len(fake.requests) == 2)
                    self.time.hold = True
                    await until(lambda: uploader.state == "waiting"
                                and uploader.attempts_today == 2)
                self.assertEqual(self.time.sleeps[0], upload_module.REFUSED_WAIT_S)
                self.assertGreaterEqual(self.time.now - first, upload_module.REFUSED_WAIT_S)
                await uploader.stop()
                self.assertEqual(fake.most_open, 1)

    async def test_a_ban_page_waits_four_hours(self):
        replies = [b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\n\r\n<html>Banned</html>",
                   b"HTTP/1.0 200 OK\r\n\r\n<html><body>Your IP is banned</body></html>"]
        for reply in replies:
            with self.subTest(reply=reply[:20]):
                fake = FakeCaster(reply=reply)
                uploader = await self.make(fake, feed=True)
                self.hub.last_frame = self.time.now
                self.time.hold = True
                with self.assertLogs("gnss_base", "WARNING"):
                    uploader.start()
                    await until(lambda: uploader.state == "waiting")
                    self.assertIn("Looks like a ban", uploader.last_error)
                    self.assertEqual(uploader.snapshot()["retry_in_s"],
                                     upload_module.BANNED_WAIT_S)
                    self.time.hold = False
                    await until(lambda: len(fake.requests) == 2)
                self.assertEqual(self.time.sleeps[0], upload_module.BANNED_WAIT_S)
                await uploader.stop()

    def test_reply_classes(self):
        self.assertEqual(classify("ICY 200 OK\r\n"), "ok")
        self.assertEqual(classify("OK\r\n"), "ok")
        self.assertEqual(classify("OK\n"), "ok")
        self.assertEqual(classify("HTTP/1.1 429 Too Many Requests\r\n\r\n"), "banned")
        self.assertEqual(classify("<!DOCTYPE html><p>x</p>"), "banned")
        self.assertEqual(classify("ERROR - Your IP address has been Banned\r\n"), "banned")
        self.assertEqual(classify("ERROR - Blocked\r\n"), "banned")
        self.assertEqual(classify("ERROR - Mount Point Taken or Invalid\r\n"), "refused")
        # "ban" inside another word is not a ban.
        self.assertEqual(classify("ERROR - Bad Password for urban_base\r\n"), "refused")
        self.assertEqual(classify("Bandwidth note\r\n"), "failed")
        self.assertEqual(classify("OKAY\r\n"), "failed")
        self.assertEqual(classify("ERROR - Bad Password\r\n"), "refused")
        self.assertEqual(classify("HTTP/1.1 401 Unauthorized\r\n\r\n"), "refused")
        self.assertEqual(classify("HTTP/1.1 403 Forbidden\r\n\r\n"), "banned")
        self.assertEqual(classify("HTTP/1.1 200 OK\r\n\r\n<HTML>x</HTML>"), "banned")
        self.assertEqual(classify(""), "failed")
        self.assertEqual(classify("SOURCETABLE 200 OK\r\n"), "failed")

    async def test_retries_double_from_a_minute_to_thirty_minutes(self):
        attempts = []

        async def refuse(host, port):
            attempts.append(self.time.now)
            raise ConnectionRefusedError(111, "Connection refused")

        uploader = await self.make(connect=refuse, feed=True)
        self.hub.last_frame = self.time.now
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: len(attempts) >= 9)
        gaps = [b - a for a, b in zip(attempts, attempts[1:])]
        self.assertEqual(gaps[:8], [60, 120, 240, 480, 960, 1800, 1800, 1800])
        self.assertEqual(set(gaps[8:]), {1800} if gaps[8:] else set())
        self.assertIn("Connection refused", uploader.last_error)
        self.assertFalse(uploader.alert)
        self.assertEqual(uploader.attempts_today, len(attempts))

    async def test_jitter_only_lengthens_the_wait(self):
        attempts = []

        async def refuse(host, port):
            attempts.append(self.time.now)
            raise ConnectionRefusedError(111, "Connection refused")

        uploader = await self.make(connect=refuse, feed=True)
        uploader._rand = lambda: 1.0
        self.hub.last_frame = self.time.now
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: len(attempts) >= 3)
        gaps = [b - a for a, b in zip(attempts, attempts[1:])]
        self.assertEqual([round(g) for g in gaps[:2]], [66, 132])

    async def test_a_day_has_a_cap_on_attempts(self):
        attempts = []

        async def refuse(host, port):
            attempts.append(self.time.now)
            raise ConnectionRefusedError(111, "Connection refused")

        uploader = await self.make(connect=refuse, feed=True)
        self.hub.last_frame = self.time.now
        with mock.patch.object(upload_module, "MAX_ATTEMPTS_PER_DAY", 3), \
                self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: len(attempts) >= 7)
        day = lambda t: int((self.time.start_wall + t - 1000) // 86400)  # noqa: E731
        per_day = {}
        for t in attempts:
            per_day[day(t)] = per_day.get(day(t), 0) + 1
        self.assertEqual(list(per_day.values())[:2], [3, 3])
        self.assertEqual(day(attempts[3]), day(attempts[2]) + 1)

    async def test_never_connects_without_rtcm(self):
        attempts = []

        async def record(host, port):
            attempts.append(self.time.now)
            raise ConnectionRefusedError(111, "refused")

        uploader = await self.make(connect=record)
        uploader._poll_s = 1.0  # fake seconds per check while waiting for RTCM
        uploader.start()
        await until(lambda: self.time.now > 1000 + 600, timeout=15)  # 10 min, fake clock
        self.assertEqual(attempts, [])
        self.assertEqual(uploader.state, "waiting for RTCM")
        self.hub.publish(rtcm(1005))  # (hub clock is the fake clock)
        with self.assertLogs("gnss_base", "WARNING"):
            await until(lambda: attempts)
            await uploader.stop()

    async def test_disconnects_once_when_the_receiver_goes_quiet_and_returns_with_it(self):
        fake = FakeCaster()
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        uploader.start()
        await until(lambda: uploader.state == "connected")
        self.time.now += upload_module.QUIET_S + 1
        await until(lambda: fake.closed == 1)
        uploader._poll_s = 1.0  # fake seconds per check while waiting for RTCM
        await until(lambda: uploader.state == "waiting for RTCM")
        start = self.time.now
        await until(lambda: self.time.now > start + 600, timeout=15)
        self.assertEqual(len(fake.requests), 1, "no reconnect while the receiver is quiet")
        self.hub.publish(rtcm(1005))
        await until(lambda: uploader.state == "connected")
        self.assertEqual((len(fake.requests), fake.most_open), (2, 1))
        events = [e["event"] for e in self.log_lines()]
        self.assertEqual(events, ["upload_connect", "upload_disconnect", "upload_connect"])
        self.assertIn("No RTCM", self.log_lines()[1]["reason"])

    async def test_only_one_connection_ever_across_reconnects(self):
        fake = FakeCaster(close_after=0.05)
        uploader = await self.make(fake, feed=True)
        self.hub.last_frame = self.time.now
        uploader.start()

        def connects():
            return [e["utc"] for e in self.log_lines() if e["event"] == "upload_connect"]

        # Wait for the uploader's own record of 5 accepted connections, not the fake
        # caster's 5 requests: a stop right after the 5th request could land before the
        # uploader read the OK and logged it (this made the test fail now and then).
        await until(lambda: len(connects()) >= 5)
        await uploader.stop()
        self.assertEqual(fake.most_open, 1)
        self.assertEqual(Uploader._open, 0)
        uploader.start()  # a second start while running does nothing
        self.assertIs(uploader.start(), uploader._task)
        self.assertGreaterEqual(len(connects()), 5)
        self.assertGreaterEqual(len(fake.requests), 5)
        # A connection that ended early (closed by the caster) is retried after 60 s,
        # then 120 s, ...: never sooner than a minute.
        self.assertTrue(all(s >= 60 for s in self.time.sleeps if s > 1))

    async def test_a_second_connection_is_refused_outright(self):
        uploader = await self.make()
        Uploader._open = 1
        try:
            with self.assertRaises(RuntimeError):
                await uploader._session()
        finally:
            Uploader._open = 0

    async def test_an_internal_error_stops_the_upload_instead_of_looping(self):
        uploader = await self.make(feed=True)
        self.hub.last_frame = self.time.now
        with mock.patch.object(uploader, "_session", side_effect=ValueError(SENTINEL)), \
                self.assertLogs("gnss_base", "ERROR"):
            uploader.start()
            await until(lambda: uploader.alert)
        self.assertEqual(uploader.state, "off")
        self.assertTrue(uploader._task.done())
        self.assertNotIn(SENTINEL, uploader.last_error)

    async def test_a_stalled_upload_is_closed(self):
        fake = FakeCaster()
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        self.time.hold = True
        with mock.patch.object(upload_module, "_buffered",
                               lambda writer: upload_module.STALL_BYTES + 1):
            uploader.start()
            await until(lambda: uploader.state == "connected")
            self.hub.publish(rtcm(1074))
            self.assertEqual(uploader.frames_skipped, 1)
            await asyncio.sleep(0.05)
            self.time.now += 5
            self.hub.publish(rtcm(1074))
            await asyncio.sleep(0.05)
            self.time.now += upload_module.STALL_S + 1
            self.hub.last_frame = self.time.now
            await until(lambda: uploader.state == "waiting")
        self.assertIn("stalled", uploader.reason)

    async def test_rate_guard_stops_the_upload(self):
        fake = FakeCaster()
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        self.time.hold = True
        uploader.start()
        await until(lambda: uploader.state == "connected")
        limit = upload_module.RATE_LIMIT_BITS * upload_module.RATE_WINDOW_S // 8
        big = rtcm(1077, 1000)
        for _ in range(limit // len(big) + 2):
            self.hub.publish(big)
        await until(lambda: uploader.state == "waiting")
        self.assertTrue(uploader.alert)
        self.assertIn("kbit/s", uploader.reason)
        self.assertLessEqual(len(fake.received[0]), limit + 2 * len(big))
        self.assertEqual(uploader._next_attempt - self.time.now, upload_module.GUARD_WAIT_S)

    async def test_daily_volume_guard_stops_until_the_next_utc_day(self):
        fake = FakeCaster()
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        self.time.hold = True
        with mock.patch.object(upload_module, "DAY_LIMIT_BYTES", 2000):
            uploader.start()
            await until(lambda: uploader.state == "connected")
            for _ in range(30):
                self.hub.publish(rtcm(1074, 100))
            await until(lambda: uploader.state == "stopped for today")
            sent = len(fake.received[0])
            self.assertLess(sent, 2200)
            self.assertTrue(uploader.alert)
            self.hub.publish(rtcm(1074, 100))
            self.assertEqual(uploader.bytes_today, sent)

    async def test_rev2_is_refused_with_a_clear_message(self):
        attempts = []

        async def record(host, port):
            attempts.append(1)

        with self.assertLogs("gnss_base", "ERROR") as logs:
            uploader = await self.make(connect=record, style="rev2")
        self.assertIsNone(uploader.start())
        self.hub.publish(rtcm(1005))
        await asyncio.sleep(0.05)
        self.assertEqual(attempts, [])
        snapshot = uploader.snapshot()
        self.assertEqual(snapshot["state"], "off")
        self.assertIn("rev2", snapshot["last_error"])
        self.assertTrue(snapshot["alert"])
        self.assertIn("not supported", logs.output[0])

    async def test_off_without_mountpoint_or_password(self):
        uploader = await self.make(password="")
        self.assertIsNone(uploader.start())
        self.assertEqual(uploader.snapshot()["state"], "off")
        self.assertFalse(uploader.snapshot()["password_set"])

    def test_settings_checks(self):
        good = dict(host="127.0.0.1", port=2101, mountpoint="M_1", password=Secret("p"))
        self.assertEqual(UploadSettings(**good).problem(), "")
        for change, word in ((dict(mountpoint="a b"), "upload_mountpoint"),
                             (dict(password=Secret("has space")), "upload_password"),
                             (dict(password=Secret("x\r\nInjected: 1")), "upload_password"),
                             (dict(host="a b"), "upload_host"),
                             (dict(port=0), "upload_port"),
                             (dict(style="rev3"), "rev1 or rev2")):
            problem = UploadSettings(**dict(good, **change)).problem()
            self.assertIn(word, problem)
            self.assertNotIn("space", problem.replace("spaces", ""))

    async def test_sentinel_password_appears_only_on_the_socket(self):
        """A whole session (refusal that echoes the password, then success, then a
        connection error): the password reaches the caster and nowhere else."""
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        fake = FakeCaster(reply=f"ERROR - Bad Password {SENTINEL}\r\n".encode())
        uploader = await self.make(fake, feed=True)
        self.hub.last_frame = self.time.now
        self.time.hold = True
        uploader.start()
        await until(lambda: uploader.state == "waiting")
        texts = [json.dumps(uploader.snapshot()), uploader.last_error, uploader.last_reply,
                 repr(uploader.settings), repr(vars(uploader))]
        echoed = uploader.last_reply
        fake.reply = b"ICY 200 OK\r\n"
        self.time.hold = False
        await until(lambda: uploader.state == "connected")
        self.time.hold = True
        self.hub.publish(rtcm(1005))
        texts.append(json.dumps(uploader.snapshot()))
        fake.kick()
        await until(lambda: uploader.state == "waiting")
        texts += [json.dumps(uploader.snapshot()), output.getvalue(),
                  (self.dir / "caster.jsonl").read_text()]
        await uploader.stop()
        for text in texts:
            self.assertNotIn(SENTINEL, text)
        self.assertEqual(echoed, "ERROR - Bad Password ***")
        self.assertTrue(all(SENTINEL.encode() in request for request in fake.requests))

    def test_password_only_from_options_and_not_kept_in_settings(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as stream:
            json.dump({"upload_mountpoint": "FROM_OPTIONS", "upload_password": SENTINEL,
                       "caster_password": CASTER_SENTINEL, "caster_max_clients": 3}, stream)
        self.addCleanup(os.unlink, stream.name)
        settings = load_settings(["--options", stream.name, "--upload-mountpoint", "CLI",
                                  "--caster-mountpoint", "CLIBASE", "--no-caster",
                                  "--caster-port", "12101"])
        caster, upload = output_settings(settings)
        self.assertEqual((upload.mountpoint, upload.password.reveal()), ("CLI", SENTINEL))
        self.assertEqual((caster.mountpoint, caster.enabled, caster.port, caster.max_clients),
                         ("CLIBASE", False, 12101, 3))
        self.assertEqual(caster.password.reveal(), CASTER_SENTINEL)
        self.assertNotIn(SENTINEL, repr(settings) + repr(caster) + repr(upload))
        self.assertNotIn(CASTER_SENTINEL, repr(settings))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            load_settings(["--upload-password", "x"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            load_settings(["--caster-password", "x"])


# ---------------------------------------------------------------- no path to the receiver

class NoNetworkToReceiver(LocalOnly):
    MODULES = ("hub", "caster", "upload", "ntripclient")

    def test_output_modules_import_nothing_that_reaches_the_receiver(self):
        for name in self.MODULES:
            tree = ast.parse((ROOT / "gnss_base" / f"{name}.py").read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "") + ":" + ",".join(a.name for a in node.names))
            text = " ".join(imported)
            for forbidden in ("commands", "control", "receiver", "serial", "subprocess", "os"):
                self.assertNotRegex(text, rf"\b{forbidden}\b", f"{name} imports {forbidden}")

    async def test_rover_and_caster_bytes_never_reach_the_serial_writer(self):
        """The real wiring, with a serial writer that fails the test if called."""
        write = mock.Mock(side_effect=AssertionError("network data reached the receiver"))
        status = Status()
        control = Controller(write, self.dir / "commands.jsonl", profile.load(), status)
        hub = RtcmHub()
        pipeline = Pipeline(status, mock.Mock(), on_nmea=control.on_nmea, hub=hub)
        caster = Caster(CasterSettings(host="127.0.0.1", port=0), hub, self.events)
        await caster.start()
        self.addAsyncCleanup(caster.stop)
        commands = (nmea("PQTMCFGRCVRMODE,W,1") + nmea("PQTMRESTOREPAR") + nmea("PQTMSRR")
                    + b"$PQTMSAVEPAR*5A\r\n")
        fake = FakeCaster(after_ok=commands * 5)
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        uploader = Uploader(UploadSettings(host="127.0.0.1", port=port, mountpoint="M",
                                           password=Secret("pw")), hub, self.events,
                            poll_s=0.01)
        self.addAsyncCleanup(uploader.stop)
        pipeline.on_data(rtcm(1005))
        uploader.start()
        reader, writer, _ = await ntrip_get(caster.port, read_until=b"OK\r\n")
        writer.write(commands * 5)
        await until(lambda: uploader.state == "connected")
        await until(lambda: caster.clients
                    and caster.clients[0].bytes_received >= len(commands) * 5)
        for _ in range(5):
            pipeline.on_data(rtcm(1074) + nmea("GNGGA,1,2,3"))
            await asyncio.sleep(0.02)
        writer.close()
        write.assert_not_called()
        self.assertEqual(control.history(), [])
        for name in ("control", "write", "runner", "reader"):
            self.assertFalse(hasattr(caster, name) or hasattr(uploader, name), name)


# ---------------------------------------------------------------- 0.4.0 review fixes

class StepClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class ConnectionLogLimits(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = self.dir / "caster.jsonl"

    def lines(self, path=None):
        path = path or self.path
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_noisy_events_are_limited_per_address_and_summed_up_each_minute(self):
        clock = StepClock()
        log = ConnectionLog(self.path, clock=clock)
        for _ in range(50):
            log.write("sourcetable", address="192.0.2.1", user_agent="flood")
        log.write("refused", address="192.0.2.2", reason="other address")
        lines = self.lines()
        self.assertEqual(len(lines), ConnectionLog.NOISY_PER_ADDRESS + 1)
        self.assertEqual(lines[-1]["address"], "192.0.2.2")
        self.assertEqual(log.suppressed, 50 - ConnectionLog.NOISY_PER_ADDRESS)
        clock.now += 60
        log.write("sourcetable", address="192.0.2.1")
        summary, after = self.lines()[-2:]
        self.assertEqual(summary["event"], "suppressed")
        self.assertEqual((summary["address"], summary["count"], summary["events"]),
                         ("192.0.2.1", 40, {"sourcetable": 40}))
        self.assertEqual(after["event"], "sourcetable")  # a new minute: logged again

    def test_disconnects_have_their_own_budget_per_address(self):
        clock = StepClock()
        log = ConnectionLog(self.path, clock=clock)
        for _ in range(100):   # one address in a connect/close loop
            log.write("connect", address="192.0.2.9")
            log.write("disconnect", address="192.0.2.9")
        events = [e["event"] for e in self.lines()]
        self.assertEqual(events.count("connect"), ConnectionLog.PER_ADDRESS)
        self.assertEqual(events.count("disconnect"), ConnectionLog.PER_ADDRESS)
        # Another rover still gets its lines.
        log.write("connect", address="192.0.2.10")
        log.write("disconnect", address="192.0.2.10", last_gga_line="$GPGGA,1")
        self.assertEqual([e["event"] for e in self.lines()[-2:]], ["connect", "disconnect"])
        # The global cap still applies, disconnects included.
        for i in range(ConnectionLog.PER_MINUTE):
            log.write("refused", address=f"10.0.{i // 250}.{i % 250}")
        log.write("disconnect", address="192.0.2.11")
        self.assertEqual(len(self.lines()), ConnectionLog.PER_MINUTE)

    def test_connection_events_have_a_looser_limit_and_all_addresses_a_shared_cap(self):
        clock = StepClock()
        log = ConnectionLog(self.path, clock=clock)
        for i in range(40):
            log.write("connect", address="192.0.2.9")
        self.assertEqual(len(self.lines()), ConnectionLog.PER_ADDRESS)
        for i in range(1000):
            log.write("refused", address=f"10.0.{i // 250}.{i % 250}")
        self.assertEqual(len(self.lines()), ConnectionLog.PER_MINUTE)
        clock.now += 60
        log.write("upload_connect", host="h")  # no address: not limited, ends the minute
        summaries = [e for e in self.lines() if e["event"] == "suppressed"]
        self.assertLessEqual(len(summaries), ConnectionLog.MAX_SUMMARY_LINES + 1)
        self.assertEqual(sum(e["count"] for e in summaries), 10 + 1000 - 170)
        self.assertEqual(self.lines()[-1]["event"], "upload_connect")

    def test_rotates_by_size_and_keeps_a_few(self):
        log = ConnectionLog(self.path, max_bytes=2000, keep=2)
        for i in range(300):
            log.write("upload_error", n=i, reply="x" * 40)
        files = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(files, ["caster.jsonl", "caster.jsonl.1", "caster.jsonl.2"])
        for name in files:
            self.assertLessEqual((self.dir / name).stat().st_size, 2000)
        self.assertEqual(self.lines()[-1]["n"], 299)
        older = self.lines(self.dir / "caster.jsonl.1")
        self.assertEqual(older[-1]["n"] + 1, self.lines()[0]["n"])

    def test_nothing_is_written_below_the_free_space_floor(self):
        room = [False]
        log = ConnectionLog(self.path, room=lambda: room[0])
        log.write("upload_connect", host="h")
        self.assertFalse(self.path.exists())
        self.assertEqual(log.skipped_low_disk, 1)
        room[0] = True
        log.write("upload_connect", host="h")
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(log.snapshot()["log_skipped_low_disk"], 1)

    def test_app_wires_the_floor_and_the_background_writer(self):
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        self.assertIn("room=lambda: not log.paused", source)
        self.assertIn("events.start()", source)
        self.assertIn("await events.stop(", source)
        # The writer task ends in a finally, even if starting up or shutting down fails.
        tree = ast.parse(source)
        run = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "run")
        tries = [n for n in ast.walk(run) if isinstance(n, ast.Try)]
        self.assertTrue(any("await shutdown(" in ast.unparse(n.finalbody)
                            and "events=events" in ast.unparse(n.finalbody) for n in tries))
        stop = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "shutdown")
        self.assertIn("events.stop(", ast.unparse(stop))


class ConnectionLogBackground(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = self.dir / "caster.jsonl"

    async def test_writes_happen_off_the_loop_and_a_full_queue_drops_and_counts(self):
        log = ConnectionLog(self.path, queue_size=50)
        gate = threading.Event()
        threads = []
        real = log._flush

        def slow(lines):
            threads.append(threading.get_ident())
            gate.wait(5)
            return real(lines)

        log._flush = slow
        log.start()
        log.write("upload_connect", n=0)
        await until(lambda: threads)     # the writer is now stuck in the "disk"
        started = time.perf_counter()
        for i in range(1, 200):
            log.write("upload_connect", n=i)
        self.assertLess(time.perf_counter() - started, 0.5, "write never waits for the disk")
        self.assertEqual(log.dropped, 199 - 50)
        self.assertNotIn(threading.get_ident(), threads)
        gate.set()
        await log.flush()
        await log.stop()
        numbers = [json.loads(line)["n"] for line in self.path.read_text().splitlines()]
        self.assertEqual(numbers, list(range(51)))
        self.assertEqual(log.snapshot()["log_dropped"], 149)

    async def test_stop_writes_the_held_back_summary(self):
        log = ConnectionLog(self.path)
        log.start()
        for _ in range(20):
            log.write("auth_failed", address="192.0.2.7", reason="wrong user or password")
        await log.stop()
        lines = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(lines[-1]["event"], "suppressed")
        self.assertEqual(lines[-1]["count"], 20 - ConnectionLog.NOISY_PER_ADDRESS)
        log.write("upload_connect")      # after stop: written at once
        self.assertEqual(len(self.path.read_text().splitlines()), len(lines) + 1)

    async def test_stop_gives_up_on_a_hung_disk_after_the_timeout(self):
        log = ConnectionLog(self.path)
        gate = threading.Event()
        self.addCleanup(gate.set)
        started = []

        def hung(lines):
            started.append(1)
            gate.wait(10)
            return True, "", 0, 0

        log._flush = hung
        log.start()
        log.write("upload_connect", n=0)
        await until(lambda: started)
        log.write("upload_connect", n=1)
        before = time.perf_counter()
        with self.assertLogs("gnss_base", "ERROR"):
            await log.stop(timeout=0.2)
        self.assertLess(time.perf_counter() - before, 2)
        self.assertIsNone(log._task)
        self.assertEqual(log.dropped, 1)
        log.write("upload_connect", n=2)     # after giving up: dropped, never blocks
        self.assertEqual(log.dropped, 2)

    async def test_a_write_error_is_reported_not_raised(self):
        self.path.mkdir()                # a directory where the file should be
        log = ConnectionLog(self.path)
        log.start()
        with self.assertLogs("gnss_base", "ERROR"):
            log.write("upload_connect")
            await log.flush()
        await log.stop()
        self.assertIn("connection log", log.error)
        self.assertEqual(log.dropped, 1)


class LoginMemory(unittest.TestCase):
    def test_failed_logins_age_out_and_are_capped(self):
        clock = StepClock()
        caster = Caster(CasterSettings(password=Secret("pw")), RtcmHub(), ConnectionLog(None),
                        clock=clock)
        for i in range(caster_module.MAX_TRACKED + 500):
            caster._check_login(f"10.{i // 65536}.{i // 256 % 256}.{i % 256}", ("u", "bad"))
        self.assertLessEqual(len(caster._failures), caster_module.MAX_TRACKED)
        for _ in range(caster_module.LOCKOUT_FAILURES):
            caster._check_login("192.0.2.1", ("u", "bad"))
        self.assertIn("192.0.2.1", caster._locked)
        clock.now += caster_module.LOCKOUT_WINDOW_S + caster_module.LOCKOUT_S + 1
        caster._prune(clock.now)
        self.assertEqual((caster._failures, caster._locked), ({}, {}))


class Slowloris(LocalOnly):
    async def test_one_address_cannot_hold_every_request_slot(self):
        time_ = FakeTime()
        hub = RtcmHub(clock=time_.clock)
        caster = Caster(CasterSettings(host="127.0.0.1", port=0), hub, self.events,
                        clock=time_.clock, check_s=0.02)
        self.assertTrue(await caster.start())
        self.addAsyncCleanup(caster.stop)
        slow = []
        for _ in range(caster_module.MAX_HANDSHAKES_PER_ADDRESS):
            reader, writer = await asyncio.open_connection("127.0.0.1", caster.port)
            writer.write(b"GET /BASE HTTP/1.0\r\n")  # and never the rest
            slow.append((reader, writer))
        await until(lambda: caster._pending["127.0.0.1"] == 4)
        reader, writer = await asyncio.open_connection("127.0.0.1", caster.port)
        writer.write(b"GET /BASE HTTP/1.0\r\n\r\n")
        try:
            refused = await asyncio.wait_for(reader.read(100), 3)
        except ConnectionResetError:
            refused = b""
        self.assertEqual(refused, b"", "the fifth is closed unanswered")
        writer.close()
        # A rover from another address still gets in.
        reader, writer = await asyncio.open_connection("127.0.0.1", caster.port,
                                                       local_addr=("127.0.0.2", 0))
        writer.write(b"GET /BASE HTTP/1.0\r\n\r\n")
        self.assertEqual(await asyncio.wait_for(reader.readuntil(b"OK\r\n"), 3),
                         b"ICY 200 OK\r\n")
        self.assertEqual(caster.clients[0].address, "127.0.0.2")
        writer.close()
        for _, w in slow:
            w.close()
        await until(lambda: not caster._pending)
        refused = [e for e in self.log_lines() if e["event"] == "refused"]
        self.assertIn("from this address", refused[0]["reason"])


class RoverInput(unittest.TestCase):
    GGA = "GPGGA,123519.00,4807.0380,N,01131.0000,E,4,08,0.9,545.4,M,46.9,M,1.0,0000"
    RMC = "GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"

    def client(self):
        self.clock = StepClock()
        self.notes = []
        return Client(FakeWriter(), "192.0.2.5", "", "Ntrip/1.0", "", self.clock,
                      note=lambda event, **fields: self.notes.append((event, fields)))

    def test_counts_by_kind_keeps_the_position_and_logs_a_few_lines(self):
        client = self.client()
        for second in range(3):
            client.received(nmea(self.GGA))
            self.clock.now += 1
        client.received(nmea(self.RMC) + nmea("GNGSA,A,3,01,02,,,,,,,,,,,1.0,0.9,0.5"))
        client.received(b"\xb5\x62\x01\x07binary\r\n" + nmea("PQTMVERNO") + b"x" * 1000)
        client.received(nmea("GPGSV,1,1,00") * 3)
        summary = client.summary()
        # The x run is flushed unfinished, so the first GPGSV is the rest of that line:
        # "other", not NMEA.
        self.assertEqual((summary["gga"], summary["nmea_other"], summary["other"]), (3, 5, 3))
        self.assertEqual(summary["gga_hz"], 1.0)
        self.assertEqual(summary["types"], {"GPGSV": 2, "GPRMC": 1, "GNGSA": 1, "PQTMVERNO": 1})
        self.assertEqual(summary["bytes_received"], client.bytes_received)
        snapshot = client.snapshot(self.clock.now)
        self.assertEqual(snapshot["sent"], summary)
        position = snapshot["position"]
        self.assertAlmostEqual(position["lat"], 48 + 7.038 / 60)
        self.assertAlmostEqual(position["lon"], 11 + 31 / 60)
        self.assertEqual((position["quality"], position["utc"], position["age_s"]),
                         (4, "123519.00", 1.0))
        # Five samples logged as sent: only the first GGA, so the rest is not crowded out.
        self.assertEqual(len(self.notes), caster_module.ROVER_SAMPLES)
        self.assertEqual([f["n"] for _, f in self.notes], [1, 2, 3, 4, 5])
        self.assertEqual([f["type"] for _, f in self.notes],
                         ["GGA", "GPRMC", "GNGSA", None, "PQTMVERNO"])
        gga = self.notes[0][1]
        self.assertEqual((gga["kind"], gga["text"], gga["shortened"]),
                         ("nmea", nmea(self.GGA).decode().rstrip("\r\n"), False))
        self.assertIn("4807.038,N,01131.000,E", self.notes[1][1]["text"])
        self.assertEqual(client.last_gga_line(),
                         (nmea(self.GGA).decode().rstrip("\r\n"), False))
        for event, _ in self.notes:
            self.assertEqual(event, "rover_sent")

    def test_a_gga_without_a_fix_clears_the_position(self):
        client = self.client()
        client.received(nmea(self.GGA))
        self.assertIsNotNone(client.position)
        client.received(nmea("GPGGA,123520.00,,,,,0,00,99.9,,M,,M,,"))
        self.assertIsNone(client.snapshot(self.clock.now)["position"])
        self.assertEqual(client.gga_quality, 0)
        client.received(nmea("GPGGA,123521.00,9907.0,N,01131.0,E,1,08,0.9,0,M,0,M,,"))
        self.assertIsNone(client.position, "latitude over 90 is not a position")

    def test_logged_samples_keep_positions_and_are_escaped_and_shortened(self):
        client = self.client()
        lines = [
            b"\x00$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n",
            b" $GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n",
            nmea(self.GGA),
            nmea(self.GGA.replace("123519.00", "123520.00")),   # a second GGA: not sampled
            b"$PQTMX,\xb5\x62\x01binary\\lat=48.1173 ecef=1334567.12,-4654321.5\r\n",
            b"$X," + b"x" * 192 + b"48.1173456,11.5167" + b"9" * 300 + b"\r\n",
            nmea(self.RMC),
        ]
        client.received(b"".join(lines))
        self.assertEqual(len(self.notes), caster_module.ROVER_SAMPLES)
        self.assertEqual([(f["kind"], f["type"]) for _, f in self.notes],
                         [("other", None), ("other", None), ("nmea", "GGA"),
                          ("nmea", "PQTMX"), ("nmea", "X")])
        texts = [f["text"] for _, f in self.notes]
        # Not starting with "$": only the first word, if any, and the length.
        self.assertEqual(texts[0], f"({len(lines[0]) - 2} bytes)")
        self.assertEqual(texts[1], f"({len(lines[1]) - 2} bytes)")
        # Starting with "$": as sent, escaped.
        self.assertEqual(texts[2], nmea(self.GGA).decode().rstrip("\r\n"))
        self.assertEqual(texts[3], "$PQTMX,\\xb5b\\x01binary\\\\lat=48.1173 "
                                   "ecef=1334567.12,-4654321.5")
        self.assertFalse(any(f["shortened"] for _, f in self.notes[2:4]))
        self.assertTrue(all(f["shortened"] for _, f in self.notes[:2]))
        # A long line is cut at SAMPLE_CHARS, after escaping.
        self.assertEqual(texts[4], ("$X," + "x" * 192 + "48.1173456,11.5167")[
            :caster_module.SAMPLE_CHARS])
        self.assertTrue(self.notes[4][1]["shortened"])
        self.assertIn("123520.00", client.last_gga_line()[0])

    def test_other_lines_are_logged_as_first_word_and_length(self):
        sample = caster_module.sample
        self.assertEqual(sample(b"Authorization: Basic abc="), ("Authorization: (25 bytes)", True))
        self.assertEqual(sample(b"GET /BASE?x HTTP/1.0"), ("GET (20 bytes)", True))
        self.assertEqual(sample(b"hello"), ("(5 bytes)", True))
        self.assertEqual(sample(b" folded"), ("(7 bytes)", True))
        self.assertEqual(sample(b"A" * 33 + b" x"), ("(35 bytes)", True))
        self.assertEqual(sample(b"A" * 32 + b" x"), ("A" * 32 + " (34 bytes)", True))
        self.assertEqual(sample(b"x\\y z"), ("(5 bytes)", True))
        self.assertEqual(sample(b"$GPGSA,1", start=False), ("(8 bytes)", True))

    def test_a_bare_token_or_query_line_is_never_logged(self):
        sample = caster_module.sample
        for line in (b"hunter2", b"user=u&password=hunter2", b"/m?pw=s3cr3t", b"a=b c"):
            text = sample(line)[0]
            self.assertEqual(text, f"({len(line)} bytes)", line)
        self.assertEqual(sample(b"$GPGGA,x?apikey=s3cr3t")[0], "$GPGGA,x?apikey=-")

    def test_the_last_gga_line_is_escaped_and_shortened(self):
        client = self.client()
        self.assertEqual(client.last_gga_line(), (None, False))
        client.received(nmea(self.GGA))
        client.received(b"$GPGGA,1\x00\\" + b"9" * 50000 + b"\r\n")
        text, shortened = client.last_gga_line()
        self.assertTrue(text.startswith("$GPGGA,1\\x00\\\\999"), text)
        self.assertEqual((len(text), shortened), (caster_module.SAMPLE_CHARS, True))
        self.assertEqual(len(self.notes), 1, "only the first GGA is a sample")

    def test_a_cut_never_splits_an_escape(self):
        limit = caster_module.SAMPLE_CHARS
        for pad in range(limit - 5, limit + 1):
            for tail in (b"\\", b"\x00", b"\xff\xfe", b"\\\\"):
                raw = b"x" * pad + tail + b"yz"
                text, shortened = caster_module._escape(raw)
                self.assertLessEqual(len(text), limit)
                full = caster_module._escape(raw, limit=10 ** 6)[0]
                self.assertTrue(full.startswith(text))
                self.assertEqual(shortened, len(full) > limit)
                # What is left unescapes cleanly: no dangling backslash or partial \xN.
                self.assertIsNone(re.search(r"(?<!\\)(\\\\)*\\(x[0-9a-f]?)?$", text),
                                  (pad, tail, text[-6:]))
        exact = b"a" * (limit - 4) + b"\x01"
        self.assertEqual(caster_module._escape(exact), ("a" * (limit - 4) + "\\x01", False))
        self.assertEqual(caster_module._escape(exact + b"b")[1], True)
        # A rover_sent sample is cut the same way.
        client = self.client()
        client.received(b"$" + b"x" * (limit - 2) + b"\x07rest\r\n")
        self.assertEqual((self.notes[0][1]["text"], self.notes[0][1]["shortened"]),
                         ("$" + "x" * (limit - 2), True))

    def test_credentials_in_dollar_lines_are_hidden_positions_are_not(self):
        hide = caster_module.hide_credentials
        self.assertEqual(hide(b"$X,4807.038,N,Authorization: Basic aHVudGVyMg==\r x"),
                         b"$X,4807.038,N,Authorization: -")
        self.assertEqual(hide(b"$X,proxy-AUTHORIZATION:Bearer t"), b"$X,proxy-AUTHORIZATION: -")
        self.assertEqual(hide(b"$X,SOURCE hunter2 /BASE"), b"$X,SOURCE -")
        self.assertEqual(hide(b"$X,http://u:hun/ter2@host/B 4807.038,N"),
                         b"$X,http://-@host/B 4807.038,N")
        self.assertEqual(hide(b"$X,http://u:hun@ter2@host/B"), b"$X,http://-@host/B")
        self.assertEqual(hide(b"$X,/B?user=u&password=hunter2&n=1"), b"$X,/B?user=u&password=-&n=1")
        self.assertEqual(hide(nmea(self.GGA)), nmea(self.GGA))

    LEAKS = {
        "url beyond 200": [b"$PQTMX," + b"a" * 250 + b" http://u:hunter2@host/B\r\n",
                           b"GET " + b"a" * 250 + b" http://u:hunter2@host/B\r\n"],
        "gga with lone CR": [
            b"$GPGGA,123519.00,4807.0380,N,01131.0000,E,4,08,0.9,545.4,M,46.9,M,1.0,0000"
            b"\rAuthorization: Basic aHVudGVyMg==\r\n"],
        "bearer over reads": [b"Authorization: Bearer " + b"hunter2" * 200 + b"\r\n",
                              b"$X,Authorization: Bearer " + b"hunter2" * 200 + b"\r\n",
                              b"$X," + b"j" * 500 + b"Authoriz", b"ation: Basic hunter2\r\n"],
        "junk then split header": [b"j" * 520 + b"Authorization: Basic hunter2==\r\n",
                                   b"$" + b"j" * 519 + b"Authorization: Basic hunter2==\r\n"],
        "lone CR in header": [b"GET /BASE HTTP/1.0\rAuthorization: Basic hunter2\r\n",
                              b"Accept: x\rAuthorization: Basic hunter2\r\n"],
        "folded header": [b"Authorization: Basic\r\n hunter2\r\n",
                          b"Authorization: Basic\r\n\thunter2\r\n"],
        "url passwords": [b"GET http://u:hun/ter2@host/BASE\r\n", b"u:hun@ter2@host\r\n",
                          b"$X,http://u:hun/ter2@host/B\r\n", b"$X,http://u:hun@ter2@host\r\n"],
        "query password": [b"GET /BASE?password=hunter2 HTTP/1.0\r\n",
                           b"$X,/BASE?password=hunter2&x=1\r\n"],
    }

    def test_no_credential_reaches_the_log(self):
        log_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, log_dir)
        log = ConnectionLog(log_dir / "caster.jsonl")
        for name, chunks in self.LEAKS.items():
            for chunk in chunks:
                for size in (512, 7, len(chunk)):   # split into reads of these sizes
                    client = Client(FakeWriter(), "192.0.2.5", "", "Ntrip/1.0", "",
                                    StepClock(), note=log.write)
                    data = chunk if chunk.endswith(b"\n") else b"".join(chunks)
                    for i in range(0, len(data), size):
                        client.received(data[i:i + size])
                    text, cut = client.last_gga_line()
                    log.write("disconnect", case=name, last_gga_line=text,
                              last_gga_shortened=cut, **client.summary())
        text = (log_dir / "caster.jsonl").read_text()
        self.assertIn("4807.0380,N", text, "positions are logged")
        self.assertIn('"last_gga_line": "$GPGGA,123519.00', text)
        for secret in ("hunter", "ter2", "aHVudGVyMg", "hun/", "hun@"):
            self.assertNotIn(secret, text)

    def test_ipv4_mapped_peers_count_as_their_ipv4_address(self):
        self.assertEqual(peer_address("::ffff:192.0.2.5"), "192.0.2.5")
        self.assertEqual(peer_address("::FFFF:c000:0205"), "192.0.2.5")
        self.assertEqual(peer_address("192.0.2.5"), "192.0.2.5")
        self.assertEqual(peer_address("2001:db8::1"), "2001:db8::1")
        self.assertEqual(peer_address("fe80::1%eth0"), "fe80::1%eth0")
        self.assertEqual(peer_address("not an address"), "not an address")


class RoverInputServer(CasterBase):
    async def test_what_a_rover_sends_is_logged_as_sent(self):
        port = await self.start()
        _, writer, _ = await ntrip_get(port, read_until=b"OK\r\n")
        last = RoverInput.GGA.replace("123519.00", "123521.00")
        writer.write(nmea(RoverInput.GGA) + nmea(RoverInput.RMC) + nmea(last) + b"hello\r\n")
        await until(lambda: self.caster.clients and self.caster.clients[0].other == 1)
        client = self.caster.snapshot()["clients"][0]
        self.assertEqual(client["sent"]["gga"], 2)
        self.assertNotIn("last_gga_line", client["sent"])
        self.assertEqual(client["position"]["quality"], 4)
        writer.close()
        await until(lambda: not self.caster.clients)
        events = self.log_lines()
        sent = [e for e in events if e["event"] == "rover_sent"]
        self.assertEqual([e["type"] for e in sent], ["GGA", "GPRMC", None])
        self.assertEqual(sent[0]["text"], nmea(RoverInput.GGA).decode().rstrip("\r\n"))
        self.assertIn("4807.038,N,01131.000,E", sent[1]["text"])
        self.assertEqual(sent[2]["text"], "(5 bytes)")
        disconnect = events[-1]
        self.assertEqual(disconnect["event"], "disconnect")
        self.assertEqual((disconnect["gga"], disconnect["nmea_other"], disconnect["other"]),
                         (2, 1, 1))
        self.assertEqual(disconnect["last_gga_line"], nmea(last).decode().rstrip("\r\n"))
        self.assertIs(disconnect["last_gga_shortened"], False)
        self.assertEqual(disconnect["types"], {"GPRMC": 1})

    async def test_a_reconnecting_address_cannot_crowd_out_another_rover(self):
        port = await self.start()
        with mock.patch.object(ConnectionLog, "PER_ADDRESS", 2), \
                mock.patch.object(ConnectionLog, "PER_MINUTE", 8):
            for n in range(10):   # 127.0.0.2 connects and closes over and over
                _, writer, _ = await ntrip_get(port, read_until=b"OK\r\n", local="127.0.0.2")
                writer.write(nmea(RoverInput.GGA))
                await until(lambda: self.caster.clients and self.caster.clients[0].gga == 1)
                writer.close()
                await until(lambda: not self.caster.clients)
            _, writer, _ = await ntrip_get(port, read_until=b"OK\r\n")
            last = RoverInput.GGA.replace("123519.00", "123530.00")
            writer.write(nmea(last))
            await until(lambda: self.caster.clients and self.caster.clients[0].gga == 1)
            writer.close()
            await until(lambda: not self.caster.clients)
        events = self.log_lines()
        looping = [e["event"] for e in events if e.get("address") == "127.0.0.2"]
        self.assertEqual(looping.count("disconnect"), 2, "its own budget for disconnects")
        self.assertEqual(len(looping) - looping.count("disconnect"), 2)
        normal = [e for e in events if e.get("address") == "127.0.0.1"]
        self.assertEqual([e["event"] for e in normal], ["connect", "rover_sent", "disconnect"])
        self.assertEqual(normal[-1]["last_gga_line"], nmea(last).decode().rstrip("\r\n"))


class UploadPolicy(UploadBase):
    async def run_until_stopped(self, reply, count):
        fake = FakeCaster(reply=reply)
        uploader = await self.make(fake, feed=True, state_path=self.dir / "state.json")
        self.hub.last_frame = self.time.now
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.state == "stopped", timeout=10)
            before = self.time.now
            await until(lambda: self.time.now > before + 5 * 86400, timeout=10)
        self.assertEqual(len(fake.requests), count)
        snapshot = uploader.snapshot()
        self.assertTrue(snapshot["alert"])
        self.assertEqual(snapshot["state"], "stopped")
        await uploader.stop()
        return uploader, snapshot

    async def test_three_refusals_without_an_accepted_connection_stop_the_tries(self):
        uploader, snapshot = await self.run_until_stopped(b"ERROR - Bad Password\r\n",
                                                          upload_module.MAX_REFUSALS)
        self.assertIn("3 refusals without an accepted connection in between",
                      snapshot["stopped"])
        self.assertIn("upload_password", snapshot["reason"])
        gaps = [s for s in self.time.sleeps if s > 1][:2]
        self.assertEqual(gaps, [upload_module.REFUSED_WAIT_S] * 2)

    async def test_two_ban_replies_without_an_accepted_connection_stop_the_tries(self):
        uploader, snapshot = await self.run_until_stopped(
            b"HTTP/1.1 403 Forbidden\r\n\r\n<html>Your IP has been Banned</html>",
            upload_module.MAX_BANS)
        self.assertIn("look like a ban", snapshot["stopped"])
        self.assertEqual([s for s in self.time.sleeps if s > 1][0], upload_module.BANNED_WAIT_S)

    async def test_an_accepted_connection_resets_the_count(self):
        fake = FakeCaster(reply=b"ERROR - Bad Password\r\n")
        uploader = await self.make(fake, feed=True)
        self.hub.last_frame = self.time.now
        self.time.hold = True
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.refusals == 1 and uploader.state == "waiting")
        fake.reply = b"OK\r\n"           # a rev1 caster other than SNIP
        self.time.hold = False
        await until(lambda: uploader.state == "connected")
        self.assertEqual(uploader.refusals, 0)

    async def test_silent_after_ok_is_closed_within_five_seconds(self):
        fake = FakeCaster()
        uploader = await self.make(fake)
        self.hub.publish(rtcm(1005))
        uploader.start()
        await until(lambda: uploader.state == "connected")
        self.time.hold = True
        self.time.now += upload_module.FIRST_BYTE_S + 1   # RTCM still recent, none sent
        self.hub.last_frame = self.time.now
        await until(lambda: fake.closed == 1)
        await until(lambda: uploader.state == "waiting")
        self.assertIn("within 5 s", uploader.reason)
        self.assertEqual(self.log_lines()[-1]["kind"], "silent")

    async def test_flowing_means_rtcm_in_the_last_three_seconds(self):
        self.assertEqual((upload_module.FLOWING_S, upload_module.QUIET_S), (3, 8))
        attempts = []

        async def record(host, port):
            attempts.append(1)
            raise ConnectionRefusedError(111, "refused")

        uploader = await self.make(connect=record)
        self.hub.last_frame = self.time.now - 4
        uploader.start()
        await until(lambda: uploader.state == "waiting for RTCM")
        await asyncio.sleep(0.05)
        self.assertEqual(attempts, [])

    def test_upload_waits_use_the_poll_setting(self):
        source = (ROOT / "gnss_base" / "upload.py").read_text()
        self.assertNotIn("self._sleep(POLL_S)", source)


class UploadState(UploadBase):
    """The state file keeps the waits across restarts."""

    def restart_time(self, later_s):
        """A new process: a different monotonic clock, the wall clock `later_s` on."""
        old = self.time
        new = FakeTime()
        new.now = 50.0
        new.start_wall = old.wall() + later_s - (new.now - 1000.0)
        return new

    def restarted(self, time_, password=SENTINEL, connect=None, fake_port=None):
        hub = RtcmHub(clock=time_.clock)
        time_.hub = hub
        hub.last_frame = time_.now
        port = fake_port or self.uploader.settings.port  # the same settings as before
        settings = UploadSettings(host="127.0.0.1", port=port, mountpoint="TESTMOUNT",
                                  password=Secret(password))
        options = {} if connect is None else {"connect": connect}
        uploader = Uploader(settings, hub, self.events, clock=time_.clock, wall=time_.wall,
                            sleep=time_.sleep, rand=lambda: 0.0, poll_s=0.01,
                            state_path=self.dir / "state.json", **options)
        self.addAsyncCleanup(uploader.stop)
        return uploader

    async def refuse_once(self):
        fake = FakeCaster(reply=b"ERROR - Bad Password\r\n")
        uploader = await self.make(fake, feed=True, state_path=self.dir / "state.json")
        self.hub.last_frame = self.time.now
        self.time.hold = True
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.state == "waiting")
        await uploader.stop()
        return uploader

    async def test_a_restart_within_the_wait_still_waits(self):
        await self.refuse_once()
        state = json.loads((self.dir / "state.json").read_text())
        self.assertEqual((state["refusals"], state["attempts_today"], state["kind"]),
                         (1, 1, "refused"))
        self.assertNotIn(SENTINEL, json.dumps(state))
        attempts = []

        async def record(host, port):
            attempts.append(new.now)
            raise ConnectionRefusedError(111, "refused")

        new = self.restart_time(600)     # restarted 10 min later
        uploader = self.restarted(new, connect=record)
        snapshot = uploader.snapshot()
        self.assertEqual((uploader.refusals, uploader.attempts_today), (1, 1))
        self.assertTrue(snapshot["alert"])
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: attempts)
            await uploader.stop()
        self.assertAlmostEqual(new.sleeps[0], upload_module.REFUSED_WAIT_S - 600, delta=0.01)
        self.assertEqual(uploader.attempts_today, 1 + len(attempts))

    async def test_two_refusals_then_a_restart_stops_after_one_more(self):
        await self.refuse_once()
        state = json.loads((self.dir / "state.json").read_text())
        state["refusals"] = 2
        (self.dir / "state.json").write_text(json.dumps(state))
        new = self.restart_time(3600)
        uploader = self.restarted(new)  # the same fake caster, still refusing
        self.assertEqual(uploader.refusals, 2)
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.state == "stopped")
        self.assertEqual(len(self.fake.requests), 2)

    async def test_changed_settings_drop_the_refusal_wait_but_keep_the_minute(self):
        await self.refuse_once()
        attempts = []

        async def record(host, port):
            attempts.append(new.now)
            raise ConnectionRefusedError(111, "refused")

        new = self.restart_time(10)      # 10 s after the refusal, a new password
        uploader = self.restarted(new, password="An0ther-pw", connect=record)
        self.assertEqual(uploader.refusals, 0)
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: attempts)
            await uploader.stop()
        self.assertAlmostEqual(new.sleeps[0], upload_module.FIRST_RETRY_S - 10, delta=0.01)

    async def test_a_ban_wait_survives_changed_settings(self):
        fake = FakeCaster(reply=b"HTTP/1.1 403 Forbidden\r\n\r\n")
        uploader = await self.make(fake, feed=True, state_path=self.dir / "state.json")
        self.hub.last_frame = self.time.now
        self.time.hold = True
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.state == "waiting")
        await uploader.stop()
        new = self.restart_time(3600)
        uploader = self.restarted(new, password="An0ther-pw")
        self.assertEqual(uploader.bans, 1)
        self.assertAlmostEqual(uploader._allowed_at() - new.now,
                               upload_module.BANNED_WAIT_S - 3600, delta=0.01)

    async def test_a_clock_step_back_cannot_make_the_wait_endless(self):
        await self.refuse_once()
        new = self.restart_time(-30 * 86400)
        with self.assertLogs("gnss_base", "WARNING") as logs:
            uploader = self.restarted(new)
        self.assertLessEqual(uploader._allowed_at() - new.now, upload_module.BANNED_WAIT_S)
        # A clock behind the last attempt (not synced after a boot): only the minute.
        self.assertAlmostEqual(uploader._allowed_at() - new.now, upload_module.FIRST_RETRY_S)
        self.assertIn("clock is behind", "\n".join(logs.output))

    async def test_an_unsynced_clock_at_boot_waits_a_minute_not_hours(self):
        fake = FakeCaster(reply=b"HTTP/1.1 403 Forbidden\r\n\r\n")   # a 4 h ban wait
        uploader = await self.make(fake, feed=True, state_path=self.dir / "state.json")
        self.hub.last_frame = self.time.now
        self.time.hold = True
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: uploader.state == "waiting")
        await uploader.stop()
        attempts = []

        async def record(host, port):
            attempts.append(new.now)
            raise ConnectionRefusedError(111, "refused")

        new = self.restart_time(-1_000_000_000)   # booted with the clock near 1970
        with self.assertLogs("gnss_base", "WARNING"):
            uploader = self.restarted(new, connect=record)
            uploader.start()
            await until(lambda: attempts)
            await uploader.stop()
        self.assertAlmostEqual(attempts[0] - 50.0, upload_module.FIRST_RETRY_S, delta=0.01)

    async def test_a_broken_state_file_is_ignored(self):
        for text in ("{not json", "[1, 2]", "null", '"text"'):
            (self.dir / "state.json").write_text(text)
            time_ = FakeTime()
            with self.assertLogs("gnss_base", "WARNING"):
                uploader = self.restarted(time_, fake_port=9)
            # Never at once: the minute between attempts holds from the start.
            self.assertEqual(uploader._allowed_at() - time_.now, upload_module.FIRST_RETRY_S)
            self.assertIn("upload state", uploader.snapshot()["state_error"])

    def write_state(self, time_, **fields):
        """A state file for the settings `restarted` uses, as a previous run left it."""
        (self.dir / "state.json").unlink(missing_ok=True)
        probe = self.restarted(time_, fake_port=9)
        wall = time_.wall()
        state = {"version": 1, "salt": "ab", "settings": probe._fingerprint("ab"),
                 "last_attempt_utc": wall - 10, "next_attempt_utc": wall + 600,
                 "kind": "failed", "refusals": 1, "bans": 0, "backoff": 120,
                 "reason": "Connection failed", "day": int(wall // 86400),
                 "attempts_today": 3, "bytes_today": 1000}
        state.update(fields)
        (self.dir / "state.json").write_text(json.dumps(state))
        return state

    async def test_non_finite_numbers_in_the_state_file_are_ignored(self):
        fields = ("last_attempt_utc", "next_attempt_utc", "refusals", "bans", "backoff",
                  "attempts_today", "bytes_today", "day")
        for key in fields:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(key=key, value=value):
                    time_ = FakeTime()
                    self.write_state(time_, **{key: value})
                    self.assertIn(("NaN" if value != value else "Infinity"),
                                  (self.dir / "state.json").read_text())
                    if key == "day":   # not a day number: the day's counts are not taken
                        uploader = self.restarted(time_, fake_port=9)
                        self.assertEqual(uploader.attempts_today, 0)
                    else:
                        with self.assertLogs("gnss_base", "WARNING"):
                            uploader = self.restarted(time_, fake_port=9)
                        self.assertIn("upload state", uploader.state_error)
                        self.assertEqual((uploader.refusals, uploader.attempts_today,
                                          uploader.bytes_today), (0, 0, 0))
                    wait = uploader._allowed_at() - time_.now
                    self.assertTrue(math.isfinite(wait))
                    self.assertGreaterEqual(wait, upload_module.FIRST_RETRY_S - 10)
                    self.assertTrue(math.isfinite(uploader._backoff))
                    uploader._last_attempt = time_.now
                    uploader._schedule("failed", "x")
                    after = uploader._allowed_at() - time_.now
                    self.assertTrue(math.isfinite(after))
                    self.assertGreaterEqual(after, upload_module.FIRST_RETRY_S)

    async def test_any_unexpected_state_is_ignored_never_crashes(self):
        time_ = FakeTime()
        self.write_state(time_, kind={"x": 1}, reason=7, day="today", salt="ab",
                         refusals="3", bans=[1], backoff=None)
        uploader = self.restarted(time_, fake_port=9)   # odd types: those fields skipped
        self.assertEqual((uploader.refusals, uploader.bans, uploader._backoff), (0, 0, 60))
        self.write_state(time_)
        with mock.patch.object(Uploader, "_fingerprint", side_effect=RuntimeError("boom")):
            with self.assertLogs("gnss_base", "WARNING"):
                uploader = self.restarted(time_, fake_port=9)
        self.assertIn("boom", uploader.state_error)
        self.assertEqual(uploader._allowed_at() - time_.now, upload_module.FIRST_RETRY_S)

    async def test_a_restart_at_the_daily_limit_waits_for_midnight_utc(self):
        time_ = FakeTime()
        state = self.write_state(time_, bytes_today=upload_module.DAY_LIMIT_BYTES,
                                 next_attempt_utc=time_.wall() + 60, kind="day")
        attempts = []

        async def record(host, port):
            attempts.append(time_.wall())
            raise ConnectionRefusedError(111, "refused")

        uploader = self.restarted(time_, connect=record, fake_port=9)
        self.assertEqual(uploader.bytes_today, upload_module.DAY_LIMIT_BYTES)
        with self.assertLogs("gnss_base", "WARNING"):
            uploader.start()
            await until(lambda: attempts)
            await uploader.stop()
        midnight = (state["day"] + 1) * 86400
        self.assertGreaterEqual(attempts[0], midnight)
        self.assertLess(attempts[0], midnight + 120)
        self.assertEqual(uploader.bytes_today, 0)
        # Below the limit, or the limit reached on an earlier day: no such wait.
        for fields in ({"bytes_today": upload_module.DAY_LIMIT_BYTES - 1},
                       {"bytes_today": upload_module.DAY_LIMIT_BYTES, "day": state["day"] - 1}):
            self.write_state(time_, **fields)
            self.assertEqual(self.restarted(time_, fake_port=9)._guard, "")

    async def test_bytes_today_is_saved_while_connected(self):
        fake = FakeCaster()
        path = self.dir / "state.json"
        uploader = await self.make(fake, state_path=path)
        self.hub.publish(rtcm(1005))
        self.time.hold = True

        def saved():
            try:
                return json.loads(path.read_text())["bytes_today"]
            except (OSError, ValueError):
                return None

        uploader.start()
        await until(lambda: uploader.state == "connected")
        self.hub.publish(rtcm(1074, 200))
        sent = uploader.bytes_today
        await asyncio.sleep(0.05)
        self.assertEqual(saved(), 0, "not yet: under 5 min and 10 MB")
        self.time.now += upload_module.SAVE_EVERY_S
        self.hub.last_frame = self.time.now
        await until(lambda: saved() == sent)
        with mock.patch.object(upload_module, "SAVE_EVERY_BYTES", 1000):
            for _ in range(5):
                self.hub.publish(rtcm(1074, 300))
            await until(lambda: saved() == uploader.bytes_today)
        self.assertGreater(saved(), sent + 1000)
        self.assertEqual(uploader.state, "connected")
        self.assertFalse(list(self.dir.glob("*.partial")))

    def test_the_app_keeps_the_state_in_its_data_folder(self):
        settings = load_settings(["--options", str(self.dir / "options.json")])
        self.assertEqual(settings["data_dir"], str(self.dir))
        settings = load_settings(["--options", "/nonexistent/o.json", "--data-dir", "/x"])
        self.assertEqual(settings["data_dir"], "/x")
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        self.assertIn('state_path=Path(settings["data_dir"]) / "upload-state.json"', source)


class Packaging040(unittest.TestCase):
    def test_mountpoint_schema_and_dockerignore(self):
        text = (ROOT / "config.yaml").read_text()
        self.assertIn("upload_mountpoint: match(^[A-Za-z0-9_.-]{0,64}$)", text)
        ignored = (ROOT / ".dockerignore").read_text().split()
        for pattern in ("**/__pycache__", "**/*.pyc", "tests"):
            self.assertIn(pattern, ignored)
        self.assertIn("COPY gnss_base /app/gnss_base", (ROOT / "Dockerfile").read_text())

    def test_page_shows_what_rovers_send_and_their_positions(self):
        html = (STATIC / "index.html").read_text()
        self.assertTrue('"Sent to us"' in html)
        self.assertTrue("roverMarks" in html)


# ---------------------------------------------------------------- packaging and page

class Packaging(unittest.TestCase):
    def test_config_yaml_version_options_and_ports(self):
        text = (ROOT / "config.yaml").read_text()
        self.assertIn(f'version: "{__version__}"', text)
        self.assertEqual(__version__, "0.7.0")
        self.assertIn("ports:\n  2101/tcp: 2101\n", text)
        self.assertIn("ports_description:\n  2101/tcp: ", text)
        options = re.search(r"\noptions:\n((?:  .*\n)+)", text).group(1)
        schema = re.search(r"\nschema:\n((?:  .*\n)+)", text).group(1)

        def keys(block):
            return [line.split(":")[0].strip() for line in block.splitlines()]

        # reference_frame is a drop-down that is unset until chosen (0.6.3): optional in
        # the schema and absent from the options defaults, so the app's DEFAULTS supply "".
        # The expected station IDs (0.7.0) are optional the same way (DEFAULTS: None).
        optional = {"reference_frame", "reference2_frame", "reference_expect_station",
                    "reference2_expect_station"}
        self.assertEqual(set(keys(options)) - {"device"}, set(DEFAULTS) - {"device"} - optional)
        for prefix in ("reference", "reference2"):
            self.assertIn(f"  {prefix}_expect_station: int(0,4095)?\n", schema)
            self.assertIsNone(DEFAULTS[f"{prefix}_expect_station"])
        self.assertEqual(set(keys(schema)), set(DEFAULTS))
        self.assertIn("upload_password: password", schema)
        self.assertIn("caster_password: password", schema)
        self.assertIn('upload_mountpoint: ""', options, "no mountpoint name in the defaults")
        self.assertIn('upload_password: ""', options)
        self.assertEqual(DEFAULTS["upload_mountpoint"], "")
        self.assertEqual(DEFAULTS["caster_password"], "")
        self.assertEqual(DEFAULTS["base_frame"], "ITRF2020")
        self.assertIn("  base_frame: ITRF2020\n", options)
        self.assertIn("  base_frame: list(ITRF2020|ITRF2014|ITRF2008|NAD83(2011))?\n", schema)
        caster, _ = output_settings(load_settings(["--options", "/nonexistent/options.json"]))
        self.assertEqual((caster.base_frame, caster.frame()), ("ITRF2020", "ITRF2020"))
        caster, _ = output_settings(load_settings(["--options", "/nonexistent/options.json",
                                                   "--base-frame", " nad83(2011) "]))
        self.assertEqual((caster.base_frame, caster.frame()), ("nad83(2011)", "NAD83(2011)"))
        caster, _ = output_settings(load_settings(["--options", "/nonexistent/options.json",
                                                   "--base-frame", "WGS84", "--base-epoch",
                                                   "2026.5"]))
        self.assertEqual(caster.problem(), "")
        self.assertIn("base_frame must be", caster.frame_problem())
        self.assertEqual(caster.base_epoch, "2026.5")
        self.assertEqual(DEFAULTS["base_epoch"], "")

    def test_shipped_profile_sends_ephemerides_every_minute(self):
        by_label = {item.label: item for item in profile.load()}
        item = by_label["RTCM: MSM type, elevation mask, ephemeris output"]
        # MSM_Type, MSM_Mode, MSM_ElevThd, reserved, reserved, EPH_Mode, EPH_Interval
        self.assertEqual(item.set, "PQTMCFGRTCM,W,4,0,-90.0,07,06,2,60")
        self.assertEqual(item.query, "PQTMCFGRTCM,R")
        old = ["4", "0", "-90.0", "07", "06", "1", "0"]
        new = ["4", "0", "-90.0", "07", "06", "2", "60"]
        self.assertEqual(item.compare(old)[0], "differ", "the 0.3 setting differs: Apply sends it")
        self.assertEqual(item.compare(new)[0], "match")

    def test_status_page_has_the_output_cards_and_shows_no_password(self):
        html = (STATIC / "index.html").read_text()
        for element in ('id="caster"', 'id="casterd"', 'id="clients"', 'id="upload"',
                        'id="uploadd"'):
            self.assertIn(element, html)
        self.assertNotRegex(html, r"\.password\b(?!_set)")


# ---------------------------------------------------------------- an independent client

def find_str2str():
    path = os.environ.get("STR2STR") or shutil.which("str2str")
    return path if path and os.access(path, os.X_OK) else None


@unittest.skipUnless(find_str2str(), "str2str (RTKLIB) not found; set STR2STR to run this")
class Str2strClient(LocalOnly):
    """RTKLIB's str2str as an NTRIP client of our caster: it must get the frames,
    byte for byte."""

    async def test_str2str_receives_the_stream(self):
        hub = RtcmHub()
        caster = Caster(CasterSettings(host="127.0.0.1", port=0, password=Secret("pw")),
                        hub, self.events)
        await caster.start()
        self.addAsyncCleanup(caster.stop)
        out = self.dir / "out.rtcm3"
        process = await asyncio.create_subprocess_exec(
            find_str2str(), "-in", f"ntrip://user:pw@127.0.0.1:{caster.port}/BASE",
            "-out", f"file://{out}", stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            await until(lambda: caster.clients, timeout=10)
            frames = [rtcm(1005)] + [rtcm(1074, 80, i) for i in range(30)] + [rtcm(1230)]
            for frame in frames:
                hub.publish(frame)
                await asyncio.sleep(0.01)
            expected = b"".join(frames)
            await until(lambda: out.exists() and out.stat().st_size >= len(expected), timeout=10)
        finally:
            process.terminate()
            await process.wait()
        self.assertEqual(out.read_bytes(), expected)
        self.assertIn("RTKLIB", next(e for e in self.log_lines()
                                     if e["event"] == "connect")["user_agent"])


if __name__ == "__main__":
    unittest.main()
