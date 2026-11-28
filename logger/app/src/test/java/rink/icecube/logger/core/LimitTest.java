package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public class LimitTest {
    @Test public void defaults() {
        assertEquals(120, Limit.SETTLE_S.def, 0);   // 0.11.0 (#15): was 10, now a young-fix warning
        assertEquals(20, Limit.FLOAT_SPELL_S.def, 0);
        assertEquals(1.0, Limit.JUMP_M.def, 0);
        assertEquals(180, Limit.TILT_COVER_DEG.def, 0);
        assertEquals(2, Limit.TILT_RMS_CM.def, 0);
        assertEquals(5, Limit.TILT_MIN_DEG.def, 0);
        assertEquals(35, Limit.TILT_MAX_DEG.def, 0);
        assertEquals(5, Limit.TILT_SIGMA_CM.def, 0);
        assertEquals(3, Limit.TILT_IMU_DEG.def, 0);
        assertEquals(2, Limit.LEAN_SCATTER_CM.def, 0);
        assertEquals(2, Limit.PLACE_CM.def, 0);      // 0.12.0 (#23)
        assertEquals(1.5, Limit.STEADY_CM.def, 0);
        assertEquals(2, Limit.AUTO_PLUMB_DEG.def, 0);   // 0.13.0 (#35): the owner's 2°, 2 s, 2 cm
        assertEquals(2, Limit.AUTO_HOLD_S.def, 0);
        assertEquals(2, Limit.AUTO_STILL_CM.def, 0);
    }

    @Test public void autoCaptureLimitRanges() {
        assertEquals(0.5, Limit.AUTO_PLUMB_DEG.parse("0.5"), 0);
        assertEquals(10, Limit.AUTO_PLUMB_DEG.parse("10"), 0);
        assertEquals("must be 0.5 to 10.0 °", problem(Limit.AUTO_PLUMB_DEG, "10.5"));
        assertEquals("must be 0.5 to 30.0 s", problem(Limit.AUTO_HOLD_S, "0.4"));
        assertEquals(0.2, Limit.AUTO_STILL_CM.parse("0,2"), 1e-12);
        assertEquals("must be 0.2 to 20.0 cm", problem(Limit.AUTO_STILL_CM, "0.1"));
        assertEquals("enter a number, up to 1 decimal", problem(Limit.AUTO_HOLD_S, "1.25"));
    }

    @Test public void poleCueLimitsAcceptZeroAsOff() {
        assertEquals(0, Limit.PLACE_CM.parse("0"), 0);
        assertEquals(0, Limit.STEADY_CM.parse("0"), 0);
        assertEquals(1.5, Limit.STEADY_CM.parse("1.5"), 0);
        assertEquals("must be 0.0 to 20.0 cm", problem(Limit.PLACE_CM, "21"));
        assertEquals("enter a number, up to 1 decimal", problem(Limit.STEADY_CM, "1.25"));
    }

    @Test public void coverageDefaultsMatchCoverageParams() {
        Coverage.Params p = Coverage.Params.defaults();
        assertEquals(p.target, Limit.COV_TARGET_CM.def / 100, 1e-12);
        assertEquals(p.cell, Limit.COV_CELL_M.def, 0);
        assertEquals(p.captureRadius, Limit.COV_RADIUS_M.def, 0);
        assertEquals(p.reach, Limit.COV_REACH_M.def, 0);
        assertEquals(p.ground, Limit.COV_GROUND_CM.def / 100, 1e-12);
        assertEquals(p.wander, Limit.COV_WANDER_CM.def / 100, 1e-12);
        assertEquals(0, Limit.COV_GROUND_CM.parse("0"), 0);
        assertEquals(0.75, Limit.COV_RADIUS_M.parse("0.75"), 0);
    }

    @Test public void parseChecksFormatAndRange() {
        assertEquals(0, Limit.SETTLE_S.parse("0"), 0);
        assertEquals(300, Limit.SETTLE_S.parse(" 300 "), 0);
        assertEquals(1.25, Limit.JUMP_M.parse("1,25"), 1e-12);
        for (String bad : new String[] {"301", "-1", "1.5", "", "x"}) {
            try { Limit.SETTLE_S.parse(bad); fail(bad); } catch (IllegalArgumentException expected) { }
        }
        try { Limit.JUMP_M.parse("0.05"); fail(); } catch (IllegalArgumentException e) {
            assertEquals("must be 0.10 to 10.00 m", e.getMessage());
        }
        assertEquals(120, Limit.SETTLE_S.orDefault("abc"), 0);
        assertEquals("1.00", Limit.JUMP_M.format(1));
    }

    @Test public void coverageRadiusAndReachAtMostFiftyCells() {
        Coverage.Params d = Coverage.Params.defaults();
        assertEquals("", Limit.coverageProblem(d.cell, d.captureRadius, d.reach));
        assertEquals("", Limit.coverageProblem(0.1, 5.0, 5.0));          // exactly 50 spacings
        assertEquals("", Limit.coverageProblem(0.4, 0.75, 20));          // the reach maximum at 0.4 m
        assertEquals("reach 20.00 m is over 50 grid spacings (5.00 m at 0.10 m spacing)",
                Limit.coverageProblem(0.1, 0.75, 20));
        assertEquals("capture radius 5.00 m is over 50 grid spacings (2.50 m at 0.05 m spacing)",
                Limit.coverageProblem(0.05, 5, 1));
        assertEquals("capture radius 0.50 m is under 0.71 grid spacings (1.42 m at 2.00 m spacing):"
                + " some captures would feed no node", Limit.coverageProblem(2, 0.5, 2));
        assertEquals("", Limit.coverageProblem(1, 0.71, 2));
    }

    @Test public void aFieldThatDoesNotParseCountsWithItsStoredValue() {
        // #6 review round 2: the radius "9" does not parse, so the reach 20 m is checked
        // against the 0.1 m spacing with the stored radius, not skipped
        assertEquals("reach 20.00 m is over 50 grid spacings (5.00 m at 0.10 m spacing)",
                Limit.coverageProblem("0.1", "9", "20", 0.5, 0.75, 2.0));
        assertEquals("", Limit.coverageProblem("0.1", "9", "2", 0.5, 0.75, 2.0));
        // the stored radius itself breaks the bounds at the new spacing
        assertEquals("reach 6.00 m is over 50 grid spacings (5.00 m at 0.10 m spacing)",
                Limit.coverageProblem("0.1", "0.75", "x", 0.5, 0.75, 6.0));
        assertEquals("capture radius 0.50 m is under 0.71 grid spacings (1.42 m at 2.00 m spacing):"
                + " some captures would feed no node", Limit.coverageProblem("2", "abc", null, 0.5, 0.5, 2.0));
        assertEquals("", Limit.coverageProblem(null, null, null, 0.5, 0.75, 2.0));   // nothing built yet: stored values
    }

    @Test public void coverageAbsoluteRanges() {
        assertEquals("must be 0.10 to 2.00 m", problem(Limit.COV_CELL_M, "2.5"));
        assertEquals("must be 0.10 to 2.00 m", problem(Limit.COV_CELL_M, "0.05"));
        assertEquals("must be 0.10 to 5.00 m", problem(Limit.COV_RADIUS_M, "6"));
        assertEquals("must be 0.10 to 20.00 m", problem(Limit.COV_REACH_M, "25"));
    }

    @Test public void boundCoverageClampsOldValuesOnly() {
        Coverage.Params d = Coverage.Params.defaults();
        assertTrue(d == Limit.boundCoverage(d));
        Coverage.Params p = Limit.boundCoverage(new Coverage.Params(0.1, 10, 20, 0.03, 0.015, 0.015));
        assertEquals(5.0, p.captureRadius, 1e-12);
        assertEquals(5.0, p.reach, 1e-12);
        assertEquals(0.1, p.cell, 0);
        assertEquals("", Limit.coverageProblem(p.cell, p.captureRadius, p.reach));
        Coverage.Params q = Limit.boundCoverage(new Coverage.Params(2, 0.1, 2, 0.03, 0.015, 0.015));
        assertEquals(Math.sqrt(0.5) * 2, q.captureRadius, 1e-12);
    }

    private static String problem(Limit l, String text) {
        try { l.parse(text); return ""; } catch (IllegalArgumentException e) { return e.getMessage(); }
    }
}
