"""Geodesy for the session import: WGS84 geodetic <-> ECEF, the local ENU frame, and the
display CRS for QGIS.

Standard library only, so the import runs with a bare Windows Python too.

The local frame is a local tangent plane, ROS style (REP 103, as GeographicLib's
LocalCartesian): x east, y north, z up, metres, with its origin at a named point given as
WGS84 geodetic lat, lon and ellipsoidal h (for the Ice Cube yard: lot monument G, from the
private site config). `LocalFrame.enu()` is exact: ECEF of the point minus ECEF of the
origin, rotated by the ENU matrix at the origin's lat/lon. z is height above the tangent
plane at the origin, so it includes the earth-curvature drop (d^2 / 2R: 0.8 mm at 100 m).

The display CRS (`LocalFrame.wkt2()`) is the ellipsoidal orthographic projection (EPSG
method 9840) at the origin's lat/lon, false easting/northing 0, axes east and north in
metres. It is exactly the ENU east/north of the point dropped to the ellipsoid surface
(h = 0) about the origin dropped to the surface. For a point at height h the true x, y are
larger by the factor (N + h) / N (N about 6390 km): 1.6 ppm per 10 m of height, so under
1 mm within 100 m of the origin for the heights of a backyard. The table columns x_m, y_m,
z_m are the exact values; the CRS only places the map. `proj_string()` and `wkt1()` give
the same projection for programs that want those forms; `tmerc_proj_string()` is a
transverse Mercator fallback (k = 1 at the origin, agrees with the orthographic to well
under a millimetre within 100 m).
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


def _fmt(v):
    """A WKT/proj number: enough digits for 0.01 mm, no trailing zeros."""
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class LocalFrame:
    """Local ENU frame about an origin: geodetic lat, lon (degrees), ellipsoidal h (m)."""

    def __init__(self, lat, lon, h, name="origin", source=""):
        self.lat, self.lon, self.h = float(lat), float(lon), float(h)
        self.name = name
        self.source = source
        self._x0 = geodetic_to_ecef(self.lat, self.lon, self.h)
        la, lo = math.radians(self.lat), math.radians(self.lon)
        self._r = ((-math.sin(lo), math.cos(lo), 0.0),
                   (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)),
                   (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)))

    def key(self):
        """A string that changes when the origin moves by more than ~0.01 mm."""
        return f"{self.lat:.10f} {self.lon:.10f} {self.h:.5f}"

    def enu(self, lat, lon, h):
        """Exact (x east, y north, z up) in metres of a geodetic point."""
        x = geodetic_to_ecef(lat, lon, h)
        d = [x[i] - self._x0[i] for i in range(3)]
        return tuple(sum(self._r[k][i] * d[i] for i in range(3)) for k in range(3))

    def geodetic(self, e, n, u):
        """Inverse of enu(): (lat, lon, h) of a point given in the local frame."""
        x = [self._x0[i] + self._r[0][i] * e + self._r[1][i] * n + self._r[2][i] * u for i in range(3)]
        return ecef_to_geodetic(*x)

    # -- display CRS
    def crs_name(self):
        return f"Ice Cube local ENU (origin {self.name})"

    def wkt2(self, name=None):
        """WKT2 (ISO 19162:2019) PROJCRS: ellipsoidal orthographic at the origin, metres."""
        name = name or self.crs_name()
        deg = 'ANGLEUNIT["degree",0.0174532925199433]'
        return (
            f'PROJCRS["{name}",'
            'BASEGEOGCRS["WGS 84",DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]]],'
            f'PRIMEM["Greenwich",0,{deg}],ID["EPSG",4326]],'
            'CONVERSION["Orthographic at the origin",'
            'METHOD["Orthographic",ID["EPSG",9840]],'
            f'PARAMETER["Latitude of natural origin",{_fmt(self.lat)},{deg},ID["EPSG",8801]],'
            f'PARAMETER["Longitude of natural origin",{_fmt(self.lon)},{deg},ID["EPSG",8802]],'
            'PARAMETER["False easting",0,LENGTHUNIT["metre",1],ID["EPSG",8806]],'
            'PARAMETER["False northing",0,LENGTHUNIT["metre",1],ID["EPSG",8807]]],'
            'CS[Cartesian,2],'
            'AXIS["easting (X)",east,ORDER[1],LENGTHUNIT["metre",1]],'
            'AXIS["northing (Y)",north,ORDER[2],LENGTHUNIT["metre",1]],'
            'USAGE[SCOPE["Local site map: metres east and north of the origin"],'
            f'BBOX[{_fmt(self.lat - 0.01)},{_fmt(self.lon - 0.01)},'
            f'{_fmt(self.lat + 0.01)},{_fmt(self.lon + 0.01)}]]]')

    def wkt1(self, name=None):
        """OGC WKT 1 of the same projection, for gpkg_spatial_ref_sys.definition."""
        name = name or self.crs_name()
        return (
            f'PROJCS["{name}",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
            f'298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
            f'PROJECTION["Orthographic"],PARAMETER["latitude_of_origin",{_fmt(self.lat)}],'
            f'PARAMETER["central_meridian",{_fmt(self.lon)}],'
            f'PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1],'
            f'AXIS["Easting",EAST],AXIS["Northing",NORTH]]')

    def proj_string(self):
        return (f"+proj=ortho +lat_0={_fmt(self.lat)} +lon_0={_fmt(self.lon)} +x_0=0 +y_0=0 "
                f"+ellps=WGS84 +units=m +no_defs +type=crs")

    def tmerc_proj_string(self):
        return (f"+proj=tmerc +lat_0={_fmt(self.lat)} +lon_0={_fmt(self.lon)} +k=1 +x_0=0 +y_0=0 "
                f"+ellps=WGS84 +units=m +no_defs +type=crs")


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
