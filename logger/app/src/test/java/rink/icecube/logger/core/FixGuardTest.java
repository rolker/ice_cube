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

    static FixGuard guard() { return new FixGuard(10 * S, 1.0, 20 * S); }

    /** Feeds quality q epochs at 4 Hz from t0 for seconds s at (e, n, u); returns the next time. */
    static long feed(FixGuard g, long t0, double seconds, int q, double e, double n, double u) {
        long t = t0;
        for (; t < t0 + Math.round(seconds * S); t += Q) assertNull(g.onEpoch(ep(t, q, e, n, u)));
        return t;
    }

    @Test public void floatToFixStartsAStretchWithItsJump() {
        FixGuard g = guard();
        assertNull(g.onEpoch(ep(0, 5, 0, 0, 0)));
        assertNull(g.onEpoch(ep(Q, 5, 0.01, 0, 0)));
        FixGuard.Stretch s = g.onEpoch(ep(2 * Q, 4, 0.01, 0.3, -0.4));
        assertNotNull(s);
        assertEquals(0.5, s.jump, 1e-4);   // 3D: 0.3 north, 0.4 down
        assertFalse(s.suspect);
        assertFalse(s.withinFix);
        assertEquals(FixGuard.PRIOR_FLOAT, s.prior);
        assertEquals(0.5, s.floatSpellS, 1e-9);   // float since t=0, fix at 0.5 s
        assertEquals("", s.reason);
        assertEquals(1, g.count());
        assertTrue(s.eventText().startsWith("fix stretch start jump=0.500 m after float"));
        // following fixed epochs continue it
        assertNull(g.onEpoch(ep(3 * Q, 4, 0.02, 0.3, -0.4)));
        assertEquals(s, g.current());
    }

    // 0.11.0 (#15): "settling" (captures held back) became "young" (a warning); same timing
    @Test public void youngForTheYoungTime() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(S, 4, 0, 0, 0));
        assertTrue(g.young(S));
        assertTrue(g.young(S + 9 * S));
        assertEquals(9, g.youngAgeSeconds(S + 9 * S));
        assertEquals(0, g.youngAgeSeconds(S));
        assertFalse(g.young(S + 10 * S));
        assertEquals(-1, g.youngAgeSeconds(11 * S));
        // a fix drop ends the stretch: nothing young, no current stretch
        g.onEpoch(ep(2 * S, 5, 0, 0, 0));
        assertNull(g.current());
        assertFalse(g.young(2 * S));
        assertNotNull(g.last());
    }

    @Test public void zeroYoungTimeIsNeverYoung() {
        FixGuard g = new FixGuard(0, 1.0, 20 * S);
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(Q, 4, 0, 0, 0));
        assertFalse(g.young(Q));
    }

    @Test public void bigJumpMakesTheStretchSuspect() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        FixGuard.Stretch s = g.onEpoch(ep(Q, 4, 0.5, 0.5, -1.4));   // 1.66 m, like the field's false fixes
        assertTrue(s.suspect);
        assertEquals(FixGuard.REASON_JUMP, s.reason);
        assertTrue(s.eventText().endsWith("suspect: jump"));
        g.onEpoch(ep(2 * Q, 4, 0.5, 0.5, -1.4));
        assertTrue(g.current().suspect);   // the whole stretch
        // threshold is strict and a setting
        FixGuard h = new FixGuard(0, 2.0, 20 * S);
        h.onEpoch(ep(0, 5, 0, 0, 0));
        assertFalse(h.onEpoch(ep(Q, 4, 0, 0, 1.9)).suspect);
    }

    @Test public void jumpBetweenFixedEpochsStartsANewStretch() {
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        FixGuard.Stretch a = g.onEpoch(ep(Q, 4, 0, 0, 0));
        for (int i = 2; i < 60; i++) assertNull(g.onEpoch(ep(i * Q, 4, 0.3 * i / 4.0, 0, 0)));   // walking 0.3 m/s
        assertFalse(g.young(60 * Q));   // 14.75 s old, over the 10 s limit
        // a 1.2 m step between two fixed epochs 0.25 s apart: new stretch, young again, suspect
        FixGuard.Stretch b = g.onEpoch(ep(60 * Q, 4, 0.3 * 59 / 4.0, 1.2, 0));
        assertNotNull(b);
        assertTrue(b.withinFix);
        assertEquals(1.2, b.jump, 0.01);
        assertTrue(b.suspect);   // within a fix the jump still counts (from a fixed epoch)
        assertEquals(FixGuard.PRIOR_FIXED_JUMP, b.prior);
        assertTrue(g.young(60 * Q + S));
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
        assertEquals(FixGuard.PRIOR_GAP, s.prior);
        assertTrue(g.young(5 * S));
        assertTrue(s.eventText().contains("jump=unknown after gap"));
    }

    @Test public void firstEpochFixedHasUnknownJumpAndNoPositionCountsAsNotFixed() {
        FixGuard g = guard();
        FixGuard.Stretch s = g.onEpoch(ep(0, 4, 0, 0, 0));
        assertTrue(Double.isNaN(s.jump));
        assertEquals(FixGuard.PRIOR_NONE, s.prior);
        assertFalse(s.suspect);
        assertNull(g.onEpoch(noPos(Q)));
        assertNull(g.current());
        // back to fixed within 2 s and 0.5 m: the same stretch (#15, a short dropout)
        assertNull(g.onEpoch(ep(2 * Q, 4, 0, 0.2, 0)));
        assertEquals(s, g.current());
        // after more than 2 s without position: a new stretch, jump unknown
        for (long t = 3 * Q; t < 3 * Q + 3 * S; t += Q) g.onEpoch(noPos(t));
        FixGuard.Stretch t = g.onEpoch(ep(3 * Q + 3 * S, 4, 0, 0.2, 0));
        assertNotNull(t);
        assertTrue(Double.isNaN(t.jump));
        assertEquals(FixGuard.PRIOR_GAP, t.prior);   // no position for over 2 s
    }

    static Epoch noPos(long t) { return new Epoch(t, t / 1e9, Double.NaN, Double.NaN, Double.NaN, Double.NaN, 0); }

    // #15 parity with soundings.fix_stretches (test_soundings test_dropout_parity_with_logger_fixguard):
    // a dropout of 2 s or less inside a fix keeps the stretch; a longer one ends it
    @Test public void shortDropoutInsideAFixKeepsTheStretch() {
        for (int q : new int[] {0, 1, 2}) {   // no position, single, DGPS
            FixGuard g = guard();
            feed(g, 0, 2, 5, 0, 0, 0);
            FixGuard.Stretch a = g.onEpoch(ep(2 * S, 4, 0, 0, 0));
            long t = feed(g, 2 * S + Q, 5, 4, 0, 0, 0);
            for (long u = t; u < t + 7 * Q; u += Q) g.onEpoch(q == 0 ? noPos(u) : ep(u, q, 3, 0, 0));
            assertNull(g.current());
            assertNull(g.onEpoch(ep(t + 7 * Q, 4, 0.1, 0, 0)));   // 2 s after the last fixed epoch: same stretch
            assertEquals(a, g.current());
            assertTrue(g.young(t + 7 * Q));          // the age runs on from the stretch start
            assertEquals(7, (int) a.ageSeconds(t + 7 * Q));
            assertEquals(1, g.count());
            // 3 s: a new stretch
            long v = feed(g, t + 8 * Q, 1, 4, 0.1, 0, 0);
            for (long u = v; u < v + 3 * S; u += Q) g.onEpoch(q == 0 ? noPos(u) : ep(u, q, 3, 0, 0));
            FixGuard.Stretch b = g.onEpoch(ep(v + 3 * S, 4, 0.1, 0, 0));
            assertNotNull(b);
            assertEquals(2, g.count());
        }
        // a step over the in-fix limit across a short dropout is a jump inside the fix
        FixGuard g = guard();
        g.onEpoch(ep(0, 5, 0, 0, 0));
        g.onEpoch(ep(Q, 4, 0, 0, 0));
        g.onEpoch(noPos(2 * Q));
        FixGuard.Stretch c = g.onEpoch(ep(3 * Q, 4, 0, 1.2, 0));
        assertNotNull(c);
        assertEquals(FixGuard.PRIOR_FIXED_JUMP, c.prior);
        assertEquals(1.2, c.jump, 0.01);
        assertTrue(c.suspect);
        // a float epoch ends the stretch, however short
        FixGuard h = guard();
        h.onEpoch(ep(0, 4, 0, 0, 0));
        h.onEpoch(ep(Q, 5, 0, 0, 0));
        assertNotNull(h.onEpoch(ep(2 * Q, 4, 0, 0, 0)));
    }

    /** The 2026-11-21 shape with a one-epoch dropout before the fix: the float spell carries over it. */
    @Test public void floatSpellCarriesOverAShortDropout() {
        for (int q : new int[] {0, 1, 2}) {
            FixGuard g = guard();
            long t = feed(g, 0, 60, 5, 0, 0, 0);
            g.onEpoch(q == 0 ? noPos(t) : ep(t, q, 3, 0, 0));
            FixGuard.Stretch s = g.onEpoch(ep(t + Q, 4, 0, 0.48, 0));
            assertTrue(s.suspect);
            assertEquals(FixGuard.REASON_FLOAT_SPELL, s.reason);
            assertEquals(FixGuard.PRIOR_FLOAT, s.prior);
            assertEquals(60.25, s.floatSpellS, 1e-9);
        }
        // a dropout over 2 s ends the spell
        FixGuard g = guard();
        long t = feed(g, 0, 60, 5, 0, 0, 0);
        t = feed(g, t, 3, 1, 3, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 0, 0.48, 0));
        assertFalse(s.suspect);
        assertEquals(FixGuard.PRIOR_SINGLE, s.prior);
    }

    @Test public void distanceIsHorizontalWithoutHeight() {
        Epoch a = ep(0, 4, 0, 0, 0), b = ep(0, 4, 3, 4, 10);
        Epoch bNoH = new Epoch(0, 0, b.lat, b.lon, Double.NaN, 1, 4);
        assertEquals(Math.sqrt(125), FixGuard.distance(a, b), 1e-4);
        assertEquals(5, FixGuard.distance(a, bNoH), 1e-3);
    }

    // ---- #15 field cases (synthetic, local metres) ----

    /** Case A (2026-11-21): a fix after a long float spell with a small jump was false. */
    @Test public void fixAfterLongFloatSpellIsSuspect() {
        FixGuard g = guard();
        long t = feed(g, 0, 60, 5, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 0, 0.48, 0));
        assertEquals(0.48, s.jump, 1e-4);
        assertTrue(s.suspect);
        assertEquals(FixGuard.REASON_FLOAT_SPELL, s.reason);
        assertEquals(FixGuard.PRIOR_FLOAT, s.prior);
        assertEquals(60, s.floatSpellS, 1e-9);
        assertTrue(s.eventText().endsWith("after float 60 s suspect: float_spell"));
    }

    @Test public void fixAfterShortFloatSpellIsNotSuspect() {
        FixGuard g = guard();
        long t = feed(g, 0, 5, 5, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 0, 0.48, 0));
        assertFalse(s.suspect);
        assertEquals(5, s.floatSpellS, 1e-9);
    }

    @Test public void longFloatAndBigJumpGiveBothReasons() {
        FixGuard g = guard();
        long t = feed(g, 0, 60, 5, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 0, 1.5, 0));
        assertEquals("jump;float_spell", s.reason);
    }

    @Test public void floatSpellRuleCanBeOffAndCountsOnlyFloat() {
        FixGuard g = new FixGuard(10 * S, 1.0, 0);
        long t = feed(g, 0, 60, 5, 0, 0, 0);
        assertFalse(g.onEpoch(ep(t, 4, 0, 0.48, 0)).suspect);
        // single epochs over 2 s break a float run: only the last float run counts
        FixGuard h = guard();
        t = feed(h, 0, 30, 5, 0, 0, 0);
        t = feed(h, t, 3, 1, 0, 0, 0);
        t = feed(h, t, 10, 5, 0, 0, 0);
        FixGuard.Stretch s = h.onEpoch(ep(t, 4, 0, 0.1, 0));
        assertEquals(10, s.floatSpellS, 1e-9);
        assertFalse(s.suspect);
        // 1 s of single epochs does not (#15): the spell runs on from the first float epoch
        FixGuard m = guard();
        t = feed(m, 0, 30, 5, 0, 0, 0);
        t = feed(m, t, 1, 1, 0, 0, 0);
        t = feed(m, t, 10, 5, 0, 0, 0);
        assertEquals(41, m.onEpoch(ep(t, 4, 0, 0.1, 0)).floatSpellS, 1e-9);
        // a gap in the float run restarts it
        FixGuard k = guard();
        t = feed(k, 0, 30, 5, 0, 0, 0);
        t = feed(k, t + 5 * S, 3, 5, 0, 0, 0);
        assertEquals(3, k.onEpoch(ep(t, 4, 0, 0.1, 0)).floatSpellS, 1e-9);
    }

    /** Case B (2026-11-26): a re-fix still 6-9 cm off at 95-113 s is young under the 120 s default. */
    @Test public void refixIsYoungUntil120s() {
        FixGuard g = new FixGuard(Math.round(Limit.SETTLE_S.def * S), Limit.JUMP_M.def,
                Math.round(Limit.FLOAT_SPELL_S.def * S));
        long t = feed(g, 0, 5, 5, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 0, 0, 0));
        feed(g, t + Q, 125, 4, 0, 0, 0.07);
        assertTrue(g.young(s, t + 95 * S));
        assertTrue(g.young(s, t + 113 * S));
        assertEquals(100, g.youngAgeSeconds(t + 100 * S));
        assertFalse(g.young(s, t + 125 * S));
        assertEquals(-1, g.youngAgeSeconds(t + 125 * S));
    }

    /** Case C (session start): the first fix after single epochs jumps metres; unknown, not suspect. */
    @Test public void fixAfterSingleHasUnknownJump() {
        FixGuard g = guard();
        long t = feed(g, 0, 3, 1, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 1.2, 1.5, -1.0));   // ~2.2 m step
        assertTrue(Double.isNaN(s.jump));
        assertFalse(s.suspect);
        assertEquals(FixGuard.PRIOR_SINGLE, s.prior);
        assertTrue(Double.isNaN(s.floatSpellS));
        assertTrue(s.eventText().contains("jump=unknown after single"));
        // DGPS is the same
        FixGuard h = guard();
        t = feed(h, 0, 3, 2, 0, 0, 0);
        assertEquals(FixGuard.PRIOR_SINGLE, h.onEpoch(ep(t, 4, 1.2, 1.5, -1.0)).prior);
    }

    @Test public void fixAfterShortFloatWithBigStepIsSuspectByJump() {
        FixGuard g = guard();
        long t = feed(g, 0, 3, 1, 0, 0, 0);
        t = feed(g, t, 2, 5, 0, 0, 0);
        FixGuard.Stretch s = g.onEpoch(ep(t, 4, 1.2, 1.5, -1.0));
        assertEquals(Math.sqrt(1.44 + 2.25 + 1.0), s.jump, 0.01);
        assertTrue(s.suspect);
        assertEquals(FixGuard.REASON_JUMP, s.reason);
    }

    // 0.11.0 (#15): Survey no longer pauses a ref while the fix is young; RefOccupation keeps
    // its usable flag (an epoch marked unusable is still not counted)
    @Test public void refOccupationDoesNotCountUnusableEpochs() {
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
