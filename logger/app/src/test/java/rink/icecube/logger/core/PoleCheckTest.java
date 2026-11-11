package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

public class PoleCheckTest {
    static final double LAT = 10.0, LON = 20.0, M_LAT = 1 / 110_600.0;

    static PointRow ref(double dn, double hGround) {
        PointRow r = new PointRow();
        r.cls = PointRow.REF;
        r.lat = LAT + dn * M_LAT;
        r.lon = LON;
        r.hAnt = hGround + 1.8;
        r.hGround = hGround;
        return r;
    }

    @Test public void matchesWithinThirtyCentimetres() {
        PointRow now = ref(0, 50);
        List<PointRow> earlier = Arrays.asList(ref(0.29, 50), ref(0.31, 50), ref(-0.1, 50), ref(5, 50));
        assertEquals(2, PoleCheck.matches(now, earlier).size());
    }

    @Test public void comparesWithMostRecentAndMean() {
        PointRow now = ref(0, 50.000);
        List<PointRow> earlier = Arrays.asList(ref(0.05, 50.010), ref(3, 40), ref(-0.05, 49.990), ref(0.02, 50.020));
        PoleCheck c = PoleCheck.check(now, earlier, 0.03);
        assertEquals(3, c.visits);
        assertEquals(-0.020, c.diffLast, 1e-9);
        assertEquals(-0.0066667, c.diffMean, 1e-6);   // mean of 50.010, 49.990, 50.020
        assertTrue(c.ok);
        assertTrue(c.describe().startsWith("pole check OK (±2.0 cm"));
    }

    @Test public void wrongPoleFails() {
        PointRow now = ref(0, 50.100);   // e.g. pole set 0.1 m too short
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 50.0)), 0.03);
        assertFalse(c.ok);
        assertEquals("pole check: +10.0 cm vs last visit", c.describe());
        assertTrue(PoleCheck.check(ref(0, 50.03), Arrays.asList(ref(0, 50.0)), 0.03).ok);   // boundary
    }

    @Test public void noMatchOrNoHeight() {
        assertNull(PoleCheck.check(ref(0, 50), Arrays.asList(ref(1, 50)), 0.03));
        PointRow noH = ref(0, 50);
        noH.hGround = Double.NaN;
        assertNull(PoleCheck.check(noH, Arrays.asList(ref(0, 50)), 0.03));
        PointRow earlierNoH = ref(0, 50);
        earlierNoH.hGround = Double.NaN;
        assertNull(PoleCheck.check(ref(0, 50), Arrays.asList(earlierNoH), 0.03));
    }
}
