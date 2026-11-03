"""Entry point: read and log the receiver, serve status, send checked commands."""
import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from . import __version__, profile
from .caster import Caster, CasterSettings
from .control import Controller
from .frames import FrameParser
from .hub import ConnectionLog, RtcmHub, Secret
from .rawlog import RawLog, compress
from .receiver import ChunkQueue, SerialReader
from .status import Status
from .upload import Uploader, UploadSettings
from .web import INGRESS_PROXY, create_app

LOG = logging.getLogger("gnss_base")
BY_ID_DIR = "/dev/serial/by-id"
HOUSEKEEPING_S = 60
DEFAULTS = {"device": "", "device_match": "USB_Dual_Serial", "baud": 460800,
            "log_dir": "/share/gnss_base", "min_free_gb": 10,
            "caster_enabled": True, "caster_mountpoint": "BASE", "caster_user": "",
            "caster_password": "", "caster_max_clients": 4,
            "upload_host": "rtk2go.com", "upload_port": 2101, "upload_mountpoint": "",
            "upload_password": "", "upload_style": "rev1"}
# Options that a command-line argument can override. Passwords are options only: on a
# command line they would show in process lists.
OVERRIDES = ("device", "device_match", "baud", "log_dir", "min_free_gb", "caster_enabled",
             "caster_mountpoint", "caster_max_clients", "upload_host", "upload_port",
             "upload_mountpoint", "upload_style")


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
    args = parser.parse_args(argv)
    settings = dict(DEFAULTS)
    if os.path.exists(args.options):
        with open(args.options) as stream:
            settings.update(json.load(stream))
    for key in OVERRIDES:
        value = getattr(args, key)
        if value is not None:
            settings[key] = value
    data_dir = args.data_dir or os.path.dirname(os.path.abspath(args.options))
    settings.update(host=args.host, port=args.port, allow_any_client=args.allow_any_client,
                    by_id_dir=args.by_id_dir, profile=args.profile, data_dir=data_dir,
                    caster_host=args.caster_host, caster_port=args.caster_port)
    return settings


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


async def housekeeping(log, errors):
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
        await asyncio.sleep(HOUSEKEEPING_S)


async def run(settings):
    items = profile.load(settings["profile"])  # a broken profile stops the app here
    status = Status()
    log = RawLog(settings["log_dir"], settings["min_free_gb"])
    loop = asyncio.get_running_loop()
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
    try:
        caster_settings, upload_settings = output_settings(settings)
        caster = Caster(caster_settings, hub, events, __version__)
        uploader = Uploader(upload_settings, hub, events, __version__,
                            state_path=Path(settings["data_dir"]) / "upload-state.json")
        pipeline = Pipeline(status, log, on_nmea=control.on_nmea, hub=hub)
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
            return result

        allowed = () if settings["allow_any_client"] else (INGRESS_PROXY,)
        app = create_app(snapshot, allowed, get_track=status.track, control=control)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, settings["host"], settings["port"]).start()
        LOG.info("status page on port %s", settings["port"])
        await caster.start()
        uploader.start()

        stop = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, stop.set)
        keeper = asyncio.create_task(housekeeping(log, errors))
        await stop.wait()
        keeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keeper
        await uploader.stop()
        await caster.stop()
        await control.stop()
        await loop.run_in_executor(None, reader.stop)
        queue.drain()
        queue.close()  # a reader thread that outlived stop() can no longer reach the log
        log.close()
        await runner.cleanup()
    finally:
        # Even if starting up or shutting down failed: end the writer task,
        # waiting at most a few seconds for the disk.
        await events.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(load_settings()))


if __name__ == "__main__":
    sys.exit(main())
