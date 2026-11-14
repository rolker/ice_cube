package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Random;

import org.junit.Test;

public class StreamParserTest {
    static byte[] rawx(double tow, int payloadLength) {
        ByteBuffer p = ByteBuffer.allocate(payloadLength).order(ByteOrder.LITTLE_ENDIAN);
        p.putDouble(0, tow);
        return UbxConfig.frame(0x02, 0x15, p.array());
    }

    static String gga(double tod) {
        int h = (int) (tod / 3600), m = (int) (tod / 60) % 60;
        double s = tod % 60;
        String body = String.format(Locale.US, "GNGGA,%02d%02d%05.2f,0000.00000,N,00000.00000,E,4,12,0.6,10.0,M,-30.0,M,1.0,0000", h, m, s);
        int x = 0;
        for (char c : body.toCharArray()) x ^= c;
        return String.format(Locale.US, "$%s*%02X\r\n", body, x);
    }

    /** 200 epochs of RAWX + GGA at 4 Hz with one RAWX missing and some garbage. */
    static byte[] stream() throws Exception {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (int i = 0; i < 200; i++) {
            if (i != 100) o.write(rawx(1000 + i * 0.25, 16 + 32 * (i % 40)));
            o.write(gga(3600 + i * 0.25).getBytes(StandardCharsets.US_ASCII));
            if (i % 17 == 0) o.write(new byte[] {(byte) 0xB5, 0x00, '$', 0x12});
        }
        return o.toByteArray();
    }

    private static StreamParser run(byte[] s, Random rnd) {
        List<String> seen = new ArrayList<>();
        StreamParser p = new StreamParser(seen::add);
        int off = 0;
        while (off < s.length) {
            int n = Math.min(s.length - off, rnd == null ? 1 : 1 + rnd.nextInt(5000));
            byte[] c = new byte[n];
            System.arraycopy(s, off, c, 0, n);
            p.feed(c, n);
            off += n;
        }
        assertEquals(p.ggaCount, seen.size());
        return p;
    }

    @Test public void arbitrarySplitsGiveTheSameCounts() throws Exception {
        byte[] s = stream();
        StreamParser whole = new StreamParser(null);
        whole.feed(s, s.length);
        assertEquals(199, whole.rawxEpochs);
        assertEquals(1, whole.rawxMissed);
        assertEquals(200, whole.ggaCount);
        assertEquals(0, whole.ggaMissed);
        assertEquals(0, whole.ubxBad);
        Random rnd = new Random(7);
        for (int trial = 0; trial < 40; trial++) {
            StreamParser p = run(s, trial == 0 ? null : rnd);
            assertEquals(whole.rawxEpochs, p.rawxEpochs);
            assertEquals(whole.rawxMissed, p.rawxMissed);
            assertEquals(whole.ggaCount, p.ggaCount);
            assertEquals(whole.nmeaBad, p.nmeaBad);
            assertEquals(whole.ubxFrames, p.ubxFrames);
            assertEquals(s.length, p.bytes);
        }
    }

    @Test public void valsetAckIsCounted() {
        byte[] ack = UbxConfig.frame(0x05, 0x01, new byte[] {0x06, (byte) 0x8A});
        byte[] nak = UbxConfig.frame(0x05, 0x00, new byte[] {0x06, (byte) 0x8A});
        StreamParser p = new StreamParser(null);
        p.feed(ack, ack.length);
        p.feed(nak, nak.length);
        assertEquals(1, p.valsetAcks);
        assertEquals(1, p.valsetNaks);
    }
}
