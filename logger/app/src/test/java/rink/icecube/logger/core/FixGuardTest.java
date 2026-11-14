package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class FixGuardTest {
    static final long S = 1_000_000_000L, Q = S / 4;   // 4 Hz GGA
    static final LocalFrame F = new LocalFrame(10, 20, 100);

    /** An epoch at t (ns) at local east/north/up (m). */
    static Epoch ep(long t, int q, double e, double n, double u) {
        double[] g = F.toGeodetic(e, n, u);
        return new Epoch(t, t / 1e9, g[0], g[1], g[2], 1, q);
    }

    static FixGuard guard() { return new FixGuard(10 * S, 1.0); }

    @Test public void floatToFixStartsAStretchWithItsJump() {
        FixGuard g = guard();
        assertNull(g.onEpoch(ep(0, 5, 0, 0, 0)));
        assertNull(g.onEpoch(ep(Q, 5, 0.01, 0, 0)));
        FixGuard.Stretch s = g.onEpoch(ep(2 * Q, 4, 0.01, 0.3, -0.4));
        assertNotNull(s);
        assertEquals(0.5, s.jump, 1e-4);   // 3D: 0.3 north, 0.4 down
        assertFalse(s.suspect);
        assertFalse(s.withinFix);
        assertEquals(1, g.count());
        assertTrue(s.eventText().startsWith("fix stretch start jump=0.500 m"));
        // following fixed epochs continue it
        assertNull(g.onEpoch(ep(3 * Q, 4, 0.02, 0.3, -0.4)));
        assertEquals(s, g.current());
    }

    @Test public void settlingBlocksForTheSettleTime() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(S, 4, 0, 0, 0));
        assertTrue(g.settling(S));
        assertTrue(g.settling(S + 9 * S));
        assertEquals(1, g.settleRemainingSeconds(S + 9 * S));
        assertEquals(10, g.settleRemainingSeconds(S));
        assertFalse(g.settling(S + 10 * S));
        assertEquals(0, g.settleRemainingSeconds(11 * S));
        // a fix drop ends the stretch: nothing settling, no current stretch
        g.onEpoch(ep(2 * S, 5, 0, 0, 0));
        assertNull(g.current());
        assertFalse(g.settling(2 * S));
        assertNotNull(g.last());
    }

    @Test public void zeroSettleTimeNeverSettles() {
        FixGuard g = new FixGuard(0, 1.0);
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(Q, 4, 0, 0, 0));
        assertFalse(g.settling(Q));
    }

    @Test public void bigJumpMakesTheStretchSuspect() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        FixGuard.Stretch s = g.onEpoch(ep(Q, 4, 0.5, 0.5, -1.4));   // 1.66 m, like the field's false fixes
        assertTrue(s.suspect);
        assertTrue(s.eventText().endsWith("suspect"));
        g.onEpoch(ep(2 * Q, 4, 0.5, 0.5, -1.4));
        assertTrue(g.current().suspect);   // the whole stretch
        // threshold is strict and a setting
        FixGuard h = new FixGuard(0, 2.0);
        h.onEpoch(ep(0, 5, 0, 0, 0));
        assertFalse(h.onEpoch(ep(Q, 4, 0, 0, 1.9)).suspect);
    }

    @Test public void jumpBetweenFixedEpochsStartsANewStretch() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        FixGuard.Stretch a = g.onEpoch(ep(Q, 4, 0, 0, 0));
        for (int i = 2; i < 60; i++) assertNull(g.onEpoch(ep(i * Q, 4, 0.3 * i / 4.0, 0, 0)));   // walking 0.3 m/s
        assertFalse(g.settling(60 * Q));
        // a 1.2 m step between two fixed epochs 0.25 s apart: new stretch, settling again, suspect
        FixGuard.Stretch b = g.onEpoch(ep(60 * Q, 4, 0.3 * 59 / 4.0, 1.2, 0));
        assertNotNull(b);
        assertTrue(b.withinFix);
        assertEquals(1.2, b.jump, 0.01);
        assertTrue(b.suspect);
        assertTrue(g.settling(60 * Q + S));
        assertEquals(2, g.count());
        assertTrue(b != a);
        // 0.45 m in 0.25 s (brisk walking) is the same stretch
        assertNull(g.onEpoch(ep(61 * Q, 4, 0.3 * 59 / 4.0 + 0.45, 1.2, 0)));
    }

    @Test public void slowRatesAllowMoreMotionBetweenFixedEpochs() {
        assertEquals(0.5, FixGuard.inFixLimit(0.25), 1e-12);
        assertEquals(2.0, FixGuard.inFixLimit(1.0), 1e-12);
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(S, 4, 0, 0, 0));
        assertNull(g.onEpoch(ep(2 * S, 4, 1.5, 0, 0)));   // 1.5 m in 1 s: walking at 1 Hz
        assertNotNull(g.onEpoch(ep(3 * S, 4, 4.0, 0, 0)));
    }

    @Test public void gapStartsAStretchWithUnknownJump() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 4, 0, 0, 0));
        FixGuard.Stretch s = g.onEpoch(ep(5 * S, 4, 3, 0, 0));   // GPS quiet for 5 s
        assertNotNull(s);
        assertTrue(Double.isNaN(s.jump));
        assertFalse(s.suspect);
        assertTrue(g.settling(5 * S));
        assertTrue(s.eventText().contains("jump=unknown"));
    }

    @Test public void firstEpochFixedHasUnknownJumpAndNoPositionCountsAsNotFixed() {
        FixGuard g = guard();
        FixGuard.Stretch s = g.onEpoch(ep(0, 4, 0, 0, 0));
        assertTrue(Double.isNaN(s.jump));
        assertNull(g.onEpoch(new Epoch(Q, 0, Double.NaN, Double.NaN, Double.NaN, Double.NaN, 0)));
        assertNull(g.current());
        // back to fixed: jump from the last position (0.5 s old)
        FixGuard.Stretch t = g.onEpoch(ep(2 * Q, 4, 0, 0.2, 0));
        assertEquals(0.2, t.jump, 1e-4);
    }

    @Test public void distanceIsHorizontalWithoutHeight() {
        Epoch a = ep(0, 4, 0, 0, 0), b = ep(0, 4, 3, 4, 10);
        Epoch bNoH = new Epoch(0, 0, b.lat, b.lon, Double.NaN, 1, 4);
        assertEquals(Math.sqrt(125), FixGuard.distance(a, b), 1e-4);
        assertEquals(5, FixGuard.distance(a, bNoH), 1e-3);
    }

    @Test public void refOccupationDoesNotCountSettlingEpochs() {
        RefOccupation r = new RefOccupation(0, 3 * S);
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(S, 4, 0, 0, 0), false));
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(2 * S, 4, 0, 0, 0), false));
        assertEquals(RefOccupation.Change.PAUSED, r.onEpoch(ep(3 * S, 4, 0, 0, 0), false));
        assertEquals(0, r.epochs());
        assertEquals(RefOccupation.Change.RESUMED, r.onEpoch(ep(4 * S, 4, 0, 0, 0), true));
        assertEquals(1, r.epochs());
        assertEquals(0, r.activeSeconds(), 1e-9);
    }
}
