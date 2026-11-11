"""A small OGC GeoPackage 1.3 writer on the standard library's sqlite3.

Enough for the session import: feature tables (Point, LineString, Polygon and their Multi
forms, with or without Z), attribute tables, extra spatial reference systems, upserts that
leave chosen (user-edited) columns alone, and the RTree spatial index extension.

What it writes, per the spec (http://www.geopackage.org/spec130/):
- `PRAGMA application_id = 0x47504B47` ("GPKG") and `PRAGMA user_version = 10300`.
- `gpkg_spatial_ref_sys` with the three required rows (-1, 0, EPSG:4326) plus any added.
- `gpkg_contents` and `gpkg_geometry_columns` rows for each table, extents kept current.
- Geometry blobs: the GeoPackage binary header ("GP", version 0, flags, srs_id, optional
  envelope) followed by ISO WKB, little endian (Z types are 1000 + the 2D code).
- Optional RTree index (`gpkg_rtree_index` extension): virtual table
  `rtree_<table>_<column>` and the triggers of the 1.4 revision of that extension. The triggers call ST_IsEmpty, ST_MinX,
  ST_MaxX, ST_MinY and ST_MaxY; GDAL/QGIS provide those, and `connect()` registers Python
  versions so this module can write to the table too. Any other program that writes to these tables (an UPDATE
  of any column) needs those functions as well: use QGIS/GDAL or `connect()`.

Usage:
    gp = GeoPackage("out.gpkg")
    gp.ensure_feature_table("pts", [("name", "TEXT")], "POINT", z=True, unique=("name",))
    gp.upsert("pts", [{"name": "a", "geom": point(0.1, 0.2, 3.0)}], key=("name",))
    gp.update_extent("pts"); gp.close()
"""
from __future__ import annotations

import math
import sqlite3
import struct
from datetime import datetime, timezone

APPLICATION_ID = 0x47504B47
USER_VERSION = 10300

WGS84_WKT = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
             'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,'
             'AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
             'AXIS["Latitude",NORTH],AXIS["Longitude",EAST],AUTHORITY["EPSG","4326"]]')

GEOM_CODES = {"GEOMETRY": 0, "POINT": 1, "LINESTRING": 2, "POLYGON": 3, "MULTIPOINT": 4,
              "MULTILINESTRING": 5, "MULTIPOLYGON": 6}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def _q(name):
    """Quote an SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


# ----------------------------------------------------------------------------- geometry

def _wkb_coords(coords, z):
    fmt = "<ddd" if z else "<dd"
    return b"".join(struct.pack(fmt, *(c[:3] if z else c[:2])) for c in coords)


def wkb(gtype, coords, z):
    """ISO WKB (little endian) for gtype in POINT, LINESTRING, POLYGON, MULTIPOINT,
    MULTILINESTRING, MULTIPOLYGON. coords nest like GeoJSON: a point is (x, y[, z]), a
    linestring a list of points, a polygon a list of rings, a Multi* a list of parts."""
    code = GEOM_CODES[gtype] + (1000 if z else 0)
    head = struct.pack("<BI", 1, code)
    if gtype == "POINT":
        return head + _wkb_coords([coords], z)
    if gtype == "LINESTRING":
        return head + struct.pack("<I", len(coords)) + _wkb_coords(coords, z)
    if gtype == "POLYGON":
        return head + struct.pack("<I", len(coords)) + b"".join(
            struct.pack("<I", len(r)) + _wkb_coords(r, z) for r in coords)
    part = {"MULTIPOINT": "POINT", "MULTILINESTRING": "LINESTRING", "MULTIPOLYGON": "POLYGON"}[gtype]
    return head + struct.pack("<I", len(coords)) + b"".join(wkb(part, c, z) for c in coords)


def _flatten(gtype, coords):
    if gtype == "POINT":
        return [coords]
    if gtype in ("LINESTRING", "MULTIPOINT"):
        return list(coords)
    if gtype in ("POLYGON", "MULTILINESTRING"):
        return [p for r in coords for p in r]
    return [p for poly in coords for r in poly for p in r]


def gpkg_geometry(gtype, coords, srs_id, z=False):
    """A GeoPackage geometry blob: header (with an XY envelope except for points) + WKB."""
    pts = _flatten(gtype, coords)
    if gtype == "POINT":
        env_type, env = 0, b""
    else:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        env_type, env = 1, struct.pack("<dddd", min(xs), max(xs), min(ys), max(ys))
    flags = 0x01 | (env_type << 1)   # little endian, standard (not extended) geometry
    return b"GP" + struct.pack("<BBi", 0, flags, srs_id) + env + wkb(gtype, coords, z)


_ENV_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


def parse_header(blob):
    """(srs_id, envelope tuple or None, empty flag, offset of the WKB) of a geometry blob."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    version, flags = blob[2], blob[3]
    order = "<" if flags & 1 else ">"
    env_type = (flags >> 1) & 7
    empty = bool(flags & 0x10)
    srs_id = struct.unpack_from(order + "i", blob, 4)[0]
    nd = _ENV_DOUBLES[env_type]
    env = struct.unpack_from(order + "d" * nd, blob, 8) if nd else None
    return srs_id, env, empty, 8 + 8 * nd


def _wkb_xy(buf, off=0):
    """All (x, y) of a WKB geometry starting at off; returns (points, next offset)."""
    order = "<" if buf[off] == 1 else ">"
    code = struct.unpack_from(order + "I", buf, off + 1)[0]
    base, dim = code % 1000, {0: 2, 1: 3, 2: 3, 3: 4}[code // 1000]
    off += 5
    def read_pts(off, n):
        out = [struct.unpack_from(order + "dd", buf, off + 8 * dim * i) for i in range(n)]
        return out, off + 8 * dim * n
    if base == 1:
        return read_pts(off, 1)
    n = struct.unpack_from(order + "I", buf, off)[0]
    off += 4
    if base == 2:
        return read_pts(off, n)
    pts = []
    if base == 3:
        for _ in range(n):
            m = struct.unpack_from(order + "I", buf, off)[0]
            p, off = read_pts(off + 4, m)
            pts += p
        return pts, off
    for _ in range(n):
        p, off = _wkb_xy(buf, off)
        pts += p
    return pts, off


def envelope(blob):
    """(minx, maxx, miny, maxy) of a geometry blob, or None when null or empty."""
    if blob is None:
        return None
    _, env, empty, off = parse_header(blob)
    if empty:
        return None
    if env:
        return env[:4]
    pts = [p for p in _wkb_xy(blob, off)[0] if not math.isnan(p[0])]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _st(i):
    def f(blob):
        e = envelope(blob) if blob is not None else None
        return None if e is None else e[i]
    return f


def connect(path):
    """sqlite3 connection with the ST_* functions the GeoPackage RTree triggers use."""
    con = sqlite3.connect(str(path))
    con.create_function("ST_IsEmpty", 1, lambda b: None if b is None else int(envelope(b) is None))
    for name, i in (("ST_MinX", 0), ("ST_MaxX", 1), ("ST_MinY", 2), ("ST_MaxY", 3)):
        con.create_function(name, 1, _st(i))
    return con


# ----------------------------------------------------------------------------- package

class GeoPackage:
    """Open or create a GeoPackage. Tables are made idempotently with ensure_* (new
    columns are added to an existing table, existing data is kept)."""

    def __init__(self, path):
        self.path = str(path)
        self.con = connect(self.path)
        self.con.execute("PRAGMA foreign_keys = ON")
        self._init_core()

    def close(self):
        self.con.commit()
        self.con.close()

    # -- core tables
    def _init_core(self):
        c = self.con
        c.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        c.execute(f"PRAGMA user_version = {USER_VERSION}")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
          srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
          organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE IF NOT EXISTS gpkg_contents (
          table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
          description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT
          (strftime('%Y-%m-%dT%H:%M:%fZ','now')), min_x DOUBLE, min_y DOUBLE, max_x DOUBLE,
          max_y DOUBLE, srs_id INTEGER,
          CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
          table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
          srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
          CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
          CONSTRAINT uk_gc_table_name UNIQUE (table_name),
          CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
          CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id));
        CREATE TABLE IF NOT EXISTS gpkg_extensions (
          table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
          definition TEXT NOT NULL, scope TEXT NOT NULL,
          CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name));
        """)
        self.add_srs(-1, "Undefined cartesian SRS", "NONE", -1, "undefined",
                     "undefined cartesian coordinate reference system")
        self.add_srs(0, "Undefined geographic SRS", "NONE", 0, "undefined",
                     "undefined geographic coordinate reference system")
        self.add_srs(4326, "WGS 84 geodetic", "EPSG", 4326, WGS84_WKT,
                     "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid")
        c.commit()

    def add_srs(self, srs_id, name, organization, org_id, definition, description=""):
        """Insert or replace a row of gpkg_spatial_ref_sys."""
        self.con.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?) ON CONFLICT(srs_id) DO UPDATE SET "
            "srs_name=excluded.srs_name, organization=excluded.organization, "
            "organization_coordsys_id=excluded.organization_coordsys_id, "
            "definition=excluded.definition, description=excluded.description",
            (name, srs_id, organization, org_id, definition, description))

    # -- tables
    def tables(self):
        return [r[0] for r in self.con.execute("SELECT table_name FROM gpkg_contents")]

    def columns(self, table):
        return [r[1] for r in self.con.execute(f"PRAGMA table_info({_q(table)})")]

    def _create(self, table, columns, extra_sql, unique):
        cols = ", ".join(f"{_q(n)} {t}" for n, t in columns)
        uq = f", UNIQUE ({', '.join(_q(u) for u in unique)})" if unique else ""
        self.con.execute(f"CREATE TABLE IF NOT EXISTS {_q(table)} "
                         f"(fid INTEGER PRIMARY KEY AUTOINCREMENT{extra_sql}, {cols}{uq})")
        have = set(self.columns(table))
        for n, t in columns:
            if n not in have:
                self.con.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(n)} {t}")

    def ensure_feature_table(self, table, columns, geometry_type, srs_id=4326, z=False,
                             unique=None, identifier=None, description="", geom_column="geom",
                             rtree=True):
        """Create (or extend) a feature table: fid, geometry column, then `columns`
        [(name, sqltype), ...]; `unique` is a column tuple for upserts."""
        new = table not in self.tables()
        self._create(table, columns, f", {_q(geom_column)} {geometry_type}", unique)
        if new:
            self.con.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, "
                "last_change, srs_id) VALUES (?, 'features', ?, ?, ?, ?)",
                (table, identifier or table, description, _now(), srs_id))
            self.con.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,0)",
                             (table, geom_column, geometry_type, srs_id, 1 if z else 0))
            if rtree:
                self._add_rtree(table, geom_column)
        self.con.commit()

    def ensure_attributes_table(self, table, columns, unique=None, identifier=None,
                                description=""):
        """Create (or extend) a non-spatial table registered as 'attributes'."""
        new = table not in self.tables()
        self._create(table, columns, "", unique)
        if new:
            self.con.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, "
                "last_change) VALUES (?, 'attributes', ?, ?, ?)",
                (table, identifier or table, description, _now()))
        self.con.commit()

    def drop_table(self, table):
        """Remove a table and its metadata rows (and its RTree)."""
        r = self.con.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?",
                             (table,)).fetchone()
        if r:
            self.con.execute(f"DROP TABLE IF EXISTS {_q(f'rtree_{table}_{r[0]}')}")
        self.con.execute(f"DROP TABLE IF EXISTS {_q(table)}")
        for t in ("gpkg_extensions", "gpkg_geometry_columns", "gpkg_contents"):
            self.con.execute(f"DELETE FROM {t} WHERE table_name=?", (table,))
        self.con.commit()

    def _add_rtree(self, t, c, i="fid"):
        rt = f"rtree_{t}_{c}"
        try:
            self.con.execute(f"CREATE VIRTUAL TABLE {_q(rt)} USING rtree(id, minx, maxx, miny, maxy)")
        except sqlite3.OperationalError:
            return   # this sqlite has no RTree module: the index is optional
        T, C, I, RT = _q(t), _q(c), _q(i), _q(rt)
        vals = f"NEW.{I}, ST_MinX(NEW.{C}), ST_MaxX(NEW.{C}), ST_MinY(NEW.{C}), ST_MaxY(NEW.{C})"
        nm = lambda s: _q(f"{rt}_{s}")
        old_ok = f"(OLD.{C} NOTNULL AND NOT ST_IsEmpty(OLD.{C}))"
        new_ok = f"(NEW.{C} NOTNULL AND NOT ST_IsEmpty(NEW.{C}))"
        box = (f"minx = ST_MinX(NEW.{C}), maxx = ST_MaxX(NEW.{C}), "
               f"miny = ST_MinY(NEW.{C}), maxy = ST_MaxY(NEW.{C})")
        # The GeoPackage 1.4 trigger set (insert, update6, update7, update2, update5,
        # update4, delete): unlike the 1.3 set it never relies on INSERT OR REPLACE for an
        # existing id, which fails under an UPSERT (the outer conflict clause wins).
        self.con.executescript(f"""
        CREATE TRIGGER {nm('insert')} AFTER INSERT ON {T} WHEN {new_ok}
        BEGIN INSERT OR REPLACE INTO {RT} VALUES ({vals}); END;
        CREATE TRIGGER {nm('update6')} AFTER UPDATE OF {C} ON {T}
          WHEN OLD.{I} = NEW.{I} AND {new_ok} AND {old_ok}
        BEGIN UPDATE {RT} SET {box} WHERE id = NEW.{I}; END;
        CREATE TRIGGER {nm('update7')} AFTER UPDATE OF {C} ON {T}
          WHEN OLD.{I} = NEW.{I} AND {new_ok} AND (OLD.{C} ISNULL OR ST_IsEmpty(OLD.{C}))
        BEGIN INSERT INTO {RT} VALUES ({vals}); END;
        CREATE TRIGGER {nm('update2')} AFTER UPDATE OF {C} ON {T}
          WHEN OLD.{I} = NEW.{I} AND (NEW.{C} IS NULL OR ST_IsEmpty(NEW.{C}))
        BEGIN DELETE FROM {RT} WHERE id = OLD.{I}; END;
        CREATE TRIGGER {nm('update5')} AFTER UPDATE ON {T}
          WHEN OLD.{I} != NEW.{I} AND {new_ok}
        BEGIN DELETE FROM {RT} WHERE id = OLD.{I}; INSERT OR REPLACE INTO {RT} VALUES ({vals}); END;
        CREATE TRIGGER {nm('update4')} AFTER UPDATE ON {T}
          WHEN OLD.{I} != NEW.{I} AND (NEW.{C} IS NULL OR ST_IsEmpty(NEW.{C}))
        BEGIN DELETE FROM {RT} WHERE id IN (OLD.{I}, NEW.{I}); END;
        CREATE TRIGGER {nm('delete')} AFTER DELETE ON {T} WHEN old.{C} NOT NULL
        BEGIN DELETE FROM {RT} WHERE id = OLD.{I}; END;
        """)
        self.con.execute("INSERT OR IGNORE INTO gpkg_extensions VALUES (?,?,?,?,?)",
                         (t, c, "gpkg_rtree_index", "http://www.geopackage.org/spec120/#extension_rtree",
                          "write-only"))

    # -- rows
    def insert(self, table, rows):
        """Plain insert of dict rows (all with the same keys)."""
        rows = list(rows)
        if not rows:
            return
        keys = list(rows[0])
        self.con.executemany(
            f"INSERT INTO {_q(table)} ({', '.join(_q(k) for k in keys)}) "
            f"VALUES ({', '.join('?' * len(keys))})", [[r.get(k) for k in keys] for r in rows])

    def upsert(self, table, rows, key, preserve=()):
        """Insert dict rows, or update the existing row with the same `key` columns. Columns
        in `preserve` (user-edited ones) are written only for new rows, never overwritten."""
        rows = list(rows)
        if not rows:
            return
        keys = list(rows[0])
        upd = [k for k in keys if k not in key and k not in preserve]
        set_sql = ", ".join(f"{_q(k)}=excluded.{_q(k)}" for k in upd) or f"{_q(key[0])}={_q(key[0])}"
        self.con.executemany(
            f"INSERT INTO {_q(table)} ({', '.join(_q(k) for k in keys)}) "
            f"VALUES ({', '.join('?' * len(keys))}) "
            f"ON CONFLICT ({', '.join(_q(k) for k in key)}) DO UPDATE SET {set_sql}",
            [[r.get(k) for k in keys] for r in rows])

    def update_extent(self, table):
        """Recompute gpkg_contents min/max x/y of a feature table from its geometries."""
        r = self.con.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?",
                             (table,)).fetchone()
        if not r:
            return
        ext = [envelope(b) for (b,) in self.con.execute(f"SELECT {_q(r[0])} FROM {_q(table)}")]
        ext = [e for e in ext if e]
        bounds = ((min(e[0] for e in ext), min(e[2] for e in ext),
                   max(e[1] for e in ext), max(e[3] for e in ext)) if ext else (None,) * 4)
        self.con.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=?, "
                         "last_change=? WHERE table_name=?", (*bounds, _now(), table))

    def touch(self, table):
        self.con.execute("UPDATE gpkg_contents SET last_change=? WHERE table_name=?", (_now(), table))

    def commit(self):
        self.con.commit()


def point(x, y, z=None, srs_id=4326):
    """PointZ (or Point when z is None) blob."""
    return gpkg_geometry("POINT", (x, y) if z is None else (x, y, z), srs_id, z is not None)
