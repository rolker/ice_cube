"""What the page can ask of the receiver: queries, confirmed changes, the profile
check and the guided base setup. Every write goes through `commands.prepare`, so the
block and confirm rules hold whichever way a command arrives."""
import asyncio
import logging
import math
import time

from . import commands
from .commands import CommandLog, CommandRunner, PendingStore, prepare
from .rtcm import ecef_to_llh, llh_to_ecef

LOG = logging.getLogger("gnss_base")
STARTUP_DELAY_S = 2.0
HISTORY_COUNT = 20
SURVEY_MAX_AGE_S = 5.0
SURVEY_DEFAULT = 300
# A position this far from the Earth's centre is on or near the ground (from below
# the Dead Sea shore to above the highest mountains).
MIN_RADIUS_M, MAX_RADIUS_M = 6_355_000.0, 6_388_000.0
BASE_MODE_WARNING = ("The receiver will restart. In base mode it stops sending NMEA, so the "
                     "fix, GGA position and GSV signals disappear; the page then uses RTCM "
                     "1005 and MSM messages instead. Survey-in or a fixed position is needed "
                     "before it sends a base position.")


class RequestError(ValueError):
    """A request the page made that cannot be carried out; the text says why."""


def _number(value, name):
    if isinstance(value, bool):
        raise RequestError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise RequestError(f"{name} must be a number") from None
    if not math.isfinite(result):
        raise RequestError(f"{name} must be a finite number")
    return result


def position_details(x, y, z):
    lat, lon, height = ecef_to_llh(x, y, z)
    return {"ecef": [round(x, 4), round(y, 4), round(z, 4)], "lat": round(lat, 9),
            "lon": round(lon, 9), "height_m": round(height, 4)}


class Controller:
    def __init__(self, write, log_path, profile_items, status=None, clock=time.monotonic):
        self.runner = CommandRunner(write, CommandLog(log_path))
        self.pending = PendingStore(clock=clock)
        self.profile = profile_items
        self.status = status
        self._clock = clock
        self.firmware = ""
        self.mode = None
        self.current_uart = None
        self.profile_results = []
        self.profile_checked = None
        self.check_note = ""
        self._startup = None
        self._checking = False   # the check has started sending
        self._check_again = False

    # ---- feeding from the stream and the port ----
    def on_nmea(self, payload):
        self.runner.on_nmea(payload)
        fields = payload.split(",")
        if fields[0] == "PQTMVER" and len(fields) > 3:
            # Sent first after every start: the receiver restarted, so check again.
            self.firmware = fields[3]
            self.runner.receiver_restarted()
            self.schedule_check()

    def port_opened(self):
        self.schedule_check()

    def port_lost(self):
        """The port closed: the app may be on another port when it reopens, so
        UART and protocol writes are blocked until `PQTMCFGUART,R` answers again."""
        self.current_uart = None
        self.runner.port_lost()

    def schedule_check(self, delay=STARTUP_DELAY_S):
        """Run the read-only startup check soon, once, however often this is called.
        If a check is already sending, another runs when it ends, so the port and
        receiver are read again after a reconnect or restart."""
        if self._startup is not None and not self._startup.done():
            self._check_again = self._check_again or self._checking
            return
        self._startup = asyncio.ensure_future(self._delayed_check(delay))

    async def _delayed_check(self, delay):
        again = True
        while again:
            await asyncio.sleep(delay)
            self._checking, self._check_again = True, False
            try:
                await self.startup_check()
            except Exception:  # never let the check take the app down
                LOG.exception("receiver check failed")
            finally:
                self._checking = False
            again = self._check_again

    async def stop(self):
        if self._startup is not None and not self._startup.done():
            self._startup.cancel()
            try:
                await self._startup
            except asyncio.CancelledError:
                pass

    # ---- read-only ----
    def _learn(self, prepared, result, connection):
        """Keep what a successful query says about the receiver. `connection` is the
        runner's connection count when the query was written (None if it was not); the
        port is learned only if it has not closed since."""
        if result["outcome"] != "ok" or not result["replies"]:
            return
        fields = result["replies"][0].lstrip("$").split(",")
        if prepared.name == "PQTMVERNO" and len(fields) > 1:
            self.firmware = fields[1]
        elif prepared.body == "PQTMCFGRCVRMODE,R" and fields[1:2] == ["OK"] and len(fields) > 2:
            self.mode = {"1": "Rover", "2": "Base station"}.get(fields[2], f"Unknown ({fields[2]})")
        elif prepared.body == "PQTMCFGUART,R" and fields[1:2] == ["OK"] and len(fields) == 8:
            if fields[2] in ("1", "2", "3") and connection == self.runner.connection:
                self.current_uart = fields[2]

    async def _queries(self, bodies, user, source):
        prepared = [prepare(body, self.current_uart) for body in bodies]
        for item in prepared:
            if item.kind != "query":  # the app's own checks are read-only
                raise RequestError(f"{item.body} is not a query")
        return await self._run(prepared, user, source, stop_on_failure=False)

    async def _run(self, prepared, user, source, check=None, **options):
        """Run a batch through the runner and learn from its replies. `check(item)`,
        if given, runs immediately before each write; a reason it returns stops the
        batch. The connection count is noted at each write, for `_learn`."""
        written = {}

        def recheck(item):
            reason = check(item) if check is not None else ""
            if not reason:
                written[id(item)] = self.runner.connection
            return reason

        results = await self.runner.run(prepared, user, source, recheck=recheck, **options)
        for item, result in zip(prepared, results):
            self._learn(item, result, written.get(id(item)))
        return results

    async def startup_check(self):
        source = "startup check"
        (first,) = await self._queries(["PQTMVERNO"], None, source)
        if first["outcome"] != "ok":
            self.check_note = "The receiver did not answer PQTMVERNO; the check was skipped."
            LOG.warning("receiver check: no answer to PQTMVERNO, check skipped")
            return
        await self._queries(["PQTMCFGUART,R"], None, source)
        await self.check_profile(None, source)
        differ = [r["label"] for r in self.profile_results if r["status"] != "match"]
        if differ:
            LOG.warning("receiver profile: %d of %d settings differ or could not be read: %s",
                        len(differ), len(self.profile_results), "; ".join(differ))
        else:
            LOG.info("receiver profile: all %d settings match", len(self.profile_results))

    async def check_profile(self, user, source="profile check"):
        # The profile's receiver mode query also updates `mode` (see _learn).
        results = await self._queries([item.query for item in self.profile], user, source)
        rows = []
        for item, result in zip(self.profile, results):
            row = {"label": item.label, "query": result["command"], "expected": item.expected,
                   "actual": None, "set": item.set, "note": item.note, "restart": item.restart,
                   "base_only": item.base_only}
            if result["outcome"] == "ok":
                fields = result["replies"][-1].lstrip("$").split(",")
                if fields[1:2] == ["OK"]:
                    row["status"], row["actual"] = item.compare(fields[2:])
                else:
                    row["status"], row["actual"] = "differ", ",".join(fields[1:])
            elif result["outcome"] == "error" and result.get("error_code") in ("1", "3"):
                row["status"] = "unsupported"
                row["actual"] = f"ERROR {result['error_code']}: {result['error']}"
            else:
                row["status"] = "error"
                row["actual"] = result["error"]
            rows.append(row)
        self.profile_results = rows
        self.profile_checked = self._clock()
        self.check_note = ""
        return rows

    # ---- page requests ----
    async def command(self, text, user):
        prepared = prepare(text, self.current_uart)
        if prepared.kind == "blocked":
            (result,) = await self.runner.run([prepared], user, "console")
            return {"outcome": "blocked", "reason": prepared.reason, "result": result}
        if prepared.kind == "query":
            (result,) = await self._run([prepared], user, "console")
            return {"outcome": result["outcome"], "result": result}
        return self.pending.add([prepared], "Send this command?",
                                warnings=self._warnings([prepared]), details={"source": "console"})

    def _warnings(self, prepared):
        names = {p.name for p in prepared}
        warnings = []
        if names & commands.NO_REPLY:
            warnings.append("The receiver restarts. Its output stops for a few seconds, and "
                            "the serial port may drop.")
        if "PQTMCLRMSG" in names:
            warnings.append("PQTMCLRMSG turns off message output; the page goes quiet until "
                            "messages are turned on again.")
        if "PQTMCFGRCVRMODE" in names:
            warnings.append(BASE_MODE_WARNING)
        return warnings

    def _stage(self, bodies, title, source, warnings=(), details=None):
        prepared = [prepare(body, self.current_uart) for body in bodies]
        for item in prepared:
            if item.kind == "blocked":
                raise RequestError(f"{item.shown if item.body else item.text}: {item.reason}")
        details = dict(details or {}, source=source)
        return self.pending.add(prepared, title, warnings=list(warnings) + self._warnings(prepared),
                                details=details)

    async def confirm(self, key, user):
        entry = self.pending.take(key)
        if entry is None:
            raise RequestError("That confirmation has expired or was already used. Start again.")
        # The rules are applied again to the whole batch, before anything is sent and
        # again before each command: the port the app is on may have become known,
        # unknown or different since the batch was staged.
        staged = entry["commands"]
        source = f"confirmed ({entry['details'].get('source', 'page')})"
        reason = self._changed(staged)
        if reason:
            self.runner.log_unsent(staged, user, source, reason)
            raise RequestError(reason)
        results = await self._run(staged, user, source, check=lambda item: self._changed(staged))
        return {"title": entry["title"], "results": results,
                "outcome": "ok" if all(r["outcome"] == "ok" for r in results) else "failed"}

    def _changed(self, staged):
        """Why a staged batch may no longer be sent, or "" if the rules still give
        every command the class it was staged with."""
        for item in staged:
            now = prepare(item.body, self.current_uart)
            if now.kind == "blocked" or (now.kind, now.reason) != (item.kind, item.reason):
                why = f": {now.reason}" if now.reason else ""
                return (f"Not sent: the rules for {now.shown} changed since it was staged "
                        f"(now {now.kind}{why}). Stage the change again.")
        return ""

    async def apply_profile(self, user):
        rows = await self.check_profile(user)
        bodies = [row["set"] for row in rows if row["status"] == "differ"]
        if not bodies:
            return {"nothing": True, "message": "No setting differs; nothing to apply.",
                    "profile": rows}
        restart = any(r["restart"] for r in rows if r["status"] == "differ")
        warnings = ["Restart the receiver afterwards (step 3 or PQTMSRR) so every setting "
                    "takes effect."] if restart else []
        return self._stage(bodies + ["PQTMSAVEPAR"], "Apply the profile differences?",
                           "profile apply", warnings)

    def step_base_mode(self):
        return self._stage(["PQTMCFGRCVRMODE,W,2", "PQTMSAVEPAR", "PQTMSRR"],
                           "Switch the receiver to base mode and restart it?", "step: base mode")

    def step_survey_in(self, count=SURVEY_DEFAULT):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 86400:
            raise RequestError("The survey-in count must be a whole number from 1 to 86400.")
        return self._stage([f"PQTMCFGSVIN,W,1,{count},0,0,0,0", "PQTMSAVEPAR"],
                           f"Start a survey-in of {count} fixes?", "step: survey-in",
                           ["The receiver must be in base mode. If PQTMSVINSTATUS does not show "
                            "the survey in progress within a few seconds, restart the receiver."],
                           {"count": count})

    def survey_mean(self):
        """The latest survey-in mean (x, y, z) and whether it is valid, or a RequestError."""
        survey = self.status.survey_mean() if self.status is not None else None
        if not survey:
            raise RequestError("No survey-in mean has been received (PQTMSVINSTATUS).")
        if survey["age_s"] > SURVEY_MAX_AGE_S:
            raise RequestError("The survey-in mean is stale: PQTMSVINSTATUS stopped arriving.")
        if survey["ecef"] is None:
            raise RequestError("PQTMSVINSTATUS has no mean position yet.")
        return survey

    def step_fixed(self, request):
        source = request.get("source")
        warnings = []
        if source == "survey":
            survey = self.survey_mean()
            x, y, z = survey["ecef"]
            if survey["state"] != "Valid":
                warnings.append(f"The survey-in is not finished (state: {survey['state']}). "
                                "Its mean may still move.")
        elif source == "ecef":
            x, y, z = (_number(request.get(k), k) for k in ("x", "y", "z"))
        elif source == "llh":
            lat, lon, height = (_number(request.get(k), k) for k in ("lat", "lon", "height"))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise RequestError("Latitude must be within ±90° and longitude within ±180°.")
            x, y, z = llh_to_ecef(lat, lon, height)
        else:
            raise RequestError("source must be survey, ecef or llh.")
        radius = math.sqrt(x * x + y * y + z * z)
        if not MIN_RADIUS_M <= radius <= MAX_RADIUS_M:
            raise RequestError("That position is not near the Earth's surface; check it.")
        x, y, z = (round(v, 4) for v in (x, y, z))
        details = dict(position_details(x, y, z), **{"from": source})
        warnings.append("Any error in the base position becomes an error in every rover "
                        "position.")
        return self._stage([f"PQTMCFGSVIN,W,2,0,0,{x:.4f},{y:.4f},{z:.4f}", "PQTMSAVEPAR"],
                           "Set this fixed base position?", "step: fixed position",
                           warnings, details)

    def history(self, count=HISTORY_COUNT):
        return self.runner.log.tail(count)

    def snapshot(self):
        checked = self.profile_checked
        counts = {}
        for row in self.profile_results:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {
            "firmware": self.firmware or None, "mode": self.mode,
            "current_uart": self.current_uart, "busy_with": self.runner.busy_with,
            "log_error": self.runner.log.error, "check_note": self.check_note,
            "profile": {"checked_age_s": None if checked is None
                        else round(self._clock() - checked, 1),
                        "counts": counts, "results": self.profile_results},
        }
