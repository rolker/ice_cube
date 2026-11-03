"""A small NTRIP caster (v1) for rovers on the local network.

Plain asyncio TCP rather than aiohttp: NTRIP v1 answers `ICY 200 OK`, which is not HTTP,
and its clients send loose requests. Requests handled:

- `GET /` or an unknown mountpoint: the sourcetable, one STR line for our mountpoint, with
  latitude and longitude 0.00 (the stream's 1005 carries the real position).
- `GET /<mountpoint>`: `ICY 200 OK`, then RTCM from the output filter. With a password
  set, the Basic login must match (401 otherwise); too many clients get 503.

Each client gets whole frames written to its socket. If more than CLIENT_BUFFER bytes
are waiting for a client, new frames for it are skipped and counted (old corrections are
useless to a rover); if its buffer does not drain for STALL_S, it is dropped. Nothing a
client does can slow the serial reader or the raw log. One address may have at most
MAX_HANDSHAKES_PER_ADDRESS requests unfinished at once, so a slow sender cannot hold
every slot.

What clients send (rovers may send GGA) is counted by kind and then dropped: GGA, other
NMEA by type, and anything else. The first ROVER_SAMPLES lines that are not GGA go to the
connection log, escaped and shortened, NMEA or not, with every number that could be a
coordinate blanked first (see redact_position); a summary goes with the disconnect line.
A client's GGA lines are never logged, and its latest GGA position is kept in memory for
the page only.

This module never imports the command, control or serial modules, so nothing from the
network can reach the receiver.
"""
import asyncio
import base64
import binascii
import hmac
import ipaddress
import logging
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from .hub import OutputFilter, Secret

LOG = logging.getLogger("gnss_base")

HEADER_LIMIT = 4096
HEADER_TIMEOUT_S = 10
MAX_HANDSHAKES = 20         # connections still sending their request...
MAX_HANDSHAKES_PER_ADDRESS = 4   # ...and from any one address
CLIENT_BUFFER = 32 * 1024   # about 25 s of corrections
STALL_S = 30
CHECK_S = 1.0
CLIENT_READ = 512
LOCKOUT_FAILURES = 5        # failed logins from one address within LOCKOUT_WINDOW_S...
LOCKOUT_WINDOW_S = 60
LOCKOUT_S = 60              # ...lock it out for this long
MAX_TRACKED = 1000          # addresses remembered for the lockout
ROVER_SAMPLES = 5           # lines a client sends that are logged, per connection
SAMPLE_CHARS = 200          # each shortened to this, after escaping
MAX_TYPES = 16              # sentence types counted per client; more count as "other types"
MOUNTPOINT = re.compile(r"[A-Za-z0-9_]{1,32}")
SYSTEMS = {107: "GPS", 108: "GLO", 109: "GAL", 111: "QZS", 112: "BDS", 113: "IRN"}


@dataclass
class CasterSettings:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 2101
    mountpoint: str = "BASE"
    user: str = ""
    password: Secret = field(default_factory=lambda: Secret(""))
    max_clients: int = 4

    @property
    def login(self):
        return bool(self.password)

    def problem(self):
        if not self.enabled:
            return ""
        if not MOUNTPOINT.fullmatch(self.mountpoint or ""):
            return "caster_mountpoint must be 1 to 32 letters, digits or '_'."
        if not isinstance(self.max_clients, int) or not 1 <= self.max_clients <= 64:
            return "caster_max_clients must be 1 to 64."
        return ""


def _text(value, limit=100):
    value = "".join(c if " " <= c <= "~" else " " for c in value)
    return " ".join(value.split())[:limit]


class RequestError(Exception):
    pass


async def read_request(reader):
    """The request line and headers, at most HEADER_LIMIT bytes in all."""
    lines, total = [], 0
    while True:
        try:
            line = await reader.readline()
        except (ValueError, asyncio.LimitOverrunError):
            raise RequestError("request header too large") from None
        total += len(line)
        if total > HEADER_LIMIT:
            raise RequestError("request header too large")
        if not line:
            raise RequestError("connection closed during the request")
        line = line.decode("latin-1").rstrip("\r\n")
        if not line:
            if lines:
                break
            continue
        lines.append(line)
    parts = lines[0].split()
    if len(parts) < 2:
        raise RequestError("malformed request line")
    headers = {}
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return parts[0].upper(), parts[1], parts[2] if len(parts) > 2 else "", headers


def parse_basic(header):
    """(user, password) from an `Authorization: Basic ...` header, or None."""
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    user, colon, password = decoded.partition(":")
    return (user, password) if colon else None


def _escape(raw, limit=SAMPLE_CHARS):
    """Printable ASCII as it is, anything else as \\xNN; (text, shortened)."""
    out = []
    for byte in raw[:limit + 1]:   # each byte gives at least one character
        if byte == 0x5C:
            out.append("\\\\")
        elif 0x20 <= byte < 0x7F:
            out.append(chr(byte))
        else:
            out.append({9: "\\t", 10: "\\n", 13: "\\r"}.get(byte, f"\\x{byte:02x}"))
    text = "".join(out)
    return (text[:limit], True) if len(text) > limit else (text, False)


# Anything in a rover's line that could be a coordinate is replaced by "-":
# - a number followed by a hemisphere field (",N" ",S" ",E" ",W"): NMEA ddmm.mmmm and
#   dddmm.mmmm latitude and longitude, with or without decimals;
# - a decimal number with 4 or more digits after the point (decimal degrees, as in
#   $PQTMPVT or "lat=48.1173"), 5 or more before it (ECEF metres) or 7 or more in all
#   (ddmm.mmm without a hemisphere), and any number with an exponent.
# Over-redaction is fine: these lines only show what kind of data a rover sends.
_HEMISPHERE = re.compile(rb"\d+(?:\.\d*)?(?=\s*,\s*[NSEW]\s*(?:[,*\r\n]|$))")
_NUMBER = re.compile(rb"[-+]?(?:\d+\.\d*|\.\d+|\d+(?=[eE]))(?:[eE][-+]?\d+)?")


def _maybe_coordinate(match):
    text = match.group()
    mantissa, _, exponent = text.lstrip(b"+-").lower().partition(b"e")
    whole, point, fraction = mantissa.partition(b".")
    if exponent or (point and (len(fraction) >= 4 or len(whole) >= 5
                               or len(whole) + len(fraction) >= 7)):
        return b"-"
    return text


def redact_position(line):
    """Any line from a rover, NMEA or not, with every number that could be a coordinate
    replaced by "-" (see the rules above), so a logged sample does not give away where
    the rover is."""
    line = _HEMISPHERE.sub(b"-", line)
    return _NUMBER.sub(_maybe_coordinate, line)


def peer_address(host):
    """The client's address as one key for the per-address limits and the log: an
    IPv4-mapped IPv6 address (::ffff:192.0.2.5, from a dual-stack socket) becomes the
    IPv4 address, so one host cannot get two sets of limits."""
    try:
        mapped = ipaddress.ip_address(host.split("%", 1)[0]).ipv4_mapped
    except (ValueError, AttributeError):
        return host
    return str(mapped) if mapped is not None else host


def _sentence_type(line):
    """'GGA' for any talker's GGA, the address field for other NMEA ('GPRMC',
    'PQTMVER'), None for anything that is not NMEA."""
    if line[:1] != b"$":
        return None
    head = line[1:].split(b",", 1)[0].split(b"*", 1)[0]
    if not head or len(head) > 12 or not head.isalnum():
        return "?"
    head = head.decode("ascii")
    return "GGA" if len(head) == 5 and head[2:] == "GGA" else head


def _degrees(text, hemisphere, digits, limit):
    if not re.fullmatch(rb"\d{%d}\d\d(\.\d+)?" % digits, text) or hemisphere not in (
            b"N", b"S", b"E", b"W"):
        return None
    degrees, minutes = int(text[:digits]), float(text[digits:])
    value = degrees + minutes / 60
    if minutes >= 60 or value > limit:
        return None
    return round(-value if hemisphere in (b"S", b"W") else value, 8)


def gga_position(fields):
    """(lat, lon) from GGA fields, or None."""
    if len(fields) < 7:
        return None
    lat = _degrees(fields[2], fields[3], 2, 90)
    lon = _degrees(fields[4], fields[5], 3, 180)
    return None if lat is None or lon is None else (lat, lon)


class Client:
    def __init__(self, writer, address, agent, version, user, clock, note=None):
        self.writer = writer
        self.address = address
        self.agent = agent
        self.version = version
        self.user = user
        self._clock = clock
        self._note = note           # note(event, **fields): to the connection log
        self.output = OutputFilter(clock)
        self.connected_at = clock()
        self.bytes_sent = 0
        self.frames_sent = 0
        self.frames_skipped = 0
        self.bytes_received = 0
        self.last_gga = None
        self.first_gga = None
        self.gga_quality = None
        self.gga_time = None        # the GGA's own UTC time field, as text
        self.position = None        # (lat, lon) of the latest GGA with a fix: page only
        self.gga = 0                # GGA sentences received
        self.nmea_other = 0         # other NMEA sentences
        self.other = 0              # anything else: lines or runs of bytes
        self.types = Counter()      # other NMEA by type
        self.samples = 0
        self.reason = ""
        self._last_progress = self.connected_at
        self._last_buffer = 0
        self._partial = b""

    def buffered(self):
        transport = self.writer.transport
        return transport.get_write_buffer_size() if transport is not None else 0

    def send(self, frame):
        if self.writer.is_closing() or not self.output.accept(frame):
            return
        if self.buffered() > CLIENT_BUFFER:
            self.frames_skipped += 1
            return
        self.writer.write(frame)
        self.bytes_sent += len(frame)
        self.frames_sent += 1

    def stalled(self, now):
        """True once the client's buffer has not drained for STALL_S."""
        size = self.buffered()
        if size == 0 or size < self._last_buffer:
            self._last_progress = now
        self._last_buffer = size
        return size > 0 and now - self._last_progress > STALL_S

    def drop(self, reason):
        self.reason = reason
        transport = self.writer.transport
        if transport is not None:
            transport.abort()

    def received(self, data):
        """Count what the client sent, keep its latest GGA, log the first few other
        lines; the bytes go nowhere else."""
        self.bytes_received += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()
        if len(self._partial) > CLIENT_READ:   # a long run without a line end
            lines.append(self._partial)
            self._partial = b""
        for line in lines:
            line = line.rstrip(b"\r")
            if line:
                self._line(line)

    def _line(self, line):
        kind = _sentence_type(line)
        if kind == "GGA":
            self.gga += 1
            fields = line.split(b"*", 1)[0].split(b",")
            now = self._clock()
            self.last_gga = now
            if self.first_gga is None:
                self.first_gga = now
            self.gga_quality = (int(fields[6]) if len(fields) > 6 and fields[6].isdigit()
                                and len(fields[6]) < 3 else None)
            utc = fields[1] if len(fields) > 1 else b""
            self.gga_time = (utc.decode("ascii")
                             if re.fullmatch(rb"\d{6}(\.\d{1,4})?", utc) else None)
            position = gga_position(fields) if self.gga_quality else None
            self.position = position and (position[0], position[1], self.gga_quality, now)
            return
        if kind is None:
            self.other += 1
        else:
            self.nmea_other += 1
            if kind in self.types or len(self.types) < MAX_TYPES:
                self.types[kind] += 1
            else:
                self.types["other types"] += 1
        if self.samples < ROVER_SAMPLES:
            self.samples += 1
            # Only what can be shown is redacted (with room for a number that runs on
            # past the cut), which also keeps the regex work small.
            shown = line[:SAMPLE_CHARS + 64]
            text, shortened = _escape(redact_position(shown))
            shortened = shortened or len(line) > len(shown)
            if self._note is not None:
                self._note("rover_sent", n=self.samples, kind="nmea" if kind else "other",
                           type=kind, text=text, shortened=shortened)

    def gga_hz(self):
        if self.gga < 2 or self.last_gga is None or self.last_gga <= self.first_gga:
            return None
        return round((self.gga - 1) / (self.last_gga - self.first_gga), 2)

    def summary(self):
        """What the client sent, without positions: for the log and the page."""
        return {"bytes_received": self.bytes_received, "gga": self.gga,
                "gga_hz": self.gga_hz(), "nmea_other": self.nmea_other, "other": self.other,
                "types": dict(self.types.most_common())}

    def snapshot(self, now):
        position = None
        if self.position is not None:
            lat, lon, quality, at = self.position
            position = {"lat": lat, "lon": lon, "quality": quality, "utc": self.gga_time,
                        "age_s": round(now - at, 1)}
        return {"address": self.address, "agent": self.agent, "ntrip_version": self.version,
                "user": self.user, "connected_s": round(now - self.connected_at),
                "bytes_sent": self.bytes_sent, "frames_sent": self.frames_sent,
                "frames_skipped": self.frames_skipped, "bytes_received": self.bytes_received,
                "gga_quality": self.gga_quality,
                "gga_age_s": None if self.last_gga is None else round(now - self.last_gga, 1),
                "sent": self.summary(), "position": position}


class Caster:
    def __init__(self, settings, hub, events, version="", clock=time.monotonic,
                 check_s=CHECK_S):
        self.settings = settings
        self.hub = hub
        self.events = events
        self.version = version
        self._clock = clock
        self._check_s = check_s
        self.clients = []
        self.server = None
        self.error = settings.problem()
        self.connections = 0
        self.refused = 0
        self.auth_failures = 0
        self.sourcetables = 0
        self._handshakes = 0
        self._pending = Counter()   # address -> requests still being read
        self._failures = {}         # address -> deque of failed login times
        self._locked = {}           # address -> locked until
        self._subscriber = None
        self._watchdog = None
        self._tasks = set()

    async def start(self):
        if not self.settings.enabled or self.error:
            if self.error:
                LOG.error("caster: %s", self.error)
            return False
        try:
            self.server = await asyncio.start_server(
                self._handle, self.settings.host, self.settings.port, limit=HEADER_LIMIT)
        except OSError as error:
            self.error = f"Cannot listen on port {self.settings.port}: {error.strerror or error}"
            LOG.error("caster: %s", self.error)
            return False
        self._subscriber = self.hub.subscribe("caster", self._on_frame)
        self._watchdog = asyncio.create_task(self._watch())
        LOG.info("NTRIP caster on port %s, mountpoint %s", self.settings.port,
                 self.settings.mountpoint)
        return True

    @property
    def port(self):
        """The port actually listened on (useful when started on port 0)."""
        if self.server is None or not self.server.sockets:
            return None
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self._subscriber is not None:
            self.hub.unsubscribe(self._subscriber)
            self._subscriber = None
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self.server is not None:
            self.server.close()
            for client in list(self.clients):
                client.drop("app stopping")
            for task in list(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.wait(self._tasks, timeout=5)
            await self.server.wait_closed()
            self.server = None

    def _on_frame(self, frame, _number):
        for client in list(self.clients):
            client.send(frame)

    async def _watch(self):
        while True:
            await asyncio.sleep(self._check_s)
            now = self._clock()
            for client in list(self.clients):
                if client.stalled(now):
                    client.drop(f"stalled: nothing drained for {STALL_S} s")
            self._prune(now)

    def _prune(self, now):
        """Forget failed logins older than the window and lockouts that ended, and keep
        at most MAX_TRACKED addresses of each (the most recent)."""
        self._failures = {a: f for a, f in self._failures.items()
                          if f and now - f[-1] <= LOCKOUT_WINDOW_S}
        if len(self._failures) > MAX_TRACKED:
            keep = sorted(self._failures, key=lambda a: self._failures[a][-1])[-MAX_TRACKED:]
            self._failures = {a: self._failures[a] for a in keep}
        self._locked = {a: until for a, until in self._locked.items() if until > now}
        if len(self._locked) > MAX_TRACKED:
            keep = sorted(self._locked, key=self._locked.get)[-MAX_TRACKED:]
            self._locked = {a: self._locked[a] for a in keep}

    # ---- one connection ----

    async def _handle(self, reader, writer):
        self._tasks.add(asyncio.current_task())
        try:
            await self._serve(reader, writer)
        except asyncio.CancelledError:
            pass
        except Exception:
            LOG.exception("caster: connection failed")
        finally:
            self._tasks.discard(asyncio.current_task())
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (OSError, asyncio.TimeoutError, asyncio.CancelledError):
                pass

    def _log(self, event, address, **fields):
        self.events.write(event, address=address, **fields)

    async def _serve(self, reader, writer):
        peer = writer.get_extra_info("peername")
        address = peer_address(peer[0]) if peer else "?"
        if self._handshakes >= MAX_HANDSHAKES:
            self.refused += 1
            self._log("refused", address, reason=f"{MAX_HANDSHAKES} requests unfinished")
            return
        if self._pending[address] >= MAX_HANDSHAKES_PER_ADDRESS:
            self.refused += 1
            self._log("refused", address, reason=f"{MAX_HANDSHAKES_PER_ADDRESS} requests "
                      "from this address unfinished")
            return
        self._handshakes += 1
        self._pending[address] += 1
        try:
            request = await asyncio.wait_for(read_request(reader), HEADER_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.refused += 1
            self._log("refused", address, reason="no complete request within "
                      f"{HEADER_TIMEOUT_S} s")
            return
        except RequestError as error:
            self.refused += 1
            self._log("refused", address, reason=str(error))
            return
        finally:
            self._handshakes -= 1
            self._pending[address] -= 1
            if self._pending[address] <= 0:
                del self._pending[address]
        method, path, _protocol, headers = request
        agent = _text(headers.get("user-agent", ""))
        version = _text(headers.get("ntrip-version", "")) or "Ntrip/1.0"
        mountpoint = path.split("?", 1)[0].lstrip("/")
        settings = self.settings
        if method != "GET":
            self.refused += 1
            writer.write(b"HTTP/1.0 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            self._log("refused", address, user_agent=agent, reason=f"method {_text(method, 16)}")
            return
        if mountpoint != settings.mountpoint:
            self.sourcetables += 1
            writer.write(self.sourcetable())
            self._log("sourcetable", address, user_agent=agent,
                      mountpoint=_text(mountpoint, 40))
            return
        login = parse_basic(headers.get("authorization"))
        user = _text(login[0], 64) if login else ""
        refusal = self._check_login(address, login)
        if refusal:
            self.auth_failures += 1
            writer.write(b"HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"/"
                         + settings.mountpoint.encode() + b"\"\r\nContent-Length: 0\r\n\r\n")
            # Not the user name typed: it may be a mistyped password.
            self._log("auth_failed", address, user_agent=agent, ntrip_version=version,
                      reason=refusal)
            LOG.info("caster: login refused for %s (%s)", address, refusal)
            return
        if len(self.clients) >= settings.max_clients:
            self.refused += 1
            writer.write(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            self._log("refused", address, user_agent=agent, ntrip_version=version, user=user,
                      reason=f"already {len(self.clients)} clients (caster_max_clients)")
            return
        writer.write(b"ICY 200 OK\r\n")  # as RTKLIB's caster: no blank line
        client = Client(writer, address, agent, version, user, self._clock,
                        note=lambda event, **fields: self._log(event, address, **fields))
        self.clients.append(client)
        self.connections += 1
        self._log("connect", address, user_agent=agent, ntrip_version=version, user=user)
        LOG.info("caster: %s connected (%s)", address, agent or "no agent")
        try:
            while True:
                data = await reader.read(CLIENT_READ)
                if not data:
                    break
                client.received(data)
            client.reason = client.reason or "client closed the connection"
        except OSError as error:
            client.reason = client.reason or f"connection lost: {error.strerror or error}"
        finally:
            self.clients.remove(client)
            duration = round(self._clock() - client.connected_at, 1)
            self._log("disconnect", address, user_agent=agent, ntrip_version=version,
                      user=user, bytes_sent=client.bytes_sent,
                      frames_skipped=client.frames_skipped, duration_s=duration,
                      reason=client.reason, **client.summary())
            LOG.info("caster: %s disconnected after %s s: %s", address, duration,
                     client.reason)

    def _check_login(self, address, login):
        """"" if the client may connect, otherwise why not. No password set: anyone."""
        settings = self.settings
        if not settings.login:
            return ""
        now = self._clock()
        if self._locked.get(address, 0) > now:
            return "locked out after repeated failed logins"
        if login is None:
            reason = "no login given"
        else:
            user, password = login
            user_ok = (not settings.user
                       or hmac.compare_digest(user.encode(), settings.user.encode()))
            password_ok = hmac.compare_digest(password.encode(),
                                              settings.password.reveal().encode())
            if user_ok and password_ok:
                return ""
            reason = "wrong user or password"
        failures = self._failures.setdefault(address, deque(maxlen=LOCKOUT_FAILURES))
        failures.append(now)
        while failures and now - failures[0] > LOCKOUT_WINDOW_S:
            failures.popleft()
        if len(failures) >= LOCKOUT_FAILURES:
            self._locked[address] = now + LOCKOUT_S
            failures.clear()
        if len(self._failures) > MAX_TRACKED or len(self._locked) > MAX_TRACKED:
            self._prune(now)
        return reason

    def sourcetable(self):
        settings = self.settings
        types = sorted(self.hub.frames)
        details = ",".join(f"{n}({10 if n in (1005, 1006, 1033) else 1})" for n in types)
        systems = "+".join(dict.fromkeys(SYSTEMS[n // 10] for n in types
                                         if n // 10 in SYSTEMS and 1 <= n % 10 <= 7))
        mp = settings.mountpoint
        line = (f"STR;{mp};{mp};RTCM 3.3;{details};2;{systems};none;;0.00;0.00;0;0;"
                f"gnss_base;none;{'B' if settings.login else 'N'};N;0;\r\n")
        body = (line + "ENDSOURCETABLE\r\n").encode("ascii")
        head = (f"SOURCETABLE 200 OK\r\nServer: NTRIP gnss_base/{self.version}\r\n"
                f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n")
        return head.encode("ascii") + body

    # ---- the page ----

    def snapshot(self):
        settings = self.settings
        now = self._clock()
        return {"enabled": settings.enabled, "listening": self.server is not None,
                "error": self.error, "port": settings.port, "mountpoint": settings.mountpoint,
                "login": settings.login, "max_clients": settings.max_clients,
                "connections": self.connections, "refused": self.refused,
                "auth_failures": self.auth_failures, "sourcetables": self.sourcetables,
                "clients": [client.snapshot(now) for client in self.clients]}
