"""Reference check: a second rtkrcv (RTKLIB-EX) running RTK against a CORS station, our
receiver as the rover and the station's NTRIP stream as the base, in static mode, so the
base antenna's position is measured continuously against an independent reference.

Shape, beside the measured position's rtkrcv (measured.py, whose runner, servers and
log this reuses):

    hub --RTCM--> FeedServer "feed" (127.0.0.1) --> rtkrcv-ex inpstr1 (our receiver)
    NtripClient --> StationDecoder (1005/1006/1033/MSM), the raw log (on_raw),
                    FeedServer "cors" (127.0.0.1) --> rtkrcv-ex inpstr2 (the station)
    SolutionServer <-- rtkrcv-ex outstr1 (llh lines, one per epoch, out-solstatic=all)

The app is the NTRIP client (ntripclient.py): one login, one connection at a time, its
own backoff, and the caster's answers shown on the card (rtkrcv's built-in client says
nothing on a refused login). rtkrcv reads the station's stream from a local socket like
our own, so its config holds no credentials. Nothing here can reach the receiver: this
module never imports the command, control or serial modules (a unit test checks).

Numbers: each solution is turned into an east/north/up offset from our own RTCM 1005
position (solution minus our 1005, so the offset is the CORS frame minus ours, about a
metre here between NAD83(2011) and ITRF2020). The session (since the last rtkrcv start)
keeps the mean and standard deviation of the fixed epochs and the counts by quality;
a 10-minute window keeps the recent mean. The residual is the session mean minus the
expected offset: green within 5 cm horizontal and 10 cm up, amber within 20 cm, red
beyond, or when there has been no fix for 30 minutes.

Frames, declared and never guessed: the expected offset is computed (datums.py) from
the station's frame, our frame and the epoch, not typed in. The station's frame comes
from the `reference_frame` option only. The RTCM 1005/1006 field "ITRF realization
year" (DF021, six bits) would be the place for a stream to declare it, but RTCM 10403
reserves the field ("all bits should be set to zero for now") and defines no encoding
of a year, so a non-zero value is shown on the card as a number and not read as a
frame. Without a declared station frame there is no expected offset, no residual and
no residual colour: the card says so and shows the raw offset. Our own frame is the
`base_frame` option (ITRF2020 by default, the frame of a PPP coordinate); the epoch is
the `base_epoch` option (the coordinate's observation date), or today's date when it is
empty, which is right only for a coordinate kept current: ITRF2020 coordinates move
with the plate, about 2 cm a year here, and the computed offset moves with the epoch by
the same amount, so a coordinate observed a year ago with no base_epoch is about 2 cm
off in the expected offset.

The CORS stream is also written to disk as received (`on_raw`): the app's RawLog under
`<log_dir>/reference/raw`, one `.rtcm3` file per UTC day, compressed and paused like
the receiver's raw log. The client connects as soon as the check starts, whether or not
our receiver sends (the card then shows the login state at once); the stream is logged
for as long as the connection lasts, rtkrcv restarts or not.

Sessions: static mode accumulates one filter for as long as rtkrcv runs, so each run is
one static solution. rtkrcv is restarted at `reference_reset_utc` every day (a planned
restart, without the exit backoff), and when corrections resume after a gap of more
than GAP_RESTART_S, since the held ambiguities are then stale. A moved antenna shows as
a slow drift until the next reset; the measured position (single point) catches a move
within seconds, so that card is the one to watch for moves.

Two checks (0.7.0): `ReferenceCheck` runs once per configured reference, each with its
own settings (`ReferenceSettings.prefix`: "reference" for the first, "reference2" for the
second; the texts that name an option use it), client, rtkrcv, work folder, logs and
reset. The station behind a mountpoint is identified by its 1005/1006 station ID and
position and its 1033 descriptors; a "nearest station" mountpoint (GGA uplink, see
ntripclient.py) can switch stations, and a change of ID or position restarts rtkrcv
and is shown and logged as "station changed". `compare` sets the two checks' session
means side by side (the "Reference comparison" card): each reference minus ours, and
the second minus the first, which does not depend on our coordinate at all, with a
colour (COMPARE_GOOD_*, COMPARE_WARN_M) only when both stations' frames are declared
and the same, both checks are streaming now and each has a fixed epoch within
COMPARE_FRESH_S; otherwise the numbers are shown as stale, with each side's last-fix
age. `reference*_expect_station` (a station ID, 0 to 4095, or empty for any) marks a
1005/1006 from another station as "wrong station (got N, expected M)" on the check's
card and the comparison (no colour on either), logged once per change.
"""
import asyncio
import contextlib
import logging
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .datums import decimal_year, expected_offset_enu, parse_epoch, parse_frame
from .frames import FrameParser, rtcm_type
from .hub import Secret
from .measured import (DEFAULT_BINARY, HOST, NAVSYS, SNR_MASK_VALUES, FeedServer,
                       RtkrcvRunner, is_msm)
from .ntripclient import NtripClient
from .rtcm import decode_1005, decode_1033, ecef_to_enu, ecef_to_llh, llh_to_ecef

LOG = logging.getLogger("gnss_base")

RECENT_S = 600.0              # the running mean's window
CORRECTIONS_STALE_S = 30.0    # no CORS frame for this long: "no corrections"
SOLUTION_STALE_S = 30.0       # no solution for this long: "stale"
GAP_RESTART_S = 300.0         # corrections back after a gap this long: new session
NO_FIX_RED_S = 1800.0         # fixed once, then none for this long: red
GOOD_HORIZONTAL_M = 0.05
GOOD_UP_M = 0.10
WARN_M = 0.20
COMPARE_GOOD_HORIZONTAL_M = 0.03  # reference 2 minus reference 1, same declared frame
COMPARE_GOOD_UP_M = 0.06
COMPARE_WARN_M = 0.10
COMPARE_FRESH_S = 1800.0      # each check's last fix at most this old for a verdict
SAME_BASE_M = 0.001           # the two checks' offsets are from the same 1005 within this
MAX_FIELD = 255               # host, mountpoint, user and password: at most this long
MAX_OFFSET_M = 1_000_000.0    # an offset beyond this is not kept (a corrupt line)
EARTH_RADIUS_M = 6_371_000.0  # a 1005 nearer the centre than half of this is no position
QUALITY_KINDS = {1: "fixed", 2: "float", 4: "dgps", 5: "single", 6: "ppp"}
QUALITY_STATES = {1: "fixed", 2: "float"}

# RTKLIB-EX 2.5.1 (demo5) option spellings, from the reference spike: static, L1+L2,
# broadcast ionosphere, Saastamoinen troposphere, fix-and-hold with the AR filter,
# GLONASS ambiguities not resolved (the CORS receiver is another make: autocal blocked
# every fix), BeiDou resolved, the F9P-derived partial-AR thresholds, no antenna model on
# either side (our 1005 is the phase centre; MaCORS streams a null antenna), the base
# position from the stream's 1005/1006. out-solstatic=all makes static mode emit every
# epoch, and out-outsingle=on a single solution while RTK cannot run (no corrections,
# or older than maxage): without it rtkrcv writes nothing then, which the runner would
# take for a hang. Stream 2 is the station's stream from the app's NTRIP client, over a
# local socket like stream 1: no login in this file.
CONFIG_TEMPLATE = """\
# Written by gnss_base each time it starts the reference rtkrcv; changes here are
# overwritten. Both input streams are local sockets the app serves.
inpstr1-type   =tcpcli
inpstr1-path   ={host}:{feed_port}
inpstr1-format =rtcm3
inpstr2-type   =tcpcli
inpstr2-path   ={host}:{cors_port}
inpstr2-format =rtcm3
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
pos1-posmode   =static
pos1-frequency =l1+l2
pos1-soltype   =forward
pos1-elmask    ={elevation_mask}
pos1-snrmask_r ={snr_on}
pos1-snrmask_b =off
pos1-snrmask_L1={snr_values}
pos1-snrmask_L2={snr_values}
pos1-dynamics  =off
pos1-tidecorr  =off
pos1-ionoopt   =brdc
pos1-tropopt   =saas
pos1-sateph    =brdc
pos1-posopt5   =on
pos1-navsys    ={navsys}
pos2-armode    =fix-and-hold
pos2-gloarmode =off
pos2-bdsarmode =on
pos2-arthres   =3
pos2-arlockcnt =0
pos2-arelmask  ={elevation_mask}
pos2-elmaskhold={elevation_mask}
pos2-minfixsats=4
pos2-maxage    =30
pos2-arfilter  =on
pos2-arthresmin=3
pos2-arthresmax=3
pos2-arthres1  =0.1
pos2-arthres2  =0
pos2-arthres3  =1e-09
pos2-arthres4  =1e-05
pos2-varholdamb=0.1
pos2-gainholdamb=0.01
pos2-minholdsats=5
pos2-mindropsats=10
pos2-slipthres =0.05
pos2-dopthres  =0
pos2-rejphase  =2
pos2-rejcode   =10
out-solformat  =llh
out-outhead    =off
out-timesys    =gpst
out-timeform   =hms
out-degform    =deg
out-height     =ellipsoidal
out-solstatic  =all
out-outsingle  =on
ant1-postype   =single
ant1-anttype   =
ant1-antdele   =0
ant1-antdeln   =0
ant1-antdelu   =0
ant2-postype   =rtcm
ant2-anttype   =
ant2-antdele   =0
ant2-antdeln   =0
ant2-antdelu   =0
"""


STATION_FRAME_UNKNOWN = "station frame unknown: set reference_frame"
OUR_FRAME_UNKNOWN = "our frame unknown: set base_frame"


def station_frame_unknown(prefix="reference"):
    return f"station frame unknown: set {prefix}_frame"


def parse_reset_utc(text, name="reference_reset_utc"):
    """`reference_reset_utc` ("HH:MM", UTC) as seconds since midnight; None if empty
    (no daily reset)."""
    text = str(text).strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match or not (int(match.group(1)) < 24 and int(match.group(2)) < 60):
        raise ValueError(f"{name} must be HH:MM (UTC, 24 h) or empty")
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


_NUMBER = r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*"


def parse_gga_position(text, name="reference_gga_position"):
    """`reference_gga_position`: "lat,lon" in decimal degrees as (lat, lon), or None
    when empty (the GGA then carries our base's 1005 position). The message names the
    option, never the value."""
    text = str(text).strip()
    if not text:
        return None
    match = re.fullmatch(_NUMBER + "," + _NUMBER, text)
    if not match:
        raise ValueError(f'{name} must be "lat,lon" in decimal degrees, or empty')
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{name} must have a latitude from -90 to 90 and a longitude "
                         "from -180 to 180")
    return lat, lon


def seconds_to_reset(now_wall, reset_seconds):
    """Seconds from the wall-clock time `now_wall` to the next daily reset, or None."""
    if reset_seconds is None:
        return None
    return (reset_seconds - now_wall % 86400) % 86400


def _field_problem(value, forbidden=""):
    """Why `value` cannot go into the NTRIP request (the request line, a header), or
    "". The text names the problem, never the value."""
    if len(value) > MAX_FIELD:
        return f"is longer than {MAX_FIELD} characters"
    if not value.isascii() or not value.isprintable():
        return "has a character that is not printable ASCII"
    if any(c.isspace() for c in value):
        return "has a space"
    for c in forbidden:
        if c in value:
            return f"has a '{c}'"
    return ""


@dataclass
class ReferenceSettings:
    """One reference check's options. `prefix` is the options' prefix ("reference",
    "reference2"), used in every text that names an option. `blocked` is a problem
    found across checks (the same login twice), set by the app."""
    enabled: bool = False
    host: str = ""
    port: int = 2101
    mountpoint: str = ""
    user: str = ""
    password: Secret = field(default_factory=lambda: Secret(""))
    frame: str = ""                 # the station's frame (reference_frame); empty: unknown
    base_frame: str = "ITRF2020"    # our 1005's frame (base_frame); empty: unknown
    base_epoch: str = ""            # its observation epoch (base_epoch); empty: today
    reset_utc: str = "00:00"        # HH:MM UTC; empty: never
    binary: str = DEFAULT_BINARY
    work_dir: Path = Path("/data/reference")
    elevation_mask: int = 15        # the measured position's masks, shared
    snr_mask: int = 35
    send_gga: bool = False          # the GGA uplink (reference_send_gga)
    gga_position: str = ""          # "lat,lon" for the GGA; empty: our 1005
    expect_station: object = None   # the station ID expected (0 to 4095); None: any
    prefix: str = "reference"
    blocked: str = ""

    def problem(self):
        """Why the check cannot run with these options ("not configured"), or ""."""
        p = self.prefix
        missing = [name for name, value in ((f"{p}_host", self.host),
                                            (f"{p}_mountpoint", self.mountpoint),
                                            (f"{p}_user", self.user)) if not value]
        if missing:
            return "set " + ", ".join(missing)
        if (not isinstance(self.port, int) or isinstance(self.port, bool)
                or not 1 <= self.port <= 65535):
            return f"{p}_port must be 1 to 65535"
        for name, value, forbidden in ((f"{p}_host", self.host, ":/@"),
                                       (f"{p}_mountpoint", self.mountpoint, ":/@?"),
                                       (f"{p}_user", self.user, ":"),
                                       (f"{p}_password", self.password.reveal(), "")):
            why = _field_problem(value, forbidden)
            if why:
                return f"{name} {why}"
        for name, value, high in (("measured_elevation_mask", self.elevation_mask, 90),
                                  ("measured_snr_mask", self.snr_mask, 60)):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= high:
                return f"{name} must be a whole number from 0 to {high}"
        expect = self.expect_station
        if expect is not None and (not isinstance(expect, int) or isinstance(expect, bool)
                                   or not 0 <= expect <= 4095):
            return f"{p}_expect_station must be a whole number from 0 to 4095, or empty"
        try:
            parse_frame(self.frame, f"{p}_frame")
            parse_frame(self.base_frame, "base_frame")
            parse_epoch(self.base_epoch, "base_epoch")
            parse_reset_utc(self.reset_utc, f"{p}_reset_utc")
            parse_gga_position(self.gga_position, f"{p}_gga_position")
            build_config(1, 1, 1, self)
        except ValueError as error:
            return str(error)
        return self.blocked

    def station_frame(self):
        """The station's declared frame (canonical name), or "" when none is declared."""
        return parse_frame(self.frame, f"{self.prefix}_frame")

    def gga_latlon(self):
        """The configured GGA position (lat, lon), or None (our 1005)."""
        return parse_gga_position(self.gga_position, f"{self.prefix}_gga_position")

    def our_frame(self):
        return parse_frame(self.base_frame, "base_frame")

    def our_epoch(self):
        """The declared observation epoch of our coordinate, or None (today's date)."""
        return parse_epoch(self.base_epoch, "base_epoch")

    def reset_seconds(self):
        return parse_reset_utc(self.reset_utc, f"{self.prefix}_reset_utc")


def login_clash(first, second):
    """Why `second` cannot run beside `first`, or "": both on, with the same caster and
    user (a CORS login usually allows one connection, so two would refuse each other)."""
    if not (first.enabled and second.enabled):
        return ""
    if (first.host.lower(), first.port, first.user) != (second.host.lower(), second.port,
                                                        second.user) or not second.user:
        return ""
    return (f"{second.prefix} uses the same caster and user as {first.prefix}: one login "
            "allows one connection; use another login")


def build_config(feed_port, solution_port, cors_port, settings):
    """rtkrcv's configuration for the reference check: the three local ports and the
    masks. No login: the app's NTRIP client serves the station's stream on `cors_port`."""
    for name, value, low, high in (("feed_port", feed_port, 1, 65535),
                                   ("solution_port", solution_port, 1, 65535),
                                   ("cors_port", cors_port, 1, 65535),
                                   ("elevation_mask", settings.elevation_mask, 0, 90),
                                   ("snr_mask", settings.snr_mask, 0, 60)):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    snr = settings.snr_mask
    return CONFIG_TEMPLATE.format(
        host=HOST, feed_port=feed_port, solution_port=solution_port, cors_port=cors_port,
        elevation_mask=settings.elevation_mask,
        snr_on="on" if snr else "off", snr_values=",".join([str(snr)] * SNR_MASK_VALUES),
        navsys=NAVSYS)


class StationDecoder:
    """Takes the station's stream as the NTRIP client receives it (`feed`) and decodes
    what the check needs: when frames and MSM last came, the station position
    (1005/1006, with its DF021 field) and its receiver and antenna descriptors (1033).
    A new connection starts a new parse (`reset`), as the stream may resume inside a
    frame."""

    def __init__(self, on_frame=None, clock=time.monotonic, label="reference check"):
        self._on_frame = on_frame
        self.label = label
        self._clock = clock
        self._parser = FrameParser()
        self.unknown_bytes = 0
        self.frames = 0
        self.msm_frames = 0
        self.bytes = 0
        self.last_frame = None
        self.last_msm = None
        self.position = None        # decode_1005 result with lat/lon/height, or None
        self.position_time = None
        self.descriptors = None     # decode_1033 result, or None
        self.decode_errors = 0
        self.callback_errors = 0

    def reset(self):
        self._parser = FrameParser()

    def feed(self, data):
        self.bytes += len(data)
        before = self._parser.unknown_bytes
        for kind, payload in self._parser.feed(data):
            if kind == "rtcm":
                self._frame(payload)
        self.unknown_bytes += self._parser.unknown_bytes - before

    def _frame(self, frame):
        now = self._clock()
        self.frames += 1
        self.last_frame = now
        number = rtcm_type(frame)
        try:
            if number in (1005, 1006):
                message = decode_1005(frame)
                x, y, z = message["ecef"]
                lat, lon, height = ecef_to_llh(x, y, z)
                if (math.hypot(x, y, z) < EARTH_RADIUS_M / 2
                        or not all(math.isfinite(v) for v in (lat, lon, height))):
                    raise ValueError("no position")
                message.update(lat=lat, lon=lon, height_m=height)
                self.position, self.position_time = message, now
            elif number == 1033:
                self.descriptors = decode_1033(frame)
            elif is_msm(number):
                self.msm_frames += 1
                self.last_msm = now
        except ValueError:
            self.decode_errors += 1
        if self._on_frame is not None:
            try:
                self._on_frame(now, number)
            except Exception:   # the decoder goes on whatever the check does with it
                self.callback_errors += 1
                if self.callback_errors == 1:
                    LOG.exception("%s: handling a CORS frame failed", self.label)

    def snapshot(self):
        now = self._clock()
        position = self.position
        return {
            "frames": self.frames, "msm_frames": self.msm_frames, "bytes": self.bytes,
            "age_s": None if self.last_frame is None else round(now - self.last_frame, 1),
            "msm_age_s": None if self.last_msm is None else round(now - self.last_msm, 1),
            "decode_errors": self.decode_errors,
            "callback_errors": self.callback_errors,
            "unknown_bytes": self.unknown_bytes,
            "position": None if position is None else {
                "message": position["message"], "station_id": position["station_id"],
                "itrf_year": position["itrf_year"],
                "lat": round(position["lat"], 9), "lon": round(position["lon"], 9),
                "height_m": round(position["height_m"], 4),
                "ecef": [round(v, 4) for v in position["ecef"]],
                "antenna_height_m": None if position["antenna_height_m"] is None
                else round(position["antenna_height_m"], 4),
                "age_s": round(now - self.position_time, 1)},
            "descriptors": None if self.descriptors is None else dict(self.descriptors),
        }


class SessionStats:
    """Offsets of one session: the fixed epochs' mean and standard deviation (Welford),
    counts by quality, when the first and last fix came, and a window of recent
    offsets for the running mean."""

    def __init__(self, recent_s=RECENT_S):
        self.recent_s = recent_s
        self.reset(None)

    def reset(self, started_wall):
        self.started_wall = started_wall
        self.epochs = 0
        self.counts = {"fixed": 0, "float": 0, "single": 0, "other": 0}
        self._n = 0
        self._mean = [0.0, 0.0, 0.0]
        self._m2 = [0.0, 0.0, 0.0]
        self.first_fix = None
        self.last_fix = None
        self._recent = deque()      # (clock, e, n, u, quality)

    def add(self, now, quality, enu):
        self.epochs += 1
        kind = QUALITY_KINDS.get(quality, "other")
        self.counts[kind if kind in self.counts else "other"] += 1
        if enu is None:
            return
        self._recent.append((now, *enu, quality))
        self.prune(now)
        if quality != 1:
            return
        self._n += 1
        for axis in range(3):
            delta = enu[axis] - self._mean[axis]
            self._mean[axis] += delta / self._n
            self._m2[axis] += delta * (enu[axis] - self._mean[axis])
        self.last_fix = now
        if self.first_fix is None:
            self.first_fix = now

    def prune(self, now):
        while self._recent and now - self._recent[0][0] > self.recent_s:
            self._recent.popleft()

    @property
    def fixed_count(self):
        return self._n

    def session_mean(self):
        """(mean E N U, std E N U) of the fixed epochs, or None without any."""
        if not self._n:
            return None
        std = [math.sqrt(m2 / self._n) for m2 in self._m2]
        return list(self._mean), std

    def recent(self, now):
        """Mean and std over the last `recent_s`: of the fixed epochs if there are any
        (`kind` fixed), else of every epoch (`kind` all); None with no offsets."""
        self.prune(now)
        if not self._recent:
            return None
        fixed = [s for s in self._recent if s[4] == 1]
        samples, kind = (fixed, "fixed") if fixed else (list(self._recent), "all")
        result = {"count": len(samples), "kind": kind, "window_s": self.recent_s}
        for axis, name in ((1, "e"), (2, "n"), (3, "u")):
            values = [s[axis] for s in samples]
            mean = sum(values) / len(values)
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
            result[f"{name}_mean_m"] = round(mean, 4)
            result[f"{name}_std_m"] = round(std, 4)
        return result


def _utc(wall):
    return None if wall is None else datetime.fromtimestamp(
        wall, timezone.utc).isoformat(timespec="seconds")


def verdict(residual, last_fix_age_s, session_age_s=None, no_expected=""):
    """("good" | "warn" | "bad" | None, why) for a residual (E, N, U metres, the session
    mean of the fixed epochs minus the expected offset), the age of the last fix (None:
    no fix this session), and the age of the session: red after NO_FIX_RED_S without a
    fix, whether or not there was one before. With fixes but no residual, `no_expected`
    says why there is no expected offset (a frame not declared): no colour from it."""
    if last_fix_age_s is None:
        if session_age_s is not None and session_age_s > NO_FIX_RED_S:
            return "bad", f"no fixed solution for {session_age_s / 60:.0f} min"
        return None, "no fixed solution this session yet"
    if last_fix_age_s > NO_FIX_RED_S:
        return "bad", f"no fixed solution for {last_fix_age_s / 60:.0f} min"
    if residual is None:
        return None, f"no residual: {no_expected or 'no expected offset'}"
    horizontal, up = math.hypot(residual[0], residual[1]), abs(residual[2])
    if horizontal <= GOOD_HORIZONTAL_M and up <= GOOD_UP_M:
        return "good", (f"within {GOOD_HORIZONTAL_M * 100:.0f} cm horizontal and "
                        f"{GOOD_UP_M * 100:.0f} cm up of the expected offset")
    if horizontal <= WARN_M and up <= WARN_M:
        return "warn", f"within {WARN_M * 100:.0f} cm of the expected offset"
    return "bad", f"more than {WARN_M * 100:.0f} cm from the expected offset"


class ReferenceCheck:
    """The reference check: its NTRIP client, its rtkrcv runner (RTK static, config
    above) fed from both, the decoded station stream, the session statistics, the daily
    reset and the gap restart.

    `get_base()` returns the receiver's current 1005 position as `{"ecef": (x, y, z),
    ...}` or None (Status.current_base). `on_solution(solution, line)` gets each solution
    line (for the log); `on_raw(data)` the station's bytes as received (for the raw
    log). `version` is the app's version, for the log header and the NTRIP user agent.
    `label` names the check in the log and on the hub ("reference check", "reference
    check 2"). `client_kwargs` go to the NtripClient (tests), `runner_kwargs` to the
    runner."""

    def __init__(self, settings, hub, get_base, on_solution=None, version="",
                 clock=time.monotonic, wall=time.time, on_raw=None, client_kwargs=None,
                 label="reference check", **runner_kwargs):
        self.settings = settings
        self.label = label
        self.prefix = settings.prefix
        self._get_base = get_base
        self._on_solution = on_solution
        self._on_raw = on_raw
        self._version = version
        self._clock = clock
        self._wall = wall
        self.problem = settings.problem()
        self.station = StationDecoder(on_frame=self._corrections, clock=clock, label=label)
        self.cors_feed = FeedServer()
        # The first check's client logs as "NTRIP client", as before 0.7.0.
        self.client = NtripClient(settings, self._on_data, version=version, clock=clock,
                                  wall=wall, on_connect=self.station.reset,
                                  label="" if self.prefix == "reference" else self.prefix,
                                  gga_position=self._gga_position,
                                  **(client_kwargs or {}))
        self.runner = RtkrcvRunner(
            settings, hub, on_solution=self._solution, clock=clock, wall=wall,
            make_config=self._config, servers={"cors": self.cors_feed},
            on_start=self._session_start, label=label, **runner_kwargs)
        self.raw_errors = 0           # on_raw raised
        self.stats = SessionStats()
        self.latest = None            # the latest solution, with offset_enu
        self._latest_time = None
        self._corr_last = None        # last CORS MSM frame of this session (clock)
        self._stream_any = None       # last CORS frame of any kind this session
        self._cors_ecef = None        # the station's 1005 this session is relative to
        self._session_clock = None    # when this session started (clock)
        self.previous = None          # the last session that had fixed epochs
        self.station_changes = 0      # sessions ended by a changed CORS position or ID
        self._cors_id = None          # the station ID this session is relative to
        self._known_station = None    # (ID, ECEF) of the last 1005/1006, across sessions
        self.station_changed = None   # the last change seen: when, IDs, distance
        self._base_ecef = None        # our 1005 the offsets are from
        self._base_changed = None     # when it last changed during a session (clock)
        self._reset_task = None
        self.sessions = 0
        self.resets = 0
        self.gap_restarts = 0
        self._reset_seconds = settings.reset_seconds() if not self.problem else None
        self._station_frame = settings.station_frame() if not self.problem else ""
        self._our_frame = settings.our_frame() if not self.problem else ""
        self._our_epoch = settings.our_epoch() if not self.problem else None
        self._gga_latlon = settings.gga_latlon() if not self.problem else None
        self._wrong_logged = None     # the unexpected station ID last logged

    # ---- life cycle ----

    def start(self):
        if not self.settings.enabled:
            return
        if self.problem:
            LOG.info("%s not configured: %s", self.label, self.problem)
            return
        self.client.start()
        self.runner.start()
        if self._reset_seconds is not None and self._reset_task is None:
            self._reset_task = asyncio.get_running_loop().create_task(self._reset_loop())

    async def stop(self):
        task, self._reset_task = self._reset_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.client.stop()
        await self.runner.stop()

    def _config(self, ports):
        return build_config(ports["feed"], ports["solution"], ports["cors"], self.settings)

    def _on_data(self, data):
        """The station's bytes from the NTRIP client: to the raw log, the decoder and
        rtkrcv's second input, in that order (the log first, like the receiver's)."""
        if self._on_raw is not None:
            try:
                self._on_raw(data)
            except Exception:
                self.raw_errors += 1
                if self.raw_errors == 1:
                    LOG.exception("%s: writing the station's stream failed", self.label)
        self.station.feed(data)    # a new connection resets the parse (on_connect)
        self.cors_feed.send(data)

    def _gga_position(self):
        """What the GGA uplink sends: the configured position (height 0), or our base's
        current 1005 position, or the last one this check saw (`_base_ecef`), or None."""
        if self._gga_latlon is not None:
            return (*self._gga_latlon, 0.0)
        base = self._get_base()
        ecef = self._base_ecef if base is None else tuple(base["ecef"])
        return None if ecef is None else ecef_to_llh(*ecef)

    async def _reset_loop(self):
        """Restart rtkrcv at the daily reset time. Sleeps at most an hour at a time, so
        a wall clock set forward or back (NTP after a boot) is noticed."""
        while True:
            wait = seconds_to_reset(self._wall(), self._reset_seconds)
            await asyncio.sleep(min(wait, 3600))
            # Fire only at the reset time itself (just before or just after it): after
            # an hour of a longer wait, or a clock set back or forward (NTP after a
            # boot), wait again from now.
            remaining = seconds_to_reset(self._wall(), self._reset_seconds)
            if 2 < remaining < 86400 - 2:
                continue
            if self.runner.request_restart("daily reset"):
                self.resets += 1
            await asyncio.sleep(2)

    def seconds_to_reset(self):
        return seconds_to_reset(self._wall(), self._reset_seconds)

    # ---- events ----

    def _close_session(self, why):
        """Keep the finished session's result (its fixed mean, residual and verdict)
        for the card until the next session has one of its own."""
        session = self.stats.session_mean()
        if session is None:
            return
        residual = self.residual()
        last_fix_age = None if self.stats.last_fix is None else self._clock() - self.stats.last_fix
        colour, reason = verdict(residual, last_fix_age, no_expected=self.no_expected())
        self.previous = {
            "started_utc": _utc(self.stats.started_wall), "ended_utc": _utc(self._wall()),
            "ended_because": why, "epochs": self.stats.epochs,
            "fixed": self.stats.fixed_count,
            "mean_enu": [round(v, 4) for v in session[0]],
            "std_enu": [round(v, 4) for v in session[1]],
            "residual_enu": None if residual is None else [round(v, 4) for v in residual],
            "verdict": colour, "verdict_reason": reason}

    def _session_start(self):
        exit = self.runner.last_exit
        self._close_session(exit["reason"] if exit else "rtkrcv started")
        self.sessions += 1
        self.stats.reset(self._wall())
        self.latest = None
        self._latest_time = None
        self._corr_last = None
        self._stream_any = None
        self._cors_ecef = None
        self._session_clock = self._clock()
        self._base_changed = None

    def _corrections(self, now, number):
        """An MSM frame from the station (observations; 1005, 1033 and ephemerides alone are
        not corrections): observations back after a long gap start a new session (the
        held ambiguities are stale)."""
        self._stream_any = now
        if number in (1005, 1006) and self.station.position is not None:
            # The station's coordinate is what the solution is relative to: a changed
            # one (another station behind the mountpoint, a re-coordination) is a new
            # session, not a mix of two frames. So is a changed station ID (a
            # "nearest station" mountpoint that switched).
            ecef = tuple(self.station.position["ecef"])
            station_id = self.station.position["station_id"]
            self._note_station(now, station_id, ecef)
            self._note_expected(station_id)
            if self._cors_ecef is None:
                self._cors_ecef, self._cors_id = ecef, station_id
            else:
                moved = math.dist(ecef, self._cors_ecef) > 0.0005
                renamed = station_id != self._cors_id
                if moved or renamed:
                    why = ("the station's 1005 position changed" if not renamed else
                           f"the station changed (ID {self._cors_id} to {station_id})")
                    if self.runner.request_restart(why):
                        self.station_changes += 1
                        self._cors_ecef, self._cors_id = ecef, station_id
            return
        if not is_msm(number):
            return
        last, self._corr_last = self._corr_last, now
        if last is not None and now - last > GAP_RESTART_S:
            minutes = (now - last) / 60
            if self.runner.request_restart(
                    f"corrections resumed after a {minutes:.0f} min gap"):
                self.gap_restarts += 1

    def _note_station(self, now, station_id, ecef):
        """Keep the station's identity across sessions; a change (ID, or position by
        more than 0.5 mm) is shown on the card and logged once. Its old 1033
        descriptors are dropped: they were the old station's."""
        known, self._known_station = self._known_station, (station_id, ecef)
        if known is None:
            return
        old_id, old_ecef = known
        moved_m = math.dist(ecef, old_ecef)
        if old_id == station_id and moved_m <= 0.0005:
            return
        if old_id != station_id:
            self.station.descriptors = None
        self.station_changed = {"utc": _utc(self._wall()), "clock": now,
                                "from_id": old_id, "to_id": station_id,
                                "moved_m": round(moved_m, 4)}
        LOG.warning("%s: station changed: ID %s to %s, %.3f km from the last 1005; "
                    "rtkrcv restarts for a new session", self.label, old_id, station_id,
                    moved_m / 1000)

    def wrong_station(self):
        """"wrong station (got N, expected M)" when `expect_station` is set and the
        latest 1005/1006 has another station ID, else ""."""
        expect = self.settings.expect_station
        position = self.station.position
        if expect is None or self.problem or position is None:
            return ""
        got = position["station_id"]
        return "" if got == expect else f"wrong station (got {got}, expected {expect})"

    def _note_expected(self, station_id):
        """Log a station other than the expected one once per change, and its return."""
        wrong = self.wrong_station()
        if wrong and station_id != self._wrong_logged:
            self._wrong_logged = station_id
            LOG.warning("%s: %s (%s_expect_station)", self.label, wrong, self.prefix)
        elif not wrong and self._wrong_logged is not None:
            self._wrong_logged = None
            LOG.info("%s: the expected station %s again", self.label,
                     self.settings.expect_station)

    def _check_base(self, now):
        """Keep the offsets' reference, our 1005, current; a changed 1005 (a new fixed
        position) restarts the session statistics, since the old offsets were from the
        old position."""
        base = self._get_base()
        ecef = None if base is None else tuple(base["ecef"])
        if ecef is None:
            return               # keep the last known position
        if self._base_ecef is None:
            self._base_ecef = ecef
            return
        if math.dist(ecef, self._base_ecef) > 0.0005:
            self._close_session("our 1005 position changed")
            self._base_ecef = ecef
            self._base_changed = now
            self._session_clock = now    # the statistics start over: so does their age
            self.stats.reset(self._wall())

    def _solution(self, solution, line):
        now = self._clock()
        self._check_base(now)
        enu = None
        if self._base_ecef is not None:
            point = llh_to_ecef(solution["lat"], solution["lon"], solution["height_m"])
            enu = ecef_to_enu(self._base_ecef, point)
            if not all(math.isfinite(v) and abs(v) <= MAX_OFFSET_M for v in enu):
                enu = None
        # rtkrcv's "age" (of the corrections) keeps its name here; the snapshot's age_s
        # is the line's age, as on the measured card.
        self.latest = dict(solution, offset_enu=None if enu is None else list(enu),
                           differential_age_s=solution["age_s"])
        self._latest_time = now
        self.stats.add(now, solution["quality"], enu)
        if self._on_solution is not None:
            self._on_solution(solution, line)

    # ---- status ----

    def state(self):
        """The NTRIP client's state until it streams (connecting, sourcetable, login
        refused, mountpoint not found, unsuitable mountpoint, no data, backoff), then
        rtkrcv's (not available, idle, restarting, starting), then the corrections' and
        the solution's (no corrections, stale, fixed, float, single)."""
        if not self.settings.enabled:
            return "off"
        if self.problem:
            return "not configured"
        if self.client.died:
            return "client stopped"    # cannot happen while the app runs; never silent
        if self.client.state not in ("connected", "off"):
            return self.client.state
        state = self.runner.state()
        if state in ("not available", "idle", "restarting"):
            return state
        running = self.runner.running_s()
        if running is None:
            return "starting"
        now = self._clock()
        if self._corr_last is None:
            if self._stream_any is not None:   # the stream came, without observations
                return "no corrections"
            return "starting"
        if now - self._corr_last > CORRECTIONS_STALE_S:
            return "no corrections"
        if self._latest_time is None:
            return "starting"
        if now - self._latest_time > SOLUTION_STALE_S:
            return "stale"
        return QUALITY_STATES.get(self.latest["quality"], "single")

    # ---- frames ----

    def epoch(self):
        """The epoch the expected offset is computed at: `base_epoch` (the coordinate's
        observation epoch) when set, else today's date as a decimal year."""
        return decimal_year(self._wall()) if self._our_epoch is None else self._our_epoch

    def station_df021(self):
        """The ITRF realization year field of the station's latest 1005/1006, or None.
        Shown, never read as a frame: RTCM 10403 reserves the field."""
        position = self.station.position
        return None if position is None else position["itrf_year"]

    def no_expected(self):
        """Why there is no expected offset, or ""."""
        if not self._station_frame:
            return station_frame_unknown(self.prefix)
        if not self._our_frame:
            return OUR_FRAME_UNKNOWN
        if self._base_ecef is None:
            return "our 1005 position is not known yet"
        return ""

    def expected(self):
        """The computed expected offset (E, N, U metres, the station's frame minus ours
        at our antenna, at `epoch()`), or None with the reason in `no_expected()`."""
        if self.no_expected():
            return None
        try:
            return expected_offset_enu(self._base_ecef, self._station_frame, self.epoch(),
                                       self._our_frame)
        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    def residual(self):
        """Session mean of the fixed epochs minus the expected offset, or None (no fixed
        epoch, or no expected offset)."""
        session = self.stats.session_mean()
        expected = self.expected()
        if session is None or expected is None:
            return None
        return [m - e for m, e in zip(session[0], expected)]

    def baseline_m(self):
        position = self.station.position
        if position is None or self._base_ecef is None:
            return None
        return math.dist(position["ecef"], self._base_ecef)

    def header(self):
        """The solution log's `%` line: what frame each number is in."""
        def ecef(values):
            return "unknown" if values is None else " ".join(f"{v:.4f}" for v in values)

        position = self.station.position
        cors = None if position is None else position["ecef"]
        height = None if position is None else position["antenna_height_m"]
        df021 = self.station_df021()
        expected = self.expected()
        return (f"% gnss_base {self._version}, rtkrcv {self.runner.output.version or 'unknown version'}, "
                f"RTK static against {self.settings.mountpoint}, our 1005 ECEF {ecef(self._base_ecef)}, "
                f"CORS 1005 ECEF {ecef(cors)}"
                f"{'' if height is None else f' antenna height {height:.4f}'}, "
                f"our frame {self._our_frame or 'unknown'} (base_frame) epoch {self.epoch():.2f} "
                f"({'base_epoch' if self._our_epoch is not None else 'today'}), "
                f"station frame {self._station_frame or 'unknown'} "
                f"({'from' if self._station_frame else 'set'} {self.prefix}_frame; "
                f"stream DF021 {'unknown' if df021 is None else df021}), "
                f"expected offset E N U "
                f"{'none' if expected is None else ' '.join(f'{v:+.3f}' for v in expected) + ' m'} "
                f"(computed, station frame minus ours), "
                f"elevation mask {self.settings.elevation_mask} deg, SNR mask "
                f"{self.settings.snr_mask} dB-Hz, time GPST, lines as rtkrcv sent them (llh)")

    def snapshot(self):
        now = self._clock()
        self._check_base(now)   # a changed 1005 counts even while no solution comes
        state = self.state()
        runner = self.runner.snapshot()
        session = self.stats.session_mean()
        expected = self.expected()
        residual = self.residual()
        last_fix_age = None if self.stats.last_fix is None else now - self.stats.last_fix
        session_age = None if self._session_clock is None else now - self._session_clock
        colour, why = verdict(residual, last_fix_age, session_age, self.no_expected())
        wrong = self.wrong_station()
        if wrong:
            colour, why = None, f"{wrong}: no verdict"
        epochs = self.stats.epochs
        baseline = self.baseline_m()
        started = self.stats.started_wall
        next_reset = self.seconds_to_reset()
        show_output = state not in ("off", "not configured", "fixed", "float", "single")
        client = self.client.snapshot()
        position = self.station.position
        changed = self.station_changed
        return {
            "option": self.prefix,
            "enabled": self.settings.enabled,
            "state": state,
            "reason": self.problem
            or (client["reason"] if state in (client["state"], "client stopped") else None)
            or runner["reason"],
            "client": client,
            "cors_feed": self.cors_feed.snapshot(),
            "raw_errors": self.raw_errors,
            "host": self.settings.host or None,
            "port": self.settings.port,
            "mountpoint": self.settings.mountpoint or None,
            "user_set": bool(self.settings.user),
            "password_set": bool(self.settings.password),
            "station_frame": self._station_frame or None,
            "station_frame_source": f"{self.prefix}_frame" if self._station_frame else None,
            "station_id": None if position is None else position["station_id"],
            "expect_station": self.settings.expect_station,
            "station_mismatch": wrong or None,
            "station_changed": None if changed is None else {
                key: changed[key] for key in ("utc", "from_id", "to_id", "moved_m")}
            | {"age_s": round(now - changed["clock"], 1)},
            "send_gga": bool(self.settings.send_gga),
            "gga_source": None if not self.settings.send_gga else
            f"{self.prefix}_gga_position" if self._gga_latlon is not None else "our 1005",
            "station_df021": self.station_df021(),
            "base_frame": self._our_frame or None,
            "epoch": round(self.epoch(), 2),
            "epoch_source": "base_epoch" if self._our_epoch is not None else "today",
            "expected_enu": None if expected is None else [round(v, 4) for v in expected],
            "no_expected": self.no_expected() or None,
            "reset_utc": self.settings.reset_utc,
            "next_reset_s": None if next_reset is None else round(next_reset),
            "base_ecef": None if self._base_ecef is None
            else [round(v, 4) for v in self._base_ecef],
            "base_changed_age_s": None if self._base_changed is None
            else round(now - self._base_changed, 1),
            "baseline_km": None if baseline is None else round(baseline / 1000, 3),
            "corrections": self.station.snapshot(),
            "solution": None if self.latest is None else dict(
                self.latest, age_s=round(now - self._latest_time, 1),
                offset_enu=None if self.latest["offset_enu"] is None
                else [round(v, 4) for v in self.latest["offset_enu"]]),
            "session": {
                "started_utc": _utc(started),
                "epochs": epochs,
                "counts": dict(self.stats.counts),
                "fixed_share": None if not epochs
                else round(self.stats.counts["fixed"] / epochs, 3),
                "mean_enu": None if session is None else [round(v, 4) for v in session[0]],
                "std_enu": None if session is None else [round(v, 4) for v in session[1]],
                "first_fix_age_s": None if self.stats.first_fix is None
                else round(now - self.stats.first_fix, 1),
                "last_fix_age_s": None if last_fix_age is None else round(last_fix_age, 1),
            },
            "recent": self.stats.recent(now),
            "previous_session": self.previous,
            "residual_enu": None if residual is None else [round(v, 4) for v in residual],
            "verdict": colour,
            "verdict_reason": why,
            "sessions": self.sessions,
            "resets": self.resets,
            "gap_restarts": self.gap_restarts,
            "station_changes": self.station_changes,
            **{key: runner[key] for key in (
                "pid", "running_s", "last_solution_age_s", "retry_in_s", "last_exit",
                "stop_exit_code", "restarts", "restarts_today", "planned_restarts",
                "cpu_percent", "rss_bytes", "rtkrcv_version", "elevation_mask", "snr_mask",
                "feed", "solutions", "ephemerides", "error")},
            "output": self.runner.output.snapshot() if show_output else [],
        }


def compare_verdict(difference):
    """("good" | "warn" | "bad", why) for reference 2 minus reference 1 (E, N, U metres)
    when both are in the same declared frame: green within 3 cm horizontal and 6 cm up,
    amber within 10 cm, red beyond."""
    horizontal, up = math.hypot(difference[0], difference[1]), abs(difference[2])
    if horizontal <= COMPARE_GOOD_HORIZONTAL_M and up <= COMPARE_GOOD_UP_M:
        return "good", (f"within {COMPARE_GOOD_HORIZONTAL_M * 100:.0f} cm horizontal and "
                        f"{COMPARE_GOOD_UP_M * 100:.0f} cm up")
    if horizontal <= COMPARE_WARN_M and up <= COMPARE_WARN_M:
        return "warn", f"within {COMPARE_WARN_M * 100:.0f} cm"
    return "bad", f"more than {COMPARE_WARN_M * 100:.0f} cm apart"


def _session_station(check):
    """The station ID the check's session is relative to, else its latest one, or None."""
    if check._cors_id is not None:
        return check._cors_id
    position = check.station.position
    return None if position is None else position["station_id"]


def compare(first, second):
    """The "Reference comparison" card: the two checks' session means of the fixed
    epochs (each the station's frame minus ours, at our antenna) and the second minus
    the first, which our coordinate cancels out of, with both station IDs. Shown only
    when both have a session mean, from the same 1005 of ours. A colour only when both
    stations' frames are declared and the same (otherwise the raw difference and why
    there is no residual: the frames are never guessed, and no transformation is
    applied here), and only while it is current: both NTRIP clients streaming, each
    check's last fix at most COMPARE_FRESH_S old, and each check's rtkrcv running with
    no restart pending (else `stale`, with each side's last-fix age), and neither check
    on a wrong station (`wrong_station`)."""
    result = {"shown": False, "reason": None, "first": first.prefix, "second": second.prefix,
              "first_station_id": _session_station(first),
              "second_station_id": _session_station(second),
              "first_minus_ours": None, "second_minus_ours": None,
              "second_minus_first": None, "first_frame": None, "second_frame": None,
              "first_fixed": None, "second_fixed": None, "residual": False,
              "first_last_fix_age_s": None, "second_last_fix_age_s": None,
              "stale": False, "stale_reason": None, "station_mismatch": None,
              "verdict": None, "verdict_reason": None}
    for check in (first, second):
        if not check.settings.enabled or check.problem:
            result["reason"] = f"{check.prefix} is off or not configured"
            return result
    means = [check.stats.session_mean() for check in (first, second)]
    if any(mean is None for mean in means):
        missing = [c.prefix for c, m in zip((first, second), means) if m is None]
        result["reason"] = f"no session mean of fixed epochs yet: {', '.join(missing)}"
        return result
    bases = [check._base_ecef for check in (first, second)]
    if None in bases or math.dist(*bases) > SAME_BASE_M:
        result["reason"] = "the two sessions are not from the same 1005 position of ours"
        return result
    one, two = means[0][0], means[1][0]
    difference = [b - a for a, b in zip(one, two)]
    frames = [check._station_frame or None for check in (first, second)]
    result.update(shown=True, first_minus_ours=[round(v, 4) for v in one],
                  second_minus_ours=[round(v, 4) for v in two],
                  second_minus_first=[round(v, 4) for v in difference],
                  first_frame=frames[0], second_frame=frames[1],
                  first_fixed=first.stats.fixed_count, second_fixed=second.stats.fixed_count)
    now = first._clock()
    ages = [None if c.stats.last_fix is None else now - c.stats.last_fix
            for c in (first, second)]
    result.update(first_last_fix_age_s=None if ages[0] is None else round(ages[0], 1),
                  second_last_fix_age_s=None if ages[1] is None else round(ages[1], 1))
    stale = []
    for check, age in zip((first, second), ages):
        if check.client.died or check.client.state != "connected":
            stale.append(f"{check.prefix} is not streaming ({check.client.state})")
        elif check.runner.restart_pending() or check.runner.running_s() is None:
            # A station switch (or a gap, or the daily reset) asked for a new session:
            # until rtkrcv has restarted, the session mean is the old run's, while
            # the station ID may already be the new one.
            stale.append(f"{check.prefix}'s rtkrcv is restarting (a new session)")
        elif age is None or age > COMPARE_FRESH_S:
            stale.append(f"{check.prefix} has no fix in the last "
                         f"{COMPARE_FRESH_S / 60:.0f} min")
    wrong = [f"{c.prefix}: {c.wrong_station()}" for c in (first, second) if c.wrong_station()]
    unknown = [c.prefix for c, f in zip((first, second), frames) if not f]
    if wrong:
        result.update(station_mismatch="; ".join(wrong),
                      verdict_reason="no verdict: " + "; ".join(wrong))
    elif stale:
        result.update(stale=True, stale_reason="; ".join(stale),
                      verdict_reason="stale, no verdict: " + "; ".join(stale))
    elif unknown:
        result["verdict_reason"] = ("no residual: station frame unknown: set "
                                    + ", ".join(f"{p}_frame" for p in unknown))
    elif frames[0] != frames[1]:
        result["verdict_reason"] = (f"no residual: the frames differ ({frames[0]}, "
                                    f"{frames[1]}), so the difference includes the "
                                    "transformation between them")
    else:
        colour, why = compare_verdict(difference)
        result.update(residual=True, verdict=colour, verdict_reason=f"{why} ({frames[0]})")
    return result
