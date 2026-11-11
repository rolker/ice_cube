package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class GgaTest {
    @Test public void heightsFromAltitudeAndSeparation() {
        Gga g = Gga.parse("$GNGGA,120000.00,0030.00000,N,00015.00000,E,4,20,0.6,25.123,M,17.250,M,1.0,0000*00");
        assertEquals(0.5, g.lat, 1e-9);
        assertEquals(0.25, g.lon, 1e-9);
        assertEquals(25.123, g.alt, 1e-9);
        assertEquals(17.250, g.sep, 1e-9);
        assertEquals(42.373, g.height, 1e-9);
        assertEquals(4, g.quality);
    }

    @Test public void noSeparationMeansNoEllipsoidalHeight() {
        Gga g = Gga.parse("$GNGGA,120000.00,0030.00000,S,00015.00000,W,1,08,1.0,25.0,M,,M,,*00");
        assertEquals(-0.5, g.lat, 1e-9);
        assertEquals(25.0, g.alt, 1e-9);
        assertTrue(Double.isNaN(g.height));
    }
}
