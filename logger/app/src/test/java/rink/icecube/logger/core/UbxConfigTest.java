package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class UbxConfigTest {
    private static boolean hasKey(byte[] f, long key) {
        for (int i = 10; i + 4 <= f.length - 2; i++) {
            long k = (f[i] & 0xffL) | (f[i + 1] & 0xffL) << 8 | (f[i + 2] & 0xffL) << 16 | (f[i + 3] & 0xffL) << 24;
            if (k == key) return true;
        }
        return false;
    }

    @Test public void framesParseAndTargetTheRightPort() {
        for (byte[] f : new byte[][] {UbxConfig.enableUsbLogging(), UbxConfig.enableUart1Logging()}) {
            StreamParser p = new StreamParser(null);
            p.feed(f, f.length);
            assertEquals(1, p.ubxFrames);
            assertEquals(0x06, f[2]);
            assertEquals((byte) 0x8A, f[3]);
            assertEquals(0x01, f[7]);                 // RAM layer only
            assertEquals(4 + 8 * 5, f.length - 8);    // 8 keys of 4 + 1 bytes
        }
        assertTrue(hasKey(UbxConfig.enableUsbLogging(), 0x209102a7L));     // RAWX on USB
        assertTrue(hasKey(UbxConfig.enableUart1Logging(), 0x209102a5L));   // RAWX on UART1
        assertTrue(hasKey(UbxConfig.enableUart1Logging(), 0x10730004L));   // RTCM3 in on UART1
    }
}
