"""Track receiver state from parsed frames, including the position and its recent history.

Positions are served only behind the Home Assistant login (Ingress), never logged here.
"""
import math
import secrets
import statistics
import time
from collections import Counter, deque

from .frames import rtcm_type
from .rtcm import decode_1005, decode_msm, ecef_to_llh, signal_name

FIX_NAMES = {0: "No fix", 1: "Standalone", 2: "Differential", 3: "PPS", 4: "RTK fixed",
             5: "RTK float", 6: "Dead reckoning", 7: "Manual input (fixed position)",
             8: "Simulator"}
# GGA qualities that are measured positions. 7 (manual input) is what a receiver in
# fixed base mode reports: its configured position repeated, not a measurement, so it
# is shown but kept out of the history; so are 0 (no fix) and 8 (simulator).
MEASURED_QUALITIES = frozenset({1, 2, 3, 4, 5, 6})
CONSTELLATIONS = {"GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "GB": "BeiDou",
                  "GQ": "QZSS", "GI": "NavIC"}
# PQTMSVINSTATUS <Valid>: 0 also when PQTMCFGSVIN mode is 0, or a new setting has not
# taken effect yet (this firmware applies it only after a restart).
SVIN_STATES = {0: "Not running", 1: "In progress", 2: "Finished (valid)"}
SIGNAL_EXPIRY_S = 10
BASE_STALE_S = 10          # a base position (1005/1006) older than this is stale,
BASE_STALE_FACTOR = 3      # or than this many times the recent interval between them,
BASE_STALE_MAX_S = 120     # but never more than this
BASE_INTERVALS = 5         # recent 1005/1006 intervals kept; their median is the interval
BASE_SAME_EPOCH_S = 0.5    # a 1005 and a 1006 closer than this are one epoch, not an interval
RECENT_S = 10              # GGA or RTCM this recent tells why there is no measured position
NO_MEASURED_BASE_MODE = ("No measured position: the receiver is in base mode. "
                         "A live position from the observations is planned.")
NO_MEASURED_NO_FIX = ("No measured position: the latest GGA has no usable position fix "
                      "(no fix, or a position that was rejected).")
NO_MEASURED_FIXED = ("No measured position: the receiver reports its fixed base position "
                     "(GGA quality 7, manual input), which is not measured, so it is not "
                     "plotted. A live position from the observations is planned.")
RATE_WINDOW_S = 10
GST_EXPIRY_S = 5           # sigmas older than this are not shown with the fix
TRACK_WINDOW_S = 2 * 3600  # position history: one sample per second for 2 hours
TRACK_POINTS = 7200
RECENTRE_M = 1000          # a fix this far from the origin is held back as an outlier,
RECENTRE_SAMPLES = 30      # unless this many in a row agree: then the history restarts there
RECENTRE_AGREE_M = 50      # held fixes agree if all are this close to their median
MAX_HEIGHT_M = 100_000     # altitude, geoid separation or height beyond this is corrupt
MAX_ECEF_M = 10_000_000     # an ECEF coordinate beyond this is corrupt
MAX_CURSOR_DIGITS = 20     # a longer sequence in a cursor is not one this app sent

# WGS84
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_HEMISPHERES = {"N": (1, 90), "S": (-1, 90), "E": (1, 180), "W": (-1, 180)}


def _number(text, kind=float):
    """The field as a number, or None if it is empty, malformed or not finite."""
    try:
        value = kind(text)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def parse_coordinate(value, hemisphere):
    """NMEA ddmm.mmmm (latitude) or dddmm.mmmm (longitude) and N/S/E/W to signed
    decimal degrees. Returns None for empty or malformed fields."""
    sign, limit = _HEMISPHERES.get(hemisphere, (None, None))
    if sign is None or not value:
        return None
    whole, _, fraction = value.partition(".")
    if len(whole) < 3 or not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    minutes = float(whole[-2:] + "." + (fraction or "0"))
    if minutes >= 60:
        return None
    degrees = int(whole[:-2]) + minutes / 60
    if degrees > limit:
        return None
    return sign * degrees if degrees else 0.0


class LocalFrame:
    """East/north/up offsets in metres around an origin, by a local tangent plane
    approximation: latitude and longitude differences are scaled by the WGS84
    meridian and prime-vertical radii at the origin, and up is the difference in
    ellipsoidal height. Within a kilometre or so of the origin this is accurate to
    well under a millimetre horizontally, far better than the metre scale needed here."""

    def __init__(self, lat, lon, height):
        self.lat, self.lon, self.height = lat, lon, height
        sin_lat = math.sin(math.radians(lat))
        w = math.sqrt(1 - _E2 * sin_lat * sin_lat)
        self._north = math.radians(1) * _A * (1 - _E2) / w ** 3  # metres per degree
        self._east = math.radians(1) * _A / w * math.cos(math.radians(lat))

    def to_enu(self, lat, lon, height):
        d_lon = (lon - self.lon + 180) % 360 - 180
        return d_lon * self._east, (lat - self.lat) * self._north, height - self.height

    def from_enu(self, east, north, up):
        lon = self.lon + (east / self._east if self._east else 0.0)
        lon = (lon + 180) % 360 - 180
        return self.lat + north / self._north, lon, self.height + up


class Status:
    """Every age, rate and window here uses `clock`, a monotonic clock by default,
    so a step in the wall clock cannot make an age negative or prune the wrong samples."""

    def __init__(self, clock=None):
        self._clock = time.monotonic if clock is None else clock
        self.started = self._clock()
        self.connected = False
        self.last_error = ""
        self.last_data = None
        self.total_bytes = 0
        self.fix = {}
        self.survey_in = {}
        self.antenna = ""
        self.nmea_counts = Counter()
        self.rtcm_counts = Counter()
        self.rtcm_last = {}
        self.latest_text = deque(maxlen=20)
        self._signals = {}
        # From RTCM: (system, satellite, signal id) -> (cnr, msm type, seen)
        self._msm_cells = {}
        self._msm_last = {}        # system -> (message number, seen)
        self.base = {}             # from 1005/1006
        self._base_time = None
        self._base_arrivals = deque(maxlen=BASE_INTERVALS + 1)  # 1005/1006 epochs
        self._survey_mean = None   # (x, y, z) from PQTMSVINSTATUS, or None
        self.rtcm_decode_errors = 0
        self._rate = deque()
        self._fix_time = None
        self._survey_time = None
        self._gst = None
        self._gst_time = None
        # Position history: (time, sequence, east, north, up, quality) in metres
        # from `_anchor`, the history's origin: the first good fix since the
        # history was last empty, or the last fix of a sustained move.
        self._anchor = None
        self._history_id = None    # changes whenever the origin does
        self._track = deque(maxlen=TRACK_POINTS)
        self._track_second = None
        self._sequence = 0
        # Fixes far from the origin, held back: (time, lat, lon, height, quality)
        self._far = []
        self.outliers_dropped = 0

    def add_bytes(self, count):
        now = self._clock()
        self.total_bytes += count
        self.last_data = now
        self._rate.append((now, count))
        self._prune_rate(now)

    def _prune_rate(self, now):
        while self._rate and now - self._rate[0][0] > RATE_WINDOW_S:
            self._rate.popleft()

    def add_frame(self, kind, payload):
        if kind == "rtcm":
            number = rtcm_type(payload)
            self.rtcm_counts[number] += 1
            self.rtcm_last[number] = self._clock()
            if number in (1005, 1006):
                self._note_base_arrival(self.rtcm_last[number])
            try:
                if number in (1005, 1006):
                    self._on_1005(decode_1005(payload))
                elif number // 10 in (107, 108, 109, 111, 112, 113) and 1 <= number % 10 <= 7:
                    self._on_msm(decode_msm(payload))
            except ValueError:
                self.rtcm_decode_errors += 1
            return
        fields = payload.split(",")
        name = fields[0]
        sentence = name if name.startswith("PQTM") else name[2:]
        self.nmea_counts[sentence] += 1
        handler = getattr(self, "_on_" + sentence.lower(), None)
        if handler:
            handler(name, fields)

    def _on_gga(self, _name, fields):
        if len(fields) < 9:
            return
        quality = _number(fields[6], int)
        lat = lon = height = None
        if quality is not None and quality > 0:  # no position without a fix
            lat = parse_coordinate(fields[2], fields[3])
            lon = parse_coordinate(fields[4], fields[5])
            altitude = _number(fields[9]) if len(fields) > 9 else None
            # Height above the ellipsoid is altitude plus geoid separation. An
            # empty separation is taken as 0, as receivers without a geoid model
            # send; a malformed or non-finite one means no position.
            text = fields[11] if len(fields) > 11 else ""
            separation = _number(text) if text else 0.0
            if None not in (lat, lon, altitude, separation):
                height = altitude + separation
            # Implausible values, finite or not, mean no position: they could
            # overflow the history's median.
            if height is None or not all(
                    abs(value) <= MAX_HEIGHT_M for value in (altitude, separation, height)):
                height = None
                lat = lon = None
        # Measured only if the position itself was accepted, not by quality alone.
        measured = quality in MEASURED_QUALITIES and height is not None
        self.fix = {
            "utc": fields[1],
            "quality": quality,
            "name": FIX_NAMES.get(quality, "Unknown"),
            "measured": measured,
            "satellites": _number(fields[7], int),
            "hdop": _number(fields[8]),
            "lat": None if lat is None else round(lat, 9),
            "lon": None if lon is None else round(lon, 9),
            "height_m": None if height is None else round(height, 4),
        }
        self._fix_time = self._clock()
        if measured:
            self._add_track(self._fix_time, lat, lon, height, quality)

    def _on_gst(self, _name, fields):
        if len(fields) < 9:
            return
        self._gst = tuple(_number(value) for value in fields[6:9])
        self._gst_time = self._clock()

    def _add_track(self, now, lat, lon, height, quality):
        """Keep at most one fix per clock second. A fix more than RECENTRE_M from
        the origin is held back, not stored. If RECENTRE_SAMPLES of them in a row
        agree with each other, all within RECENTRE_AGREE_M of their median, the
        receiver has moved, and the history restarts with them; otherwise they are
        dropped and counted as outliers."""
        second = math.floor(now)
        if second == self._track_second:
            return
        self._track_second = second
        self._prune_track(now)
        if self._anchor is None:
            self._start_history(lat, lon, height)
        east, north, up = self._anchor.to_enu(lat, lon, height)
        if math.hypot(east, north) <= RECENTRE_M:
            self._drop_far()
            self._store(now, east, north, up, quality)
            return
        self._far.append((now, lat, lon, height, quality))
        while not self._far_agree():  # the oldest held fixes disagree: no move
            del self._far[0]
            self.outliers_dropped += 1
        if len(self._far) >= RECENTRE_SAMPLES:  # moved: start a new history here
            far, self._far = self._far, []
            self._track.clear()
            self._start_history(*far[-1][1:4])
            for t, *position, q in far:
                self._store(t, *self._anchor.to_enu(*position), q)

    def _far_agree(self):
        newest = LocalFrame(*self._far[-1][1:4])
        offsets = [newest.to_enu(*fix[1:4])[:2] for fix in self._far]
        centre = [statistics.median(axis) for axis in zip(*offsets)]
        return all(math.hypot(e - centre[0], n - centre[1]) <= RECENTRE_AGREE_M
                   for e, n in offsets)

    def _start_history(self, lat, lon, height):
        self._anchor = LocalFrame(lat, lon, height)
        self._history_id = secrets.token_hex(6)

    def _store(self, now, east, north, up, quality):
        if not all(math.isfinite(value) for value in (east, north, up)):
            self.outliers_dropped += 1  # finite heights whose difference overflows
            return
        self._sequence += 1
        self._track.append((now, self._sequence, east, north, up, quality))

    def _drop_far(self):
        self.outliers_dropped += len(self._far)
        self._far.clear()

    def _prune_track(self, now):
        while self._track and now - self._track[0][0] > TRACK_WINDOW_S:
            self._track.popleft()
        if not self._track and self._anchor is not None:
            self._anchor = None
            self._drop_far()

    def _resume_after(self, since):
        """The sequence number in a cursor from an earlier response, if it belongs
        to the current history, otherwise None: the caller needs everything."""
        history, _, sequence = (since or "").rpartition(":")
        if not history or history != self._history_id:
            return None
        if not (len(sequence) <= MAX_CURSOR_DIGITS and sequence.isascii()
                and sequence.isdigit()):
            return None
        sequence = int(sequence)
        return sequence if sequence <= self._track[-1][1] else None

    def track(self, since=None):
        """Recent positions as offsets from a reference, the per-axis median of the
        stored samples. The reference moves only slowly as samples come and go, so
        drift shows up as a trend around it, and a few bad fixes do not shift it.

        `since` is the `cursor` of an earlier response. If it belongs to the current
        history, only newer points are returned and `full` is false; otherwise, for
        example after a restart or a move, all points are returned. Adding
        `reference_offset` (the reference from the history's fixed origin) to a
        point gives its offset from that origin, which does not change between calls.
        `oldest_age_s` is the age of the oldest point still kept, so a caller can drop
        older points it kept itself.

        If the reference or an offset is not finite, which only corrupt heights could
        cause, the response has no reference and no points."""
        now = self._clock()
        self._prune_track(now)
        result = {"window_s": TRACK_WINDOW_S, "max_points": TRACK_POINTS,
                  "fields": ["age_s", "e", "n", "u", "quality"],
                  "reference": None, "reference_offset": None, "cursor": None, "full": True,
                  "outliers_dropped": self.outliers_dropped, "oldest_age_s": None, "points": []}
        result["no_measured_reason"] = self.no_measured_reason(now)
        if not self._track:
            return result
        mid = [statistics.median(sample[axis] for sample in self._track) for axis in (2, 3, 4)]
        lat, lon, height = self._anchor.from_enu(*mid)
        if not all(math.isfinite(value) for value in (*mid, lat, lon, height)):
            return result
        empty = dict(result)
        result["reference"] = {"lat": round(lat, 9), "lon": round(lon, 9),
                               "height_m": round(height, 4)}
        result["reference_offset"] = [round(value, 4) for value in mid]
        result["cursor"] = f"{self._history_id}:{self._track[-1][1]}"
        result["oldest_age_s"] = round(now - self._track[0][0], 1)
        after = self._resume_after(since)
        samples = self._track
        if after is not None:
            result["full"] = False
            newer = []
            for sample in reversed(self._track):
                if sample[1] <= after:
                    break
                newer.append(sample)
            samples = reversed(newer)
        result["points"] = [
            [round(now - t, 1), round(e - mid[0], 4), round(n - mid[1], 4),
             round(u - mid[2], 4), quality]
            for t, _, e, n, u, quality in samples]
        if not all(math.isfinite(value) for point in result["points"] for value in point[1:4]):
            return empty
        return result

    def no_measured_reason(self, now=None):
        """Why there is no measured position now, for the drift plot and the map, or
        None if there is one or nothing says why: a recent GGA of quality 7 (fixed base
        position); a recent 1005/1006 (base mode, which may send no GGA) with no recent
        measured GGA; or a recent GGA without an accepted measured position (no fix).
        Other RTCM, such as MSM from a rover, does not mean base mode."""
        now = self._clock() if now is None else now
        fix_recent = self._fix_time is not None and now - self._fix_time <= RECENT_S
        if fix_recent:
            if self.fix.get("measured"):
                return None
            if self.fix.get("quality") == 7:
                return NO_MEASURED_FIXED
        base_seen = max((self.rtcm_last[n] for n in (1005, 1006) if n in self.rtcm_last),
                        default=None)
        if base_seen is not None and now - base_seen <= max(RECENT_S, self.base_stale_s()):
            return NO_MEASURED_BASE_MODE
        if fix_recent:
            return NO_MEASURED_NO_FIX
        return None

    def _note_base_arrival(self, now):
        if self._base_arrivals and now - self._base_arrivals[-1] < BASE_SAME_EPOCH_S:
            return  # the 1006 (or 1005) of an epoch already counted
        self._base_arrivals.append(now)

    def base_stale_s(self):
        """Age at which the base position is stale: BASE_STALE_S, or BASE_STALE_FACTOR
        times the median recent interval between 1005/1006 epochs if that is longer, so
        a receiver sending them every 30 s is not stale between messages, capped at
        BASE_STALE_MAX_S."""
        times = list(self._base_arrivals)
        intervals = [b - a for a, b in zip(times, times[1:])]
        if not intervals:
            return BASE_STALE_S
        return min(max(BASE_STALE_S, BASE_STALE_FACTOR * statistics.median(intervals)),
                   BASE_STALE_MAX_S)

    def _on_1005(self, message):
        x, y, z = message["ecef"]
        lat, lon, height = ecef_to_llh(x, y, z)
        if not (all(math.isfinite(v) for v in (lat, lon, height))
                and abs(height) <= MAX_HEIGHT_M):
            self.rtcm_decode_errors += 1  # zero or corrupt: no base position
            return
        self.base = {"message": message["message"], "station_id": message["station_id"],
                     "lat": round(lat, 9), "lon": round(lon, 9), "height_m": round(height, 4),
                     "ecef": [round(v, 4) for v in (x, y, z)],
                     "antenna_height_m": None if message["antenna_height_m"] is None
                     else round(message["antenna_height_m"], 4)}
        self._base_time = self._clock()

    def _on_msm(self, message):
        now = self._clock()
        system = message["system"]
        self._msm_last[system] = (message["message"], now)
        for satellite, signal, cnr in message["cells"]:
            self._msm_cells[(system, satellite, signal)] = (cnr, message["msm"], now)

    def _on_gsv(self, name, fields):
        talker = name[:2]
        has_signal = (len(fields) - 4) % 4 == 1
        signal = fields[-1] if has_signal else "?"
        now = self._clock()
        end = len(fields) - (1 if has_signal else 0)
        for index in range(4, end - 3, 4):
            cn0 = _number(fields[index + 3], int)
            if fields[index] and cn0:
                self._signals[(talker, signal, fields[index])] = (cn0, now)

    def _on_pqtmsvinstatus(self, _name, fields):
        if len(fields) < 7:
            return
        valid = _number(fields[3], int)
        state = SVIN_STATES.get(valid, "Unknown")
        mean = tuple(_number(v) for v in fields[8:11]) if len(fields) > 10 else ()
        # A mean of zeros means none yet; one far from the Earth is corrupt.
        plausible = (len(mean) == 3 and None not in mean and any(mean)
                     and all(abs(v) <= MAX_ECEF_M for v in mean))
        self._survey_mean = mean if plausible else None
        self.survey_in = {
            "state": state, "valid": valid,
            "observations": _number(fields[6], int),
            "target": _number(fields[7], int) if len(fields) > 7 else None,
            "accuracy_m": _number(fields[11]) if len(fields) > 11 else None,
        }
        self._survey_time = self._clock()

    def _on_pqtmantennastatus(self, _name, fields):
        self.antenna = ",".join(fields[1:])

    def _on_txt(self, _name, fields):
        self.latest_text.append(",".join(fields[1:]))

    def _on_pqtmtxt(self, name, fields):
        self._on_txt(name, fields)

    def survey_mean(self):
        """The latest survey-in mean: ECEF, state, progress and age, or None."""
        if not self.survey_in:
            return None
        survey = self.survey_in
        return {"ecef": self._survey_mean, "state": survey["state"], "valid": survey["valid"],
                "observations": survey["observations"], "target": survey["target"],
                "accuracy_m": survey["accuracy_m"], "age_s": self._clock() - self._survey_time}

    def _prune_msm(self, now):
        for key in [k for k, v in self._msm_cells.items() if now - v[2] > SIGNAL_EXPIRY_S]:
            del self._msm_cells[key]

    def signals(self):
        """Signal strengths per constellation and signal. GSV is used for a
        constellation while it is fresh; otherwise RTCM MSM, for example in base mode,
        when the receiver sends no NMEA."""
        now = self._clock()
        for key in [k for k, v in self._signals.items() if now - v[1] > SIGNAL_EXPIRY_S]:
            del self._signals[key]
        self._prune_msm(now)
        grouped = {}
        for (talker, signal, _satellite), (cn0, _seen) in self._signals.items():
            key = (CONSTELLATIONS.get(talker, talker), signal, "GSV")
            grouped.setdefault(key, []).append(cn0)
        from_gsv = {constellation for constellation, _, _ in grouped}
        for (system, _satellite, signal), (cnr, msm, _seen) in self._msm_cells.items():
            if system in from_gsv or cnr is None:
                continue
            key = (system, signal_name(system, signal), f"RTCM MSM{msm}")
            grouped.setdefault(key, []).append(round(cnr, 1))
        result = []
        for (constellation, signal, source), values in sorted(grouped.items()):
            values.sort(reverse=True)
            result.append({
                "constellation": constellation,
                "signal": signal,
                "satellites": len(values),
                "strongest": values[0],
                "median": values[len(values) // 2],
                "source": source,
            })
        return result

    def msm_summary(self):
        """Satellites and signals per constellation in recent MSM messages."""
        now = self._clock()
        self._prune_msm(now)
        satellites, signals = {}, {}
        for system, satellite, signal in self._msm_cells:
            satellites.setdefault(system, set()).add(satellite)
            signals.setdefault(system, set()).add(signal)
        return {system: {"satellites": len(satellites[system]),
                         "signals": [signal_name(system, s) for s in sorted(signals[system])],
                         "message": self._msm_last[system][0],
                         "age_s": round(now - self._msm_last[system][1], 1)}
                for system in sorted(satellites)}

    def snapshot(self):
        now = self._clock()
        self._prune_rate(now)
        # Average over the whole window (or the uptime, if shorter), so a burst
        # after a quiet spell is not divided by a few seconds only.
        window = max(min(now - self.started, RATE_WINDOW_S), 1.0)

        def aged(values, seen):
            if not values:
                return {}
            return dict(values, age_s=round(now - seen, 1))

        fix = aged(self.fix, self._fix_time)
        if fix:
            fresh = self._gst is not None and now - self._gst_time <= GST_EXPIRY_S
            sigmas = self._gst if fresh else (None, None, None)
            fix.update(zip(("lat_sigma_m", "lon_sigma_m", "height_sigma_m"), sigmas))

        return {
            "connected": self.connected,
            "last_error": self.last_error,
            "data_age_s": None if self.last_data is None else round(now - self.last_data, 1),
            "uptime_s": round(now - self.started),
            "bytes_per_s": round(sum(c for _, c in self._rate) / window),
            "total_bytes": self.total_bytes,
            "fix": fix,
            "survey_in": aged(self.survey_in, self._survey_time),
            "survey_mean": None if self._survey_mean is None else dict(
                zip(("lat", "lon", "height_m"),
                    (round(v, r) for v, r in zip(ecef_to_llh(*self._survey_mean), (9, 9, 4)))),
                ecef=[round(v, 4) for v in self._survey_mean]),
            "base_position": dict(aged(self.base, self._base_time),
                                  stale=now - self._base_time > self.base_stale_s(),
                                  stale_after_s=round(self.base_stale_s(), 1))
            if self.base else {},
            "no_measured_reason": self.no_measured_reason(now),
            "msm": self.msm_summary(),
            "rtcm_decode_errors": self.rtcm_decode_errors,
            "antenna": self.antenna,
            "signals": self.signals(),
            "nmea": dict(sorted(self.nmea_counts.items())),
            "rtcm": {
                str(number): {"count": count, "age_s": round(now - self.rtcm_last[number], 1)}
                for number, count in sorted(self.rtcm_counts.items())
            },
            "messages": list(self.latest_text),
        }
