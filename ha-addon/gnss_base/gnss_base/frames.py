"""Split a mixed receiver byte stream into checked RTCM3 and NMEA frames."""
import re

MAX_NMEA = 512
# An NMEA body is printable ASCII without '$'; anything else ends it.
_NMEA_BODY = re.compile(rb"[\x20-\x23\x25-\x7E]*")
_HEX2 = re.compile(rb"[0-9A-Fa-f]{2}")
_CRC_POLY = 0x1864CFB


def crc24q(data):
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= _CRC_POLY
    return crc & 0xFFFFFF


def nmea_checksum(body):
    value = 0
    for byte in body:
        value ^= byte
    return value


class FrameParser:
    """Feed bytes in; get (kind, payload) frames out.

    kind is "rtcm" (payload: whole frame) or "nmea" (payload: text without the
    leading $ or the checksum). Bytes that do not form a valid frame are counted
    in `unknown_bytes`, and frames with a bad checksum in `bad_frames`.
    """

    def __init__(self):
        self._buffer = bytearray()
        self.unknown_bytes = 0
        self.bad_frames = 0

    def feed(self, data):
        self._buffer.extend(data)
        frames = []
        pos = 0
        buffer = self._buffer
        while pos < len(buffer):
            start = buffer[pos]
            if start == 0xD3:
                result = self._rtcm_at(pos)
            elif start == 0x24:
                result = self._nmea_at(pos)
            else:
                result = 0
            if result is None:  # possible frame, still incomplete
                break
            if isinstance(result, tuple):
                frames.append(result[0])
                pos += result[1]
            else:
                self.unknown_bytes += 1
                pos += 1
        del buffer[:pos]
        return frames

    def _rtcm_at(self, pos):
        buffer = self._buffer
        if len(buffer) - pos < 3:
            return None
        if buffer[pos + 1] & 0xFC:
            return 0
        length = ((buffer[pos + 1] & 0x03) << 8) | buffer[pos + 2]
        if length < 2:  # too short to hold a message number: not a frame
            return 0
        total = length + 6
        if len(buffer) - pos < total:
            return None
        frame = bytes(buffer[pos : pos + total])
        if crc24q(frame[:-3]) != int.from_bytes(frame[-3:], "big"):
            self.bad_frames += 1
            return 0
        return ("rtcm", frame), total

    def _nmea_at(self, pos):
        buffer = self._buffer
        limit = min(len(buffer), pos + MAX_NMEA)
        end = _NMEA_BODY.match(buffer, pos + 1, limit).end()
        if end == limit:
            return None if limit - pos < MAX_NMEA else 0
        if buffer[end] == 0x0D:  # CR, then LF must follow
            if end + 1 >= len(buffer):
                return None
            if buffer[end + 1] != 0x0A:
                return 0
            stop = end + 2
        elif buffer[end] == 0x0A:
            stop = end + 1
        else:  # binary or another '$': this was not an NMEA sentence
            return 0
        body, star, checksum = bytes(buffer[pos + 1 : end]).rpartition(b"*")
        if not star or not _HEX2.fullmatch(checksum):
            return 0
        if int(checksum, 16) != nmea_checksum(body):
            self.bad_frames += 1
            return 0
        return ("nmea", body.decode("ascii")), stop - pos


def rtcm_type(frame):
    """Message number from a whole RTCM3 frame."""
    return (frame[3] << 4) | (frame[4] >> 4)
