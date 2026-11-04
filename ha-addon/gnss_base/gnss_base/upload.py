"""Upload the corrections to an NTRIP caster such as RTK2go: an NTRIP server connection.

The app connects out, sends `SOURCE <password> /<mountpoint>` (NTRIP rev1), and after
`ICY 200 OK` writes only RTCM frames from the output filter. Whatever the caster sends
is read and dropped. This module never imports the command, control or serial modules,
so nothing from the network can reach the receiver.

Rules this follows, from RTK2go's usage pages (they apply to any SNIP caster):
- connect only while the receiver is sending RTCM (a frame in the last FLOWING_S), close
  if no RTCM has been sent FIRST_BYTE_S after the caster accepted, and disconnect once if
  the receiver stops for QUIET_S, so the caster never sees a silent source (SNIP drops one
  after about 10 s, and connect-then-nothing is a ban reason);
- never more than one upload connection, and never two at once during a reconnect;
- retries no sooner than 60 s after the last attempt, doubling to 30 min; 30 min after a
  refusal (bad password, mountpoint taken); 4 h after a reply that looks like a ban;
  after MAX_REFUSALS refusals or MAX_BANS ban replies without an accepted connection in
  between, no more tries until the options change or the app restarts; at most
  MAX_ATTEMPTS_PER_DAY attempts per UTC day;
- the next allowed attempt, the refusal and ban counts and the day's attempts and bytes
  are kept in a state file (in the app's /data, saved every few minutes while
  connected), so a restart does not skip a wait or the daily limit.
- RTCM only, with 1005/1033 thinned to once per 10 s; stop and report if a minute
  averages over 30 kbit/s or a UTC day reaches 400 MB.

The password is held in a Secret, whose repr and str are redacted. It is written to the
socket in the request and nowhere else: not to logs, the connection log, errors or status.
The state file holds only a salted hash of the settings, to notice that they changed.
"""
import asyncio
import hashlib
import json
import logging
import math
import random
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from .hub import OutputFilter, Secret

LOG = logging.getLogger("gnss_base")
STATE_STOP_WAIT_S = 2.0   # stop() waits at most this long for the last state write

FIRST_RETRY_S = 60          # first retry, and the least time between any two attempts
MAX_RETRY_S = 30 * 60       # the retry delay doubles up to this
JITTER = 0.1                # up to this fraction is added to a retry delay
RESET_AFTER_S = 30 * 60     # a connection that lasted this long resets the delay
REFUSED_WAIT_S = 30 * 60    # after "ERROR - Bad Password" or "Mount Point Taken"
BANNED_WAIT_S = 4 * 3600    # after a reply that looks like a ban (RTK2go bans ~3 h)
GUARD_WAIT_S = 30 * 60      # after the rate guard stopped the upload
MAX_ATTEMPTS_PER_DAY = 100  # then nothing until the next UTC day
MAX_REFUSALS = 3            # refusals with no accepted connection in between: stop
MAX_BANS = 2                # the same for ban-like replies
FLOWING_S = 3               # connect only if RTCM arrived this recently
FIRST_BYTE_S = 5            # close if no RTCM was sent this long after the caster's OK
QUIET_S = 8                 # disconnect if no RTCM from the receiver for this long
CONNECT_TIMEOUT_S = 15
REPLY_TIMEOUT_S = 10
MAX_REPLY = 1024
STALL_BYTES = 64 * 1024     # socket buffer above this: frames are skipped...
STALL_S = 60                # ...and after this long the connection is closed
RATE_WINDOW_S = 60
RATE_LIMIT_BITS = 30_000    # RTK2go allows 35 kbit/s; a base sends about 12
DAY_LIMIT_BYTES = 400_000_000  # RTK2go bans above 500 MB a day
POLL_S = 1.0
SAVE_EVERY_S = 300          # while connected, the state file is saved this often...
SAVE_EVERY_BYTES = 10_000_000   # ...or after this many more bytes, for the daily count
STYLES = ("rev1", "rev2")
MOUNTPOINT = re.compile(r"[A-Za-z0-9_.-]{1,64}")
HOST = re.compile(r"[A-Za-z0-9.:-]{1,253}")
SECRET_TEXT = re.compile(r"[\x21-\x7e]{1,128}")   # printable, no spaces or line breaks


@dataclass
class UploadSettings:
    host: str = "rtk2go.com"
    port: int = 2101
    mountpoint: str = ""
    password: Secret = field(default_factory=lambda: Secret(""))
    style: str = "rev1"

    @property
    def enabled(self):
        return bool(self.mountpoint) and bool(self.password)

    def problem(self):
        """Why these settings cannot be used, or "". Never contains the password."""
        if not self.enabled:
            return ""
        if self.style not in STYLES:
            return f"upload_style must be rev1 or rev2, not {self.style[:20]!r}."
        if self.style == "rev2":
            return ("upload_style rev2 (NTRIP 2.0 POST) is not supported yet. Use rev1, "
                    "which RTK2go accepts for most reservations, or ask RTK2go to change "
                    "the reservation's style.")
        if not HOST.fullmatch(self.host or ""):
            return "upload_host is empty or not a host name."
        if not isinstance(self.port, int) or not 0 < self.port < 65536:
            return "upload_port must be 1 to 65535."
        if not MOUNTPOINT.fullmatch(self.mountpoint):
            return "upload_mountpoint may hold only letters, digits, '_', '-' and '.'."
        if not SECRET_TEXT.fullmatch(self.password.reveal()):
            return "upload_password must be printable characters without spaces."
        return ""


BAN_TEXT = re.compile(r"\b(banned|blocked)\b|has been banned", re.IGNORECASE)
HTML = re.compile(r"<\s*(html|!doctype\s+html|body|head)\b", re.IGNORECASE)
STATUS = re.compile(r"HTTP/\d(?:\.\d)?\s+(\d{3})\b")


def classify(reply):
    """Sort the caster's first reply: ok, refused, banned or failed.

    ok: `ICY 200 OK` (SNIP, rev1), or a bare `OK` line (some other rev1 casters).
    banned: HTTP 403 or 429, an HTML page (SNIP's ban page), or the words "banned" or
    "blocked" ("has been Banned"). refused: `ERROR - ...` (bad password, mountpoint
    taken) or another HTTP 4xx such as 401."""
    head = reply.lstrip().split("\n", 1)[0].strip()
    if head.startswith("ICY 200 OK") or head == "OK":
        return "ok"
    status = STATUS.match(head)
    code = status.group(1) if status else ""
    if code in ("403", "429") or HTML.search(reply) or BAN_TEXT.search(reply):
        return "banned"
    if head.upper().startswith("ERROR") or code.startswith("4"):
        return "refused"
    return "failed"


def _accepted(data):
    data = data.lstrip()
    return data.startswith(b"ICY 200 OK") or re.match(rb"OK\r?\n", data) is not None


def _printable(text, limit=160):
    text = "".join(c if " " <= c <= "~" else " " for c in text)
    return " ".join(text.split())[:limit]


class Uploader:
    """Keeps at most one upload connection, and only while RTCM is flowing.

    `clock` (monotonic) times waits and rates, `wall` (Unix time) the UTC day for the
    daily limits, and `sleep` is the wait between attempts, so tests can run the retry
    schedule on a fake clock. `connect` opens the TCP connection."""

    _open = 0  # upload connections open in this process, across instances

    def __init__(self, settings, hub, events, version="", clock=time.monotonic,
                 wall=time.time, sleep=asyncio.sleep, connect=asyncio.open_connection,
                 rand=random.random, poll_s=POLL_S, state_path=None):
        self.settings = settings
        self.hub = hub
        self.events = events
        self.version = version
        self._clock, self._wall, self._sleep = clock, wall, sleep
        self._connect, self._rand, self._poll_s = connect, rand, poll_s
        self._task = None
        self._writer = None
        self.state = "off"
        self.reason = ""
        self.last_error = ""
        self.last_reply = ""
        self.alert = False          # red on the page: a refusal, ban or guard stop
        self._backoff = FIRST_RETRY_S
        self._next_attempt = None
        self._last_attempt = None
        self._connected_at = None
        self._day = None
        self.attempts_today = 0
        self.bytes_today = 0
        self.bytes_sent = 0         # this connection
        self.frames_sent = 0
        self.frames_skipped = 0
        self.total_bytes = 0
        self.connections = 0
        self._recent = deque()      # (time, bytes) over RATE_WINDOW_S
        self._recent_bytes = 0
        self._guard = ""
        self._stall_since = None
        # Refusals and ban-like replies since the last accepted connection. Only an
        # accepted connection resets them; other failures in between do not.
        self.refusals = 0
        self.bans = 0
        self.stopped = ""           # why tries stopped until the options change, or ""
        self._last_kind = ""
        self._state_path = state_path
        self._salt = secrets.token_hex(16)
        self._saving = None
        self.state_error = ""
        problem = settings.problem()
        if problem:
            self.state, self.last_error, self.alert = "off", problem, True
            LOG.error("upload: %s", problem)
        elif not settings.enabled:
            self.reason = "Set upload_mountpoint and upload_password to upload."
        else:
            self._load()

    @property
    def usable(self):
        return self.settings.enabled and not self.settings.problem()

    def start(self):
        if self._task is None and self.usable:
            self._task = asyncio.create_task(self._guarded())
        return self._task

    async def _guarded(self):
        """Run the schedule; an unexpected error stops the upload (shown on the page)
        rather than risk a retry loop."""
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.exception("upload stopped after an internal error")
            self.alert = True
            self.last_error = self._scrub(f"Stopped after an internal error: {error}")
            self._set("off", "Restart the app to try again.")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                LOG.exception("upload stopped with an error")
            self._task = None
        # The last state write gets a moment; a stalled disk must not hold the app's stop
        # (the write itself runs on in a daemon thread, see __main__.DaemonExecutor).
        await self.saved(timeout=STATE_STOP_WAIT_S)

    # ---- the state file: survives a restart ----

    def _fingerprint(self, salt):
        settings = self.settings
        text = "\n".join((salt, settings.host, str(settings.port), settings.mountpoint,
                          settings.style, settings.password.reveal()))
        return hashlib.sha256(text.encode()).hexdigest()

    def _load(self):
        """Take the waits and counts from the state file. Times are stored as Unix time
        and turned into this run's monotonic clock; a wait never comes back longer than
        BANNED_WAIT_S, in case the wall clock stepped back. If the wall clock is behind
        the last attempt (not yet synced after a boot), only the FIRST_RETRY_S gap is
        kept. With changed settings the refusal count and a refusal's wait are dropped
        (not a ban's, which is about the IP address), but the 60 s gap since the last
        attempt still holds. A file that cannot be read or holds anything unexpected is
        ignored with a warning, and then the first attempt still waits FIRST_RETRY_S."""
        if self._state_path is None:
            return
        try:
            text = self._state_path.read_text()
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            self._state_ignored(error)
            return
        try:
            self._restore(json.loads(text))
        except Exception as error:  # anything at all: never stop the app over this file
            self._state_ignored(error)

    def _state_ignored(self, error):
        self.state_error = self._scrub(f"upload state: {error}")
        LOG.warning("upload state file ignored: %s", self.state_error)
        self.refusals = self.bans = self.attempts_today = self.bytes_today = 0
        self._backoff = FIRST_RETRY_S
        self._next_attempt = self._day = None
        self._last_kind = self.reason = ""
        self.alert = False
        self._guard = ""
        self._last_attempt = self._clock()   # still a minute before the first attempt

    def _restore(self, data):
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        now, wall = self._clock(), self._wall()

        def number(key, low=0, high=None):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if not math.isfinite(value):
                raise ValueError(f"{key} is {value}")
            value = max(value, low)
            return value if high is None else min(value, high)

        salt = data.get("salt")
        same = isinstance(salt, str) and data.get("settings") == self._fingerprint(salt)
        last = number("last_attempt_utc")
        nxt = number("next_attempt_utc")
        kind = data.get("kind") if isinstance(data.get("kind"), str) else ""
        bans = int(number("bans", 0, 100) or 0)
        refusals = int(number("refusals", 0, 100) or 0)
        backoff = number("backoff", FIRST_RETRY_S, MAX_RETRY_S) or FIRST_RETRY_S
        attempts = int(number("attempts_today", 0, 10 ** 6) or 0)
        sent = int(number("bytes_today", 0, 10 ** 12) or 0)
        unsynced = last is not None and wall < last
        if unsynced:
            LOG.warning("upload: the clock is behind the last attempt in the state file; "
                        "keeping only the %d s gap", FIRST_RETRY_S)
        # From here on nothing can fail: the values are set together.
        self.bans = bans
        if last is not None:
            self._last_attempt = now - min(max(wall - last, 0), BANNED_WAIT_S)
        if nxt is not None and not unsynced and (same or kind == "banned"):
            self._next_attempt = now + min(max(nxt - wall, 0), BANNED_WAIT_S)
        if same:
            self.refusals = refusals
            self._backoff = backoff
            self._last_kind = kind
            reason = data.get("reason")
            if isinstance(reason, str) and self._next_attempt is not None:
                self.reason = self._scrub(reason)
                self.alert = kind in ("refused", "banned", "guard", "day")
        day = data.get("day")
        if not isinstance(day, bool) and isinstance(day, int) and day == int(wall // 86400):
            self._day = day
            self.attempts_today = attempts
            self.bytes_today = sent
            if sent >= DAY_LIMIT_BYTES:
                self._guard = "day"   # nothing more today, even after a restart

    def _save(self):
        """Write the state file in a background thread (one at a time, in order)."""
        if self._state_path is None:
            return
        wall, now = self._wall(), self._clock()

        def utc(t):
            return None if t is None else round(wall + t - now, 3)

        data = {"version": 1, "salt": self._salt, "settings": self._fingerprint(self._salt),
                "last_attempt_utc": utc(self._last_attempt),
                "next_attempt_utc": utc(self._next_attempt), "kind": self._last_kind,
                "refusals": self.refusals, "bans": self.bans, "backoff": self._backoff,
                "reason": self._scrub(self.reason), "day": self._day,
                "attempts_today": self.attempts_today, "bytes_today": self.bytes_today}
        text = json.dumps(data, indent=1) + "\n"
        loop = asyncio.get_running_loop()
        previous, path = self._saving, self._state_path

        async def write():
            # One write at a time, in order: each waits for the one before it. The file
            # work runs in the loop's default executor (daemon threads in the app).
            if previous is not None:
                await asyncio.wait({previous})
            await loop.run_in_executor(None, _write_state, path, text)

        self._saving = loop.create_task(write())
        self._saving.add_done_callback(self._saved)

    def _saved(self, future):
        error = None if future.cancelled() else future.exception()
        if error is not None:
            if not self.state_error:
                LOG.error("upload state: %s", error)
            self.state_error = f"upload state: {error}"
        else:
            self.state_error = ""

    async def saved(self, timeout=None):
        """Wait for the last state write (for stop and tests), at most `timeout` s."""
        if self._saving is not None:
            await asyncio.wait({self._saving}, timeout=timeout)

    # ---- the schedule ----

    def _roll_day(self):
        day = int(self._wall() // 86400)
        if day != self._day:
            self._day = day
            self.attempts_today = 0
            self.bytes_today = 0
            if self._guard == "day":
                self._guard = ""

    def _seconds_to_next_day(self):
        return 86400 - self._wall() % 86400 + 1

    def _set(self, state, reason=""):
        self.state, self.reason = state, reason

    async def _run(self):
        while True:
            self._roll_day()
            now = self._clock()
            if self.stopped:
                self.alert = True
                self._set("stopped", self.stopped)
                await self._sleep(3600)
                continue
            if self._guard == "day":
                self.alert = True
                self._set("stopped for today", f"{self.bytes_today // 1_000_000} MB uploaded "
                          "today, near RTK2go's daily limit. Resumes after midnight UTC.")
                await self._sleep(max(self._seconds_to_next_day(), self._poll_s))
                continue
            if self.attempts_today >= MAX_ATTEMPTS_PER_DAY:
                self.alert = True
                self._set("stopped for today", f"{self.attempts_today} connection attempts "
                          "today. Resumes after midnight UTC.")
                await self._sleep(max(self._seconds_to_next_day(), self._poll_s))
                continue
            allowed = self._allowed_at()
            if allowed is not None and now < allowed:
                if self.state != "waiting":
                    self._set("waiting", self.reason)
                await self._sleep(allowed - now)
                continue
            if not self.hub.flowing(FLOWING_S):
                self._set("waiting for RTCM", "No RTCM from the receiver in the last "
                          f"{FLOWING_S} s; connects when it flows.")
                await self._sleep(self._poll_s)
                continue
            await self._session()

    def _allowed_at(self):
        """The earliest time of the next attempt, or None if any time."""
        times = [t for t in (self._next_attempt, None if self._last_attempt is None
                             else self._last_attempt + FIRST_RETRY_S) if t is not None]
        return max(times) if times else None

    def _schedule(self, kind, reason):
        """Set the next attempt after a session ended. Never sooner than FIRST_RETRY_S
        after the previous attempt. MAX_REFUSALS refusals or MAX_BANS ban replies
        without an accepted connection in between stop the tries until the options
        change or the app restarts."""
        now = self._clock()
        self._last_kind = kind
        if kind == "refused":
            delay = REFUSED_WAIT_S
            self.refusals += 1
            if self.refusals >= MAX_REFUSALS:
                self.stopped = (
                    f"Stopped after {self.refusals} refusals without an accepted connection in "
                    f"between ({reason}). Check "
                    "upload_mountpoint and upload_password against the RTK2go reservation "
                    "(both are case sensitive), and that no other base, such as an old "
                    "setup, is still sending to this mountpoint. Saving changed options "
                    "tries again after a minute; restarting with the same options tries "
                    f"once more, {REFUSED_WAIT_S // 60} min after the last refusal.")
        elif kind == "banned":
            delay = BANNED_WAIT_S
            self.bans += 1
            if self.bans >= MAX_BANS:
                self.stopped = (
                    f"Stopped after {self.bans} replies that look like a ban, without an "
                    f"accepted connection in between ({reason}). RTK2go bans an IP "
                    "address for hours after many failed or silent connections. Check "
                    "rtk2go.com for a notice about your IP address and fix the cause, "
                    "then restart the app; it tries once more, "
                    f"{BANNED_WAIT_S // 3600} h after the last such reply.")
        elif kind in ("guard", "day"):
            delay = GUARD_WAIT_S
        else:
            lasted = 0 if self._connected_at is None else now - self._connected_at
            if lasted >= RESET_AFTER_S or not math.isfinite(self._backoff):
                self._backoff = FIRST_RETRY_S
            delay = self._backoff * (1 + JITTER * self._rand())
            self._backoff = min(self._backoff * 2, MAX_RETRY_S)
        self.alert = kind in ("refused", "banned", "guard", "day")
        self._next_attempt = max(now + delay, self._last_attempt + FIRST_RETRY_S)
        if not math.isfinite(self._next_attempt):   # cannot happen; never retry at once
            self._next_attempt = now + MAX_RETRY_S
        self.reason = reason
        if self.stopped:
            LOG.error("upload: %s", self.stopped)

    # ---- one connection ----

    def _request(self):
        settings = self.settings
        agent = f"NTRIP gnss_base/{self.version}".rstrip("/")
        return (f"SOURCE {settings.password.reveal()} /{settings.mountpoint}\r\n"
                f"Source-Agent: {agent}\r\n\r\n").encode("ascii")

    def _scrub(self, text):
        return _printable(self.settings.password.scrub(text))

    async def _read_reply(self, reader):
        data = b""
        while b"\n" not in data and len(data) < MAX_REPLY:
            if _accepted(data):
                break
            chunk = await reader.read(MAX_REPLY - len(data))
            if not chunk:
                break
            data += chunk
        if not _accepted(data):
            # Not accepted: read a little more, to see whether it is a ban page.
            with _suppress_timeout():
                while len(data) < MAX_REPLY:
                    chunk = await asyncio.wait_for(reader.read(MAX_REPLY - len(data)), 2)
                    if not chunk:
                        break
                    data += chunk
        return data.decode("latin-1")

    async def _session(self):
        settings = self.settings
        if Uploader._open:
            # Cannot happen with one task; refuse rather than open a second connection.
            raise RuntimeError("an upload connection is already open")
        self._last_attempt = self._clock()
        self.attempts_today += 1
        self._connected_at = None
        self._set("connecting", f"{settings.host}:{settings.port}")
        self._save()  # the attempt counts even if the app stops during it
        writer = None
        kind, reason = "failed", ""
        try:
            Uploader._open += 1
            try:
                reader, writer = await asyncio.wait_for(
                    self._connect(settings.host, settings.port), CONNECT_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise ConnectionError("no answer to the connection request") from None
            self._writer = writer
            writer.write(self._request())
            try:
                reply = await asyncio.wait_for(self._read_reply(reader), REPLY_TIMEOUT_S)
            except asyncio.TimeoutError:
                reply = ""
                kind, reason = "failed", "No reply to the SOURCE request."
            else:
                kind = classify(reply)
            self.last_reply = self._scrub(reply)
            if kind != "ok":
                if not reason:
                    reason = {"refused": "Refused: ", "banned": "Looks like a ban: ",
                              "failed": "Unexpected reply: "}[kind] + (
                                  self.last_reply or "(none)")
                if kind == "refused":
                    reason += (" Check upload_mountpoint and upload_password, and that no "
                               "other source uses this mountpoint.")
                self.last_error = reason
                self.events.write("upload_error", host=settings.host, port=settings.port,
                                  mountpoint=settings.mountpoint, kind=kind,
                                  reply=self.last_reply)
                LOG.warning("upload: %s", reason)
                return
            self.connections += 1
            self._connected_at = self._clock()
            self.alert = False
            self.last_error = ""
            self.refusals = self.bans = 0
            self.events.write("upload_connect", host=settings.host, port=settings.port,
                              mountpoint=settings.mountpoint)
            LOG.info("upload: connected to %s:%s/%s", settings.host, settings.port,
                     settings.mountpoint)
            kind, reason = await self._stream(reader, writer)
            self.last_error = reason if kind != "quiet" else ""
            self.events.write("upload_disconnect", reason=reason, kind=kind,
                              bytes_sent=self.bytes_sent, frames_skipped=self.frames_skipped,
                              duration_s=round(self._clock() - self._connected_at, 1))
            LOG.info("upload: disconnected: %s", reason)
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionError, EOFError, UnicodeError) as error:
            kind = "failed"
            reason = f"Connection failed: {self._scrub(str(error) or type(error).__name__)}"
            self.last_error = reason
            self.events.write("upload_error", host=settings.host, port=settings.port,
                              mountpoint=settings.mountpoint, kind=kind, reply="",
                              error=reason)
            LOG.warning("upload: %s", reason)
        finally:
            self._writer = None
            if writer is not None:
                writer.close()
                with _suppress_close():
                    await asyncio.wait_for(writer.wait_closed(), 5)
            Uploader._open -= 1
            if self.state != "off":
                self._schedule(kind, reason)
                self._set("waiting", reason)
            self._connected_at = None
            self._save()

    async def _discard(self, reader):
        """Read and drop whatever the caster sends, until it closes."""
        while await reader.read(4096):
            pass

    def _on_frame(self, frame, writer, output):
        if self._guard or writer.is_closing() or not output.accept(frame):
            return
        if _buffered(writer) > STALL_BYTES:
            self.frames_skipped += 1
            return
        writer.write(frame)
        size = len(frame)
        self.bytes_sent += size
        self.total_bytes += size
        self.frames_sent += 1
        self._roll_day()
        self.bytes_today += size
        now = self._clock()
        self._recent.append((now, size))
        self._recent_bytes += size
        while self._recent and now - self._recent[0][0] > RATE_WINDOW_S:
            self._recent_bytes -= self._recent.popleft()[1]
        if self._recent_bytes * 8 > RATE_LIMIT_BITS * RATE_WINDOW_S:
            self._guard = "rate"
        elif self.bytes_today >= DAY_LIMIT_BYTES:
            self._guard = "day"

    async def _stream(self, reader, writer):
        output = OutputFilter(self._clock)
        self.bytes_sent = self.frames_sent = self.frames_skipped = 0
        self._recent.clear()
        self._recent_bytes = 0
        self._stall_since = None
        self._set("connected")
        saved_at, saved_bytes = self._clock(), self.bytes_today
        subscriber = self.hub.subscribe(
            "upload", lambda frame, _number: self._on_frame(frame, writer, output))
        drain = asyncio.create_task(self._discard(reader))
        drain.add_done_callback(_retrieve)
        try:
            while True:
                await asyncio.wait({drain}, timeout=self._poll_s)
                now = self._clock()
                if drain.done():
                    error = None if drain.cancelled() else drain.exception()
                    text = f": {self._scrub(str(error))}" if error else ""
                    return "failed", f"The caster closed the connection{text}."
                if ((now - saved_at >= SAVE_EVERY_S
                     or abs(self.bytes_today - saved_bytes) >= SAVE_EVERY_BYTES)
                        and (self._saving is None or self._saving.done())):
                    saved_at, saved_bytes = now, self.bytes_today
                    self._save()   # so a crash or power cut keeps the day's bytes
                if self._guard == "rate":
                    self._guard = ""
                    return "guard", (f"Stopped: over {RATE_LIMIT_BITS // 1000} kbit/s for a "
                                     "minute, more than a base should send.")
                if self._guard == "day":
                    return "day", (f"Stopped: {self.bytes_today // 1_000_000} MB uploaded "
                                   "today. Resumes after midnight UTC.")
                if not self.hub.flowing(QUIET_S):
                    return "quiet", (f"No RTCM from the receiver for {QUIET_S} s; "
                                     "disconnected until it returns.")
                if (self.frames_sent + self.frames_skipped == 0
                        and now - self._connected_at > FIRST_BYTE_S):
                    return "silent", (f"No RTCM to send within {FIRST_BYTE_S} s of the "
                                      "caster's OK; closed rather than stay silent.")
                if _buffered(writer) > STALL_BYTES:
                    if self._stall_since is None:
                        self._stall_since = now
                    elif now - self._stall_since > STALL_S:
                        return "failed", "The upload stalled (nothing sent for a minute)."
                else:
                    self._stall_since = None
        finally:
            self.hub.unsubscribe(subscriber)
            drain.cancel()

    # ---- the page ----

    def snapshot(self):
        settings = self.settings
        now = self._clock()
        retry = None
        allowed = self._allowed_at()
        if self.state == "waiting" and allowed is not None:
            retry = max(0, round(allowed - now))
        connected = self.state == "connected" and self._connected_at is not None
        return {"enabled": settings.enabled, "host": settings.host, "port": settings.port,
                "mountpoint": settings.mountpoint, "style": settings.style,
                "password_set": bool(settings.password), "state": self.state,
                "reason": self.reason, "alert": self.alert, "retry_in_s": retry,
                "connected_s": round(now - self._connected_at) if connected else None,
                "bytes_sent": self.bytes_sent, "frames_sent": self.frames_sent,
                "frames_skipped": self.frames_skipped, "total_bytes": self.total_bytes,
                "bytes_today": self.bytes_today, "attempts_today": self.attempts_today,
                "connections": self.connections, "last_error": self.last_error,
                "last_reply": self.last_reply, "refusals": self.refusals, "bans": self.bans,
                "stopped": self.stopped, "state_error": self.state_error}


def _write_state(path, text):
    """Replace the state file in one step (a crash leaves the old or the new one)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text)
    partial.replace(path)


def _retrieve(task):
    """Mark a finished task's exception as seen; the stream loop reports it."""
    if not task.cancelled():
        task.exception()


def _buffered(writer):
    transport = writer.transport
    return transport.get_write_buffer_size() if transport is not None else 0


class _suppress_timeout:
    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _tb):
        return kind is asyncio.TimeoutError


class _suppress_close:
    """Ignore errors while closing; cancellation still passes."""

    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _tb):
        return kind is not None and issubclass(kind, Exception)
