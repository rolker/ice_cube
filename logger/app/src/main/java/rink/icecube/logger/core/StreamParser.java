package rink.icecube.logger.core;

import java.util.Arrays;

/**
 * Incremental parser for the mixed UBX and NMEA byte stream from a u-blox receiver.
 * It tolerates reads split at any byte and corrupted data, and keeps the counts
 * needed to judge whether a session lost data.
 */
public final class StreamParser {
    public interface Listener { void onGga(String sentence); }

    private static final int MAX_FRAME = 8200;
    private final byte[] buf = new byte[1 << 17];
    private int len = 0;
    private final Listener listener;

    public long bytes, ubxFrames, ubxBad, nmeaGood, nmeaBad, skipped;
    public long rawxEpochs, rawxGaps, rawxMissed, ggaCount, ggaGaps, ggaMissed;
    public double rawxMaxGap, ggaMaxGap;
    public int valsetAcks, valsetNaks;
    private double lastTow = Double.NaN, lastGga = Double.NaN;
    public double nominalPeriod = 0.25;

    public StreamParser(Listener l) { listener = l; }

    public void feed(byte[] data, int n) {
        bytes += n;
        int off = 0;
        while (off < n) {
            int c = Math.min(n - off, buf.length - len);
            System.arraycopy(data, off, buf, len, c);
            len += c; off += c;
            int used = parse();
            if (used > 0) { System.arraycopy(buf, used, buf, 0, len - used); len -= used; }
        }
    }

    private int parse() {
        int i = 0;
        while (i < len) {
            int b = buf[i] & 0xff;
            if (b == 0xB5) {
                if (i + 6 > len) return i;
                if ((buf[i + 1] & 0xff) != 0x62) { i++; skipped++; continue; }
                int pl = (buf[i + 4] & 0xff) | ((buf[i + 5] & 0xff) << 8);
                if (pl > MAX_FRAME) { i++; skipped++; ubxBad++; continue; }
                if (i + 8 + pl > len) return i;
                int a = 0, d = 0;
                for (int k = i + 2; k < i + 6 + pl; k++) { a = (a + (buf[k] & 0xff)) & 0xff; d = (d + a) & 0xff; }
                if (a == (buf[i + 6 + pl] & 0xff) && d == (buf[i + 7 + pl] & 0xff)) {
                    ubxFrames++;
                    onUbx(buf[i + 2] & 0xff, buf[i + 3] & 0xff, i + 6, pl);
                    i += 8 + pl;
                } else { ubxBad++; i++; skipped++; }
            } else if (b == '$') {
                int end = -1;
                for (int k = i + 1; k < len && k < i + 256; k++) {
                    if (buf[k] == '\n') { end = k; break; }
                    if (buf[k] == '$' || (buf[k] & 0xff) == 0xB5) { end = -2; break; }
                }
                if (end == -1) { if (len - i < 256) return i; i++; nmeaBad++; continue; }
                if (end == -2) { i++; nmeaBad++; continue; }
                String s = new String(buf, i, end - i, java.nio.charset.StandardCharsets.ISO_8859_1).trim();
                if (nmeaOk(s)) {
                    nmeaGood++;
                    if (s.length() > 6 && s.startsWith("GGA", 3)) onGga(s);
                    i = end + 1;
                } else { nmeaBad++; i++; }
            } else { i++; skipped++; }
        }
        return i;
    }

    public static boolean nmeaOk(String s) {
        int star = s.lastIndexOf('*');
        if (star < 1 || star + 3 > s.length()) return false;
        int x = 0;
        for (int k = 1; k < star; k++) x ^= s.charAt(k);
        try { return x == Integer.parseInt(s.substring(star + 1, star + 3), 16); }
        catch (NumberFormatException e) { return false; }
    }

    private void onUbx(int cls, int id, int p, int n) {
        if (cls == 0x02 && id == 0x15 && n >= 16) {
            long v = 0;
            for (int k = 7; k >= 0; k--) v = (v << 8) | (buf[p + k] & 0xff);
            double tow = Double.longBitsToDouble(v);
            if (!Double.isNaN(lastTow)) {
                double dt = tow - lastTow;
                if (dt < 0) dt += 604800;
                if (dt > 0 && dt < 3600) {
                    rawxMaxGap = Math.max(rawxMaxGap, dt);
                    long miss = Math.round(dt / nominalPeriod) - 1;
                    if (miss > 0) { rawxGaps++; rawxMissed += miss; }
                }
            }
            lastTow = tow;
            rawxEpochs++;
        } else if (cls == 0x05 && n >= 2 && (buf[p] & 0xff) == 0x06 && (buf[p + 1] & 0xff) == 0x8A) {
            if (id == 0x01) valsetAcks++; else if (id == 0x00) valsetNaks++;
        }
    }

    private void onGga(String s) {
        Gga g = Gga.parse(s);
        ggaCount++;
        if (g != null && !Double.isNaN(g.tod)) {
            if (!Double.isNaN(lastGga)) {
                double dt = g.tod - lastGga;
                if (dt < 0) dt += 86400;
                if (dt > 0 && dt < 3600) {
                    ggaMaxGap = Math.max(ggaMaxGap, dt);
                    long miss = Math.round(dt / nominalPeriod) - 1;
                    if (miss > 0) { ggaGaps++; ggaMissed += miss; }
                }
            }
            lastGga = g.tod;
        }
        if (listener != null) listener.onGga(s);
    }

    public double rawxLossPct() {
        long t = rawxEpochs + rawxMissed;
        return t == 0 ? 0 : 100.0 * rawxMissed / t;
    }

    public double ggaLossPct() {
        long t = ggaCount + ggaMissed;
        return t == 0 ? 0 : 100.0 * ggaMissed / t;
    }

    /** Test helper. */
    public static byte[] copy(byte[] b, int n) { return Arrays.copyOf(b, n); }
}
