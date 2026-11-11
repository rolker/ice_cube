"""Entry point: read and log the receiver, serve status, send checked commands."""
import argparse
import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import signal
import sys
import re
import threading
import urllib.request
from pathlib import Path

from aiohttp import web

from . import __version__, profile
from .caster import Caster, CasterSettings
from .control import Controller
from .datums import FRAMES
from .frames import FrameParser
from .hub import ConnectionLog, RtcmHub, Secret
from .measured import DEFAULT_BINARY, MeasuredSettings, RtkrcvRunner, SolutionLog
from .rawlog import RawLog, compress
from .receiver import ChunkQueue, SerialReader
from .reference import (ReferenceCheck, ReferenceSettings, block_clashes, slugify,
                        summary, unresolved_secret)
from .refhistory import WINDOWS, ReferenceHistory
from .status import Status
from .upload import Uploader, UploadSettings
from .web import INGRESS_PROXY, create_app

LOG = logging.getLogger("gnss_base")
BY_ID_DIR = "/dev/serial/by-id"
HOUSEKEEPING_S = 60
SHUTDOWN_BUDGET_S = 9.0   # Home Assistant kills the app 10 s after asking it to stop
WEB_SHUTDOWN_S = 1.5      # open page requests get this long at shutdown
DEFAULTS = {"device": "", "device_match": "USB_Dual_Serial", "baud": 460800,
            "base_frame": "ITRF2020", "base_epoch": "",
            "caster_enabled": True, "caster_mountpoint": "BASE", "caster_user": "",
            "caster_password": "", "caster_max_clients": 16,
            "upload_host": "rtk2go.com", "upload_port": 2101, "upload_mountpoint": "",
            "upload_password": "", "upload_style": "rev1",
            "measured_enabled": True, "measured_elevation_mask": 15, "measured_snr_mask": 35,
            "references": [],
            "log_dir": "/share/gnss_base", "min_free_gb": 10}
# One entry of `references`: its fields and their defaults when absent. `name`, `host`
# and `mountpoint` have none (required in the schema); `user` is required by the check.
REFERENCE_FIELDS = {"name": "", "enabled": True, "host": "", "port": 2101,
                    "mountpoint": "", "user": "", "password": "", "frame": "",
                    "epoch": "", "send_gga": False, "gga_position": "",
                    "expect_station": None, "reset_utc": "00:00"}
# The 0.7 options the migration reads (reference_* and reference2_*), field by field.
LEGACY_PREFIXES = ("reference", "reference2")
LEGACY_FIELDS = ("enabled", "host", "port", "mountpoint", "user", "password", "frame",
                 "reset_utc", "send_gga", "gga_position", "expect_station")
SUPERVISOR_INFO = "http://supervisor/addons/self/info"
# Options that a command-line argument can override. Passwords are options only: on a
# command line they would show in process lists. The references are options only.
OVERRIDES = ("device", "device_match", "baud", "log_dir", "min_free_gb", "caster_enabled",
             "caster_mountpoint", "caster_max_clients", "upload_host", "upload_port",
             "upload_mountpoint", "upload_style", "measured_enabled",
             "measured_elevation_mask", "measured_snr_mask", "base_frame", "base_epoch")


def load_settings(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", default=os.environ.get("OPTIONS_FILE", "/data/options.json"))
    parser.add_argument("--data-dir", dest="data_dir",
                        help="the app's private state folder (default: the options "
                             "file's folder, /data in Home Assistant)")
    parser.add_argument("--device")
    parser.add_argument("--device-match", dest="device_match")
    parser.add_argument("--baud", type=int)
    parser.add_argument("--log-dir", dest="log_dir")
    parser.add_argument("--min-free-gb", dest="min_free_gb", type=float)
    parser.add_argument("--profile", default=str(profile.DEFAULT_PATH),
                        help="receiver profile (TOML) to check the receiver against")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--by-id-dir", dest="by_id_dir", default=BY_ID_DIR,
                        help="where stable serial names live (for testing)")
    parser.add_argument("--allow-any-client", action="store_true",
                        help="for local testing: skip the Ingress-only check")
    parser.add_argument("--no-caster", dest="caster_enabled", action="store_const",
                        const=False, help="do not run the NTRIP caster")
    parser.add_argument("--caster-host", default="0.0.0.0",
                        help="address the NTRIP caster listens on")
    parser.add_argument("--caster-port", type=int, default=2101,
                        help="port the NTRIP caster listens on inside the container")
    parser.add_argument("--caster-mountpoint", dest="caster_mountpoint")
    parser.add_argument("--caster-max-clients", dest="caster_max_clients", type=int)
    parser.add_argument("--upload-host", dest="upload_host")
    parser.add_argument("--upload-port", dest="upload_port", type=int)
    parser.add_argument("--upload-mountpoint", dest="upload_mountpoint")
    parser.add_argument("--upload-style", dest="upload_style", choices=("rev1", "rev2"))
    parser.add_argument("--no-measured", dest="measured_enabled", action="store_const",
                        const=False, help="do not run rtkrcv for the live measured position")
    parser.add_argument("--measured-elevation-mask", dest="measured_elevation_mask", type=int)
    parser.add_argument("--measured-snr-mask", dest="measured_snr_mask", type=int,
                        help="dB-Hz; 0 = no SNR mask")
    parser.add_argument("--base-frame", dest="base_frame",
                        help="the frame of our own base position (default ITRF2020, the "
                             "frame of a PPP coordinate); empty: not declared; one of "
                             f"{', '.join(FRAMES)}")
    parser.add_argument("--base-epoch", dest="base_epoch",
                        help="when our base position was observed, YYYY-MM-DD or a decimal "
                             "year; empty: today's date")
    parser.add_argument("--no-supervisor", dest="supervisor", action="store_false",
                        help="do not ask the Supervisor for 0.7 reference options to migrate")
    parser.add_argument("--rtkrcv", default=DEFAULT_BINARY,
                        help=f"the rtkrcv program for both instances (default: "
                             f"{DEFAULT_BINARY} on PATH, RTKLIB-EX built into the image)")
    args = parser.parse_args(argv)
    settings = dict(DEFAULTS)
    if os.path.exists(args.options):
        with open(args.options) as stream:
            settings.update(json.load(stream))
    for key in OVERRIDES:
        value = getattr(args, key)
        if value is not None:
            settings[key] = value
    # Absolute: rtkrcv runs in a folder under it, with its own working directory.
    data_dir = os.path.abspath(args.data_dir or os.path.dirname(os.path.abspath(args.options)))
    settings.update(host=args.host, port=args.port, allow_any_client=args.allow_any_client,
                    by_id_dir=args.by_id_dir, profile=args.profile, data_dir=data_dir,
                    caster_host=args.caster_host, caster_port=args.caster_port,
                    rtkrcv=args.rtkrcv, supervisor=args.supervisor)
    return settings


def measured_settings(settings):
    """rtkrcv's settings from the options. Its work folder is `<data_dir>/rtkrcv`, kept
    across restarts for its ephemeris cache."""
    def number(key):
        value = settings[key]
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return MeasuredSettings(
        enabled=bool(settings["measured_enabled"]), binary=str(settings["rtkrcv"]),
        work_dir=Path(settings["data_dir"]).absolute() / "rtkrcv",
        elevation_mask=number("measured_elevation_mask"),
        snr_mask=number("measured_snr_mask"))


def _text(value):
    return str(value if value is not None else "").strip()


def _station(value):
    """An expected station ID: None when empty; a whole number as given, or as digits in
    a text; anything else as is, for problem() to refuse."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def reference_settings(settings, entry, index):
    """One check's settings from an entry of `references` (a dict; absent fields take
    REFERENCE_FIELDS' defaults), `index` its place from 1. Its work folder is
    `<data_dir>/reference-<slug>`; the masks, base_frame and base_epoch are shared. The
    password goes into a Secret and is removed from the entry."""
    def number(key):
        value = settings[key]
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if not isinstance(entry, dict):
        entry = {}
    # A field given as "!secret <name>" text (send_gga, port, enabled too, which are
    # converted below): the entry does not run, and problem() names the field.
    secret_field = next((k for k in REFERENCE_FIELDS if unresolved_secret(entry.get(k))), "")
    values = dict(REFERENCE_FIELDS, **{k: v for k, v in entry.items() if v is not None
                                       or k == "expect_station"})
    password = Secret(str(entry.pop("password", "") or ""))
    reset = values["reset_utc"]
    made = ReferenceSettings(
        enabled=values["enabled"] is not False, host=_text(values["host"]),
        port=values["port"], mountpoint=_text(values["mountpoint"]),
        user=_text(values["user"]), password=password, frame=_text(values["frame"]),
        epoch=_text(values["epoch"]), base_frame=_text(settings["base_frame"]),
        base_epoch=_text(settings["base_epoch"]),
        reset_utc=_text(reset), send_gga=values["send_gga"] is True,
        gga_position=_text(values["gga_position"]),
        expect_station=_station(values["expect_station"]),
        name=values["name"].strip() if isinstance(values["name"], str) else values["name"],
        index=index, secret_field=secret_field, binary=str(settings["rtkrcv"]),
        elevation_mask=number("measured_elevation_mask"),
        snr_mask=number("measured_snr_mask"))
    made.work_dir = Path(settings["data_dir"]).absolute() / f"reference-{made.slug}"
    return made


def all_reference_settings(settings):
    """Every entry's settings, in order; an entry that clashes with an earlier one (the
    same folder name, or the same login while both are on) is blocked, with why. The
    passwords are removed from `settings`."""
    entries = settings.get("references") or []
    if not isinstance(entries, list):
        entries = []
    return block_clashes([reference_settings(settings, entry, index)
                          for index, entry in enumerate(entries, 1)])


def legacy_name(mountpoint, index, taken):
    """A migrated entry's name: the mountpoint in lower case when it is a short plain
    name ("ABCD" -> "abcd"), else "reference<index>"; never one already taken."""
    text = _text(mountpoint)
    name = text.lower() if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,15}", text) else ""
    if not name or slugify(name) in taken:
        name = f"reference{index}"
    while slugify(name) in taken:
        name += "x"
    return name


def legacy_references(options):
    """The 0.7 options (`reference_*`, `reference2_*`) found in `options` as `references`
    entries: one per prefix that has a host or mountpoint set, or is enabled."""
    entries, taken = [], set()
    for index, prefix in enumerate(LEGACY_PREFIXES, 1):
        old = {field: options[f"{prefix}_{field}"] for field in LEGACY_FIELDS
               if f"{prefix}_{field}" in options}
        if not (_text(old.get("host")) or _text(old.get("mountpoint")) or old.get("enabled")):
            continue
        name = legacy_name(old.get("mountpoint"), index, taken)
        taken.add(slugify(name))
        entry = {"name": name}
        for field in LEGACY_FIELDS:
            if field in old and old[field] is not None:
                entry[field] = old[field]
        entry["enabled"] = old.get("enabled") is True
        entries.append(entry)
    return entries


def has_legacy(options):
    return any(key.startswith(f"{prefix}_") for key in options for prefix in LEGACY_PREFIXES)


def supervisor_options(token, url=SUPERVISOR_INFO, timeout=5):
    """This app's saved options as the Supervisor keeps them (`/addons/self/info`), which
    still hold the options a new version dropped from its schema until the
    configuration is saved again; the token is the app's SUPERVISOR_TOKEN. None when
    there is no answer."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.load(reply)
    except (OSError, ValueError):
        return None
    options = (data.get("data") or {}).get("options") if isinstance(data, dict) else None
    return options if isinstance(options, dict) else None


def migrate_references(settings, fetch=None):
    """0.8.0: the reference checks moved from flat options (`reference_*`,
    `reference2_*`) to the `references` list. When `references` is empty, the old values
    are looked for in the options file and, in Home Assistant, in the Supervisor's saved
    options (`fetch()`, which returns them or None; Home Assistant drops options a new
    schema no longer has from options.json, but keeps the saved ones until the
    configuration is saved again). Found ones become `settings["references"]`.

    Returns a notice for the log and the page: {"kind": "migrated" | "none" | None,
    "source", "names", "text", "yaml"} (`yaml`: the entries to paste into the
    configuration, without passwords, GGA positions or !secret references, each
    replaced by a placeholder). The old keys are removed from `settings`. Nothing here
    logs or returns a password or a GGA position.

    The Supervisor's saved options hold a `!secret <name>` as that text (it is resolved
    only into options.json): such an entry is blocked by ReferenceSettings.problem, and
    the notice names its fields, never their values."""
    legacy_here = {k: settings.pop(k) for k in list(settings)
                   if any(k.startswith(f"{p}_") for p in LEGACY_PREFIXES)}
    if settings.get("references"):
        if legacy_here:
            return {"kind": None, "source": None, "names": [], "yaml": None,
                    "text": "0.7 options reference_* in the options are ignored: the "
                            "references list is set."}
        return {"kind": None, "source": None, "names": [], "yaml": None, "text": None}
    source, options = None, None
    if legacy_here:
        source, options = "the options file", legacy_here
    elif fetch is not None:
        fetched = fetch()
        if fetched and has_legacy(fetched):
            source, options = "the Supervisor's saved options", fetched
    entries = legacy_references(options) if options else []
    if not entries:
        return {"kind": "none", "source": None, "names": [], "yaml": None,
                "text": "No reference stations are configured (the references list is "
                        "empty). If you used reference_* options in 0.7.x, enter them "
                        "again as references entries: Home Assistant drops options "
                        "that a new version no longer has. See the README, Migration."}
    settings["references"] = entries
    names = [e["name"] for e in entries]
    secrets = [f"references[{index}].{k}" for index, e in enumerate(entries, 1)
               for k, v in e.items() if unresolved_secret(v)]

    def shown(key, value):
        if unresolved_secret(value):
            return '"<your !secret, as before>"'
        if key in ("password", "gga_position") and value != "":
            return '"<the same as before>"'
        return json.dumps(value)

    yaml = "references:\n" + "".join(
        "  - " + "\n    ".join(f"{k}: {shown(k, v)}" for k, v in e.items()) + "\n"
        for e in entries)
    text = (f"The references list is empty, so the 0.7 reference options from "
            f"{source} are used for now as {len(entries)} "
            f"reference{'s' if len(entries) > 1 else ''} "
            f"({', '.join(names)}). Add them as references entries in the app "
            "configuration and save: Home Assistant forgets the old options the "
            "next time the configuration is saved.")
    if secrets:
        text += (f" {', '.join(secrets)} {'is a' if len(secrets) == 1 else 'are'} "
                 f"!secret{'' if len(secrets) == 1 else 's'}, which the app cannot read "
                 "from the saved options, so that entry does not run: in the new "
                 "references entry, enter the real value or keep using !secret.")
    return {"kind": "migrated", "source": source, "names": names, "yaml": yaml,
            "text": text}


def output_settings(settings):
    """Caster and upload settings from the options. The passwords go straight into
    Secrets, whose repr is redacted, and are removed from `settings`."""
    caster = CasterSettings(
        enabled=bool(settings["caster_enabled"]), host=settings["caster_host"],
        port=settings["caster_port"], mountpoint=str(settings["caster_mountpoint"]),
        user=str(settings["caster_user"]),
        password=Secret(str(settings.pop("caster_password", "") or "")),
        max_clients=settings["caster_max_clients"],
        base_frame=str(settings["base_frame"] if settings["base_frame"] is not None else "").strip(),
        base_epoch=str(settings["base_epoch"] if settings["base_epoch"] is not None else "").strip())
    upload = UploadSettings(
        host=str(settings["upload_host"]).strip(), port=settings["upload_port"],
        mountpoint=str(settings["upload_mountpoint"]).strip(),
        password=Secret(str(settings.pop("upload_password", "") or "")),
        style=str(settings["upload_style"]))
    return caster, upload


def check_device(settings, by_id_dir=BY_ID_DIR):
    """Return "" if the device is the receiver, otherwise why it was refused.

    The device is resolved to the real tty, and accepted only if a stable
    /dev/serial/by-id name for that same tty contains `device_match`. This
    protects a Zigbee stick or any other serial device on the same machine.
    """
    device, match = settings.get("device", ""), settings.get("device_match", "")
    if not device:
        return "No device selected. Choose the receiver in the app configuration."
    if not match:
        return "device_match is empty, so the receiver cannot be identified. Set it."
    try:
        target = os.path.realpath(device, strict=True)
    except OSError:
        return f"Device {device} does not exist."
    names = []
    by_id = Path(by_id_dir)
    if by_id.is_dir():
        for link in by_id.iterdir():
            try:
                if os.path.realpath(link, strict=True) == target:
                    names.append(link.name)
            except OSError:
                continue
    if any(match in name for name in names):
        return ""
    known = ", ".join(sorted(names)) or "no stable name"
    return f"Device {device} ({known}) does not match '{match}', so it was not opened."


class Pipeline:
    def __init__(self, status, log, on_nmea=None, hub=None):
        self.status = status
        self.log = log
        self.parser = FrameParser()
        self.on_nmea = on_nmea
        self.hub = hub

    def on_data(self, data):
        self.status.add_bytes(len(data))
        self.log.write(data)  # the raw log first, before anything else can go wrong
        for kind, payload in self.parser.feed(data):
            self.status.add_frame(kind, payload)
            if kind == "nmea" and self.on_nmea is not None:
                self.on_nmea(payload)
            elif kind == "rtcm" and self.hub is not None:
                self.hub.publish(payload)  # whole, CRC-checked frames only


class ErrorReporter:
    """Send each new error to the add-on log once, not on every repeat."""

    def __init__(self):
        self._last = {}

    def check(self, name, message):
        if message and message != self._last.get(name):
            LOG.error("%s: %s", name, message)
        self._last[name] = message


def merge_storage(storage, other, prefix):
    """Add another RawLog's snapshot (the CORS stream) to the receiver log's for the
    page: its files, named under `prefix`, join the list; the stored bytes and the
    compressed bytes per day add up, so "days until limit" counts both; its own numbers
    (paused, errors, bytes today, dropped) stay on its card."""
    storage["files"] = (storage["files"]
                        + [dict(f, file=prefix + f["file"]) for f in other["files"]])
    storage["stored_bytes"] += other["stored_bytes"]
    per_day = [x["compressed_bytes_per_day"] for x in (storage, other)
               if x["compressed_bytes_per_day"]]
    if per_day:
        total = sum(per_day)
        room = max(storage["free_bytes"] - storage["min_free_bytes"], 0)
        storage["compressed_bytes_per_day"] = total
        storage["days_until_limit"] = round(room / total, 1)
    return storage


async def _raw_housekeeping(log, loop):
    """One round for a RawLog: close a finished day, compress finished days."""
    try:
        log.tidy()
        for path in log.pending_days():
            if not log.start_compress(path):
                continue
            LOG.info("compressing %s", path.name)
            try:
                await loop.run_in_executor(None, compress, path)
                log.compress_done(path)
            except Exception as error:
                LOG.exception("compressing %s failed", path.name)
                log.compress_failed(path, error)
            finally:
                log.end_compress(path)
        if not log.has_compress_failures():
            log.housekeeping_error = ""
    except Exception as error:
        LOG.exception("housekeeping failed")
        log.housekeeping_error = f"housekeeping: {error}"


async def housekeeping(log, errors, *solution_logs, raw_logs=(), histories=()):
    """`log` is the receiver's raw log (its usage record and errors are the app's);
    `raw_logs` other RawLogs (the CORS stream) that get the same tidy and compression;
    `histories` the reference plots' history (refhistory.py), written each round."""
    loop = asyncio.get_running_loop()
    while True:
        await _raw_housekeeping(log, loop)
        with contextlib.suppress(Exception):
            log.record_usage()
        errors.check("logging", log.write_error)
        errors.check("disk", log.disk_error)
        errors.check("housekeeping", log.housekeeping_error)
        for raw_log in raw_logs:
            await _raw_housekeeping(raw_log, loop)
            # One name per log, so the reporter tells their errors apart.
            errors.check(f"CORS raw log {raw_log.directory.name}",
                         raw_log.write_error or raw_log.housekeeping_error)
        for history in histories:
            try:
                await history.flush(loop)
            except Exception:
                LOG.exception("plot history failed")
            errors.check(f"plot history {history.path.name}", history.error)
        for solution_log in solution_logs:
            try:
                await solution_log.housekeep()
            except Exception as error:
                LOG.exception("solution log housekeeping failed")
                solution_log.error = f"solution log housekeeping: {error}"
        await asyncio.sleep(HOUSEKEEPING_S)


class DaemonExecutor(concurrent.futures.ThreadPoolExecutor):
    """The event loop's executor for file work (log writes, a finished day's gzip, the
    upload state, stopping the serial reader). Each call runs in its own daemon thread,
    and `shutdown` never waits, so work still running at the shutdown deadline cannot hold
    the app's exit: it is abandoned. That is safe: a log write is only cut short, and an
    unfinished compression leaves its `.partial` file, which the raw and solution logs
    remove at the next start, and the original day, which is compressed again.
    (A ThreadPoolExecutor only because asyncio's set_default_executor asks for one; its
    pool is never used.)"""

    def __init__(self):
        super().__init__(max_workers=1)
        self._closed = False

    def submit(self, fn, /, *args, **kwargs):
        if self._closed:
            raise RuntimeError("the executor is shut down")
        future = concurrent.futures.Future()

        def work():
            if not future.set_running_or_notify_cancel():
                return
            try:
                result = fn(*args, **kwargs)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)

        threading.Thread(target=work, daemon=True, name="gnss_base-io").start()
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        self._closed = True    # never waits: see the class comment


async def _bounded(deadline, awaitable, what):
    """Await `awaitable` until `deadline` (loop time) at the latest."""
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(awaitable, max(0.1, deadline - loop.time()))
    except asyncio.TimeoutError:
        LOG.error("shutdown: %s did not finish in time; going on", what)
    except Exception:
        LOG.exception("shutdown: %s failed", what)


async def run(settings):
    items = profile.load(settings["profile"])  # a broken profile stops the app here
    status = Status()
    log = RawLog(settings["log_dir"], settings["min_free_gb"])
    loop = asyncio.get_running_loop()
    executor = DaemonExecutor()
    loop.set_default_executor(executor)
    errors = ErrorReporter()
    reader = None

    def write(data):
        return reader.write(data)

    control = Controller(write, Path(settings["log_dir"]) / "commands.jsonl", items, status)
    # The outputs get the hub only: no path from the network to the receiver.
    hub = RtcmHub()
    # Below the raw log's free space floor the connection log is not written either.
    events = ConnectionLog(Path(settings["log_dir"]) / "caster.jsonl",
                           room=lambda: not log.paused)
    events.start()  # file writes in a background task, never on the serial path
    measured = measured_settings(settings)
    token = os.environ.get("SUPERVISOR_TOKEN")
    fetch = (lambda: supervisor_options(token)) if token and settings.get("supervisor", True) else None
    notice = await loop.run_in_executor(None, migrate_references, settings, fetch)
    if notice["kind"] == "migrated":
        LOG.warning("references: %s Entries: %s.", notice["text"], ", ".join(
            f"{name} ({e.get('mountpoint') or '?'})"
            for name, e in zip(notice["names"], settings["references"])))
    elif notice["text"]:
        LOG.info("references: %s", notice["text"])
    reference_options = all_reference_settings(settings)
    rtkrcv = caster = uploader = queue = runner = keeper = None
    references = []

    def solution_header():
        version = rtkrcv.output.version if rtkrcv is not None else None
        return (f"% gnss_base {__version__}, rtkrcv {version or 'unknown version'}, "
                f"single point, elevation mask {measured.elevation_mask} deg, SNR mask "
                f"{measured.snr_mask} dB-Hz, time GPST, lines as rtkrcv sent them (llh)")

    solutions = SolutionLog(Path(settings["log_dir"]) / "measured", solution_header,
                            room=lambda: not log.paused)
    solutions.start()
    # Each reference check's log, `reference-<slug>/YYYY-MM-DD.llh`: the header names
    # the frames (ReferenceCheck.header). The CORS stream as received,
    # `reference-<slug>/raw/YYYY-MM-DD.rtcm3`, with the raw log's rotation, compression
    # and free-space floor. Both only for a check that can run (enabled, no problem), so
    # a blocked duplicate name never writes into another entry's folder. The plots'
    # history is in `<data_dir>/history/` (refhistory.py).
    reference_logs, cors_logs, histories = [], [], []
    for options in reference_options:
        runs = options.enabled and not options.problem()

        def header(index=len(reference_logs)):
            return references[index].header() if len(references) > index else ""

        folder = Path(settings["log_dir"]) / f"reference-{options.slug}"
        reference_log = SolutionLog(folder, header, room=lambda: not log.paused,
                                    suffix=".llh") if runs else None
        if reference_log is not None:
            reference_log.start()
        reference_logs.append(reference_log)
        cors_logs.append(RawLog(folder, settings["min_free_gb"], suffix=".rtcm3")
                         if runs else None)
        histories.append(ReferenceHistory(Path(settings["data_dir"]) / "history",
                                          options.slug).load() if runs else None)
    try:
        caster_settings, upload_settings = output_settings(settings)
        caster = Caster(caster_settings, hub, events, __version__)
        uploader = Uploader(upload_settings, hub, events, __version__,
                            state_path=Path(settings["data_dir"]) / "upload-state.json")
        pipeline = Pipeline(status, log, on_nmea=control.on_nmea, hub=hub)

        def on_solution(solution, line):
            status.add_measured(solution)
            solutions.write(line)

        # rtkrcv gets the hub only, like the other outputs.
        rtkrcv = RtkrcvRunner(measured, hub, on_solution)
        # The reference checks: one more rtkrcv each on the hub, and the receiver's 1005
        # from the status (read only) as the reference for their offsets.
        for options, reference_log, cors_log, history in zip(
                reference_options, reference_logs, cors_logs, histories):
            check = ReferenceCheck(
                options, hub, status.current_base,
                on_solution=None if reference_log is None
                else (lambda solution, line, out=reference_log: out.write(line)),
                version=__version__, on_raw=None if cors_log is None else cors_log.write,
                on_fixed=None if history is None else history.add,
                label=f"reference check {options.name}")
            if history is not None:
                history.expected = check.expected
                history.station = lambda c=check: c._cors_id
            references.append(check)
        # The no-measured-GGA texts mention rtkrcv only while it can give a solution.
        status.measured_expected = lambda: rtkrcv.state() not in ("off", "not available")
        queue = ChunkQueue(loop, pipeline.on_data)

        def on_state(connected, error):
            def apply():
                status.connected = connected
                status.last_error = error
                if connected:
                    LOG.info("receiver connected")
                    control.port_opened()
                else:
                    control.port_lost()
                    errors.check("receiver", error)
            loop.call_soon_threadsafe(apply)

        # The device is checked again before every open, so a receiver that is
        # missing now is picked up later, and a refused device is never opened.
        reader = SerialReader(settings["device"], settings["baud"], queue.put, on_state,
                              check=lambda: check_device(settings, settings["by_id_dir"]))
        reader.start()

        def snapshot():
            result = status.snapshot()
            result["storage"] = log.snapshot()
            for options, cors_log in zip(reference_options, cors_logs):
                if cors_log is not None:
                    merge_storage(result["storage"], cors_log.snapshot(),
                                  f"reference-{options.slug}/raw/")
            result["parser"] = {"unknown_bytes": pipeline.parser.unknown_bytes,
                                "bad_frames": pipeline.parser.bad_frames,
                                "queue_dropped_bytes": queue.dropped_bytes}
            result["receiver"] = control.snapshot()
            result["caster"] = dict(caster.snapshot(), **hub.snapshot(), **events.snapshot())
            result["upload"] = uploader.snapshot()
            result["measured"] = dict(rtkrcv.snapshot(), **status.measured_summary(),
                                      log=solutions.snapshot())
            result["references"] = [
                dict(reference.snapshot(),
                     log=None if reference_log is None else reference_log.snapshot(),
                     raw_log=None if cors_log is None else cors_log.snapshot(),
                     history_error=None if history is None else history.error or None)
                for reference, reference_log, cors_log, history in zip(
                    references, reference_logs, cors_logs, histories)]
            result["reference_summary"] = dict(summary(references), notice={
                key: notice[key] for key in ("kind", "source", "names", "text", "yaml")})
            return result

        def plots(window):
            """The page's reference plots (refhistory.py): each check's residual bins of
            the last `window` (1h, 24h, 7d), its session mean and east-north covariance,
            and its daily means."""
            window_s = WINDOWS[window]
            result = {"window": window, "window_s": window_s, "references": []}
            for reference, history in zip(references, histories):
                if not reference.settings.enabled:
                    continue
                residual = reference.residual()
                session = reference.stats.session_mean()
                result["references"].append({
                    "name": reference.settings.name, "slug": reference.settings.slug,
                    "index": reference.settings.index,
                    "station_frame": reference._station_frame or None,
                    "no_residual": None if residual is not None
                    else reference.no_expected() or "no fixed solution this session yet",
                    "points": [] if history is None else history.points(window_s),
                    "days": [] if history is None else history.days(),
                    "session_mean": None if residual is None
                    else [round(v, 4) for v in residual],
                    "session_std": None if session is None
                    else [round(v, 4) for v in session[1]],
                    "covariance_en": None if residual is None
                    else [[round(v, 8) for v in row]
                          for row in reference.stats.covariance_en()],
                    "fixed": reference.stats.fixed_count})
            return result

        allowed = () if settings["allow_any_client"] else (INGRESS_PROXY,)
        app = create_app(snapshot, allowed, get_track=status.track, control=control,
                         get_plots=plots)
        runner = web.AppRunner(app, shutdown_timeout=WEB_SHUTDOWN_S)
        await runner.setup()
        await web.TCPSite(runner, settings["host"], settings["port"]).start()
        LOG.info("status page on port %s", settings["port"])
        await caster.start()
        uploader.start()
        rtkrcv.start()
        for reference in references:
            reference.start()

        stop = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.add_signal_handler(name, stop.set)
        keeper = asyncio.create_task(housekeeping(
            log, errors, solutions, *[x for x in reference_logs if x is not None],
            raw_logs=[x for x in cors_logs if x is not None],
            histories=[x for x in histories if x is not None]))
        await stop.wait()
    finally:
        await shutdown(SHUTDOWN_BUDGET_S, keeper=keeper, rtkrcv=rtkrcv, solutions=solutions,
                       outputs=[x for x in (uploader, caster) if x is not None],
                       events=events, control=control, reader=reader, queue=queue,
                       log=log, runner=runner, references=references,
                       reference_logs=reference_logs, cors_logs=cors_logs,
                       histories=histories)
        executor.shutdown(wait=False, cancel_futures=True)


async def shutdown(budget, keeper, rtkrcv, solutions, outputs, events, control, reader,
                   queue, log, runner, references=(), reference_logs=(), cors_logs=(),
                   histories=()):
    """Stop everything within `budget` seconds from now (Home Assistant's 10 s stop
    timeout), independent parts at once, each bounded by the time left:
    - every rtkrcv (SIGTERM so each saves its ephemeris cache, SIGKILL after 3 s), and
      the reference checks' NTRIP clients, then the CORS raw logs and the solution
      logs, so the lines they sent last are written;
    - the upload and the caster, then the connection log, which records their ends;
    - the startup check, the serial reader, then what it still queued, into the raw log;
    - the status page, with WEB_SHUTDOWN_S for open requests.
    Anything still running at the deadline, such as a day being compressed, is left
    behind (see DaemonExecutor)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    if keeper is not None:
        keeper.cancel()   # housekeeping: a compression in its thread is abandoned

    def left(margin=0.0):
        return max(0.1, deadline - loop.time() - margin)

    async def measured_part():
        await asyncio.gather(*(x.stop() for x in (rtkrcv, *references) if x is not None))
        for cors_log in cors_logs:
            if cors_log is not None:
                cors_log.close()   # after the client stopped: its last bytes are in
        logs = [x for x in (solutions, *reference_logs) if x is not None]
        for history in histories:
            if history is not None:
                history.close_bin()
                await history.flush(loop)
        await asyncio.gather(*(x.stop(timeout=left(0.2)) for x in logs))

    async def outputs_part():
        await asyncio.gather(*(output.stop() for output in outputs), return_exceptions=True)
        await events.stop(timeout=left(0.2))

    async def receiver_part():
        await control.stop()
        if reader is not None:
            await loop.run_in_executor(None, reader.stop)
        if queue is not None:
            queue.drain()
            queue.close()  # a reader thread that outlived stop() can no longer reach the log
        log.close()

    async def web_part():
        if runner is not None:
            await runner.cleanup()

    await asyncio.gather(_bounded(deadline, measured_part(), "rtkrcv and its log"),
                         _bounded(deadline, outputs_part(), "the outputs and their log"),
                         _bounded(deadline, receiver_part(), "the receiver"),
                         _bounded(deadline, web_part(), "the status page"))
    if keeper is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(keeper, 0.1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(load_settings()))


if __name__ == "__main__":
    sys.exit(main())
