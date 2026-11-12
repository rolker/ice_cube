#!/usr/bin/env python3
"""Local, datum-corrected GeoTIFF copies of ArcGIS ImageServer orthophotos (NH GRANIT by
default) in the site's local frame, so QGIS shows them offline and in the right place.

    python3 analysis/qgis/fetch_imagery.py --config site.toml [--datasets NAME ...]
    python3 analysis/qgis/fetch_imagery.py --config site.toml --list   # coverage only

Reads the `[imagery]` section of the site config (see config.example.toml; command-line
arguments take priority) plus `[site.origin]` (or the origin stored in the season gpkg) for
the local frame. For each dataset it:

1. reads the service metadata (native spatial reference, pixel size, band count);
2. maps the local-frame extent (the bounding box of the `extent_layer` GeoJSON plus
   `margin_m`, optionally only its features matching `extent_filter`, e.g. group=core of
   fetch_parcels.py's output; or an explicit `bbox`) into the service's native coordinates
   and snaps it to the native pixel grid, so the server returns its own pixels without
   resampling;
3. downloads that area with exportImage (tiles of at most `tile_px`, default 2048, within
   the service's size limit, one request at a time with a pause, retried on server
   errors), skipping datasets that do not cover it (all-zero result);
4. resamples into a north-up grid in the local frame (x east, y north, metres) at the
   native pixel size rounded to the centimetre, bilinear: each output pixel centre ->
   geodetic (our frame, WGS84/ITRF) -> + datum shift (NAD83 minus our frame, metres east,
   north) - extra shift (the image's own registration error) -> native projection ->
   source pixel;
5. writes `<out_dir>/<dataset>.tif` (RGB + alpha, or grey + alpha; band 4 of the 4-band
   GRANIT orthos is near-infrared and is dropped; DEFLATE, or JPEG with an internal mask per
   `[imagery.compress]`; tiled, with overviews) with the local CRS (WKT from the season
   gpkg's `icecube_meta.local_wkt` when given, else built by local_frame.py), and a
   `<dataset>.json` sidecar (dataset, acquisition years, pixel size, shifts, service,
   fetched UTC).

Shift convention: an image is moved by (-datum_shift + extra_shift) in metres east, north.
GRANIT is NAD83; at the Ice Cube site NAD83(2011) minus ITRF2020 is E +0.561, N -1.193 m,
so NAD83 imagery drawn as if it were WGS84 lands ~1.3 m off, and the copy moves it back.
`extra_shift_m` is for a measured residual of a particular image (see imagery_check.py).

Supported native references: NH State Plane ftUS (wkid 3445, 102710, 3437, 2823) and
UTM 19N NAD83 (26919), with the transverse Mercator in this file; anything else needs
pyproj. Dependencies: numpy (the transform math; tests need only numpy) and rasterio (to
read the downloaded tiles and write the GeoTIFFs). The outputs hold the site location:
never commit them (*.tif is git-ignored).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import re
import sqlite3
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from local_frame import A as WGS84_A, E2 as WGS84_E2, LocalFrame  # noqa: E402

DEFAULT_SERVICE = "https://granit24a.sr.unh.edu/image/rest/services/ImageServices"
DEFAULT_DATASETS = ["NH_2021_2022_6in_RGB", "NH_2015_1ft_RGB", "NH_2010_2011_1ft_RGB",
                    "NH_DOQs_92_98", "LiDAR_Bare_Earth_DEM_shaded_relief_NH_2022"]
US_FT = 1200.0 / 3937.0
GRS80 = (6378137.0, 1 / 298.257222101)


# ----------------------------------------------------------------------------- projection math

class TransverseMercator:
    """Ellipsoidal transverse Mercator (Krueger series to n^4; sub-millimetre within a few
    hundred km of the central meridian), on numpy arrays or floats.

    a, f: ellipsoid; lat0, lon0 (degrees); k0; fe, fn false easting/northing in metres;
    unit: metres per output unit (US_FT for State Plane in US survey feet)."""

    def __init__(self, a, f, lat0, lon0, k0, fe=0.0, fn=0.0, unit=1.0):
        self.lon0, self.k0, self.fe, self.fn, self.unit = math.radians(lon0), k0, fe, fn, unit
        self.e = math.sqrt(f * (2 - f))
        n = f / (2 - f)
        self.n = n
        self.A = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
        self.alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180,
                      13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440,
                      61 * n ** 3 / 240 - 103 * n ** 4 / 140,
                      49561 * n ** 4 / 161280)
        self.beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360,
                     n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440,
                     17 * n ** 3 / 480 - 37 * n ** 4 / 840,
                     4397 * n ** 4 / 161280)
        self.delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3 + 116 * n ** 4 / 45,
                      7 * n ** 2 / 3 - 8 * n ** 3 / 5 - 227 * n ** 4 / 45,
                      56 * n ** 3 / 15 - 136 * n ** 4 / 35,
                      4279 * n ** 4 / 630)
        self.xi0 = float(self._xi_eta(np.radians(lat0), 0.0)[0])

    def _xi_eta(self, phi, dlam):
        e = self.e
        t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
        xp = np.arctan2(t, np.cos(dlam))
        ep = np.arctanh(np.sin(dlam) / np.sqrt(1 + t * t))
        xi, eta = xp.copy() if isinstance(xp, np.ndarray) else xp, ep
        for j, a in enumerate(self.alpha, 1):
            xi = xi + a * np.sin(2 * j * xp) * np.cosh(2 * j * ep)
            eta = eta + a * np.cos(2 * j * xp) * np.sinh(2 * j * ep)
        return xi, eta

    def forward(self, lat, lon):
        """Degrees -> (x, y) in the projection's unit."""
        phi = np.radians(np.asarray(lat, dtype=float))
        dlam = np.radians(np.asarray(lon, dtype=float)) - self.lon0
        xi, eta = self._xi_eta(phi, dlam)
        k = self.k0 * self.A
        return (self.fe + k * eta) / self.unit, (self.fn + k * (xi - self.xi0)) / self.unit

    def inverse(self, x, y):
        """(x, y) in the projection's unit -> (lat, lon) degrees."""
        k = self.k0 * self.A
        xi = (np.asarray(y, dtype=float) * self.unit - self.fn) / k + self.xi0
        eta = (np.asarray(x, dtype=float) * self.unit - self.fe) / k
        xp, ep = xi, eta
        for j, b in enumerate(self.beta, 1):
            xp = xp - b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
            ep = ep - b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        chi = np.arcsin(np.sin(xp) / np.cosh(ep))
        phi = chi
        for j, d in enumerate(self.delta, 1):
            phi = phi + d * np.sin(2 * j * chi)
        lam = self.lon0 + np.arctan2(np.sinh(ep), np.cos(xp))
        return np.degrees(phi), np.degrees(lam)


def nh_state_plane():
    """NAD83 / New Hampshire (ftUS), EPSG:3445 (HARN), 3437 (NSRS2007), ESRI 102710, 2823
    (metre variant has unit 1): GRS80, lat0 42.5, lon0 -71.6666666666667, k0 0.999966667,
    false easting 300000 m (984250 ftUS)."""
    return TransverseMercator(*GRS80, 42.5, -71.6666666666667, 0.999966667, 300000.0, 0.0, US_FT)


def utm_north(zone):
    return TransverseMercator(*GRS80, 0.0, -183.0 + 6.0 * zone, 0.9996, 500000.0, 0.0, 1.0)


KNOWN_SR = {3445: nh_state_plane, 3437: nh_state_plane, 102710: nh_state_plane,
            2823: lambda: TransverseMercator(*GRS80, 42.5, -71.6666666666667, 0.999966667,
                                             300000.0, 0.0, 1.0),
            26919: lambda: utm_north(19), 26918: lambda: utm_north(18)}


class PyprojProjection:
    """Fallback for other references (needs pyproj): same forward/inverse interface."""

    def __init__(self, wkid):
        from pyproj import Transformer
        self._f = Transformer.from_crs(4326, wkid, always_xy=True)
        self._i = Transformer.from_crs(wkid, 4326, always_xy=True)

    def forward(self, lat, lon):
        x, y = self._f.transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(x), np.asarray(y)

    def inverse(self, x, y):
        lon, lat = self._i.transform(np.asarray(x, float), np.asarray(y, float))
        return np.asarray(lat), np.asarray(lon)


def projection_for(wkid):
    if wkid in KNOWN_SR:
        return KNOWN_SR[wkid]()
    try:
        return PyprojProjection(wkid)
    except ImportError:
        raise SystemExit(f"spatial reference {wkid} is not built in; install pyproj") from None


# ----------------------------------------------------------------------------- local frame (numpy)

def local_to_geodetic(frame: LocalFrame, x, y, z=0.0):
    """Vectorised LocalFrame.geodetic: local (x east, y north, z up) metres -> (lat, lon)
    degrees on the WGS84 ellipsoid (heights are not needed for the imagery)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    z = np.broadcast_to(np.asarray(z, float), x.shape)
    r = frame._r
    x0 = frame._x0
    X = x0[0] + r[0][0] * x + r[1][0] * y + r[2][0] * z
    Y = x0[1] + r[0][1] * x + r[1][1] * y + r[2][1] * z
    Z = x0[2] + r[0][2] * x + r[1][2] * y + r[2][2] * z
    p = np.hypot(X, Y)
    lon = np.arctan2(Y, X)
    lat = np.arctan2(Z, p * (1 - WGS84_E2))
    for _ in range(5):
        n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(Z, p * (1 - WGS84_E2 * n / (n + h)))
    return np.degrees(lat), np.degrees(lon)


def local_to_source(frame, proj, x, y, datum_shift=(0.0, 0.0), extra_shift=(0.0, 0.0)):
    """Where the source image (in its own datum and projection) has the content that
    belongs at local (x, y). The image is moved by (-datum_shift + extra_shift), so its
    content for local p sits at p + datum_shift - extra_shift in the image's datum.
    datum_shift: (east, north) metres of (image datum minus our frame), e.g. NAD83(2011)
    minus ITRF2020; extra_shift: the image's measured residual move, metres."""
    sx = np.asarray(x, float) + datum_shift[0] - extra_shift[0]
    sy = np.asarray(y, float) + datum_shift[1] - extra_shift[1]
    lat, lon = local_to_geodetic(frame, sx, sy)
    return proj.forward(lat, lon)


# ----------------------------------------------------------------------------- grids

def source_window(frame, proj, bbox, pixel, grid_origin, datum_shift, extra_shift, pad=3):
    """The native-grid window covering local `bbox` (x0, y0, x1, y1): returns
    (xmin, ymin, xmax, ymax) in native units snapped to `grid_origin` (x, y of the service
    extent's lower-left) at `pixel` native units, padded by `pad` pixels."""
    x0, y0, x1, y1 = bbox
    t = np.linspace(0, 1, 21)
    ex = np.concatenate([x0 + (x1 - x0) * t, np.full_like(t, x1), x0 + (x1 - x0) * t, np.full_like(t, x0)])
    ey = np.concatenate([np.full_like(t, y0), y0 + (y1 - y0) * t, np.full_like(t, y1), y0 + (y1 - y0) * t])
    sx, sy = local_to_source(frame, proj, ex, ey, datum_shift, extra_shift)
    gx, gy = grid_origin
    xmin = gx + (math.floor((sx.min() - gx) / pixel) - pad) * pixel
    ymin = gy + (math.floor((sy.min() - gy) / pixel) - pad) * pixel
    xmax = gx + (math.ceil((sx.max() - gx) / pixel) + pad) * pixel
    ymax = gy + (math.ceil((sy.max() - gy) / pixel) + pad) * pixel
    return xmin, ymin, xmax, ymax


def tiles(width, height, limit_w, limit_h):
    """[(col0, row0, w, h), ...] covering width x height pixels with tiles within limits."""
    out = []
    for r in range(0, height, limit_h):
        for c in range(0, width, limit_w):
            out.append((c, r, min(limit_w, width - c), min(limit_h, height - r)))
    return out


def output_grid(bbox, pixel):
    """North-up output grid for local bbox: (x_left, y_top, ncols, nrows), edges snapped to
    multiples of `pixel` so copies at the same pixel size line up pixel for pixel."""
    x0 = math.floor(bbox[0] / pixel) * pixel
    y0 = math.floor(bbox[1] / pixel) * pixel
    x1 = math.ceil(bbox[2] / pixel) * pixel
    y1 = math.ceil(bbox[3] / pixel) * pixel
    return x0, y1, int(round((x1 - x0) / pixel)), int(round((y1 - y0) / pixel))


def bilinear(src, valid, col, row):
    """Sample src (bands, H, W) at fractional (col, row) pixel-centre coordinates
    (0, 0 = centre of the top-left pixel). Returns (bands, ...) float32 and a bool mask that
    is True where all four neighbours exist and are valid."""
    h, w = src.shape[1:]
    c0 = np.floor(col).astype(int)
    r0 = np.floor(row).astype(int)
    fc = (col - c0).astype(np.float32)
    fr = (row - r0).astype(np.float32)
    ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
    c0c = np.clip(c0, 0, w - 2)
    r0c = np.clip(r0, 0, h - 2)
    ok &= valid[r0c, c0c] & valid[r0c, c0c + 1] & valid[r0c + 1, c0c] & valid[r0c + 1, c0c + 1]
    out = np.empty((src.shape[0],) + col.shape, np.float32)
    for b in range(src.shape[0]):
        s = src[b].astype(np.float32)
        top = s[r0c, c0c] * (1 - fc) + s[r0c, c0c + 1] * fc
        bot = s[r0c + 1, c0c] * (1 - fc) + s[r0c + 1, c0c + 1] * fc
        out[b] = top * (1 - fr) + bot * fr
    return out, ok


def resample(frame, proj, src, valid, src_xmin, src_ymax, src_pixel, bbox, pixel,
             datum_shift=(0.0, 0.0), extra_shift=(0.0, 0.0)):
    """Resample a north-up native image (top-left corner src_xmin, src_ymax, pixel
    src_pixel, native units) into the local grid of `bbox` at `pixel` metres. Returns
    (array (bands, rows, cols) float32, mask (rows, cols) bool, (x_left, y_top))."""
    xl, yt, nc, nr = output_grid(bbox, pixel)
    xs = xl + (np.arange(nc) + 0.5) * pixel
    out = np.empty((src.shape[0], nr, nc), np.float32)
    ok = np.empty((nr, nc), bool)
    step = max(1, 2_000_000 // max(nc, 1))   # rows per block: bounded memory on large extents
    for r0 in range(0, nr, step):
        r1 = min(nr, r0 + step)
        ys = yt - (np.arange(r0, r1) + 0.5) * pixel
        gx, gy = np.meshgrid(xs, ys)
        sx, sy = local_to_source(frame, proj, gx, gy, datum_shift, extra_shift)
        col = (sx - src_xmin) / src_pixel - 0.5
        row = (src_ymax - sy) / src_pixel - 0.5
        out[:, r0:r1], ok[r0:r1] = bilinear(src, valid, col, row)
    return out, ok, (xl, yt)


# ----------------------------------------------------------------------------- metadata

def years_from_name(name):
    """Acquisition years implied by a GRANIT dataset name: NH_2021_2022_6in_RGB -> [2021,
    2022], Coastal2016_1ft_RGB -> [2016], NH_DOQs_92_98 -> [1992, 1998] (a range)."""
    four = [int(y) for y in re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", name)]
    if four:
        return four
    two = re.findall(r"_(\d\d)(?=_|$)", name)
    return [1900 + int(y) if int(y) > 30 else 2000 + int(y) for y in two]


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def native_pixel_m(meta):
    wkid = meta["spatialReference"].get("latestWkid") or meta["spatialReference"].get("wkid")
    proj = KNOWN_SR.get(wkid)
    unit = proj().unit if proj else 1.0
    return meta["pixelSizeX"] * unit


def output_pixel_m(meta):
    """Native pixel size in metres rounded to the centimetre (0.5 ft -> 0.15 m)."""
    return max(0.01, round(native_pixel_m(meta), 2))


# ----------------------------------------------------------------------------- config

def load_config(path):
    if path is None:
        p = HERE.parents[1] / "config.toml"
        if not p.is_file():
            return {}, Path.cwd()
    else:
        p = Path(path)
    return tomllib.loads(p.read_text()), p.resolve().parent


def _abs(p, base):
    p = Path(p).expanduser()
    return p if p.is_absolute() else base / p


def read_gpkg_meta(gpkg):
    if not gpkg or not Path(gpkg).is_file():
        return {}
    con = sqlite3.connect(f"file:{Path(gpkg).as_posix()}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT key, value FROM icecube_meta"))
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def resolve_frame(cfg, meta):
    """[site.origin] of the config, else the origin stored in the gpkg."""
    o = cfg.get("site", {}).get("origin")
    if o:
        return LocalFrame(o["lat"], o["lon"], o["h"], name=str(o.get("name", "origin")))
    if meta.get("origin_lat"):
        return LocalFrame(float(meta["origin_lat"]), float(meta["origin_lon"]), float(meta["origin_h"]),
                          name=meta.get("origin_name", "origin"))
    raise SystemExit("no local frame origin: set [site.origin] in the config or pass --gpkg")


def feature_matches(props, flt):
    """True when every key=value of `flt` (a dict) equals the feature's property (as text)."""
    return all(str((props or {}).get(k)) == str(v) for k, v in (flt or {}).items())


def geojson_bbox(path, frame, flt=None):
    """Local (x0, y0, x1, y1) of every coordinate in a GeoJSON ([lon, lat, h?]), of the
    features whose properties match `flt` ({key: value}, all of them) when given."""
    d = json.loads(Path(path).read_text())
    pts = []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            pts.append(c)
        else:
            for k in c:
                walk(k)
    for f in d.get("features", []):
        g = f.get("geometry") or {}
        if g.get("coordinates") and feature_matches(f.get("properties"), flt):
            walk(g["coordinates"])
    if not pts:
        raise SystemExit(f"{path}: no coordinates" + (f" in features matching {flt}" if flt else ""))
    xy = [frame.enu(p[1], p[0], p[2] if len(p) > 2 else frame.h)[:2] for p in pts]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return min(xs), min(ys), max(xs), max(ys)


def settings(args, cfg, cfg_dir):
    """Resolved settings dict from config [imagery] and the command line."""
    im = cfg.get("imagery", {})
    season = cfg.get("season", {})
    gpkg = args.gpkg or (str(_abs(season["gpkg"], cfg_dir)) if season.get("gpkg") else None)
    out = args.out_dir or (str(_abs(im["out_dir"], cfg_dir)) if im.get("out_dir") else None)
    if out is None:
        if not gpkg:
            raise SystemExit("no output folder: set [imagery].out_dir, [season].gpkg or --out-dir")
        out = str(Path(gpkg).resolve().parent / "imagery")
    datum = args.datum_shift or im.get("datum_shift_m")
    if datum is None:
        print("WARNING: no [imagery].datum_shift_m: the copies are not datum corrected", file=sys.stderr)
        datum = [0.0, 0.0]
    shifts = {k: tuple(map(float, v)) for k, v in im.get("extra_shift_m", {}).items()}
    for name, dx, dy in args.shift or []:
        shifts[name] = (float(dx), float(dy))
    return {
        "service": (args.service or im.get("service") or DEFAULT_SERVICE).rstrip("/"),
        "datasets": args.datasets or im.get("datasets") or DEFAULT_DATASETS,
        "gpkg": gpkg, "out_dir": out,
        "margin_m": args.margin if args.margin is not None else float(im.get("margin_m", 30.0)),
        "bbox": args.bbox or im.get("bbox"),
        "extent_layer": args.extent_layer or (str(_abs(im["extent_layer"], cfg_dir))
                                              if im.get("extent_layer") else None),
        "extent_filter": (dict(kv.split("=", 1) for kv in args.extent_filter)
                          if getattr(args, "extent_filter", None)
                          else dict(im.get("extent_filter", {}))),
        "compress": {k: str(v).lower() for k, v in im.get("compress", {}).items()},
        "datum_shift": tuple(map(float, datum)),
        "datum_note": im.get("datum_note", ""),
        "extra_shift": shifts,
        "pixel_m": {k: float(v) for k, v in im.get("pixel_m", {}).items()},
        "rendering_rule": dict(im.get("rendering_rule", {})),
        "pause_s": float(im.get("pause_s", 0.5)),
        "tile_px": int(im.get("tile_px", 2048)),
    }


def local_bbox(s, frame):
    if s["bbox"]:
        return tuple(map(float, s["bbox"]))
    if not s["extent_layer"]:
        raise SystemExit("no extent: set [imagery].bbox or extent_layer (or --bbox / --extent-layer)")
    x0, y0, x1, y1 = geojson_bbox(s["extent_layer"], frame, s.get("extent_filter"))
    m = s["margin_m"]
    return x0 - m, y0 - m, x1 + m, y1 + m


# ----------------------------------------------------------------------------- service

UA = {"User-Agent": "ice_cube-fetch_imagery/1"}


def get(url, params=None, binary=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        data = r.read()
    if binary:
        return data
    return json.loads(data)


def service_meta(service, name):
    m = get(f"{service}/{name}/ImageServer", {"f": "json"})
    if "error" in m:
        raise RuntimeError(f"{name}: {m['error'].get('message')}")
    return m


def pick_rendering_rule(meta, configured):
    """None for raw pixels; for non-8-bit services (a DEM behind a shaded-relief service)
    the first raster function other than None, so the copy is what the service displays."""
    if configured is not None:
        return configured or None
    if meta.get("pixelType") == "U8":
        return None
    for f in meta.get("rasterFunctionInfos") or []:
        if f.get("name") and f["name"] != "None":
            return f["name"]
    return None


def get_retry(url, params, pause, tries=3):
    """get(binary) with retries on HTTP 5xx / network errors (pause, then 4x pause, ...)."""
    import urllib.error
    for k in range(tries):
        try:
            return get(url, params, binary=True)
        except (urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if k == tries - 1 or (code is not None and code < 500):
                raise
            time.sleep(max(pause, 0.5) * 4 ** (k + 1))


def download(service, name, meta, window, wkid, pixel_native, rule, pause, tile_px=2048):
    """Mosaic (bands, H, W) uint8/float of the native window, plus the request count.
    Tiles are at most `tile_px` square (and within the service's maxImageWidth/Height):
    GRANIT advertises 15000 x 4100 but answers HTTP 500 to a 3000 x 3000 4-band request."""
    import rasterio
    xmin, ymin, xmax, ymax = window
    w = int(round((xmax - xmin) / pixel_native))
    h = int(round((ymax - ymin) / pixel_native))
    lw = min(tile_px, int(meta.get("maxImageWidth", 4000)))
    lh = min(tile_px, int(meta.get("maxImageHeight", 4000)))
    mosaic = None
    n = 0
    for c, r, tw, th in tiles(w, h, lw, lh):
        bx0 = xmin + c * pixel_native
        by1 = ymax - r * pixel_native
        p = {"bbox": f"{bx0},{by1 - th * pixel_native},{bx0 + tw * pixel_native},{by1}",
             "bboxSR": wkid, "imageSR": wkid, "size": f"{tw},{th}", "format": "tiff",
             "interpolation": "RSP_NearestNeighbor", "f": "image"}
        if rule:
            p["renderingRule"] = json.dumps({"rasterFunction": rule})
        if n:
            time.sleep(pause)
        raw = get_retry(f"{service}/{name}/ImageServer/exportImage", p, pause)
        n += 1
        if raw[:1] == b"{":
            raise RuntimeError(f"{name}: {raw[:300].decode(errors='replace')}")
        with rasterio.MemoryFile(raw) as mf, mf.open() as ds:
            a = ds.read()
            if (ds.width, ds.height) != (tw, th):
                raise RuntimeError(f"{name}: asked {tw}x{th}, got {ds.width}x{ds.height}")
        if mosaic is None:
            mosaic = np.zeros((a.shape[0], h, w), a.dtype)
        mosaic[:, r:r + th, c:c + tw] = a
    return mosaic, n


def write_geotiff(path, arr, mask, x_left, y_top, pixel, wkt, gray, compress="deflate"):
    """RGB or grey uint8 + alpha GeoTIFF, tiled, with overviews. compress "deflate"
    (lossless, predictor 2; alpha band) or "jpeg" (RGB JPEG-in-TIFF, YCbCr, quality 90, for
    large photos: about 5x smaller; the no-data area is an internal mask instead of an
    alpha band, which QGIS honours the same way)."""
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.transform import from_origin
    if compress == "jpeg" and not gray:
        return _write_jpeg_tiff(path, arr, mask, x_left, y_top, pixel, wkt)
    bands = arr.shape[0]
    data = np.concatenate([arr, (mask * 255).astype(np.uint8)[None]], axis=0)
    prof = {"driver": "GTiff", "width": arr.shape[2], "height": arr.shape[1], "count": bands + 1,
            "dtype": "uint8", "crs": rasterio.crs.CRS.from_wkt(wkt),
            "transform": from_origin(x_left, y_top, pixel, pixel), "compress": "deflate",
            "predictor": 2, "tiled": True, "blockxsize": 256, "blockysize": 256,
            "photometric": "MINISBLACK" if gray else "RGB", "alpha": "YES"}
    tmp = Path(str(path) + ".tmp.tif")
    with rasterio.open(tmp, "w", **prof) as ds:
        ds.write(data)
        ds.colorinterp = ([ColorInterp.gray] if gray else
                          [ColorInterp.red, ColorInterp.green, ColorInterp.blue]) + [ColorInterp.alpha]
        f = [2 ** k for k in range(1, 8) if max(arr.shape[1:]) / 2 ** k >= 128]
        if f:
            ds.build_overviews(f, Resampling.average)
    tmp.replace(path)


def _write_jpeg_tiff(path, arr, mask, x_left, y_top, pixel, wkt):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    prof = {"driver": "GTiff", "width": arr.shape[2], "height": arr.shape[1], "count": 3,
            "dtype": "uint8", "crs": rasterio.crs.CRS.from_wkt(wkt),
            "transform": from_origin(x_left, y_top, pixel, pixel), "compress": "jpeg",
            "jpeg_quality": 90, "photometric": "YCBCR", "tiled": True, "blockxsize": 256,
            "blockysize": 256}
    tmp = Path(str(path) + ".tmp.tif")
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True, JPEG_QUALITY_OVERVIEW=85,
                      PHOTOMETRIC_OVERVIEW="YCBCR", COMPRESS_OVERVIEW="JPEG"):
        with rasterio.open(tmp, "w", **prof) as ds:
            ds.write(arr[:3])
            ds.write_mask((mask * 255).astype(np.uint8))
            f = [2 ** k for k in range(1, 8) if max(arr.shape[1:]) / 2 ** k >= 128]
            if f:
                ds.build_overviews(f, Resampling.average)
    tmp.replace(path)


def to_uint8(a):
    if a.dtype == np.uint8:
        return a
    return np.clip(np.round(a), 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------- main

def fetch_one(name, s, frame, bbox, wkt, log=print):
    meta = service_meta(s["service"], name)
    sr = meta["spatialReference"]
    wkid = sr.get("latestWkid") or sr.get("wkid")
    proj = projection_for(wkid)
    pixel_native = float(meta["pixelSizeX"])
    pixel = s["pixel_m"].get(name) or output_pixel_m(meta)
    extra = s["extra_shift"].get(name, (0.0, 0.0))
    ext = meta["extent"]
    window = source_window(frame, proj, bbox, pixel_native, (ext["xmin"], ext["ymin"]),
                           s["datum_shift"], extra)
    if (window[2] < ext["xmin"] or window[0] > ext["xmax"] or window[3] < ext["ymin"]
            or window[1] > ext["ymax"]):
        log(f"{name}: outside the service extent, skipped")
        return None
    rule = pick_rendering_rule(meta, s["rendering_rule"].get(name))
    t0 = time.time()
    src, nreq = download(s["service"], name, meta, window, wkid, pixel_native, rule, s["pause_s"],
                         s.get("tile_px", 2048))
    gray = src.shape[0] < 3
    src = src[:1] if gray else src[:3]
    valid = np.any(src != 0, axis=0)
    cover = float(valid.mean())
    if cover < 0.01:
        log(f"{name}: no data over the area ({cover:.1%} of pixels), skipped")
        return None
    arr, ok, (xl, yt) = resample(frame, proj, src, valid, window[0], window[3], pixel_native, bbox, pixel,
                                 s["datum_shift"], extra)
    arr = to_uint8(arr)
    out_dir = Path(s["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tif = out_dir / f"{name}.tif"
    comp = s.get("compress", {}).get(name, "deflate")
    write_geotiff(tif, arr, ok, xl, yt, pixel, wkt, gray, comp)
    total = (-s["datum_shift"][0] + extra[0], -s["datum_shift"][1] + extra[1])
    side = {
        "dataset": name,
        "service": f"{s['service']}/{name}/ImageServer",
        "description": strip_html(meta.get("description"))[:500],
        "acquisition_years": years_from_name(name),
        "source_wkid": wkid,
        "source_pixel": pixel_native,
        "source_bands": meta.get("bandCount"),
        "bands": "grey" if gray else "RGB (service bands 1-3; band 4, near-infrared, dropped)"
        if meta.get("bandCount", 3) >= 4 else "RGB",
        "rendering_rule": rule,
        "pixel_m": pixel,
        "compression": comp if not gray else "deflate",
        "local_bbox_m": [round(v, 3) for v in (xl, yt - arr.shape[1] * pixel, xl + arr.shape[2] * pixel, yt)],
        "size_px": [arr.shape[2], arr.shape[1]],
        "coverage": round(float(ok.mean()), 4),
        "datum_shift_m": list(s["datum_shift"]),
        "datum_note": s["datum_note"],
        "extra_shift_m": list(extra),
        "image_moved_by_m": [round(total[0], 4), round(total[1], 4)],
        "crs": frame.crs_name(),
        "requests": nreq,
        "fetched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "analysis/qgis/fetch_imagery.py",
    }
    (out_dir / f"{name}.json").write_text(json.dumps(side, indent=2) + "\n")
    mb = tif.stat().st_size / 1e6
    log(f"{name}: {arr.shape[2]}x{arr.shape[1]} px at {pixel} m, coverage {ok.mean():.0%}, "
        f"moved E {total[0]:+.3f} N {total[1]:+.3f} m, {mb:.1f} MB, {nreq} request(s), {time.time() - t0:.1f} s")
    return tif


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="site config TOML with [imagery] and [site.origin] (default: config.toml)")
    ap.add_argument("--datasets", nargs="+", help="dataset (ImageServer) names")
    ap.add_argument("--service", help="ImageServices folder URL")
    ap.add_argument("--gpkg", help="season gpkg: local CRS WKT and origin (default [season].gpkg)")
    ap.add_argument("--out-dir", help="output folder (default [imagery].out_dir, else imagery/ next to the gpkg)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                    help="local-frame extent in metres (instead of extent_layer + margin)")
    ap.add_argument("--extent-layer", help="GeoJSON whose bounding box (+ margin) is the extent")
    ap.add_argument("--extent-filter", nargs="+", metavar="KEY=VALUE",
                    help="only features of the extent layer with these properties (e.g. group=core)")
    ap.add_argument("--margin", type=float, help="metres around the extent layer (default 30)")
    ap.add_argument("--datum-shift", nargs=2, type=float, metavar=("DE", "DN"),
                    help="image datum minus our frame at the site, metres east, north")
    ap.add_argument("--shift", nargs=3, action="append", metavar=("DATASET", "DX", "DY"),
                    help="extra move of one dataset's image, metres east, north (repeatable)")
    ap.add_argument("--list", action="store_true", help="only report each dataset's coverage of the extent")
    args = ap.parse_args(argv)
    cfg, cfg_dir = load_config(args.config)
    s = settings(args, cfg, cfg_dir)
    meta = read_gpkg_meta(s["gpkg"])
    frame = resolve_frame(cfg, meta)
    wkt = meta.get("local_wkt") or frame.wkt2()
    bbox = local_bbox(s, frame)
    print(f"extent x {bbox[0]:.1f}..{bbox[2]:.1f} y {bbox[1]:.1f}..{bbox[3]:.1f} m ({frame.crs_name()}); "
          f"datum shift E {s['datum_shift'][0]:+.3f} N {s['datum_shift'][1]:+.3f} m; out {s['out_dir']}")
    done = []
    for i, name in enumerate(s["datasets"]):
        if i:
            time.sleep(s["pause_s"])
        try:
            if args.list:
                m = service_meta(s["service"], name)
                wkid = m["spatialReference"].get("latestWkid") or m["spatialReference"].get("wkid")
                w = source_window(frame, projection_for(wkid), bbox, m["pixelSizeX"],
                                  (m["extent"]["xmin"], m["extent"]["ymin"]), s["datum_shift"], (0, 0))
                e = m["extent"]
                inside = not (w[2] < e["xmin"] or w[0] > e["xmax"] or w[3] < e["ymin"] or w[1] > e["ymax"])
                print(f"{name}: wkid {wkid}, pixel {native_pixel_m(m):.3f} m, {m.get('bandCount')} band(s), "
                      f"{'extent covers the area (may still be empty)' if inside else 'outside the extent'}")
                continue
            t = fetch_one(name, s, frame, bbox, wkt)
            if t:
                done.append(t)
        except Exception as e:   # noqa: BLE001 (one dataset failing should not stop the rest)
            print(f"{name}: FAILED ({e})", file=sys.stderr)
    if not args.list:
        print(f"{len(done)} image(s) written to {s['out_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
