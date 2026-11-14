"""Reference frames: ECEF and geodetic coordinates, 14-parameter Helmert transformations
with rates, ITRF2020 <-> ITRF2014 / ITRF2008 / NAD83(2011), and the offset to expect
between a station's frame and ours. Pure Python, no numpy, so the app and the analysis
scripts share one copy.

Frames and parameters (every set is documented at its definition):

- ITRF2020 -> ITRF2014 and ITRF2020 -> ITRF2008: the IERS/IGN ITRF2020 transformation
  parameters (itrf.ign.fr, "Transformation parameters from ITRF2020 to past ITRFs"),
  reference epoch 2015.0, IERS convention. Millimetres: the ITRFs agree to the centimetre.
- ITRF2008 -> NAD83(2011): the NGS parameters of Pearson & Snay 2013 ("Introducing
  HTDP 3.1 to transform coordinates across time and spatial reference frames", GPS
  Solutions 17:1-15), as HTDP uses them, reference epoch 1997.00, HTDP convention (the
  rotation sign opposite to the IERS one). About a metre in the eastern United States.
- ITRF2020 -> NAD83(2011) chains the two. NAD83(2011) coordinates are quoted at epoch
  2010.00: the transformation is evaluated at the epoch of the ITRF2020 coordinate (the
  epoch of observation), which puts the point in NAD83(2011) at that epoch, and the
  point's own NAD83 velocity (millimetres a year in the stable interior of North America,
  not modelled here) is what separates that from epoch 2010.00.

The epoch of an ITRF coordinate matters because the plates move: an ITRF2020 coordinate
of a fixed point changes by centimetres a year. The frame transformations here take the
epoch the coordinate is at; they do not move a point in time.

Sign convention of `expected_offset_enu`: the station's frame minus ours, as the
reference check measures (its solution is in the station's frame, our 1005 in ours).
"""
import math
from datetime import datetime, timezone

MAS = math.pi / 180 / 3600 / 1000      # milliarcseconds to radians
PPB = 1e-9

# Ellipsoids (semi-major axis in metres, flattening). GRS80 is the ellipsoid of the
# ITRFs and NAD83; WGS84 differs by 0.1 mm in the polar radius.
GRS80 = (6378137.0, 1 / 298.257222101)
WGS84 = (6378137.0, 1 / 298.257223563)

FRAMES = ("ITRF2020", "ITRF2014", "ITRF2008", "NAD83(2011)")


class Helmert:
    """A 14-parameter transformation between two frames, with rates:

        x' = T(t) + (1 + s(t)) x + sign * (R(t) x x)

    where T is in metres, R in radians and s a number, each `value + rate * (t - epoch)`
    with t in decimal years. `sign` is +1 for the IERS convention (the ITRF tables: a
    counter-clockwise rotation of the coordinate axes seen as [[0,-Rz,Ry],[Rz,0,-Rx],
    [-Ry,Rx,0]]) and -1 for the NGS/HTDP convention (Pearson & Snay 2013 eq. 1:
    [[0,Rz,-Ry],[-Rz,0,Rx],[Ry,-Rx,0]]). The two differ only by the rotation sign; a
    parameter set is only right with the convention it was published in."""

    def __init__(self, name, epoch, t_m, t_rate_m, r_mas, r_rate_mas, s_ppb, s_rate_ppb,
                 sign, source):
        self.name = name
        self.epoch = epoch
        self.t_m = t_m
        self.t_rate_m = t_rate_m
        self.r_mas = r_mas
        self.r_rate_mas = r_rate_mas
        self.s_ppb = s_ppb
        self.s_rate_ppb = s_rate_ppb
        self.sign = sign
        self.source = source

    def at(self, epoch):
        """(T metres, R radians, s) at `epoch` (decimal years)."""
        dt = epoch - self.epoch
        t = tuple(v + r * dt for v, r in zip(self.t_m, self.t_rate_m))
        r = tuple((v + rr * dt) * MAS for v, rr in zip(self.r_mas, self.r_rate_mas))
        s = (self.s_ppb + self.s_rate_ppb * dt) * PPB
        return t, r, s

    def forward(self, xyz, epoch):
        t, r, s = self.at(epoch)
        x, y, z = xyz
        rx, ry, rz = r
        cross = (ry * z - rz * y, rz * x - rx * z, rx * y - ry * x)
        return tuple(ti + (1 + s) * vi + self.sign * ci for ti, vi, ci in zip(t, xyz, cross))

    def inverse(self, xyz, epoch):
        """The x with forward(x) == xyz, by fixed-point iteration: the transformation
        is the identity to a part in a million, so each step gains six digits."""
        guess = tuple(xyz)
        for _ in range(8):
            image = self.forward(guess, epoch)
            guess = tuple(g - (i - v) for g, i, v in zip(guess, image, xyz))
        return guess


# IERS/IGN "Transformation parameters from ITRF2020 to past ITRFs" (itrf.ign.fr, 2022):
# T1 T2 T3 in mm, D in ppb, R1 R2 R3 in mas, with rates per year, at epoch 2015.0.
# ITRF2020 -> ITRF2014: -1.4 -0.9 1.4 mm, -0.42 ppb, 0 0 0 mas; rates 0.0 -0.1 0.2 mm/a,
# 0.00 ppb/a, 0 mas/a. ITRF2020 -> ITRF2008: 0.2 1.0 3.3 mm, -0.29 ppb, 0 0 0 mas; rates
# 0.0 -0.1 0.1 mm/a, 0.03 ppb/a, 0 mas/a. (The same table as the private script that
# this module replaces used for ITRF2008.)
ITRF2020_TO_ITRF2014 = Helmert(
    "ITRF2020 -> ITRF2014", 2015.0, (-0.0014, -0.0009, 0.0014), (0.0, -0.0001, 0.0002),
    (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), -0.42, 0.0, +1,
    "IERS/IGN ITRF2020 transformation parameters, epoch 2015.0")
ITRF2020_TO_ITRF2008 = Helmert(
    "ITRF2020 -> ITRF2008", 2015.0, (0.0002, 0.0010, 0.0033), (0.0, -0.0001, 0.0001),
    (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), -0.29, 0.03, +1,
    "IERS/IGN ITRF2020 transformation parameters, epoch 2015.0")
# NGS: ITRF2008 -> NAD83(2011), Pearson & Snay 2013 Table (as in HTDP 3.x): T 0.99343
# -1.90331 -0.52655 m, rates 0.00079 -0.00060 -0.00134 m/a; R 25.91467 9.42645 11.59935
# mas, rates 0.06667 -0.75744 -0.05133 mas/a; s 1.71504 ppb, rate -0.10201 ppb/a; epoch
# 1997.00; HTDP rotation convention (sign -1 here).
ITRF2008_TO_NAD83_2011 = Helmert(
    "ITRF2008 -> NAD83(2011)", 1997.0, (0.99343, -1.90331, -0.52655),
    (0.00079, -0.00060, -0.00134), (25.91467, 9.42645, 11.59935),
    (0.06667, -0.75744, -0.05133), 1.71504, -0.10201, -1,
    "NGS, Pearson & Snay 2013 (HTDP), epoch 1997.00")


def itrf2020_to_itrf2014(xyz, epoch):
    return ITRF2020_TO_ITRF2014.forward(xyz, epoch)


def itrf2014_to_itrf2020(xyz, epoch):
    return ITRF2020_TO_ITRF2014.inverse(xyz, epoch)


def itrf2020_to_itrf2008(xyz, epoch):
    return ITRF2020_TO_ITRF2008.forward(xyz, epoch)


def itrf2008_to_itrf2020(xyz, epoch):
    return ITRF2020_TO_ITRF2008.inverse(xyz, epoch)


def itrf2008_to_nad83_2011(xyz, epoch):
    return ITRF2008_TO_NAD83_2011.forward(xyz, epoch)


def nad83_2011_to_itrf2008(xyz, epoch):
    return ITRF2008_TO_NAD83_2011.inverse(xyz, epoch)


def itrf2020_to_nad83_2011(xyz, epoch):
    """ITRF2020 ECEF at `epoch` (decimal years) to NAD83(2011) ECEF: through ITRF2008
    with the IERS parameters, then the NGS parameters at the same epoch."""
    return itrf2008_to_nad83_2011(itrf2020_to_itrf2008(xyz, epoch), epoch)


def nad83_2011_to_itrf2020(xyz, epoch):
    return itrf2008_to_itrf2020(nad83_2011_to_itrf2008(xyz, epoch), epoch)


_FROM_ITRF2020 = {"ITRF2020": lambda xyz, epoch: tuple(xyz),
                  "ITRF2014": itrf2020_to_itrf2014, "ITRF2008": itrf2020_to_itrf2008,
                  "NAD83(2011)": itrf2020_to_nad83_2011}
_TO_ITRF2020 = {"ITRF2020": lambda xyz, epoch: tuple(xyz),
                "ITRF2014": itrf2014_to_itrf2020, "ITRF2008": itrf2008_to_itrf2020,
                "NAD83(2011)": nad83_2011_to_itrf2020}


def parse_frame(text, option="frame"):
    """The frame named by an option as its canonical name (one of FRAMES), or "" for an
    empty option (no frame declared). Case and spaces do not matter. A name that is not
    known raises ValueError naming the option, never repeating the text."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError(f"{option} must be text: one of {', '.join(FRAMES)}, or empty")
    key = "".join(text.split()).upper()
    if not key:
        return ""
    for frame in FRAMES:
        if key == frame.upper():
            return frame
    raise ValueError(f"{option} must be one of {', '.join(FRAMES)}, or empty")


def transform(xyz, from_frame, to_frame, epoch):
    """ECEF `xyz` of a point in `from_frame` at `epoch` as its ECEF in `to_frame`, at
    the same epoch. The frames are canonical names (parse_frame)."""
    if from_frame not in _TO_ITRF2020 or to_frame not in _FROM_ITRF2020:
        raise ValueError(f"frames must be one of {', '.join(FRAMES)}")
    return _FROM_ITRF2020[to_frame](_TO_ITRF2020[from_frame](xyz, epoch), epoch)


def expected_offset_enu(our_ecef, station_frame, epoch, base_frame="ITRF2020"):
    """East, north and up, in metres, of our antenna's coordinate in `station_frame`
    minus its coordinate in `base_frame` (the frame `our_ecef` is in), at `epoch`: what
    a solution in the station's frame should differ from our 1005 by, with the reference
    check's sign (station frame minus ours). Zero when the frames are the same."""
    station = transform(our_ecef, base_frame, station_frame, epoch)
    return ecef_to_enu(our_ecef, station)


# ---- geodetic ----

def ecef_to_geodetic(x, y, z, ellipsoid=GRS80):
    """ECEF metres to latitude and longitude in degrees and ellipsoidal height."""
    a, f = ellipsoid
    e2 = f * (2 - f)
    p = math.hypot(x, y)
    lon = math.atan2(y, x)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        lat = math.atan2(z + e2 * n * sin_lat, p)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    height = p * cos_lat + z * sin_lat - a * math.sqrt(1 - e2 * sin_lat * sin_lat)
    return math.degrees(lat), math.degrees(lon), height


def geodetic_to_ecef(lat, lon, height, ellipsoid=GRS80):
    a, f = ellipsoid
    e2 = f * (2 - f)
    lat, lon = math.radians(lat), math.radians(lon)
    sin_lat = math.sin(lat)
    n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    return ((n + height) * math.cos(lat) * math.cos(lon),
            (n + height) * math.cos(lat) * math.sin(lon),
            (n * (1 - e2) + height) * sin_lat)


def ecef_to_enu(origin, point, ellipsoid=GRS80):
    """East, north and up of `point` seen from `origin` (both ECEF metres): the
    difference rotated into the local tangent plane at the origin. A rotation, so it
    is exact at any distance; a mistyped digit can put a position thousands of
    kilometres away, where a flat approximation would be meaningless."""
    lat, lon, _height = ecef_to_geodetic(*origin, ellipsoid=ellipsoid)
    lat, lon = math.radians(lat), math.radians(lon)
    dx, dy, dz = (p - o for p, o in zip(point, origin))
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return east, north, up


def decimal_year(timestamp):
    """A Unix time as a decimal year (UTC): 2026-11-06 is 2026.85."""
    when = datetime.fromtimestamp(timestamp, timezone.utc)
    start = datetime(when.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(when.year + 1, 1, 1, tzinfo=timezone.utc)
    return when.year + (when - start) / (end - start)


EPOCH_YEARS = (1990.0, 2100.0)


def parse_epoch(text, option="epoch"):
    """An epoch option as a decimal year: "" or None for none (today's date is used),
    a date "YYYY-MM-DD" (UTC), or a decimal year such as 2026.85, within EPOCH_YEARS.
    A ValueError names the option, never the text."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError(f"{option} must be a date YYYY-MM-DD or a decimal year, or empty")
    text = text.strip()
    if not text:
        return None
    try:
        when = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        value = decimal_year(when.timestamp())
    except ValueError:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{option} must be a date YYYY-MM-DD or a decimal year, "
                             "or empty") from None
    if not math.isfinite(value) or not EPOCH_YEARS[0] <= value <= EPOCH_YEARS[1]:
        raise ValueError(f"{option} must be a year between {EPOCH_YEARS[0]:.0f} and "
                         f"{EPOCH_YEARS[1]:.0f}")
    return value
