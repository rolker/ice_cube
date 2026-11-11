"""An NTRIP client for one single-station mountpoint: the app's own connection to the
CORS caster for a reference check, so that what the caster answers is seen and shown
(rtkrcv's built-in client says nothing on a refused login).

One attempt is: TCP connect (CONNECT_TIMEOUT_S); when the mountpoint has not been
checked yet, the last attempt failed, or the table is a day old (TABLE_REFRESH_S), `GET
/` first for the sourcetable, with the login when a user is set (some casters list more
mountpoints to a logged-in client) and again without it if that gets no table; the
table tells whether the mountpoint exists and what its STR record says (format,
systems, whether it wants a GGA position: see `unsuitable`), and its STR, CAS and NET
records are kept for the card's list of the caster's mountpoints; then `GET
/<mountpoint>` with Basic login, `Ntrip-Version: Ntrip/2.0` and HTTP/1.0 (so a
version 2 caster answers without chunked encoding; chunks are decoded anyway if one
sends them). The status line decides: `ICY 200 OK` or `HTTP/1.x 200` streams,
`HTTP/1.x 401` is "login refused", `404` or a sourcetable in reply is "mountpoint not
found", anything else is "failed" with the line. A mountpoint missing from the
sourcetable is still asked for (casters hide private mountpoints from the table; the
status line is what counts), with the table's verdict added to a refusal. Streaming ends when the caster closes
or when no byte comes for STALE_S ("no data").

GGA uplink (`send_gga` in the settings, and a `gga_position()` that returns (lat, lon,
height) or None): an NMEA GGA sentence (`build_gga`) goes with the stream request twice
over, as the NTRIP 2 header `Ntrip-GGA: $GPGGA,...*hh` (the whole sentence, for a
caster that reads the request as strict HTTP and picks the station when it arrives) and
as raw bytes right after the request, NTRIP 1 style; then raw every GGA_INTERVAL_S
while the stream is up, so a "nearest station" (nmea 1) mountpoint can pick a station.
It holds only that position (our base's own, or one set in the options), quality 1, the
time from the system clock; it goes to this caster only, and is never logged, neither
as a sentence nor as the header (the log counts GGA sent; `_scrub` also replaces any
GGA sentence in a caster's reply). Without a position there is no attempt ("no
position") until there is one.

After a refused login (401) on the stream request, the next attempt fetches the
sourcetable without the login: with a one-connection login that the caster frees with
a delay, the logged-in `GET /` could itself keep the login busy and get the stream
request refused again. The table from the last logged-in fetch is kept for the card's
list of mountpoints (and searched too, as it may list more), and the next table after a
stream that was not refused is fetched with the login again.

Exactly one connection per login at a time: a CORS login often allows one, and a second
would get the first refused. The attempts run one after the other in one task, the
socket is closed and awaited before the next is scheduled, and a class-level count per
caster and user (two reference checks may use two casters at once) refuses a second
open connection with the same login outright. Between attempts: FIRST_RETRY_S, doubling to MAX_RETRY_S,
reset after a connection that streamed for STABLE_S; REFUSED_WAIT_S after a refused
login or a mountpoint that is missing or unsuitable, so a wrong password does not hammer
the caster (and, with a one-login account, does not lock out a fixed password either).

The password is used in the Authorization header only. It is a Secret: scrubbed from
every text that could carry it (the caster's replies, error messages), and never in
the snapshot, the log or rtkrcv's config. This module never imports the command,
control or serial modules (a unit test checks).

States (`state`), with `reason` for the card: off, not configured, connecting,
sourcetable, connected, no data, login refused, mountpoint not found, unsuitable
mountpoint, no position (GGA uplink on and no position to send yet), backoff (a failure of another kind; `retry_in_s` says when the next try is,
for every waiting state). An unexpected exception inside an attempt is logged and
retried after INTERNAL_ERROR_WAIT_S, with the error as the reason: the client never
stops on its own while the app runs.
"""
import asyncio
import base64
import contextlib
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
NO_POSITION_WAIT_S = 10.0       # GGA uplink on, no position yet: look again after this
GGA_INTERVAL_S = 10.0           # a GGA on the stream connection this often
GGA_WRITE_TIMEOUT_S = 5.0
TABLE_REFRESH_S = 86400.0       # the sourcetable is fetched again after a day
MAX_LISTED = 500                # STR records kept for the card's list
MAX_OTHER_LINES = 20            # CAS and NET records kept, each cut to MAX_OTHER_CHARS
MAX_OTHER_CHARS = 200
JITTER = 0.1
MAX_HEAD = 4096                 # status line and headers
MAX_SOURCETABLE = 2_000_000
MAX_CHUNK_LINE = 64
READ_SIZE = 8192
RATE_WINDOW_S = 10.0
WAITING_STATES = ("login refused", "mountpoint not found", "unsuitable mountpoint",
                  "no data", "no position", "backoff")
STR_FIELDS = ("mountpoint", "identifier", "format", "format_details", "carrier",
              "nav_system", "network", "country", "latitude", "longitude", "nmea",
              "solution", "generator", "compression", "authentication", "fee", "bitrate",
              "misc")
_STATUS = re.compile(r"HTTP/\d(?:\.\d)?\s+(\d{3})\b")
_GGA_TEXT = re.compile(r"\$?G[A-Z]GGA,[^\s]*")   # a GGA sentence in any text: scrubbed
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


def parse_other_records(text):
    """The CAS and NET records of a sourcetable body, as their lines, at most
    MAX_OTHER_LINES of at most MAX_OTHER_CHARS characters each."""
    lines = [line.rstrip("\r") for line in text.splitlines()
             if line.startswith(("CAS;", "NET;"))]
    return [line[:MAX_OTHER_CHARS] for line in lines[:MAX_OTHER_LINES]]


def nmea_checksum(body):
    """The NMEA checksum of the text between `$` and `*`: the XOR of its bytes, as two
    upper-case hex digits."""
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return f"{value:02X}"


def build_gga(lat, lon, height_m, wall, quality=1, satellites=12, hdop=1.0):
    """An NMEA GGA sentence (bytes, CR LF at the end) for the GGA uplink: the position
    (decimal degrees; the height goes in the altitude field with a geoid separation of
    0, as casters use the horizontal position only), the UTC time of `wall` (Unix
    seconds, the system clock), quality 1 (GPS fix: standard for a caster's "nearest
    station" or VRS choice), a nominal 12 satellites and HDOP 1.0."""
    if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(height_m)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("no position for a GGA")
    moment = datetime.fromtimestamp(wall, timezone.utc)
    hundredths = min(moment.microsecond // 10000, 99)
    stamp = moment.strftime("%H%M%S") + f".{hundredths:02d}"
    lat_deg, lat_min = divmod(abs(lat) * 60, 60)
    lon_deg, lon_min = divmod(abs(lon) * 60, 60)
    # Minutes to 5 decimals (2 cm); rounding up to 60.00000 carries into the degrees.
    lat_min, lon_min = round(lat_min, 5), round(lon_min, 5)
    if lat_min >= 60:
        lat_deg, lat_min = lat_deg + 1, 0.0
    if lon_min >= 60:
        lon_deg, lon_min = lon_deg + 1, 0.0
    body = (f"GPGGA,{stamp},{int(lat_deg):02d}{lat_min:08.5f},{'N' if lat >= 0 else 'S'},"
            f"{int(lon_deg):03d}{lon_min:08.5f},{'E' if lon >= 0 else 'W'},{quality},"
            f"{satellites:02d},{hdop:.1f},{height_m:.3f},M,0.000,M,,")
    return f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")


_RTCM3 = re.compile(r"rtcm\s*3", re.IGNORECASE)


def unsuitable(record, send_gga=False):
    """Why a mountpoint's STR record says this client cannot use it, or "": a network
    solution (solution 1) is not a single station, and a format that is not RTCM 3 is
    nothing rtkrcv's rtcm3 input can read (an empty format field is let through: the
    stream will tell). A stream that wants a GGA position (nmea 1) is refused too,
    unless the GGA uplink is on (`send_gga`): then it is taken for a "nearest station"
    mountpoint that picks a single station by the GGA, which the station's 1005 and the
    card then show."""
    if record.get("nmea") == "1" and not send_gga:
        return ("the mountpoint wants a GGA position from the client (nmea 1): a network "
                "or VRS stream, not a single station (or a nearest-station mountpoint, "
                "usable with the GGA uplink on)")
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
    password (a Secret), and may have `option(field)` (an option's name as the texts
    give it, "references[1].user"; without it `prefix` + "_" + field, "reference_user"
    by default) and `send_gga` (the GGA uplink, off by
    default); `on_data(bytes)` gets the stream as received. `gga_position()` returns
    the GGA's (lat, lon, height) or None. `label` names this client in the log ("NTRIP
    client (label)"). `clock` (monotonic), `wall`, `sleep`, `connect`, `rand`,
    `gga_sleep` and `gga_interval_s` are for tests."""

    _open = {}  # stream connections open in this process, by (host, port, user)

    def __init__(self, settings, on_data, version="", clock=time.monotonic, wall=time.time,
                 sleep=asyncio.sleep, connect=asyncio.open_connection, rand=random.random,
                 stale_s=STALE_S, check_sourcetable=True, on_connect=None, label="",
                 gga_position=None, gga_sleep=asyncio.sleep, gga_interval_s=GGA_INTERVAL_S):
        self.settings = settings
        self._on_data = on_data
        self._on_connect = on_connect   # on_connect(): a stream has just been accepted
        self.name = f"NTRIP client ({label})" if label else "NTRIP client"
        self.prefix = getattr(settings, "prefix", "reference")
        option = getattr(settings, "option", None)
        self._option = option if callable(option) else (lambda f: f"{self.prefix}_{f}")
        self.send_gga = bool(getattr(settings, "send_gga", False))
        self._gga_position = gga_position
        self._gga_sleep = gga_sleep
        self.gga_interval_s = gga_interval_s
        self.gga_sent = 0           # since start
        self.gga_sent_connection = 0
        self.gga_skipped = 0        # due, with no position to send
        self._gga_last = None       # clock of the last GGA sent
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
        self.sourcetable_login = None   # "login", "no login" or "no login (fallback)"
        self.records = []           # the table's STR records (MAX_LISTED), for the card
        self.other_records = []     # its CAS and NET lines
        self._table_clock = None    # when the table was last fetched (clock)
        self.listed = None          # whether the mountpoint is in the table (None: unknown)
        self._login_records = None  # (STR, CAS/NET, size, utc) of the last logged-in table
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
                LOG.error("%s stopped with an error: %s", self.name, type(error).__name__)
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
                    LOG.error("%s: %s; trying again in %.0f s\n%s", self.name, reason,
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
            LOG.error("%s stopped: %s (restart the app)", self.name, type(error).__name__)

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
        text = _GGA_TEXT.sub("GGA ***", text)   # our position, should a caster echo it
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
        elif kind == "no position":
            delay = NO_POSITION_WAIT_S
        else:
            streamed = 0.0 if self._connected_at is None else now - self._connected_at
            if streamed >= STABLE_S or not math.isfinite(self._backoff):
                self._backoff = FIRST_RETRY_S
            delay = self._backoff * (1 + JITTER * self._rand())
            self._backoff = min(self._backoff * 2, MAX_RETRY_S)
        self._next_attempt = now + delay
        state = {"refused": "login refused", "not found": "mountpoint not found",
                 "unsuitable": "unsuitable mountpoint", "no data": "no data",
                 "no position": "no position"}.get(kind, "backoff")
        self._set(state, reason)

    # ---- one attempt ----

    def _headers(self, path, login, gga=None):
        """The request. `gga` (a sentence from build_gga) adds the NTRIP 2 header
        `Ntrip-GGA`, whose value is the whole sentence, `$` to checksum, without CR LF.
        The request is never logged: it holds the login token and the position."""
        settings = self.settings
        lines = [f"GET {path} HTTP/1.0", f"Host: {settings.host}",
                 "Ntrip-Version: Ntrip/2.0",
                 f"User-Agent: NTRIP gnss_base/{self.version}".rstrip("/"),
                 "Connection: close"]
        if gga is not None:
            lines.append("Ntrip-GGA: " + gga.decode("ascii").rstrip("\r\n"))
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
                    or self._last_kind in ("refused", "not found", "unsuitable", "failed")
                    or self._table_clock is None
                    or self._clock() - self._table_clock >= TABLE_REFRESH_S):
                why = await self._check_sourcetable()
                if why:
                    kind, reason = why
                    return
            if self.send_gga and self._gga() is None:
                self.attempts_today -= 1       # a wait, not an attempt on the caster
                kind, reason = "no position", (
                    "Waiting for a position to send in the GGA (our base's RTCM 1005, or "
                    f"{self._option('gga_position')}) before connecting.")
                return
            kind, reason = await self._stream()
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionError, EOFError, UnicodeError) as error:
            kind = "failed"
            reason = f"Connection failed: {self._scrub(str(error) or type(error).__name__)}"
            LOG.warning("%s: %s", self.name, reason)
        finally:
            if kind is not None and self.state != "off":
                repeat = kind == "no position" and self._last_kind == kind
                self._schedule(kind, reason)   # looks at how long the stream lasted
                if repeat:                     # said once, not every 10 s
                    kind = None
            if kind is not None and self.state != "off":
                gga = (f" ({self.gga_sent_connection} GGA sent)"
                       if self.send_gga and self._connected_at is not None else "")
                LOG.info("%s: %s%s; next try in %.0f s", self.name, reason, gga,
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
        self._table_clock = self._clock()
        # After a refused stream request, no login: see the module docstring.
        after_refusal = bool(settings.user) and self._last_kind == "refused"
        login = bool(settings.user) and not after_refusal
        head, body, line = await self._fetch_table(login)
        is_table = line.startswith("SOURCETABLE 200 OK") or "STR;" in body
        self.sourcetable_login = ("login" if login else
                                  "no login (after a refused login)" if after_refusal
                                  else "no login")
        if login and not is_table:
            # The logged-in request got no table (a caster that refuses a login at "/",
            # say): the anonymous one may still get it.
            LOG.info("%s: no sourcetable with the login (%s); asking without it",
                     self.name, self._scrub(line) or "no reply")
            head, body, line = await self._fetch_table(False)
            is_table = line.startswith("SOURCETABLE 200 OK") or "STR;" in body
            self.sourcetable_login = "no login (fallback)"
        self.last_reply = self._scrub(line)
        kept = self._login_records if after_refusal else None
        if not is_table and kept is None:
            # No table at "/" (or no answer to that): the stream request will tell.
            self.sourcetable, self.listed, self.sourcetable_size = {}, None, 0
            self.records, self.other_records = [], []
            self.sourcetable_utc = self._utc_now()
            return None
        # Caster text: scrubbed of the password before it is kept, like every reply.
        records = [{k: self._scrub(v) for k, v in r.items()}
                   for r in parse_sourcetable(body)] if is_table else []
        if is_table:
            LOG.info("%s: the caster's sourcetable (%s) lists %d mountpoints: %s", self.name,
                     self.sourcetable_login, len(records),
                     ", ".join(r["mountpoint"] for r in records[:MAX_LISTED]) or "none")
        if kept is not None:
            # The logged-in table stays on the card; the mountpoint is looked for in
            # the new table first, then in the kept one (which may list more).
            all_kept, self.other_records, self.sourcetable_size, self.sourcetable_utc = kept
            self.records = all_kept[:MAX_LISTED]
            self.sourcetable_login = ("login (kept; the last table was read without it, "
                                      "after a refused login)")
            records = records + all_kept
        else:
            self.sourcetable_size = len(records)
            self.sourcetable_utc = self._utc_now()
            self.records = records[:MAX_LISTED]
            self.other_records = [self._scrub(line) for line in parse_other_records(body)]
            if login and self.sourcetable_login == "login":
                self._login_records = (records, list(self.other_records),
                                       self.sourcetable_size, self.sourcetable_utc)
        found = [r for r in records if r["mountpoint"] == settings.mountpoint]
        self.listed = bool(found)
        if not found:
            # Not listed: still asked for, since casters hide private mountpoints from
            # the table; a 404 then says so with the table's count (see _stream).
            self.sourcetable = {}
            return None
        self.sourcetable = found[0]
        why = unsuitable(found[0], self.send_gga)
        if why:
            return "unsuitable", f"Not used: {why}. Pick a single-station mountpoint."
        return None

    async def _fetch_table(self, login):
        """One `GET /`, with the login or without: (head, body, status line)."""
        reader, writer = await self._open_connection()
        try:
            writer.write(self._headers("/", login=login))
            await writer.drain()
            data = await asyncio.wait_for(self._read_sourcetable(reader), SOURCETABLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            data = b""
            LOG.warning("%s: no complete sourcetable within %.0f s; asking for the "
                        "stream anyway", self.name, SOURCETABLE_TIMEOUT_S)
        finally:
            await self._close(writer)
        head, body = self._split_reply(data)
        return head, body, head.split("\n", 1)[0].strip()

    def accepts(self, record):
        """Whether this client would take a mountpoint with this STR record."""
        return not unsuitable(record, self.send_gga)

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

    def _login_key(self):
        """The key of the one-connection guard, or None without a user: no login, so no
        per-login limit (two anonymous checks on one caster both stream)."""
        settings = self.settings
        if not settings.user:
            return None
        return (settings.host.lower(), settings.port, settings.user)

    def _gga(self):
        """The GGA sentence to send now, or None without a position."""
        if self._gga_position is None:
            return None
        position = self._gga_position()
        if position is None:
            return None
        try:
            return build_gga(*position, self._wall())
        except (ValueError, TypeError, OverflowError):
            return None

    async def _gga_loop(self, writer):
        """A GGA every gga_interval_s while the stream is up (the first went with the
        request). A due GGA without a position is skipped and counted."""
        while True:
            await self._gga_sleep(self.gga_interval_s)
            sentence = self._gga()
            if sentence is None:
                self.gga_skipped += 1
                continue
            writer.write(sentence)
            await asyncio.wait_for(writer.drain(), GGA_WRITE_TIMEOUT_S)
            self._count_gga()

    def _count_gga(self):
        self.gga_sent += 1
        self.gga_sent_connection += 1
        self._gga_last = self._clock()

    async def _stream(self):
        settings = self.settings
        key = self._login_key()
        if key is not None and NtripClient._open.get(key):
            raise RuntimeError("an NTRIP stream connection with this login is already open")
        self._set("connecting", f"{settings.host}:{settings.port}/{settings.mountpoint}")
        reader, writer = await self._open_connection()
        if key is not None:
            NtripClient._open[key] = NtripClient._open.get(key, 0) + 1
        gga_task = None
        self.gga_sent_connection = 0
        try:
            sentence = self._gga() if self.send_gga else None
            # The GGA twice: in the NTRIP 2 header Ntrip-GGA, and raw right after the
            # request, NTRIP 1 style: what a caster expects for an nmea 1 mountpoint
            # before it picks a station or starts sending. Neither is ever logged.
            request = self._headers(f"/{settings.mountpoint}", login=True, gga=sentence)
            writer.write(request + (sentence or b""))
            await writer.drain()
            if sentence is not None:
                self._count_gga()
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
                                     f"{self._option('user')} and {self._option('password')}; or the caster "
                                     "still counts this login's previous connection (one at "
                                     f"a time), in which case the try in {REFUSED_WAIT_S / 60:.0f} "
                                     "min will do.",
                          "not found": f"Mountpoint not found ({self.last_reply}{listed}): "
                                       f"check {self._option('mountpoint')} (case matters).",
                          "failed": f"Unexpected reply: {self.last_reply or '(none)'}"}[kind]
                LOG.warning("%s: %s", self.name, reason)
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
            LOG.info("%s: connected to %s:%s/%s (%s)%s", self.name, settings.host,
                     settings.port, settings.mountpoint, self.last_reply,
                     f"; GGA uplink on, every {self.gga_interval_s:.0f} s" if self.send_gga else "")
            if self.send_gga:
                gga_task = asyncio.get_running_loop().create_task(self._gga_loop(writer))
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception:
                    self.data_errors += 1
                    LOG.exception("%s: the connect callback failed", self.name)
            if rest:
                self._data(rest, decoder)
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(READ_SIZE), self.stale_s)
                except asyncio.TimeoutError:
                    return "no data", f"No data from the caster for {self.stale_s:.0f} s."
                if not chunk:
                    return "closed", "The caster closed the connection."
                if gga_task is not None and gga_task.done() and not gga_task.cancelled():
                    error = gga_task.exception()
                    if error is not None:   # the type only: a write error, no GGA text
                        return "failed", f"Sending the GGA failed: {type(error).__name__}"
                self._data(chunk, decoder)
                if decoder is not None and decoder.ended:
                    return "closed", "The caster ended the stream."
        finally:
            try:
                if gga_task is not None:
                    gga_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await gga_task
                await self._close(writer)
            finally:   # even if the close is cancelled (stop): the guard must not stick
                if key is not None:
                    NtripClient._open[key] -= 1
                    if not NtripClient._open[key]:
                        del NtripClient._open[key]

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
                LOG.exception("%s: handling the stream failed", self.name)

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
            "sourcetable_login": self.sourcetable_login,
            "mountpoints": [{key: r[key] for key in (
                "mountpoint", "identifier", "format", "nav_system", "nmea", "solution")}
                | {"accepted": self.accepts(r), "configured": r["mountpoint"] == settings.mountpoint}
                for r in self.records],
            "mountpoints_cut": max(0, self.sourcetable_size - len(self.records)),
            "caster_records": list(self.other_records),
            "send_gga": self.send_gga,
            "gga_sent": self.gga_sent,
            "gga_sent_connection": self.gga_sent_connection,
            "gga_skipped": self.gga_skipped,
            "gga_last_age_s": None if self._gga_last is None
            else round(now - self._gga_last, 1),
        }
