"""Synthetic Ice Cube Logger sessions for the tests. All positions are made up, near
0 N 0 E (the Gulf of Guinea), and nothing refers to a real site."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from functools import reduce
from operator import xor
from pathlib import Path

BASE = (0.0001, 0.0002, 30.0)            # made-up base: lat, lon, ellipsoidal h
START = datetime(2026, 12, 1, 10, 0, 0, tzinfo=timezone.utc)
T0_NS = 500_000_000_000_000              # phone elapsed clock at START
SEP = 15.0                               # made-up geoid separation, m
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"   # stand-in photo bytes
POINTS_HDR = ("n,class,elapsed_ns,utc,lat,lon,h_ant_ellips,pole_m,h_ground_ellips,fix,n_epochs,"
              "sd_e_m,sd_n_m,sd_u_m,age_s,photo,note")


def nmea_gga(t: datetime, lat, lon, h, fix=4, nsat=22, hdop=0.6, age=1.0):
    """A checksummed $GNGGA for a position with ellipsoidal height h (alt = h - SEP)."""
    def dm(v, w):
        d = int(abs(v))
        return f"{d:0{w}d}{(abs(v) - d) * 60:010.7f}"
    tod = t.strftime("%H%M%S.") + f"{t.microsecond // 10000:02d}"
    body = (f"GNGGA,{tod},{dm(lat, 2)},{'N' if lat >= 0 else 'S'},{dm(lon, 3)},"
            f"{'E' if lon >= 0 else 'W'},{fix},{nsat},{hdop},{h - SEP:.4f},M,{SEP:.4f},M,"
            f"{age:.1f},0000")
    return f"${body}*{reduce(xor, body.encode(), 0):02X}\r\n".encode()


def offset(e, n, u=0.0):
    """Made-up lat, lon, h at e, n metres from BASE (spherical, plenty for tests)."""
    return (BASE[0] + n / 110574.0, BASE[1] + e / 111320.0, BASE[2] + u)


def write_ubx(epochs):
    """gps.ubx bytes (GGA mixed with fake UBX frames) and gps_chunks.csv text.
    epochs: [(seconds after START, lat, lon, h, fix)]."""
    ubx, chunks = bytearray(), ["elapsed_ns,byte_offset,length"]
    for s, lat, lon, h, fix in epochs:
        junk = b"\xb5\x62\x02\x15\x04\x00\x01\x02\x03\x04\x00\x00"
        line = nmea_gga(START + timedelta(seconds=s), lat, lon, h, fix)
        data = junk + line
        chunks.append(f"{T0_NS + int(s * 1e9) + 50_000_000},{len(ubx)},{len(data)}")
        ubx += data
    return bytes(ubx), "\n".join(chunks) + "\n"


def events(lines, app="0.5"):
    """events.csv from [(seconds after START, text)]; adds session start and stop."""
    rows = ["elapsed_ns,wall_ms,event",
            f"{T0_NS},{int(START.timestamp() * 1000)},session start app={app} android_sdk=37 device=Test Phone",
            f"{T0_NS + 1000},{int(START.timestamp() * 1000)},pole_height=1.800"]
    last = 0.0
    for s, text in lines:
        rows.append(f"{T0_NS + int(s * 1e9)},{int(START.timestamp() * 1000 + s * 1000)},{text}")
        last = max(last, s)
    rows.append(f"{T0_NS + int((last + 1) * 1e9)},{int(START.timestamp() * 1000 + (last + 1) * 1000)},"
                f"stop gps_bytes=1 rawx=10 rawx_lost=0 gga=10 gga_lost=1 rtcm_crc_errors=0")
    return "\n".join(rows) + "\n"


def walk(seconds, rate=4, start_e=0.0, fix_at=lambda s: 4):
    """4 Hz epochs walking east 0.5 m/s from start_e, 1 m north of the base."""
    out = []
    for i in range(int(seconds * rate)):
        s = i / rate
        lat, lon, h = offset(start_e + 0.5 * s, 1.0, 1.8)
        out.append((s, lat, lon, h, fix_at(s)))
    return out


def captures_v05():
    """Four captures (surface, object with a photo, ref, photo row) as points.csv rows."""
    rows = []
    specs = [(1, "surface", 2.0, 0.0, ""), (2, "object", 3.0, 0.1, "photo_2_20261201T100010Z.jpg"),
             (3, "ref", 0.0, 0.2, ""), (4, "photo", 5.0, 0.0, "photo_4_20261201T100030Z.jpg")]
    for n, cls, e, u, photo in specs:
        lat, lon, h = offset(e, 2.0, u + 1.8)
        t = START + timedelta(seconds=n * 10)
        rows.append(f"{n},{cls},{T0_NS + n * 10_000_000_000},{t.strftime('%Y-%m-%dT%H:%M:%S.00Z')},"
                    f"{lat:.9f},{lon:.9f},{h:.4f},1.800,{h - 1.8:.4f},4,{30 if cls == 'ref' else 5},"
                    f"0.0020,0.0020,0.0040,1.0,{photo},")
    return rows


def session_v05(name="2026-12-01_10-00-00", extra_rows=()):
    """{relative path: bytes} of a 0.5 session folder."""
    ubx, chunks = write_ubx(walk(60, fix_at=lambda s: 5 if 20 <= s < 25 else 4))
    ev = events([(5, "gps connected bluetooth Test-Rover secure gps_offset=0"),
                 (6, "caster start mount=TEST01 log_corrections=true"),
                 (7, "base station=7 type=1005 ecef=6378167.0;22.264;11.057 h=none"),
                 (8, "frame=ITRF2020 epoch=2026.9 source=setting"),
                 (9, "pole_height=1.800 changed"),
                 (10, "photo file=photos/photo_2_20261201T100010Z.jpg bytes=68 attached to n=2"),
                 (30, "ref end n=3 epochs=30 pauses=1"),
                 (31, "pole check n=3: pole check OK (±0.4 cm vs last visit; -1.0 cm vs mean of 2 visits); "
                      "diff_last_m=-0.0040 diff_mean_m=-0.0100 visits=2 last=2026-11-30_09-00-00/5 ok=true"),
                 (32, "photo file=photos/photo_4_20261201T100030Z.jpg bytes=68 own row n=4"),
                 (40, "caster lost: error: test; retry in 5 s")])
    pts = "\n".join([POINTS_HDR] + captures_v05() + list(extra_rows)) + "\n"
    return {f"{name}/events.csv": ev.encode(), f"{name}/points.csv": pts.encode(),
            f"{name}/gps.ubx": ubx, f"{name}/gps_chunks.csv": chunks.encode(),
            f"{name}/photos/photo_2_20261201T100010Z.jpg": JPEG,
            f"{name}/photos/photo_4_20261201T100030Z.jpg": JPEG}


def refs_csv():
    """A refs.csv with this session's ref and one from an earlier session."""
    lat, lon, h = offset(0.1, 2.0, 1.8)
    return ("session," + POINTS_HDR + "\n"
            f"2026-11-30_09-00-00,5,ref,1,2026-11-30T14:00:00.00Z,{lat:.9f},{lon:.9f},{h:.4f},1.800,"
            f"{h - 1.8:.4f},4,30,0.002,0.002,0.004,1.0,,\n"
            "2026-12-01_10-00-00," + captures_v05()[2] + "\n")


def session_v03(name="2026-12-01_11-00-00"):
    """A 0.3 session: no points.csv, two control marks with pole heights."""
    ubx, chunks = write_ubx(walk(30))
    ev = events([(10.1, "control n=1 pole_height=1.800 gga_quality=4"),
                 (20.1, "pole_height=1.600 changed"),
                 (20.6, "control n=2 pole_height=1.600 gga_quality=4")], app="0.3")
    return {f"{name}/events.csv": ev.encode(), f"{name}/gps.ubx": ubx,
            f"{name}/gps_chunks.csv": chunks.encode()}


def session_v02(name="2026-12-01_09-00-00"):
    """A 0.2 session: unnumbered control events, a 10 s gap in the GGA."""
    ep = [e for e in walk(40) if not 15 <= e[0] < 25]
    ubx, chunks = write_ubx(ep)
    ev = events([(10, "control"), (30, "control")], app="0.2")
    return {f"{name}/events.csv": ev.encode(), f"{name}/gps.ubx": ubx,
            f"{name}/gps_chunks.csv": chunks.encode()}


def write_zip(path: Path, files: dict, refs: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v, compress_type=zipfile.ZIP_STORED if k.endswith(".jpg") else zipfile.ZIP_DEFLATED)
        if refs is not None:
            z.writestr("refs.csv", refs)
    return path


def write_dir(root: Path, files: dict):
    for k, v in files.items():
        p = root / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(v)
    return root / next(iter(files)).split("/")[0]


def layers_geojson(path: Path):
    """A layer file with a base point, a monument, a 2D polygon and a line."""
    lat, lon, h = BASE
    d = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Base", "kind": "base", "stroke": "#ff3030"},
         "geometry": {"type": "Point", "coordinates": [lon, lat, h]}},
        {"type": "Feature", "properties": {"name": "Monument A", "label": "A", "kind": "monument",
                                           "status": "predicted", "uncertainty_m": 0.5,
                                           "measured_local_en_m": [1.0, 2.0], "neighbour": True},
         "geometry": {"type": "Point", "coordinates": [lon + 0.0001, lat, h - 2]}},
        {"type": "Feature", "properties": {"name": "Rink", "kind": "rink", "stroke": "#1565c0",
                                           "fill": "#331565c0"},
         "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + 1e-4, lat],
                                                           [lon + 1e-4, lat + 1e-4], [lon, lat]]]}},
        {"type": "Feature", "properties": {"name": "Fence", "kind": "fence"},
         "geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon, lat + 2e-4]]}},
        {"type": "Feature", "properties": {"name": "broken"}, "geometry": None},
    ]}
    path.write_text(json.dumps(d))
    return path
