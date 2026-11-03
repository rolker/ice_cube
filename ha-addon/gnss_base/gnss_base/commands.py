"""Send commands to the receiver: safety rules, one command at a time, and a log.

Every command is first normalised and then classified, and the exact bytes that
were classified are the bytes that are sent:

- "blocked": never sent (factory reset, firmware or bootloader commands, and any
  PQTMCFGUART or PQTMCFGPROT write that could touch UART1 or the current port);
- "query": read-only, sent at once (`,R` forms of the documented Set/Get commands,
  and PQTMVERNO, PQTMUNIQID, PQTMSN and PQTMLSTMSG);
- "confirm": anything else, including unknown commands. These are sent only after
  a second, explicit request that names a pending id (see `PendingStore`).

Command syntax, replies and error codes follow the Quectel LG29xP & LGx80P GNSS
protocol specification, version 1.3.
"""
import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .frames import nmea_checksum

# Table 6 of the specification
ERROR_TEXT = {"1": "Invalid parameters", "2": "Failed execution", "3": "Unsupported command"}

# Documented Set/Get commands. Their `,R` forms are queries.
SET_GET = frozenset("PQTMCFG" + name for name in (
    "ANTDELTA ANTENNA ANTINF BLD CNRTHD CNST ELETHD EVENT FIXRATE GEOFENCE GEOSEP MSGRATE "
    "NAVMODE NMEADP NMEATID ODO PINALT PPP PPS PPS2 PROT RCVRMODE RSID RTCM RTK RTKRL "
    "RTKSRCTYPE SAT SBAS SIGGRP SIGNAL SIGNAL2 STANDALONE SVIN UART WN").split())
# Read-only commands without an R field, and how many arguments each may take.
QUERY_ONLY = {"PQTMVERNO": (0,), "PQTMUNIQID": (0,), "PQTMSN": (0,), "PQTMLSTMSG": (0, 2)}
# Commands the specification gives no reply for: the receiver restarts.
NO_REPLY = frozenset({"PQTMSRR", "PQTMCOLD", "PQTMWARM", "PQTMHOT"})
# PQTMLSTMSG answers with several lines, ending with this one.
MULTI_LINE_END = {"PQTMLSTMSG": ["OK", "End"]}

FACTORY_RESET = "PQTMRESTOREPAR"
# The specification lists no firmware or bootloader commands; refuse anything that
# looks like one, in case a firmware version has undocumented ones.
FIRMWARE_WORDS = ("BOOT", "FIRMWARE", "FWU", "FWUP", "UPGRADE", "UPDATE", "FLASH",
                  "DOWNLOAD", "ERASE", "BURN", "OTA", "DFU")
UART_WRITE_BLOCKED = ("Changing UART settings is blocked for UART1 and for the port this app "
                      "uses, because a wrong setting would cut the app off from the receiver.")
PROT_WRITE_BLOCKED = ("Changing the protocols of UART1 or of the port this app uses is blocked, "
                      "because a wrong setting would cut the app off from the receiver.")
PROTECTED_UART = "1"
OTHER_UARTS = ("2", "3")
BAUD_RATES = frozenset({"9600", "115200", "230400", "460800", "921600"})

MAX_LENGTH = 160
_BODY = re.compile(r"[A-Z0-9][A-Z0-9,.+_-]*")
_NAME = re.compile(r"[A-Z0-9]+")
_WHITESPACE = re.compile(r"\s+")

TIMEOUT_S = 3.0
SAVE_TIMEOUT_S = 10.0
LIST_TIMEOUT_S = 5.0
WRITE_TIMEOUT_S = 5.0
# After a timeout, how long the next command of that name waits for a late reply.
LATE_GRACE_S = 3.0
# How long a command that timed out is still owed a reply.
OWED_S = 60.0
STOPPED = "Not sent: the app stopped first."
PENDING_TTL_S = 60.0
MAX_PENDING = 50
MAX_QUEUED = 20
HISTORY_BYTES = 256 * 1024


@dataclass
class Prepared:
    """A command ready to send, or the reason it may not be."""
    text: str           # what was asked for
    body: str = ""      # normalised: no $, no checksum, no line end
    kind: str = "blocked"
    reason: str = ""

    @property
    def name(self):
        return self.body.split(",", 1)[0]

    @property
    def sentence(self):
        """The exact bytes sent: $, body, *, checksum, CR LF."""
        return f"${self.body}*{nmea_checksum(self.body.encode('ascii')):02X}\r\n".encode("ascii")

    @property
    def shown(self):
        return self.sentence.decode("ascii").rstrip()

    @property
    def timeout(self):
        if self.name == "PQTMSAVEPAR":
            return SAVE_TIMEOUT_S
        return LIST_TIMEOUT_S if self.name in MULTI_LINE_END else TIMEOUT_S


def checksum(body):
    """The NMEA checksum of a body (text between $ and *) as two hex digits."""
    return f"{nmea_checksum(body.encode('ascii')):02X}"


def normalise(text):
    """(body, problem). The body is upper case, without white space, a leading $,
    a checksum or a line end. A checksum that was given must match what was typed."""
    if not isinstance(text, str):
        return "", "The command must be text."
    if not text.isascii():
        return "", "Only plain ASCII commands can be sent."
    body = text.strip()
    if "\r" in body or "\n" in body:
        return "", "Send one command at a time, on one line."
    if body.startswith("$"):
        body = body[1:]
    if "*" in body:
        body, _, given = body.rpartition("*")
        given = given.strip()
        if not re.fullmatch(r"[0-9A-Fa-f]{2}", given):
            return "", "The checksum after * must be two hex digits; or leave it off."
        if int(given, 16) != nmea_checksum(body.strip().encode("ascii")):
            return "", ("The checksum does not match the command. Leave it off and the app "
                        "adds the right one.")
    body = _WHITESPACE.sub("", body).upper()
    if not body:
        return "", "The command is empty."
    if len(body) > MAX_LENGTH:
        return "", f"The command is longer than {MAX_LENGTH} characters."
    if not _BODY.fullmatch(body) or not _NAME.fullmatch(body.split(",", 1)[0]):
        return "", ("The command may contain only letters, digits and , . + - _ "
                    "after an optional $, and an optional checksum.")
    return body, ""


def _uart_rule(fields, current):
    """(kind, reason) for PQTMCFGUART."""
    args = fields[1:]
    if args == ["R"] or (len(args) == 2 and args[0] == "R" and args[1] in ("1", "2", "3")):
        return "query", ""
    # Only the indexed form names a port. The form without an index sets the
    # current port, so it is always blocked, as is anything that does not parse.
    if (len(args) in (3, 7) and args[0] == "W" and args[1] in OTHER_UARTS
            and args[2] in BAUD_RATES and current is not None and args[1] != current):
        return "confirm", ""
    if current is None and len(args) in (3, 7) and args[0] == "W" and args[1] in OTHER_UARTS:
        return "blocked", UART_WRITE_BLOCKED + " The app does not know its port yet."
    return "blocked", UART_WRITE_BLOCKED


def _prot_rule(fields, current):
    args = fields[1:]
    if len(args) == 3 and args[0] == "R" and args[1] == "1" and args[2] in ("1", "2", "3"):
        return "query", ""
    if (len(args) == 5 and args[0] == "W" and args[1] == "1" and args[2] in OTHER_UARTS
            and current is not None and args[2] != current):
        return "confirm", ""
    if current is None and len(args) == 5 and args[0] == "W" and args[2] in OTHER_UARTS:
        return "blocked", PROT_WRITE_BLOCKED + " The app does not know its port yet."
    return "blocked", PROT_WRITE_BLOCKED


def classify(body, current_uart=None):
    """(kind, reason) for a normalised body. `current_uart` is the index ("1" to
    "3") of the receiver UART this app is connected to, if known."""
    fields = body.split(",")
    name = fields[0]
    if name == FACTORY_RESET:
        return "blocked", ("Factory reset (PQTMRESTOREPAR) is blocked: it would undo the base "
                           "configuration and could change the port settings.")
    if any(word in name for word in FIRMWARE_WORDS):
        return "blocked", "Firmware and bootloader commands are blocked."
    if name == "PQTMCFGUART":
        return _uart_rule(fields, current_uart)
    if name == "PQTMCFGPROT":
        return _prot_rule(fields, current_uart)
    if name in QUERY_ONLY:
        args = fields[1:]
        if len(args) in QUERY_ONLY[name] and all(a.isdigit() for a in args):
            return "query", ""
        return "confirm", ""
    if name in SET_GET and len(fields) >= 2 and fields[1] == "R":
        return "query", ""
    return "confirm", ""


def prepare(text, current_uart=None):
    body, problem = normalise(text)
    prepared = Prepared(text=text if isinstance(text, str) else repr(text), body=body)
    if problem:
        prepared.reason = problem
        return prepared
    prepared.kind, prepared.reason = classify(body, current_uart)
    return prepared


def error_text(code):
    return ERROR_TEXT.get(code, f"error code {code}")


def _same(given, echoed):
    """Whether a reply field echoes a request field: numbers by value ("1" is "01")."""
    if given.isdigit() and echoed.isdigit():
        return int(given) == int(echoed)
    return given == echoed


def _msgrate_shape(asked, rest):
    """PQTMCFGMSGRATE,R: asked is [<PortType>,<PortID>,]<Msg>[,<Ver>], and the reply
    after OK is [<PortType>,<PortID>,]<Msg>,<Rate>[,<Ver or Offset>]. The port fields
    are optional in both; the message must match, and the port too when both give it."""
    port, message = (asked[:2], asked[2]) if len(asked) >= 3 and asked[0].isdigit() else \
        ([], asked[0] if asked else "")
    if not message:
        return None
    if len(rest) >= 2 and rest[0] == message:
        return "ok"
    if len(rest) >= 4 and rest[2] == message and rest[0].isdigit() and rest[1].isdigit():
        return "ok" if all(_same(a, r) for a, r in zip(port, rest)) else None
    return None


def reply_shape(body, fields):
    """How a reply (split fields, same name) answers the request `body`: "error", "ok",
    "part" (a line of a multi-line answer), or None if it cannot be the answer to it.
    Shapes follow the specification: a write is answered by `OK` alone, a read by
    `OK,<fields>` that repeat the index arguments it named, either by `ERROR,<code>`."""
    request = body.split(",")
    name, args = request[0], request[1:]
    if fields[0] != name:
        return None
    if fields[1:2] == ["ERROR"]:
        return "error" if len(fields) == 3 else None
    if name == "PQTMVERNO":
        return "ok" if len(fields) > 1 and fields[1] != "OK" else None
    if fields[1:2] != ["OK"]:
        return None
    rest = fields[2:]
    if name in MULTI_LINE_END:
        if rest == MULTI_LINE_END[name][1:]:
            return "ok"
        return "part" if len(rest) >= len(args) and all(
            _same(a, r) for a, r in zip(args, rest)) else None
    if name in QUERY_ONLY:
        return "ok" if rest else None
    if name == "PQTMCFGMSGRATE" and args[:1] == ["R"]:
        return _msgrate_shape(args[1:], rest)
    if args[:1] == ["R"]:
        echo = args[1:]
        if len(rest) <= len(echo) or not all(_same(a, r) for a, r in zip(echo, rest)):
            return None
        return "ok"
    # A write or any other command: OK alone (a trailing empty field is tolerated).
    return "ok" if not any(rest) else None


class CommandLog:
    """JSON lines in `<log_dir>/commands.jsonl`, one per command or refusal."""

    def __init__(self, path, wall=time.time):
        self.path = Path(path)
        self._wall = wall
        self.error = ""

    def append(self, entry):
        entry = dict(entry, utc=datetime.fromtimestamp(self._wall(), timezone.utc)
                     .isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
            self.error = ""
        except OSError as error:
            self.error = f"command log: {error}"
        return entry

    def tail(self, count=20):
        """The last `count` entries, newest last. Unreadable lines are skipped."""
        try:
            with open(self.path, "rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - HISTORY_BYTES))
                lines = stream.read().splitlines()
                if size > HISTORY_BYTES:
                    lines = lines[1:]  # probably cut
        except OSError:
            return []
        entries = []
        for line in lines[::-1]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
            if len(entries) >= count:
                break
        return entries[::-1]


class PortClosed(Exception):
    pass


class _Halt(Exception):
    """Nothing more of this batch may be sent; the text says why."""


@dataclass
class _Waiter:
    body: str
    future: asyncio.Future
    query: bool = False
    lines: list = field(default_factory=list)
    ignored: list = field(default_factory=list)

    @property
    def name(self):
        return self.body.split(",", 1)[0]


class CommandRunner:
    """Send commands one at a time and match replies from the parsed NMEA stream.

    `write` is a blocking function that writes bytes to the open serial port, or
    raises if it is not open; it runs in an executor. `on_nmea` must be fed every
    NMEA frame (text without $ or checksum) on the event loop thread. Requests wait
    their turn in order (an asyncio lock is a fair queue); at most MAX_QUEUED wait.

    A reply completes a request only if its shape fits the request (`reply_shape`).
    A command that was written but not answered in time is owed a reply for
    OWED_S: the first reply of that name that fits it is taken as that late reply,
    never as the answer to a newer command. After a timeout, the next command of the
    same name also waits LATE_GRACE_S, so most late replies pass before it is sent.
    A write that times out is waited for before anything else is written.
    """

    def __init__(self, write, log, clock=time.monotonic):
        self._write = write
        self.log = log
        self._clock = clock
        self._lock = asyncio.Lock()
        self._waiter = None
        self._queued = 0
        self._quiet_until = {}   # command name: loop time until which replies may be late
        self._owed = {}          # command name: [[body, expiry]] owed a reply
        self._unfinished = None  # the executor future of a write not waited for
        self.connection = 0      # counts port losses
        self.busy_with = ""

    def on_nmea(self, payload):
        fields = payload.split(",")
        waiter = self._waiter
        if waiter is not None and (waiter.future.done() or fields[0] != waiter.name):
            waiter = None
        if fields[0] in self._owed:
            # A late answer to the very query being sent is as good as a fresh one.
            if self._late_reply(fields, waiter.body if waiter and waiter.query else None):
                if waiter is not None:
                    waiter.ignored.append("$" + payload)
                return
        if waiter is None:
            return
        shape = reply_shape(waiter.body, fields)
        if shape is None:
            waiter.ignored.append("$" + payload)
            return
        waiter.lines.append("$" + payload)
        if shape == "error":
            waiter.future.set_result(("error", fields[2], error_text(fields[2])))
        elif shape == "ok":
            waiter.future.set_result(("ok", "", ""))

    def _owed_now(self, name):
        """The unexpired commands of this name that are owed a reply, oldest first."""
        now = self._clock()
        owed = [entry for entry in self._owed.get(name, ()) if entry[1] > now]
        if owed:
            self._owed[name] = owed
        else:
            self._owed.pop(name, None)
        return owed

    def _late_reply(self, fields, skip=None):
        """Whether a reply belongs to an earlier command owed one (other than `skip`).
        A final reply settles the oldest owed command it fits."""
        owed = self._owed_now(fields[0])
        for index, (body, _expiry) in enumerate(owed):
            if body == skip:
                continue
            shape = reply_shape(body, fields)
            if shape in ("ok", "error"):
                del owed[index]
                return True
            if shape == "part":
                return True
        return False

    def _owe(self, prepared):
        """Expect a late reply to this command; returns the entry, or None."""
        if prepared.name in NO_REPLY:
            return None
        entry = [prepared.body, self._clock() + OWED_S]
        self._owed.setdefault(prepared.name, []).append(entry)
        return entry

    def receiver_restarted(self):
        """The receiver started again: no earlier command will be answered now."""
        self._owed.clear()
        self._quiet_until.clear()

    def port_lost(self):
        self.connection += 1
        waiter = self._waiter
        if waiter is not None and not waiter.future.done():
            waiter.future.set_result(
                ("error", "", "The serial port closed; the receiver may be restarting."))

    def _quiet(self, name):
        loop = asyncio.get_running_loop()
        self._quiet_until[name] = max(self._quiet_until.get(name, 0.0),
                                      loop.time() + LATE_GRACE_S)

    def _not_waited_for(self, write, prepared):
        """A write whose end was not waited for: nothing else is written until it is
        done. Its reply is owed from now, since it may arrive before the end of the
        write is seen here, and no longer if the write fails."""
        self._unfinished = write
        entry = self._owe(prepared)
        write.add_done_callback(lambda done: self._write_done(done, prepared.name, entry))

    def _write_done(self, write, name, entry):
        if entry is None:
            return
        if write.cancelled() or write.exception() is not None:
            self._owed[name] = [e for e in self._owed.get(name, []) if e is not entry]
        else:  # still owed unless its reply came already: owed for OWED_S from now
            entry[1] = self._clock() + OWED_S
            self._quiet(name)

    def log_unsent(self, commands, user, source, reason):
        """Log commands that will not be sent, and return their results."""
        return [self._finish(p, user, source, "not sent", [], reason) for p in commands]

    async def run(self, commands, user=None, source="", stop_on_failure=True, recheck=None):
        """Send prepared commands in order, holding the port for all of them. Each is
        logged, including those not sent. Blocked commands are refused, never sent.
        `recheck(prepared)`, if given, is called immediately before each write; a
        reason it returns stops the batch there. A change that is not a query also
        stops the batch if the port closed since the batch took the port, and every
        batch stops if an earlier write is still stuck. Returns results."""
        commands = list(commands)
        if self._queued >= MAX_QUEUED:
            return self.log_unsent(commands, user, source,
                                   "Too many commands are waiting; try again shortly.")
        self._queued += 1
        try:
            try:
                await self._lock.acquire()
            except asyncio.CancelledError:
                self.log_unsent(commands, user, source, STOPPED)
                raise
            try:
                return await self._run_held(commands, user, source, stop_on_failure, recheck)
            finally:
                self._lock.release()
        finally:
            self._queued -= 1

    async def _run_held(self, commands, user, source, stop_on_failure, recheck):
        connection = self.connection
        results = []
        for index, prepared in enumerate(commands):
            if prepared.kind == "blocked":
                result = self._finish(prepared, user, source, "blocked", [], prepared.reason)
            else:
                self.busy_with = prepared.shown
                try:
                    result = await self._send(prepared, user, source, recheck, connection)
                except _Halt as halt:
                    return results + self.log_unsent(commands[index:], user, source, str(halt))
                except asyncio.CancelledError:  # _send logged this command
                    self.log_unsent(commands[index + 1:], user, source, STOPPED)
                    raise
                finally:
                    self.busy_with = ""
            results.append(result)
            if stop_on_failure and result["outcome"] != "ok":
                reason = (f"Not sent: the earlier command {result['command']} "
                          f"ended {result['outcome']}.")
                return results + self.log_unsent(commands[index + 1:], user, source, reason)
        return results

    async def _write_finished(self):
        """Wait for a write that was not waited for; False if it is still not done."""
        if self._unfinished is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(self._unfinished), WRITE_TIMEOUT_S)
        except asyncio.TimeoutError:
            return False
        except asyncio.CancelledError:
            raise
        except Exception:  # its failure was already reported
            pass
        self._unfinished = None
        return True

    async def _before_write(self, prepared, recheck, connection):
        """Wait for an unfinished write and for late replies, then check that the
        command may still be sent. Raises _Halt if it may not."""
        loop = asyncio.get_running_loop()
        if not await self._write_finished():
            raise _Halt("Not sent: an earlier write to the receiver has not finished.")
        delay = self._quiet_until.get(prepared.name, 0.0) - loop.time()
        if delay > 0:  # let any late reply to an earlier command of this name pass
            await asyncio.sleep(delay)
        # Nothing is awaited from here until the write is submitted.
        if prepared.kind != "query" and self.connection != connection:
            raise _Halt("Not sent: the serial port closed while this batch waited; the "
                        "receiver may have restarted. Check it and send again.")
        reason = recheck(prepared) if recheck is not None else ""
        if reason:
            raise _Halt(reason)

    async def _send(self, prepared, user, source, recheck, connection):
        loop = asyncio.get_running_loop()
        try:
            await self._before_write(prepared, recheck, connection)
        except asyncio.CancelledError:
            self._finish(prepared, user, source, "not sent", [], STOPPED)
            raise
        owed_before = bool(self._owed_now(prepared.name))
        waiter = _Waiter(prepared.body, loop.create_future(), query=prepared.kind == "query")
        self._waiter = waiter
        try:
            write = loop.run_in_executor(None, self._write, prepared.sentence)
            try:
                await asyncio.wait_for(asyncio.shield(write), WRITE_TIMEOUT_S)
            except asyncio.TimeoutError:
                self._not_waited_for(write, prepared)
                return self._finish(prepared, user, source, "timeout", [],
                                    f"The write did not finish within {WRITE_TIMEOUT_S:g} s; "
                                    "the command may have been sent.")
            except asyncio.CancelledError:
                self._not_waited_for(write, prepared)
                self._finish(prepared, user, source, "cancelled", [],
                             "The app stopped while writing; the command may have been sent.")
                raise
            except PortClosed as error:
                return self._finish(prepared, user, source, "error", [], str(error))
            except Exception as error:
                return self._finish(prepared, user, source, "error", [],
                                    f"Could not write: {type(error).__name__}: {error}")
            if prepared.name in NO_REPLY:
                return self._finish(prepared, user, source, "ok", [],
                                    "", note="Sent. This command has no reply.")
            try:
                outcome, code, text = await asyncio.wait_for(asyncio.shield(waiter.future),
                                                             prepared.timeout)
            except asyncio.CancelledError:
                # The app is stopping: the command was sent, so it is still logged.
                self._owe(prepared)
                self._finish(prepared, user, source, "error", waiter.lines,
                             "The app stopped before the reply arrived.", ignored=waiter.ignored)
                raise
            except asyncio.TimeoutError:
                self._quiet(prepared.name)
                self._owe(prepared)
                error = f"No reply within {prepared.timeout:g} s."
                if owed_before:
                    error += (f" An earlier {prepared.name} was still owed a reply, so the "
                              "reply to this one may have been taken as that one's.")
                return self._finish(prepared, user, source, "timeout", waiter.lines, error,
                                    ignored=waiter.ignored)
            return self._finish(prepared, user, source, outcome, waiter.lines, text, code=code,
                                ignored=waiter.ignored)
        finally:
            self._waiter = None

    def _finish(self, prepared, user, source, outcome, replies, error, code="", note="",
                ignored=()):
        entry = {"user": user or {}, "source": source, "command": prepared.shown
                 if prepared.body else prepared.text[:MAX_LENGTH],
                 "kind": prepared.kind, "outcome": outcome, "replies": list(replies),
                 "error": error}
        if code:
            entry["error_code"] = code
        if ignored:
            entry["ignored_replies"] = list(ignored)
        if note:
            entry["note"] = note
        return self.log.append(entry)


class PendingStore:
    """Commands waiting for confirmation. An id can be used once, within the TTL."""

    def __init__(self, clock=time.monotonic, ttl=PENDING_TTL_S):
        self._clock = clock
        self._ttl = ttl
        self._items = {}

    def _prune(self):
        now = self._clock()
        for key in [k for k, v in self._items.items() if now - v["created"] > self._ttl]:
            del self._items[key]

    def add(self, commands, title, warnings=(), details=None):
        """Store prepared commands; returns what the page shows."""
        self._prune()
        while len(self._items) >= MAX_PENDING:
            del self._items[next(iter(self._items))]
        key = secrets.token_urlsafe(18)
        self._items[key] = {"created": self._clock(), "commands": list(commands),
                            "title": title, "details": details or {}}
        return {"pending": key, "title": title, "expires_s": self._ttl,
                "commands": [p.shown for p in commands], "warnings": list(warnings),
                "details": details or {}}

    def take(self, key):
        """The pending entry for `key`, removed, or None if unknown or expired."""
        self._prune()
        if not isinstance(key, str):
            return None
        return self._items.pop(key, None)
