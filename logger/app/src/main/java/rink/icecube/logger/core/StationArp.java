package rink.icecube.logger.core;

import java.util.Locale;

/**
 * RTCM 1005 / 1006 stationary reference station ARP: station ID, ECEF X/Y/Z of the antenna
 * reference point (DF025-027, signed 38 bits, 0.0001 m) and, for 1006, the antenna height
 * above the marker (DF028, unsigned 16 bits, 0.0001 m). The message says nothing about the
 * reference frame apart from DF021, a 6-bit "ITRF realization year" that is normally 0.
 */
public final class StationArp {
    public final int type, stationId, itrfYear;
    public final double x, y, z;
    public final double antennaHeight;   // NaN for 1005

    StationArp(int type, int stationId, int itrfYear, double x, double y, double z, double antennaHeight) {
        this.type = type; this.stationId = stationId; this.itrfYear = itrfYear;
        this.x = x; this.y = y; this.z = z; this.antennaHeight = antennaHeight;
    }

    /** Decodes a 1005 or 1006 payload (the bytes after the 3-byte header). Null if it is neither or too short. */
    public static StationArp decode(byte[] b, int off, int len) {
        if (len < 19) return null;
        Bits r = new Bits(b, off, len);
        int type = (int) r.u(12);
        if (type != 1005 && type != 1006) return null;
        if (type == 1006 && len < 21) return null;
        int id = (int) r.u(12);
        int itrf = (int) r.u(6);
        r.u(4);                                // GPS, GLONASS, Galileo, reference-station indicators
        double x = r.s(38) * 1e-4;
        r.u(2);                                // single receiver oscillator, reserved
        double y = r.s(38) * 1e-4;
        r.u(2);                                // quarter cycle indicator
        double z = r.s(38) * 1e-4;
        double h = type == 1006 ? r.u(16) * 1e-4 : Double.NaN;
        return new StationArp(type, id, itrf, x, y, z, h);
    }

    public double distanceTo(StationArp o) { return Math.sqrt(sq(x - o.x) + sq(y - o.y) + sq(z - o.z)); }

    private static double sq(double v) { return v * v; }

    /** "base station=<id> ecef=<x>,<y>,<z> h=<ant height>" for events.csv (commas become ';' there). */
    public String eventText() {
        return String.format(Locale.US, "base station=%d type=%d ecef=%.4f,%.4f,%.4f h=%s%s", stationId, type, x, y, z,
                Double.isNaN(antennaHeight) ? "none" : String.format(Locale.US, "%.4f", antennaHeight),
                itrfYear != 0 ? " itrf_year_field=" + itrfYear : "");
    }

    /** Big-endian bit reader. */
    static final class Bits {
        private final byte[] b;
        private final int end;
        private int pos;

        Bits(byte[] b, int off, int len) { this.b = b; this.pos = off * 8; this.end = (off + len) * 8; }

        long u(int n) {
            if (pos + n > end) throw new IllegalArgumentException("message too short");
            long v = 0;
            for (int i = 0; i < n; i++, pos++) v = (v << 1) | ((b[pos >> 3] >> (7 - (pos & 7))) & 1);
            return v;
        }

        long s(int n) {
            long v = u(n);
            return (v & (1L << (n - 1))) != 0 ? v - (1L << n) : v;
        }
    }
}
