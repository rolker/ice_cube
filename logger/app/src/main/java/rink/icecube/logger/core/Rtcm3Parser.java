package rink.icecube.logger.core;

/**
 * Incremental RTCM3 frame parser. A frame is the preamble 0xD3, 6 reserved zero bits,
 * a 10-bit payload length, the payload, and a CRC-24Q over everything before it.
 * The message number is the first 12 bits of the payload. Reads may be split at any byte.
 * The parser only counts; it never changes the bytes it is given.
 */
public final class Rtcm3Parser {
    public interface Listener { void onMessage(int type, int payloadLength); }
    /** Optional: sees each good frame's payload, buf[off..off+len), only during the call. */
    public interface PayloadListener { void onPayload(int type, byte[] buf, int off, int len); }

    private final byte[] buf = new byte[1 << 14];
    private int len = 0;
    private final Listener listener;

    public long bytes, frames, crcErrors, skipped;

    public Rtcm3Parser(Listener l) { listener = l; }

    public volatile PayloadListener payloads;

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
            if ((buf[i] & 0xff) != 0xD3) { i++; skipped++; continue; }
            if (i + 3 > len) return i;
            if ((buf[i + 1] & 0xFC) != 0) { i++; skipped++; continue; }
            int pl = ((buf[i + 1] & 0x03) << 8) | (buf[i + 2] & 0xff);
            if (i + 6 + pl > len) return i;
            int want = crc24q(buf, i, 3 + pl);
            int got = ((buf[i + 3 + pl] & 0xff) << 16) | ((buf[i + 4 + pl] & 0xff) << 8) | (buf[i + 5 + pl] & 0xff);
            if (want != got) { crcErrors++; skipped++; i++; continue; }
            frames++;
            int type = pl >= 2 ? ((buf[i + 3] & 0xff) << 4) | ((buf[i + 4] & 0xff) >> 4) : 0;
            if (listener != null) listener.onMessage(type, pl);
            PayloadListener pls = payloads;
            if (pls != null) pls.onPayload(type, buf, i + 3, pl);
            i += 6 + pl;
        }
        return i;
    }

    /** CRC-24Q (polynomial 0x1864CFB, initial value 0) as used by RTCM3. */
    public static int crc24q(byte[] b, int off, int n) {
        int crc = 0;
        for (int k = off; k < off + n; k++) {
            crc ^= (b[k] & 0xff) << 16;
            for (int j = 0; j < 8; j++) {
                crc <<= 1;
                if ((crc & 0x1000000) != 0) crc ^= 0x1864CFB;
            }
        }
        return crc & 0xFFFFFF;
    }

    /** Builds a frame around a payload. Used by tests. */
    public static byte[] frame(byte[] payload) {
        byte[] f = new byte[payload.length + 6];
        f[0] = (byte) 0xD3;
        f[1] = (byte) ((payload.length >> 8) & 0x03);
        f[2] = (byte) payload.length;
        System.arraycopy(payload, 0, f, 3, payload.length);
        int c = crc24q(f, 0, 3 + payload.length);
        f[3 + payload.length] = (byte) (c >> 16);
        f[4 + payload.length] = (byte) (c >> 8);
        f[5 + payload.length] = (byte) c;
        return f;
    }
}
