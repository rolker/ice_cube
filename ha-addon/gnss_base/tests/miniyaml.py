"""A small YAML reader for the app's own config.yaml and translations/en.yaml, so the
tests need no PyYAML: block mappings and block lists (of scalars or mappings) by
indentation, `[]`, comments, double-quoted strings, and plain scalars (true/false, whole
numbers, floats, the rest as text). Anything else in those files is a test failure, not
something to support."""
import json
import re


def _scalar(text):
    text = text.strip()
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text.startswith('"'):
        return json.loads(text)
    if text.startswith("'"):
        return text[1:-1].replace("''", "'")
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d*", text):
        return float(text)
    return text


def _lines(text):
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield len(raw) - len(raw.lstrip(" ")), raw.strip()


def _split(item):
    """'key: value' -> (key, value or None); a key may contain no ': '."""
    key, sep, value = item.partition(":")
    if not sep:
        raise ValueError(f"not a mapping line: {item!r}")
    return key.strip(), (value.strip() or None)


def _block(lines, i, indent):
    """Parse the block at `indent` starting at lines[i]; return (value, next i)."""
    if lines[i][1].startswith("- "):
        result = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            item = lines[i][1][2:]
            if ":" in item and not item.startswith('"'):
                # A mapping item: its first key on the dash line, the rest indented by 2.
                lines[i] = (indent + 2, item)
                value, i = _block(lines, i, indent + 2)
            else:
                value, i = _scalar(item), i + 1
            result.append(value)
        return result, i
    result = {}
    while i < len(lines) and lines[i][0] == indent:
        key, value = _split(lines[i][1])
        i += 1
        if value is None:
            if i < len(lines) and lines[i][0] > indent:
                value, i = _block(lines, i, lines[i][0])
            elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                value, i = _block(lines, i, indent)
            else:
                value = None
        else:
            value = _scalar(value)
        result[key] = value
    return result, i


def load(text):
    lines = list(_lines(text))
    value, i = _block(lines, 0, lines[0][0])
    if i != len(lines):
        raise ValueError(f"unparsed from line {i}: {lines[i]}")
    return value
