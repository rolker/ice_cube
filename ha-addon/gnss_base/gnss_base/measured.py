"""Live measured position: RTKLIB's rtkrcv, single point positioning on a copy of the
receiver's RTCM.

The app runs two servers on 127.0.0.1, each on a port the system picks: the feed server
sends rtkrcv every RTCM frame from the hub, unfiltered; the solution server takes
rtkrcv's solution lines (llh format) back. rtkrcv connects to both as a TCP client, so
it opens no listening socket of its own. Nothing from rtkrcv can reach the receiver:
this module never imports the command, control or serial modules (a unit test checks).

The runner, the servers and the solution log are shared with the reference check
(reference.py), which runs a second rtkrcv with its own config.
"""
import asyncio
import contextlib
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hub import _ends_with_newline
from .rawlog import compress, utc_day

LOG = logging.getLogger("gnss_base")

HOST = "127.0.0.1"
CLIENT_BUFFER = 32 * 1024     # feed bytes waiting for rtkrcv; more and frames are skipped
MAX_LINE = 512                # a longer solution line is dropped
SOLUTION_FIELDS = 15
MAX_HEIGHT_M = 100_000
NAVSYS = 45                   # GPS 1 + GLONASS 4 + Galileo 8 + BeiDou 32
SNR_MASK_VALUES = 9           # rtkrcv takes one mask per 10 degrees of elevation

CONFIG_TEMPLATE = """\
# Written by gnss_base each time it starts rtkrcv; changes here are overwritten.
inpstr1-type   =tcpcli
inpstr1-path   ={host}:{feed_port}
inpstr1-format =rtcm3
inpstr2-type   =off
inpstr3-type   =off
outstr1-type   =tcpcli
outstr1-path   ={host}:{solution_port}
outstr1-format =llh
outstr2-type   =off
logstr1-type   =off
logstr2-type   =off
logstr3-type   =off
misc-timeout   =10000
misc-reconnect =5000
misc-navmsgsel =all
misc-svrcycle  =50
pos1-posmode   =single
pos1-frequency =l1
pos1-soltype   =forward
pos1-elmask    ={elevation_mask}
pos1-snrmask_r ={snr_on}
pos1-snrmask_L1={snr_values}
pos1-posopt5   =on
pos1-navsys    ={navsys}
pos1-sateph    =brdc
pos1-ionoopt   =brdc
pos1-tropopt   =saas
pos1-dynamics  =off
pos1-tidecorr  =off
out-solformat  =llh
out-outhead    =off
out-timesys    =gpst
out-timeform   =hms
out-degform    =deg
out-height     =ellipsoidal
out-solstatic  =all
"""


def build_config(feed_port, solution_port, elevation_mask=15, snr_mask=35):
    """rtkrcv's configuration: single point, L1, broadcast orbits and ionosphere, RAIM
    FDE, the two local ports. No position and no password: single mode needs neither.
    An SNR mask of 0 turns the mask off."""
    for name, value, low, high in (("feed_port", feed_port, 1, 65535),
                                   ("solution_port", solution_port, 1, 65535),
                                   ("elevation_mask", elevation_mask, 0, 90),
                                   ("snr_mask", snr_mask, 0, 60)):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return CONFIG_TEMPLATE.format(
        host=HOST, feed_port=feed_port, solution_port=solution_port,
        elevation_mask=elevation_mask, snr_on="on" if snr_mask else "off",
        snr_values=",".join([str(snr_mask)] * SNR_MASK_VALUES), navsys=NAVSYS)


def _finite(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("not finite")
    return value


def parse_solution(line):
    """One llh line from rtkrcv as (solution, None), or (None, why) if it is not one:
    "header" (a `%` line, sent on every connect), "empty", "malformed" or "implausible".

    The line is `YYYY/MM/DD HH:MM:SS.sss lat lon h Q ns sdn sde sdu sdne sdeu sdun age
    ratio`, time in GPST. The time is rounded to the second, since the first epoch after
    a start can be a few milliseconds off (receiver clock not yet steered)."""
    if isinstance(line, (bytes, bytearray)):
        line = bytes(line).decode("ascii", "replace")
    text = line.strip()
    if not text:
        return None, "empty"
    if text.startswith("%"):
        return None, "header"
    fields = text.split()
    if len(fields) != SOLUTION_FIELDS:
        return None, "malformed"
    try:
        when = datetime.strptime(f"{fields[0]} {fields[1]}", "%Y/%m/%d %H:%M:%S.%f")
        lat, lon, height = (_finite(v) for v in fields[2:5])
        quality, satellites = int(fields[5]), int(fields[6])
        sdn, sde, sdu = (_finite(v) for v in fields[7:10])
        for value in fields[10:13]:
            _finite(value)
        age, ratio = _finite(fields[13]), _finite(fields[14])
    except ValueError:
        return None, "malformed"
    if not (abs(lat) <= 90 and abs(lon) <= 180 and abs(height) <= MAX_HEIGHT_M
            and 1 <= quality <= 6 and 0 <= satellites <= 200
            and all(0 <= s <= MAX_HEIGHT_M for s in (sdn, sde, sdu))):
        return None, "implausible"
    when = (when + timedelta(microseconds=500_000)).replace(microsecond=0)
    return {"gpst": when.isoformat(), "lat": lat, "lon": lon, "height_m": height,
            "quality": quality, "satellites": satellites, "sigma_n_m": sdn,
            "sigma_e_m": sde, "sigma_u_m": sdu, "age_s": age, "ratio": ratio}, None


class _OneClientServer:
    """A TCP server on 127.0.0.1 that keeps one client: a new connection replaces the old
    one (rtkrcv reconnects on its own; the newest connection is the live one)."""

    name = "server"

    def __init__(self):
        self._server = None
        self.port = None
        self._writer = None
        self._tasks = set()
        self.connections = 0

    @property
    def connected(self):
        return self._writer is not None

    async def start(self):
        if self._server is None:
            self._server = await asyncio.start_server(self._accept, HOST, 0)
            self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self, drain_s=0.5):
        """Stop listening and end the connection. Its reader gets up to `drain_s` first to
        take what is already received (the peer has usually exited by now, so it soon
        reads the end), so the last solution lines are not lost."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=drain_s)
        self._drop(self._writer)
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), 1)

    async def wait_disconnected(self, timeout):
        """Wait up to `timeout` s for the current connection, if any, to end."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self.connected and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)

    def _drop(self, writer):
        if writer is None:
            return
        if writer is self._writer:
            self._writer = None
        with contextlib.suppress(Exception):
            writer.close()

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        old, self._writer = self._writer, writer
        self.connections += 1
        self._drop(old)
        self.on_connect()
        try:
            await self._serve(reader)
        except (ConnectionError, OSError):
            pass
        finally:
            self._drop(writer)
            self._tasks.discard(task)

    def on_connect(self):
        pass

    async def _serve(self, reader):
        raise NotImplementedError


class FeedServer(_OneClientServer):
    """Sends each RTCM frame to rtkrcv as it comes (`send`, called on the event loop from
    the hub subscriber). With more than CLIENT_BUFFER bytes waiting for rtkrcv, frames are
    skipped and counted. Anything rtkrcv sends is read and dropped."""

    name = "feed"

    def __init__(self, max_buffer=CLIENT_BUFFER):
        super().__init__()
        self.max_buffer = max_buffer
        self.frames_sent = 0
        self.bytes_sent = 0
        self.frames_skipped = 0
        self.bytes_received = 0

    def send(self, frame):
        writer = self._writer
        if writer is None or self._server is None:
            return
        transport = writer.transport
        if transport is None or transport.is_closing():
            return
        if transport.get_write_buffer_size() > self.max_buffer:
            self.frames_skipped += 1
            return
        writer.write(frame)
        self.frames_sent += 1
        self.bytes_sent += len(frame)

    async def _serve(self, reader):
        while data := await reader.read(4096):
            self.bytes_received += len(data)   # dropped: nothing goes anywhere

    def snapshot(self):
        return {"connected": self.connected, "frames_sent": self.frames_sent,
                "frames_skipped": self.frames_skipped, "connections": self.connections}


class SolutionServer(_OneClientServer):
    """Reads rtkrcv's solution lines. Each good line goes to `on_solution(solution,
    line)`; `%` header lines, malformed, implausible and over-long lines are dropped and
    counted. Nothing is ever sent back."""

    name = "solution"

    def __init__(self, on_solution, max_line=MAX_LINE, label="measured position"):
        super().__init__()
        self.on_solution = on_solution
        self.label = label
        self.max_line = max_line
        self.solutions = 0
        self.dropped = {}       # why -> count
        self.callback_errors = 0

    def _count(self, why):
        self.dropped[why] = self.dropped.get(why, 0) + 1

    async def _serve(self, reader):
        pending = b""
        skipping = False        # inside an over-long line: drop up to its end
        while data := await reader.read(4096):
            pending += data
            while True:
                end = pending.find(b"\n")
                if end < 0:
                    if len(pending) > self.max_line:
                        if not skipping:
                            self._count("long")
                        skipping, pending = True, b""
                    break
                line, pending = pending[:end], pending[end + 1:]
                if skipping:
                    skipping = False
                    continue
                if len(line) > self.max_line:
                    self._count("long")
                    continue
                self._line(line)

    def _line(self, line):
        solution, why = parse_solution(line)
        if solution is None:
            if why != "empty":
                self._count(why)
            return
        self.solutions += 1
        try:
            self.on_solution(solution, line.decode("ascii", "replace").rstrip("\r"))
        except Exception:
            self.callback_errors += 1
            if self.callback_errors == 1:
                LOG.exception("%s: handling a solution failed", self.label)

    def snapshot(self):
        return {"connected": self.connected, "solutions": self.solutions,
                "dropped": dict(self.dropped), "connections": self.connections}


# ---------------------------------------------------------------- supervisor

RESTART_S = 5.0               # first restart delay, doubling up to RESTART_MAX_S
RESTART_MAX_S = 300.0
RESET_AFTER_S = 600.0         # a run this long resets the delay
HANG_S = 300.0                # MSM arriving and no solution for this long: restart,
# ... once rtkrcv has had ephemerides (1019) of this many GPS satellites:
HANG_EPHEMERIDES = 4
NO_SOLUTION_S = 30.0          # no solution line for this long: state "no solution"
MSM_FLOWING_S = 10.0
STOP_TIMEOUT_S = 3.0          # SIGTERM, then SIGKILL after this long (shutdown budget)
STATS_S = 60.0                # rtkrcv's CPU and memory are read this often
POLL_S = 1.0
OUTPUT_LINES = 20
EPHEMERIS_TYPES = {"GPS": (1019,), "GLONASS": (1020,), "Galileo": (1045, 1046),
                   "BeiDou": (1042,)}
CONFIG_NAME = "rtkrcv.conf"
CACHE_NAME = "rtkrcv.nav"     # rtkrcv saves its ephemerides here on a clean exit
# "rtkrcv ver.2.4.3 b34 console" (RTKLIB 2.4.3) or "rtkrcv ver.EX 2.5.1 console" (RTKLIB-EX).
_VERSION = re.compile(r"rtkrcv ver\.\s*((?:EX\s+)?[0-9][0-9A-Za-z.]*(?: b\d+)?)")
_PROBE = re.compile(r"rtkrcv RTKLIB\s+(\S[^\r\n]*)")   # `rtkrcv --version` (RTKLIB-EX)
PROBE_TIMEOUT_S = 10.0
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PR_SET_PDEATHSIG = 1


def _parent_death_signal():
    """A preexec function that asks Linux to send the child SIGTERM when the app dies,
    so rtkrcv never outlives it (and still saves its cache); None where unavailable."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    except (OSError, AttributeError):
        return None

    # Python warns that preexec_fn is unsafe with threads: the child runs it between fork
    # and exec, and a lock another thread held at the fork stays locked in the child.
    # This one is acceptable: prctl is looked up above, before any fork; the call passes
    # only ints, so it allocates nothing, imports nothing and takes no lock; and the fork
    # happens on the event loop (main) thread. That thread is also the one whose death
    # Linux reports, and it lives as long as the app.
    def preexec():
        prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)

    return preexec


async def probe_version(binary):
    """`rtkrcv --version` for a build that has it (RTKLIB-EX: "EX 2.5.1"), or None for
    one that prints its usage instead (RTKLIB 2.4.3 b34). A build with --version also
    takes -nc (start with no console), which is how it is run: with a console on a
    pseudo-terminal (-d), RTKLIB-EX does not exit on SIGTERM (its console thread is
    joined while it waits on the terminal), so it would be killed at every stop and lose
    its ephemeris cache. 2.4.3 b34 has neither option and keeps the pseudo-terminal."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary, "--version", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), PROBE_TIMEOUT_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            return None
    except OSError:
        return None
    match = _PROBE.search(output.decode("utf-8", "replace"))
    return match.group(1).strip() if match else None



def gps_ephemeris_prn(frame):
    """The satellite number of a whole RTCM 1019 frame (the 6 bits after the type)."""
    return ((frame[4] & 0x0F) << 2) | (frame[5] >> 6)


def is_msm(number):
    return 1071 <= number <= 1127 and 1 <= number % 10 <= 7


DEFAULT_BINARY = "rtkrcv-ex"   # RTKLIB-EX, built into the image (see the Dockerfile)


@dataclass
class MeasuredSettings:
    enabled: bool = True
    binary: str = DEFAULT_BINARY
    work_dir: Path = Path("/data/rtkrcv")
    elevation_mask: int = 15
    snr_mask: int = 35


class OutputLines:
    """The last lines rtkrcv wrote to its console or its stdout/stderr, for the page.
    Neither instance's config holds a credential (the reference check's NTRIP login
    stays in the app), so the lines are kept as they come."""

    LONG_LINE = 2000

    def __init__(self, size=OUTPUT_LINES):
        self.lines = deque(maxlen=size)
        self._partial = ""
        self.version = None

    @staticmethod
    def _clean(line):
        return "".join(c for c in line if c.isprintable()).strip()

    def feed(self, data):
        text = _ANSI.sub("", self._partial + data.decode("utf-8", "replace"))
        *lines, self._partial = text.replace("\r", "\n").split("\n")
        if len(self._partial) > self.LONG_LINE:
            lines.append(self._partial)
            self._partial = ""
        for line in lines:
            line = self._clean(line)
            if line:
                self.lines.append(line[:200])
                match = _VERSION.search(line)
                if match and self.version is None:
                    self.version = match.group(1)

    def snapshot(self):
        tail = self._partial.strip()
        return list(self.lines) + ([tail] if tail else [])


def read_proc_stats(pid, proc="/proc"):
    """(cpu seconds, rss bytes) of a process from /proc, or None if unreadable."""
    try:
        with open(f"{proc}/{pid}/stat") as stream:
            fields = stream.read().rsplit(")", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])   # utime, stime
        rss = None
        with open(f"{proc}/{pid}/status") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
        return ticks / os.sysconf("SC_CLK_TCK"), rss
    except (OSError, ValueError, IndexError):
        return None


class RtkrcvRunner:
    """Starts rtkrcv when the first MSM frame arrives and keeps it running.

    - No console (`-nc`) with RTKLIB-EX, found by `--version` (probe_version); with
      2.4.3 b34, a console on a pseudo-terminal the app owns (`-d /dev/pts/N`), which
      the app drains and never writes to. No telnet (`-p`) or monitor (`-m`) port.
    - Working folder `work_dir`, kept across restarts: rtkrcv loads its ephemeris cache
      (rtkrcv.nav) there at start and saves it on a clean exit, so it is stopped with
      SIGTERM, and SIGKILL only if it does not exit within STOP_TIMEOUT_S.
    - Exit: restart after RESTART_S, doubling to RESTART_MAX_S, reset after a run of
      RESET_AFTER_S. Hang (MSM arriving, no solution for HANG_S): stopped and restarted
      the same way. A missing binary or no pseudo-terminal: "not available", the rest of
      the app runs.

    `on_solution(solution, line)` gets each good solution line.

    The reference check reuses the runner with: `make_config(ports)`, which returns the
    config text for a dict of the local ports (`feed`, `solution`, and one per extra
    server in `servers`, by name); `servers`, extra local servers started and stopped with
    the feed and solution ones (rtkrcv's second input connects to one); `on_start`,
    called when a new rtkrcv process has started, once the previous one's connections
    are gone; and `label`, the name used in log messages. `request_restart(reason)` ends the current
    process (SIGTERM) and starts a new one at once, without the exit backoff."""

    def __init__(self, settings, hub, on_solution=None, clock=time.monotonic, wall=time.time,
                 restart_s=RESTART_S, restart_max_s=RESTART_MAX_S, reset_after_s=RESET_AFTER_S,
                 hang_s=HANG_S, no_solution_s=NO_SOLUTION_S, stop_timeout_s=STOP_TIMEOUT_S,
                 stats_s=STATS_S, poll_s=POLL_S, proc="/proc", make_config=None, servers=None,
                 on_start=None, label="measured position"):
        self.settings = settings
        self.hub = hub
        self._on_solution = on_solution
        self._on_start = on_start
        self._clock = clock
        self._wall = wall
        self.restart_s, self.restart_max_s = restart_s, restart_max_s
        self.reset_after_s, self.hang_s = reset_after_s, hang_s
        self.no_solution_s, self.stop_timeout_s = no_solution_s, stop_timeout_s
        self.stats_s, self.poll_s, self._proc_dir = stats_s, poll_s, proc
        self.label = label
        self._make_config = make_config or self._default_config
        self.servers = dict(servers or {})
        self.feed = FeedServer()
        self.solutions = SolutionServer(self._solution, label=label)
        self.output = OutputLines()
        self._subscriber = None
        self._task = None
        self._msm_seen = asyncio.Event()
        self._last_msm = None
        self._process = None
        self._started = None          # when the current rtkrcv started
        self._last_solution = None
        self._retry_at = None
        self._unavailable = ""
        self._stopping = False
        self._restart_reason = ""     # set by request_restart until the supervisor acts
        self.console = None           # "none" (-nc, RTKLIB-EX) or "pty" (-d), once probed
        self.last_exit = None         # {code, reason, ran_s}
        self.restarts = 0
        self.planned_restarts = 0     # by request_restart (not counted as failures)
        self.runs = 0                 # rtkrcv processes started
        self._restart_days = {}       # UTC day -> restarts
        self.cpu_percent = None
        self.rss_bytes = None
        self._stats_mark = None       # (clock, cpu seconds)
        self._eph_seen = set()        # GPS satellites with an ephemeris since rtkrcv started
        self._eph_ready = None        # when there were HANG_EPHEMERIDES of them
        self.stop_exit_code = None    # rtkrcv's exit code when the app stopped it
        self.error = ""

    # ---- hub subscriber, on the event loop ----

    def _on_frame(self, frame, number):
        if is_msm(number):
            self._last_msm = self._clock()
            self._msm_seen.set()
        elif number == 1019 and self._process is not None and self.feed.connected:
            # Distinct satellites, counted only while rtkrcv is connected to the feed.
            self._eph_seen.add(gps_ephemeris_prn(frame))
            if self._eph_ready is None and len(self._eph_seen) >= HANG_EPHEMERIDES:
                self._eph_ready = self._clock()
        self.feed.send(frame)

    def _solution(self, solution, line):
        self._last_solution = self._clock()
        if self._on_solution is not None:
            self._on_solution(solution, line)

    def _default_config(self, ports):
        return build_config(ports["feed"], ports["solution"], self.settings.elevation_mask,
                            self.settings.snr_mask)

    def _config_text(self, ports=None):
        if ports is None:   # a check before anything runs: any valid ports
            ports = {name: 1 for name in ("feed", "solution", *self.servers)}
        return self._make_config(ports)

    # ---- life cycle ----

    def start(self):
        if not self.settings.enabled or self._task is not None:
            return
        try:
            self._config_text()
        except ValueError as error:
            self._not_available(f"invalid options: {error}")
            return
        if shutil.which(self.settings.binary) is None:
            self._not_available(f"rtkrcv not found ({self.settings.binary})")
            return
        self._stopping = False
        self._subscriber = self.hub.subscribe(self.label, self._on_frame)
        self._task = asyncio.get_running_loop().create_task(self._supervise())

    def request_restart(self, reason):
        """End the running rtkrcv (SIGTERM, so it saves its cache) and start a new one
        at once: a new session for a static solution. False if none is running (the
        next start is a new session anyway)."""
        if self._process is None or self._stopping or self._restart_reason:
            return False
        self._restart_reason = reason
        return True

    def restart_pending(self):
        """True from an accepted request_restart until the supervisor acts on it (the
        session's numbers until then are the old run's)."""
        return bool(self._restart_reason)

    def running_s(self):
        """Seconds the current rtkrcv has run, or None without one."""
        return None if self._process is None else self._clock() - self._started

    async def stop(self):
        """Stop rtkrcv (SIGTERM, SIGKILL after stop_timeout_s) and the servers."""
        self._stopping = True
        if self._subscriber is not None:
            self.hub.unsubscribe(self._subscriber)
            self._subscriber = None
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.gather(self.feed.stop(), self.solutions.stop(),
                             *(server.stop() for server in self.servers.values()))

    async def _supervise(self):
        try:
            await self._msm_seen.wait()
            delay = self.restart_s
            while not self._stopping:
                binary = shutil.which(self.settings.binary)
                if binary is None:
                    self._not_available(f"rtkrcv not found ({self.settings.binary})")
                    return
                binary = os.path.abspath(binary)   # it runs in its work folder
                if self.console is None:
                    version = await probe_version(binary)
                    self.console = "pty" if version is None else "none"
                    if version is not None:
                        self.output.version = version
                try:
                    await self.feed.start()
                    await self.solutions.start()
                    for server in self.servers.values():
                        await server.start()
                except OSError as error:
                    self.error = f"local servers: {error}"
                    reason = self.error
                    ran = 0.0
                else:
                    ran, reason = await self._run_once(binary)
                    if self._unavailable:
                        return
                if self._stopping:
                    return
                if ran >= self.reset_after_s:
                    delay = self.restart_s
                planned, self._restart_reason = self._restart_reason, ""
                if planned and reason.startswith("restarted:"):
                    self.planned_restarts += 1
                    LOG.info("%s: rtkrcv restarted: %s", self.label, planned)
                    continue
                # A request that came as rtkrcv exited or hung on its own is a failure
                # like any other: counted, backed off.
                self._note_restart()
                LOG.warning("%s: rtkrcv %s; restarting in %.0f s", self.label, reason, delay)
                self._retry_at = self._clock() + delay
                await self._sleep(delay)
                self._retry_at = None
                delay = min(delay * 2, self.restart_max_s)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # never let the supervisor die silently
            LOG.exception("%s: supervisor failed", self.label)
            self._not_available(f"supervisor failed: {type(error).__name__}")

    async def _sleep(self, seconds):
        await asyncio.sleep(seconds)

    def _not_available(self, reason):
        self._unavailable = reason
        LOG.error("%s not available: %s", self.label, reason)

    def _note_restart(self):
        self.restarts += 1
        day = datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y-%m-%d")
        self._restart_days = {day: self._restart_days.get(day, 0) + 1}

    async def _run_once(self, binary):
        """Run rtkrcv until it exits, hangs, or the runner stops. Returns (seconds it
        ran, why it ended)."""
        work = Path(self.settings.work_dir).absolute()
        try:
            work.mkdir(parents=True, exist_ok=True)
            ports = {"feed": self.feed.port, "solution": self.solutions.port,
                     **{name: server.port for name, server in self.servers.items()}}
            (work / CONFIG_NAME).write_text(self._config_text(ports))
        except (OSError, ValueError) as error:
            reason = f"could not write its config: {type(error).__name__}: {error}"
            self.last_exit = {"code": None, "reason": reason, "ran_s": 0.0}
            return 0.0, reason
        master = slave = None
        if self.console == "pty":
            try:
                master, slave = os.openpty()
            except OSError as error:
                self._not_available(f"no pseudo-terminal for its console: {error}")
                return 0.0, ""
        loop = asyncio.get_running_loop()
        reading = False
        drain = None
        process = None
        try:
            if master is not None:
                os.set_blocking(master, False)
                loop.add_reader(master, self._read_console, master)
                reading = True
                args = ["-s", "-o", str(work / CONFIG_NAME), "-d", os.ttyname(slave)]
            else:
                args = ["-nc", "-o", str(work / CONFIG_NAME)]
            # The last process's connections end before the next starts, so lines still
            # in a socket buffer from it are handled under the old session.
            for server in (self.feed, self.solutions, *self.servers.values()):
                await server.wait_disconnected(1.0)
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, *args, cwd=str(work),
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                    preexec_fn=_parent_death_signal())
            except OSError as error:
                self._not_available(f"could not start {binary}: {error}")
                return 0.0, ""
            self._process = process
            self._started = self._clock()
            self._last_solution = None
            self._eph_seen = set()
            self._eph_ready = None
            self._stats_mark = None
            self.cpu_percent = self.rss_bytes = None
            self.runs += 1
            LOG.info("%s: rtkrcv started (pid %s)", self.label, process.pid)
            if self._on_start is not None:
                self._on_start()
            drain = loop.create_task(self._drain(process.stdout))
            reason = await self._watch(process)
            if process.returncode is None:      # hang: stop it, then restart
                await self._terminate(process)
                self.stop_exit_code = process.returncode
            ran = self._clock() - self._started
            self.last_exit = {"code": process.returncode, "reason": reason,
                              "ran_s": round(ran, 1)}
            return ran, reason
        finally:
            if process is not None and process.returncode is None:
                await asyncio.shield(self._terminate(process))
                self.stop_exit_code = process.returncode
            if drain is not None:
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(drain), 1)
                drain.cancel()
            if reading:
                loop.remove_reader(master)
            for fd in (master, slave):
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            self._process = None

    def _read_console(self, master):
        try:
            data = os.read(master, 4096)
        except BlockingIOError:
            return
        except OSError:
            asyncio.get_running_loop().remove_reader(master)
            return
        if data:
            self.output.feed(data)

    async def _drain(self, stream):
        while data := await stream.read(4096):
            self.output.feed(data)

    async def _watch(self, process):
        """Wait until rtkrcv exits or hangs; read its CPU and memory meanwhile."""
        self._read_stats(process.pid)
        waiter = asyncio.ensure_future(process.wait())
        try:
            while True:
                await asyncio.wait({waiter}, timeout=self.poll_s)
                if process.returncode is not None:
                    return f"exited with code {process.returncode}"
                reason = self._check(process)
                if reason:
                    return reason
        finally:
            waiter.cancel()

    def _check(self, process):
        """Once a poll: CPU and memory now and then; a reason if rtkrcv hangs."""
        now = self._clock()
        if self._stats_mark is None or now - self._stats_mark[0] >= self.stats_s:
            self._read_stats(process.pid)
        if self._restart_reason:
            return f"restarted: {self._restart_reason}"
        # A cold start waits for orbits: no hang until enough GPS ephemerides came in.
        if self._eph_ready is None and self._last_solution is None:
            return ""
        last = max(t for t in (self._last_solution, self._eph_ready, self._started)
                   if t is not None)
        msm = self._last_msm is not None and now - self._last_msm <= MSM_FLOWING_S
        if msm and now - last >= self.hang_s:
            return f"gave no solution for {self.hang_s:.0f} s while MSM arrived"
        return ""

    def _read_stats(self, pid):
        stats = read_proc_stats(pid, self._proc_dir)
        if stats is None:
            return
        cpu, self.rss_bytes = stats
        now = self._clock()
        if self._stats_mark is not None and now > self._stats_mark[0]:
            self.cpu_percent = round(100 * (cpu - self._stats_mark[1])
                                     / (now - self._stats_mark[0]), 1)
        self._stats_mark = (now, cpu)

    async def _terminate(self, process):
        """SIGTERM, so rtkrcv saves its ephemeris cache; SIGKILL if it has not exited
        within stop_timeout_s."""
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), self.stop_timeout_s)
            return
        except asyncio.TimeoutError:
            pass
        LOG.warning("%s: rtkrcv ignored SIGTERM; killing it", self.label)
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()

    # ---- status ----

    def state(self):
        if not self.settings.enabled:
            return "off"
        if self._unavailable:
            return "not available"
        if self._retry_at is not None:
            return "restarting"
        if self._process is None:
            return "idle" if not self._msm_seen.is_set() else "starting"
        now = self._clock()
        if self._last_solution is None:
            return "starting" if now - self._started < self.no_solution_s else "no solution"
        return "running" if now - self._last_solution <= self.no_solution_s else "no solution"

    def ephemerides(self):
        return {system: sum(self.hub.frames.get(n, 0) for n in numbers)
                for system, numbers in EPHEMERIS_TYPES.items()}

    def snapshot(self):
        now = self._clock()
        state = self.state()
        day = datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y-%m-%d")
        return {
            "enabled": self.settings.enabled,
            "state": state,
            "reason": self._unavailable or None,
            "pid": None if self._process is None else self._process.pid,
            "running_s": None if self._process is None else round(now - self._started, 1),
            "last_solution_age_s": None if self._last_solution is None
            else round(now - self._last_solution, 1),
            "retry_in_s": None if self._retry_at is None
            else round(max(self._retry_at - now, 0), 1),
            "last_exit": self.last_exit,
            "stop_exit_code": self.stop_exit_code,
            "restarts": self.restarts,
            "restarts_today": self._restart_days.get(day, 0),
            "planned_restarts": self.planned_restarts,
            "cpu_percent": self.cpu_percent,
            "rss_bytes": self.rss_bytes,
            "rtkrcv_version": self.output.version,
            "console": self.console,
            "elevation_mask": self.settings.elevation_mask,
            "snr_mask": self.settings.snr_mask,
            "feed": self.feed.snapshot(),
            "solutions": self.solutions.snapshot(),
            "ephemerides": self.ephemerides(),
            "error": self.error or None,
            "output": self.output.snapshot() if state in (
                "not available", "restarting", "no solution", "starting") else [],
        }


# ---------------------------------------------------------------- solution log

def _last_header(stream):
    """The last `%` line of an open text file (read from the start), or None."""
    stream.seek(0)
    header = None
    for line in stream:
        if line.startswith("%"):
            header = line.rstrip("\n")
    return header


class SolutionLog:
    """`<directory>/YYYY-MM-DD<suffix>` (UTC day of arrival): a `%` header line per file, then
    rtkrcv's llh lines as received. They hold positions: keep them private, like the raw
    log.

    `write` never touches the disk: lines go into a bounded queue that a writer task
    drains, doing the file work in an executor; a full queue drops lines and counts them.
    Without `start` (tools, tests) lines are written at once. Nothing is written while
    `room()` is false (the raw log's free space floor). Finished days are compressed by
    `housekeep`, with the raw log's `compress`. File writes and compression hold one lock,
    so a late line for a day being compressed waits, then starts a new file for that day
    (compressed in turn as `-2`), and is never lost. When the header changes (another
    rtkrcv version or other masks after a restart), a new `%` line is written before the
    next lines, so each block of lines follows the header it was made with."""

    QUEUE = 3600
    BATCH = 600
    STOP_TIMEOUT_S = 5.0
    SETTLE_S = 120          # a file written this recently is not compressed yet
    RETRY_S = 3600

    def __init__(self, directory, header=lambda: "", wall=time.time, room=None,
                 queue_size=QUEUE, suffix=".pos"):
        self.directory = Path(directory)
        self.suffix = suffix
        self._header = header
        self._wall = wall
        self._room = room or (lambda: True)
        self._queue = deque()
        self._queue_size = queue_size
        self._wake = None
        self._task = None
        self._busy = False
        self._stopping = False
        self._failed = {}       # file name -> when compressing it failed
        self._lock = threading.Lock()   # file writes and compression, in the executor
        self._headers = {}      # file name -> the last header line in it
        self.written = 0
        self.dropped = 0
        self.skipped_low_disk = 0
        self.abandoned = 0      # lines handed to a write that stop() gave up waiting for
        self._in_flight = 0
        self.error = ""
        with contextlib.suppress(OSError):
            for leftover in self.directory.glob("*.partial"):   # an interrupted compress
                leftover.unlink(missing_ok=True)

    def write(self, line):
        """Queue one line, with the day and the header current now, so it is written under
        the header it was made with even if the header changes before the write."""
        entry = (utc_day(self._wall()), self._header().rstrip("\n"), line)
        if self._task is None:
            self._finish(self._flush([entry]))
            return
        if len(self._queue) >= self._queue_size:
            self.dropped += 1
            return
        self._queue.append(entry)
        if len(self._queue) == 1 or len(self._queue) >= self.BATCH:
            self._wake.set()

    def start(self):
        if self._task is None:
            self._wake = asyncio.Event()
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self._writer())
        return self._task

    async def stop(self, timeout=STOP_TIMEOUT_S):
        task = self._task
        if task is None:
            return
        self._stopping = True
        self._wake.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # A batch already handed to its thread may still land, or not: counted apart.
            self.abandoned += self._in_flight
            self._in_flight = 0
            self.dropped += len(self._queue)
            self._queue.clear()
        self._task = None

    async def flush(self):
        """Wait until everything queued so far is on disk (for tests)."""
        while self._queue or self._busy:
            await asyncio.sleep(0.005)

    async def _writer(self):
        loop = asyncio.get_running_loop()
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 5)
            self._wake.clear()
            while self._queue:
                self._busy = True
                try:
                    batch = [self._queue.popleft()
                             for _ in range(min(self.BATCH, len(self._queue)))]
                    self._in_flight = len(batch)
                    result = await asyncio.shield(
                        loop.run_in_executor(None, self._flush, batch))
                    self._in_flight = 0
                    self._finish(result)
                except Exception as error:   # keep the writer alive whatever happens
                    self._finish((0, f"{type(error).__name__}: {error}", 0,
                                  self._in_flight))
                    self._in_flight = 0
                finally:
                    self._busy = False
            if self._stopping:
                return

    def _finish(self, result):
        written, error, skipped, lost = result
        self.written += written
        self.skipped_low_disk += skipped
        self.dropped += lost
        if error:
            if not self.error:
                LOG.error("solution log: %s", error)
            self.error = f"solution log: {error}"
        elif written:
            self.error = ""

    def _flush(self, batch):
        """Append the lines, each to its day's file; a new file starts with the header.
        Runs in the executor. Returns (written, error, skipped, lost)."""
        try:
            if not self._room():
                return 0, "", len(batch), 0
        except Exception as error:
            LOG.debug("solution log room check: %s", error)
        # Runs of lines with the same day and header, in arrival order.
        runs = []
        for day, header, line in batch:
            if runs and runs[-1][:2] == (day, header):
                runs[-1][2].append(line)
            else:
                runs.append((day, header, [line]))
        written = 0
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                for day, header, lines in runs:
                    path = self.directory / f"{day}{self.suffix}"
                    with open(path, "a+", encoding="ascii", errors="replace") as stream:
                        if stream.tell() == 0:
                            self._headers.pop(path.name, None)
                        elif path.name not in self._headers:
                            # First write here since the start: a line cut short by a
                            # power loss gets its newline, so the next stays on its own.
                            if not _ends_with_newline(path):
                                stream.write("\n")
                            self._headers[path.name] = _last_header(stream)
                        if self._headers.get(path.name) != header:
                            stream.write(header + "\n")
                            self._headers[path.name] = header
                        stream.write("".join(line.rstrip("\r\n") + "\n" for line in lines))
                    written += len(lines)
            return written, "", 0, 0
        except OSError as error:
            return written, str(error), 0, len(batch) - written

    def _compress(self, path):
        """The raw log's compress, under the file lock (in the executor)."""
        with self._lock:
            self._headers.pop(path.name, None)
            return compress(path)

    def pending_days(self):
        """Files of finished UTC days, untouched for SETTLE_S, not failed in the last
        RETRY_S."""
        now = self._wall()
        today = utc_day(now)
        due = []
        try:
            paths = sorted(self.directory.glob(f"*{self.suffix}"))
        except OSError:
            return due
        for path in paths:
            try:
                settled = now - path.stat().st_mtime >= self.SETTLE_S
            except OSError:
                continue
            failed = self._failed.get(path.name)
            if (path.stem[:10] < today and settled
                    and (failed is None or now - failed >= self.RETRY_S)):
                due.append(path)
        return due

    async def housekeep(self):
        """Compress finished days, in an executor."""
        loop = asyncio.get_running_loop()
        for path in self.pending_days():
            try:
                await loop.run_in_executor(None, self._compress, path)
                self._failed.pop(path.name, None)
                LOG.info("compressed %s", path.name)
            except Exception as error:
                self._failed[path.name] = self._wall()
                self.error = f"solution log: compress {path.name}: {error}"
                LOG.error("%s", self.error)

    def snapshot(self):
        return {"folder": self.directory.name, "lines_written": self.written,
                "dropped": self.dropped, "skipped_low_disk": self.skipped_low_disk,
                "abandoned": self.abandoned,
                "error": self.error or None}
