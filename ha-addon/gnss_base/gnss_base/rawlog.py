"""Write every received byte to daily files, compress old days, record growth."""
import csv
import gzip
import os
import shutil
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

GB = 1024 ** 3
USAGE_INTERVAL_S = 3600
CHECK_DISK_INTERVAL_S = 60
COMPRESS_RETRY_S = 3600
# Compression may run below the logging floor, because it frees space overall,
# but never if it could bring free space under this absolute reserve.
COMPRESS_RESERVE_BYTES = 1 * GB
CHUNK = 1024 * 1024


def utc_day(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _gz_target(path):
    target = path.with_name(path.name + ".gz")
    part = 1
    while target.exists():  # never overwrite: a day can restart after a failure
        part += 1
        target = path.with_name(f"{path.stem}-{part}{path.suffix}.gz")
    return target


def compress(path, disk_free=None, reserve=COMPRESS_RESERVE_BYTES):
    """Gzip one finished day. The original is deleted only after the copy is
    on disk and verified, and only if it did not change meanwhile. On any
    failure the partial or new file is removed and the original is left alone."""
    disk_free = disk_free or (lambda: shutil.disk_usage(path.parent).free)
    before = path.stat()
    size = before.st_size
    if disk_free() - size < reserve:
        raise OSError(f"not enough free space to compress {path.name}")
    target = _gz_target(path)
    partial = target.with_name(target.name + ".partial")
    try:
        with open(path, "rb") as source, open(partial, "wb") as raw_sink:
            with gzip.GzipFile(fileobj=raw_sink, mode="wb", compresslevel=6, mtime=0) as sink:
                while chunk := source.read(CHUNK):
                    sink.write(chunk)
                    if disk_free() < reserve:
                        raise OSError(f"free space fell under the reserve compressing {path.name}")
            raw_sink.flush()
            os.fsync(raw_sink.fileno())
        try:
            verified = _gzip_size(partial) == size
        except (EOFError, zlib.error) as error:
            raise OSError(f"verification failed compressing {path.name}: {error}") from error
        if not verified:
            raise OSError(f"verification failed compressing {path.name}")
        os.replace(partial, target)
        _fsync_dir(path.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        target.unlink(missing_ok=True)
        _fsync_dir(path.parent)
        raise OSError(f"{path.name} changed while it was being compressed; kept it")
    path.unlink()
    _fsync_dir(path.parent)
    return target


def _gzip_size(path):
    """Decompressed length, reading the whole file so corruption is caught."""
    total = 0
    with gzip.open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            total += len(chunk)
    return total


class RawLog:
    """All methods run on the event loop thread, except `compress`, which the
    caller runs in an executor on a file this object no longer has open. The
    caller marks that file with `start_compress` and `end_compress` so that no
    write reopens it meanwhile, even if the clock steps back. Bytes for a day
    that is being compressed go to `<day>-late-<n>.raw` instead, which is
    compressed in turn once that day is over."""

    def __init__(self, directory, min_free_gb, clock=time.time, disk_free=None,
                 suffix=".raw"):
        self.directory = Path(directory)
        self.raw_dir = self.directory / "raw"
        self.suffix = suffix       # of the day files: .raw (the receiver), .rtcm3 (a CORS)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.usage_path = self.directory / "storage-usage.csv"
        self.min_free_bytes = int(min_free_gb * GB)
        self._clock = clock
        self._disk_free = disk_free or (lambda: shutil.disk_usage(self.directory).free)
        self._day = None           # the UTC day of the open file
        self._stem = None          # its name without .raw: the day, or a late file
        self._file = None
        self._last_disk_check = 0
        self._last_usage = 0
        self._compress_failed = {}
        self._compressing = set()
        # A failed close is reported until a later close succeeds, and its day
        # is not compressed until `tidy` has confirmed the file is closed.
        self._close_error = ""
        self._unclosed = {}
        self.free_bytes = 0
        self.paused = False
        try:
            self.bytes_today = self.path_for(utc_day(clock())).stat().st_size
        except OSError:
            self.bytes_today = 0
        self.dropped_bytes = 0
        self.write_error = ""
        self.disk_error = ""
        self.housekeeping_error = ""
        self.refresh_disk()
        for leftover in self.raw_dir.glob("*.partial"):
            leftover.unlink(missing_ok=True)

    def path_for(self, day):
        return self.raw_dir / f"{day}{self.suffix}"

    def _writable_path(self, day):
        """The day's file, or, if that is being compressed, a late file for the day."""
        path = self.path_for(day)
        part = 0
        while path.name in self._compressing:
            part += 1
            path = self.raw_dir / f"{day}-late-{part}{self.suffix}"
        return path

    def refresh_disk(self):
        self._last_disk_check = self._clock()
        try:
            self.free_bytes = self._disk_free()
        except OSError as error:  # keep the last known free space and pause state
            self.disk_error = f"disk check: {error}"
            return
        self.disk_error = ""
        self.paused = self.free_bytes < self.min_free_bytes

    def write(self, data):
        view = memoryview(data)  # what is left to write
        try:
            now = self._clock()
            if now - self._last_disk_check >= CHECK_DISK_INTERVAL_S:
                self.refresh_disk()
            if self.paused:
                self.dropped_bytes += len(data)
                self._close()
                return
            day = utc_day(now)
            if day != self._day or self._file is None:
                self._open(day)
            # The file is unbuffered, so every byte is written now or counted.
            while view:
                written = self._file.write(view)
                if not written:
                    raise OSError("log file accepted no data")
                self.bytes_today += written
                view = view[written:]
            self.write_error = self._close_error
        except (OSError, ValueError) as error:
            self.dropped_bytes += len(view)
            self.write_error = str(error)
            self._close()

    def _open(self, day):
        self._close()
        path = self._writable_path(day)  # never a file being compressed
        self._file = open(path, "ab", buffering=0)
        self._day, self._stem = day, path.stem
        self.bytes_today = path.stat().st_size

    def _close(self):
        file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
                self._close_error = ""
            except (OSError, ValueError) as error:
                self._close_error = f"closing {self._stem}{self.suffix}: {error}"
                self.write_error = self._close_error
                self._unclosed[self._stem] = file

    def close(self):
        self._close()

    def tidy(self):
        """Periodic work: refresh free space, confirm that files whose close
        failed are closed, and close a file whose UTC day has ended so it can
        be compressed."""
        self.refresh_disk()
        for day, file in list(self._unclosed.items()):
            if not file.closed:
                try:
                    file.close()
                except (OSError, ValueError):
                    pass
            if file.closed:
                del self._unclosed[day]
        if self._file is not None and self._day != utc_day(self._clock()):
            self._close()

    def pending_days(self):
        """Uncompressed files from finished UTC days that are due for a try.
        Today and any later day are skipped, in case the clock stepped back.
        A late file, `<day>-late-<n>`, sorts after its day and before the next."""
        today = utc_day(self._clock())
        now = self._clock()
        for name in list(self._compress_failed):
            if not (self.raw_dir / name).exists():
                del self._compress_failed[name]
        due = []
        for path in sorted(self.raw_dir.glob(f"*{self.suffix}")):
            if path.stem >= today or path.stem in self._unclosed:
                continue
            if path.stem == self._stem and self._file is not None:
                continue
            failed = self._compress_failed.get(path.name)
            if failed is not None and now - failed < COMPRESS_RETRY_S:
                continue
            due.append(path)
        return due

    def start_compress(self, path):
        """Mark a file as being compressed. Returns False, and marks nothing,
        if the file is open for writing or no longer a finished day, which can
        happen when the clock steps back between listing and compressing."""
        if path.stem >= utc_day(self._clock()):
            return False
        if path.stem == self._stem and self._file is not None:
            return False
        self._compressing.add(path.name)
        return True

    def end_compress(self, path):
        self._compressing.discard(path.name)

    def compress_failed(self, path, error):
        self._compress_failed[path.name] = self._clock()
        self.housekeeping_error = f"compress {path.name}: {error}"

    def compress_done(self, path):
        self._compress_failed.pop(path.name, None)

    def has_compress_failures(self):
        return bool(self._compress_failed)

    def _files(self):
        """(name, size) for each log file, skipping files that vanish meanwhile."""
        files = []
        for path in self.raw_dir.iterdir():
            try:
                if path.is_file() and not path.name.endswith(".partial"):
                    files.append((path.name, path.stat().st_size))
            except FileNotFoundError:
                continue
        return sorted(files, reverse=True)

    def stored_bytes(self):
        return sum(size for _, size in self._files())

    def record_usage(self):
        """Append one line to storage-usage.csv, at most once an hour."""
        now = self._clock()
        if self.paused or now - self._last_usage < USAGE_INTERVAL_S:
            return
        self._last_usage = now
        new_file = not self.usage_path.exists()
        with open(self.usage_path, "a", newline="") as stream:
            writer = csv.writer(stream)
            if new_file:
                writer.writerow(["utc", "bytes_today", "stored_bytes", "free_bytes",
                                 "dropped_bytes"])
            writer.writerow([
                datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                self.bytes_today, self.stored_bytes(), self.free_bytes, self.dropped_bytes,
            ])

    def snapshot(self):
        files = self._files()
        finished = [size for name, size in files[:14] if name.endswith(".gz")]
        per_day = sum(finished) / len(finished) if finished else None
        room = self.free_bytes - self.min_free_bytes
        return {
            "paused": self.paused,
            "last_error": "; ".join(
                e for e in (self.write_error, self.disk_error, self.housekeeping_error) if e),
            "bytes_today": self.bytes_today,
            "dropped_bytes": self.dropped_bytes,
            "stored_bytes": sum(size for _, size in files),
            "free_bytes": self.free_bytes,
            "min_free_bytes": self.min_free_bytes,
            "compressed_bytes_per_day": None if per_day is None else round(per_day),
            "days_until_limit": None if not per_day else round(max(room, 0) / per_day, 1),
            "files": [{"file": name, "bytes": size} for name, size in files[:14]],
        }
