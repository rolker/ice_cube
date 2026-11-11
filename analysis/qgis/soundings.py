"""Build a `soundings` table: every ground height of a season with an honest sigma.

    python3 analysis/qgis/soundings.py --config config.toml [--gpkg ...] [--csv out.csv]

Reads the sources listed under `[soundings]` in the site config (see config.example.toml)
and writes one row per sounding into the season GeoPackage (table `soundings`, replaced
on every run: it is derived data with no user columns) and optionally a CSV with the same
columns. icecube reads either (`reader = "soundings"`).

Sources (`[[soundings.sources]]`, `type`):
  logger         Ice Cube Logger captures from the gpkg `captures` table (surface ->
                 ground, ref and control -> anchor, object -> object) plus, when the raw
                 session folders or zips are given (`session_paths`), POLE STOPS found in
                 the 4 Hz GGA of gps.ubx, checked for plumb with the phone's gravity sensor.
  swmaps         SW Maps points (CSV export) with the receiver's NMEA log (.ubx with GGA
                 and GST) for fix age, correction age, base station and pole stops.
  swmaps_points  SW Maps points without a log (e.g. a survey on another network),
                 optionally moved by a measured frame shift.

Frame. Rows are in the gpkg's local frame (exact ENU about the origin in icecube_meta,
x east, y north, z up). An RTK rover reports 1005 + baseline, so a position taken while an
older base coordinate was broadcast is moved by (current base - base then), from the
`base_history` CSV (start_utc, end_utc, lat, lon, h of each broadcast coordinate); the
current base is the gpkg's (icecube_meta base_lat/lon/h). Times outside every history row
are taken as on the current base.

Per-sounding vertical sigma (m), all terms independent:

    sigma_z^2 = rtk^2 + age^2 + settle^2 + pole^2 + ground^2
    rtk    = max(floor, 1 s scatter, GST height sigma)
    age    = age_rate * max(0, correction age - age_free_s)
    settle = settle_extra * (1 - fix_age / settle_s)^2 for a capture taken fix_age seconds
             after its RTK fix started (logger captures only; SW Maps points and pole stops
             inside settle_s are dropped instead)
    pole   = pole_m * (1 - cos(tilt)) + tip_sink     (tilt: bubble, IMU-checked or unknown)
    ground = representativeness of one shot of lawn (0 for a ref on a mark)
    sigma_xy^2 = max(floor_h, scatter_h, GST_h)^2 + (pole_m * sin(tilt))^2

The slowly varying RTK wander is not in sigma_z: it is the pass bias icecube solves, and
each row carries its `pass_id` and the source's `prior_m` (the expected pass wander).

Flags (comma-separated; `use` = 1 for rows icecube should take): float, other_station,
suspect_jump (the fix began with a jump over suspect_jump_m: false fixes), settle,
listed:<reason> (config list of known-bad points), no_fix_info, imu_unstable, not_plumb,
no_imu, no_plumb_ref, plumb_unverified (a stop without a passed IMU plumb check, left
out while [soundings.stops] require_plumb = true: on the Nov 4 walk such stops read a median
4 cm low against nearby points, a leaning pole), dup_capture (a stop that is a capture),
local_outlier, ppk (position from post-processing), ppk_disagree.

Nothing here prints coordinates. Standard library only (like the import), plus nothing.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import io
import math
import re
import sqlite3
import statistics as st
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import reduce
from operator import xor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gpkg_writer as gw  # noqa: E402
from local_frame import LocalFrame, geodetic_to_ecef  # noqa: E402
from logger_session import find_sessions, read_chunks, read_events, _nmea_deg  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "table": "soundings",
    "settle_s": 90.0,          # fix age below which a point is not trusted (Nov 3/4: 10-30 cm for 25-75 s)
    "suspect_jump_m": 1.0,     # float-to-fix jump that marks a false fix (field: 1.3-2.9 m)
    "max_pass_s": 600.0,       # a pass (one RTK bias) is cut after this long
    "gap_s": 120.0,            # points-only sources: a new pass after this gap
    "jump_m": 3.0,             # ... or this horizontal jump
    "anchor_radius_m": 0.30,   # refs/controls within this share an anchor (as refs.site)
    "anchor_snap_m": 0.10,     # another sounding this close to an anchor (and within anchor_dz_m) joins it
    "anchor_dz_m": 0.10,
    "sigma": {"floor": 0.01, "floor_h": 0.01, "age_free_s": 2.0, "age_rate": 0.001,
              "settle_extra": 0.08, "tip_sink": 0.01, "ground": 0.015,
              "tilt_bubble_deg": 1.0, "tilt_imu_deg": 2.0, "tilt_unknown_deg": 5.0},
    "stops": {"window_s": 2.0, "max_h_m": 0.03, "max_sd_z_m": 0.012, "max_gap_s": 0.6,
              "imu_stable_deg": 0.5, "imu_plumb_deg": 2.0, "capture_margin_s": 1.0,
              "outlier_radius_m": 2.0, "outlier_min_dz_m": 0.10, "outlier_k": 3.0,
              "require_plumb": True},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


# ----------------------------------------------------------------------------- time

def iso(t):
    d = datetime.fromtimestamp(t, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 10000:02d}Z"


def parse_iso(s):
    s = s.strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


ZONES = {"UTC": 0, "GMT": 0, "Z": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7,
         "MDT": -6, "PST": -8, "PDT": -7, "AST": -4, "ADT": -3}


def parse_swmaps_time(s, utc_offset_h=None):
    """'11/04/2026 15:49:38.750 EST' (also with AM/PM) -> Unix seconds. The zone label gives
    the UTC offset; `utc_offset_h` overrides it. None when it does not parse."""
    parts = s.strip().split()
    if len(parts) < 2:
        return None
    rest = [p.upper() for p in parts[2:]]
    ampm = next((p for p in rest if p in ("AM", "PM")), "")
    zone = next((p for p in rest if p not in ("AM", "PM")), "")
    off = utc_offset_h if utc_offset_h is not None else ZONES.get(zone)
    if off is None:
        return None
    txt = parts[0] + " " + parts[1] + (" " + ampm if ampm else "")
    for day in ("%m/%d/%Y", "%Y-%m-%d"):
        for sec in (":%S.%f", ":%S"):
            fmt = f"{day} {'%I' if ampm else '%H'}:%M{sec}{' %p' if ampm else ''}"
            try:
                d = datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc)
                return d.timestamp() - off * 3600
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------------- NMEA

NMEA_RE = re.compile(rb"\$G[A-Z](GGA|GST),[^$\r\n\xb5]*\*([0-9A-Fa-f]{2})")


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Epochs:
    """A receiver's epochs, time order. Arrays are lists of equal length.
    t: Unix s; fix: GGA quality; lat/lon/h: antenna, ellipsoidal; age: correction age (s);
    station: reference station id (-1 unknown); gst_h, gst_u: GST sigmas (m, None if absent);
    off: byte offset in the log; ns: phone elapsed ns (None without chunks).
    Filled later: x, y, za (antenna, local frame), pole, z (ground), stretch, fix_age, jump,
    suspect."""
    t: list = field(default_factory=list)
    fix: list = field(default_factory=list)
    lat: list = field(default_factory=list)
    lon: list = field(default_factory=list)
    h: list = field(default_factory=list)
    age: list = field(default_factory=list)
    station: list = field(default_factory=list)
    gst_h: list = field(default_factory=list)
    gst_u: list = field(default_factory=list)
    off: list = field(default_factory=list)
    ns: list = field(default_factory=list)
    x: list = field(default_factory=list)
    y: list = field(default_factory=list)
    za: list = field(default_factory=list)
    pole: list = field(default_factory=list)
    z: list = field(default_factory=list)
    stretch: list = field(default_factory=list)
    fix_age: list = field(default_factory=list)
    jump: list = field(default_factory=list)
    suspect: list = field(default_factory=list)

    def __len__(self):
        return len(self.t)

    def index_at(self, t, tol=0.6):
        """Index of the epoch nearest t (within tol s), or None."""
        k = bisect.bisect_left(self.t, t)
        best = None
        for j in (k - 1, k):
            if 0 <= j < len(self.t) and abs(self.t[j] - t) <= tol and (
                    best is None or abs(self.t[j] - t) < abs(self.t[best] - t)):
                best = j
        return best

    def window(self, t0, t1):
        return range(bisect.bisect_left(self.t, t0 - 1e-6), bisect.bisect_right(self.t, t1 + 1e-6))


def read_nmea(data: bytes, day: datetime) -> Epochs:
    """GGA (+ GST, merged by time of day) from a receiver log; `day` (UTC) is the date near
    which the times of day are placed (rollover handled, as logger_session.utc_of_tod)."""
    gga, gst = {}, {}
    for m in NMEA_RE.finditer(data):
        s = m.group()
        body = s[1: s.rfind(b"*")]
        if reduce(xor, body, 0) != int(m.group(2), 16):
            continue
        f = s.decode("ascii", "replace").split("*")[0].split(",")
        if len(f) < 2 or len(f[1]) < 6:
            continue
        try:
            tod = round(int(f[1][:2]) * 3600 + int(f[1][2:4]) * 60 + float(f[1][4:]), 2)
        except ValueError:
            continue
        if m.group(1) == b"GGA" and len(f) >= 14:
            try:
                alt, sep = _num(f[9]), _num(f[11])
                lat, lon = _nmea_deg(f[2], f[3], 2), _nmea_deg(f[4], f[5], 3)
            except ValueError:
                continue
            if tod in gga:
                continue
            st_id = f[14].strip() if len(f) > 14 else ""
            gga[tod] = (int(_num(f[6]) or 0), lat, lon,
                        alt + sep if alt is not None and sep is not None else None,
                        _num(f[13]), int(st_id) if st_id.isdigit() else -1, m.start())
        elif m.group(1) == b"GST" and len(f) >= 9:
            sl, so, sa = _num(f[6]), _num(f[7]), _num(f[8])
            gst[tod] = (math.hypot(sl, so) / math.sqrt(2) if sl is not None and so is not None else None, sa)
    ep = Epochs()
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
    near = day.timestamp()
    for tod in sorted(gga):
        t = base + tod
        if t - near > 43200:
            t -= 86400
        elif near - t > 43200:
            t += 86400
        fix, lat, lon, h, age, stn, off = gga[tod]
        g = gst.get(tod, (None, None))
        ep.t.append(t); ep.fix.append(fix); ep.lat.append(lat); ep.lon.append(lon); ep.h.append(h)
        ep.age.append(age); ep.station.append(stn); ep.gst_h.append(g[0]); ep.gst_u.append(g[1])
        ep.off.append(off); ep.ns.append(None)
    order = sorted(range(len(ep.t)), key=lambda i: ep.t[i])
    for name in ("t", "fix", "lat", "lon", "h", "age", "station", "gst_h", "gst_u", "off", "ns"):
        v = getattr(ep, name)
        setattr(ep, name, [v[i] for i in order])
    return ep


def attach_chunk_times(ep: Epochs, offsets, tns):
    """Phone elapsed ns of each epoch: the arrival time of the chunk holding its bytes."""
    if not offsets:
        return
    for i, off in enumerate(ep.off):
        k = bisect.bisect_right(offsets, off) - 1
        ep.ns[i] = tns[k] if k >= 0 else None


def clock_map(ep: Epochs):
    """(a, b) with elapsed_ns ~= a + b * unix_s, fitted to the lower envelope of chunk
    arrival minus epoch time (arrival is later by a jittery latency). None without chunks."""
    pts = [(t, ns - t * 1e9) for t, ns in zip(ep.t, ep.ns) if ns is not None]
    if len(pts) < 10:
        return None
    bins = {}
    for t, d in pts:
        k = int(t // 60)
        if k not in bins or d < bins[k][1]:
            bins[k] = (t, d)
    xs = [v[0] for v in bins.values()]
    ys = [v[1] for v in bins.values()]
    if len(xs) < 2:
        return (ys[0], 1.0)
    mx, my = st.fmean(xs), st.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    # elapsed = unix*1e9 + d(t) = unix*1e9 + my + slope*(t - mx)
    return (my - slope * mx, 1e9 + slope)


# ----------------------------------------------------------------------------- base history

class BaseHistory:
    """Broadcast base coordinates over time. shift(t) is the ECEF vector (current base -
    base in force at t) to add to a rover position taken at t."""

    def __init__(self, current, rows=()):
        self.cur = geodetic_to_ecef(*current)
        self.rows = [(t0, t1, geodetic_to_ecef(la, lo, h)) for t0, t1, la, lo, h in rows]

    @classmethod
    def from_csv(cls, current, path):
        rows = []
        if path:
            with open(path, newline="") as fh:
                for r in csv.DictReader(fh):
                    rows.append((parse_iso(r["start_utc"]), parse_iso(r["end_utc"]),
                                 float(r["lat"]), float(r["lon"]), float(r["h"])))
        return cls(current, rows)

    def shift(self, t):
        for t0, t1, p in self.rows:
            if t0 <= t < t1:
                return tuple(self.cur[i] - p[i] for i in range(3))
        return (0.0, 0.0, 0.0)


def to_local(frame: LocalFrame, lat, lon, h, shift=(0.0, 0.0, 0.0)):
    X = geodetic_to_ecef(lat, lon, h)
    return frame.enu_ecef(X[0] + shift[0], X[1] + shift[1], X[2] + shift[2])


def localise(ep: Epochs, frame, history: BaseHistory, pole_at):
    """Fill x, y, za (antenna) and z (ground = antenna - pole) in the local frame."""
    for i in range(len(ep)):
        if ep.lat[i] is None or ep.h[i] is None:
            ep.x.append(None); ep.y.append(None); ep.za.append(None); ep.pole.append(None); ep.z.append(None)
            continue
        x, y, za = to_local(frame, ep.lat[i], ep.lon[i], ep.h[i], history.shift(ep.t[i]))
        p = pole_at(ep.t[i], ep.ns[i])
        ep.x.append(x); ep.y.append(y); ep.za.append(za); ep.pole.append(p)
        ep.z.append(za - p if p is not None else None)


# ----------------------------------------------------------------------------- fix stretches

def fix_stretches(ep: Epochs, suspect_jump_m=1.0, in_fix_jump_m=0.5, in_fix_speed=2.0, gap_s=2.0,
                  station=None):
    """The logger's false-fix guard (logger FixGuard) over a log: a stretch of RTK fixed
    epochs starts when the previous epoch was not fixed, is over gap_s older, or is further
    than max(in_fix_jump_m, in_fix_speed * dt) away, or the reference station changed. The
    stretch's jump is the 3D step from the previous epoch with a position (if <= gap_s
    older), suspect when over suspect_jump_m. Fills stretch (-1 when not fixed), fix_age
    (s since the stretch start), jump (m or None), suspect."""
    idx, start, jump, prev, prev_fixed = -1, None, None, None, False
    for i in range(len(ep)):
        has = ep.x[i] is not None
        fixed = ep.fix[i] == 4 and has and (station is None or ep.station[i] in (station, -1))
        if fixed:
            new = prev is None or not prev_fixed or ep.t[i] - ep.t[prev] > gap_s
            if not new:
                d = math.dist((ep.x[i], ep.y[i], ep.za[i]), (ep.x[prev], ep.y[prev], ep.za[prev]))
                dt = ep.t[i] - ep.t[prev]
                new = d > max(in_fix_jump_m, in_fix_speed * dt) or ep.station[i] != ep.station[prev]
            if new:
                idx += 1
                start = ep.t[i]
                # from a float or fixed epoch only: a first fix after single/DGNSS epochs (no
                # corrections yet) always "jumps" by metres; that is not a false-fix signature
                jump = (math.dist((ep.x[i], ep.y[i], ep.za[i]), (ep.x[prev], ep.y[prev], ep.za[prev]))
                        if prev is not None and ep.t[i] - ep.t[prev] <= gap_s and ep.fix[prev] in (4, 5)
                        else None)
            ep.stretch.append(idx); ep.fix_age.append(ep.t[i] - start); ep.jump.append(jump)
            ep.suspect.append(jump is not None and jump > suspect_jump_m)
        else:
            ep.stretch.append(-1); ep.fix_age.append(None); ep.jump.append(None); ep.suspect.append(False)
        if has:
            prev, prev_fixed = i, fixed


# ----------------------------------------------------------------------------- pole stops

def find_stops(ep: Epochs, ok, window_s=2.0, max_h_m=0.03, max_sd_z_m=0.012, max_gap_s=0.6):
    """Still-pole stops: maximal runs of epochs covered by `window_s` windows in which every
    epoch is `ok` (RTK fixed, usable), there is no time gap over max_gap_s, every epoch is
    within max_h_m horizontally of the window mean and the ground height SD is at most
    max_sd_z_m. A run that creeps more than max_h_m from its first window's mean is split.
    Returns [(i0, i1)] inclusive epoch index ranges."""
    n = len(ep)
    still = [False] * n
    j = 0
    for i in range(n):
        if not ok[i]:
            continue
        j = max(j, i)
        while j + 1 < n and ep.t[j + 1] - ep.t[i] <= window_s + 1e-6:
            j += 1
        if ep.t[j] - ep.t[i] < window_s - 0.3:
            continue
        rng = range(i, j + 1)
        if not all(ok[k] for k in rng) or any(ep.t[k + 1] - ep.t[k] > max_gap_s for k in range(i, j)):
            continue
        mx = st.fmean(ep.x[k] for k in rng); my = st.fmean(ep.y[k] for k in rng)
        if max(math.hypot(ep.x[k] - mx, ep.y[k] - my) for k in rng) > max_h_m:
            continue
        if st.pstdev([ep.z[k] for k in rng]) > max_sd_z_m:
            continue
        for k in rng:
            still[k] = True
    stops, i = [], 0
    while i < n:
        if not still[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and still[j + 1] and ep.t[j + 1] - ep.t[j] <= max_gap_s:
            j += 1
        # split on creep: compare with the mean of the first window_s of the piece
        a = i
        while a <= j:
            b = a
            while b + 1 <= j and ep.t[b + 1] - ep.t[a] <= window_s:
                b += 1
            cx = st.fmean(ep.x[k] for k in range(a, b + 1)); cy = st.fmean(ep.y[k] for k in range(a, b + 1))
            e = b
            while e + 1 <= j and math.hypot(ep.x[e + 1] - cx, ep.y[e + 1] - cy) <= max_h_m:
                e += 1
            if ep.t[e] - ep.t[a] >= window_s - 0.3:
                stops.append((a, e))
            a = e + 1
        i = j + 1
    return stops


# ----------------------------------------------------------------------------- IMU

def read_gravity(data: bytes):
    """(ns list, [(gx, gy, gz)]) from imu.csv: the 'grav' sensor, or 'acc' if there is none."""
    rows = {"grav": ([], []), "acc": ([], [])}
    for line in io.StringIO(data.decode("ascii", "replace")):
        if line.startswith("grav,") or line.startswith("acc,"):
            p = line.rstrip().split(",")
            if len(p) >= 5:
                try:
                    v = (float(p[2]), float(p[3]), float(p[4]))
                    rows[p[0]][0].append(int(p[1])); rows[p[0]][1].append(v)
                except ValueError:
                    pass
    ns, g = rows["grav"] if rows["grav"][0] else rows["acc"]
    order = sorted(range(len(ns)), key=ns.__getitem__)
    return [ns[i] for i in order], [g[i] for i in order]


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 0 else None


def angle_deg(a, b):
    c = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(c))


def gravity_window(ns, g, a_ns, b_ns, sub_ns=250_000_000):
    """(mean unit gravity direction, stability in degrees = RMS angle between the means of the
    0.25 s pieces and the whole mean, samples) over [a_ns, b_ns]; (None, None, 0) if empty."""
    i0, i1 = bisect.bisect_left(ns, a_ns), bisect.bisect_right(ns, b_ns)
    if i1 - i0 < 3:
        return None, None, 0
    mean = _unit(tuple(sum(g[k][c] for k in range(i0, i1)) for c in range(3)))
    dev, k = [], i0
    while k < i1:
        e = bisect.bisect_right(ns, ns[k] + sub_ns, k, i1)
        u = _unit(tuple(sum(g[q][c] for q in range(k, e)) for c in range(3)))
        if u is not None and mean is not None:
            dev.append(angle_deg(u, mean))
        k = e
    rms = math.sqrt(sum(d * d for d in dev) / len(dev)) if dev else None
    return mean, rms, i1 - i0


def median_direction(dirs):
    """Component-wise median, normalised: a robust mean direction for small spreads."""
    return _unit(tuple(st.median(d[c] for d in dirs) for c in range(3)))


# ----------------------------------------------------------------------------- sigma model

def sigma_terms(sg, *, sd_u=None, gst_u=None, sd_h=None, gst_h=None, age=None, fix_age=None,
                settle_s=90.0, pole_m=1.8, tilt_deg=1.0, on_mark=False, inflate_settle=True):
    """The error budget of one sounding (module docstring). Returns a dict of terms (m)."""
    rtk = max(sg["floor"], sd_u or 0.0, gst_u or 0.0)
    a = sg["age_rate"] * max(0.0, (age or 0.0) - sg["age_free_s"])
    s = 0.0
    if inflate_settle and fix_age is not None and fix_age < settle_s:
        s = sg["settle_extra"] * (1.0 - fix_age / settle_s) ** 2
    tilt = math.radians(tilt_deg)
    pole = (pole_m or 0.0) * (1.0 - math.cos(tilt)) + (0.0 if on_mark else sg["tip_sink"])
    ground = 0.0 if on_mark else sg["ground"]
    h = max(sg["floor_h"], sd_h or 0.0, gst_h or 0.0)
    return {"sigma_rtk": rtk, "sigma_age": a, "sigma_settle": s, "sigma_pole": pole,
            "sigma_ground": ground,
            "sigma_z": math.sqrt(rtk ** 2 + a ** 2 + s ** 2 + pole ** 2 + ground ** 2),
            "sigma_xy": math.hypot(h, (pole_m or 0.0) * math.sin(tilt))}


# ----------------------------------------------------------------------------- rows

COLUMNS = [("sid", "TEXT"), ("source", "TEXT"), ("session", "TEXT"), ("kind", "TEXT"),
           ("n", "INTEGER"), ("class", "TEXT"), ("use", "INTEGER"), ("flags", "TEXT"),
           ("utc", "TEXT"), ("t_unix", "REAL"), ("duration_s", "REAL"),
           ("x_m", "REAL"), ("y_m", "REAL"), ("z_m", "REAL"),
           ("sigma_z", "REAL"), ("sigma_xy", "REAL"), ("sigma_rtk", "REAL"), ("sigma_age", "REAL"),
           ("sigma_settle", "REAL"), ("sigma_pole", "REAL"), ("sigma_ground", "REAL"),
           ("pass_id", "TEXT"), ("prior_m", "REAL"), ("common_prior_m", "REAL"), ("anchor", "TEXT"),
           ("fix", "INTEGER"), ("fix_age_s", "REAL"), ("jump_m", "REAL"), ("age_s", "REAL"),
           ("n_epochs", "INTEGER"), ("sd_u_m", "REAL"), ("gst_u_m", "REAL"), ("pole_m", "REAL"),
           ("tilt_deg", "REAL"), ("imu_stable_deg", "REAL"), ("label", "TEXT")]
KEYS = [c for c, _ in COLUMNS]
EXCLUDING = {"float", "other_station", "suspect_jump", "settle", "imu_unstable", "not_plumb",
             "dup_capture", "local_outlier", "no_position", "plumb_unverified"}


def new_row(**kw):
    r = {k: None for k in KEYS}
    r.update(kw)
    r.setdefault("flags", "")
    return r


def add_flag(r, f):
    fl = [x for x in (r["flags"] or "").split(",") if x]
    if f not in fl:
        fl.append(f)
    r["flags"] = ",".join(fl)


def flags_of(r):
    return [x for x in (r["flags"] or "").split(",") if x]


def finish_use(r):
    """use = 1 for ground and anchor rows without an excluding flag."""
    fl = flags_of(r)
    bad = any(f in EXCLUDING or f.startswith("listed") for f in fl)
    r["use"] = 1 if r["class"] in ("ground", "anchor") and not bad else 0


def pass_name(session, stretch, fix_age, max_s):
    piece = int(fix_age // max_s) if fix_age is not None and max_s else 0
    return f"{session}/s{stretch:03d}" + (f".{piece}" if piece else "")


# ----------------------------------------------------------------------------- sources

def gpkg_meta(con):
    return dict(con.execute("SELECT key, value FROM icecube_meta"))


def listed(src_cfg):
    """{(session, n): reason} and [(session or '', t0, t1, reason)] from the config lists."""
    by_n, by_t = {}, []
    for item in src_cfg.get("exclude", []):
        reason = str(item.get("reason", "listed")).replace(",", ";")
        ses = item.get("session", "")
        for n in item.get("n", []):
            by_n[(ses, int(n))] = reason
        for a, b in item.get("utc", []):
            by_t.append((ses, parse_iso(a), parse_iso(b), reason))
    return by_n, by_t


def _listed_reason(by_n, by_t, session, n, t):
    r = by_n.get((session, n)) if n is not None else None
    if r:
        return r
    for ses, a, b, reason in by_t:
        if (not ses or ses == session) and t is not None and a <= t <= b:
            return reason
    return None


def logger_source(cfg, src, con, frame, history, log):
    """Captures from the gpkg plus pole stops from the raw sessions."""
    sg, sp = cfg["sigma"], cfg["stops"]
    settle_s, max_s = cfg["settle_s"], cfg["max_pass_s"]
    prior, cprior = float(src.get("prior_m", 0.015)), float(src.get("common_prior_m", 0.0))
    by_n, by_t = listed(src)
    sessions = src.get("sessions")
    q = ("SELECT session, n, class, utc, lat, lon, h_ground, pole_m, fix, n_epochs, sd_e_m, sd_n_m, "
         "sd_u_m, age_s, elapsed_ns, label FROM captures")
    caps = [dict(zip(["session", "n", "class", "utc", "lat", "lon", "h_ground", "pole_m", "fix",
                      "n_epochs", "sd_e_m", "sd_n_m", "sd_u_m", "age_s", "elapsed_ns", "label"], r))
            for r in con.execute(q)]
    if sessions:
        caps = [c for c in caps if c["session"] in sessions]
    sites = {}
    try:
        sites = {(s, n): site for s, n, site in con.execute("SELECT session, n, site FROM refs")}
    except sqlite3.Error:
        pass
    starts = dict(con.execute("SELECT session, start_utc FROM sessions"))
    raws = {}
    if src.get("session_paths"):
        for s in find_sessions([Path(p) for p in src["session_paths"]]):
            raws[s.name] = s
    rows = []
    by_session = {}
    for c in caps:
        by_session.setdefault(c["session"], []).append(c)
    names = set(by_session) | (set(raws) & set(sessions) if sessions else set(raws))
    for ses in sorted(names):
        clist = sorted(by_session.get(ses, []), key=lambda c: c["n"])
        raw = raws.get(ses)
        ep = None
        if raw is not None:
            start = parse_iso(starts[ses]) if starts.get(ses) else None
            ev = read_events(raw)
            if start is None and ev:
                start = ev[0][1] / 1000
            ubx = raw.read("gps.ubx") or b""
            ep = read_nmea(ubx, datetime.fromtimestamp(start or 0, timezone.utc))
            offs, tns = read_chunks(raw)
            attach_chunk_times(ep, offs, tns)
            poles = [(t_ns, float(text.split("=")[1].split()[0])) for t_ns, _, text in ev
                     if text.startswith("pole_height=")]

            def pole_at(t, ns, poles=poles):
                if ns is None or not poles:
                    return poles[-1][1] if poles else None
                k = bisect.bisect_right([p[0] for p in poles], ns) - 1
                return poles[max(k, 0)][1]
            localise(ep, frame, history, pole_at)
            fix_stretches(ep, cfg["suspect_jump_m"], station=src.get("station"))
        # captures
        for c in clist:
            if c["class"] not in ("surface", "ref", "control", "object"):
                continue
            t = parse_iso(c["utc"]) if c["utc"] else None
            cls = {"surface": "ground", "ref": "anchor", "control": "anchor", "object": "object"}[c["class"]]
            r = new_row(sid=f"{src['id']}:{ses}:c{c['n']}", source=src["id"], session=ses, kind="capture",
                        n=c["n"], **{"class": cls}, utc=c["utc"], t_unix=t, fix=c["fix"],
                        n_epochs=c["n_epochs"], sd_u_m=c["sd_u_m"], age_s=c["age_s"], pole_m=c["pole_m"],
                        label=c["label"], prior_m=prior, common_prior_m=cprior)
            if c["lat"] is None or c["h_ground"] is None:
                add_flag(r, "no_position"); finish_use(r); rows.append(r); continue
            r["x_m"], r["y_m"], r["z_m"] = to_local(frame, c["lat"], c["lon"], c["h_ground"],
                                                    history.shift(t) if t else (0, 0, 0))
            if c["fix"] != 4:
                add_flag(r, "float")
            k = ep.index_at(t) if ep is not None and t else None
            gst_u = gst_h = None
            if k is not None:
                if ep.stretch[k] >= 0:
                    r["fix_age_s"], r["jump_m"] = ep.fix_age[k], ep.jump[k]
                    r["pass_id"] = pass_name(ses, ep.stretch[k], ep.fix_age[k], max_s)
                    if ep.suspect[k]:
                        add_flag(r, "suspect_jump")
                win = [j for j in ep.window(t - 1.0, t) if ep.gst_u[j] is not None]
                if win:
                    gst_u = st.median(ep.gst_u[j] for j in win)
                    gst_h = st.median(ep.gst_h[j] for j in win if ep.gst_h[j] is not None) if any(
                        ep.gst_h[j] is not None for j in win) else None
            else:
                add_flag(r, "no_fix_info")
            if r["pass_id"] is None:
                r["pass_id"] = f"{ses}/c{c['n']}"
            r["gst_u_m"] = gst_u
            reason = _listed_reason(by_n, by_t, ses, c["n"], t)
            if reason:
                add_flag(r, "listed:" + reason)
            on_mark = cls == "anchor"
            sd_h = math.hypot(c["sd_e_m"] or 0, c["sd_n_m"] or 0) / math.sqrt(2)
            r.update(sigma_terms(sg, sd_u=c["sd_u_m"], gst_u=gst_u, sd_h=sd_h, gst_h=gst_h, age=c["age_s"],
                                 fix_age=r["fix_age_s"], settle_s=settle_s, pole_m=c["pole_m"],
                                 tilt_deg=sg["tilt_bubble_deg"], on_mark=on_mark))
            r["tilt_deg"] = sg["tilt_bubble_deg"]
            if r["fix_age_s"] is not None and r["fix_age_s"] < settle_s:
                add_flag(r, "settle_inflated")
            if cls == "anchor":
                site = sites.get((ses, c["n"]))
                r["anchor"] = f"site{site}" if site is not None else None
            rows.append(r)
        if ep is None:
            continue
        # IMU plumb reference from the refs / controls of this session
        grav = None
        cmap = clock_map(ep)
        imu = raw.read("imu.csv")
        if imu and cmap:
            ns, g = read_gravity(imu)
            grav = (ns, g)
        plumb, plumb_spread, nref = None, None, 0
        if grav:
            dirs = []
            for c in clist:
                if c["class"] in ("ref", "control") and c["elapsed_ns"] and c["fix"] == 4:
                    span = 20e9 if c["class"] == "ref" else 1e9
                    d, stab, m = gravity_window(*grav, c["elapsed_ns"] - span - 1e9, c["elapsed_ns"] - 1e9)
                    if d is not None:
                        dirs.append(d)
            if dirs:
                plumb = median_direction(dirs)
                nref = len(dirs)
                plumb_spread = st.median(angle_deg(d, plumb) for d in dirs)
        log(f"  {ses}: {len(ep)} epochs, {sum(1 for f in ep.fix if f == 4)} fixed, "
            f"{len({s for s in ep.stretch if s >= 0})} fix stretches, "
            f"{sum(1 for s, sus in zip(ep.stretch, ep.suspect) if sus and s >= 0)} epochs in suspect stretches; "
            + ("IMU " + (f"plumb reference from {nref} refs/controls, median spread {plumb_spread:.2f} deg"
                         if plumb else "without a plumb reference") if grav else "no IMU"))
        rows += stops_from_epochs(cfg, src, ses, ep, clist_windows(clist, ep), grav, cmap, plumb,
                                  prior, cprior, by_n, by_t)
    return rows


def clist_windows(clist, ep):
    """Time windows (unix s) of the captures of a session, to drop stops that are captures."""
    out = []
    for c in clist:
        if not c["utc"]:
            continue
        t = parse_iso(c["utc"])
        span = 30.0 if c["class"] == "ref" else 1.0
        out.append((t - span, t))
    return out


def stops_from_epochs(cfg, src, ses, ep, capture_windows, grav, cmap, plumb, prior, cprior, by_n, by_t):
    sg, sp = cfg["sigma"], cfg["stops"]
    settle_s, max_s = cfg["settle_s"], cfg["max_pass_s"]
    ok = [ep.stretch[i] >= 0 and ep.z[i] is not None for i in range(len(ep))]
    stops = find_stops(ep, ok, sp["window_s"], sp["max_h_m"], sp["max_sd_z_m"], sp["max_gap_s"])
    rows = []
    m = sp["capture_margin_s"]
    for k, (i0, i1) in enumerate(stops):
        rng = range(i0, i1 + 1)
        t0, t1 = ep.t[i0], ep.t[i1]
        tm = st.fmean(ep.t[i] for i in rng)
        zs = [ep.z[i] for i in rng]
        sd_u = st.pstdev(zs)
        sd_h = math.sqrt((st.pstdev([ep.x[i] for i in rng]) ** 2 + st.pstdev([ep.y[i] for i in rng]) ** 2) / 2)
        gu = [ep.gst_u[i] for i in rng if ep.gst_u[i] is not None]
        gh = [ep.gst_h[i] for i in rng if ep.gst_h[i] is not None]
        ages = [ep.age[i] for i in rng if ep.age[i] is not None]
        pole = ep.pole[i0]
        r = new_row(sid=f"{src['id']}:{ses}:p{k}", source=src["id"], session=ses, kind="stop", n=k,
                    **{"class": "ground"}, utc=iso(tm), t_unix=tm, duration_s=t1 - t0,
                    x_m=st.fmean(ep.x[i] for i in rng), y_m=st.fmean(ep.y[i] for i in rng), z_m=st.fmean(zs),
                    fix=4, fix_age_s=ep.fix_age[i0], jump_m=ep.jump[i0], age_s=st.median(ages) if ages else None,
                    n_epochs=len(rng), sd_u_m=sd_u, gst_u_m=st.median(gu) if gu else None, pole_m=pole,
                    pass_id=pass_name(ses, ep.stretch[i0], ep.fix_age[i0], max_s), prior_m=prior,
                    common_prior_m=cprior)
        if ep.suspect[i0]:
            add_flag(r, "suspect_jump")
        if ep.fix_age[i0] < settle_s:
            add_flag(r, "settle")
        if any(a - m <= t1 and t0 <= b + m for a, b in capture_windows):
            add_flag(r, "dup_capture")
        reason = _listed_reason(by_n, by_t, ses, None, tm)
        if reason:
            add_flag(r, "listed:" + reason)
        tilt = sg["tilt_unknown_deg"]
        if grav and cmap:
            a_ns = cmap[0] + cmap[1] * t0
            b_ns = cmap[0] + cmap[1] * t1
            d, stab, nimu = gravity_window(*grav, a_ns, b_ns)
            if d is None:
                add_flag(r, "no_imu")
            else:
                r["imu_stable_deg"] = stab
                if stab > sp["imu_stable_deg"]:
                    add_flag(r, "imu_unstable")
                if plumb is None:
                    add_flag(r, "no_plumb_ref")
                else:
                    ang = angle_deg(d, plumb)
                    r["tilt_deg"] = ang
                    if ang > sp["imu_plumb_deg"]:
                        add_flag(r, "not_plumb")
                    else:
                        tilt = sg["tilt_imu_deg"]
        else:
            add_flag(r, "no_imu")
        if sp["require_plumb"] and tilt != sg["tilt_imu_deg"]:
            add_flag(r, "plumb_unverified")
        r.update(sigma_terms(sg, sd_u=sd_u, gst_u=r["gst_u_m"], sd_h=sd_h, gst_h=st.median(gh) if gh else None,
                             age=r["age_s"], fix_age=None, settle_s=settle_s, pole_m=pole, tilt_deg=tilt))
        rows.append(r)
    return rows


def _swmaps_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def swmaps_source(cfg, src, frame, history, log, with_log=True):
    """SW Maps points (CSV) with, if given, the receiver log for fix age, station and stops."""
    sg = cfg["sigma"]
    settle_s, max_s = cfg["settle_s"], cfg["max_pass_s"]
    prior, cprior = float(src.get("prior_m", 0.015)), float(src.get("common_prior_m", 0.0))
    by_n, by_t = listed(src)
    ses = src.get("session", src["id"])
    pole_m = float(src.get("pole_m", 1.8))
    off_h = src.get("utc_offset_h")
    use_history = bool(src.get("base_history", True))
    shift_enu = src.get("shift_enu")
    extra = (0.0, 0.0, 0.0)
    if shift_enu:
        extra = frame.rotate_enu(*[float(v) for v in shift_enu])
        if src.get("shift_measured_utc"):   # measured against our base as broadcast then
            s = history.shift(parse_iso(src["shift_measured_utc"]))
            extra = tuple(extra[i] + s[i] for i in range(3))
    ep = None
    if with_log and src.get("nmea_log"):
        day = datetime.fromisoformat(src["date"]).replace(tzinfo=timezone.utc) + timedelta(hours=12)
        ep = read_nmea(Path(src["nmea_log"]).read_bytes(), day)
        localise(ep, frame, history if use_history else BaseHistory((0, 0, 0)),
                 lambda t, ns: pole_m)
        fix_stretches(ep, cfg["suspect_jump_m"], station=src.get("station"))
        log(f"  {ses}: log {len(ep)} epochs, {sum(1 for f in ep.fix if f == 4)} fixed, "
            f"{len({s for s in ep.stretch if s >= 0})} fix stretches")
    rows, windows, pts = [], [], []
    for path in src.get("points_csv", []):
        layer = Path(path).stem
        for rr in _swmaps_rows(path):
            la, lo, el = _num(rr.get("Latitude") or rr.get("Lat")), _num(rr.get("Longitude") or rr.get("Lon")), _num(rr.get("Elevation"))
            t = parse_swmaps_time(rr.get("Time", ""), off_h)
            ts = parse_swmaps_time(rr.get("Start Time", ""), off_h) if rr.get("Start Time") else None
            pts.append((layer, rr, la, lo, el, t, ts))
    pts.sort(key=lambda p: (p[5] if p[5] is not None else 0))
    cls_of = src.get("classes", {})       # layer stem substring -> class
    g_index, last = 0, None
    for k, (layer, rr, la, lo, el, t, ts) in enumerate(pts):
        cls = "ground"
        for key, c in cls_of.items():
            if key in layer:
                cls = c
        fixq = int(_num(rr.get("Fix ID")) or -1)
        n = int(_num(rr.get("ID")) or k)
        r = new_row(sid=f"{src['id']}:{layer}:{n}", source=src["id"], session=ses, kind="point", n=n,
                    **{"class": cls}, utc=iso(t) if t else None, t_unix=t, fix=fixq,
                    n_epochs=int(_num(rr.get("Averaged Count")) or 1), pole_m=_num(rr.get("Instrument Ht")) or pole_m,
                    label=(rr.get("Remarks") or "").strip() or layer, prior_m=prior, common_prior_m=cprior)
        if la is None or el is None:
            add_flag(r, "no_position"); finish_use(r); rows.append(r); continue
        s = history.shift(t) if (use_history and t) else (0.0, 0.0, 0.0)
        s = tuple(s[i] + extra[i] for i in range(3))
        r["x_m"], r["y_m"], r["z_m"] = to_local(frame, la, lo, el, s)
        if fixq != 4:
            add_flag(r, "float")
        va, ha = _num(rr.get("Vertical Accuracy")), _num(rr.get("Horizontal Accuracy"))
        if t is not None and ts is not None:
            r["duration_s"] = t - ts
            windows.append((ts, t))
        elif t is not None:
            windows.append((t - 0.5, t))
        kk = ep.index_at(t) if ep is not None and t is not None else None
        if kk is not None and ep.stretch[kk] >= 0:
            r["fix_age_s"], r["jump_m"], r["age_s"] = ep.fix_age[kk], ep.jump[kk], ep.age[kk]
            r["gst_u_m"] = ep.gst_u[kk]
            r["pass_id"] = pass_name(ses, ep.stretch[kk], ep.fix_age[kk], max_s)
            if ep.suspect[kk]:
                add_flag(r, "suspect_jump")
            fa = r["fix_age_s"] - (r["duration_s"] or 0.0)   # age when the averaging began
            if fa < settle_s:
                add_flag(r, "settle")
        elif kk is not None:
            add_flag(r, "float" if ep.fix[kk] != 4 else "other_station")
        else:
            add_flag(r, "no_fix_info")
            # no log: a new pass after a time gap, a horizontal jump, or max_pass_s
            if t is not None:
                if last is not None and (t - last[0] > cfg["gap_s"] or t - last[3] > max_s or math.hypot(
                        r["x_m"] - last[1], r["y_m"] - last[2]) > cfg["jump_m"]):
                    g_index += 1
                    last = None
                last = (t, r["x_m"], r["y_m"], t if last is None else last[3])
            r["pass_id"] = f"{ses}/g{g_index:03d}"
        if kk is not None and src.get("station") is not None and ep.station[kk] not in (-1, src["station"]):
            add_flag(r, "other_station")
        if r["pass_id"] is None:
            r["pass_id"] = f"{ses}/pt{n}"
        reason = _listed_reason(by_n, by_t, ses, n, t)
        if reason:
            add_flag(r, "listed:" + reason)
        r.update(sigma_terms(sg, sd_u=va, gst_u=r["gst_u_m"], sd_h=ha, age=r["age_s"], fix_age=None,
                             settle_s=settle_s, pole_m=r["pole_m"], tilt_deg=sg["tilt_bubble_deg"],
                             on_mark=False))
        r["tilt_deg"] = sg["tilt_bubble_deg"]
        rows.append(r)
    if ep is not None and src.get("stops", True):
        rows += stops_from_epochs(cfg, src, ses, ep, windows, None, None, None, prior, cprior, by_n, by_t)
    return rows


# ----------------------------------------------------------------------------- post-processing

def assign_anchors(rows, radius, snap, dz):
    """Group anchor rows (refs, controls) within `radius` m (single link) into sites, named
    by the existing refs.site where present; then let other usable soundings within `snap` m
    and `dz` m of a site's median join it as anchors (a repeat spot shot as a plain point)."""
    an = [r for r in rows if r["class"] == "anchor" and r["x_m"] is not None]
    parent = list(range(len(an)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i in range(len(an)):
        for j in range(i + 1, len(an)):
            if math.hypot(an[i]["x_m"] - an[j]["x_m"], an[i]["y_m"] - an[j]["y_m"]) <= radius:
                parent[find(i)] = find(j)
    groups = {}
    for i, r in enumerate(an):
        groups.setdefault(find(i), []).append(r)
    sites = []
    for g in sorted(groups.values(), key=lambda g: -len(g)):
        names = sorted({r["anchor"] for r in g if r["anchor"]})
        name = names[0] if names else f"site_{len(sites) + 1}"
        for r in g:
            r["anchor"] = name
        good = [r for r in g if not any(f in EXCLUDING or f.startswith("listed") for f in flags_of(r))] or g
        sites.append((name, st.median(r["x_m"] for r in good), st.median(r["y_m"] for r in good),
                      st.median(r["z_m"] for r in good)))
    joined = 0
    for r in rows:
        if r["class"] != "ground" or r["x_m"] is None:
            continue
        for name, x, y, z in sites:
            if math.hypot(r["x_m"] - x, r["y_m"] - y) <= snap and abs(r["z_m"] - z) <= dz:
                r["class"], r["anchor"] = "anchor", name
                r["sigma_ground"] = 0.0
                r["sigma_z"] = math.sqrt(max(r["sigma_z"] ** 2 - DEFAULTS["sigma"]["ground"] ** 2, 0.0))
                joined += 1
                break
    return sites, joined


def verify_suspect(rows, radius=1.5, max_med=0.10, max_any=0.25, min_n=2):
    """A fix stretch that began with a big jump is suspect, not necessarily wrong: keep it
    where independent data agree. For each pass holding suspect rows (not otherwise
    excluded), compare its rows with usable rows of other passes: an anchor with the median
    of its site's other visits, a ground row with a plane through the other passes' usable
    ground rows within `radius` m (at least 3; else the nearest within radius / 2). If at
    least `min_n` rows compare (1 for anchors) with |median dz| <= max_med and at least
    80 % of |dz| <= max_any, the pass is verified: its rows get `suspect_verified` instead of
    `suspect_jump`. Returns (verified passes, passes left suspect)."""
    def usable(r):
        return r["use"] and r["x_m"] is not None
    ref = [r for r in rows if usable(r) and r["class"] == "ground"]
    cells = {}
    for r in ref:
        cells.setdefault((int(r["x_m"] // radius), int(r["y_m"] // radius)), []).append(r)
    sites = {}
    for r in rows:
        if usable(r) and r["class"] == "anchor" and r["anchor"]:
            sites.setdefault(r["anchor"], []).append(r)
    by_pass = {}
    for r in rows:
        fl = flags_of(r)
        if "suspect_jump" in fl and not any(f.startswith("listed") or f in ("float", "other_station") for f in fl):
            by_pass.setdefault(r["pass_id"], []).append(r)
    ok_n = bad_n = 0
    for pid, members in by_pass.items():
        dz = []
        for r in members:
            if r["x_m"] is None:
                continue
            if r["class"] == "anchor" and r["anchor"] in sites:
                others = [q["z_m"] for q in sites[r["anchor"]] if q["pass_id"] != pid]
                if others:
                    dz.append(("a", r["z_m"] - st.median(others)))
                continue
            cx, cy = int(r["x_m"] // radius), int(r["y_m"] // radius)
            nb = [q for a in (-1, 0, 1) for b in (-1, 0, 1) for q in cells.get((cx + a, cy + b), [])
                  if q["pass_id"] != pid and math.hypot(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"]) <= radius]
            fit = _plane([(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"], q["z_m"]) for q in nb]) if len(nb) >= 3 else None
            if fit is not None:
                dz.append(("g", r["z_m"] - fit[0]))
            elif nb:
                q = min(nb, key=lambda q: math.hypot(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"]))
                if math.hypot(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"]) <= radius / 2:
                    dz.append(("g", r["z_m"] - q["z_m"]))
        need = 1 if any(k == "a" for k, _ in dz) else min_n
        vals = [v for _, v in dz]
        if (len(vals) >= need and abs(st.median(vals)) <= max_med
                and sum(abs(v) <= max_any for v in vals) >= 0.8 * len(vals)):
            for r in members:
                r["flags"] = ",".join("suspect_verified" if f == "suspect_jump" else f for f in flags_of(r))
            ok_n += 1
        else:
            bad_n += 1
    return ok_n, bad_n


def flag_local_outliers(rows, radius, min_dz, k):
    """Flag pole stops that sit far off a plane fitted to the usable captures and points
    within `radius` m (at least 4): |dz| > max(min_dz, k * sqrt(sigma^2 + rms^2)). Catches a
    pole held still off the ground. Captures are the reference; they are not tested."""
    ref = [r for r in rows if r["use"] and r["class"] == "ground" and r["kind"] in ("capture", "point")]
    cells = {}
    for r in ref:
        cells.setdefault((int(r["x_m"] // radius), int(r["y_m"] // radius)), []).append(r)
    n = 0
    for r in rows:
        if not (r["use"] and r["kind"] == "stop" and r["class"] == "ground"):
            continue
        cx, cy = int(r["x_m"] // radius), int(r["y_m"] // radius)
        nb = [q for a in (-1, 0, 1) for b in (-1, 0, 1) for q in cells.get((cx + a, cy + b), [])
              if math.hypot(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"]) <= radius]
        if len(nb) < 4:
            continue
        fit = _plane([(q["x_m"] - r["x_m"], q["y_m"] - r["y_m"], q["z_m"]) for q in nb])
        if fit is None:
            continue
        z0, rms = fit
        if abs(r["z_m"] - z0) > max(min_dz, k * math.hypot(r["sigma_z"], rms)):
            add_flag(r, "local_outlier")
            finish_use(r)
            n += 1
    return n


def _plane(pts):
    """Least-squares plane z = a + b x + c y; returns (a, rms) or None if degenerate."""
    n = len(pts)
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts); sz = sum(p[2] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts); syy = sum(p[1] ** 2 for p in pts); sxy = sum(p[0] * p[1] for p in pts)
    sxz = sum(p[0] * p[2] for p in pts); syz = sum(p[1] * p[2] for p in pts)
    M = [[n, sx, sy], [sx, sxx, sxy], [sy, sxy, syy]]
    v = [sz, sxz, syz]
    det = _det3(M)
    if abs(det) < 1e-9:
        return None
    sol = []
    for c in range(3):
        Mc = [row[:] for row in M]
        for r_ in range(3):
            Mc[r_][c] = v[r_]
        sol.append(_det3(Mc) / det)
    res = [p[2] - (sol[0] + sol[1] * p[0] + sol[2] * p[1]) for p in pts]
    return sol[0], math.sqrt(sum(e * e for e in res) / max(n - 3, 1))


def _det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def apply_corrections(rows, path, log):
    """Hook for post-processed (PPK) results per capture: a CSV with session, n (capture
    number), action, x_m, y_m, z_m (ground, local frame), sigma_z and note.
    - action `replace`: the capture takes the given position; sigma_rtk becomes sigma_z, the
      settle and age terms are dropped, and the fix flags (suspect_jump, float, settle,
      settle_inflated, listed:*) are cleared; flag `ppk`.
    - action `flag`: live and PPK disagree at a level neither can settle (10-30 cm): the
      position is kept, sigma_rtk is raised to sigma_z (the disagreement) and the row gets
      `ppk_disagree` (still used, down-weighted).
    Rows not named keep the live solution."""
    with open(path, newline="") as fh:
        corr = {(r["session"], int(r["n"])): r for r in csv.DictReader(fh)}
    hit = {"replace": 0, "flag": 0}
    for r in rows:
        c = corr.get((r["session"], r["n"])) if r["kind"] == "capture" else None
        if c is None:
            continue
        act = (c.get("action") or "replace").strip()
        sz = float(c["sigma_z"]) if c.get("sigma_z") else None
        if act == "replace":
            r["x_m"], r["y_m"], r["z_m"] = float(c["x_m"]), float(c["y_m"]), float(c["z_m"])
            if sz is not None:
                r["sigma_rtk"], r["sigma_settle"], r["sigma_age"] = sz, 0.0, 0.0
            r["flags"] = ",".join(f for f in flags_of(r) if f not in (
                "suspect_jump", "float", "settle", "settle_inflated") and not f.startswith("listed"))
            add_flag(r, "ppk")
        elif act == "flag":
            if sz is not None:
                r["sigma_rtk"] = max(r["sigma_rtk"] or 0.0, sz)
            add_flag(r, "ppk_disagree")
        else:
            raise SystemExit(f"{Path(path).name}: unknown action {act!r}")
        r["sigma_z"] = math.sqrt(sum((r[k] or 0.0) ** 2 for k in (
            "sigma_rtk", "sigma_age", "sigma_settle", "sigma_pole", "sigma_ground")))
        hit[act] += 1
    log(f"corrections: {hit['replace']} replaced, {hit['flag']} flagged, from {len(corr)} rows of {Path(path).name}")


# ----------------------------------------------------------------------------- output

def write_gpkg(gpkg, rows, frame, table):
    gp = gw.GeoPackage(gpkg)
    try:
        if table in gp.tables():
            gp.drop_table(table)
        gp.ensure_feature_table(table, COLUMNS, "POINT", z=True, unique=("sid",),
                                description="Ground soundings with sigmas (analysis/qgis/soundings.py); "
                                            "rebuilt on every run")
        out = []
        for r in rows:
            d = dict(r)
            if r["x_m"] is not None:
                la, lo, h = frame.geodetic(r["x_m"], r["y_m"], r["z_m"])
                d["geom"] = gw.point(lo, la, h)
            out.append(d)
        gp.insert(table, out)
        gp.update_extent(table)
        gp.commit()
    finally:
        gp.close()


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else (f"{r[k]:.4f}" if isinstance(r[k], float) and k.endswith(("_m", "_s", "deg"))
                                                     or k.startswith("sigma") else r[k])) for k in KEYS})


def summary(rows):
    """Counts per source, kind, class and flag (no coordinates)."""
    lines = ["source       kind     class   rows  use   median sigma_z (cm)"]
    keys = sorted({(r["source"], r["kind"], r["class"]) for r in rows})
    for s, k, c in keys:
        g = [r for r in rows if (r["source"], r["kind"], r["class"]) == (s, k, c)]
        u = [r for r in g if r["use"]]
        ms = st.median(r["sigma_z"] for r in u) * 100 if u else float("nan")
        lines.append(f"{s:12s} {k:8s} {c:7s} {len(g):5d} {len(u):4d}   {ms:5.1f}")
    fl = {}
    for r in rows:
        for f in flags_of(r):
            fl[f.split(":")[0] if not f.startswith("listed") else f] = fl.get(
                f.split(":")[0] if not f.startswith("listed") else f, 0) + 1
    lines.append("flags: " + ", ".join(f"{k} {v}" for k, v in sorted(fl.items())))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main

def build(cfg_all, cfg_dir, gpkg=None, csv_out=None, log=print, write=True):
    """Build the rows from a config dict (the whole site config); returns the rows."""
    sc = cfg_all.get("soundings", {})
    cfg = _merge(DEFAULTS, {k: v for k, v in sc.items() if k != "sources"})
    ab = lambda p: str(p if Path(p).is_absolute() else (cfg_dir / p))  # noqa: E731
    gpkg = gpkg or sc.get("gpkg") or cfg_all.get("season", {}).get("gpkg")
    if not gpkg:
        raise SystemExit("no gpkg: give --gpkg or [soundings].gpkg / [season].gpkg")
    gpkg = ab(gpkg)
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    meta = gpkg_meta(con)
    if not meta.get("origin_lat"):
        raise SystemExit("the gpkg has no local frame origin (icecube_meta origin_*): import with [site.origin]")
    frame = LocalFrame(float(meta["origin_lat"]), float(meta["origin_lon"]), float(meta["origin_h"]),
                       name=meta.get("origin_name", "origin"))
    current = (float(meta["base_lat"]), float(meta["base_lon"]), float(meta["base_h"]))
    history = BaseHistory.from_csv(current, ab(sc["base_history"]) if sc.get("base_history") else None)
    log(f"local frame: ENU about {frame.name}; base history rows: {len(history.rows)}")
    rows = []
    for src in sc.get("sources", []):
        src = dict(src)
        for key in ("session_paths", "points_csv"):
            if key in src:
                src[key] = [ab(p) for p in src[key]]
        if "nmea_log" in src:
            src["nmea_log"] = ab(src["nmea_log"])
        log(f"source {src['id']} ({src['type']})")
        if src["type"] == "logger":
            got = logger_source(cfg, src, con, frame, history, log)
        elif src["type"] == "swmaps":
            got = swmaps_source(cfg, src, frame, history, log, with_log=True)
        elif src["type"] == "swmaps_points":
            got = swmaps_source(cfg, src, frame, history, log, with_log=False)
        else:
            raise SystemExit(f"unknown source type {src['type']!r}")
        rows += got
    con.close()
    if sc.get("corrections_csv"):
        p = Path(ab(sc["corrections_csv"]))
        if p.is_file():
            apply_corrections(rows, p, log)
        else:
            log(f"corrections: {p.name} not found, none applied (hook for post-processed positions)")
    for r in rows:
        finish_use(r)
    sites, joined = assign_anchors(rows, cfg["anchor_radius_m"], cfg["anchor_snap_m"], cfg["anchor_dz_m"])
    for r in rows:
        finish_use(r)
    v_ok, v_bad = verify_suspect(rows)
    for r in rows:
        finish_use(r)
    log(f"suspect fix stretches (jump > {cfg['suspect_jump_m']} m): {v_ok} passes verified by other data, "
        f"{v_bad} left out")
    n_out = flag_local_outliers(rows, cfg["stops"]["outlier_radius_m"], cfg["stops"]["outlier_min_dz_m"],
                                cfg["stops"]["outlier_k"])
    log(f"anchors: {len(sites)} sites ({sum(1 for r in rows if r['class'] == 'anchor')} rows, "
        f"{joined} joined by proximity); stops flagged as local outliers: {n_out}")
    log(summary(rows))
    if write:
        write_gpkg(gpkg, rows, frame, cfg["table"])
        log(f"wrote {len(rows)} rows to table {cfg['table']} of {Path(gpkg).name}")
    if csv_out or sc.get("csv"):
        p = ab(csv_out or sc["csv"])
        write_csv(p, rows)
        log(f"wrote {Path(p).name}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="site config TOML with [soundings] (default: config.toml at the repo root)")
    ap.add_argument("--gpkg", help="season GeoPackage (default [soundings].gpkg or [season].gpkg)")
    ap.add_argument("--csv", help="also write the rows to this CSV")
    ap.add_argument("--dry-run", action="store_true", help="do not write the gpkg")
    a = ap.parse_args(argv)
    p = Path(a.config) if a.config else REPO / "config.toml"
    cfg = tomllib.loads(p.read_text())
    build(cfg, p.resolve().parent, gpkg=a.gpkg, csv_out=a.csv, write=not a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
