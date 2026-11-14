"""What the page can ask of the receiver: queries, confirmed changes, the profile
check and the guided base setup. Every write goes through `commands.prepare`, so the
block and confirm rules hold whichever way a command arrives."""
import asyncio
import logging
import math
import time

from . import commands
from .commands import CommandLog, CommandRunner, PendingStore, prepare
from .rtcm import ecef_to_enu, ecef_to_llh, llh_to_ecef
from .status import BASE_MOVED_M

LOG = logging.getLogger("gnss_base")
STARTUP_DELAY_S = 2.0
HISTORY_COUNT = 20
SURVEY_MAX_AGE_S = 5.0
SURVEY_DEFAULT = 300
# After a confirmed profile apply, the app checks the profile again after this long.
RECHECK_DELAY_S = 0.5
# A position this far from the Earth's centre is on or near the ground (from below
# the Dead Sea shore to above the highest mountains).
MIN_RADIUS_M, MAX_RADIUS_M = 6_355_000.0, 6_388_000.0
# The survey-in configuration (PQTMCFGSVIN,R) is read with the profile. Its <Mode>:
SVIN_MODES = {0: "disabled", 1: "survey-in", 2: "fixed"}
SVIN_QUERY = "PQTMCFGSVIN,R"
SVIN_UNREAD = "not read yet"
SVIN_WRITTEN = ("PQTMCFGSVIN was written and the receiver has not restarted since, so the "
                "configuration in force is not known until it restarts")
# A fixed position this far (3D) from the one the receiver now sends gets a warning
# to check the numbers: a wrong digit is the mistake to catch.
SHIFT_CHECK_M = 1.0
ALREADY_FIXED = ("The receiver is already in fixed mode (PQTMCFGSVIN mode 2), so the "
                 "survey-in mean it reports is the configured position repeated, not a "
                 "measurement. Enter latitude, longitude and height, or ECEF, instead.")
BASE_MODE_WARNING = ("The receiver will restart. In base mode it stops sending NMEA, so the "
                     "fix, GGA position and GSV signals disappear; the page then uses RTCM "
                     "1005 and MSM messages instead. Survey-in or a fixed position is needed "
                     "before it sends a base position.")
SVIN_RESTART_NOTE = ("The receiver restarts after saving: this firmware applies PQTMCFGSVIN "
                     "only after a restart. It checks the profile again once it is back.")


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


def near_earth(x, y, z):
    """Whether an ECEF position is on or near the ground."""
    return MIN_RADIUS_M <= math.sqrt(x * x + y * y + z * z) <= MAX_RADIUS_M


def metres(value):
    """A length in words: mm below 1 cm, cm below 1 m, else m to the millimetre."""
    size = abs(value)
    if size >= 1:
        text = f"{value:.3f} m"
    elif size >= 0.01:
        text = f"{value * 100:.1f} cm"
    else:
        text = f"{value * 1000:.1f} mm"
    return text[1:] if text.startswith("-0.0 mm") else text  # never "-0.0 mm"


def position_details(x, y, z):
    lat, lon, height = ecef_to_llh(x, y, z)
    return {"ecef": [round(x, 4), round(y, 4), round(z, 4)], "lat": round(lat, 9),
            "lon": round(lon, 9), "height_m": round(height, 4)}


def answered(result):
    """Whether the receiver answered: OK, or an ERROR reply (which says the receiver
    heard the query, though it may not support it)."""
    return result["outcome"] == "ok" or (result["outcome"] == "error"
                                         and bool(result.get("error_code")))


def uart_port(result):
    """The app's port (1 to 3) from a PQTMCFGUART,R result, or None if the result is
    not a well-formed OK reply."""
    if result["outcome"] != "ok" or not result["replies"]:
        return None
    fields = result["replies"][0].lstrip("$").split(",")
    if fields[1:2] == ["OK"] and len(fields) == 8 and fields[2] in ("1", "2", "3"):
        return fields[2]
    return None


def svin_config(result):
    """The survey-in configuration from a PQTMCFGSVIN,R result: `mode` 0 (disabled),
    1 (survey-in), 2 (fixed) or None if it could not be read, `ecef` the configured
    position when fixed (None if not understood), and `reason` why the mode is
    unknown. The specification (v1.3, 2.3.22) gives the reply as
    `$PQTMCFGSVIN,OK,<Mode>,<CFG_CNT>,<3D_AccLimit>,<ECEF_X>,<ECEF_Y>,<ECEF_Z>[,<Distance>]`."""
    config = {"mode": None, "ecef": None, "reason": ""}
    if result["outcome"] == "ok" and result["replies"]:
        reply = result["replies"][-1]
        fields = reply.lstrip("$").split(",")
        if fields[1:2] == ["OK"] and len(fields) >= 8 and fields[2] in ("0", "1", "2"):
            config["mode"] = int(fields[2])
            if config["mode"] == 2:
                try:
                    ecef = tuple(float(v) for v in fields[5:8])
                except ValueError:
                    ecef = None
                if ecef is not None and all(math.isfinite(v) for v in ecef) \
                        and near_earth(*ecef):
                    config["ecef"] = ecef
            return config
        config["reason"] = f"{SVIN_QUERY} answered {reply}, which is not understood"
    elif result["outcome"] == "error" and result.get("error_code"):
        config["reason"] = f"{SVIN_QUERY} answered ERROR,{result['error_code']}"
    elif result["outcome"] == "timeout":
        config["reason"] = f"{SVIN_QUERY} got no answer"
    else:  # the port closed, or the app stopped (a query not sent never gets here)
        config["reason"] = f"{SVIN_QUERY} ended {result['outcome']}: {result['error']}"
    return config


def shift_warning(shift, unknown):
    """The shift from the current base position in words: `shift` as `shift_details`
    gives it (unrounded), or None with `unknown` saying why."""
    if shift is None:
        return (f"The shift from the current base position could not be checked "
                f"({unknown}). Check every digit before sending.")
    text = (f"The new position is {metres(shift['total_m'])} from the base position the "
            f"receiver now sends in RTCM 1005: east {metres(shift['east_m'])}, north "
            f"{metres(shift['north_m'])}, up {metres(shift['up_m'])}.")
    if shift["total_m"] > SHIFT_CHECK_M:
        text += (f" That is more than {SHIFT_CHECK_M:g} m: check every digit before "
                 "sending. A wrong digit here moves every rover position by the same amount.")
    return text


def survey_unfinished(survey):
    """Why an unfinished survey-in mean is not used for a fixed position."""
    def shown(value, unit=""):
        return "—" if value is None else f"{value}{unit}"
    return (f"The survey-in is not finished (state: {survey['state']}, "
            f"Valid={shown(survey['valid'])}, Obs {shown(survey.get('observations'))} of "
            f"CfgDur {shown(survey.get('target'))}, mean accuracy "
            f"{shown(survey.get('accuracy_m'), ' m')}). Its mean may still move, so it is not "
            "used. Wait until it is finished (Valid=2), or enter coordinates.")


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
        # From PQTMCFGSVIN,R at the last check (see svin_config). This firmware applies
        # a PQTMCFGSVIN write only after a restart, and every restart starts a check,
        # so what was read then is the configuration in force.
        self.svin = {"mode": None, "ecef": None, "reason": SVIN_UNREAD}
        self.svin_read = None
        # A PQTMCFGSVIN write was answered OK and the receiver has not restarted since:
        # PQTMCFGSVIN,R may then answer with the written values while the receiver still
        # runs the old ones, so the configuration in force is unknown until a restart.
        self._svin_written = False
        self.restarts = 0            # PQTMVER sentences seen
        # When the port last closed or the receiver last restarted (clock time): a
        # base position or survey-in status received before then is not what the
        # receiver sends now.
        self._cutoff = None
        self.profile_results = []
        self.profile_checked = None
        self.check_note = ""
        # Why the profile table is not current, and when that was found: None while it is.
        self.profile_stale = None
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
            self.restarts += 1
            self._cutoff = self._clock()
            self._svin_written = False
            self.runner.receiver_restarted()
            self.forget_svin("the receiver restarted and it has not been read again")
            self.schedule_check()

    def port_opened(self):
        self.schedule_check()

    def port_lost(self):
        """The port closed: the app may be on another port when it reopens, so
        UART and protocol writes are blocked until `PQTMCFGUART,R` answers again."""
        self.current_uart = None
        self._cutoff = self._clock()
        self.forget_svin("the port closed and it has not been read again")
        self.runner.port_lost()

    def forget_svin(self, reason):
        """The survey-in configuration read earlier may no longer be in force (a restart
        applies pending writes; a port loss may hide one): unknown until read again."""
        self.svin = {"mode": None, "ecef": None, "reason": reason}
        self.svin_read = None

    def schedule_check(self, delay=None):
        """Run the read-only startup check soon (after STARTUP_DELAY_S by default),
        once, however often this is called. If a check is already sending, another
        runs when it ends, so the port and receiver are read again after a reconnect
        or restart."""
        delay = STARTUP_DELAY_S if delay is None else delay
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
            except Exception as error:  # never let the check take the app down
                LOG.exception("receiver check failed")
                self._mark_stale(f"the check failed ({type(error).__name__})")
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
    def _learn(self, prepared, result, written):
        """Keep what a successful query says about the receiver. `written` is the
        runner's connection count and the restart count when the query was written
        (None if it was not); the port is learned only if it has not closed since, and
        the survey-in configuration only if the receiver has not restarted either (a
        restart applies a pending write, so the answer may be obsolete; `forget_svin`
        has run and the check after the restart reads it again). A survey-in query
        that was not sent leaves what was read before. Any other answer to
        PQTMCFGUART,R (an ERROR, a malformed OK, no reply) leaves the port unknown, so
        UART and protocol writes for it are refused until it is read again."""
        connection = written[0] if written is not None else None
        if prepared.body == "PQTMCFGUART,R":
            port = uart_port(result)
            self.current_uart = port if connection == self.runner.connection else None
            return
        if prepared.body == SVIN_QUERY:
            if written == (self.runner.connection, self.restarts):
                self.svin = svin_config(result)
                self.svin_read = self._clock()
                if self._svin_written:
                    self.forget_svin(SVIN_WRITTEN)
            return
        if prepared.body.startswith("PQTMCFGSVIN,W"):
            # A write the receiver may hold: anything written counts, whatever came back
            # (no reply, or the port closed before one), except an ERROR reply, which
            # rejected it. Not if the receiver has restarted since: the restart applied
            # it, and the check after it reads it again. A port loss alone applies nothing.
            if (written is not None and written[1] == self.restarts
                    and not result.get("error_code")):
                self._svin_written = True
                self.forget_svin(SVIN_WRITTEN)
            return
        if result["outcome"] != "ok" or not result["replies"]:
            return
        fields = result["replies"][0].lstrip("$").split(",")
        if prepared.name == "PQTMVERNO" and len(fields) > 1:
            self.firmware = fields[1]
        elif prepared.body == "PQTMCFGRCVRMODE,R" and fields[1:2] == ["OK"] and len(fields) > 2:
            self.mode = {"1": "Rover", "2": "Base station"}.get(fields[2], f"Unknown ({fields[2]})")

    async def _queries(self, bodies, user, source, send=None, stop_on=()):
        prepared = [prepare(body, self.current_uart) for body in bodies]
        for item in prepared:
            if item.kind != "query":  # the app's own checks are read-only
                raise RequestError(f"{item.body} is not a query")
        return await self._run(prepared, user, source, send=send, stop_on_failure=False,
                               stop_on=stop_on)

    async def _run(self, prepared, user, source, check=None, send=None, **options):
        """Run a batch through the runner and learn from its replies. `check(item)`,
        if given, runs immediately before each write; a reason it returns stops the
        batch. `send` is the runner's `hold` function while the port is held, else
        the batch waits for the port. The connection and restart counts are noted at
        each write, for `_learn`."""
        written = {}

        def recheck(item):
            reason = check(item) if check is not None else ""
            if not reason:
                written[id(item)] = (self.runner.connection, self.restarts)
            return reason

        send = send or self.runner.run
        try:
            results = await send(prepared, user, source, recheck=recheck, **options)
        except asyncio.CancelledError:
            # The app is stopping. A PQTMCFGSVIN write that reached the port still
            # counts (it holds until a restart, as _learn would have noted).
            for item in prepared:
                if id(item) in written and item.body.startswith("PQTMCFGSVIN,W"):
                    self._learn(item, {"outcome": "cancelled", "replies": [], "error": ""},
                                written[id(item)])
            raise
        for item, result in zip(prepared, results):
            self._learn(item, result, written.get(id(item)))
        return results

    def _mark_stale(self, reason, kind="failed"):
        """The profile table is not current: a check failed before reading the profile
        (`kind` "failed": the table is from an earlier check, if any), or read it only
        in part or without a verified port ("incomplete": the table is from this
        check). `time` is wall-clock time, for the page to show."""
        self.profile_stale = {"reason": reason, "kind": kind, "at": self._clock(),
                              "time": time.time()}

    async def startup_check(self):
        """Read the firmware, the app's port and the profile, holding the port for the
        whole check so that no confirmed change (a restart, say) lands in between. A
        request made meanwhile waits for the check: a few dozen ms per query, or at
        most one timeout per step if the receiver goes quiet."""
        source = "startup check"
        async with self.runner.hold() as send:
            connection = self.runner.connection
            (first,) = await self._queries(["PQTMVERNO"], None, source, send)
            if first["outcome"] != "ok":
                self.check_note = "The receiver did not answer PQTMVERNO; the check was skipped."
                self._mark_stale("no answer to PQTMVERNO")
                LOG.warning("receiver check: no answer to PQTMVERNO, check skipped")
                return
            (uart,) = await self._queries(["PQTMCFGUART,R"], None, source, send)
            if not answered(uart):
                self.check_note = ("The receiver did not answer PQTMCFGUART,R; the profile "
                                   "check was skipped.")
                self._mark_stale("no answer to PQTMCFGUART,R")
                LOG.warning("receiver check: no answer to PQTMCFGUART,R, profile skipped")
                return
            # The profile is still read when the receiver answers ERROR (the firmware
            # may not support the query), but the check is then incomplete: without a
            # verified port, UART and protocol writes for it stay refused (see _learn).
            unverified = ""
            if self.current_uart is None:
                if uart["outcome"] == "error":
                    unverified = (f"PQTMCFGUART,R answered ERROR,{uart['error_code']}, so "
                                  "the app's UART is unverified")
                elif connection != self.runner.connection:
                    unverified = "the port closed during the check"
                else:
                    unverified = (f"PQTMCFGUART,R answered {uart['replies'][0]}, which is "
                                  "not understood, so the app's UART is unverified")
            await self.check_profile(None, source, send, unverified, connection)
        differ = [r["label"] for r in self.profile_results if r["status"] != "match"]
        if self.profile_stale is not None:
            LOG.warning("receiver check incomplete: %s", self.profile_stale["reason"])
        if differ:
            LOG.warning("receiver profile: %d of %d settings differ or could not be read: %s",
                        len(differ), len(self.profile_results), "; ".join(differ))
        else:
            LOG.info("receiver profile: all %d settings match", len(self.profile_results))
        if self.svin["mode"] is None:
            LOG.warning("receiver survey-in configuration unknown: %s", self.svin["reason"])
        else:
            LOG.info("receiver survey-in configuration: mode %d (%s)", self.svin["mode"],
                     SVIN_MODES[self.svin["mode"]])

    async def check_profile(self, user, source="profile check", send=None, incomplete="",
                            connection=None):
        """Read the profile and keep the rows. They are a complete check only if every
        query was answered (OK, or ERROR from the receiver), the port stayed open since
        `connection` (the connection count when the check began) and the caller gave
        no `incomplete` reason; otherwise they are kept, but marked stale."""
        if connection is None:
            connection = self.runner.connection
        # The profile's receiver mode query also updates `mode`, and the survey-in
        # configuration query at the end updates `svin` (see _learn). A query that
        # times out stops the check: the receiver has likely gone quiet, and each
        # further query would hold the port for another timeout.
        results = await self._queries([item.query for item in self.profile] + [SVIN_QUERY],
                                      user, source, send, stop_on=("timeout",))
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
        # The survey-in query is not part of the profile: unanswered, it leaves `svin`
        # unknown with the reason, and the table is as complete as its own rows.
        results = results[:len(self.profile)]
        missing = next((r for r in results if not answered(r)), None)
        if incomplete:
            reason, note = incomplete, f"The check is incomplete: {incomplete}."
        elif connection != self.runner.connection:
            reason = "the port closed during the check"
            note = "The serial port closed during the check, so the table may not be current."
        elif missing is not None and missing["outcome"] == "timeout":
            reason = f"{missing['command']} got no answer"
            rest = results[-1]["outcome"] == "not sent"
            note = reason + (", so the rest of the profile was not read." if rest else ".")
        elif missing is not None:
            reason = f"{missing['command']} ended {missing['outcome']}: {missing['error']}"
            note = f"The check is incomplete: {reason}."
        else:
            reason = note = ""
        self.check_note = note
        if reason:
            self._mark_stale(reason, "incomplete")
        else:
            self.profile_stale = None
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

        def changed(item=None):
            # Before the batch and again before its first write (a check that ended
            # since the step was staged, or while this confirmation waited for the
            # port, may have found fixed mode, another base position or another mean):
            # the receiver state the position was staged against must still hold. Once
            # the position is written, the save and restart that follow are not held
            # back by a change of state; the rules (_changed) apply to every write.
            if item is None or item is staged[0]:
                if entry["details"].get("from") == "survey" and self.svin["mode"] == 2:
                    return f"Not sent: {ALREADY_FIXED}"
                reason = (self._survey_outdated(entry["details"])
                          or self._shift_outdated(entry["details"].get("current_base")))
                if reason:
                    return reason
            return self._changed(staged)

        reason = changed()
        if reason:
            self.runner.log_unsent(staged, user, source, reason)
            raise RequestError(reason)
        results = await self._run(staged, user, source, check=changed)
        reply = {"title": entry["title"], "results": results,
                 "outcome": "ok" if all(r["outcome"] == "ok" for r in results) else "failed"}
        if entry["details"].get("source") == "profile apply":
            # Whatever the outcome, read the profile again so the page shows what the
            # receiver now has. A restart is not needed for this: its own PQTMVER
            # starts a check (on_nmea), and the apply batch has no restart.
            self.schedule_check(RECHECK_DELAY_S)
            reply["rechecking"] = True
        return reply

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
        return self._stage([f"PQTMCFGSVIN,W,1,{count},0,0,0,0", "PQTMSAVEPAR", "PQTMSRR"],
                           f"Start a survey-in of {count} fixes and restart the receiver?",
                           "step: survey-in",
                           ["The receiver must be in base mode.", SVIN_RESTART_NOTE],
                           {"count": count})

    def survey_mean(self):
        """The latest survey-in mean (x, y, z) and its state, or a RequestError."""
        survey = self.status.survey_mean() if self.status is not None else None
        if not survey:
            raise RequestError("No survey-in mean has been received (PQTMSVINSTATUS).")
        if survey["age_s"] > SURVEY_MAX_AGE_S:
            raise RequestError("The survey-in mean is stale: PQTMSVINSTATUS stopped arriving.")
        if self._cutoff is not None and survey["at"] <= self._cutoff:
            raise RequestError("No PQTMSVINSTATUS has arrived since the port reopened or the "
                               "receiver restarted; the last mean may be from before.")
        if survey["ecef"] is None:
            raise RequestError("PQTMSVINSTATUS has no mean position yet.")
        return survey

    def _current_base(self):
        """(the base position the receiver now sends in RTCM 1005, or None, why not):
        the latest 1005 if the receiver is connected, it arrived since the port last
        opened and the receiver last restarted, and it is still fresh."""
        current = self.status.current_base() if self.status is not None else None
        if current is None:
            return None, "no RTCM 1005 received"
        if not current["connected"]:
            return None, "the receiver is not connected, so what it sends now is not known"
        if self._cutoff is not None and current["at"] <= self._cutoff:
            return None, "no RTCM 1005 received since the port reopened or the receiver restarted"
        if current["stale"]:
            return None, f"the last RTCM 1005 is stale ({current['age_s']:.0f} s old)"
        return current, ""

    def _survey_outdated(self, details):
        """Why a batch staged from the survey-in mean may no longer be sent: the mean
        is no longer usable (stale, from before a restart, unfinished) or it moved."""
        if details.get("from") != "survey":
            return ""
        if self.svin["mode"] != details.get("svin_mode"):
            # The configuration it was staged under was read again, lost (a restart or
            # a port loss, which may hide a restart into fixed mode) or found since.
            known = ("" if self.svin["mode"] is not None
                     else f" ({self.svin['reason']})")
            return ("Not sent: the survey-in configuration is no longer what this was "
                    f"staged under{known}. Stage the change again.")
        try:
            survey = self.survey_mean()
        except RequestError as error:
            return f"Not sent: {error} Stage the change again."
        if survey["valid"] != 2:
            return f"Not sent: {survey_unfinished(survey)} Stage the change again."
        if math.dist(survey["ecef"], details["ecef"]) > BASE_MOVED_M:
            return ("Not sent: the survey-in mean changed since this was staged. Stage the "
                    "change again.")
        return ""

    def _shift_outdated(self, shown):
        """Why the shift a confirmation showed, against the base position `shown` (None
        if none was known when it was staged), may no longer hold: the receiver now
        sends another position, or what it sends is no longer known."""
        if shown is None:
            return ""
        current, why = self._current_base()
        if current is None:
            return (f"Not sent: the base position the receiver sends is no longer known "
                    f"({why}), so the shift shown may be out of date. Stage the change again.")
        if math.dist(current["ecef"], shown) > BASE_MOVED_M:
            return ("Not sent: the base position the receiver sends (RTCM 1005) changed "
                    "since this was staged, so the shift shown was out of date. Stage the "
                    "change again.")
        return ""

    def shift_details(self, new):
        """The base position the receiver now sends in RTCM 1005 and the shift from it
        to `new` (ECEF): east, north, up, horizontal and 3D, in metres. Returns the
        details for the page (rounded) and the unrounded shift, which is None, with
        `shift_unknown` saying why, without a usable 1005 (see _current_base)."""
        current, why = self._current_base()
        if current is not None:
            east, north, up = ecef_to_enu(current["ecef"], new)
            shift = {"east_m": east, "north_m": north, "up_m": up,
                     "horizontal_m": math.hypot(east, north),
                     "total_m": math.dist(current["ecef"], new)}
            return {"current_base": [round(v, 4) for v in current["ecef"]],
                    "shift": {k: round(v, 4) for k, v in shift.items()},
                    "shift_unknown": ""}, shift
        return {"current_base": None, "shift": None, "shift_unknown": why}, None

    def step_fixed(self, request):
        source = request.get("source")
        warnings = []
        if source == "survey":
            if self.svin["mode"] == 2:
                raise RequestError(ALREADY_FIXED)
            survey = self.survey_mean()
            if survey["valid"] != 2:
                raise RequestError(survey_unfinished(survey))
            x, y, z = survey["ecef"]
            if self.svin["mode"] is None:
                warnings.append("The survey-in configuration is not known "
                                f"({self.svin['reason']}), so the app cannot tell whether "
                                "this mean is a measurement or the receiver's configured "
                                "fixed position repeated. If the receiver is already in "
                                "fixed mode, this re-sends that position.")
        elif source == "ecef":
            x, y, z = (_number(request.get(k), k) for k in ("x", "y", "z"))
        elif source == "llh":
            lat, lon, height = (_number(request.get(k), k) for k in ("lat", "lon", "height"))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise RequestError("Latitude must be within ±90° and longitude within ±180°.")
            x, y, z = llh_to_ecef(lat, lon, height)
        else:
            raise RequestError("source must be survey, ecef or llh.")
        if not near_earth(x, y, z):
            raise RequestError("That position is not near the Earth's surface; check it.")
        x, y, z = (round(v, 4) for v in (x, y, z))
        details = dict(position_details(x, y, z), **{"from": source})
        if source == "survey":
            details["svin_mode"] = self.svin["mode"]  # the evidence this rests on
        shift_details, shift = self.shift_details((x, y, z))
        details.update(shift_details)
        warnings.append(shift_warning(shift, shift_details["shift_unknown"]))
        warnings.append("Any error in the base position becomes an error in every rover "
                        "position.")
        warnings.append(SVIN_RESTART_NOTE)
        return self._stage([f"PQTMCFGSVIN,W,2,0,0,{x:.4f},{y:.4f},{z:.4f}", "PQTMSAVEPAR",
                            "PQTMSRR"],
                           "Set this fixed base position and restart the receiver?",
                           "step: fixed position", warnings, details)

    def history(self, count=HISTORY_COUNT):
        return self.runner.log.tail(count)

    def snapshot(self):
        checked, stale = self.profile_checked, self.profile_stale
        counts = {}
        for row in self.profile_results:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        svin = dict(self.svin, mode_name=SVIN_MODES.get(self.svin["mode"]),
                    read_age_s=None if self.svin_read is None
                    else round(self._clock() - self.svin_read, 1))
        if svin["ecef"] is not None:
            svin.update(position_details(*svin["ecef"]))
        else:
            svin.update(lat=None, lon=None, height_m=None)
        return {
            "firmware": self.firmware or None, "mode": self.mode, "svin": svin,
            "current_uart": self.current_uart, "busy_with": self.runner.busy_with,
            "checking": self._startup is not None and not self._startup.done(),
            "log_error": self.runner.log.error, "check_note": self.check_note,
            "profile": {"checked_age_s": None if checked is None
                        else round(self._clock() - checked, 1),
                        "counts": counts, "results": self.profile_results,
                        "stale": None if stale is None else {
                            "reason": stale["reason"], "kind": stale["kind"],
                            "time": stale["time"],
                            "age_s": round(self._clock() - stale["at"], 1)}},
        }
