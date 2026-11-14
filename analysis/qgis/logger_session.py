"""Read Ice Cube Logger sessions (see logger/README.md) from share zips or folders.

Standard library only. Formats handled:
- 0.5 and later: `points.csv` holds the captures (surface, object, ref, photo).
- 0.3 / 0.4: no points.csv; `control n=<k> pole_height=<m> ...` events mark control
  points. Their positions are rebuilt from the GGA sentences in `gps.ubx` at the mark's
  phone time (gps_chunks.csv maps byte offsets to phone time) with the 0.5 Surface rule:
  the epochs of the last 1 s before the mark, best fix quality only, averaged.
- 0.2: unnumbered `control` events without a pole height: only the track is imported.

A share zip (`IceCubeLogger_<session>.zip`; older ones were named without `.zip` or e.g.
`session-14-02-28.zip`) holds `<session>/...` and, from 0.5.1, a top-level `refs.csv`.
A folder input is either a session folder (it holds events.csv) or any folder above
session folders and zips, searched recursively.

Nothing here prints coordinates.
"""
from __future__ import annotations

import bisect
import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import reduce
from operator import xor
from pathlib import Path

from local_frame import enu_scatter

PHOTO_RE = re.compile(r"photo_(\d+)_(\d{8}T\d{6}Z)\.jpe?g$", re.I)
GGA_RE = re.compile(rb"\$G[A-Z]GGA,[^$\r\n\xb5]*\*([0-9A-Fa-f]{2})")
QUALITY_RANK = {4: 0, 5: 1, 2: 2, 1: 3}    # best first; others are not positions


# ----------------------------------------------------------------------------- sources

class SessionSource:
    """One session inside a zip or a folder. `name` is the session folder name, which is
    the session's key everywhere (e.g. 2026-11-11_14-02-28)."""

    def __init__(self, path: Path, name: str, zip_prefix: str | None = None):
        self.path = Path(path)
        self.name = name
        self.zip_prefix = zip_prefix          # "<session>/" inside a zip, None for a folder
        self._zip = None

    @property
    def is_zip(self):
        return self.zip_prefix is not None

    def _z(self):
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.path)
        return self._zip

    def close(self):
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def read(self, rel):
        """Bytes of a file in the session folder, or None when absent."""
        if self.is_zip:
            try:
                return self._z().read(self.zip_prefix + rel)
            except KeyError:
                return None
        p = self.path / rel
        return p.read_bytes() if p.is_file() else None

    def size(self, rel):
        if self.is_zip:
            try:
                return self._z().getinfo(self.zip_prefix + rel).file_size
            except KeyError:
                return None
        p = self.path / rel
        return p.stat().st_size if p.is_file() else None

    def photo_names(self):
        if self.is_zip:
            pre = self.zip_prefix + "photos/"
            return sorted(n[len(pre):] for n in self._z().namelist()
                          if n.startswith(pre) and len(n) > len(pre) and "/" not in n[len(pre):])
        d = self.path / "photos"
        return sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    def extract_photo(self, name, dest: Path):
        """Copy photos/<name> to dest unless a file of the same size is already there."""
        size = self.size("photos/" + name)
        if dest.is_file() and dest.stat().st_size == size:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        if self.is_zip:
            with self._z().open(self.zip_prefix + "photos/" + name) as src, open(tmp, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
        else:
            with open(self.path / "photos" / name, "rb") as src, open(tmp, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
        os.replace(tmp, dest)
        return True

    def refs_csv(self):
        """Text of the refs.csv shipped with this session (zip top level, or the folder's
        parent), or None."""
        if self.is_zip:
            inner = self.zip_prefix.rstrip("/")
            parent = inner.rsplit("/", 1)[0] + "/" if "/" in inner else ""
            for cand in (parent + "refs.csv", "refs.csv"):
                try:
                    return self._z().read(cand).decode("utf-8", "replace")
                except KeyError:
                    pass
            return None
        p = self.path.parent / "refs.csv"
        return p.read_text("utf-8", "replace") if p.is_file() else None

    @property
    def snapshot(self):
        return self.read("SNAPSHOT.txt") is not None or (
            self.is_zip and any(n.endswith("SNAPSHOT.txt") for n in self._z().namelist()))

    def signature(self):
        """Changes when the source changes: decides whether a re-import is needed."""
        if self.is_zip:
            return f"zip:{self.path.name}:{self.path.stat().st_size}"
        parts = [str(self.size(f)) for f in ("events.csv", "points.csv", "gps.ubx")]
        return "dir:" + ":".join(parts) + f":{len(self.photo_names())}"

    def label(self):
        return self.path.name + (f"!{self.zip_prefix}" if self.is_zip else "")


def _sessions_in_zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        return []
    out = []
    for n in names:
        if n.endswith("events.csv"):
            prefix = n[: -len("events.csv")]
            if prefix and prefix.endswith("/"):
                out.append(SessionSource(path, prefix.rstrip("/").split("/")[-1], prefix))
    return out


def find_sessions(paths):
    """Session sources under the given zips and folders (recursively). A session found
    more than once (a zip and its extracted folder, a snapshot and the final zip) is
    returned once: the non-snapshot copy, then the one with the larger events.csv."""
    found = []
    for p in map(Path, paths):
        if p.is_file():
            found += _sessions_in_zip(p)
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                if "events.csv" in files:
                    found.append(SessionSource(Path(root), Path(root).name))
                    dirs[:] = []
                    continue
                dirs.sort()
                for f in sorted(files):
                    fp = Path(root) / f
                    if f.lower().endswith((".csv", ".ubx", ".rtcm", ".jpg", ".jpeg", ".geojson",
                                           ".gpkg", ".qgz", ".toml", ".md", ".py", ".png")):
                        continue
                    if zipfile.is_zipfile(fp):
                        found += _sessions_in_zip(fp)
        else:
            raise FileNotFoundError(p)
    best = {}
    for s in found:
        k = (not s.snapshot, s.size("events.csv") or 0)
        if s.name not in best or k > best[s.name][0]:
            best[s.name] = (k, s)
    return [best[n][1] for n in sorted(best)]


# ----------------------------------------------------------------------------- parsing

def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def _i(x):
    v = _f(x)
    return int(v) if v is not None else None


def read_events(src):
    """[(elapsed_ns, wall_ms, text), ...] from events.csv."""
    raw = src.read("events.csv")
    if raw is None:
        return []
    out = []
    for row in csv.reader(io.StringIO(raw.decode("utf-8", "replace"))):
        if len(row) < 3 or not row[0].isdigit():
            continue
        out.append((int(row[0]), int(row[1]), ",".join(row[2:])))
    return out


def read_points(text):
    """points.csv rows as dicts with typed values (see the logger README)."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("n"):
            continue
        rows.append({
            "n": int(r["n"]), "class": r.get("class") or "", "elapsed_ns": _i(r.get("elapsed_ns")),
            "utc": r.get("utc") or None, "lat": _f(r.get("lat")), "lon": _f(r.get("lon")),
            "h_ant": _f(r.get("h_ant_ellips")), "pole_m": _f(r.get("pole_m")),
            "h_ground": _f(r.get("h_ground_ellips")), "fix": _i(r.get("fix")),
            "n_epochs": _i(r.get("n_epochs")), "sd_e_m": _f(r.get("sd_e_m")),
            "sd_n_m": _f(r.get("sd_n_m")), "sd_u_m": _f(r.get("sd_u_m")), "age_s": _f(r.get("age_s")),
            "photo_files": [p for p in (r.get("photo") or "").split(";") if p],
            "note": r.get("note") or None,
        })
    return rows


def read_refs_csv(text):
    """refs.csv rows: the session folder name, then the points.csv columns. Accepts a
    header line (first column `session` or anything not a number) or none."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    cols = ["n", "class", "elapsed_ns", "utc", "lat", "lon", "h_ant_ellips", "pole_m",
            "h_ground_ellips", "fix", "n_epochs", "sd_e_m", "sd_n_m", "sd_u_m", "age_s", "photo", "note"]
    first = next(csv.reader([lines[0]]))
    if len(first) > 1 and "n" in first:
        hdr, body = first, lines[1:]
        hdr = ["session" if i == 0 and h != "n" else h for i, h in enumerate(hdr)]
    else:
        hdr, body = ["session"] + cols, lines
    rows = []
    for r in csv.reader(body):
        d = dict(zip(hdr, r))
        if not d.get("session") or not (d.get("n") or "").isdigit():
            continue
        p = read_points(",".join(cols) + "\n" + ",".join(
            '"' + (d.get(c) or "").replace('"', '""') + '"' for c in cols))
        if p:
            p[0]["session"] = d["session"]
            rows.append(p[0])
    return rows


def _nmea_deg(v, hemi, width):
    if not v:
        return None
    d = float(v[:width]) + float(v[width:]) / 60
    return -d if hemi in ("S", "W") else d


def read_gga(ubx: bytes):
    """[(byte offset, dict), ...] for every GGA with a valid checksum. The dict has tod
    (s of day), fix, nsat, hdop, lat, lon, h (alt + geoid separation; None without a
    separation) and age_s."""
    out = []
    for m in GGA_RE.finditer(ubx):
        s = m.group()
        body = s[1: s.rfind(b"*")]
        if reduce(xor, body, 0) != int(m.group(1), 16):
            continue
        f = s.decode("ascii", "replace").split("*")[0].split(",")
        if len(f) < 14 or len(f[1]) < 6:
            continue
        try:
            tod = int(f[1][:2]) * 3600 + int(f[1][2:4]) * 60 + float(f[1][4:])
            alt, sep = _f(f[9]), _f(f[11])
            out.append((m.start(), {
                "tod": tod, "fix": _i(f[6]) or 0, "nsat": _i(f[7]), "hdop": _f(f[8]),
                "lat": _nmea_deg(f[2], f[3], 2), "lon": _nmea_deg(f[4], f[5], 3),
                "h": alt + sep if alt is not None and sep is not None else None,
                "age_s": _f(f[13]),
            }))
        except ValueError:
            continue
    return out


def utc_of_tod(tod, near: datetime):
    """Datetime for a GGA time of day: the day that puts it nearest `near`."""
    t = datetime(near.year, near.month, near.day, tzinfo=timezone.utc) + timedelta(seconds=tod)
    if t - near > timedelta(hours=12):
        t -= timedelta(days=1)
    elif near - t > timedelta(hours=12):
        t += timedelta(days=1)
    return t


def iso(t: datetime):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 10000:02d}Z"


def average_epochs(epochs):
    """The 0.5 Surface rule over GGA epoch dicts (time order): epochs of the last 1 s
    ending at the latest, best fix quality only. Returns a capture-like dict or None."""
    if not epochs:
        return None
    last = epochs[-1]["tod"]
    win = [e for e in epochs if last - 1.0 - 1e-6 <= e["tod"] <= last and e["lat"] is not None
           and e["h"] is not None and e["fix"] in QUALITY_RANK]
    if not win:
        return None
    best = min(QUALITY_RANK[e["fix"]] for e in win)
    win = [e for e in win if QUALITY_RANK[e["fix"]] == best]
    m = len(win)
    pts = [(e["lat"], e["lon"], e["h"]) for e in win]
    sd = enu_scatter(pts)
    ages = [e["age_s"] for e in win if e["age_s"] is not None]
    return {"lat": sum(p[0] for p in pts) / m, "lon": sum(p[1] for p in pts) / m,
            "h_ant": sum(p[2] for p in pts) / m, "fix": win[0]["fix"], "n_epochs": m,
            "sd_e_m": sd[0], "sd_n_m": sd[1], "sd_u_m": sd[2],
            "age_s": sum(ages) / len(ages) if ages else None, "tod": win[-1]["tod"]}


def read_chunks(src):
    """(offsets, elapsed_ns) arrays from gps_chunks.csv."""
    raw = src.read("gps_chunks.csv")
    offs, tns = [], []
    if raw is None:
        return offs, tns
    for row in csv.reader(io.StringIO(raw.decode("ascii", "replace"))):
        if len(row) >= 2 and row[0].isdigit():
            tns.append(int(row[0]))
            offs.append(int(row[1]))
    return offs, tns


# ----------------------------------------------------------------------------- session

@dataclass
class Session:
    name: str
    fmt: str = ""
    info: dict = field(default_factory=dict)       # one sessions-table row
    captures: list = field(default_factory=list)   # dicts, keyed by n
    photos: list = field(default_factory=list)     # dicts: n, name, utc, attached, bytes
    refs: dict = field(default_factory=dict)       # n -> pole-check / ref-event fields
    track: list = field(default_factory=list)      # 1 Hz epochs: utc, t, lat, lon, h, fix, ...
    remarks: list = field(default_factory=list)


def _kv(text):
    return dict(re.findall(r"(\w+)=([^\s;]+)", text))


def load_session(src: SessionSource) -> Session:
    """Parse one session into captures, photos, ref checks, a 1 Hz track and a summary."""
    s = Session(src.name)
    ev = read_events(src)
    info = s.info
    info["session"] = src.name
    info["snapshot"] = 1 if src.snapshot else 0
    info["source"] = src.path.name
    info["source_sig"] = src.signature()
    start = datetime.fromtimestamp(ev[0][1] / 1000, timezone.utc) if ev else None
    if start is None:
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)", src.name)
        if m:   # folder name is the phone's local time: good enough to pick the GGA day
            start = datetime(*map(int, m.groups()), tzinfo=timezone.utc)
    if ev:
        info["start_utc"] = iso(start)
        end = datetime.fromtimestamp(ev[-1][1] / 1000, timezone.utc)
        info["end_utc"] = iso(end)
        info["duration_min"] = round((end - start).total_seconds() / 60, 1)

    poles, frames, gps_lost, caster_lost, controls_unnumbered = [], [], 0, 0, 0
    controls = []
    for t_ns, wall, text in ev:
        when = datetime.fromtimestamp(wall / 1000, timezone.utc).strftime("%H:%M:%SZ")
        if text.startswith("session start"):
            kv = _kv(text)
            info["app"] = kv.get("app")
            m = re.search(r"device=(.*)$", text)
            info["device"] = m.group(1).strip() if m else None
        elif text.startswith("gps connected") and "link" not in info:
            info["link"] = text.split()[2] if len(text.split()) > 2 else None
        elif text.startswith("caster start"):
            info["caster_mount"] = _kv(text).get("mount")
        elif text.startswith("frame"):
            if text not in frames:
                frames.append(text)
        elif text.startswith("base station=") and "base_station" not in info:
            kv = _kv(text)
            info["base_station"] = kv.get("station")
            info["base_ecef"] = kv.get("ecef")
            info["base_type"] = kv.get("type")
        elif text.startswith("layer base check"):
            info["layer_base_check"] = text
        elif text.startswith("pole_height="):
            v = text.split("=")[1].split()[0]
            poles.append(v if not poles else f"{v} at {when}")
        elif text.startswith("gps lost"):
            gps_lost += 1
        elif text.startswith(("caster lost", "caster failed")):
            caster_lost += 1
        elif text.startswith("stop "):
            kv = _kv(text)
            for k in ("gga", "gga_lost", "rawx", "rawx_lost", "rtcm_crc_errors"):
                info[k] = _i(kv.get(k))
        elif text.startswith("control"):
            kv = _kv(text)
            if "n" in kv:
                controls.append((t_ns, int(kv["n"]), _f(kv.get("pole_height")), _i(kv.get("gga_quality"))))
            else:
                controls_unnumbered += 1
        elif text.startswith("ref end"):
            kv = _kv(text)
            s.refs.setdefault(int(kv["n"]), {})["pauses"] = _i(kv.get("pauses"))
        elif text.startswith("pole check"):
            m = re.match(r"pole check n=(\d+): (.*)$", text)
            if m:
                body = m.group(2)
                kv = _kv(body)
                msg = body.split("; diff_last_m=")[0].replace(";", ",").strip()
                ok = kv.get("ok")
                s.refs.setdefault(int(m.group(1)), {}).update({
                    "pole_check": msg, "diff_last_m": _f(kv.get("diff_last_m")),
                    "diff_mean_m": _f(kv.get("diff_mean_m")), "visits": _i(kv.get("visits")),
                    "last_visit": kv.get("last"),
                    "pole_ok": None if ok is None else int(ok == "true")})
    info["frame"] = " | ".join(frames) or None
    info["pole_heights"] = ", ".join(poles) or None
    info["gps_lost"] = gps_lost
    info["caster_lost"] = caster_lost

    # GGA: track and (for control marks) positions
    ubx = src.read("gps.ubx") or b""
    gga = read_gga(ubx)
    del ubx
    epochs = []
    for off, g in gga:
        g["t"] = utc_of_tod(g["tod"], start) if start else None
        epochs.append((off, g))
    if epochs:
        fixed = sum(1 for _, g in epochs if g["fix"] == 4)
        info["pct_fixed"] = round(100 * fixed / len(epochs), 1)
        info["gga_parsed"] = len(epochs)
    seen = set()
    for _, g in epochs:
        if g["t"] is None or g["lat"] is None or g["h"] is None:
            continue
        sec = int(g["t"].timestamp())
        if sec in seen:
            continue
        seen.add(sec)
        s.track.append({"utc": iso(g["t"]), "t": g["t"].timestamp(), "lat": g["lat"],
                        "lon": g["lon"], "h": g["h"], "fix": g["fix"], "nsat": g["nsat"],
                        "hdop": g["hdop"], "age_s": g["age_s"]})
    s.track.sort(key=lambda e: e["t"])

    # captures
    pts = src.read("points.csv")
    if pts is not None:
        s.fmt = "points.csv"
        for r in read_points(pts.decode("utf-8", "replace")):
            r["source"] = "points.csv"
            s.captures.append(r)
    elif controls:
        s.fmt = "control events"
        offs, tns = read_chunks(src)
        arrival = []
        for off, g in epochs:
            i = bisect.bisect_right(offs, off) - 1
            arrival.append(tns[i] if i >= 0 else None)
        for t_ns, n, pole, _q in controls:
            j = bisect.bisect_right([a if a is not None else -1 for a in arrival], t_ns)
            avg = average_epochs([g for _, g in epochs[max(0, j - 40):j]])
            row = {"n": n, "class": "control", "elapsed_ns": t_ns, "pole_m": pole,
                   "photo_files": [], "note": None, "source": "control event + GGA"}
            if avg:
                t = utc_of_tod(avg.pop("tod"), start)
                row.update(avg, utc=iso(t))
                row["h_ground"] = avg["h_ant"] - pole if pole is not None else None
            else:
                s.remarks.append(f"control n={n}: no GGA fix before the mark")
            s.captures.append(row)
    else:
        s.fmt = "track only"
    if controls_unnumbered:
        s.remarks.append(f"{controls_unnumbered} unnumbered control marks (0.2 format) not imported")

    # photos
    attach = {}
    for _, _, text in ev:
        m = re.match(r"photo file=photos/(\S+) .*?(attached to|own row) n=(\d+)", text)
        if m:
            attach[m.group(1)] = (int(m.group(3)), "capture" if m.group(2) == "attached to" else "own row")
    by_file = {}
    for c in s.captures:
        for p in c["photo_files"]:
            by_file[p] = c["n"]
    for name in src.photo_names():
        m = PHOTO_RE.match(name)
        n = by_file.get(name) or (attach.get(name) or (None,))[0] or (int(m.group(1)) if m else None)
        utc = None
        if m:
            utc = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
        kind = (attach.get(name) or (None, None))[1]
        s.photos.append({"n": n, "name": name, "utc": utc, "attached": kind,
                         "bytes": src.size("photos/" + name)})
    caps = {c["n"]: c for c in s.captures}
    for p in s.photos:
        c = caps.get(p["n"])
        if c is not None and p["name"] not in c["photo_files"]:
            c["photo_files"].append(p["name"])
        if p["attached"] is None and c is not None:
            p["attached"] = "own row" if c["class"] == "photo" else "capture"

    counts = {}
    for c in s.captures:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    info["format"] = s.fmt
    info["n_captures"] = len(s.captures)
    for k in ("surface", "object", "ref", "photo", "control"):
        info[f"n_{k}"] = counts.get(k, 0)
    info["n_photos"] = len(s.photos)
    info["n_track_points"] = len(s.track)
    info["remarks"] = "; ".join(s.remarks) or None
    return s
