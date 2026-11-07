"""An NTRIP client for one single-station mountpoint: the app's own connection to the
CORS caster for the reference check, so that what the caster answers is seen and shown
(rtkrcv's built-in client says nothing on a refused login).

One attempt is: TCP connect (CONNECT_TIMEOUT_S); when the mountpoint has not been
checked yet, or the last attempt failed, `GET /` first for the sourcetable, to tell
whether the mountpoint exists and what its STR record says (format, systems, whether it
wants a GGA position: a network stream, which this client cannot feed, is refused);
then `GET /<mountpoint>` with Basic login, `Ntrip-Version: Ntrip/2.0` and HTTP/1.0 (so a
version 2 caster answers without chunked encoding; chunks are decoded anyway if one
sends them). The status line decides: `ICY 200 OK` or `HTTP/1.x 200` streams,
`HTTP/1.x 401` is "login refused", `404` or a sourcetable in reply is "mountpoint not
found", anything else is "failed" with the line. A mountpoint missing from the
sourcetable is still asked for (casters hide private mountpoints from the table; the
status line is what counts), with the table's verdict added to a refusal. Streaming ends when the caster closes
or when no byte comes for STALE_S ("no data").

Exactly one connection at a time: a CORS login often allows one, and a second would
get the first refused. The attempts run one after the other in one task, the socket is
closed and awaited before the next is scheduled, and a class-level count refuses a second
open connection outright. Between attempts: FIRST_RETRY_S, doubling to MAX_RETRY_S,
reset after a connection that streamed for STABLE_S; REFUSED_WAIT_S after a refused
login or a mountpoint that is missing or unsuitable, so a wrong password does not hammer
the caster (and, with a one-login account, does not lock out a fixed password either).

The password is used in the Authorization header only. It is a Secret: scrubbed from
every text that could carry it (the caster's replies, error messages), and never in
the snapshot, the log or rtkrcv's config. This module never imports the command,
control or serial modules (a unit test checks).

States (`state`), with `reason` for the card: off, not configured, connecting,
sourcetable, connected, no data, login refused, mountpoint not found, unsuitable
mountpoint, backoff (a failure of another kind; `retry_in_s` says when the next try is,
for every waiting state). An unexpected exception inside an attempt is logged and
retried after INTERNAL_ERROR_WAIT_S, with the error as the reason: the client never
stops on its own while the app runs.
"""
import asyncio
import base64
import logging
import math
import random
import re
import time
import traceback
from collections import deque
from datetime import datetime, timezone

from .upload import _printable, _suppress_close

LOG = logging.getLogger("gnss_base")

CONNECT_TIMEOUT_S = 15.0
REPLY_TIMEOUT_S = 10.0          # the status line and headers
SOURCETABLE_TIMEOUT_S = 20.0    # the whole table
STALE_S = 30.0                  # no byte for this long: reconnect
FIRST_RETRY_S = 10.0
MAX_RETRY_S = 600.0
STABLE_S = 60.0                 # streamed this long: the delay starts over
REFUSED_WAIT_S = 300.0          # after 401, a missing or an unsuitable mountpoint
INTERNAL_ERROR_WAIT_S = 60.0    # after an unexpected exception in an attempt
JITTER = 0.1
MAX_HEAD = 4096                 # status line and headers
MAX_SOURCETABLE = 2_000_000
MAX_CHUNK_LINE = 64
READ_SIZE = 8192
RATE_WINDOW_S = 10.0
WAITING_STATES = ("login refused", "mountpoint not found", "unsuitable mountpoint",
                  "no data", "backoff")
STR_FIELDS = ("mountpoint", "identifier", "format", "format_details", "carrier",
              "nav_system", "network", "country", "latitude", "longitude", "nmea",
              "solution", "generator", "compression", "authentication", "fee", "bitrate",
              "misc")
_STATUS = re.compile(r"HTTP/\d(?:\.\d)?\s+(\d{3})\b")
_REFUSED = re.compile(r"\b401\b|unauthori[sz]ed|bad password", re.IGNORECASE)


def parse_sourcetable(text):
    """The STR records of a sourcetable body as dicts keyed by STR_FIELDS (fields past
    the 18th are dropped; missing ones are "")."""
    records = []
    for line in text.splitlines():
        if not line.startswith("STR;"):
            continue
        parts = line.rstrip("\r").split(";")[1:]
        parts += [""] * (len(STR_FIELDS) - len(parts))
        records.append(dict(zip(STR_FIELDS, parts)))
    return records


_RTCM3 = re.compile(r"rtcm\s*3", re.IGNORECASE)


def unsuitable(record):
    """Why a mountpoint's STR record says this client cannot use it, or "": a stream
    that wants a GGA position (nmea 1) or is a network solution (solution 1) is not a
    single station, and a format that is not RTCM 3 is nothing rtkrcv's rtcm3 input
    can read (an empty format field is let through: the stream will tell)."""
    if record.get("nmea") == "1":
        return ("the mountpoint wants a GGA position from the client (nmea 1): a network "
                "or VRS stream, not a single station")
    if record.get("solution") == "1":
        return "the mountpoint is a network solution (solution 1), not a single station"
    fmt = record.get("format", "")
    if fmt and not _RTCM3.search(fmt):
        return f"the mountpoint's format is {fmt[:40]!r}, not RTCM 3"
    return ""


def classify_reply(head):
    """("ok" | "refused" | "not found" | "failed", the status line) for a caster's
    reply to a stream request."""
    line = head.lstrip().split("\n", 1)[0].strip()
    if line.startswith("ICY 200 OK"):
        return "ok", line
    if line.startswith("SOURCETABLE 200 OK"):
        return "not found", line
    status = _STATUS.match(line)
    code = status.group(1) if status else ""
    if code == "200":
        return "ok", line
    if code == "401" or _REFUSED.search(line):   # also "ICY 401", "ERROR - Bad Password"
        return "refused", line
    if code == "404":
        return "not found", line
    return "failed", line


class ChunkDecoder:
    """HTTP chunked transfer encoding, fed as the bytes come; `feed` returns the data."""

    def __init__(self):
        self._pending = b""
        self._left = 0          # bytes left in the current chunk
        self._skip = 0          # bytes of the chunk's trailing CRLF still to drop
        self.ended = False
        self.errors = 0

    def feed(self, data):
        self._pending += data
        out = []
        while self._pending and not self.ended:
            if self._skip:          # the CRLF after a chunk, possibly split across reads
                drop = min(self._skip, len(self._pending))
                self._pending = self._pending[drop:]
                self._skip -= drop
                continue
            if self._left:
                take = self._pending[:self._left]
                out.append(take)
                self._pending = self._pending[len(take):]
                self._left -= len(take)
                if not self._left:
                    self._skip = 2
                continue
            end = self._pending.find(b"\r\n")
            if end < 0:
                if len(self._pending) > MAX_CHUNK_LINE:
                    self.errors += 1
                    self.ended = True
                break
            size = self._pending[:end].split(b";", 1)[0].strip()
            self._pending = self._pending[end + 2:]
            try:
                self._left = int(size, 16)
            except ValueError:
                self.errors += 1
                self.ended = True
                break
            if self._left == 0:
                self.ended = True
        return b"".join(out)


class NtripClient:
    """See the module docstring. `settings` has host, port, mountpoint, user and
    password (a Secret); `on_data(bytes)` gets the stream as received. `clock`
    (monotonic), `wall`, `sleep`, `connect` and `rand` are for tests."""

    _open = 0   # stream connections open in this process, across instances

    def __init__(self, settings, on_data, version="", clock=time.monotonic, wall=time.time,
                 sleep=asyncio.sleep, connect=asyncio.open_connection, rand=random.random,
                 stale_s=STALE_S, check_sourcetable=True, on_connect=None):
        self.settings = settings
        self._on_data = on_data
        self._on_connect = on_connect   # on_connect(): a stream has just been accepted
        self.version = version
        self._clock, self._wall, self._sleep = clock, wall, sleep
        self._connect, self._rand = connect, rand
        self.stale_s = stale_s
        self.check_sourcetable = check_sourcetable
        self._task = None
        self.state = "off"
        self.reason = ""
        self.last_reply = ""        # the caster's last status line, scrubbed
        self.sourcetable = None     # the mountpoint's STR record, {} if the caster has
                                    # no table, None until looked at
        self.sourcetable_utc = None
        self.sourcetable_size = 0   # STR records in the caster's table
        self.listed = None          # whether the mountpoint is in the table (None: unknown)
        self.started = False
        self._backoff = FIRST_RETRY_S
        self._next_attempt = None
        self._last_kind = ""
        self._connected_at = None
        self._last_byte = None
        self.bytes = 0              # this connection
        self.total_bytes = 0
        self.connections = 0
        self.attempts_today = 0
        self._day = None
        self._recent = deque()      # (clock, bytes) within RATE_WINDOW_S
        self._recent_bytes = 0
        self.data_errors = 0        # on_data raised
        self.internal_errors = 0
        self.died = False           # the task ended on its own: cannot happen, but shown
        self.chunked = False
        self._strip_crlf = False    # an ICY reply's optional blank line, if it comes late

    # ---- life cycle ----

    def start(self):
        if self._task is None:
            self.started, self.died = True, False
            self._task = asyncio.get_running_loop().create_task(self._guarded())
        return self._task

    async def stop(self):
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:   # the type only: never an unscrubbed message
                LOG.error("NTRIP client stopped with an error: %s", type(error).__name__)
        self.started = False
        self._set("off", "")

    async def _guarded(self):
        """Run the schedule; an unexpected error (a bug) is logged and the attempts go
        on after a wait, so the card never sits silent on a stopped client. Should even
        that fail, `died` says so (the check shows "client stopped")."""
        try:
            while True:
                try:
                    await self._run()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.internal_errors += 1
                    reason = self._scrub(f"Internal error: {type(error).__name__}: {error}")
                    # The traceback is scrubbed too: no exception text is logged as is.
                    LOG.error("NTRIP client: %s; trying again in %.0f s\n%s", reason,
                              INTERNAL_ERROR_WAIT_S, self._scrub_lines(traceback.format_exc()))
                    self._connected_at = None
                    self._next_attempt = self._clock() + INTERNAL_ERROR_WAIT_S
                    self._set("backoff", reason)
                    await self._sleep(INTERNAL_ERROR_WAIT_S)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # The guard itself failed (the scrubber, say). Only the type is logged, and
            # the task ends without re-raising, so nothing logs the exception chain
            # (with the original message in it) unscrubbed later.
            self.died = True
            LOG.error("NTRIP client stopped: %s (restart the app)", type(error).__name__)

    def _scrub_lines(self, text):
        return "\n".join(self._scrub(line) for line in text.splitlines())

    def _set(self, state, reason=""):
        self.state, self.reason = state, reason

    def _scrub(self, text):
        """`text` without the password, in clear or as the request's base64 token."""
        text = self.settings.password.scrub(text)
        token = self._token()
        if token:
            text = text.replace(token, "***")
        return _printable(text)

    def _token(self):
        settings = self.settings
        if not settings.password:
            return ""
        try:
            text = f"{settings.user}:{settings.password.reveal()}".encode("utf-8")
        except UnicodeEncodeError:   # a lone surrogate from the options file: no token
            return ""
        return base64.b64encode(text).decode("ascii")

    async def _run(self):
        while True:
            self._roll_day()
            now = self._clock()
            if self._next_attempt is not None and now < self._next_attempt:
                await self._sleep(self._next_attempt - now)
                continue
            await self._attempt()

    def _roll_day(self):
        day = datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self.attempts_today = day, 0

    def retry_in_s(self):
        if self._next_attempt is None or self._connected_at is not None:
            return None
        return max(0.0, self._next_attempt - self._clock())

    def _schedule(self, kind, reason):
        """The next attempt after an attempt ended with `kind`."""
        now = self._clock()
        self._last_kind = kind
        if kind in ("refused", "not found", "unsuitable"):
            delay = REFUSED_WAIT_S
        else:
            streamed = 0.0 if self._connected_at is None else now - self._connected_at
            if streamed >= STABLE_S or not math.isfinite(self._backoff):
                self._backoff = FIRST_RETRY_S
            delay = self._backoff * (1 + JITTER * self._rand())
            self._backoff = min(self._backoff * 2, MAX_RETRY_S)
        self._next_attempt = now + delay
        state = {"refused": "login refused", "not found": "mountpoint not found",
                 "unsuitable": "unsuitable mountpoint", "no data": "no data"}.get(kind, "backoff")
        self._set(state, reason)

    # ---- one attempt ----

    def _headers(self, path, login):
        settings = self.settings
        lines = [f"GET {path} HTTP/1.0", f"Host: {settings.host}",
                 "Ntrip-Version: Ntrip/2.0",
                 f"User-Agent: NTRIP gnss_base/{self.version}".rstrip("/"),
                 "Connection: close"]
        if login:
            lines.append(f"Authorization: Basic {self._token()}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    async def _open_connection(self):
        settings = self.settings
        try:
            return await asyncio.wait_for(self._connect(settings.host, settings.port),
                                          CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise ConnectionError(f"no answer from {settings.host}:{settings.port} within "
                                  f"{CONNECT_TIMEOUT_S:.0f} s") from None

    async def _attempt(self):
        self.attempts_today += 1
        kind = reason = None    # set by every handled outcome; an unhandled exception
        try:                    # leaves them None and the schedule to _guarded
            if self.check_sourcetable and (
                    self.sourcetable is None
                    or self._last_kind in ("refused", "not found", "unsuitable", "failed")):
                why = await self._check_sourcetable()
                if why:
                    kind, reason = why
                    return
            kind, reason = await self._stream()
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionError, EOFError, UnicodeError) as error:
            kind = "failed"
            reason = f"Connection failed: {self._scrub(str(error) or type(error).__name__)}"
            LOG.warning("NTRIP client: %s", reason)
        finally:
            if kind is not None and self.state != "off":
                self._schedule(kind, reason)   # looks at how long the stream lasted
                LOG.info("NTRIP client: %s; next try in %.0f s", reason,
                         self._next_attempt - self._clock())
            self._connected_at = None
        return None

    async def _check_sourcetable(self):
        """Fetch the sourcetable and look for the mountpoint. Returns (kind, reason) to
        stop this attempt (the mountpoint is listed as unsuitable), or None to go on to
        the stream request: the table is advisory, so a caster that gives none at "/",
        answers something else, or does not finish it in time, is still asked for the
        stream (the status line decides)."""
        settings = self.settings
        self._set("sourcetable", f"asking {settings.host}:{settings.port} for its sourcetable")
        reader, writer = await self._open_connection()
        try:
            writer.write(self._headers("/", login=False))
            await writer.drain()
            data = await asyncio.wait_for(self._read_sourcetable(reader), SOURCETABLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            data = b""
            LOG.warning("NTRIP client: no complete sourcetable within %.0f s; asking for the "
                        "stream anyway", SOURCETABLE_TIMEOUT_S)
        finally:
            await self._close(writer)
        head, body = self._split_reply(data)
        line = head.split("\n", 1)[0].strip()
        self.last_reply = self._scrub(line)
        if not line.startswith("SOURCETABLE 200 OK") and "STR;" not in body:
            # No table at "/" (or no answer to that): the stream request will tell.
            self.sourcetable, self.listed, self.sourcetable_size = {}, None, 0
            self.sourcetable_utc = self._utc_now()
            return None
        # Caster text: scrubbed of the password before it is kept, like every reply.
        records = [{k: self._scrub(v) for k, v in r.items()} for r in parse_sourcetable(body)]
        self.sourcetable_size = len(records)
        self.sourcetable_utc = self._utc_now()
        found = [r for r in records if r["mountpoint"] == settings.mountpoint]
        self.listed = bool(found)
        if not found:
            # Not listed: still asked for, since casters hide private mountpoints from
            # the table; a 404 then says so with the table's count (see _stream).
            self.sourcetable = {}
            return None
        self.sourcetable = found[0]
        why = unsuitable(found[0])
        if why:
            return "unsuitable", f"Not used: {why}. Pick a single-station mountpoint."
        return None

    async def _read_sourcetable(self, reader):
        """The whole reply to `GET /`, up to ENDSOURCETABLE, the end of the connection
        or MAX_SOURCETABLE; a reply that is neither a sourcetable nor HTTP is cut at
        MAX_HEAD, enough to show its first line."""
        data = b""
        while len(data) < MAX_SOURCETABLE:
            chunk = await reader.read(READ_SIZE)
            if not chunk:
                break
            data += chunk
            if b"ENDSOURCETABLE" in data or self._reply_complete(data):
                break
            if (len(data) >= MAX_HEAD
                    and not data.lstrip().startswith((b"SOURCETABLE", b"HTTP/"))):
                break   # not a table: enough to classify
        return data

    @classmethod
    def _reply_complete(cls, data):
        """Whether an HTTP-style reply is all there (a caster may hold the socket
        open): the headers are complete and either the status is not 200 or the body
        has reached its Content-Length."""
        end = data.find(b"\r\n\r\n")
        if end < 0:
            return False
        head = data[:end].decode("latin-1")
        line = head.split("\n", 1)[0].strip()
        status = _STATUS.match(line)
        if status and status.group(1) != "200":
            return True
        if "chunked" in cls._header(head, "transfer-encoding").lower():
            return data.endswith(b"0\r\n\r\n")   # the last chunk
        length = cls._header(head, "content-length")
        return length.isdigit() and len(data) - end - 4 >= int(length)

    @classmethod
    def _split_reply(cls, data):
        """(head text, body text) of a sourcetable reply: the status line and headers,
        then the table, decoded if the headers say chunked (an NTRIP 2 caster answering
        a version 2 request that way)."""
        end = data.find(b"\r\n\r\n")
        if end < 0:
            text = data.decode("latin-1")
            # An NTRIP 1 caster may put no blank line between the status line and
            # the records: the table is then the whole text.
            return text, text if text.startswith("SOURCETABLE 200 OK") else ""
        head = data[:end].decode("latin-1")
        body = data[end + 4:]
        if "chunked" in cls._header(head, "transfer-encoding").lower():
            body = ChunkDecoder().feed(body)
        return head, body.decode("latin-1")

    async def _read_head(self, reader):
        """The status line and headers: up to the blank line, or for an `ICY` reply the
        first line (NTRIP 1 casters send no headers). Returns (head text, bytes after,
        complete): after REPLY_TIMEOUT_S, an EOF or MAX_HEAD without the end of the
        head, what came is returned with `complete` False, so a refusal with no line
        ending ("ERROR - Bad Password" and silence) can still be read as one."""
        data = b""
        deadline = asyncio.get_running_loop().time() + REPLY_TIMEOUT_S
        while len(data) < MAX_HEAD:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(READ_SIZE), left)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            data += chunk
            if data.lstrip().startswith(b"ICY "):
                end = data.find(b"\r\n")
                if end >= 0:
                    rest = data[end + 2:]
                    if rest.startswith(b"\r\n"):   # a caster that adds a blank line
                        rest = rest[2:]
                    return data[:end].decode("latin-1"), rest, True
                continue
            end = data.find(b"\r\n\r\n")
            if end >= 0:
                return data[:end].decode("latin-1"), data[end + 4:], True
            if b"\n" in data and not data.lstrip().startswith((b"HTTP/", b"SOURCETABLE")):
                return data.decode("latin-1"), b"", True   # not HTTP: one line is all
        return data.decode("latin-1"), b"", False

    async def _stream(self):
        settings = self.settings
        if NtripClient._open:
            raise RuntimeError("an NTRIP stream connection is already open")
        self._set("connecting", f"{settings.host}:{settings.port}/{settings.mountpoint}")
        reader, writer = await self._open_connection()
        NtripClient._open += 1
        try:
            writer.write(self._headers(f"/{settings.mountpoint}", login=True))
            await writer.drain()
            head, rest, complete = await self._read_head(reader)
            if not head.strip():
                return "failed", ("No reply to the stream request (the caster closed, or "
                                  "said nothing within the time allowed).")
            if not complete and head.lstrip().startswith(("HTTP/", "SOURCETABLE")):
                # Headers that never ended: not a stream to read bytes from.
                self.last_reply = self._scrub(head.split("\n", 1)[0].strip())
                return "failed", f"Incomplete reply to the stream request: {self.last_reply}"
            kind, line = classify_reply(head)
            if kind == "ok" and "sourcetable" in self._header(head, "content-type").lower():
                kind = "not found"    # an NTRIP 2 caster's table for an unknown mountpoint
                line += " (a sourcetable, not the stream)"
            self.last_reply = self._scrub(line)
            if kind != "ok":
                listed = ("" if self.listed is None else
                          f"; {'listed' if self.listed else 'not listed'} in the caster's "
                          f"sourcetable of {self.sourcetable_size} mountpoints")
                reason = {"refused": f"Login refused by the caster ({self.last_reply}): check "
                                     "reference_user and reference_password; or the caster "
                                     "still counts this login's previous connection (one at "
                                     f"a time), in which case the try in {REFUSED_WAIT_S / 60:.0f} "
                                     "min will do.",
                          "not found": f"Mountpoint not found ({self.last_reply}{listed}): "
                                       "check reference_mountpoint (case matters).",
                          "failed": f"Unexpected reply: {self.last_reply or '(none)'}"}[kind]
                LOG.warning("NTRIP client: %s", reason)
                return kind, reason
            self.chunked = "chunked" in self._header(head, "transfer-encoding").lower()
            decoder = ChunkDecoder() if self.chunked else None
            # An NTRIP 1 caster may add a blank line after "ICY 200 OK"; if it did not
            # arrive with the status line, drop it from the first data read.
            self._strip_crlf = head.startswith("ICY ") and not rest
            self.connections += 1
            self._connected_at = self._clock()
            self._last_byte = self._connected_at
            self.bytes = 0
            self._recent.clear()
            self._recent_bytes = 0
            self._set("connected", "")
            LOG.info("NTRIP client: connected to %s:%s/%s (%s)", settings.host, settings.port,
                     settings.mountpoint, self.last_reply)
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception:
                    self.data_errors += 1
                    LOG.exception("NTRIP client: the connect callback failed")
            if rest:
                self._data(rest, decoder)
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(READ_SIZE), self.stale_s)
                except asyncio.TimeoutError:
                    return "no data", f"No data from the caster for {self.stale_s:.0f} s."
                if not chunk:
                    return "closed", "The caster closed the connection."
                self._data(chunk, decoder)
                if decoder is not None and decoder.ended:
                    return "closed", "The caster ended the stream."
        finally:
            try:
                await self._close(writer)
            finally:   # even if the close is cancelled (stop): the guard must not stick
                NtripClient._open -= 1

    @staticmethod
    def _header(head, name):
        for line in head.split("\n")[1:]:
            key, colon, value = line.partition(":")
            if colon and key.strip().lower() == name:
                return value.strip()
        return ""

    def _data(self, chunk, decoder):
        now = self._clock()
        self._last_byte = now
        if self._strip_crlf:
            self._strip_crlf = False
            if chunk.startswith(b"\r\n"):
                chunk = chunk[2:]
        if decoder is not None:
            chunk = decoder.feed(chunk)
        if not chunk:
            return
        size = len(chunk)
        self.bytes += size
        self.total_bytes += size
        self._recent.append((now, size))
        self._recent_bytes += size
        while self._recent and now - self._recent[0][0] > RATE_WINDOW_S:
            self._recent_bytes -= self._recent.popleft()[1]
        try:
            self._on_data(chunk)
        except Exception:
            self.data_errors += 1
            if self.data_errors == 1:
                LOG.exception("NTRIP client: handling the stream failed")

    @staticmethod
    async def _close(writer):
        writer.close()
        with _suppress_close():
            await asyncio.wait_for(writer.wait_closed(), 5)

    def _utc_now(self):
        return datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds")

    # ---- the page ----

    def bytes_per_s(self):
        if self._connected_at is None:
            return None
        window = min(RATE_WINDOW_S, max(self._clock() - self._connected_at, 1.0))
        return self._recent_bytes / window

    def snapshot(self):
        now = self._clock()
        settings = self.settings
        retry = self.retry_in_s()
        return {
            "state": self.state, "reason": self.reason or None,
            "host": settings.host or None, "port": settings.port,
            "mountpoint": settings.mountpoint or None,
            "user_set": bool(settings.user), "password_set": bool(settings.password),
            "connected_s": None if self._connected_at is None
            else round(now - self._connected_at, 1),
            "bytes": self.bytes, "total_bytes": self.total_bytes,
            "bytes_per_s": None if self.bytes_per_s() is None else round(self.bytes_per_s(), 1),
            "last_byte_age_s": None if self._last_byte is None or self._connected_at is None
            else round(now - self._last_byte, 1),
            "connections": self.connections, "attempts_today": self.attempts_today,
            "retry_in_s": None if retry is None else round(retry),
            "last_reply": self.last_reply or None,
            "chunked": self.chunked,
            "data_errors": self.data_errors,
            "internal_errors": self.internal_errors,
            "listed": self.listed,
            "sourcetable": None if self.sourcetable is None else dict(self.sourcetable),
            "sourcetable_utc": self.sourcetable_utc,
            "sourcetable_size": self.sourcetable_size,
        }
