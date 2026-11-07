"""The app's NTRIP client for the reference check (0.6.2), against the fake caster on
localhost: the replies it understands, one connection at a time, the backoff, the
sourcetable check, chunked encoding, and that the password goes nowhere but the
Authorization header.

Run from ha-addon/gnss_base with: python -m unittest tests.test_ntripclient
"""
import asyncio
import ast
import base64
import json
import unittest
from unittest import mock

from gnss_base import ntripclient
from gnss_base.frames import crc24q
from gnss_base.hub import Secret
from gnss_base.ntripclient import (FIRST_RETRY_S, MAX_RETRY_S, REFUSED_WAIT_S, STABLE_S,
                                   ChunkDecoder, NtripClient, classify_reply,
                                   parse_sourcetable, unsuitable)
from gnss_base.reference import ReferenceSettings
from tests.fake_caster import FakeCaster, str_line

SENTINEL = "Cli-S3ntinel-pw-9d1e"


def rtcm(number, size=19):
    payload = bytes([number >> 4, (number & 15) << 4]) + bytes(size - 2)
    head = bytes([0xD3, size >> 8, size & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


def settings(port, **fields):
    base = dict(enabled=True, host="127.0.0.1", port=port, mountpoint="MP_1", user="user",
                password=Secret(SENTINEL))
    base.update(fields)
    return ReferenceSettings(**base)


class Clock:
    """A monotonic clock and a sleep that advances it (and lets the loop run)."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.hold = False    # while set, a sleep waits instead of advancing the clock

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        while self.hold:
            await asyncio.sleep(0.005)
        self.now += max(seconds, 0)
        await asyncio.sleep(0.01)


async def until(condition, timeout=5.0, what=""):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"not in time: {what}")
        await asyncio.sleep(0.01)


class Parsing(unittest.TestCase):
    def test_sourcetable_records(self):
        text = ("SOURCETABLE 200 OK\r\n\r\nCAS;host;2101;x;y;0;USA;0;0;\r\n"
                + str_line("A", nmea="1", solution="1", misc="ITRF2014 epoch 2010.00")
                + "STR;B;short\r\n" + "ENDSOURCETABLE\r\n")
        records = parse_sourcetable(text)
        self.assertEqual([r["mountpoint"] for r in records], ["A", "B"])
        self.assertEqual((records[0]["format"], records[0]["nav_system"], records[0]["nmea"],
                          records[0]["solution"], records[0]["misc"]),
                         ("RTCM 3.2", "GPS+GLO+GAL+BDS", "1", "1", "ITRF2014 epoch 2010.00"))
        self.assertEqual(records[1]["identifier"], "short")
        self.assertEqual(records[1]["misc"], "")
        self.assertIn("GGA position", unsuitable(records[0]))
        self.assertEqual(unsuitable(records[1]), "")
        self.assertIn("network solution", unsuitable({"nmea": "0", "solution": "1"}))
        self.assertIn("not RTCM 3", unsuitable({"format": "RTCM 2.3"}))
        self.assertIn("not RTCM 3", unsuitable({"format": "RAW"}))
        for fmt in ("RTCM 3.2", "RTCM3", "rtcm 3.3", ""):
            self.assertEqual(unsuitable({"format": fmt}), "", fmt)

    def test_reply_classes(self):
        self.assertEqual(classify_reply("ICY 200 OK"), ("ok", "ICY 200 OK"))
        self.assertEqual(classify_reply("HTTP/1.1 200 OK\r\nServer: x")[0], "ok")
        self.assertEqual(classify_reply("HTTP/1.0 401 Unauthorized")[0], "refused")
        self.assertEqual(classify_reply("HTTP/1.1 404 Not Found")[0], "not found")
        self.assertEqual(classify_reply("SOURCETABLE 200 OK\r\n")[0], "not found")
        self.assertEqual(classify_reply("HTTP/1.1 503 Busy"), ("failed", "HTTP/1.1 503 Busy"))
        # Refusals not phrased as HTTP 401 wait the long wait too.
        self.assertEqual(classify_reply("ERROR - Bad Password")[0], "refused")
        self.assertEqual(classify_reply("ICY 401 Unauthorized")[0], "refused")
        self.assertEqual(classify_reply("HTTP/1.0 403 unauthorised")[0], "refused")
        self.assertEqual(classify_reply("")[0], "failed")

    def test_chunk_decoder(self):
        decoder = ChunkDecoder()
        self.assertEqual(decoder.feed(b"3\r\nabc\r\n2;ext\r\nde\r\n"), b"abcde")
        self.assertEqual(decoder.feed(b"4\r\nfg"), b"fg")
        self.assertEqual(decoder.feed(b"hi\r\n0\r\n\r\n"), b"hi")
        self.assertTrue(decoder.ended)
        # A chunk's trailing CRLF split across reads (a normal TCP read pattern), and a
        # chunk size line split too.
        split = ChunkDecoder()
        self.assertEqual(split.feed(b"3\r\nabc"), b"abc")
        self.assertEqual(split.feed(b"\r"), b"")
        self.assertEqual(split.feed(b"\n2\r\nde\r"), b"de")
        self.assertEqual(split.feed(b"\n1"), b"")
        self.assertEqual(split.feed(b"\r\nf\r\n0\r\n\r\n"), b"f")
        self.assertEqual((split.errors, split.ended), (0, True))
        byte_by_byte = ChunkDecoder()
        got = b"".join(byte_by_byte.feed(bytes([b])) for b in b"4\r\nwxyz\r\n2\r\nab\r\n0\r\n\r\n")
        self.assertEqual((got, byte_by_byte.errors, byte_by_byte.ended), (b"wxyzab", 0, True))
        bad = ChunkDecoder()
        self.assertEqual(bad.feed(b"zz\r\nabc"), b"")
        self.assertEqual((bad.errors, bad.ended), (1, True))
        long = ChunkDecoder()
        long.feed(b"a" * 100)
        self.assertEqual((long.errors, long.ended), (1, True))


class Streaming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.caster = FakeCaster(data=rtcm(1074, 40) + rtcm(1005), period=0.02)
        self.port = await self.caster.start()
        self.got = []
        self.clock = Clock()

    async def asyncTearDown(self):
        await self.caster.stop()

    def client(self, **fields):
        made = NtripClient(settings(self.port, **fields), self.got.append, version="t",
                           clock=self.clock, sleep=self.clock.sleep, rand=lambda: 0.0)
        self.addAsyncCleanup(made.stop)
        return made

    async def test_icy_stream_after_a_sourcetable_check(self):
        client = self.client()
        client.start()
        await until(lambda: len(self.got) >= 3, what=client.snapshot())
        snap = client.snapshot()
        self.assertEqual(snap["state"], "connected")
        self.assertEqual(snap["sourcetable"]["mountpoint"], "MP_1")
        self.assertEqual(snap["sourcetable"]["format"], "RTCM 3.2")
        self.assertEqual(snap["last_reply"], "ICY 200 OK")
        self.assertEqual((snap["connections"], snap["attempts_today"], snap["chunked"]),
                         (1, 1, False))
        self.assertGreater(snap["bytes"], 0)
        self.assertEqual(b"".join(self.got)[:len(rtcm(1074, 40))], rtcm(1074, 40))
        self.assertEqual([path for path, _ in self.caster.requests], ["/", "/MP_1"])
        table, stream = (headers for _, headers in self.caster.requests)
        self.assertNotIn("authorization", table, "no login for the sourcetable")
        token = base64.b64encode(f"user:{SENTINEL}".encode()).decode()
        self.assertEqual(stream["authorization"], f"Basic {token}")
        self.assertEqual((stream["ntrip-version"], stream["user-agent"]),
                         ("Ntrip/2.0", "NTRIP gnss_base/t"))
        self.assertEqual(self.caster.sourcetable_requests, 1)
        self.assertNotIn(SENTINEL, json.dumps(snap))

    async def test_http_and_chunked_streams(self):
        for behaviour in ("http", "chunked", "icy-blank"):
            self.caster.mountpoints = {"MP_1": behaviour}
            self.got.clear()
            client = self.client()
            client.start()
            await until(lambda: len(self.got) >= 3, what=client.snapshot())
            snap = client.snapshot()
            self.assertEqual((snap["state"], snap["chunked"]), ("connected", behaviour == "chunked"))
            self.assertEqual(snap["last_reply"],
                             "ICY 200 OK" if behaviour == "icy-blank" else "HTTP/1.1 200 OK")
            data = b"".join(self.got)
            self.assertTrue(data.startswith(rtcm(1074, 40) + rtcm(1005)), behaviour)
            self.assertNotIn(b"\r\n", data[:len(rtcm(1074, 40))], "no chunk framing in the data")
            await client.stop()

    async def test_refused_login_waits_five_minutes(self):
        self.caster.login = ("user", "other")
        self.clock.hold = True
        client = self.client()
        with self.assertLogs("gnss_base", "INFO") as logs:
            client.start()
            await until(lambda: client.state == "login refused", what=client.snapshot())
        text = "\n".join(logs.output)
        self.assertIn("Login refused", text)
        self.assertNotIn(SENTINEL, text, "the add-on log never carries the password")
        self.assertNotIn(base64.b64encode(f"user:{SENTINEL}".encode()).decode(), text)
        snap = client.snapshot()
        self.assertIn("Login refused by the caster (HTTP/1.1 401 Unauthorized)", snap["reason"])
        self.assertIn("reference_user and reference_password", snap["reason"])
        self.assertEqual(snap["retry_in_s"], REFUSED_WAIT_S)
        self.assertEqual(self.caster.stream_connections, 0)
        self.assertEqual(self.caster.open, 0, "the socket is closed while waiting")
        # The next attempt comes only after the wait, and checks the sourcetable again.
        attempts = len(self.caster.requests)
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.caster.requests), attempts)
        self.assertNotIn(SENTINEL, json.dumps(client.snapshot()))
        self.clock.hold = False
        await until(lambda: len(self.caster.requests) > attempts, what=self.caster.requests)
        self.assertEqual(self.caster.requests[attempts][0], "/", "the table is checked again")

    async def test_large_http_and_chunked_sourcetables(self):
        """An NTRIP 2 caster answers with HTTP headers, possibly chunked, and a table
        of hundreds of mountpoints: ours, listed last, is still found and checked."""
        self.caster.table_extra = "".join(str_line(f"OTHER_{i:03d}", nmea="1")
                                          for i in range(120))   # about 10 kB before ours
        for style in ("http", "chunked", "v1"):
            self.caster.table_style = style
            self.caster.mountpoints = {"MP_1": {"misc": "ours"}}
            self.got.clear()
            client = self.client()
            client.start()
            await until(lambda: client.state == "connected", what=(style, client.snapshot()))
            snap = client.snapshot()
            self.assertEqual((snap["listed"], snap["sourcetable_size"],
                              snap["sourcetable"]["misc"]), (True, 121, "ours"), style)
            await client.stop()
            # An unsuitable record late in a chunked table is still seen.
            self.caster.mountpoints = {"MP_1": {"nmea": "1"}}
            client = self.client()
            client.start()
            await until(lambda: client.state == "unsuitable mountpoint", what=client.snapshot())
            await client.stop()

    async def test_mountpoint_missing_from_the_sourcetable_is_still_asked_for(self):
        """Casters hide private mountpoints from the table: the stream request decides,
        and a 404 then says the table did not list it either."""
        self.clock.hold = True
        client = self.client(mountpoint="NOPE")
        client.start()
        await until(lambda: client.state == "mountpoint not found", what=client.snapshot())
        snap = client.snapshot()
        self.assertEqual(snap["reason"], "Mountpoint not found (HTTP/1.1 404 Not Found; not "
                         "listed in the caster's sourcetable of 1 mountpoints): check "
                         "reference_mountpoint (case matters).")
        self.assertEqual((snap["sourcetable"], snap["listed"]), ({}, False))
        self.assertEqual(snap["retry_in_s"], REFUSED_WAIT_S)
        self.assertEqual([path for path, _ in self.caster.requests], ["/", "/NOPE"])
        self.assertEqual(self.caster.most_open, 1, "the table's connection closed first")
        # A hidden mountpoint that exists streams, and the card says it was not listed.
        await client.stop()
        self.caster.mountpoints["HIDDEN"] = "icy"
        self.caster.table_extra = ""
        hidden = NtripClient(settings(self.port, mountpoint="HIDDEN"), self.got.append,
                             clock=self.clock, sleep=self.clock.sleep)
        self.addAsyncCleanup(hidden.stop)
        original = self.caster.sourcetable
        self.caster.sourcetable = lambda: original().replace(b"STR;HIDDEN;", b"STR;GONE;")
        hidden.start()
        await until(lambda: hidden.state == "connected", what=hidden.snapshot())
        self.assertEqual((hidden.listed, hidden.sourcetable), (False, {}))

    async def test_not_found_by_the_stream_request(self):
        """Without the sourcetable check: a 404, or a version 1 caster's sourcetable
        in reply, both mean "mountpoint not found"."""
        client = NtripClient(settings(self.port, mountpoint="NOPE"), self.got.append,
                             clock=self.clock, sleep=self.clock.sleep, check_sourcetable=False)
        self.addAsyncCleanup(client.stop)
        client.start()
        await until(lambda: client.state == "mountpoint not found")
        self.assertIn("HTTP/1.1 404 Not Found", client.reason)
        await client.stop()
        self.caster.v1_table_for_unknown = True
        client = NtripClient(settings(self.port, mountpoint="NOPE"), self.got.append,
                             clock=self.clock, sleep=self.clock.sleep, check_sourcetable=False)
        self.addAsyncCleanup(client.stop)
        client.start()
        await until(lambda: client.state == "mountpoint not found")
        self.assertIn("SOURCETABLE 200 OK", client.reason)

    async def test_unsuitable_mountpoint_is_refused_before_login(self):
        self.caster.mountpoints = {"MP_1": {"nmea": "1"}}
        client = self.client()
        client.start()
        await until(lambda: client.state == "unsuitable mountpoint", what=client.snapshot())
        self.assertIn("wants a GGA position", client.reason)
        self.assertEqual([path for path, _ in self.caster.requests], ["/"])
        self.caster.mountpoints = {"MP_1": {"solution": "1"}}
        client = self.client()
        client.start()
        await until(lambda: client.state == "unsuitable mountpoint" and "network solution"
                    in client.reason)
        await client.stop()
        self.caster.mountpoints = {"MP_1": {"fmt": "RTCM 2.3"}}
        client = self.client()
        client.start()
        await until(lambda: client.state == "unsuitable mountpoint" and "not RTCM 3"
                    in client.reason)

    async def test_sourcetable_text_is_scrubbed_of_the_password(self):
        """Caster text never reaches the page unscrubbed, the STR record included."""
        self.caster.mountpoints = {"MP_1": {"misc": f"echo {SENTINEL} back"}}
        client = self.client()
        client.start()
        await until(lambda: client.state == "connected")
        snap = client.snapshot()
        self.assertEqual(snap["sourcetable"]["misc"], "echo *** back")
        self.assertNotIn(SENTINEL, json.dumps(snap))

    async def test_no_data_reconnects_with_backoff_and_one_connection_at_a_time(self):
        self.caster.mountpoints = {"MP_1": "silent"}
        client = NtripClient(settings(self.port), self.got.append, clock=self.clock,
                             sleep=self.clock.sleep, rand=lambda: 0.0, stale_s=0.2)
        self.addAsyncCleanup(client.stop)
        self.clock.hold = True
        client.start()
        await until(lambda: client.state == "connected")
        await until(lambda: client.state == "no data", timeout=3, what=client.snapshot())
        self.assertEqual(client.reason, "No data from the caster for 0 s.")
        self.assertEqual(client.retry_in_s(), FIRST_RETRY_S)
        self.clock.hold = False
        await until(lambda: self.caster.stream_connections >= 3, timeout=5)
        self.assertEqual(self.caster.most_open, 1, "never two connections at once")
        delays = [s for s in self.clock.sleeps if s >= 1]
        self.assertEqual(delays[:2], [FIRST_RETRY_S, FIRST_RETRY_S * 2], "doubling")
        self.assertEqual([path for path, _ in self.caster.requests][:3], ["/", "/MP_1", "/MP_1"],
                         "the sourcetable is not asked again after a connection that worked")

    async def test_a_stable_connection_resets_the_backoff(self):
        client = self.client()
        client._backoff = MAX_RETRY_S
        client.start()
        await until(lambda: client.state == "connected")
        self.clock.now += STABLE_S + 1
        self.clock.hold = True
        await self.caster.drop_clients()
        await until(lambda: client.state == "backoff", what=client.snapshot())
        self.assertEqual(client.reason, "The caster closed the connection.")
        self.assertEqual(client.retry_in_s(), FIRST_RETRY_S)
        self.assertEqual(client.snapshot()["connected_s"], None)

    async def test_connection_refused_is_backoff_with_the_error(self):
        await self.caster.stop()
        client = self.client()
        client.start()
        await until(lambda: client.state == "backoff", what=client.snapshot())
        self.assertIn("Connection failed", client.reason)
        self.assertNotIn(SENTINEL, client.reason)
        self.assertEqual(client.snapshot()["connections"], 0)

    async def test_an_internal_error_is_retried_not_fatal(self):
        """0.6.2 review: a bug in an attempt must not leave the client silently off
        (the 0.6.1 symptom): it is logged, shown as the reason, and retried."""
        client = self.client()
        calls = []

        async def broken():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(f"an NTRIP stream connection is already open {SENTINEL}")
            return "closed", "fine"

        client._stream = broken
        self.clock.hold = True
        with self.assertLogs("gnss_base", "ERROR") as logs:
            client.start()
            await until(lambda: client.state == "backoff" and "Internal error" in client.reason)
        self.assertIn("internal error", "\n".join(logs.output).lower())
        self.assertEqual(client.reason, "Internal error: RuntimeError: an NTRIP stream "
                         "connection is already open ***")
        self.assertEqual(client.retry_in_s(), 60.0)
        self.assertTrue(client.started)
        self.assertEqual(client._backoff, FIRST_RETRY_S, "no doubling for an internal error")
        self.clock.hold = False
        await until(lambda: len(calls) >= 2, what=client.snapshot())
        self.assertEqual(client.internal_errors, 1)
        self.assertFalse(client.died)
        # The traceback in the log is scrubbed too.
        text = "\n".join(logs.output)
        self.assertNotIn(SENTINEL, text)
        self.assertIn("already open ***", text)
        # A failure inside the guard itself marks the client dead (the check shows it),
        # ends the task cleanly (no exception chain for anyone to log unscrubbed), and
        # logs the type only.
        client._scrub = lambda text: 1 / 0
        with self.assertLogs("gnss_base", "ERROR") as logs:
            calls.clear()

            async def broken_again():
                raise RuntimeError(f"again {SENTINEL}")

            client._stream = broken_again
            client._next_attempt = None
            await until(lambda: client.died, timeout=5, what=client.snapshot())
            await asyncio.sleep(0.05)
        self.assertTrue(client._task.done())
        self.assertIsNone(client._task.exception(), "ended, not raised")
        text = "\n".join(logs.output)
        self.assertIn("NTRIP client stopped: ZeroDivisionError", text)
        self.assertNotIn(SENTINEL, text)
        await client.stop()

    async def test_a_sourcetable_sent_as_http_200_is_not_a_stream(self):
        self.caster.mountpoints = {"MP_1": "table-200"}
        self.clock.hold = True
        client = self.client()
        client.start()
        await until(lambda: client.state == "mountpoint not found", what=client.snapshot())
        self.assertIn("HTTP/1.1 200 OK (a sourcetable, not the stream)", client.reason)
        self.assertEqual(self.got, [], "nothing of it reached the data path")

    async def test_chunked_table_with_the_end_marker_across_chunks_and_a_held_socket(self):
        """The caster holds the socket after the table (no EOF): the last chunk ends the
        read, whatever the chunk boundaries cut through."""
        self.caster.table_style = "chunked"
        self.caster.mountpoints = {"MP_1": {"nmea": "1"}}
        text = self.caster.sourcetable()
        body_start = text.index(b"\r\n\r\n") + 4
        # A chunk size that puts a boundary inside "ENDSOURCETABLE".
        raw = text[body_start:]
        self.caster.chunk_size = 1
        self.caster.chunk_size = max(1, (len(raw) - len(b"ENDSOURCETABLE\r\n") + 5) // 2)
        self.clock.hold = True
        client = self.client()
        client.start()
        await until(lambda: client.state == "unsuitable mountpoint", timeout=5,
                    what=client.snapshot())
        self.assertEqual(client.sourcetable_size, 1)

    async def test_a_refusal_without_a_line_ending_is_still_a_refusal(self):
        """A caster that writes "ERROR - Bad Password" and then holds the socket open:
        the partial reply is classified after the reply timeout, as refused."""
        import gnss_base.ntripclient as module
        self.caster.mountpoints = {"MP_1": "bare-error"}
        self.clock.hold = True
        with mock.patch.object(module, "REPLY_TIMEOUT_S", 0.3):
            client = self.client()
            client.start()
            await until(lambda: client.state == "login refused", timeout=5, what=client.snapshot())
        self.assertIn("ERROR - Bad Password", client.reason)
        self.assertEqual(client.retry_in_s(), REFUSED_WAIT_S)

    async def test_a_caster_without_a_sourcetable_still_gets_the_stream_request(self):
        """The table is advisory: a 404 at "/" (or a held socket) does not stop the
        attempt."""
        import gnss_base.ntripclient as module
        self.caster.root_reply = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
        client = self.client()
        client.start()
        await until(lambda: client.state == "connected", what=client.snapshot())
        self.assertEqual((client.sourcetable, client.listed), ({}, None))
        from pathlib import Path
        page = (Path(ntripclient.__file__).parent / "static" / "index.html").read_text()
        self.assertIn("the caster gave no sourcetable at /", page, "what the card says then")
        await client.stop()
        # Headers of a 200 that never end are not a stream either.
        self.caster.root_reply = None
        self.caster.mountpoints = {"MP_1": "headers-only"}
        self.got.clear()
        with mock.patch.object(module, "REPLY_TIMEOUT_S", 0.3):
            self.clock.hold = True
            client = self.client()
            client.start()
            await until(lambda: client.state == "backoff", timeout=5, what=client.snapshot())
        self.assertIn("Incomplete reply to the stream request: HTTP/1.1 200 OK", client.reason)
        self.assertEqual(self.got, [])
        await client.stop()
        self.caster.mountpoints = {"MP_1": "icy"}
        self.clock.hold = False
        # A version 1 table with no blank line after the status line still parses.
        self.caster.root_reply = (b"SOURCETABLE 200 OK\r\n" + str_line("MP_1", nmea="1").encode()
                                  + b"ENDSOURCETABLE\r\n")
        client = self.client()
        client.start()
        await until(lambda: client.state == "unsuitable mountpoint", what=client.snapshot())
        self.assertEqual(client.sourcetable_size, 1)
        self.caster.root_reply = b"HTTP/1.1 200 OK\r\n"     # headers never finished
        with mock.patch.object(module, "SOURCETABLE_TIMEOUT_S", 0.3):
            client = self.client()
            with self.assertLogs("gnss_base", "WARNING") as logs:
                client.start()
                await until(lambda: client.state == "connected", timeout=5, what=client.snapshot())
        self.assertIn("no complete sourcetable", "\n".join(logs.output))

    async def test_connect_callback_runs_once_per_stream(self):
        connects = []
        client = NtripClient(settings(self.port), self.got.append, clock=self.clock,
                             sleep=self.clock.sleep, on_connect=lambda: connects.append(1))
        self.addAsyncCleanup(client.stop)
        client.start()
        await until(lambda: len(self.got) >= 2)
        self.assertEqual(connects, [1])
        await self.caster.drop_clients()
        await until(lambda: client.connections == 2, timeout=5)
        self.assertEqual(connects, [1, 1])

    async def test_stop_closes_the_socket_and_a_handler_error_is_counted(self):
        def boom(data):
            raise RuntimeError("disk gone")

        client = NtripClient(settings(self.port), boom, clock=self.clock, sleep=self.clock.sleep)
        client.start()
        with self.assertLogs("gnss_base", "ERROR"):
            await until(lambda: client.data_errors >= 1)
        self.assertEqual(client.state, "connected", "the stream goes on")
        await client.stop()
        self.assertEqual(client.state, "off")
        await until(lambda: self.caster.open == 0)

    async def test_bytes_per_second_and_day_roll(self):
        client = self.client()
        client.start()
        await until(lambda: client.bytes >= 3 * len(self.caster.data))
        self.clock.now += 5
        rate = client.snapshot()["bytes_per_s"]
        self.assertGreater(rate, 0)
        self.assertLessEqual(rate, client.bytes)
        client._day = "yesterday"
        client._roll_day()
        self.assertEqual(client.attempts_today, 0)


class NoPathToTheReceiver(unittest.TestCase):
    def test_client_imports_nothing_that_reaches_the_receiver(self):
        source = (ntripclient.__file__)
        tree = ast.parse(open(source).read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "") + ":" + ",".join(a.name for a in node.names))
        text = " ".join(imported)
        for forbidden in ("commands", "control", "receiver", "serial", "web", "status"):
            self.assertNotRegex(text, rf"\b{forbidden}\b", f"ntripclient imports {forbidden}")


if __name__ == "__main__":
    unittest.main()
