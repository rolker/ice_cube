"""The Ice Cube plugin's logic that does not need QGIS: settings, path resolution, the
import command line, GeoPackage table lists, the write-lock probe and the summary text.
Tested in analysis/qgis/tests/test_plugin.py without QGIS.

Settings (QgsSettings under `icecube/`, see SETTINGS) override the site config (TOML,
the same file import_sessions.py takes with --config); relative paths in the site config
are relative to the config file, as for the scripts.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
import tomllib
from pathlib import Path

PREFIX = "icecube/"
SETTINGS = {   # key: default
    "gpkg": "",          # season GeoPackage (default: [season].gpkg of the site config)
    "input_dir": "",     # folder with IceCubeLogger_<session>.zip files ([season].input_dir)
    "site_config": "",   # site config TOML (season, base, origin)
    "layers": [],        # map-layer GeoJSON files ([season].layers)
}
PIPELINE = ("gpkg_writer", "local_frame", "logger_session", "import_sessions", "export_labels",
            "build_project")


# ----------------------------------------------------------------------------- settings

def as_list(v):
    """QgsSettings gives a list back as a list, a single string, or None."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if str(x).strip()]


def load_settings(store):
    """Settings from `store` (QgsSettings, or anything with value(key, default))."""
    out = {}
    for k, default in SETTINGS.items():
        v = store.value(PREFIX + k, default)
        out[k] = as_list(v) if isinstance(default, list) else ("" if v is None else str(v).strip())
    return out


def save_settings(store, values):
    for k, default in SETTINGS.items():
        v = values.get(k, default)
        store.setValue(PREFIX + k, as_list(v) if isinstance(default, list) else str(v or "").strip())


# ----------------------------------------------------------------------------- paths

class SetupError(ValueError):
    """A setting is missing or wrong; the message says what to set."""


def read_site_config(path):
    """(config dict, its directory); ({}, None) without a path."""
    if not path:
        return {}, None
    p = Path(path).expanduser()
    if not p.is_file():
        raise SetupError(f"site config not found: {p} (Ice Cube > Settings)")
    try:
        return tomllib.loads(p.read_text(encoding="utf-8")), p.resolve().parent
    except tomllib.TOMLDecodeError as e:
        raise SetupError(f"site config {p.name} is not valid TOML: {e}") from e


def _abs(p, base):
    p = Path(p).expanduser()
    return p if p.is_absolute() or base is None else base / p


def resolve(settings):
    """The effective paths: settings first, then the site config. Returns a dict with
    site_config, gpkg, input_dir, layers (lists of str), season, labels_csv, photos_dir,
    qgz (str or None). Raises SetupError when the site config cannot be read."""
    cfg, cfg_dir = read_site_config(settings.get("site_config"))
    season = cfg.get("season", {})
    gpkg = settings.get("gpkg") or (str(_abs(season["gpkg"], cfg_dir)) if season.get("gpkg") else "")
    input_dir = settings.get("input_dir") or (str(_abs(season["input_dir"], cfg_dir))
                                              if season.get("input_dir") else "")
    layers = as_list(settings.get("layers")) or [str(_abs(p, cfg_dir)) for p in season.get("layers", [])]
    out = {"site_config": settings.get("site_config") or "", "gpkg": gpkg, "input_dir": input_dir,
           "layers": layers, "season": None, "labels_csv": None, "photos_dir": None, "qgz": None}
    if gpkg:
        g = Path(gpkg)
        meta = gpkg_meta(g) if g.is_file() else {}
        name = meta.get("season") or season.get("name") or g.stem
        out["season"] = name
        out["qgz"] = str(g.with_name(name + ".qgz"))
        out["photos_dir"] = str(g.parent / "photos")
        out["labels_csv"] = (str(_abs(season["labels_csv"], cfg_dir)) if season.get("labels_csv")
                             else str(g.with_name(g.stem + "-captures.csv")))
    return out


def require(resolved, *keys):
    """SetupError naming the first missing key among `keys`."""
    what = {"gpkg": "the season GeoPackage", "input_dir": "the season input folder"}
    for k in keys:
        if not resolved.get(k):
            raise SetupError(f"no {what.get(k, k)}: set it in Ice Cube > Settings "
                             "(or in the site config's [season])")
    return resolved


def import_argv(resolved, force=False):
    """The import_sessions.py command line for these settings."""
    require(resolved, "gpkg", "input_dir")
    if not Path(resolved["input_dir"]).exists():
        raise SetupError(f"season input folder not found: {resolved['input_dir']}")
    argv = [resolved["input_dir"], "--gpkg", resolved["gpkg"]]
    if resolved.get("site_config"):
        argv += ["--config", resolved["site_config"]]
    if resolved.get("layers"):
        missing = [p for p in resolved["layers"] if not Path(p).is_file()]
        if missing:
            raise SetupError("map layer file(s) not found: " + ", ".join(missing))
        argv += ["--layers", *resolved["layers"]]
    if force:
        argv.append("--force")
    return argv


def same_file(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(os.path.abspath(a))) == \
        os.path.normcase(os.path.normpath(os.path.abspath(b)))


def source_path(source):
    """The file of a layer source such as 'C:/x/season.gpkg|layername=captures'."""
    p = (source or "").split("|", 1)[0].strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
        if len(p) > 2 and p[0] == "/" and p[2] == ":":   # file:///C:/...
            p = p[1:]
    return p


def pipeline_dir(plugin_dir):
    """Where the pipeline modules are: the plugin's vendored `pipeline/` (the packaged
    zip), else analysis/qgis of the repo the plugin folder sits in (development)."""
    plugin_dir = Path(plugin_dir)
    for d in (plugin_dir / "pipeline", plugin_dir.parents[1]):
        if all((d / f"{m}.py").is_file() for m in PIPELINE):
            return d
    raise SetupError(f"pipeline modules not found next to the plugin ({plugin_dir}); "
                     "reinstall icecube_qgis.zip built by package_plugin.py")


def add_pipeline_path(plugin_dir):
    d = str(pipeline_dir(plugin_dir))
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


# ----------------------------------------------------------------------------- gpkg

def gpkg_tables(gpkg):
    """Table names in gpkg_contents (empty when the file does not exist yet)."""
    if not gpkg or not Path(gpkg).is_file():
        return set()
    con = sqlite3.connect(f"file:{Path(gpkg).as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        return {r[0] for r in con.execute("SELECT table_name FROM gpkg_contents")}
    except sqlite3.DatabaseError:
        return set()
    finally:
        con.close()


def gpkg_meta(gpkg):
    con = sqlite3.connect(f"file:{Path(gpkg).as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        return dict(con.execute("SELECT key, value FROM icecube_meta"))
    except sqlite3.DatabaseError:
        return {}
    finally:
        con.close()


def write_lock_error(gpkg, timeout=5.0):
    """None when the gpkg can be written now (or does not exist yet), else the reason.
    Takes and releases a write lock, as the import will."""
    if not gpkg or not Path(gpkg).is_file():
        return None
    try:
        con = sqlite3.connect(str(gpkg), timeout=timeout, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("ROLLBACK")
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        return f"{Path(gpkg).name} is in use ({e}): close other programs using it and try again"
    return None


# ----------------------------------------------------------------------------- report

def summary_text(result):
    """One short paragraph from import_sessions.run()'s summary."""
    added = result.get("added", [])
    new = [a for a in added if not a.get("replaced")]
    redone = [a for a in added if a.get("replaced")]
    parts = [f"{len(new)} new session(s)"]
    if redone:
        parts.append(f"{len(redone)} re-read")
    parts.append(f"{len(result.get('unchanged', []))} unchanged")
    caps = sum(a.get("captures", 0) for a in added)
    phot = sum(a.get("photos", 0) for a in added)
    text = ", ".join(parts) + f"; {caps} captures and {phot} photos read"
    tot = result.get("totals") or {}
    if tot:
        text += (f". Season: {tot.get('sessions', 0)} sessions, {tot.get('captures', 0)} captures, "
                 f"{tot.get('photos', 0)} photos, {tot.get('refs', 0)} refs")
    if result.get("recomputed"):
        text += f". x/y/z recomputed about {result.get('origin')}"
    return text + "."


# ----------------------------------------------------------------------------- log file

LOG_NAME = "icecube_qgis.log"
LOG_MAX_BYTES = 1_000_000
_log_lock = threading.Lock()


def log_path(resolved=None, fallback_dir=None):
    """Where the plugin's log file goes: next to the season GeoPackage when it is known
    (so it sits with the data), else in `fallback_dir` (the QGIS profile folder)."""
    gpkg = (resolved or {}).get("gpkg")
    if gpkg:
        return str(Path(gpkg).with_name(LOG_NAME))
    return str(Path(fallback_dir) / LOG_NAME) if fallback_dir else None


def append_log(path, msg, level="Info", now=None):
    """Append one timestamped entry to the log file (thread-safe; the import runs in a
    background task). Rotates to <name>.1 above LOG_MAX_BYTES. Never raises: a log that
    cannot be written must not break the action being logged. Returns True if written."""
    if not path:
        return False
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    text = str(msg).rstrip("\n")
    lines = text.split("\n")
    entry = f"{stamp} {level.upper():8s} {lines[0]}\n" + "".join(f"    {l}\n" for l in lines[1:])
    try:
        with _log_lock:
            p = Path(path)
            if p.is_file() and p.stat().st_size > LOG_MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(entry)
        return True
    except OSError:
        return False
