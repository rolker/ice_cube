package rink.icecube.logger.core;

import java.io.ByteArrayOutputStream;

/**
 * Builds a UBX-CFG-VALSET message that turns on the logged messages on the receiver's USB port.
 * It writes to the RAM layer only, so a power cycle restores the receiver's saved settings.
 */
public final class UbxConfig {
    private static final long[][] U1_KEYS = {
        {0x209100bdL, 1},   // CFG-MSGOUT-NMEA_ID_GGA_USB
        {0x209100aeL, 1},   // CFG-MSGOUT-NMEA_ID_RMC_USB
        {0x209100d6L, 1},   // CFG-MSGOUT-NMEA_ID_GST_USB
        {0x209102a7L, 1},   // CFG-MSGOUT-UBX_RXM_RAWX_USB
        {0x20910234L, 1},   // CFG-MSGOUT-UBX_RXM_SFRBX_USB
    };
    private static final long[][] L_KEYS = {
        {0x10780001L, 1},   // CFG-USBOUTPROT-UBX
        {0x10780002L, 1},   // CFG-USBOUTPROT-NMEA
        {0x10770004L, 1},   // CFG-USBINPROT-RTCM3X
    };

    public static byte[] enableUsbLogging() {
        ByteArrayOutputStream p = new ByteArrayOutputStream();
        p.write(0); p.write(0x01); p.write(0); p.write(0);   // version 0, RAM layer, reserved
        for (long[] k : L_KEYS) { u4(p, k[0]); p.write((int) k[1]); }
        for (long[] k : U1_KEYS) { u4(p, k[0]); p.write((int) k[1]); }
        return frame(0x06, 0x8A, p.toByteArray());
    }

    private static void u4(ByteArrayOutputStream o, long v) {
        for (int i = 0; i < 4; i++) o.write((int) ((v >> (8 * i)) & 0xff));
    }

    public static byte[] frame(int cls, int id, byte[] payload) {
        byte[] f = new byte[8 + payload.length];
        f[0] = (byte) 0xB5; f[1] = 0x62; f[2] = (byte) cls; f[3] = (byte) id;
        f[4] = (byte) (payload.length & 0xff); f[5] = (byte) (payload.length >> 8);
        System.arraycopy(payload, 0, f, 6, payload.length);
        int a = 0, b = 0;
        for (int i = 2; i < 6 + payload.length; i++) { a = (a + (f[i] & 0xff)) & 0xff; b = (b + a) & 0xff; }
        f[6 + payload.length] = (byte) a; f[7 + payload.length] = (byte) b;
        return f;
    }
}
