"""The receiver profile: settings the base should have, read back and compared.

The profile is TOML (see profile.toml). It never holds coordinates.
"""
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("profile.toml")
_TOKEN = re.compile(r"[A-Za-z0-9.+_-]+")
_NAME = re.compile(r"[A-Z0-9]+")
_MESSAGE = re.compile(r"[A-Z0-9][A-Z0-9-]*")


class ProfileError(ValueError):
    pass


@dataclass
class Item:
    label: str
    query: str          # body of the read command
    name: str           # command name the reply carries
    expect: list        # values expected after OK (settings) or [rate, extra] (messages)
    set: str            # body of the write command
    note: str = ""
    restart: bool = False
    base_only: bool = False
    message: str = ""   # message name, for message-rate items

    def compare(self, fields):
        """'match' or 'differ' for the fields of a reply line after `<name>,OK`."""
        if self.message:
            if self.message not in fields:
                return "differ", ",".join(fields)
            index = fields.index(self.message)
            actual = fields[index + 1:]
        else:
            actual = fields
        shown = ",".join(actual)
        if len(actual) < len(self.expect):
            return "differ", shown
        # A query for a message rate may echo a version or offset that the profile
        # does not set; settings must match field for field.
        if not self.message and len(actual) != len(self.expect):
            return "differ", shown
        for want, got in zip(self.expect, actual):
            if not same(want, got):
                return "differ", shown
        return "match", shown

    @property
    def expected(self):
        return ",".join(self.expect)


def same(want, got):
    """Equal as numbers if both are numbers (so 07 equals 7 and -90 equals -90.0),
    otherwise as text."""
    try:
        a, b = float(want), float(got)
    except ValueError:
        return want == got
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) < 1e-9


def _text(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProfileError(f"{where}: values must be numbers or text")
    text = repr(value) if isinstance(value, float) else str(value)
    if not _TOKEN.fullmatch(text):
        raise ProfileError(f"{where}: value {text!r} has characters a command cannot carry")
    return text.upper()


def _flags(entry, where):
    for key in ("restart", "base_only"):
        if not isinstance(entry.get(key, False), bool):
            raise ProfileError(f"{where}: {key} must be true or false")
    note = entry.get("note", "")
    if not isinstance(note, str):
        raise ProfileError(f"{where}: note must be text")
    return dict(note=note.strip(), restart=entry.get("restart", False),
                base_only=entry.get("base_only", False))


def parse(text):
    """A list of Items from profile TOML text. Raises ProfileError."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"not valid TOML: {error}") from error
    unknown = set(data) - {"port", "setting", "message"}
    if unknown:
        raise ProfileError(f"unknown sections: {', '.join(sorted(unknown))}")
    port = data.get("port", {"type": 1, "id": 1})
    port_type, port_id = port.get("type"), port.get("id")
    if port_type != 1 or port_id not in (1, 2, 3):
        raise ProfileError("[port] needs type = 1 (UART) and id = 1, 2 or 3")
    items = []
    for number, entry in enumerate(data.get("setting", []), 1):
        where = f"setting {number}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{where}: must be a table")
        command, values = entry.get("command"), entry.get("value")
        if not isinstance(command, str) or not _NAME.fullmatch(command):
            raise ProfileError(f"{where}: command must be a name like PQTMCFGRTCM")
        if not command.startswith("PQTMCFG"):
            raise ProfileError(f"{where}: only PQTMCFG Set/Get commands can be checked")
        if not isinstance(values, list) or not values:
            raise ProfileError(f"{where}: value must be a non-empty list")
        expect = [_text(v, where) for v in values]
        items.append(Item(label=str(entry.get("label", command)), query=f"{command},R",
                          name=command, expect=expect,
                          set=",".join([command, "W"] + expect), **_flags(entry, where)))
    for number, entry in enumerate(data.get("message", []), 1):
        where = f"message {number}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{where}: must be a table")
        name, rate = entry.get("name"), entry.get("rate")
        if not isinstance(name, str) or not _MESSAGE.fullmatch(name):
            raise ProfileError(f"{where}: name must be a message name like RTCM3-1005")
        if isinstance(rate, bool) or not isinstance(rate, int) or not 0 <= rate <= 1200:
            raise ProfileError(f"{where}: rate must be a whole number from 0 to 1200")
        if "offset" in entry and "version" in entry:
            raise ProfileError(f"{where}: give an offset (MSM) or a version (PQTM), not both")
        extra = entry.get("offset", entry.get("version"))
        if extra is not None and (isinstance(extra, bool) or not isinstance(extra, int)
                                  or extra < 0):
            raise ProfileError(f"{where}: offset or version must be a whole number")
        prefix = f"PQTMCFGMSGRATE,{{}},{port_type},{port_id},{name}"
        # PQTM messages are queried by version; MSM queries leave the offset out.
        query = prefix.format("R") + (f",{extra}" if "version" in entry else "")
        values = [str(rate)] + ([] if extra is None else [str(extra)])
        items.append(Item(label=str(entry.get("label", name)), query=query,
                          name="PQTMCFGMSGRATE", expect=values,
                          set=",".join([prefix.format("W")] + values), message=name,
                          **_flags(entry, where)))
    if not items:
        raise ProfileError("the profile has no settings or messages")
    return items


def load(path=DEFAULT_PATH):
    return parse(Path(path).read_text(encoding="utf-8"))
