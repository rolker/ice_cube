package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class RefOccupationTest {
    static final long S = 1_000_000_000L;

    static Epoch ep(long ns, int q) { return new Epoch(ns, 0, 10, 20, 100 + ns / 1e12, 1, q); }

    @Test public void completesAfterDurationOfFixedEpochs() {
        RefOccupation r = new RefOccupation(0, 10 * S);
        for (int i = 1; i < 10; i++) assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(i * S, 4)));
        assertFalse(r.done());
        assertEquals(RefOccupation.Change.DONE, r.onEpoch(ep(10 * S, 4)));
        assertTrue(r.done());
        assertEquals(10, r.epochs());
        assertEquals(10, r.average().n);
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(11 * S, 4)));   // ignored once done
    }

    @Test public void pausesWhenNotFixedForMoreThanTwoSeconds() {
        RefOccupation r = new RefOccupation(0, 5 * S);
        r.onEpoch(ep(S, 4));
        r.onEpoch(ep(2 * S, 4));                                       // 2 s counted
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(3 * S, 5)));
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(4 * S, 5)));   // exactly 2 s: not yet
        assertEquals(RefOccupation.Change.PAUSED, r.onEpoch(ep(5 * S, 5)));
        assertTrue(r.pausedAt(5 * S));
        assertEquals(RefOccupation.Change.NONE, r.onEpoch(ep(6 * S, 5)));
        assertEquals(RefOccupation.Change.RESUMED, r.onEpoch(ep(9 * S, 4)));   // gap not counted
        assertEquals(2.0, r.activeSeconds(), 1e-9);
        assertEquals(1, r.pauses());
        r.onEpoch(ep(10 * S, 4));
        r.onEpoch(ep(11 * S, 4));
        assertEquals(RefOccupation.Change.DONE, r.onEpoch(ep(12 * S, 4)));
        assertEquals(6, r.epochs());   // float epochs are not averaged
        assertEquals(4, r.average().quality);
    }

    @Test public void silentGpsCountsAsPause() {
        RefOccupation r = new RefOccupation(0, 30 * S);
        r.onEpoch(ep(S, 4));
        assertFalse(r.pausedAt(2 * S));
        assertTrue(r.pausedAt(4 * S));
        assertEquals(RefOccupation.Change.RESUMED, r.onEpoch(ep(10 * S, 4)));
        assertEquals(1.0, r.activeSeconds(), 1e-9);
        assertEquals(29.0, r.remainingSeconds(), 1e-9);
    }
}
