"""Decode the RTCM3 messages the page needs: 1005/1006 (base position) and the
headers, masks and signal strengths of MSM1 to MSM7 (RTCM 10403.3).

Frames are whole RTCM3 frames whose CRC the frame parser has already checked.
"""
import math

from .frames import rtcm_type

# WGS84
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)

MSM_SYSTEMS = {107: "GPS", 108: "GLONASS", 109: "Galileo", 111: "QZSS", 112: "BeiDou",
               113: "NavIC"}
_HEADER_BITS = 12 + 12 + 30 + 1 + 3 + 7 + 2 + 2 + 1 + 3 + 64 + 32
# Bits per satellite of the satellite data, by MSM type
_SAT_BITS = {1: 10, 2: 10, 3: 10, 4: 18, 5: 36, 6: 18, 7: 36}
# Per cell: (bits of the arrays before CNR, CNR bits, bits after CNR, CNR scale)
_CELL = {1: (15, 0, 0, 0), 2: (22 + 4 + 1, 0, 0, 0), 3: (15 + 22 + 4 + 1, 0, 0, 0),
         4: (15 + 22 + 4 + 1, 6, 0, 1.0), 5: (15 + 22 + 4 + 1, 6, 15, 1.0),
         6: (20 + 24 + 10 + 1, 10, 0, 0.0625), 7: (20 + 24 + 10 + 1, 10, 15, 0.0625)}

# Signal IDs (1 to 32) to readable names, from the MSM signal tables of RTCM 10403.3,
# with the RINEX observation code. IDs not listed are shown by number.
SIGNAL_NAMES = {
    "GPS": {2: "L1 C/A (1C)", 3: "L1 P (1P)", 4: "L1 Z (1W)", 8: "L2 C/A (2C)", 9: "L2 P (2P)",
            10: "L2 Z (2W)", 15: "L2C M (2S)", 16: "L2C L (2L)", 17: "L2C M+L (2X)",
            22: "L5 I (5I)", 23: "L5 Q (5Q)", 24: "L5 I+Q (5X)", 30: "L1C D (1S)",
            31: "L1C P (1L)", 32: "L1C D+P (1X)"},
    "GLONASS": {2: "G1 C/A (1C)", 3: "G1 P (1P)", 8: "G2 C/A (2C)", 9: "G2 P (2P)"},
    "Galileo": {2: "E1 C (1C)", 3: "E1 A (1A)", 4: "E1 B (1B)", 5: "E1 B+C (1X)",
                6: "E1 A+B+C (1Z)", 8: "E6 C (6C)", 9: "E6 A (6A)", 10: "E6 B (6B)",
                11: "E6 B+C (6X)", 12: "E6 A+B+C (6Z)", 14: "E5b I (7I)", 15: "E5b Q (7Q)",
                16: "E5b I+Q (7X)", 18: "E5 I (8I)", 19: "E5 Q (8Q)", 20: "E5 I+Q (8X)",
                22: "E5a I (5I)", 23: "E5a Q (5Q)", 24: "E5a I+Q (5X)"},
    "BeiDou": {2: "B1I (2I)", 3: "B1 Q (2Q)", 4: "B1 I+Q (2X)", 8: "B3I (6I)", 9: "B3 Q (6Q)",
               10: "B3 I+Q (6X)", 14: "B2I (7I)", 15: "B2 Q (7Q)", 16: "B2 I+Q (7X)",
               22: "B2a D (5D)", 23: "B2a P (5P)", 24: "B2a D+P (5X)", 25: "B2b I (7D)",
               30: "B1C D (1D)", 31: "B1C P (1P)", 32: "B1C D+P (1X)"},
    "QZSS": {2: "L1 C/A (1C)", 9: "L6 D (6S)", 10: "L6 P (6L)", 11: "L6 D+P (6X)",
             15: "L2C M (2S)", 16: "L2C L (2L)", 17: "L2C M+L (2X)", 22: "L5 I (5I)",
             23: "L5 Q (5Q)", 24: "L5 I+Q (5X)", 30: "L1C D (1S)", 31: "L1C P (1L)",
             32: "L1C D+P (1X)"},
}


def signal_name(system, signal_id):
    return SIGNAL_NAMES.get(system, {}).get(signal_id, f"signal {signal_id}")


class Bits:
    """Read unsigned and two's complement fields, most significant bit first."""

    def __init__(self, data):
        self._value = int.from_bytes(data, "big")
        self.size = len(data) * 8
        self.pos = 0

    def u(self, count):
        if count == 0:
            return 0
        if self.pos + count > self.size:
            raise ValueError("message too short")
        self.pos += count
        return (self._value >> (self.size - self.pos)) & ((1 << count) - 1)

    def s(self, count):
        value = self.u(count)
        return value - (1 << count) if value >> (count - 1) else value

    def skip(self, count):
        if self.pos + count > self.size:
            raise ValueError("message too short")
        self.pos += count


def ecef_to_llh(x, y, z):
    """WGS84 ECEF metres to latitude and longitude in degrees and ellipsoidal height."""
    p = math.hypot(x, y)
    lon = math.atan2(y, x)
    lat = math.atan2(z, p * (1 - _E2))
    for _ in range(10):
        sin_lat = math.sin(lat)
        n = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
        lat = math.atan2(z + _E2 * n * sin_lat, p)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    height = p * cos_lat + z * sin_lat - _A * math.sqrt(1 - _E2 * sin_lat * sin_lat)
    return math.degrees(lat), math.degrees(lon), height


def llh_to_ecef(lat, lon, height):
    lat, lon = math.radians(lat), math.radians(lon)
    sin_lat = math.sin(lat)
    n = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
    return ((n + height) * math.cos(lat) * math.cos(lon),
            (n + height) * math.cos(lat) * math.sin(lon),
            (n * (1 - _E2) + height) * sin_lat)


def decode_1005(frame):
    """Station ARP from a 1005 or 1006 frame: ECEF in metres, and for 1006 the
    antenna height above the marker."""
    number = rtcm_type(frame)
    if number not in (1005, 1006):
        raise ValueError(f"not a 1005 or 1006 message: {number}")
    bits = Bits(frame[3:-3])
    bits.skip(12)
    station = bits.u(12)
    itrf_year = bits.u(6)
    systems = [name for name in ("GPS", "GLONASS", "Galileo") if bits.u(1)]
    bits.skip(1)                    # reference station indicator
    x = bits.s(38) * 0.0001
    bits.skip(2)                    # single receiver oscillator, reserved
    y = bits.s(38) * 0.0001
    bits.skip(2)                    # quarter cycle indicator
    z = bits.s(38) * 0.0001
    result = {"message": number, "station_id": station, "itrf_year": itrf_year,
              "systems": systems, "ecef": (x, y, z), "antenna_height_m": None}
    if number == 1006:
        result["antenna_height_m"] = bits.u(16) * 0.0001
    return result


def decode_msm(frame):
    """Header, masks and per-cell CNR of an MSM1 to MSM7 frame. Raises ValueError
    if the frame is too short for what its masks declare. CNR is None for MSM1 to
    MSM3, and for cells whose CNR is 0 (not available)."""
    number = rtcm_type(frame)
    system = MSM_SYSTEMS.get(number // 10)
    msm = number % 10
    if system is None or not 1 <= msm <= 7:
        raise ValueError(f"not an MSM message: {number}")
    bits = Bits(frame[3:-3])
    bits.skip(12)
    station = bits.u(12)
    epoch = bits.u(30)
    multiple = bool(bits.u(1))
    bits.skip(3 + 7 + 2 + 2 + 1 + 3)
    sat_mask, sig_mask = bits.u(64), bits.u(32)
    satellites = [index + 1 for index in range(64) if sat_mask >> (63 - index) & 1]
    signals = [index + 1 for index in range(32) if sig_mask >> (31 - index) & 1]
    if len(satellites) * len(signals) > 64:
        raise ValueError("more than 64 cells")
    size = len(satellites) * len(signals)
    cell_mask = bits.u(size)
    cells = [(sat, sig) for i, sat in enumerate(satellites) for j, sig in enumerate(signals)
             if cell_mask >> (size - 1 - (i * len(signals) + j)) & 1]
    before, cnr_bits, after, scale = _CELL[msm]
    needed = (_HEADER_BITS + size + _SAT_BITS[msm] * len(satellites)
              + (before + cnr_bits + after) * len(cells))
    if needed > bits.size:
        raise ValueError("message too short for its masks")
    bits.skip(_SAT_BITS[msm] * len(satellites) + before * len(cells))
    cnr = []
    for _ in cells:
        value = bits.u(cnr_bits) if cnr_bits else 0
        cnr.append(value * scale if value else None)
    return {"message": number, "system": system, "msm": msm, "station_id": station,
            "epoch": epoch, "multiple": multiple, "satellites": satellites,
            "signals": signals,
            "cells": [(sat, sig, value) for (sat, sig), value in zip(cells, cnr)]}
