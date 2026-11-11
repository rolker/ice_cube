package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.junit.Test;

public class StationArpTest {
    /** Big-endian bit writer for synthetic messages. */
    static final class W {
        final List<Boolean> bits = new ArrayList<>();
        W put(long v, int n) { for (int i = n - 1; i >= 0; i--) bits.add(((v >> i) & 1) != 0); return this; }
        byte[] bytes() {
            byte[] b = new byte[(bits.size() + 7) / 8];
            for (int i = 0; i < bits.size(); i++) if (bits.get(i)) b[i >> 3] |= (byte) (0x80 >> (i & 7));
            return b;
        }
    }

    /** A 1005 or 1006 payload for an ECEF position in metres. */
    static byte[] payload(int type, int id, double x, double y, double z, double antennaHeight) {
        W w = new W().put(type, 12).put(id, 12).put(0, 6).put(0b1010, 4)
                .put(Math.round(x * 1e4) & ((1L << 38) - 1), 38).put(0, 2)
                .put(Math.round(y * 1e4) & ((1L << 38) - 1), 38).put(0, 2)
                .put(Math.round(z * 1e4) & ((1L << 38) - 1), 38);
        if (type == 1006) w.put(Math.round(antennaHeight * 1e4), 16);
        return w.bytes();
    }

    static StationArp decodeFrame(byte[] frame) {
        return StationArp.decode(frame, 3, frame.length - 6);
    }

    @Test public void standardExample1005() {
        StationArp s = decodeFrame(Rtcm3ParserTest.MSG_1005);
        assertEquals(1005, s.type);
        assertEquals(2003, s.stationId);
        assertEquals(1114104.5999, s.x, 1e-6);
        assertEquals(-4850729.7108, s.y, 1e-6);
        assertEquals(3975521.4643, s.z, 1e-6);
        assertTrue(Double.isNaN(s.antennaHeight));
    }

    @Test public void synthetic1006WithNegativeCoordinates() {
        double[] ecef = LocalFrame.toEcef(-0.5, -0.25, 31.5);   // made-up point near 0N 0E
        byte[] f = Rtcm3Parser.frame(payload(1006, 4095, ecef[0], -ecef[1] - 1234.5678, -ecef[2], 1.8765));
        StationArp s = decodeFrame(f);
        assertEquals(1006, s.type);
        assertEquals(4095, s.stationId);
        assertEquals(ecef[0], s.x, 1e-4);
        assertEquals(-ecef[1] - 1234.5678, s.y, 1e-4);
        assertEquals(-ecef[2], s.z, 1e-4);
        assertEquals(1.8765, s.antennaHeight, 1e-9);
        assertTrue(s.eventText().startsWith("base station=4095 type=1006 ecef="));
    }

    @Test public void parserHandsPayloadToListener() {
        List<StationArp> got = new ArrayList<>();
        Rtcm3Parser p = new Rtcm3Parser(null);
        p.payloads = (type, b, off, len) -> { if (type == 1005) got.add(StationArp.decode(b, off, len)); };
        byte[] other = Rtcm3ParserTest.message(1077, 50, 3);
        p.feed(other, other.length);
        p.feed(Rtcm3ParserTest.MSG_1005, Rtcm3ParserTest.MSG_1005.length);
        assertEquals(1, got.size());
        assertEquals(2003, got.get(0).stationId);
    }

    @Test public void otherTypesAndShortPayloadsAreIgnored() {
        byte[] f = Rtcm3ParserTest.message(1077, 30, 1);
        assertNull(decodeFrame(f));
        assertNull(StationArp.decode(new byte[] {(byte) 0x3E, (byte) 0xD0}, 0, 2));
    }

    @Test public void watchReportsFirstThenMovesOnce() {
        BaseWatch w = new BaseWatch();
        byte[] a = payload(1005, 7, 6378000.0, 1000.0, 2000.0, 0);
        byte[] b = payload(1006, 7, 6378000.004, 1000.0, 2000.0, 1.5);
        byte[] moved = payload(1005, 7, 6378000.0, 1000.03, 2000.0, 0);
        List<String> e1 = w.onStation(StationArp.decode(a, 0, a.length));
        assertEquals(1, e1.size());
        assertTrue(e1.get(0).startsWith("base station=7 type=1005"));
        List<String> e2 = w.onStation(StationArp.decode(b, 0, b.length));   // first 1006, within 0.01 m
        assertEquals(1, e2.size());
        assertNull(w.warning());
        assertTrue(w.onStation(StationArp.decode(a, 0, a.length)).isEmpty());
        List<String> e3 = w.onStation(StationArp.decode(moved, 0, moved.length));
        assertEquals(1, e3.size());
        assertTrue(e3.get(0).startsWith("base moved 0.0300 m"));
        assertTrue(w.onStation(StationArp.decode(moved, 0, moved.length)).isEmpty());   // not repeated
        byte[] other = payload(1005, 8, 6378000.0, 1000.0, 2000.0, 0);
        List<String> e4 = w.onStation(StationArp.decode(other, 0, other.length));
        assertTrue(e4.get(0).startsWith("base moved: station ID 7 -> 8"));
        assertTrue(w.warning().contains("station ID"));
    }

    @Test public void layerCheckAgainstArp() {
        double lat = 0.12, lon = -0.34, h = 45.678;   // made-up base near 0N 0E
        double[] x = LocalFrame.toEcef(lat, lon, h);
        byte[] p = payload(1005, 1, x[0], x[1], x[2], 0);
        StationArp arp = StationArp.decode(p, 0, p.length);
        BaseCheck same = BaseCheck.compare(lon, lat, h, arp);
        assertTrue(same.ok);
        assertEquals(0, same.dE, 2e-4);
        assertEquals(0, same.dU, 2e-4);
        assertTrue(same.describe().startsWith("layers match base"));

        // Layer point 0.3 m south and 0.1 m lower than the ARP.
        LocalFrame f = new LocalFrame(lat, lon, h);
        double[] shifted = f.toGeodetic(0, -0.3, -0.1);
        BaseCheck off = BaseCheck.compare(shifted[1], shifted[0], shifted[2], arp);
        assertTrue(!off.ok);
        assertEquals(0.3, off.dN, 1e-3);
        assertEquals(0.1, off.dU, 1e-3);
        assertTrue(off.describe().startsWith("map layers are not in this base's frame: dE"));

        // Only vertical off by 0.06 m.
        assertTrue(!BaseCheck.compare(lon, lat, h - 0.06, arp).ok);
        // No height in the layer: horizontal check only.
        BaseCheck noH = BaseCheck.compare(lon, lat, Double.NaN, arp);
        assertTrue(noH.ok);
        assertTrue(Double.isNaN(noH.dU));
    }
}
