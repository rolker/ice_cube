"""Readers: every survey format becomes one Points table in local metres.

Formats:
  swmaps_geojson  SW Maps GeoJSON export, Point features (z = third coordinate,
                  REMARKS property) and Polygon features (each ring vertex is a point).
                  No time, no sigma.
  swmaps_csv      SW Maps CSV export (Latitude/Longitude/Elevation, Time, Fix ID,
                  Horizontal/Vertical Accuracy, Averaged Count, Remarks).
  enu_csv         A 4 Hz E/N/U table already in local metres (columns utc, quality,
                  E_m, N_m, U_m, gst_sig_lat, gst_sig_lon, gst_sig_alt); the date comes
                  from the survey config because the file holds only the time of day.

Local x, y are UTM metres minus a site origin, the same convention as
analysis/config.py (`origin_utm19n` in site_config.json), so the two packages share
an origin. Alternatively (`[site] origin_geodetic = [lat, lon, h]`) x, y, z are exact
ENU about a geodetic origin (x east, y north, z up: ECEF difference rotated at the
origin), the frame of the QGIS session import and the logger map, so local
coordinates match theirs; then z is height above the origin's tangent plane. The UTM forward projection is the Krueger series (Karney 2011), accurate to
well under a millimetre at these distances, pure numpy. Heights are kept as given
(ellipsoidal if the file is ellipsoidal) minus the configured origin height, so nothing
in this package needs the absolute height.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# GRS80 ellipsoid (NAD83) ; WGS84 differs by 0.1 mm in b, irrelevant here.
_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996


def utm_forward(lat_deg, lon_deg, zone, northern=True):
    """Geodetic degrees -> UTM easting, northing (m) for a given zone.

    Krueger series to alpha_6 (Karney 2011, "Transverse Mercator with an accuracy of a
    few nanometers", the standard form). Vectorised over numpy arrays.
    """
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = _F / (2 - _F)
    A = _A / (1 + n) * (1 + n**2 / 4 + n**4 / 64 + n**6 / 256)
    alpha = _alpha(n)
    e = math.sqrt(_F * (2 - _F))
    # conformal latitude
    s = np.sinh(e * np.arctanh(e * np.sin(lat)))
    tau1 = np.tan(lat) * np.sqrt(1 + s**2) - s * np.sqrt(1 + np.tan(lat) ** 2)
    dl = lon - lon0
    xi1 = np.arctan2(tau1, np.cos(dl))
    eta1 = np.arcsinh(np.sin(dl) / np.sqrt(tau1**2 + np.cos(dl) ** 2))
    xi = xi1.copy()
    eta = eta1.copy()
    for j, a in enumerate(alpha, start=1):
        xi = xi + a * np.sin(2 * j * xi1) * np.cosh(2 * j * eta1)
        eta = eta + a * np.cos(2 * j * xi1) * np.sinh(2 * j * eta1)
    x = _K0 * A * eta + 500000.0
    y = _K0 * A * xi + (0.0 if northern else 10000000.0)
    return x, y


def utm_inverse(x, y, zone, northern=True):
    """UTM easting, northing (m) -> geodetic degrees. Used by the tests to check
    utm_forward by round trip (the inverse uses its own, independent coefficients)."""
    x = (np.asarray(x, dtype=float) - 500000.0)
    y = np.asarray(y, dtype=float) - (0.0 if northern else 10000000.0)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = _F / (2 - _F)
    A = _A / (1 + n) * (1 + n**2 / 4 + n**4 / 64 + n**6 / 256)
    beta = _beta(n)
    e = math.sqrt(_F * (2 - _F))
    xi = y / (_K0 * A)
    eta = x / (_K0 * A)
    xi1 = xi.copy()
    eta1 = eta.copy()
    for j, b in enumerate(beta, start=1):
        xi1 = xi1 - b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta1 = eta1 - b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    tau1 = np.sin(xi1) / np.sqrt(np.sinh(eta1) ** 2 + np.cos(xi1) ** 2)
    dl = np.arctan2(np.sinh(eta1), np.cos(xi1))
    # Newton iteration from conformal latitude tau1 to geodetic tau
    tau = tau1.copy()
    for _ in range(6):
        s = np.sinh(e * np.arctanh(e * tau / np.sqrt(1 + tau**2)))
        t1 = tau * np.sqrt(1 + s**2) - s * np.sqrt(1 + tau**2)
        dt = (tau1 - t1) / np.sqrt(1 + t1**2) * (1 + (1 - e**2) * tau**2) / ((1 - e**2) * np.sqrt(1 + tau**2))
        tau = tau + dt
    return np.degrees(np.arctan(tau)), np.degrees(lon0 + dl)


def _alpha(n):
    return (
        n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180 - 127 * n**5 / 288 + 7891 * n**6 / 37800,
        13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440 + 281 * n**5 / 630 - 1983433 * n**6 / 1935360,
        61 * n**3 / 240 - 103 * n**4 / 140 + 15061 * n**5 / 26880 + 167603 * n**6 / 181440,
        49561 * n**4 / 161280 - 179 * n**5 / 168 + 6601661 * n**6 / 7257600,
        34729 * n**5 / 80640 - 3418889 * n**6 / 1995840,
        212378941 * n**6 / 319334400,
    )


def _beta(n):
    return (
        n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96 - n**4 / 360 - 81 * n**5 / 512 + 96199 * n**6 / 604800,
        n**2 / 48 + n**3 / 15 - 437 * n**4 / 1440 + 46 * n**5 / 105 - 1118711 * n**6 / 3870720,
        17 * n**3 / 480 - 37 * n**4 / 840 - 209 * n**5 / 4480 + 5569 * n**6 / 90720,
        4397 * n**4 / 161280 - 11 * n**5 / 504 - 830251 * n**6 / 7257600,
        4583 * n**5 / 161280 - 108847 * n**6 / 3991680,
        20648693 * n**6 / 638668800,
    )


def geodetic_to_ecef(lat_deg, lon_deg, h):
    """Geodetic degrees and ellipsoidal metres -> ECEF X, Y, Z (m), arrays."""
    e2 = _F * (2 - _F)
    la, lo = np.radians(lat_deg), np.radians(lon_deg)
    n = _A / np.sqrt(1 - e2 * np.sin(la) ** 2)
    h = np.asarray(h, dtype=float)
    return ((n + h) * np.cos(la) * np.cos(lo), (n + h) * np.cos(la) * np.sin(lo), (n * (1 - e2) + h) * np.sin(la))


def enu(lat_deg, lon_deg, h, lat0, lon0, h0):
    """Exact east, north, up (m) of geodetic points about a geodetic origin."""
    x, y, z = geodetic_to_ecef(lat_deg, lon_deg, h)
    x0, y0, z0 = geodetic_to_ecef(lat0, lon0, h0)
    dx, dy, dz = x - x0, y - y0, z - z0
    sl, cl = math.sin(math.radians(lon0)), math.cos(math.radians(lon0))
    sp, cp = math.sin(math.radians(lat0)), math.cos(math.radians(lat0))
    return (-sl * dx + cl * dy, -sp * cl * dx - sp * sl * dy + cp * dz, cp * cl * dx + cp * sl * dy + sp * dz)


@dataclass
class Site:
    """Where local (0, 0, 0) is. `origin` is UTM (E, N) in the given zone; `z0` is
    subtracted from every height. If `origin_geodetic` (lat, lon, h) is set, local
    coordinates are exact ENU about that point instead (UTM settings unused; z0 is
    still subtracted, normally 0). Never print these."""
    zone: int = 19
    origin: tuple = (0.0, 0.0)
    z0: float = 0.0
    northern: bool = True
    origin_geodetic: tuple | None = None

    @classmethod
    def from_config(cls, cfg: dict, base_dir: Path | None = None):
        site = dict(cfg.get("site", {}))
        sc = site.get("site_config")
        og = site.get("origin_geodetic")
        if site.get("origin_from_gpkg"):
            if og is not None or "origin" in site:
                raise ValueError("[site]: give one of origin, origin_geodetic, origin_from_gpkg")
            p = Path(site["origin_from_gpkg"])
            if base_dir is not None and not p.is_absolute():
                p = base_dir / p
            og = gpkg_origin(p)
        if og is not None and "origin" in site:
            raise ValueError("[site]: give origin (UTM) or origin_geodetic (ENU), not both")
        if sc and "origin" not in site and og is None:
            p = Path(sc)
            if base_dir is not None and not p.is_absolute():
                p = base_dir / p
            j = json.loads(p.read_text())
            site["origin"] = j["origin_utm19n"]
            epsg = int(j.get("epsg", 32619))
            site.setdefault("utm_zone", epsg % 100)
            site.setdefault("northern", 32600 <= epsg < 32700)
        return cls(zone=int(site.get("utm_zone", 19)),
                   origin=tuple(float(v) for v in site.get("origin", (0.0, 0.0))),
                   z0=float(site.get("z0", 0.0)),
                   northern=bool(site.get("northern", True)),
                   origin_geodetic=None if og is None else tuple(float(v) for v in og))

    def local(self, lat, lon, h):
        if self.origin_geodetic is not None:
            lat0, lon0, h0 = self.origin_geodetic
            hh = np.broadcast_to(np.asarray(h, dtype=float), np.shape(lat))
            x, y, z = enu(lat, lon, hh, lat0, lon0, h0)
            return x, y, z - self.z0
        x, y = utm_forward(lat, lon, self.zone, self.northern)
        return x - self.origin[0], y - self.origin[1], np.asarray(h, dtype=float) - self.z0


def gpkg_origin(path):
    """(lat, lon, h) of the local frame origin stored by the QGIS session import in a
    season GeoPackage (table icecube_meta, keys origin_lat/lon/h)."""
    import sqlite3
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        m = dict(con.execute("SELECT key, value FROM icecube_meta"))
    finally:
        con.close()
    if not m.get("origin_lat"):
        raise ValueError(f"{path}: no origin_lat/lon/h in icecube_meta")
    return float(m["origin_lat"]), float(m["origin_lon"]), float(m["origin_h"])


def gpkg_local_wkt(path):
    """WKT of the local CRS the session import stored (icecube_meta local_wkt), or None."""
    import sqlite3
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        m = dict(con.execute("SELECT key, value FROM icecube_meta"))
    finally:
        con.close()
    return m.get("local_wkt")


@dataclass
class Points:
    """One point table. All arrays have the same length n.

    t        seconds since the Unix epoch (NaN when the file has no time)
    x, y, z  local metres
    sz, sh   vertical / horizontal one-sigma (m), NaN when the file has none
    quality  GGA fix quality (4 fixed, 5 float, ...), -1 unknown
    survey   survey id (same for the whole table)
    pass_id  filled by passes.segment, -1 before
    remark   free text per point ("" when none)
    seq      original order in the file
    """
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    sz: np.ndarray
    sh: np.ndarray
    quality: np.ndarray
    survey: str
    remark: np.ndarray
    seq: np.ndarray
    pass_id: np.ndarray = field(default=None)
    frame: str = ""
    date: str = ""
    anchor: np.ndarray = field(default=None)   # anchor (repeat mark) id per point, "" for none
    group: np.ndarray = field(default=None)    # session (or other grouping) per point, for validation
    prior: float | None = None                 # pass wander prior of this survey (None: run default)
    common_prior: float = 0.0                  # prior of a bias shared by all passes of this survey
    pass_names: list | None = None             # original pass labels, index = pass_id (soundings reader)

    def __post_init__(self):
        n = len(self.z)
        if self.pass_id is None:
            self.pass_id = np.full(n, -1, dtype=int)
        if self.anchor is None:
            self.anchor = np.full(n, "", dtype=object)
        if self.group is None:
            self.group = np.full(n, self.survey, dtype=object)
        for name in ("t", "x", "y", "z", "sz", "sh", "quality", "remark", "seq", "pass_id", "anchor", "group"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"Points.{name} has length {len(getattr(self, name))}, expected {n}")

    def __len__(self):
        return len(self.z)

    def _meta(self):
        return dict(survey=self.survey, frame=self.frame, date=self.date, prior=self.prior,
                    common_prior=self.common_prior, pass_names=self.pass_names)

    def replace(self, **changes):
        """A copy with some per-point arrays (same length) or table attributes replaced."""
        d = {k: getattr(self, k) for k in ("t", "x", "y", "z", "sz", "sh", "quality", "remark", "seq",
                                           "pass_id", "anchor", "group")}
        d.update(self._meta())
        d.update(changes)
        return Points(**d)

    def subset(self, mask):
        mask = np.asarray(mask)
        return Points(t=self.t[mask], x=self.x[mask], y=self.y[mask], z=self.z[mask],
                      sz=self.sz[mask], sh=self.sh[mask], quality=self.quality[mask],
                      remark=self.remark[mask], seq=self.seq[mask], pass_id=self.pass_id[mask],
                      anchor=self.anchor[mask], group=self.group[mask], **self._meta())

    def with_defaults(self, sigma_z, sigma_h, floor=0.001):
        """Fill missing sigmas with the configured defaults; no sigma below `floor`
        (a receiver can report 0, which would give an infinite weight)."""
        sz = np.maximum(np.where(np.isfinite(self.sz), self.sz, sigma_z), floor)
        sh = np.maximum(np.where(np.isfinite(self.sh), self.sh, sigma_h), floor)
        return self.replace(sz=sz, sh=sh)

    @staticmethod
    def concat(parts: list["Points"]):
        if not parts:
            raise ValueError("nothing to concatenate")
        survey = parts[0].survey
        frames = {p.frame for p in parts}
        if len(frames) > 1:
            raise ValueError(f"cannot concatenate surveys in different frames: {sorted(frames)}")
        cat = lambda name: np.concatenate([getattr(p, name) for p in parts])  # noqa: E731
        return Points(t=cat("t"), x=cat("x"), y=cat("y"), z=cat("z"), sz=cat("sz"), sh=cat("sh"),
                      quality=cat("quality"), survey=survey, remark=cat("remark"), seq=cat("seq"),
                      pass_id=cat("pass_id"), anchor=cat("anchor"), group=cat("group"),
                      frame=parts[0].frame, date=max(p.date for p in parts), prior=parts[0].prior,
                      common_prior=parts[0].common_prior)


def _make(survey, t, x, y, z, sz=None, sh=None, quality=None, remark=None, frame="", date=""):
    n = len(z)
    nan = np.full(n, np.nan)
    return Points(t=np.asarray(t, float) if t is not None else nan.copy(),
                  x=np.asarray(x, float), y=np.asarray(y, float), z=np.asarray(z, float),
                  sz=np.asarray(sz, float) if sz is not None else nan.copy(),
                  sh=np.asarray(sh, float) if sh is not None else nan.copy(),
                  quality=np.asarray(quality, int) if quality is not None else np.full(n, -1, int),
                  survey=survey,
                  remark=np.asarray(remark if remark is not None else [""] * n, dtype=object),
                  seq=np.arange(n), frame=frame, date=date)


# ----------------------------------------------------------------------------- readers

def read_swmaps_geojson(path, site: Site, survey="", geometry="point", names=None,
                        frame="", date=""):
    """SW Maps GeoJSON. geometry="point" takes Point features; "polygon" takes the
    vertices of Polygon features (closing repeat dropped), optionally only those whose
    NAME is in `names`. The remark is REMARKS for points and NAME for polygon vertices."""
    d = json.loads(Path(path).read_text())
    lat, lon, h, rem = [], [], [], []
    for ft in d["features"]:
        g = ft["geometry"]
        p = ft.get("properties") or {}
        if geometry == "point" and g["type"] == "Point":
            c = g["coordinates"]
            if len(c) < 3:
                raise ValueError(f"{path}: point without a height")
            lon.append(c[0]); lat.append(c[1]); h.append(c[2]); rem.append(p.get("REMARKS") or "")
        elif geometry == "polygon" and g["type"] == "Polygon":
            name = p.get("NAME") or ""
            if names and name not in names:
                continue
            ring = g["coordinates"][0]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            for c in ring:
                lon.append(c[0]); lat.append(c[1]); h.append(c[2]); rem.append(name)
    x, y, z = site.local(np.array(lat, float), np.array(lon, float), np.array(h, float))
    return _make(survey, None, x, y, z, remark=rem, frame=frame, date=date)


def read_polygons(path, site: Site, names=None, kinds=None):
    """[(name, kind, (x, y) ring)] of the Polygon features of a GeoJSON (lon, lat), local
    metres, closing repeat dropped; only those whose NAME/name is in `names` or whose kind is
    in `kinds` when either is given (names match exactly or as a prefix + space)."""
    d = json.loads(Path(path).read_text())
    out = []
    for ft in d["features"]:
        g = ft.get("geometry") or {}
        if g.get("type") != "Polygon":
            continue
        p = ft.get("properties") or {}
        nm = str(p.get("NAME", p.get("name", "")) or "")
        kd = str(p.get("kind", "") or "")
        if names is not None or kinds is not None:
            hit = (names is not None and any(nm == n or nm.startswith(n + " ") for n in names)) or (
                kinds is not None and kd in kinds)
            if not hit:
                continue
        ring = np.array([c[:2] for c in g["coordinates"][0]], float)
        if len(ring) > 1 and np.all(ring[0] == ring[-1]):
            ring = ring[:-1]
        x, y, _ = site.local(ring[:, 1], ring[:, 0], 0.0)
        out.append((nm, kd, np.c_[x, y]))
    return out


def read_polygon_outline(path, site: Site, name):
    """The (x, y) ring of one named Polygon feature (property NAME or name), local metres,
    closing repeat dropped."""
    for nm, _, ring in read_polygons(path, site):
        if nm == name:
            return ring
    raise ValueError(f"{path}: no Polygon named {name!r}")


def _parse_swmaps_time(s):
    """'10/18/2026 14:10:51.500 EDT' or '10/18/2026 2:10:51.500 PM EDT' -> seconds.
    The zone label is dropped: times are only compared within one survey. NaN when
    the text does not parse (the CSV reader refuses a file with any NaN time)."""
    parts = s.strip().split()
    if len(parts) < 2:
        return float("nan")
    tag = parts[2].upper().replace(".", "") if len(parts) >= 3 else ""
    ampm = tag if tag in ("AM", "PM") else ""
    if tag and not ampm and len(tag) <= 2:
        return float("nan")   # a short third token that is not AM/PM: do not guess
    txt = parts[0] + " " + parts[1] + (" " + ampm if ampm else "")
    hour = "%I" if ampm else "%H"
    tail = " %p" if ampm else ""
    # month-first is tried first (SW Maps on a US phone); the day-first fallback is only
    # reached for days > 12, so a day-first file crossing midnight on the 12th/13th
    # would split a pass. The survey date itself comes from the config.
    for day in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        for sec in (":%S.%f", ":%S"):
            try:
                return datetime.strptime(txt, f"{day} {hour}:%M{sec}{tail}").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return float("nan")


def _col(row, *names, default=None):
    for n in names:
        if n in row and row[n] not in ("", None):
            return row[n]
    return default


def read_swmaps_csv(path, site: Site, survey="", frame="", date=""):
    """SW Maps CSV export (feature layer or FEATURE_POINTS)."""
    lat, lon, h, t, sz, sh, q, rem = [], [], [], [], [], [], [], []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        has_time = "Time" in (reader.fieldnames or [])
        for row in reader:
            la = _col(row, "Latitude", "Lat"); lo = _col(row, "Longitude", "Lon"); el = _col(row, "Elevation")
            if la is None or lo is None or el is None:
                continue
            lat.append(float(la)); lon.append(float(lo)); h.append(float(el))
            t.append(_parse_swmaps_time(_col(row, "Time", default="")) if has_time else float("nan"))
            sz.append(float(_col(row, "Vertical Accuracy", default="nan")))
            sh.append(float(_col(row, "Horizontal Accuracy", default="nan")))
            q.append(int(float(_col(row, "Fix ID", default="-1"))))
            rem.append(_col(row, "Remarks", "REMARKS", "Feature Name", default="") or "")
    x, y, z = site.local(np.array(lat, float), np.array(lon, float), np.array(h, float))
    bad = int(np.sum(np.isnan(t))) if has_time else 0
    if bad:
        raise ValueError(f"{path}: {bad} of {len(t)} Time values did not parse (expected "
                         f"'MM/DD/YYYY HH:MM:SS[.fff] [AM|PM] ZONE'); a NaN time would silently "
                         f"disable the gap, settle and max_s pass rules")
    return _make(survey, t, x, y, z, sz=sz, sh=sh, quality=q, remark=rem, frame=frame, date=date)


def read_enu_csv(path, survey="", date="", frame="", offset=(0.0, 0.0, 0.0), pole_height=0.0):
    """4 Hz E/N/U CSV in local metres (columns utc, quality, E_m, N_m, U_m,
    gst_sig_lat, gst_sig_lon, gst_sig_alt). `offset` moves the file's origin to the
    site origin; `pole_height` is subtracted from U (antenna -> ground). `date` is
    YYYY-MM-DD and gives the day for the utc column; a time of day that goes backwards
    is taken as midnight rollover."""
    t, x, y, z, sz, sh, q = [], [], [], [], [], [], []
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() if date else 0.0
    last = -1.0
    roll = 0.0
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            hh, mm, ss = row["utc"].split(":")
            tod = int(hh) * 3600 + int(mm) * 60 + float(ss)
            if tod < last - 3600:   # time of day went backwards by hours: midnight
                roll += 86400
            last = tod
            t.append(day + roll + tod)
            x.append(float(row["E_m"]) + offset[0]); y.append(float(row["N_m"]) + offset[1])
            z.append(float(row["U_m"]) + offset[2] - pole_height)
            sl = float(row.get("gst_sig_lat") or "nan"); so = float(row.get("gst_sig_lon") or "nan")
            sh.append(math.hypot(sl, so) / math.sqrt(2) if np.isfinite(sl) and np.isfinite(so) else float("nan"))
            sz.append(float(row.get("gst_sig_alt") or "nan"))
            q.append(int(float(row.get("quality") or -1)))
    return _make(survey, t, x, y, z, sz=sz, sh=sh, quality=q, frame=frame, date=date)


SOUNDINGS_COLS = ("sid", "source", "session", "kind", "class", "use", "t_unix", "x_m", "y_m", "z_m",
                  "sigma_z", "sigma_xy", "pass_id", "prior_m", "common_prior_m", "anchor", "fix")


def _soundings_rows(path, table="soundings"):
    path = Path(path)
    if path.suffix.lower() == ".gpkg":
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            if not cols:
                raise ValueError(f"{path}: no table {table!r} (run analysis/qgis/soundings.py)")
            q = ", ".join(f'"{c}"' for c in SOUNDINGS_COLS)
            rows = [dict(zip(SOUNDINGS_COLS, r)) for r in con.execute(f'SELECT {q} FROM "{table}"')]
        finally:
            con.close()
        return rows
    with open(path, newline="") as fh:
        return [{k: r.get(k) for k in SOUNDINGS_COLS} for r in csv.DictReader(fh)]


def read_soundings(path, survey="", frame="", date="", sources=None, classes=("ground", "anchor"),
                   kinds=None, use_only=True, table="soundings", site=None):
    """The `soundings` table written by analysis/qgis/soundings.py, from the season gpkg or
    its CSV export. x_m, y_m, z_m are already in the gpkg's local frame; when `site` uses an
    ENU origin and the source is a gpkg, the gpkg's origin must be the same point (1 mm).
    Keeps rows with use = 1 (unless use_only is false), of the given sources, classes and
    kinds. sz = sigma_z, sh = sigma_xy, pass_id from the table's pass labels (pass_names),
    anchor = the anchor id of class anchor rows, group = session, remark = sid. The survey's
    prior / common_prior are the median prior_m / common_prior_m of the rows kept."""
    path = Path(path)
    if site is not None and site.origin_geodetic is not None and path.suffix.lower() == ".gpkg":
        o = gpkg_origin(path)
        d = enu(o[0], o[1], o[2], *site.origin_geodetic)
        if math.hypot(*d) > 0.001:
            raise ValueError(f"{path.name}: its local frame origin differs from [site] by "
                             f"{math.hypot(*d):.3f} m; the soundings would be in another frame")
    rows = _soundings_rows(path, table)
    def keep(r):
        if use_only and str(r["use"]) not in ("1", "1.0", "True"):
            return False
        if sources is not None and r["source"] not in sources:
            return False
        if classes is not None and r["class"] not in classes:
            return False
        if kinds is not None and r["kind"] not in kinds:
            return False
        return r["x_m"] not in (None, "") and r["z_m"] not in (None, "")
    rows = [r for r in rows if keep(r)]
    names = sorted({str(r["pass_id"]) for r in rows})
    idx = {n: i for i, n in enumerate(names)}
    f = lambda r, k: float(r[k]) if r[k] not in (None, "") else float("nan")  # noqa: E731
    pts = Points(t=np.array([f(r, "t_unix") for r in rows]), x=np.array([f(r, "x_m") for r in rows]),
                 y=np.array([f(r, "y_m") for r in rows]), z=np.array([f(r, "z_m") for r in rows]),
                 sz=np.array([f(r, "sigma_z") for r in rows]), sh=np.array([f(r, "sigma_xy") for r in rows]),
                 quality=np.array([int(float(r["fix"])) if r["fix"] not in (None, "") else -1 for r in rows], int),
                 survey=survey, remark=np.array([str(r["sid"]) for r in rows], dtype=object),
                 seq=np.arange(len(rows)), pass_id=np.array([idx[str(r["pass_id"])] for r in rows], int),
                 anchor=np.array([str(r["anchor"] or "") if r["class"] == "anchor" else "" for r in rows], dtype=object),
                 group=np.array([str(r["session"]) for r in rows], dtype=object), frame=frame, date=date,
                 pass_names=names)
    pr = [f(r, "prior_m") for r in rows if r["prior_m"] not in (None, "")]
    cp = [f(r, "common_prior_m") for r in rows if r["common_prior_m"] not in (None, "")]
    pts.prior = float(np.median(pr)) if pr else None
    pts.common_prior = float(np.median(cp)) if cp else 0.0
    return pts


READERS = {"swmaps_geojson": read_swmaps_geojson, "swmaps_csv": read_swmaps_csv, "enu_csv": read_enu_csv,
           "soundings": read_soundings}


def read_survey(scfg: dict, site: Site, base_dir: Path | None = None) -> Points:
    """Read one `[[surveys]]` entry from the config into a Points table and apply its
    filters: `keep_quality` (list of fix ids), `exclude_remarks` (substrings, case
    insensitive), `z_min`/`z_max` (local metres), `offset` (dx, dy, dz added), `pole_height`."""
    reader = scfg.get("reader", "swmaps_geojson")
    path = Path(scfg["path"])
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    sid = scfg["id"]
    frame = scfg.get("frame", "")
    date = scfg.get("date", "")
    off = tuple(float(v) for v in scfg.get("offset", (0.0, 0.0, 0.0)))
    pole = float(scfg.get("pole_height", 0.0))
    if reader == "swmaps_geojson":
        pts = read_swmaps_geojson(path, site, sid, geometry=scfg.get("geometry", "point"),
                                  names=scfg.get("names"), frame=frame, date=date)
    elif reader == "swmaps_csv":
        pts = read_swmaps_csv(path, site, sid, frame=frame, date=date)
    elif reader == "enu_csv":
        pts = read_enu_csv(path, sid, date=date, frame=frame, offset=off, pole_height=pole)
        off, pole = (0.0, 0.0, 0.0), 0.0
    elif reader == "soundings":
        pts = read_soundings(path, sid, frame=frame, date=date, sources=scfg.get("sources"),
                             classes=tuple(scfg.get("classes", ("ground", "anchor"))), kinds=scfg.get("kinds"),
                             use_only=bool(scfg.get("use_only", True)), table=scfg.get("table", "soundings"),
                             site=site)
    elif reader == "dem":
        from .dem import read_dem_survey
        pts = read_dem_survey(scfg, path, site, base_dir)
    else:
        raise ValueError(f"unknown reader {reader!r}")
    if off != (0.0, 0.0, 0.0) or pole:
        pts.x = pts.x + off[0]; pts.y = pts.y + off[1]; pts.z = pts.z + off[2] - pole
    keep = np.ones(len(pts), bool)
    if "keep_quality" in scfg:
        keep &= np.isin(pts.quality, [int(v) for v in scfg["keep_quality"]])
    for sub in scfg.get("exclude_remarks", []):
        keep &= np.array([sub.lower() not in str(r).lower() for r in pts.remark])
    if "z_min" in scfg:
        keep &= pts.z >= float(scfg["z_min"])
    if "z_max" in scfg:
        keep &= pts.z <= float(scfg["z_max"])
    if "drop_seq" in scfg:
        keep &= ~np.isin(pts.seq, [int(v) for v in scfg["drop_seq"]])
    out = pts.subset(keep)
    if "wander_prior" in scfg:
        out.prior = float(scfg["wander_prior"])
    if "common_prior" in scfg:
        out.common_prior = float(scfg["common_prior"])
    return out
