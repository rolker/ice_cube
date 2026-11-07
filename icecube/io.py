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
an origin. The UTM forward projection is the Krueger series (Karney 2011), accurate to
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


@dataclass
class Site:
    """Where local (0, 0, 0) is. `origin` is UTM (E, N) in the given zone; `z0` is
    subtracted from every height. Never print these."""
    zone: int = 19
    origin: tuple = (0.0, 0.0)
    z0: float = 0.0
    northern: bool = True

    @classmethod
    def from_config(cls, cfg: dict, base_dir: Path | None = None):
        site = dict(cfg.get("site", {}))
        sc = site.get("site_config")
        if sc and "origin" not in site:
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
                   northern=bool(site.get("northern", True)))

    def local(self, lat, lon, h):
        x, y = utm_forward(lat, lon, self.zone, self.northern)
        return x - self.origin[0], y - self.origin[1], np.asarray(h, dtype=float) - self.z0


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

    def __post_init__(self):
        n = len(self.z)
        if self.pass_id is None:
            self.pass_id = np.full(n, -1, dtype=int)
        for name in ("t", "x", "y", "z", "sz", "sh", "quality", "remark", "seq", "pass_id"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"Points.{name} has length {len(getattr(self, name))}, expected {n}")

    def __len__(self):
        return len(self.z)

    def subset(self, mask):
        mask = np.asarray(mask)
        return Points(t=self.t[mask], x=self.x[mask], y=self.y[mask], z=self.z[mask],
                      sz=self.sz[mask], sh=self.sh[mask], quality=self.quality[mask],
                      survey=self.survey, remark=self.remark[mask], seq=self.seq[mask],
                      pass_id=self.pass_id[mask], frame=self.frame, date=self.date)

    def with_defaults(self, sigma_z, sigma_h):
        """Fill missing sigmas with the configured defaults."""
        sz = np.where(np.isfinite(self.sz), self.sz, sigma_z)
        sh = np.where(np.isfinite(self.sh), self.sh, sigma_h)
        return Points(t=self.t, x=self.x, y=self.y, z=self.z, sz=sz, sh=sh,
                      quality=self.quality, survey=self.survey, remark=self.remark,
                      seq=self.seq, pass_id=self.pass_id, frame=self.frame, date=self.date)

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
                      pass_id=cat("pass_id"), frame=parts[0].frame, date=max(p.date for p in parts))


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


def read_polygon_outline(path, site: Site, name):
    """The (x, y) ring of one named Polygon feature, local metres, closing repeat dropped."""
    d = json.loads(Path(path).read_text())
    for ft in d["features"]:
        g = ft["geometry"]
        if g["type"] == "Polygon" and (ft.get("properties") or {}).get("NAME") == name:
            ring = np.array(g["coordinates"][0], float)
            if len(ring) > 1 and np.all(ring[0] == ring[-1]):
                ring = ring[:-1]
            x, y, _ = site.local(ring[:, 1], ring[:, 0], ring[:, 2] if ring.shape[1] > 2 else 0.0)
            return np.c_[x, y]
    raise ValueError(f"{path}: no Polygon named {name!r}")


def _parse_swmaps_time(s):
    """'10/18/2026 14:10:51.500 EDT' -> seconds. The zone label is dropped: times are
    only compared within one survey."""
    s = s.strip()
    parts = s.split()
    if len(parts) >= 2:
        s = parts[0] + " " + parts[1]
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
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
        for row in csv.DictReader(fh):
            la = _col(row, "Latitude", "Lat"); lo = _col(row, "Longitude", "Lon"); el = _col(row, "Elevation")
            if la is None or lo is None or el is None:
                continue
            lat.append(float(la)); lon.append(float(lo)); h.append(float(el))
            t.append(_parse_swmaps_time(_col(row, "Time", default="")))
            sz.append(float(_col(row, "Vertical Accuracy", default="nan")))
            sh.append(float(_col(row, "Horizontal Accuracy", default="nan")))
            q.append(int(float(_col(row, "Fix ID", default="-1"))))
            rem.append(_col(row, "Remarks", "REMARKS", "Feature Name", default="") or "")
    x, y, z = site.local(np.array(lat, float), np.array(lon, float), np.array(h, float))
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
            if tod < last - 1:
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


READERS = {"swmaps_geojson": read_swmaps_geojson, "swmaps_csv": read_swmaps_csv, "enu_csv": read_enu_csv}


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
    return pts.subset(keep)
