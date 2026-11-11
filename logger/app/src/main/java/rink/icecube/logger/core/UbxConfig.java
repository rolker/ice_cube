package rink.icecube.logger.core;

import java.io.ByteArrayOutputStream;

/**
 * Builds a UBX-CFG-VALSET message that turns on the logged messages (GGA, RMC, GST,
 * RXM-RAWX, RXM-SFRBX) and RTCM3 input on one receiver port. It writes to the RAM layer
 * only, so a power cycle restores the receiver's saved settings.
 */
public final class UbxConfig {
    private static final long[][] USB_KEYS = {
        {0x10780001L, 1},   // CFG-USBOUTPROT-UBX
        {0x10780002L, 1},   // CFG-USBOUTPROT-NMEA
        {0x10770004L, 1},   // CFG-USBINPROT-RTCM3X
        {0x209100bdL, 1},   // CFG-MSGOUT-NMEA_ID_GGA_USB
        {0x209100aeL, 1},   // CFG-MSGOUT-NMEA_ID_RMC_USB
        {0x209100d6L, 1},   // CFG-MSGOUT-NMEA_ID_GST_USB
        {0x209102a7L, 1},   // CFG-MSGOUT-UBX_RXM_RAWX_USB
        {0x20910234L, 1},   // CFG-MSGOUT-UBX_RXM_SFRBX_USB
    };
    /** UART1 is the port the RTK Express's ESP32 (and so Bluetooth) talks to. */
    private static final long[][] UART1_KEYS = {
        {0x10740001L, 1},   // CFG-UART1OUTPROT-UBX
        {0x10740002L, 1},   // CFG-UART1OUTPROT-NMEA
        {0x10730004L, 1},   // CFG-UART1INPROT-RTCM3X
        {0x209100bbL, 1},   // CFG-MSGOUT-NMEA_ID_GGA_UART1
        {0x209100acL, 1},   // CFG-MSGOUT-NMEA_ID_RMC_UART1
        {0x209100d4L, 1},   // CFG-MSGOUT-NMEA_ID_GST_UART1
        {0x209102a5L, 1},   // CFG-MSGOUT-UBX_RXM_RAWX_UART1
        {0x20910232L, 1},   // CFG-MSGOUT-UBX_RXM_SFRBX_UART1
    };

    public static byte[] enableUsbLogging() { return valset(USB_KEYS); }

    public static byte[] enableUart1Logging() { return valset(UART1_KEYS); }

    private static byte[] valset(long[][] keys) {
        ByteArrayOutputStream p = new ByteArrayOutputStream();
        p.write(0); p.write(0x01); p.write(0); p.write(0);   // version 0, RAM layer, reserved
        for (long[] k : keys) { u4(p, k[0]); p.write((int) k[1]); }
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
