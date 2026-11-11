"""Stage 3c: the live measured position (rtkrcv runner, its feed and solution servers).

Run from ha-addon/gnss_base with: python -m unittest tests.test_measured

Localhost only. A fake rtkrcv (tests/fake_rtkrcv.py) stands in for the real one; the real
one is used only by the smoke test, which is skipped unless a binary is found (`RTKRCV`
environment variable, or `rtkrcv` on PATH). No RTCM data from a real receiver is used:
the frames are synthetic, and the positions made up.
"""
import ast
import asyncio
import gzip
import json
import os
import pty
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from gnss_base.frames import crc24q
from gnss_base import rtcm as rtcmdec
from gnss_base.__main__ import DEFAULTS, OVERRIDES, load_settings, measured_settings
from gnss_base.hub import ConnectionLog, RtcmHub
from gnss_base.measured import (CACHE_NAME, CLIENT_BUFFER, CONFIG_NAME, FeedServer,
                                MeasuredSettings, OutputLines, RtkrcvRunner, SolutionLog,
                                SolutionServer,
                                build_config, is_msm, parse_solution, read_proc_stats)

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake_rtkrcv.py"

# Made-up positions near 0/0.
LINE = ("2026/01/02 03:04:05.000   0.000001000    0.000002000     3.0000   5  20"
        "   1.2000   0.9000   2.5000   0.1000   0.2000   0.3000   0.00    0.0")


def eph(prn, size=67):
    """A made-up GPS ephemeris frame (1019) for satellite `prn`; the rest zeros."""
    payload = ((1019 << 12 | prn << 6)).to_bytes(3, "big") + bytes(size - 3)
    head = bytes([0xD3, size >> 8, size & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


def rtcm(number, size=19, fill=0):
    payload = bytes([number >> 4, (number & 15) << 4]) + bytes([fill]) * (size - 2)
    head = bytes([0xD3, size >> 8, size & 255]) + payload
    return head + crc24q(head).to_bytes(3, "big")


class Config(unittest.TestCase):
    def test_golden_file(self):
        golden = (ROOT / "tests" / "rtkrcv_golden.conf").read_text()
        self.assertEqual(build_config(40001, 40002, 15, 35), golden)

    def test_masks_and_ports_from_options(self):
        text = build_config(1234, 5678, elevation_mask=10, snr_mask=38)
        self.assertIn("inpstr1-path   =127.0.0.1:1234\n", text)
        self.assertIn("outstr1-path   =127.0.0.1:5678\n", text)
        self.assertIn("pos1-elmask    =10\n", text)
        self.assertIn("pos1-snrmask_r =on\n", text)
        self.assertIn("pos1-snrmask_L1=" + ",".join(["38"] * 9) + "\n", text)
        off = build_config(1234, 5678, snr_mask=0)
        self.assertIn("pos1-snrmask_r =off\n", off)

    def test_no_secrets_no_position_no_listening_ports(self):
        text = build_config(40001, 40002)
        for word in ("passwd", "password", "user", "ant2-pos", "ant1-pos", "tcpsvr",
                     "ntripc", "ntrips", "file://"):
            self.assertNotIn(word, text)
        settings = dict(line.split("=", 1) for line in text.splitlines()
                        if line and not line.startswith("#"))
        types = {k.strip(): v for k, v in settings.items() if k.strip().endswith("-type")}
        self.assertEqual({v for v in types.values()} - {"off"}, {"tcpcli"})

    def test_bad_values_are_refused(self):
        for args in ((0, 1), (1, 70000), (1, 2, -1), (1, 2, 15, 99), (1, 2, "15"),
                     (1, 2, True)):
            with self.assertRaises(ValueError, msg=args):
                build_config(*args)


class Parser(unittest.TestCase):
    def test_a_solution_line(self):
        solution, why = parse_solution(LINE)
        self.assertIsNone(why)
        self.assertEqual(solution["gpst"], "2026-01-02T03:04:05")
        self.assertEqual((solution["lat"], solution["lon"], solution["height_m"]),
                         (0.000001, 0.000002, 3.0))
        self.assertEqual((solution["quality"], solution["satellites"]), (5, 20))
        self.assertEqual((solution["sigma_n_m"], solution["sigma_e_m"], solution["sigma_u_m"]),
                         (1.2, 0.9, 2.5))
        json.dumps(solution, allow_nan=False)

    def test_time_is_rounded_to_the_second(self):
        for text, expected in (("05.996", "06"), ("05.499", "05"), ("59.700", "00")):
            line = LINE.replace("03:04:05.000", "03:04:" + text)
            solution, _ = parse_solution(line.encode())
            self.assertTrue(solution["gpst"].endswith(":" + expected), solution["gpst"])
        solution, _ = parse_solution(LINE.replace("2026/01/02 03:04:05.000",
                                                  "2026/12/31 23:59:59.900"))
        self.assertEqual(solution["gpst"], "2027-01-01T00:00:00")

    def test_lines_that_are_not_solutions(self):
        cases = {
            "% (lat/lon/height=WGS84/ellipsoidal,Q=1:fix,2:float,3:sbas,4:dgps,5:single)":
                "header",
            "": "empty", "   \r": "empty",
            LINE + " extra": "malformed", LINE.rsplit(" ", 1)[0]: "malformed",
            LINE.replace("0.000001000", "nan"): "malformed",
            LINE.replace("0.000001000", "inf"): "malformed",
            LINE.replace("3.0000", "1e400"): "malformed",
            LINE.replace("2026/01/02", "2026/13/02"): "malformed",
            LINE.replace("   5  20", " 5.5  20"): "malformed",
            LINE.replace("3.0000", "200000.0"): "implausible",
            LINE.replace("0.000001000", "91.0"): "implausible",
            LINE.replace("   5  20", "   0  20"): "implausible",
            LINE.replace("   5  20", "   5  -1"): "implausible",
            LINE.replace("1.2000", "-1.200"): "implausible",
            "\x00\xff garbage": "malformed",
        }
        for line, why in cases.items():
            self.assertEqual(parse_solution(line), (None, why), line)
        self.assertEqual(parse_solution(b"\xff\xfe" * 10)[1], "malformed")


class Servers(unittest.IsolatedAsyncioTestCase):
    async def connect(self, server):
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        self.addCleanup(writer.close)
        await asyncio.sleep(0.02)
        return reader, writer

    async def test_feed_sends_every_frame_byte_exact_to_the_newest_client(self):
        feed = FeedServer()
        port = await feed.start()
        self.addAsyncCleanup(feed.stop)
        self.assertGreater(port, 0)
        hub = RtcmHub()
        hub.subscribe("measured", lambda frame, number: feed.send(frame))
        frames = [rtcm(1005), rtcm(1074, 60, 7), rtcm(1033, 30), rtcm(1019, 67, 3)]
        hub.publish(frames[0])   # no client yet: dropped, not queued
        first, first_writer = await self.connect(feed)
        hub.publish(frames[1])
        self.assertEqual(await asyncio.wait_for(first.readexactly(len(frames[1])), 2),
                         frames[1])
        second, _ = await self.connect(feed)   # rtkrcv reconnected: the newest wins
        self.assertEqual(await asyncio.wait_for(first.read(), 2), b"", "old one closed")
        for frame in frames[1:]:
            hub.publish(frame)
        expected = b"".join(frames[1:])
        self.assertEqual(await asyncio.wait_for(second.readexactly(len(expected)), 2),
                         expected)
        self.assertEqual((feed.connections, feed.frames_sent, feed.frames_skipped), (2, 4, 0))

    async def test_feed_reads_and_drops_what_the_client_sends(self):
        feed = FeedServer()
        await feed.start()
        self.addAsyncCleanup(feed.stop)
        reader, writer = await self.connect(feed)
        writer.write(b"$GPGGA,anything\r\n" * 100 + bytes(range(256)))
        await writer.drain()
        await asyncio.sleep(0.05)
        self.assertEqual(feed.bytes_received, 17 * 100 + 256)
        feed.send(rtcm(1074))
        self.assertEqual(await asyncio.wait_for(reader.readexactly(25), 2), rtcm(1074))

    async def test_feed_skips_frames_for_a_slow_reader(self):
        feed = FeedServer(max_buffer=1000)
        await feed.start()
        self.addAsyncCleanup(feed.stop)
        # A client that never reads, with a small receive buffer: the slow reader. The
        # app side is left as it is.
        client = socket.socket()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client.setblocking(False)
        self.addCleanup(client.close)
        await asyncio.get_running_loop().sock_connect(client, ("127.0.0.1", feed.port))
        await self.until_connected(feed)
        big = rtcm(1077, 1000)
        for _ in range(20):            # let the kernel buffers fill
            for _ in range(200):
                feed.send(big)
            await asyncio.sleep(0.01)
        self.assertGreater(feed.frames_skipped, 0)
        transport = feed._writer.transport
        self.assertLessEqual(transport.get_write_buffer_size(), 1000 + len(big))
        self.assertEqual(feed.frames_sent + feed.frames_skipped, 4000)
        self.assertEqual(CLIENT_BUFFER, 32 * 1024)

    async def until_connected(self, server):
        for _ in range(200):
            if server.connected:
                return
            await asyncio.sleep(0.01)
        self.fail("no connection")

    async def test_solution_server_parses_lines_and_counts_the_rest(self):
        got = []
        server = SolutionServer(lambda solution, line: got.append((solution, line)))
        await server.start()
        self.addAsyncCleanup(server.stop)
        _, writer = await self.connect(server)
        writer.write(b"% header line\n" + LINE.encode() + b"\r\n" + b"junk\n" + b"x" * 600
                     + b"\n" + LINE[:30].encode())
        await writer.drain()
        writer.write(LINE[30:].encode() + b"\n" + LINE.replace("3.0000", "nan").encode()
                     + b"\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][1], LINE)
        self.assertEqual(server.solutions, 2)
        self.assertEqual(server.dropped, {"header": 1, "malformed": 2, "long": 1})

    async def test_an_endless_line_is_dropped_up_to_its_end(self):
        got = []
        server = SolutionServer(lambda solution, line: got.append(solution), max_line=512)
        await server.start()
        self.addAsyncCleanup(server.stop)
        _, writer = await self.connect(server)
        for _ in range(50):
            writer.write(b"y" * 1000)
        writer.write(b"tail of the long line\n" + LINE.encode() + b"\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        self.assertEqual(len(got), 1)
        self.assertEqual(server.dropped, {"long": 1})

    async def test_solution_server_newest_wins_and_never_sends(self):
        got = []
        server = SolutionServer(lambda solution, line: got.append(solution))
        await server.start()
        self.addAsyncCleanup(server.stop)
        old_reader, old = await self.connect(server)
        new_reader, new = await self.connect(server)
        self.assertEqual(await asyncio.wait_for(old_reader.read(), 2), b"")
        new.write(LINE.encode() + b"\n")
        await new.drain()
        await asyncio.sleep(0.05)
        self.assertEqual(len(got), 1)
        new.close()
        self.assertEqual(await asyncio.wait_for(new_reader.read(), 2), b"", "nothing sent")

    async def test_a_failing_callback_is_counted(self):
        def broken(_solution, _line):
            raise RuntimeError("broken")

        server = SolutionServer(broken)
        await server.start()
        self.addAsyncCleanup(server.stop)
        _, writer = await self.connect(server)
        with self.assertLogs("gnss_base", "ERROR"):
            writer.write((LINE + "\n").encode() * 3)
            await writer.drain()
            await asyncio.sleep(0.05)
        self.assertEqual((server.solutions, server.callback_errors), (3, 3))

    async def test_servers_listen_on_localhost_only(self):
        for server in (FeedServer(), SolutionServer(lambda solution, line: None)):
            await server.start()
            self.addAsyncCleanup(server.stop)
            self.assertEqual(server._server.sockets[0].getsockname()[0], "127.0.0.1")


class Runner(unittest.IsolatedAsyncioTestCase):
    """The supervisor against tests/fake_rtkrcv.py, with short timings."""

    TIMINGS = dict(restart_s=0.1, restart_max_s=0.4, reset_after_s=60, hang_s=0.6,
                   no_solution_s=0.5, stop_timeout_s=0.5, stats_s=0.2, poll_s=0.05)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.record = self.dir / "record.jsonl"
        self.env = mock.patch.dict(os.environ, {"FAKE_RTKRCV_RECORD": str(self.record),
                                                "FAKE_RTKRCV_PERIOD": "0.05"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.hub = RtcmHub()
        self.got = []

    def runner(self, mode="normal", binary=str(FAKE), **timings):
        os.environ["FAKE_RTKRCV_MODE"] = mode
        settings = MeasuredSettings(binary=binary, work_dir=self.dir / "rtkrcv")
        runner = RtkrcvRunner(settings, self.hub, lambda sol, line: self.got.append((sol, line)),
                              **dict(self.TIMINGS, **timings))
        self.delays = []
        real_sleep = runner._sleep

        async def sleep(seconds):
            self.delays.append(seconds)
            await real_sleep(seconds)

        runner._sleep = sleep
        self.addAsyncCleanup(runner.stop)
        return runner

    def records(self, event=None):
        if not self.record.exists():
            return []
        lines = [json.loads(line) for line in self.record.read_text().splitlines()]
        return [line for line in lines if event is None or line["event"] == event]

    async def until(self, check, timeout=10.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("timed out waiting")
            await asyncio.sleep(0.02)

    async def test_starts_on_the_first_msm_with_a_pty_console_and_stops_cleanly(self):
        runner = self.runner()
        runner.start()
        self.hub.publish(rtcm(1005))
        self.hub.publish(rtcm(1019, 67))
        await asyncio.sleep(0.3)
        self.assertEqual(runner.state(), "idle", "no MSM yet: not started")
        self.assertEqual(self.records(), [])
        self.hub.publish(rtcm(1074, 40))
        await self.until(lambda: self.got)
        await self.until(lambda: runner.state() == "running")
        (start,) = self.records("start")
        work = self.dir / "rtkrcv"
        self.assertEqual(start["argv"][:3], ["-s", "-o", str(work / CONFIG_NAME)])
        self.assertEqual(start["argv"][3], "-d")
        self.assertTrue(start["argv"][4].startswith("/dev/"), start["argv"])
        self.assertEqual(len(start["argv"]), 5, "no -p telnet or -m monitor port")
        self.assertTrue(start["tty"])
        self.assertFalse(start["stdin_tty"])
        self.assertEqual(Path(start["cwd"]).resolve(), work.resolve())
        self.assertEqual(start["config"], build_config(runner.feed.port, runner.solutions.port))
        self.assertEqual((start["cache"], self.got[0][0]["satellites"]), (False, 20))
        # Every frame from now on reaches rtkrcv, unfiltered.
        await self.until(lambda: runner.feed.connected)
        for _ in range(20):
            self.hub.publish(rtcm(1005))
        await self.until(lambda: runner.rss_bytes is not None and runner.cpu_percent is not None)
        snap = runner.snapshot()
        self.assertEqual((snap["state"], snap["rtkrcv_version"], snap["restarts"]),
                         ("running", "2.4.3 b34", 0))
        self.assertGreater(snap["rss_bytes"], 0)
        self.assertEqual(snap["output"], [], "output lines only when something is wrong")
        json.dumps(snap, allow_nan=False)
        await runner.stop()
        (end,) = self.records("exit")
        self.assertEqual(end["received"], 20 * 25)
        self.assertTrue((work / CACHE_NAME).exists(), "SIGTERM: the cache is saved")
        # A restart finds the cache.
        self.got.clear()
        again = self.runner()
        again.start()
        self.hub.publish(rtcm(1124, 40))
        await self.until(lambda: self.got)
        self.assertTrue(self.records("start")[-1]["cache"])

    async def test_exit_restarts_with_a_doubling_delay(self):
        runner = self.runner("exit")
        runner.start()
        self.hub.publish(rtcm(1077, 40))
        await self.until(lambda: len(self.delays) >= 5)
        self.assertEqual(self.delays[:5], [0.1, 0.2, 0.4, 0.4, 0.4])
        snap = runner.snapshot()
        self.assertEqual(snap["last_exit"]["code"], 3)
        self.assertGreaterEqual(snap["restarts_today"], 5)
        self.assertIn("exiting early", " ".join(runner.output.snapshot()))
        await self.until(lambda: runner.state() == "restarting")
        self.assertTrue(any("exiting early" in line for line in runner.snapshot()["output"]))

    async def test_a_long_run_resets_the_delay(self):
        os.environ["FAKE_RTKRCV_EXIT_AFTER"] = "0.3"
        self.addCleanup(os.environ.pop, "FAKE_RTKRCV_EXIT_AFTER", None)
        runner = self.runner(reset_after_s=0.2)
        runner.start()
        self.hub.publish(rtcm(1087, 40))
        await self.until(lambda: len(self.delays) >= 3)
        self.assertEqual(self.delays[:3], [0.1, 0.1, 0.1])
        self.assertEqual(runner.last_exit["code"], 4)

    async def test_a_hang_with_msm_flowing_is_restarted(self):
        runner = self.runner("hang")
        runner.start()

        async def receiver():
            while True:
                self.hub.publish(rtcm(1074, 40))
                await asyncio.sleep(0.05)

        task = asyncio.create_task(receiver())
        self.addCleanup(task.cancel)
        await self.until(lambda: runner.state() == "no solution")
        self.assertIn("rtkrcv> ", " ".join(runner.snapshot()["output"]) + " ")
        await asyncio.sleep(1.0)
        self.assertEqual(self.delays, [], "a cold start without orbits is no hang")
        for _ in range(6):              # one satellite's orbit, again and again: not enough
            self.hub.publish(eph(5))
        await asyncio.sleep(1.0)
        self.assertEqual(self.delays, [])
        for prn in (7, 9, 11):          # four distinct satellites: from now on it is a hang
            self.hub.publish(eph(prn))
        await self.until(lambda: self.delays)
        self.assertIn("no solution", runner.last_exit["reason"])
        self.assertTrue((self.dir / "rtkrcv" / CACHE_NAME).exists(), "stopped with SIGTERM")
        await self.until(lambda: len(self.records("start")) >= 2)

    async def test_stop_timeout_fits_the_shutdown_budget(self):
        from gnss_base import measured
        self.assertLessEqual(measured.STOP_TIMEOUT_S, 3.0)
        runner = self.runner("normal")
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        await self.until(lambda: self.got)
        await runner.stop()
        self.assertEqual(runner.stop_exit_code, 0)

    async def test_a_relative_binary_and_work_folder_are_made_absolute(self):
        old = os.getcwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, old)
        os.environ["FAKE_RTKRCV_MODE"] = "normal"
        work = Path(os.path.relpath(self.dir / "rel", ROOT))
        runner = RtkrcvRunner(MeasuredSettings(binary="tests/fake_rtkrcv.py", work_dir=work),
                              self.hub, lambda s_, l_: self.got.append(s_), **self.TIMINGS)
        self.addAsyncCleanup(runner.stop)
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        await self.until(lambda: self.got)
        (start,) = self.records("start")
        self.assertEqual(Path(start["cwd"]).resolve(), (self.dir / "rel").resolve())

    async def test_rtkrcv_gets_sigterm_when_the_app_dies(self):
        script = self.dir / "app.py"
        script.write_text(f"""
import asyncio, os, sys
sys.path.insert(0, {str(ROOT)!r})
from gnss_base.hub import RtcmHub
from gnss_base.measured import MeasuredSettings, RtkrcvRunner
from tests.test_measured import rtcm
async def main():
    hub = RtcmHub()
    runner = RtkrcvRunner(MeasuredSettings(binary={str(FAKE)!r},
                                           work_dir={str(self.dir / "orphan")!r}), hub)
    runner.start()
    hub.publish(rtcm(1074, 40))
    await asyncio.sleep(60)
asyncio.run(main())
""")
        os.environ["FAKE_RTKRCV_MODE"] = "normal"
        app = subprocess.Popen([sys.executable, str(script)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(app.kill)
        await self.until(lambda: self.records("start"))
        app.kill()          # SIGKILL: no chance to stop rtkrcv itself
        app.wait()
        await self.until(lambda: self.records("exit"), timeout=5)
        self.assertTrue((self.dir / "orphan" / CACHE_NAME).exists())

    async def test_no_hang_restart_while_the_receiver_is_quiet(self):
        runner = self.runner("hang")
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        with mock.patch("gnss_base.measured.MSM_FLOWING_S", 0.2):
            await asyncio.sleep(1.5)
        self.assertEqual(self.delays, [])
        self.assertEqual(runner.state(), "no solution")
        self.assertEqual(len(self.records("start")), 1)

    async def test_sigkill_if_sigterm_is_ignored(self):
        runner = self.runner("ignore-term")
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        await self.until(lambda: self.got)
        loop = asyncio.get_running_loop()
        began = loop.time()
        with self.assertLogs("gnss_base", "WARNING") as logs:
            await runner.stop()
        self.assertLess(loop.time() - began, 2.0)
        self.assertIn("killing", " ".join(logs.output))
        self.assertFalse((self.dir / "rtkrcv" / CACHE_NAME).exists())
        self.assertEqual(self.records("exit"), [])

    async def test_garbage_output_is_dropped_and_good_lines_kept(self):
        runner = self.runner("garbage")
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        await self.until(lambda: len(self.got) >= 3)
        dropped = runner.snapshot()["solutions"]["dropped"]
        self.assertGreaterEqual(dropped["malformed"], 2)
        self.assertGreaterEqual(dropped["long"], 2)
        self.assertEqual(dropped["header"], 1)

    async def test_missing_binary_is_not_available(self):
        with self.assertLogs("gnss_base", "ERROR"):
            runner = self.runner(binary=str(self.dir / "no-such-rtkrcv"))
            runner.start()
        self.hub.publish(rtcm(1074, 40))
        snap = runner.snapshot()
        self.assertEqual(snap["state"], "not available")
        self.assertIn("rtkrcv not found", snap["reason"])
        self.assertEqual(self.hub.frames[1074], 1, "the hub carries on")

    async def test_no_pseudo_terminal_is_not_available(self):
        runner = self.runner()
        with mock.patch("os.openpty", side_effect=OSError("out of ptys")), \
                self.assertLogs("gnss_base", "ERROR"):
            runner.start()
            self.hub.publish(rtcm(1074, 40))
            await self.until(lambda: runner.state() == "not available")
        self.assertIn("out of ptys", runner.snapshot()["reason"])
        self.assertEqual(self.records(), [])

    async def test_off(self):
        runner = RtkrcvRunner(MeasuredSettings(enabled=False), self.hub)
        runner.start()
        self.hub.publish(rtcm(1074, 40))
        self.assertEqual(runner.snapshot()["state"], "off")
        self.assertEqual(self.hub.snapshot()["subscriber_errors"], {})
        await runner.stop()


class Helpers(unittest.TestCase):
    def test_msm_numbers(self):
        self.assertEqual([n for n in range(1060, 1140) if is_msm(n)],
                         [n for n in range(1071, 1128) if 1 <= n % 10 <= 7])

    def test_proc_stats(self):
        cpu, rss = read_proc_stats(os.getpid())
        self.assertGreater(rss, 0)
        self.assertGreaterEqual(cpu, 0)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "42").mkdir()
        ticks = os.sysconf("SC_CLK_TCK")
        (folder / "42" / "stat").write_text(
            f"42 (rtk rcv) S 1 42 42 0 -1 0 0 0 0 0 {3 * ticks} {ticks} 0 0 20 0 3 0 1 2 3\n")
        (folder / "42" / "status").write_text("Name:\trtkrcv\nVmRSS:\t   13312 kB\n")
        self.assertEqual(read_proc_stats(42, str(folder)), (4.0, 13312 * 1024))
        self.assertIsNone(read_proc_stats(43, str(folder)))

    def test_output_lines_know_rtklib_ex(self):
        lines = OutputLines()
        lines.feed(b"\x1b[1m** rtkrcv ver.EX 2.5.1 console (h:help) **\x1b[0m\r\n"
                   b"stream 2 path=127.0.0.1:40003\r\n")
        self.assertEqual(lines.version, "EX 2.5.1")
        self.assertEqual(list(lines.lines)[-1], "stream 2 path=127.0.0.1:40003")

    def test_output_lines_strip_escapes_and_find_the_version(self):
        lines = OutputLines(size=3)
        lines.feed(b"\r\n\x1b[1m** rtkrcv ver.2.4.3 b34 console (h:help) **\x1b[0m\r\nrtk")
        lines.feed(b"rcv> ")
        self.assertEqual(lines.version, "2.4.3 b34")
        self.assertEqual(lines.snapshot(), ["** rtkrcv ver.2.4.3 b34 console (h:help) **",
                                            "rtkrcv>"])
        lines.feed(b"\n" + b"a\n" * 5)
        self.assertEqual(lines.snapshot(), ["a", "a", "a"])


def find_rtkrcv():
    return (os.environ.get("RTKRCV") or shutil.which("rtkrcv-ex") or shutil.which("rtkrcv"))


@unittest.skipUnless(find_rtkrcv(), "no rtkrcv binary (set RTKRCV or put rtkrcv-ex on PATH)")
class RealRtkrcv(unittest.IsolatedAsyncioTestCase):
    """Smoke test with the real rtkrcv and one empty MSM frame (no receiver data): it
    starts with our config on our pty, connects to both servers, and when stopped exits
    with code 0 on SIGTERM, its cache written."""

    async def test_smoke(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        hub = RtcmHub()
        runner = RtkrcvRunner(MeasuredSettings(binary=find_rtkrcv(), work_dir=folder),
                              hub, stats_s=0.5)
        self.addAsyncCleanup(runner.stop)
        runner.start()
        hub.publish(rtcm(1074, 40))   # an empty MSM: starts it, decodes to nothing
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15
        while not (runner.feed.connections and runner.solutions.connections
                   and runner.rss_bytes):
            self.assertLess(loop.time(), deadline, runner.snapshot())
            await asyncio.sleep(0.1)
        self.assertEqual(runner.state(), "starting")
        version = runner.output.version
        self.assertTrue(version and (version.startswith("2.4.3") or version.startswith("EX")),
                        version)
        await runner.stop()
        self.assertEqual(runner.last_exit, None)
        self.assertEqual(runner.stop_exit_code, 0, "exited on SIGTERM, not killed")
        self.assertTrue((folder / CACHE_NAME).exists())


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


DAY_ONE = 1_798_761_600.0     # 2027-01-01 00:00:00 UTC


class SolutionLogFile(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.wall = Clock(DAY_ONE + 86400 - 2)
        self.room = True
        self.log = SolutionLog(self.dir / "measured", lambda: "% header", wall=self.wall,
                               room=lambda: self.room)

    async def test_daily_files_with_a_header_through_the_writer_task(self):
        self.log.start()
        self.log.write(LINE)
        self.log.write(LINE + "\r\n")
        await self.log.flush()
        self.wall.now += 5          # past midnight UTC
        self.log.write(LINE)
        await self.log.stop()
        first = (self.dir / "measured" / "2027-01-01.pos").read_text()
        self.assertEqual(first, f"% header\n{LINE}\n{LINE}\n")
        second = (self.dir / "measured" / "2027-01-02.pos").read_text()
        self.assertEqual(second, f"% header\n{LINE}\n")
        self.assertEqual(self.log.snapshot()["lines_written"], 3)
        # A restart appends, without a second header.
        again = SolutionLog(self.dir / "measured", lambda: "% header", wall=self.wall)
        again.write(LINE)
        self.assertEqual((self.dir / "measured" / "2027-01-02.pos").read_text().count("%"), 1)

    async def test_nothing_written_below_the_disk_floor(self):
        self.room = False
        self.log.write(LINE)
        self.assertFalse((self.dir / "measured").exists())
        self.assertEqual(self.log.skipped_low_disk, 1)

    async def test_a_full_queue_drops_and_counts(self):
        log = SolutionLog(self.dir / "m", wall=self.wall, queue_size=3)
        log.start()
        for _ in range(10):
            log.write(LINE)
        await log.stop()
        self.assertEqual(log.written + log.dropped, 10)
        self.assertGreaterEqual(log.dropped, 7)

    async def test_a_write_error_is_shown_and_logging_goes_on(self):
        (self.dir / "measured").write_text("a file where the folder should be")
        with self.assertLogs("gnss_base", "ERROR"):
            self.log.write(LINE)
        self.assertIn("solution log", self.log.snapshot()["error"])
        self.assertEqual(self.log.dropped, 1)

    async def test_finished_days_are_compressed(self):
        self.wall.now = DAY_ONE + 3600
        self.log.write(LINE)
        self.wall.now += 86400
        self.log.write(LINE)
        folder = self.dir / "measured"
        os.utime(folder / "2027-01-01.pos", (self.wall.now, self.wall.now))
        self.assertEqual(self.log.pending_days(), [], "yesterday was written just now")
        self.wall.now += SolutionLog.SETTLE_S
        os.utime(folder / "2027-01-01.pos", (DAY_ONE, DAY_ONE))
        self.assertEqual([p.name for p in self.log.pending_days()], ["2027-01-01.pos"])
        await self.log.housekeep()
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["2027-01-01.pos.gz", "2027-01-02.pos"])
        with gzip.open(folder / "2027-01-01.pos.gz", "rt") as stream:
            self.assertEqual(stream.read(), f"% header\n{LINE}\n")

    async def test_a_late_line_during_compression_is_kept(self):
        from gnss_base import measured
        self.wall.now = DAY_ONE + 3600
        self.log.write(LINE)
        folder = self.dir / "measured"
        self.wall.now += 86400 + SolutionLog.SETTLE_S
        os.utime(folder / "2027-01-01.pos", (DAY_ONE, DAY_ONE))
        self.log.start()
        real = measured.compress
        entered = threading.Event()

        def slow(path):
            entered.set()
            time.sleep(0.3)
            return real(path)

        with mock.patch("gnss_base.measured.compress", slow):
            compressing = asyncio.ensure_future(self.log.housekeep())
            while not entered.is_set():
                await asyncio.sleep(0.01)
            self.wall.now = DAY_ONE + 7200      # a line for the day being compressed
            self.log.write("late " + LINE)
            await compressing
        await self.log.stop()
        with gzip.open(folder / "2027-01-01.pos.gz", "rt") as stream:
            self.assertEqual(stream.read(), f"% header\n{LINE}\n")
        self.assertEqual((folder / "2027-01-01.pos").read_text(), f"% header\nlate {LINE}\n")
        self.assertEqual(self.log.dropped, 0)

    async def test_a_new_header_block_when_settings_change(self):
        current = ["% a"]
        log = SolutionLog(self.dir / "h", lambda: current[0], wall=self.wall)
        log.write(LINE)
        log.write(LINE)
        again = SolutionLog(self.dir / "h", lambda: current[0], wall=self.wall)
        again.write(LINE)             # a restart with the same settings: no new header
        current[0] = "% b"
        again.write(LINE)             # changed: a new block
        restart = SolutionLog(self.dir / "h", lambda: current[0], wall=self.wall)
        restart.write(LINE)
        text = (self.dir / "h" / "2027-01-01.pos").read_text().splitlines()
        self.assertEqual([line for line in text if line.startswith("%")], ["% a", "% b"])
        self.assertEqual(text, ["% a", LINE, LINE, LINE, "% b", LINE, LINE])

    async def test_leftover_partial_files_are_removed(self):
        folder = self.dir / "p"
        folder.mkdir()
        (folder / "2027-01-01.pos.gz.partial").write_text("half")
        (folder / "2027-01-01.pos").write_text("% kept\n")
        SolutionLog(folder)
        self.assertEqual([p.name for p in folder.iterdir()], ["2027-01-01.pos"])

    async def test_each_line_keeps_the_header_of_its_time(self):
        current = ["% a"]
        log = SolutionLog(self.dir / "q", lambda: current[0], wall=self.wall)
        log.start()
        log.write("one")                # queued under % a ...
        current[0] = "% b"              # ... and rtkrcv restarted before the writer ran
        log.write("two")
        await log.stop()
        text = (self.dir / "q" / "2027-01-01.pos").read_text().splitlines()
        self.assertEqual(text, ["% a", "one", "% b", "two"])

    async def test_a_write_abandoned_at_stop_is_counted(self):
        log = SolutionLog(self.dir / "s", wall=self.wall)
        release = threading.Event()
        real = log._flush

        def slow(batch):
            release.wait(5)
            return real(batch)

        log._flush = slow
        log.start()
        for _ in range(3):
            log.write(LINE)
        await asyncio.sleep(0.05)       # the writer hands the batch to its thread
        for _ in range(2):
            log.write(LINE)             # still queued
        await log.stop(timeout=0.2)
        release.set()
        snap = log.snapshot()
        self.assertEqual((snap["abandoned"], snap["dropped"], snap["lines_written"]),
                         (3, 2, 0))

    async def test_a_cut_last_line_gets_its_newline(self):
        folder = self.dir / "cut"
        folder.mkdir()
        (folder / "2027-01-01.pos").write_text("% header\n2027/01/01 00:00:01.000 0.1")
        log = SolutionLog(folder, lambda: "% header", wall=self.wall)
        log.write(LINE)
        log.write(LINE)
        self.assertEqual((folder / "2027-01-01.pos").read_text().splitlines(),
                         ["% header", "2027/01/01 00:00:01.000 0.1", LINE, LINE])
        cut = ConnectionLog(folder / "caster.jsonl")
        (folder / "caster.jsonl").write_text('{"event": "conn')
        cut.write("connect", address="192.0.2.1")
        lines = (folder / "caster.jsonl").read_text().splitlines()
        self.assertEqual(lines[0], '{"event": "conn')
        self.assertEqual(json.loads(lines[1])["event"], "connect")


class Settings(unittest.TestCase):
    def test_defaults_overrides_and_work_folder(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        options = folder / "options.json"
        options.write_text(json.dumps({"measured_snr_mask": 38}))
        settings = load_settings(["--options", str(options)])
        measured = measured_settings(settings)
        self.assertEqual((measured.enabled, measured.binary, measured.elevation_mask,
                          measured.snr_mask, measured.work_dir),
                         (True, "rtkrcv-ex", 15, 38, folder / "rtkrcv"))
        settings = load_settings(["--options", str(options), "--no-measured",
                                  "--measured-elevation-mask", "10", "--measured-snr-mask",
                                  "0", "--rtkrcv", "/opt/rtklib/rtkrcv", "--data-dir", "/x"])
        measured = measured_settings(settings)
        self.assertEqual((measured.enabled, measured.binary, measured.elevation_mask,
                          measured.snr_mask, measured.work_dir),
                         (False, "/opt/rtklib/rtkrcv", 10, 0, Path("/x/rtkrcv")))
        relative = load_settings(["--options", str(options), "--data-dir", "rel/data"])
        self.assertEqual(measured_settings(relative).work_dir,
                         Path(os.getcwd()) / "rel" / "data" / "rtkrcv")
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        self.assertIn("signal.SIGHUP", source)
        for key in ("measured_enabled", "measured_elevation_mask", "measured_snr_mask"):
            self.assertIn(key, DEFAULTS)
            self.assertIn(key, OVERRIDES)
        self.assertEqual((DEFAULTS["measured_elevation_mask"], DEFAULTS["measured_snr_mask"]),
                         (15, 35))

    def test_invalid_masks_are_not_available_not_a_crash(self):
        async def go():
            runner = RtkrcvRunner(MeasuredSettings(binary=str(FAKE), elevation_mask=None),
                                  RtcmHub())
            with self.assertLogs("gnss_base", "ERROR"):
                runner.start()
            await runner.stop()
            return runner.snapshot()

        snap = asyncio.run(go())
        self.assertEqual(snap["state"], "not available")
        self.assertIn("invalid options", snap["reason"])

    def test_page_and_packaging(self):
        text = (ROOT / "config.yaml").read_text()
        for line in ("  measured_enabled: true\n", "  measured_elevation_mask: 15\n",
                     "  measured_snr_mask: 35\n", "  measured_enabled: bool\n",
                     "  measured_elevation_mask: int(0,45)\n",
                     "  measured_snr_mask: int(0,50)\n"):
            self.assertIn(line, text)
        self.assertIn("/usr/local/bin/rtkrcv-ex", (ROOT / "Dockerfile").read_text())
        source = (ROOT / "gnss_base" / "__main__.py").read_text()
        self.assertIn("x.stop() for x in (rtkrcv, *references)", source)
        self.assertIn("x.stop(timeout=left(0.2)) for x in logs", source)
        self.assertIn("SHUTDOWN_BUDGET_S = 9.0", source)


class EndToEnd(unittest.TestCase):
    """The whole app with a simulated receiver and the fake rtkrcv: MSM starts it, its
    solutions reach the status, the measured track and the solution log, and the app
    stops it with SIGTERM within Home Assistant's 10 s."""

    def run_app(self, mode="normal", record_sent=False):
        """Run the app until rtkrcv's track has 3 points, then SIGTERM it. Returns
        (folder, status, track, seconds the shutdown took, exit code)."""
        from tests.test_app import SimulatedReceiver, arp_frame, free_port
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        master, slave = pty.openpty()
        tty.setraw(master)
        tty.setraw(slave)
        by_id = directory / "by-id"
        by_id.mkdir()
        (by_id / "usb-test_USB_Dual_Serial_0-if00").symlink_to(os.ttyname(slave))
        port = free_port()
        options = directory / "options.json"
        options.write_text(json.dumps({"device": os.ttyname(slave), "baud": 460800,
                                       "log_dir": str(directory / "logs"), "min_free_gb": 0,
                                       "caster_enabled": False}))
        env = dict(os.environ, FAKE_RTKRCV_PERIOD="0.2", FAKE_RTKRCV_MODE=mode,
                   FAKE_RTKRCV_RECORD_SENT="1" if record_sent else "0",
                   FAKE_RTKRCV_RECORD=str(directory / "record.jsonl"))
        process = subprocess.Popen(
            [sys.executable, "-m", "gnss_base", "--options", str(options), "--host",
             "127.0.0.1", "--port", str(port), "--allow-any-client", "--by-id-dir",
             str(by_id), "--rtkrcv", str(FAKE)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        receiver = SimulatedReceiver(master)
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as reply:
                return json.load(reply)

        stream = arp_frame(*rtcmdec.llh_to_ecef(0.0, 0.0, 0.0)) + rtcm(1074, 40)
        status = track = None
        try:
            receiver.start()
            deadline = time.time() + 30
            while time.time() < deadline:
                receiver.send(stream)
                time.sleep(0.2)
                try:
                    status = get("/api/status")
                    track = get("/api/track")
                except OSError:
                    continue
                if len(track["points"]) >= 3:
                    break
        finally:
            stopping = time.time()
            process.terminate()
            process.wait(timeout=15)
            took = time.time() - stopping
            receiver.stop()
        return directory, status, track, took, process.returncode

    def test_app_runs_rtkrcv_and_shows_its_solution(self):
        directory, status, track, took, code = self.run_app()
        self.assertLess(took, 10)
        self.assertEqual(code, 0)
        measured = status["measured"]
        self.assertEqual(measured["state"], "running", measured)
        self.assertEqual(measured["solution"]["satellites"], 20)
        self.assertEqual(measured["reference_kind"], "base")
        self.assertEqual((track["source"], track["reference_kind"]), ("measured", "base"))
        self.assertEqual(track["reference"], {"lat": 0.0, "lon": 0.0, "height_m": 0.0})
        self.assertEqual(track["points"][-1][3], 3.0, "3 m above the made-up 1005")
        records = [json.loads(line) for line in
                   (directory / "record.jsonl").read_text().splitlines()]
        self.assertEqual([r["event"] for r in records], ["start", "exit"])
        self.assertEqual(Path(records[0]["cwd"]).resolve(), (directory / "rtkrcv").resolve())
        self.assertTrue((directory / "rtkrcv" / CACHE_NAME).exists())
        (pos,) = (directory / "logs" / "measured").glob("*.pos")
        lines = pos.read_text().splitlines()
        self.assertTrue(lines[0].startswith("% gnss_base "), lines[0])
        self.assertIn("rtkrcv 2.4.3 b34", lines[0])
        self.assertGreaterEqual(len(lines), 4)
        self.assertEqual(parse_solution(lines[1])[1], None)

    def test_shutdown_fits_10_s_with_an_rtkrcv_that_ignores_sigterm(self):
        directory, status, track, took, code = self.run_app("ignore-term", record_sent=True)
        self.assertEqual(status["measured"]["state"], "running")
        self.assertLess(took, 9.5)
        self.assertGreater(took, 2.5, "SIGKILL only after the SIGTERM wait")
        self.assertEqual(code, 0)
        # rtkrcv kept sending for 3 s after the app was told to stop: every line it sent
        # (and saw accepted by the socket) is in the log, the last ones included.
        sent = [json.loads(line)["time"] for line in
                (directory / "record.jsonl").read_text().splitlines()
                if json.loads(line)["event"] == "sent"]
        (pos,) = (directory / "logs" / "measured").glob("*.pos")
        logged = {line.split()[1][:8] for line in pos.read_text().splitlines()
                  if not line.startswith("%")}
        self.assertGreater(len(sent), 15, "lines sent during the SIGTERM wait too")
        self.assertEqual([t for t in sent if t not in logged], [])


class Shutdown(unittest.TestCase):
    """The whole app stops within its 9 s budget even while a finished day is being
    compressed: the compression is abandoned (and redone at the next start)."""

    def test_a_slow_compression_does_not_hold_the_exit(self):
        import signal
        from gnss_base import __main__ as app
        from tests.test_app import free_port
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "logs" / "raw").mkdir(parents=True)
        (folder / "logs" / "raw" / "2020-01-01.raw").write_bytes(b"old day")
        options = folder / "options.json"
        options.write_text(json.dumps({"device": "", "log_dir": str(folder / "logs"),
                                       "min_free_gb": 0}))
        settings = load_settings(["--options", str(options), "--host", "127.0.0.1",
                                  "--port", str(free_port()), "--caster-host", "127.0.0.1",
                                  "--caster-port", str(free_port()), "--no-measured"])
        entered, release = threading.Event(), threading.Event()

        def slow(path):
            entered.set()
            release.wait(5)      # a 5 s gzip
            return None

        async def main():
            async def stopper():
                while not entered.is_set():
                    await asyncio.sleep(0.02)
                os.kill(os.getpid(), signal.SIGTERM)

            killer = asyncio.ensure_future(stopper())
            await app.run(settings)
            await killer

        with mock.patch("gnss_base.__main__.compress", slow):
            began = time.monotonic()
            asyncio.run(main())
            took = time.monotonic() - began
        still_compressing = not release.is_set()
        release.set()
        self.assertTrue(still_compressing)
        self.assertLess(took, 9.5)
        self.assertTrue((folder / "logs" / "raw" / "2020-01-01.raw").exists(),
                        "the day is kept, to be compressed at the next start")


class UploadStateAtExit(unittest.TestCase):
    """A state write stuck on a stalled disk neither holds the uploader's stop nor the
    process exit: it runs in a daemon thread of the app's executor."""

    def test_a_stuck_state_write_does_not_hold_the_exit(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        script = folder / "app.py"
        script.write_text(f"""
import asyncio, sys, time
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
from gnss_base import upload
from gnss_base.__main__ import DaemonExecutor
from gnss_base.hub import ConnectionLog, RtcmHub, Secret
upload._write_state = lambda path, text: time.sleep(30)   # a stalled /data
async def main():
    asyncio.get_running_loop().set_default_executor(DaemonExecutor())
    uploader = upload.Uploader(upload.UploadSettings(host="127.0.0.1", port=9, mountpoint="M",
                               password=Secret("pw")), RtcmHub(), ConnectionLog(None),
                               state_path=Path({str(folder / "state.json")!r}))
    uploader._save()
    uploader._save()
    began = time.monotonic()
    await uploader.stop()
    print(round(time.monotonic() - began, 1), flush=True)
asyncio.run(main())
""")
        began = time.monotonic()
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                timeout=60)
        took = time.monotonic() - began
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(float(result.stdout), 2.5, "stop waits 2 s at most")
        self.assertLess(took, 6, "the process exits without waiting for the write")
        source = (ROOT / "gnss_base" / "upload.py").read_text()
        self.assertNotIn("ThreadPoolExecutor", source)


class Page(unittest.TestCase):
    def test_measured_card_labels_mean_line_and_caveat(self):
        html = (ROOT / "gnss_base" / "static" / "index.html").read_text()
        for text in ('id="meas"', 'id="measd"', 'id="measout"', 'id="drifth"', 'id="meankey"',
                     "renderMeasured(s.measured)", "trackLabel(track)", "runningMean(pts)",
                     "5-min running mean", 'class="key meas"',
                     "Single point positioning: expect 1-2 m scatter, and Up several metres "
                     "high (RTKLIB's default ionosphere model). A steady offset is normal; "
                     "look for jumps and trends.",
                     "waiting for ephemerides"):
            self.assertIn(text, html)
        card = html[html.index("function renderMeasured"):html.index("function render(s)")]
        self.assertNotIn("innerHTML", card, "values reach the card through dl, which escapes")
        self.assertIn("$(\"measout\").textContent=out", card)


class NoPathToTheReceiver(unittest.TestCase):
    def test_measured_imports_nothing_that_reaches_the_receiver(self):
        tree = ast.parse((ROOT / "gnss_base" / "measured.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "") + ":" + ",".join(a.name for a in node.names))
        text = " ".join(imported)
        for forbidden in ("commands", "control", "receiver", "serial", "web"):
            self.assertNotRegex(text, rf"\b{forbidden}\b", f"measured imports {forbidden}")


if __name__ == "__main__":
    unittest.main()
