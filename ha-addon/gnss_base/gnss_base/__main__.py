"""Stage 1 entry point: listen to the receiver, log it, serve status."""
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

from .frames import FrameParser
from .rawlog import RawLog, compress
from .receiver import ChunkQueue, SerialReader
from .status import Status
from .web import INGRESS_PROXY, create_app

LOG = logging.getLogger("gnss_base")
BY_ID_DIR = "/dev/serial/by-id"
HOUSEKEEPING_S = 60
DEFAULTS = {"device": "", "device_match": "USB_Dual_Serial", "baud": 460800,
            "log_dir": "/share/gnss_base", "min_free_gb": 10}


def load_settings(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", default=os.environ.get("OPTIONS_FILE", "/data/options.json"))
    parser.add_argument("--device")
    parser.add_argument("--device-match", dest="device_match")
    parser.add_argument("--baud", type=int)
    parser.add_argument("--log-dir", dest="log_dir")
    parser.add_argument("--min-free-gb", dest="min_free_gb", type=float)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--by-id-dir", dest="by_id_dir", default=BY_ID_DIR,
                        help="where stable serial names live (for testing)")
    parser.add_argument("--allow-any-client", action="store_true",
                        help="for local testing: skip the Ingress-only check")
    args = parser.parse_args(argv)
    settings = dict(DEFAULTS)
    if os.path.exists(args.options):
        with open(args.options) as stream:
            settings.update(json.load(stream))
    for key in ("device", "device_match", "baud", "log_dir", "min_free_gb"):
        value = getattr(args, key)
        if value is not None:
            settings[key] = value
    settings.update(host=args.host, port=args.port, allow_any_client=args.allow_any_client,
                    by_id_dir=args.by_id_dir)
    return settings


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
    def __init__(self, status, log):
        self.status = status
        self.log = log
        self.parser = FrameParser()

    def on_data(self, data):
        self.status.add_bytes(len(data))
        self.log.write(data)
        for kind, payload in self.parser.feed(data):
            self.status.add_frame(kind, payload)


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
    status = Status()
    log = RawLog(settings["log_dir"], settings["min_free_gb"])
    pipeline = Pipeline(status, log)
    loop = asyncio.get_running_loop()
    queue = ChunkQueue(loop, pipeline.on_data)
    errors = ErrorReporter()

    def on_state(connected, error):
        def apply():
            status.connected = connected
            status.last_error = error
            if connected:
                LOG.info("receiver connected")
            else:
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
        return result

    allowed = () if settings["allow_any_client"] else (INGRESS_PROXY,)
    runner = web.AppRunner(create_app(snapshot, allowed, get_track=status.track))
    await runner.setup()
    await web.TCPSite(runner, settings["host"], settings["port"]).start()
    LOG.info("status page on port %s", settings["port"])

    stop = asyncio.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    keeper = asyncio.create_task(housekeeping(log, errors))
    await stop.wait()
    keeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await keeper
    await loop.run_in_executor(None, reader.stop)
    queue.drain()
    queue.close()  # a reader thread that outlived stop() can no longer reach the log
    log.close()
    await runner.cleanup()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(load_settings()))


if __name__ == "__main__":
    sys.exit(main())
