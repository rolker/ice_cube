"""RTCM fan-out to the outputs (local caster, upload), the shared output filter, and the
connection log.

Nothing here can reach the receiver: this module, the caster and the uploader never
import the command, control or serial modules (a unit test checks it).
"""
import asyncio
import contextlib
import json
import logging
import time
from collections import Counter, deque
from datetime import datetime, timezone

LOG = logging.getLogger("gnss_base")

STOP_TIMEOUT_S = 5.0   # ConnectionLog.stop waits at most this long for the disk

# Station messages (antenna position 1005/1006, receiver and antenna names 1033) are
# sent to the outputs at most once per this many seconds; the receiver still sends them
# every second into the raw log and the page. Everything else passes as it comes.
THIN_S = {1005: 10.0, 1006: 10.0, 1033: 10.0}
THIN_TOLERANCE_S = 0.5     # a 1 Hz message that arrives a little early still counts


def is_rtcm_frame(frame):
    """A whole RTCM3 frame by its shape: preamble, length and room for the CRC. The
    parser has checked the CRC already; this keeps anything else out of the outputs."""
    if not isinstance(frame, (bytes, bytearray)) or len(frame) < 8 or frame[0] != 0xD3:
        return False
    if frame[1] & 0xFC:
        return False
    length = ((frame[1] & 0x03) << 8) | frame[2]
    return length >= 2 and len(frame) == length + 6


def frame_type(frame):
    return (frame[3] << 4) | (frame[4] >> 4)


class Secret:
    """A password. Only `reveal()` gives the value; everything else prints it hidden."""

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value or ""

    def __repr__(self):
        return "Secret(set)" if self._value else "Secret(empty)"

    __str__ = __repr__

    def __bool__(self):
        return bool(self._value)

    def __eq__(self, other):
        return NotImplemented

    __hash__ = None

    def __reduce__(self):
        raise TypeError("a Secret cannot be pickled")

    def reveal(self):
        return self._value

    def scrub(self, text):
        """`text` with any copy of the value replaced, for text that came from outside."""
        return text.replace(self._value, "***") if self._value else text


class _Subscriber:
    def __init__(self, name, callback):
        self.name = name
        self.callback = callback
        self.errors = 0


class RtcmHub:
    """Receives each whole, CRC-checked RTCM frame from the pipeline, after the raw log
    write, and hands it to every subscriber. `publish` never awaits; a subscriber that
    raises is counted and skipped, so it cannot stop logging or the other outputs."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._subscribers = []
        self.frames = Counter()
        self.bytes = Counter()
        self.last_frame = None
        self.rejected = 0

    def subscribe(self, name, callback):
        """`callback(frame, number)`, called on the event loop; must not block."""
        subscriber = _Subscriber(name, callback)
        self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def publish(self, frame):
        if not is_rtcm_frame(frame):
            self.rejected += 1
            return
        frame = bytes(frame)
        number = frame_type(frame)
        self.frames[number] += 1
        self.bytes[number] += len(frame)
        self.last_frame = self._clock()
        for subscriber in list(self._subscribers):
            try:
                subscriber.callback(frame, number)
            except Exception:
                subscriber.errors += 1
                if subscriber.errors == 1:
                    LOG.exception("RTCM output %s failed; counting further errors",
                                  subscriber.name)

    def age_s(self):
        """Seconds since the last RTCM frame, or None if there has been none."""
        return None if self.last_frame is None else self._clock() - self.last_frame

    def flowing(self, within_s):
        age = self.age_s()
        return age is not None and age <= within_s

    def snapshot(self):
        age = self.age_s()
        return {"last_rtcm_age_s": None if age is None else round(age, 1),
                "frames": sum(self.frames.values()), "bytes": sum(self.bytes.values()),
                "subscriber_errors": {s.name: s.errors for s in self._subscribers if s.errors}}


class OutputFilter:
    """What leaves the app: RTCM frames only, with 1005/1006/1033 thinned to one per
    THIN_S. Each output (each caster client, the uploader) has its own filter, so the
    first station message reaches a new client at once instead of up to 10 s later."""

    def __init__(self, clock=time.monotonic, intervals=None):
        self._clock = clock
        self._intervals = THIN_S if intervals is None else intervals
        self._last = {}
        self.thinned = 0
        self.dropped = 0

    def accept(self, frame):
        if not is_rtcm_frame(frame):
            self.dropped += 1
            return False
        number = frame_type(frame)
        interval = self._intervals.get(number)
        if interval is None:
            return True
        now = self._clock()
        last = self._last.get(number)
        if last is not None and now - last < interval - THIN_TOLERANCE_S:
            self.thinned += 1
            return False
        self._last[number] = now
        return True


class ConnectionLog:
    """`caster.jsonl`: one JSON line per caster connect, disconnect or refusal, per upload
    connect or error, and what rovers send. Callers pass only safe fields: never a
    password, a rover's GGA line or the position taken from it. The few sample lines of
    what a rover sent (`rover_sent`) are any bytes, NMEA or not, with every number that
    could be a coordinate replaced by "-" first (`caster.redact_position`).

    `write` never touches the disk on the caller's path. Once `start` has run (in the
    app), lines go into a bounded queue that a writer task drains, doing the file work in
    an executor; a full queue drops lines and counts them. Without `start` (tools, most
    tests) lines are written at once.

    Limits, so a busy or hostile network cannot fill the disk:
    - each address gets at most PER_ADDRESS lines a minute (NOISY_PER_ADDRESS for
      sourcetable requests, refusals and failed logins), and all addresses together
      PER_MINUTE; the rest are counted and summed up in one `suppressed` line per
      address when the minute ends;
    - the file is rotated at `max_bytes` to `caster.jsonl.1`, ... keeping `keep` old ones;
    - nothing is written while `room()` is false (the raw log's free space floor)."""

    NOISY = frozenset({"sourcetable", "refused", "auth_failed"})
    NOISY_PER_ADDRESS = 10
    PER_ADDRESS = 30
    PER_MINUTE = 200
    WINDOW_S = 60
    MAX_ADDRESSES = 200        # addresses tracked in one window; the rest share "other"
    MAX_SUMMARY_LINES = 20     # suppressed lines per window; the rest are summed in one
    QUEUE = 2000
    BATCH = 500
    MAX_BYTES = 10 * 1024 * 1024
    KEEP = 3

    def __init__(self, path, wall=time.time, clock=time.monotonic, room=None,
                 max_bytes=MAX_BYTES, keep=KEEP, queue_size=QUEUE):
        self.path = path
        self._wall = wall
        self._clock = clock
        self._room = room or (lambda: True)
        self.max_bytes = max_bytes
        self.keep = keep
        self._queue_size = queue_size
        self._queue = deque()
        self._wake = None
        self._task = None
        self._size = None          # bytes in the current file, known after the first write
        self.error = ""
        self.dropped = 0           # lines lost: queue full or write failed
        self.suppressed = 0        # lines held back by the rate limits
        self.skipped_low_disk = 0  # lines not written because free space is low
        self._window = None
        self._counts = Counter()          # address -> lines written this window
        self._held = {}                   # address -> Counter of events held back
        self._total = 0                   # rate-limited lines written this window
        self._busy = False
        self._stopping = False
        self._abandoned = False    # stop() gave up on a hung disk: lines are dropped

    # ---- on the event loop ----

    def write(self, event, **fields):
        """Queue one line; returns the entry. Never blocks."""
        self._roll()
        entry = self._entry(event, fields)
        address = fields.get("address")
        if address is not None and not self._allow(event, address):
            return entry
        self._put(entry)
        return entry

    def _entry(self, event, fields):
        utc = datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds")
        return dict({"utc": utc, "event": event}, **fields)

    def _allow(self, event, address):
        if (address not in self._counts and address not in self._held
                and len(self._counts) + len(self._held) >= self.MAX_ADDRESSES):
            address = "other"
        limit = self.NOISY_PER_ADDRESS if event in self.NOISY else self.PER_ADDRESS
        if self._counts[address] >= limit or self._total >= self.PER_MINUTE:
            self._held.setdefault(address, Counter())[event] += 1
            self.suppressed += 1
            return False
        self._counts[address] += 1
        self._total += 1
        return True

    def _roll(self):
        """At the end of each minute, one `suppressed` line per address that had lines
        held back."""
        window = int(self._clock() // self.WINDOW_S)
        if window == self._window:
            return
        self._window = window
        held, self._held = self._held, {}
        self._counts.clear()
        self._total = 0
        addresses = sorted(held, key=lambda a: -sum(held[a].values()))
        for address in addresses[:self.MAX_SUMMARY_LINES]:
            self._put(self._entry("suppressed", {
                "address": address, "count": sum(held[address].values()),
                "events": dict(held[address]), "window_s": self.WINDOW_S}))
        rest = addresses[self.MAX_SUMMARY_LINES:]
        if rest:
            total = Counter()
            for address in rest:
                total.update(held[address])
            self._put(self._entry("suppressed", {
                "address": f"{len(rest)} more addresses", "count": sum(total.values()),
                "events": dict(total), "window_s": self.WINDOW_S}))

    def _put(self, entry):
        if self.path is None:
            return
        if self._abandoned:
            self.dropped += 1
            return
        if self._task is None:
            self._queue.append(entry)
            self._finish(self._flush(self._take()))
            return
        if len(self._queue) >= self._queue_size:
            self.dropped += 1
            return
        self._queue.append(entry)
        if len(self._queue) == 1 or len(self._queue) >= self.BATCH:
            self._wake.set()

    def _take(self):
        lines = []
        while self._queue and len(lines) < self.BATCH:
            lines.append(json.dumps(self._queue.popleft(), default=str) + "\n")
        return lines

    def start(self):
        """Write from now on in a background task (call on the running loop)."""
        if self._task is None:
            self._wake = asyncio.Event()
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self._writer())
        return self._task

    async def stop(self, timeout=STOP_TIMEOUT_S):
        """Write what is queued (and any pending `suppressed` lines), end the writer task,
        and go back to writing at once. If that takes over `timeout` seconds (a hung
        disk), the task is cancelled, what is still queued is dropped and counted, and
        later lines are dropped too, so shutting down never waits on the disk."""
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
            self.dropped += len(self._queue)
            self._queue.clear()
            self._abandoned = True
            LOG.error("connection log: not written within %s s at shutdown; "
                      "%d lines dropped so far", timeout, self.dropped)
        self._task = None

    async def flush(self):
        """Wait until everything queued so far is on disk (for tests)."""
        while self._queue or self._busy:
            await asyncio.sleep(0.005)

    async def _writer(self):
        loop = asyncio.get_running_loop()
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.WINDOW_S / 4)
            self._wake.clear()
            if self._stopping:
                self._window = None   # sum up what was held back so far
            self._roll()
            while self._queue:
                self._busy = True
                try:
                    lines = self._take()
                    # Not cancelled midway: the executor would go on writing.
                    self._finish(await asyncio.shield(
                        loop.run_in_executor(None, self._flush, lines)))
                except Exception as error:  # keep the writer alive whatever happens
                    self._finish((False, f"{type(error).__name__}: {error}", 0, 0))
                finally:
                    self._busy = False
            if self._stopping:
                return

    def _finish(self, result):
        written, error, skipped, lost = result
        self.skipped_low_disk += skipped
        self.dropped += lost
        if error:
            if not self.error:
                LOG.error("connection log: %s", error)
            self.error = f"connection log: {error}"
        elif written:
            self.error = ""

    def snapshot(self):
        return {"log_error": self.error, "log_dropped": self.dropped,
                "log_suppressed": self.suppressed, "log_skipped_low_disk": self.skipped_low_disk}

    # ---- in the executor (or at once without start) ----

    def _flush(self, lines):
        """Append `lines`, rotating first if they would pass max_bytes. Returns
        (written, error). Touches only the file, the size and the skip counters."""
        if not lines:
            return False, "", 0, 0
        try:
            if not self._room():
                return False, "", len(lines), 0
        except Exception as error:  # a failing floor check must not lose the log
            LOG.debug("connection log room check: %s", error)
        data = "".join(lines).encode()
        try:
            if self._size is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._size = self.path.stat().st_size if self.path.exists() else 0
            if self._size and self._size + len(data) > self.max_bytes:
                self._rotate()
            with open(self.path, "ab") as stream:
                stream.write(data)
            self._size += len(data)
            return True, "", 0, 0
        except OSError as error:
            self._size = None
            return False, str(error), 0, len(lines)

    def _rotate(self):
        name = self.path.name
        oldest = self.path.with_name(f"{name}.{self.keep}")
        oldest.unlink(missing_ok=True)
        for number in range(self.keep - 1, 0, -1):
            older = self.path.with_name(f"{name}.{number}")
            if older.exists():
                older.replace(self.path.with_name(f"{name}.{number + 1}"))
        if self.keep > 0:
            self.path.replace(self.path.with_name(f"{name}.1"))
        else:
            self.path.unlink(missing_ok=True)
        self._size = 0
