"""Read a u-blox log (UBX plus NMEA, as SW Maps or the logger app writes it) and report
lost 4 Hz positions and RAWX epochs. Writes out/gga.pkl for detect_points.py and fix_map.py.

Usage: python log_quality.py [log.ubx]   (defaults to nov4_ubx from the config)"""
import functools
import operator
import re
import struct
import sys

import numpy as np
import pandas as pd

from config import X0, Y0, SH, OUT, f
from surveys import T

PERIOD = 0.25


def gga_sentences(b):
    g = {}
    for m in re.finditer(rb"\$G[A-Z]GGA,[^$\r\n\xb5]{20,120}\*[0-9A-F]{2}", b):
        s = m.group().decode("latin1")
        body, ck = s[1:].rsplit("*", 1)
        if functools.reduce(operator.xor, body.encode("latin1"), 0) == int(ck, 16):
            g[s.split(",")[1]] = s
    return sorted(g.values(), key=lambda s: s.split(",")[1])


def rawx_tows(b):
    tow = set()
    for m in re.finditer(rb"\xb5\x62\x02\x15", b):
        i = m.start()
        n = struct.unpack("<H", b[i + 4:i + 6])[0]
        if i + 8 + n > len(b):
            continue
        A = B = 0
        for x in b[i + 2:i + 6 + n]:
            A = (A + x) & 255
            B = (B + A) & 255
        if A == b[i + 6 + n] and B == b[i + 7 + n]:
            tow.add(struct.unpack("<d", b[i + 6:i + 14])[0])
    return np.array(sorted(tow))


def gga_frame(sentences, pole=1.8):
    rows = []
    for g in sentences:
        p = g.split(",")
        if not p[2]:
            continue
        t = int(p[1][:2]) * 3600 + int(p[1][2:4]) * 60 + float(p[1][4:])
        la = int(p[2][:2]) + float(p[2][2:]) / 60
        lo = int(p[4][:3]) + float(p[4][3:]) / 60
        la = -la if p[3] == "S" else la
        lo = -lo if p[5] == "W" else lo
        rows.append((t, la, lo, int(p[6]), float(p[9]) + float(p[11]), float(p[13]) if p[13] else np.nan))
    d = pd.DataFrame(rows, columns=["t", "lat", "lon", "q", "h", "age"]).sort_values("t").reset_index(drop=True)
    d["X"], d["Y"] = T.transform(d.lon.values, d.lat.values)
    d["z"] = d.h - pole
    d["m"] = (d.t - d.t.iloc[0]) / 60
    # local grid, MassDOT frame (assumes the log used the home base)
    d["x"] = d.X + SH[0] - X0
    d["y"] = d.Y + SH[1] - Y0
    return d


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else f("nov4_ubx")
    b = open(path, "rb").read()
    tow = rawx_tows(b)
    d = gga_frame(gga_sentences(b))
    span = d.t.iloc[-1] - d.t.iloc[0]
    print(f"bytes {len(b)}  span {span / 60:.1f} min")
    print(f"positions {len(d)}  lost {100 * (1 - len(d) / (span / PERIOD + 1)):.1f}%  fix {d.q.value_counts().to_dict()}")
    if len(tow):
        dt = np.diff(tow)
        n = int((tow[-1] - tow[0]) / PERIOD) + 1
        print(f"RAWX epochs {len(tow)}  lost {100 * (1 - len(tow) / n):.1f}%  gaps {(dt > .26).sum()}  over 1.1 s {(dt > 1.1).sum()}  longest {dt.max():.2f} s")
    print(f"float or worse {100 * (d.q != 4).mean():.1f}% of samples, {(d.q != 4).sum() / 4 / 60:.1f} min")
    d.to_pickle(OUT / "gga.pkl")
