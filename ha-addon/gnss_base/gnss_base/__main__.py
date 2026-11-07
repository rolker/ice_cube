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
import threading
from pathlib import Path

from aiohttp import web

from . import __version__, profile
from .caster import Caster, CasterSettings
from .control import Controller
from .frames import FrameParser
from .hub import ConnectionLog, RtcmHub, Secret
from .measured import DEFAULT_BINARY, MeasuredSettings, RtkrcvRunner, SolutionLog
from .rawlog import RawLog, compress
from .receiver import ChunkQueue, SerialReader
from .reference import ReferenceCheck, ReferenceSettings
from .status import Status
from .upload import Uploader, UploadSettings
from .web import INGRESS_PROXY, create_app

LOG = logging.getLogger("gnss_base")
BY_ID_DIR = "/dev/serial/by-id"
HOUSEKEEPING_S = 60
SHUTDOWN_BUDGET_S = 9.0   # Home Assistant kills the app 10 s after asking it to stop
WEB_SHUTDOWN_S = 1.5      # open page requests get this long at shutdown
DEFAULTS = {"device": "", "device_match": "USB_Dual_Serial", "baud": 460800,
            "log_dir": "/share/gnss_base", "min_free_gb": 10,
            "caster_enabled": True, "caster_mountpoint": "BASE", "caster_user": "",
            "caster_password": "", "caster_max_clients": 16,
            "upload_host": "rtk2go.com", "upload_port": 2101, "upload_mountpoint": "",
            "upload_password": "", "upload_style": "rev1",
            "measured_enabled": True, "measured_elevation_mask": 15, "measured_snr_mask": 35,
            "reference_enabled": False, "reference_host": "", "reference_port": 2101,
            "reference_mountpoint": "", "reference_user": "", "reference_password": "",
            "reference_expected_enu": "0 0 0", "reference_reset_utc": "00:00"}
# Options that a command-line argument can override. Passwords are options only: on a
# command line they would show in process lists.
OVERRIDES = ("device", "device_match", "baud", "log_dir", "min_free_gb", "caster_enabled",
             "caster_mountpoint", "caster_max_clients", "upload_host", "upload_port",
             "upload_mountpoint", "upload_style", "measured_enabled",
             "measured_elevation_mask", "measured_snr_mask", "reference_enabled",
             "reference_host", "reference_port", "reference_mountpoint", "reference_user",
             "reference_expected_enu", "reference_reset_utc")


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
    parser.add_argument("--reference", dest="reference_enabled", action="store_const",
                        const=True, help="run the reference check against a CORS station")
    parser.add_argument("--no-reference", dest="reference_enabled", action="store_const",
                        const=False, help="do not run the reference check")
    parser.add_argument("--reference-host", dest="reference_host")
    parser.add_argument("--reference-port", dest="reference_port", type=int)
    parser.add_argument("--reference-mountpoint", dest="reference_mountpoint")
    parser.add_argument("--reference-user", dest="reference_user")
    parser.add_argument("--reference-expected-enu", dest="reference_expected_enu",
                        help='expected offset "E N U" in metres (solution minus our 1005)')
    parser.add_argument("--reference-reset-utc", dest="reference_reset_utc",
                        help="daily restart of the reference rtkrcv, HH:MM UTC; empty: never")
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
                    rtkrcv=args.rtkrcv)
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


def reference_settings(settings):
    """The reference check's settings from the options. Its work folder is
    `<data_dir>/reference`; the masks are the measured position's. The password goes
    into a Secret and is removed from `settings`."""
    def number(key):
        value = settings[key]
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def text(key):
        return str(settings[key] if settings[key] is not None else "").strip()

    return ReferenceSettings(
        enabled=bool(settings["reference_enabled"]), host=text("reference_host"),
        port=settings["reference_port"], mountpoint=text("reference_mountpoint"),
        user=text("reference_user"),
        password=Secret(str(settings.pop("reference_password", "") or "")),
        expected_enu=text("reference_expected_enu"), reset_utc=text("reference_reset_utc"),
        binary=str(settings["rtkrcv"]),
        work_dir=Path(settings["data_dir"]).absolute() / "reference",
        elevation_mask=number("measured_elevation_mask"), snr_mask=number("measured_snr_mask"))


def output_settings(settings):
    """Caster and upload settings from the options. The passwords go straight into
    Secrets, whose repr is redacted, and are removed from `settings`."""
    caster = CasterSettings(
        enabled=bool(settings["caster_enabled"]), host=settings["caster_host"],
        port=settings["caster_port"], mountpoint=str(settings["caster_mountpoint"]),
        user=str(settings["caster_user"]),
        password=Secret(str(settings.pop("caster_password", "") or "")),
        max_clients=settings["caster_max_clients"])
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


async def housekeeping(log, errors, *solution_logs):
    loop = asyncio.get_running_loop()
    while True:
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
            log.record_usage()
            if not log.has_compress_failures():
                log.housekeeping_error = ""
        except Exception as error:
            LOG.exception("housekeeping failed")
            log.housekeeping_error = f"housekeeping: {error}"
        errors.check("logging", log.write_error)
        errors.check("disk", log.disk_error)
        errors.check("housekeeping", log.housekeeping_error)
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
    reference_options = reference_settings(settings)
    rtkrcv = caster = uploader = queue = runner = keeper = reference = None

    def solution_header():
        version = rtkrcv.output.version if rtkrcv is not None else None
        return (f"% gnss_base {__version__}, rtkrcv {version or 'unknown version'}, "
                f"single point, elevation mask {measured.elevation_mask} deg, SNR mask "
                f"{measured.snr_mask} dB-Hz, time GPST, lines as rtkrcv sent them (llh)")

    solutions = SolutionLog(Path(settings["log_dir"]) / "measured", solution_header,
                            room=lambda: not log.paused)
    solutions.start()
    # The reference check's log: the header names the frames (ReferenceCheck.header).
    reference_log = SolutionLog(Path(settings["log_dir"]) / "reference",
                                lambda: reference.header() if reference is not None else "",
                                room=lambda: not log.paused, suffix=".llh")
    reference_log.start()
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
        # The reference check: a second rtkrcv on the hub, and the receiver's 1005 from
        # the status (read only) as the reference for its offsets.
        reference = ReferenceCheck(reference_options, hub, status.current_base,
                                   on_solution=lambda solution, line: reference_log.write(line),
                                   version=__version__)
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
            result["parser"] = {"unknown_bytes": pipeline.parser.unknown_bytes,
                                "bad_frames": pipeline.parser.bad_frames,
                                "queue_dropped_bytes": queue.dropped_bytes}
            result["receiver"] = control.snapshot()
            result["caster"] = dict(caster.snapshot(), **hub.snapshot(), **events.snapshot())
            result["upload"] = uploader.snapshot()
            result["measured"] = dict(rtkrcv.snapshot(), **status.measured_summary(),
                                      log=solutions.snapshot())
            result["reference"] = dict(reference.snapshot(), log=reference_log.snapshot())
            return result

        allowed = () if settings["allow_any_client"] else (INGRESS_PROXY,)
        app = create_app(snapshot, allowed, get_track=status.track, control=control)
        runner = web.AppRunner(app, shutdown_timeout=WEB_SHUTDOWN_S)
        await runner.setup()
        await web.TCPSite(runner, settings["host"], settings["port"]).start()
        LOG.info("status page on port %s", settings["port"])
        await caster.start()
        uploader.start()
        rtkrcv.start()
        reference.start()

        stop = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.add_signal_handler(name, stop.set)
        keeper = asyncio.create_task(housekeeping(log, errors, solutions, reference_log))
        await stop.wait()
    finally:
        await shutdown(SHUTDOWN_BUDGET_S, keeper=keeper, rtkrcv=rtkrcv, solutions=solutions,
                       outputs=[x for x in (uploader, caster) if x is not None],
                       events=events, control=control, reader=reader, queue=queue,
                       log=log, runner=runner, reference=reference, reference_log=reference_log)
        executor.shutdown(wait=False, cancel_futures=True)


async def shutdown(budget, keeper, rtkrcv, solutions, outputs, events, control, reader,
                   queue, log, runner, reference=None, reference_log=None):
    """Stop everything within `budget` seconds from now (Home Assistant's 10 s stop
    timeout), independent parts at once, each bounded by the time left:
    - both rtkrcv (SIGTERM so each saves its ephemeris cache, SIGKILL after 3 s), then
      their solution logs, so the lines they sent last are written;
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
        await asyncio.gather(*(x.stop() for x in (rtkrcv, reference) if x is not None))
        logs = [x for x in (solutions, reference_log) if x is not None]
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
