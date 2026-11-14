"""The reference checks' history for the page's plots: each check's fixed epochs as
30-second bins (the mean offset of the fixed epochs in each bin), with the residual
(offset minus the expected offset, so our antenna in our frame: 0, 0, 0 for a consistent
reference), and a running mean of the residual per UTC day.

Files, in the app's private folder (`<data_dir>/history/`, not the share: they are
working state, not logs):

- `reference-<slug>.jsonl`: one line per bin, `{"t": <bin start, Unix s>, "c": <fixed
  epochs>, "raw": [e, n, u], "res": [e, n, u] | null, "sid": <station ID> | null}`, in
  metres, the last KEEP_S (8 days); older lines are dropped when the file is rewritten
  at start and once a day (the rewrite keeps each line's station ID).
- `reference-<slug>-daily.json`: `{"YYYY-MM-DD": [count, sum_e, sum_n, sum_u]}` of the
  residuals, kept for every day (a few dozen bytes a day). A daily file that cannot be
  read is moved aside as `reference-<slug>-daily.json.corrupt-<UTC time>` before
  anything is written, and the error stays on the page until the next start (the
  history then starts the daily means afresh).

Both files are written to a temporary file, flushed to disk and renamed over the old
one, so a power cut leaves the old file or the new one. A failed write keeps what it
was to write: the lines go back in the queue, and the rewrite and the daily file are
done again at the next write.

The bins are offsets from our 1005 at the time: a bin keeps the residual computed then
(the expected offset for the frames and epoch of that moment), so a later change of an
entry's frame does not rewrite history. Bins without a residual (station frame unknown)
keep their raw offset and are not plotted.

`add` runs in the event loop; the file work is `take` (in the loop: what to write),
`write` (in an executor, so the loop never waits for the disk; True when written) and,
when it failed, `restore` (in the loop), see `flush`.
"""
import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("gnss_base")

BIN_S = 30
KEEP_S = 8 * 86400
REWRITE_S = 86400
MAX_POINTS = 1500          # per reference per response
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}


def _day(wall):
    return datetime.fromtimestamp(wall, timezone.utc).strftime("%Y-%m-%d")


def _line(item):
    """A bin as its line in the bins file (without the newline)."""
    start, count, raw, res, sid = item
    return json.dumps({"t": start, "c": count, "raw": [round(v, 4) for v in raw],
                       "res": None if res is None else [round(v, 4) for v in res],
                       "sid": sid})


def _replace(path, text):
    """Write `text` to a temporary file beside `path`, flush it to disk, rename it over
    `path`: a power cut leaves either file whole."""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ReferenceHistory:
    """One reference's bins and daily means. `expected()` returns the expected offset
    (E, N, U metres) now, or None; `station()` the station ID now, or None."""

    def __init__(self, directory, slug, expected=lambda: None, station=lambda: None,
                 wall=time.time):
        self.directory = Path(directory)
        self.path = self.directory / f"reference-{slug}.jsonl"
        self.daily_path = self.directory / f"reference-{slug}-daily.json"
        self.expected = expected
        self.station = station
        self._wall = wall
        self.bins = deque()        # (start, count, raw[3], res[3] | None, sid | None)
        self.daily = {}            # day -> [count, sum_e, sum_n, sum_u]
        self._current = None       # [start, count, se, sn, su]
        self._pending = []
        self._daily_dirty = False
        self._rewritten = None     # wall time of the last rewrite
        self.write_error = ""      # the last write's, cleared by a good write
        self.load_error = ""       # a damaged daily file at start, kept until next start
        self._daily_frozen = False # the damaged daily file could not be moved aside

    @property
    def error(self):
        """For the page and the log: the load error and the write error, or ""."""
        return "; ".join(e for e in (self.load_error, self.write_error) if e)

    # ---- loading ----

    def load(self):
        """Read the files (missing or damaged lines are skipped). Sync: call at start."""
        cutoff = self._wall() - KEEP_S
        try:
            with open(self.path) as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                        start, count, raw = float(item["t"]), int(item["c"]), item["raw"]
                        res = item.get("res")
                        if start < cutoff or count < 1 or len(raw) != 3:
                            continue
                        if self.bins and start <= self.bins[-1][0]:
                            continue     # a line written again after a failed append
                        if res is not None and len(res) != 3:
                            res = None
                        sid = item.get("sid")
                        if not isinstance(sid, int) or isinstance(sid, bool):
                            sid = None
                        self.bins.append((start, count, [float(v) for v in raw],
                                          None if res is None else [float(v) for v in res],
                                          sid))
                    except (ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        except OSError as error:
            self.load_error = f"reading {self.path.name}: {error}"
        try:
            text = self.daily_path.read_text()
        except FileNotFoundError:
            text = None
        except OSError as error:
            # Unreadable now (not damaged): never overwrite it in this run.
            self._daily_frozen = True
            self.load_error = (f"reading {self.daily_path.name}: {error}; the daily means "
                               "are not written until the next start")
            text = None
        if text is not None:
            try:
                data = json.loads(text)
                self.daily = {day: [int(v[0]), float(v[1]), float(v[2]), float(v[3])]
                              for day, v in data.items() if len(v) == 4 and int(v[0]) > 0}
            except (ValueError, TypeError, AttributeError, IndexError, KeyError) as error:
                self.daily = {}
                self._set_aside(error)
        self._rewritten = None     # the first write prunes the file
        return self

    def _set_aside(self, error):
        """The daily file is damaged: move it aside, so the new one does not replace
        the only copy of the past daily means; if that fails, write no daily file."""
        stamp = datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        aside = self.daily_path.with_name(f"{self.daily_path.name}.corrupt-{stamp}")
        number = 1
        while aside.exists():      # never replace a file moved aside before
            number += 1
            aside = self.daily_path.with_name(
                f"{self.daily_path.name}.corrupt-{stamp}-{number}")
        try:
            os.replace(self.daily_path, aside)
        except OSError as move_error:
            self._daily_frozen = True
            self.load_error = (f"{self.daily_path.name} is damaged ({error}) and could not "
                               f"be moved aside ({move_error}): the daily means are not "
                               "written until the next start")
            return
        self.load_error = (f"{self.daily_path.name} was damaged ({error}): moved to "
                           f"{aside.name}, the daily means start afresh")

    # ---- adding ----

    def add(self, wall, enu):
        """A fixed epoch's offset (solution minus our 1005, metres)."""
        if not all(math.isfinite(v) for v in enu):
            return
        start = math.floor(wall / BIN_S) * BIN_S
        current = self._current
        if current is not None and current[0] != start:
            self.close_bin()
            current = None
        if current is None:
            current = self._current = [start, 0, 0.0, 0.0, 0.0]
        current[1] += 1
        for axis in range(3):
            current[2 + axis] += enu[axis]

    def close_bin(self):
        """End the open bin: its mean, its residual against the expected offset now."""
        current, self._current = self._current, None
        if current is None or not current[1]:
            return
        start, count = current[0], current[1]
        raw = [v / count for v in current[2:]]
        try:
            expected = self.expected()
        except Exception:
            expected = None
        res = None if expected is None else [r - e for r, e in zip(raw, expected)]
        try:
            station = self.station()
        except Exception:
            station = None
        if not isinstance(station, int) or isinstance(station, bool):
            station = None
        item = (start, count, raw, res, station)
        self.bins.append(item)
        while self.bins and self.bins[0][0] < start - KEEP_S:
            self.bins.popleft()
        self._pending.append(_line(item))
        if res is not None:
            day = self.daily.setdefault(_day(start), [0, 0.0, 0.0, 0.0])
            day[0] += count
            for axis in range(3):
                day[1 + axis] += res[axis] * count
            self._daily_dirty = True

    # ---- files ----

    def take(self):
        """In the loop: what `write` should do now, as plain data, or None. A bin open
        for longer than BIN_S is closed first."""
        now = self._wall()
        if self._current is not None and now - self._current[0] >= BIN_S:
            self.close_bin()
        rewrite = self._rewritten is None or now - self._rewritten > REWRITE_S
        if not (self._pending or self._daily_dirty or rewrite):
            return None
        lines, self._pending = self._pending, []
        if rewrite:
            self._rewritten = now
            lines = [_line(b) for b in self.bins]
        daily = None
        if self._daily_dirty and not self._daily_frozen:
            daily = json.dumps({d: [v[0]] + [round(x, 4) for x in v[1:]]
                                for d, v in sorted(self.daily.items())})
        self._daily_dirty = False
        return {"lines": lines, "rewrite": rewrite, "daily": daily}

    def restore(self, job):
        """In the loop, after `write(job)` failed: what it was to write is to be written
        again. Appended lines go back before any newer ones; a failed rewrite is done
        again (from the bins, which hold its lines); the daily file is written again."""
        if job is None:
            return
        if job.get("lines_done"):
            pass                 # the bins file was written; only the daily file failed
        elif job["rewrite"]:
            self._rewritten = None
        else:
            self._pending[:0] = job["lines"]
        if job["daily"] is not None:
            self._daily_dirty = True

    def write(self, job):
        """In an executor: append (or rewrite) the bins file, replace the daily file.
        True when done (or nothing to do), False when it failed (see `restore`)."""
        if job is None:
            return True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            text = "".join(line + "\n" for line in job["lines"])
            if job["rewrite"]:
                _replace(self.path, text)
            elif text:
                with open(self.path, "a") as stream:
                    stream.write(text)
            job["lines_done"] = True     # for restore: not to be written again
            if job["daily"] is not None:
                _replace(self.daily_path, job["daily"])
            self.write_error = ""
            return True
        except OSError as error:
            self.write_error = f"writing the plot history: {error}"
            return False

    async def flush(self, loop):
        """Take, write in an executor, and restore on failure. True when written."""
        job = self.take()
        written = await loop.run_in_executor(None, self.write, job)
        if not written:
            self.restore(job)
        return written

    # ---- for the page ----

    def points(self, window_s, now=None, max_points=MAX_POINTS):
        """The residual bins of the last `window_s` as `[age_s, e, n, u, count]`
        (metres, oldest first), merged in runs of consecutive bins (weighted by their
        fixed epochs) down to at most `max_points`."""
        now = self._wall() if now is None else now
        chosen = [b for b in self.bins if b[3] is not None and now - b[0] <= window_s]
        group = max(1, math.ceil(len(chosen) / max_points))
        result = []
        for i in range(0, len(chosen), group):
            run = chosen[i:i + group]
            count = sum(b[1] for b in run)
            mean = [sum(b[3][a] * b[1] for b in run) / count for a in range(3)]
            start = sum(b[0] * b[1] for b in run) / count
            result.append([round(now - start - BIN_S / 2, 1)] + [round(v, 4) for v in mean]
                          + [count])
        return result

    def days(self):
        """The daily means of the residual, `[day, e, n, u, count]`, oldest first."""
        return [[day, *(round(v[1 + a] / v[0], 4) for a in range(3)), v[0]]
                for day, v in sorted(self.daily.items()) if v[0]]
