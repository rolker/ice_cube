"""Geodesy for the session import: WGS84 geodetic <-> ECEF, local E/N/U about a base, and
the local projected CRS for QGIS.

Standard library only, so the import runs with a bare Windows Python too.

The local frame has its origin at the base (the base antenna as broadcast in RTCM 1005, or
whatever point the site config names). Two flavours, which agree to well under a
millimetre within a few hundred metres of the origin:

- `LocalFrame.enu()`: exact east/north on the tangent plane at the base (what the logger's
  map uses), and up = ellipsoidal height minus the base height (no earth-curvature drop, so
  "up" compares heights the way a level does).
- `LocalFrame.proj_string()` / `wkt()`: a transverse Mercator projection centred on the
  base with scale 1 and no false easting/northing, for QGIS. A transverse Mercator with
  k=1 is conformal and true to scale along its central meridian; within 200 m of the
  origin its scale error is under 1 ppm (0.1 mm at 100 m), so distances and areas measured
  in QGIS are true metres. An orthographic projection would match the tangent plane more
  literally but is not conformal and QGIS handles it less well; tmerc is the usual choice.
"""
from __future__ import annotations

import math

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)


def geodetic_to_ecef(lat, lon, h):
    """Degrees, degrees, metres (WGS84 ellipsoid) -> ECEF X, Y, Z in metres."""
    la, lo = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo),
            (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - E2) + h) * math.sin(la))


def ecef_to_geodetic(x, y, z):
    """ECEF metres -> (lat, lon, h) degrees, degrees, metres. Iterative, < 0.1 mm."""
    p = math.hypot(x, y)
    lon = math.atan2(y, x)
    lat = math.atan2(z, p * (1 - E2))
    for _ in range(8):
        n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - E2 * n / (n + h)))
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


class LocalFrame:
    """Local frame about an origin given as geodetic lat, lon (degrees) and ellipsoidal h (m)."""

    def __init__(self, lat, lon, h, source=""):
        self.lat, self.lon, self.h = float(lat), float(lon), float(h)
        self.source = source
        self._x0 = geodetic_to_ecef(self.lat, self.lon, self.h)
        la, lo = math.radians(self.lat), math.radians(self.lon)
        self._r = ((-math.sin(lo), math.cos(lo), 0.0),
                   (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)),
                   (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)))

    def enu(self, lat, lon, h):
        """(east, north, up) in metres; up = h - origin h (see the module docstring)."""
        x = geodetic_to_ecef(lat, lon, h)
        d = [x[i] - self._x0[i] for i in range(3)]
        e = sum(self._r[0][i] * d[i] for i in range(3))
        n = sum(self._r[1][i] * d[i] for i in range(3))
        return e, n, h - self.h

    def proj_string(self):
        return (f"+proj=tmerc +lat_0={self.lat:.9f} +lon_0={self.lon:.9f} +k=1 +x_0=0 +y_0=0 "
                f"+ellps=WGS84 +units=m +no_defs")

    def wkt(self, name="Ice Cube local (tmerc at base)"):
        """OGC WKT 1 of the same projection, for gpkg_spatial_ref_sys."""
        return (
            f'PROJCS["{name}",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
            f'298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
            f'PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",{self.lat:.9f}],'
            f'PARAMETER["central_meridian",{self.lon:.9f}],PARAMETER["scale_factor",1],'
            f'PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1],'
            f'AXIS["Easting",EAST],AXIS["Northing",NORTH]]')


def enu_scatter(points):
    """Sample standard deviations (n - 1) east, north, up of [(lat, lon, h), ...] about their
    mean, in metres; zeros for a single point."""
    m = len(points)
    if m < 2:
        return 0.0, 0.0, 0.0
    lat0 = sum(p[0] for p in points) / m
    lon0 = sum(p[1] for p in points) / m
    h0 = sum(p[2] for p in points) / m
    fr = LocalFrame(lat0, lon0, h0)
    v = [fr.enu(*p) for p in points]
    out = []
    for k in range(3):
        mu = sum(x[k] for x in v) / m
        out.append(math.sqrt(sum((x[k] - mu) ** 2 for x in v) / (m - 1)))
    return tuple(out)
