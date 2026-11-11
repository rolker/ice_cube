"""The reference checks' history for the page's plots: each check's fixed epochs as
30-second bins (the mean offset of the fixed epochs in each bin), with the residual
(offset minus the expected offset, so our antenna in our frame: 0, 0, 0 for a consistent
reference), and a running mean of the residual per UTC day.

Files, in the app's private folder (`<data_dir>/history/`, not the share: they are
working state, not logs):

- `reference-<slug>.jsonl`: one line per bin, `{"t": <bin start, Unix s>, "c": <fixed
  epochs>, "raw": [e, n, u], "res": [e, n, u] | null, "sid": <station ID> | null}`, in
  metres, the last KEEP_S (8 days); older lines are dropped when the file is rewritten
  at start and once a day.
- `reference-<slug>-daily.json`: `{"YYYY-MM-DD": [count, sum_e, sum_n, sum_u]}` of the
  residuals, kept for every day (a few dozen bytes a day).

The bins are offsets from our 1005 at the time: a bin keeps the residual computed then
(the expected offset for the frames and epoch of that moment), so a later change of an
entry's frame does not rewrite history. Bins without a residual (station frame unknown)
keep their raw offset and are not plotted.

`add` runs in the event loop; the file work is `take` (in the loop: what to write) and
`write` (in an executor), so the loop never waits for the disk.
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
        self.bins = deque()        # (start, count, raw[3], res[3] | None)
        self.daily = {}            # day -> [count, sum_e, sum_n, sum_u]
        self._current = None       # [start, count, se, sn, su]
        self._pending = []
        self._daily_dirty = False
        self._rewritten = None     # wall time of the last rewrite
        self.error = ""

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
                        if res is not None and len(res) != 3:
                            res = None
                        self.bins.append((start, count, [float(v) for v in raw],
                                          None if res is None else [float(v) for v in res]))
                    except (ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        except OSError as error:
            self.error = f"reading {self.path.name}: {error}"
        try:
            data = json.loads(self.daily_path.read_text())
            self.daily = {day: [int(v[0]), float(v[1]), float(v[2]), float(v[3])]
                          for day, v in data.items() if len(v) == 4 and int(v[0]) > 0}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, IndexError) as error:
            self.error = f"reading {self.daily_path.name}: {error}"
        self._rewritten = None     # the first write prunes the file
        return self

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
        self.bins.append((start, count, raw, res))
        while self.bins and self.bins[0][0] < start - KEEP_S:
            self.bins.popleft()
        try:
            station = self.station()
        except Exception:
            station = None
        self._pending.append(json.dumps({
            "t": start, "c": count, "raw": [round(v, 4) for v in raw],
            "res": None if res is None else [round(v, 4) for v in res], "sid": station}))
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
            lines = [json.dumps({"t": b[0], "c": b[1], "raw": [round(v, 4) for v in b[2]],
                                 "res": None if b[3] is None else [round(v, 4) for v in b[3]]})
                     for b in self.bins]
        daily = None
        if self._daily_dirty:
            self._daily_dirty = False
            daily = json.dumps({d: [v[0]] + [round(x, 4) for x in v[1:]]
                                for d, v in sorted(self.daily.items())})
        return {"lines": lines, "rewrite": rewrite, "daily": daily}

    def write(self, job):
        """In an executor: append (or rewrite) the bins file, replace the daily file."""
        if job is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            text = "".join(line + "\n" for line in job["lines"])
            if job["rewrite"]:
                temporary = self.path.with_suffix(".jsonl.tmp")
                temporary.write_text(text)
                os.replace(temporary, self.path)
            elif text:
                with open(self.path, "a") as stream:
                    stream.write(text)
            if job["daily"] is not None:
                temporary = self.daily_path.with_suffix(".json.tmp")
                temporary.write_text(job["daily"])
                os.replace(temporary, self.daily_path)
            self.error = ""
        except OSError as error:
            self.error = f"writing the plot history: {error}"

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
