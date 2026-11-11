package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

import org.junit.Test;

public class Rtcm3ParserTest {
    /** RTCM 1005 example frame from the RTCM 10403 standard. */
    static final byte[] MSG_1005 = hex("D300133ED7D30202980EDEEF34B4BD62AC0941986F33360B98");

    static byte[] hex(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }

    /** A frame whose payload starts with the 12-bit message number. */
    static byte[] message(int type, int payloadLength, int seed) {
        byte[] p = new byte[payloadLength];
        new Random(seed).nextBytes(p);
        p[0] = (byte) (type >> 4);
        p[1] = (byte) ((type << 4) | (p[1] & 0x0F));
        return Rtcm3Parser.frame(p);
    }

    private static final class Recorder implements Rtcm3Parser.Listener {
        final List<Integer> types = new ArrayList<>();
        @Override public void onMessage(int type, int len) { types.add(type); }
    }

    @Test public void crcCheckValue() {
        byte[] b = "123456789".getBytes(StandardCharsets.US_ASCII);
        assertEquals(0xCDE703, Rtcm3Parser.crc24q(b, 0, b.length));
    }

    @Test public void parsesStandardExample() {
        Recorder r = new Recorder();
        Rtcm3Parser p = new Rtcm3Parser(r);
        p.feed(MSG_1005, MSG_1005.length);
        assertEquals(1, p.frames);
        assertEquals(0, p.crcErrors);
        assertEquals(1005, (int) r.types.get(0));
    }

    @Test public void builtFrameMatchesExample() {
        byte[] payload = new byte[MSG_1005.length - 6];
        System.arraycopy(MSG_1005, 3, payload, 0, payload.length);
        assertEquals(new String(hexOf(MSG_1005)), new String(hexOf(Rtcm3Parser.frame(payload))));
    }

    private static char[] hexOf(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02X", x));
        return s.toString().toCharArray();
    }

    @Test public void anySplitGivesSameResult() throws Exception {
        ByteArrayOutputStream all = new ByteArrayOutputStream();
        int[] types = {1005, 1077, 1087, 1097, 1127, 1230, 4095, 1};
        List<Integer> expected = new ArrayList<>();
        for (int i = 0; i < 40; i++) {
            int t = types[i % types.length];
            all.write(message(t, 2 + (i * 37) % 1022, i));
            expected.add(t);
            if (i % 5 == 0) all.write(new byte[] {0x00, (byte) 0xD3, 0x7F, 0x55});   // garbage, includes a false preamble
        }
        byte[] stream = all.toByteArray();
        Random rnd = new Random(1);
        for (int trial = 0; trial < 50; trial++) {
            Recorder r = new Recorder();
            Rtcm3Parser p = new Rtcm3Parser(r);
            int off = 0;
            while (off < stream.length) {
                int n = Math.min(stream.length - off, trial == 0 ? 1 : 1 + rnd.nextInt(3000));
                byte[] chunk = new byte[n];
                System.arraycopy(stream, off, chunk, 0, n);
                p.feed(chunk, n);
                off += n;
            }
            assertEquals(expected, r.types);
            assertEquals(stream.length, p.bytes);
        }
    }

    @Test public void corruptFrameIsRejectedAndNextIsFound() {
        byte[] a = message(1077, 200, 1), b = message(1087, 100, 2);
        a[50] ^= 0x01;
        byte[] s = new byte[a.length + b.length];
        System.arraycopy(a, 0, s, 0, a.length);
        System.arraycopy(b, 0, s, a.length, b.length);
        Recorder r = new Recorder();
        Rtcm3Parser p = new Rtcm3Parser(r);
        p.feed(s, s.length);
        assertEquals(1, p.frames);
        assertEquals(1, p.crcErrors);
        assertEquals(1087, (int) r.types.get(0));
    }

    @Test public void statsCountAndRate() {
        RtcmStats st = new RtcmStats(10000);
        for (int i = 0; i <= 20; i++) st.add(1077, i * 1000L);
        st.add(1005, 0);
        st.add(1005, 10000);
        st.add(1005, 20000);
        assertEquals(21, st.count(1077));
        assertEquals(1.0, st.rate(1077, 20000), 0.11);
        assertEquals(0.1, st.rate(1005, 20000), 0.01);
        assertEquals(0.0, st.rate(1005, 60000), 1e-9);
        assertEquals(0, st.count(1230));
    }
}
