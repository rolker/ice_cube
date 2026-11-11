package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import org.junit.Test;

public class LimitTest {
    @Test public void defaults() {
        assertEquals(10, Limit.SETTLE_S.def, 0);
        assertEquals(1.0, Limit.JUMP_M.def, 0);
        assertEquals(180, Limit.TILT_COVER_DEG.def, 0);
        assertEquals(2, Limit.TILT_RMS_CM.def, 0);
        assertEquals(5, Limit.TILT_MIN_DEG.def, 0);
        assertEquals(35, Limit.TILT_MAX_DEG.def, 0);
        assertEquals(5, Limit.TILT_SIGMA_CM.def, 0);
        assertEquals(3, Limit.TILT_IMU_DEG.def, 0);
        assertEquals(2, Limit.LEAN_SCATTER_CM.def, 0);
    }

    @Test public void parseChecksFormatAndRange() {
        assertEquals(0, Limit.SETTLE_S.parse("0"), 0);
        assertEquals(60, Limit.SETTLE_S.parse(" 60 "), 0);
        assertEquals(1.25, Limit.JUMP_M.parse("1,25"), 1e-12);
        for (String bad : new String[] {"61", "-1", "1.5", "", "x"}) {
            try { Limit.SETTLE_S.parse(bad); fail(bad); } catch (IllegalArgumentException expected) { }
        }
        try { Limit.JUMP_M.parse("0.05"); fail(); } catch (IllegalArgumentException e) {
            assertEquals("must be 0.10 to 10.00 m", e.getMessage());
        }
        assertEquals(10, Limit.SETTLE_S.orDefault("abc"), 0);
        assertEquals("1.00", Limit.JUMP_M.format(1));
    }
}
