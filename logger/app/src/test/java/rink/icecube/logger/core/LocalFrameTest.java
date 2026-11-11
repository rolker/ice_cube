package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class LocalFrameTest {
    @Test public void originIsZero() {
        LocalFrame f = new LocalFrame(0.5, 0.5, 12.0);
        double[] e = f.toEnu(0.5, 0.5, 12.0);
        assertEquals(0, e[0], 1e-6);
        assertEquals(0, e[1], 1e-6);
        assertEquals(0, e[2], 1e-6);
    }

    @Test public void thousandthOfADegreeAtTheEquator() {
        LocalFrame f = new LocalFrame(0, 0, 0);
        double[] n = f.toEnu(0.001, 0, 0);
        assertEquals(110.574, n[1], 110.6 * 0.005);   // meridian arc at the equator
        assertEquals(0, n[0], 1e-6);
        double[] e = f.toEnu(0, 0.001, 0);
        assertEquals(111.319, e[0], 111.3 * 0.005);   // equatorial arc
        assertEquals(0, e[1], 1e-6);
        double[] u = f.toEnu(0, 0, 7.5);
        assertEquals(7.5, u[2], 1e-6);
    }

    @Test public void roundTrip() {
        double[][] origins = {{0.3, -0.2, 5}, {-45.5, 170.25, 300}, {80.1, 120.0, -20}};
        for (double[] o : origins) {
            LocalFrame f = new LocalFrame(o[0], o[1], o[2]);
            double[] p = {o[0] + 0.0123, o[1] - 0.0456, o[2] + 3.21};
            double[] enu = f.toEnu(p[0], p[1], p[2]);
            double[] back = f.toGeodetic(enu[0], enu[1], enu[2]);
            assertEquals(p[0], back[0], 1e-10);
            assertEquals(p[1], back[1], 1e-10);
            assertEquals(p[2], back[2], 1e-5);
        }
    }

    @Test public void ecefRoundTrip() {
        double[] x = LocalFrame.toEcef(-12.5, 33.25, 1234.5);
        double[] g = LocalFrame.fromEcef(x[0], x[1], x[2]);
        assertEquals(-12.5, g[0], 1e-11);
        assertEquals(33.25, g[1], 1e-11);
        assertEquals(1234.5, g[2], 1e-5);
        double[] eq = LocalFrame.toEcef(0, 0, 0);
        assertEquals(LocalFrame.A, eq[0], 1e-6);
    }

    @Test public void nanHeightMeansOriginHeight() {
        LocalFrame f = new LocalFrame(1, 1, 50);
        assertEquals(0, f.toEnu(1, 1, Double.NaN)[2], 1e-6);
    }
}
