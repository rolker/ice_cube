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

    @Test public void nonFiniteCoordinatesNeverMatch() {
        PointRow now = ref(0, 50);
        PointRow infLat = ref(0, 50), infLon = ref(0, 50), nanLat = ref(0, 50);
        infLat.lat = Double.POSITIVE_INFINITY;
        infLon.lon = Double.NEGATIVE_INFINITY;
        nanLat.lat = Double.NaN;
        assertFalse(infLat.hasPosition());
        assertFalse(infLon.hasPosition());
        assertEquals(0, PoleCheck.matches(now, Arrays.asList(infLat, infLon, nanLat)).size());
        assertNull(PoleCheck.check(now, Arrays.asList(infLat, infLon, nanLat), 0.03));
        PointRow infNow = ref(0, 50);
        infNow.lat = Double.POSITIVE_INFINITY;
        assertEquals(0, PoleCheck.matches(infNow, Arrays.asList(ref(0, 50))).size());
    }

    static PointRow ref(double dn, double de, double hGround) {
        PointRow r = ref(dn, hGround);
        r.lon = LON + de / (111_320.0 * Math.cos(Math.toRadians(LAT)));
        return r;
    }

    static PointRow held(double hGround, double sdU) {
        PointRow r = ref(0, hGround);
        r.method = PointRow.OCCUPATION;
        r.sdU = sdU;
        return r;
    }

    @Test public void threeOrMoreVisitsUseTheMedian() {
        PointRow now = ref(0, 50.000);
        List<PointRow> earlier = Arrays.asList(ref(0.05, 50.010), ref(3, 40), ref(-0.05, 49.990), ref(0.02, 50.020));
        PoleCheck c = PoleCheck.check(now, earlier, 0.03);
        assertEquals(3, c.visits);
        assertEquals(PoleCheck.RefKind.MEDIAN, c.refKind);
        assertEquals(50.010, c.reference, 1e-9);
        assertEquals(-0.010, c.diffRef, 1e-9);
        assertEquals(-0.020, c.diffLast, 1e-9);       // kept as information
        assertEquals(-0.0066667, c.diffMean, 1e-6);   // mean of 50.010, 49.990, 50.020
        assertTrue(c.ok);
        assertEquals("pole check OK (±1.0 cm vs median of 3 visits; -2.0 cm vs last visit)", c.describe());
    }

    @Test public void anOutlierAmongThreeDoesNotMoveTheReference() {
        List<PointRow> earlier = Arrays.asList(ref(0, 50.0), ref(0, 50.07), ref(0, 50.001));
        PoleCheck good = PoleCheck.check(ref(0, 50.0), earlier, 0.03);
        assertEquals(50.001, good.reference, 1e-9);
        assertTrue(good.ok);   // the last-visit rule would compare with 50.001 too; the mean would be +2.4 cm
        PoleCheck afterBad = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 50.0), ref(0, 50.001), ref(0, 50.07)), 0.03);
        assertTrue(afterBad.ok);   // the 127 -> 128 pattern: the bad visit is the most recent one
        assertEquals(-0.07, afterBad.diffLast, 1e-9);
    }

    @Test public void evenCountUsesTheMeanOfTheMiddleTwo() {
        assertEquals(2.5, PoleCheck.median(new double[] {4, 1, 3, 2}), 0);
        assertEquals(3, PoleCheck.median(new double[] {5, 1, 3}), 0);
        PoleCheck c = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 50.0), ref(0, 50.01), ref(0, 50.02), ref(0, 50.2)), 0.03);
        assertEquals(50.015, c.reference, 1e-9);
    }

    @Test public void badMiddleVisitFailsAgainstAnAgreeingPair() {
        // visits 0, 0, then +7 cm: the +7 visit fails red against the mean of an agreeing pair
        PoleCheck bad = PoleCheck.check(ref(0, 50.07), Arrays.asList(ref(0, 50.0), ref(0, 50.002)), 0.03);
        assertEquals(PoleCheck.RefKind.PAIR, bad.refKind);
        assertFalse(bad.ok);
        assertFalse(bad.doubtful);
        assertTrue(bad.failed());
        assertEquals(PoleCheck.Colour.RED, PoleCheck.colour(bad, null));
        // then a good one at 0 does not fail: median of 0, 0.2 cm, +7 cm
        PoleCheck next = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 50.0), ref(0, 50.002), ref(0, 50.07)), 0.03);
        assertTrue(next.ok);
    }

    @Test public void singleVisitIsComparedAndNamed() {
        PoleCheck c = PoleCheck.check(ref(0, 50.008), Arrays.asList(ref(0, 50.0)), 0.03);
        assertEquals(PoleCheck.RefKind.SINGLE, c.refKind);
        assertTrue(c.ok);
        assertEquals("pole check OK (±0.8 cm vs 1 earlier visit)", c.describe());
        assertEquals(PoleCheck.Colour.GREEN, PoleCheck.colour(c, null));
        assertTrue(Double.isNaN(c.spread));
    }

    @Test public void twoDisagreeingVisitsMakeADoubtfulReference() {
        List<PointRow> pair = Arrays.asList(ref(0, 50.0), ref(0, 50.035));
        PoleCheck c = PoleCheck.check(ref(0, 50.0595), pair, 0.03);
        assertEquals(PoleCheck.RefKind.PAIR, c.refKind);
        assertEquals(50.0175, c.reference, 1e-9);
        assertEquals(0.035, c.spread, 1e-9);
        assertFalse(c.ok);
        assertTrue(c.doubtful);
        assertFalse(c.failed());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, null));
        assertEquals("pole check: +4.2 cm vs mean of 2 visits that differ by 3.5 cm; shoot again", c.describe());
        PoleCheck within = PoleCheck.check(ref(0, 50.02), pair, 0.03);
        assertTrue(within.ok);
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(within, null));   // the reference is still in doubt
        assertEquals("pole check OK (±0.3 cm vs mean of 2 visits that differ by 3.5 cm; -1.5 cm vs last visit)",
                within.describe());
        PoleCheck agreeing = PoleCheck.check(ref(0, 50.02), Arrays.asList(ref(0, 50.0), ref(0, 50.03)), 0.03);
        assertFalse(agreeing.doubtful);   // exactly the tolerance apart: not doubtful
    }

    static PointRow visit(String session, int n, double hGround) {
        PointRow r = ref(0, hGround);
        r.session = session;
        r.n = n;
        return r;
    }

    @Test public void aRefInSeveralRefsCopiesIsOneVisit() {
        // refs.csv in two storage roots: 50.00 (s1/4) is read twice. Counted twice it would make
        // a median of 50.00 and the 50.07 shot would fail red; once, the pair is doubtful (amber).
        List<PointRow> earlier = Arrays.asList(visit("2026-11-01_10-00-00", 4, 50.00),
                visit("2026-11-02_10-00-00", 7, 50.07), visit("2026-11-01_10-00-00", 4, 50.00));
        PoleCheck c = PoleCheck.check(ref(0, 50.07), earlier, 0.03);
        assertEquals(2, c.visits);
        assertEquals(PoleCheck.RefKind.PAIR, c.refKind);
        assertTrue(c.doubtful);
        assertFalse(c.failed());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, null));
        assertEquals(7, c.last.n);   // the most recent visit, not the copy read last
    }

    @Test public void visitsAreOrderedBySessionThenN() {
        // a second copy read first holds the newer ref: last is still the most recent visit
        List<PointRow> earlier = Arrays.asList(visit("2026-11-02_10-00-00", 3, 50.01),
                visit("2026-11-01_10-00-00", 9, 50.00), visit("2026-11-02_10-00-00", 1, 50.02));
        PoleCheck c = PoleCheck.check(ref(0, 50.0), earlier, 0.03);
        assertEquals(3, c.visits);
        assertEquals("2026-11-02_10-00-00", c.last.session);
        assertEquals(3, c.last.n);
        assertEquals(-0.01, c.diffLast, 1e-9);
    }

    @Test public void evenCountSplitInTheMiddleIsDoubtful() {
        // 2/2 split by 8 cm: the midpoint reference is in doubt, so a good shot is amber, not red
        List<PointRow> split = Arrays.asList(ref(0, 50.0), ref(0, 50.002), ref(0, 50.08), ref(0, 50.082));
        PoleCheck c = PoleCheck.check(ref(0, 50.0), split, 0.03);
        assertEquals(PoleCheck.RefKind.MEDIAN, c.refKind);
        assertEquals(0.078, c.spread, 1e-9);
        assertFalse(c.ok);
        assertTrue(c.doubtful);
        assertFalse(c.failed());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, null));
        assertEquals("pole check: -4.1 cm vs median of 4 visits that disagree by 7.8 cm around it; shoot again",
                c.describe());
        // middle two agree (one outlier each side): not doubtful
        PoleCheck agree = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 49.9), ref(0, 50.0), ref(0, 50.01),
                ref(0, 50.2)), 0.03);
        assertFalse(agree.doubtful);
    }

    @Test public void oddCountWithNoVisitNearTheMedianIsDoubtful() {
        // 0, +5, +10 cm: no visit within 3 cm of the median
        PoleCheck c = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 50.0), ref(0, 50.05), ref(0, 50.10)), 0.03);
        assertEquals(0.05, c.spread, 1e-9);
        assertTrue(c.doubtful);
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, null));
        // one bad visit among three is what the median is for: not doubtful
        PoleCheck one = PoleCheck.check(ref(0, 50.0), Arrays.asList(ref(0, 50.0), ref(0, 50.07), ref(0, 50.001)), 0.03);
        assertFalse(one.doubtful);
        assertEquals(0.001, one.spread, 1e-9);
        assertTrue(Double.isNaN(PoleCheck.spread(new double[] {1})));
    }

    @Test public void wrongPoleFails() {
        PointRow now = ref(0, 50.100);   // e.g. pole set 0.1 m too short
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 50.0)), 0.03);
        assertFalse(c.ok);
        assertEquals("pole check: +10.0 cm vs 1 earlier visit", c.describe());
        assertTrue(PoleCheck.check(ref(0, 50.03), Arrays.asList(ref(0, 50.0)), 0.03).ok);   // boundary
        assertTrue(PoleCheck.check(ref(0, 49.97), Arrays.asList(ref(0, 50.0), ref(0, 50.0), ref(0, 50.0)), 0.03).ok);
    }

    @Test public void placementCue() {
        List<PointRow> earlier = Arrays.asList(ref(0, 0, 50.0), ref(0.002, 0, 50.0), ref(-0.002, 0.001, 50.0));
        PoleCheck off = PoleCheck.check(ref(0, 0.025, 50.0), earlier, 0.03);
        assertEquals(0.025, off.placeOffset, 5e-4);
        PoleCheck.Cues q = PoleCheck.Cues.of(ref(0, 0.025, 50.0), off, 0.02, 0.015);
        assertTrue(q.offMark);
        assertEquals("; pole may be off the mark centre (2.5 cm); re-shoot", q.describe());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(off, q));
        PoleCheck near = PoleCheck.check(ref(0, 0.019, 50.0), earlier, 0.03);
        assertFalse(PoleCheck.Cues.of(ref(0, 0.019, 50.0), near, 0.02, 0.015).offMark);
        assertFalse(PoleCheck.Cues.of(ref(0, 0.025, 50.0), off, 0, 0.015).offMark);   // 0: off
        // one earlier visit: the offset is to that visit
        PoleCheck one = PoleCheck.check(ref(0.025, 0, 50.0), Arrays.asList(ref(0, 0, 50.0)), 0.03);
        assertEquals(0.025, one.placeOffset, 5e-4);
    }

    @Test public void placementCueWithoutAGroundHeight() {
        // the new ref has no ground height, so no check, but the earlier visits still place the mark
        List<PointRow> earlier = Arrays.asList(ref(0, 0, 50.0), ref(0.002, 0, 50.0), ref(-0.002, 0.001, 50.0));
        PointRow noH = ref(0, 0.025, 50.0);
        noH.hGround = Double.NaN;
        assertNull(PoleCheck.check(noH, earlier, 0.03));
        PoleCheck.Cues q = PoleCheck.Cues.of(noH, earlier, null, 0.02, 0.015);
        assertEquals(0.025, q.placeOffset, 5e-4);
        assertTrue(q.offMark);
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(null, q));
        PointRow near = ref(0, 0.005, 50.0);
        near.hGround = Double.NaN;
        assertFalse(PoleCheck.Cues.of(near, earlier, null, 0.02, 0.015).offMark);
        // a first visit (no earlier ref within 0.30 m): no offset
        assertTrue(Double.isNaN(PoleCheck.Cues.of(noH, Arrays.asList(ref(1, 0, 50.0)), null, 0.02, 0.015).placeOffset));
        // with a check, the check's offset is used
        PoleCheck c = PoleCheck.check(ref(0, 0.025, 50.0), earlier, 0.03);
        assertEquals(c.placeOffset, PoleCheck.Cues.of(ref(0, 0.025, 50.0), earlier, c, 0.02, 0.015).placeOffset, 0);
    }

    @Test public void steadinessCueForHeldRefsOnly() {
        PointRow moved = held(50.0, 0.031);
        PoleCheck.Cues q = PoleCheck.Cues.of(moved, null, 0.02, 0.015);   // first visit: still cued
        assertTrue(q.unsteady);
        assertTrue(Double.isNaN(q.placeOffset));
        assertEquals("; pole moved during the ref (sd 3.1 cm); re-shoot", q.describe());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(null, q));
        assertFalse(PoleCheck.Cues.of(held(50.0, 0.009), null, 0.02, 0.015).unsteady);
        assertFalse(PoleCheck.Cues.of(moved, null, 0.02, 0).unsteady);   // 0: off
        PointRow tilt = held(50.0, 0.031);
        tilt.method = PointRow.TILT;
        assertFalse(PoleCheck.Cues.of(tilt, null, 0.02, 0.015).unsteady);
        assertEquals(PoleCheck.Colour.GREEN, PoleCheck.colour(null, PoleCheck.Cues.of(tilt, null, 0.02, 0.015)));
    }

    @Test public void bothCuesAtOnce() {
        PointRow now = held(50.0, 0.031);
        now.lon = LON + 0.025 / (111_320.0 * Math.cos(Math.toRadians(LAT)));   // 2.5 cm east of the mark
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 0, 50.0)), 0.03);
        PoleCheck.Cues q = PoleCheck.Cues.of(now, c, 0.02, 0.015);
        assertTrue(q.offMark);
        assertTrue(q.unsteady);
        assertEquals("; pole may be off the mark centre (2.5 cm); re-shoot; pole moved during the ref (sd 3.1 cm); re-shoot",
                q.describe());
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, q));
    }

    @Test public void placementUsesTheMedianPositionOfTheVisits() {
        // three visits on the mark and one 20 cm off east and north (still within 0.30 m):
        // the median position stays on the mark, a mean would move about 7 cm
        List<PointRow> earlier = Arrays.asList(ref(0, 0, 50.0), ref(0.002, -0.001, 50.0), ref(-0.001, 0.002, 50.0),
                ref(0.20, 0.20, 50.0));
        PoleCheck c = PoleCheck.check(ref(0, 0, 50.0), earlier, 0.03);
        assertEquals(4, c.visits);
        assertTrue(c.placeOffset < 0.003);
        assertFalse(PoleCheck.Cues.of(ref(0, 0, 50.0), c, 0.02, 0.015).offMark);
    }

    @Test public void doubtfulPairPlusACueStaysAmber() {
        PointRow now = held(50.0595, 0.031);
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 50.0), ref(0, 50.035)), 0.03);
        assertFalse(c.ok);
        assertTrue(c.doubtful);
        PoleCheck.Cues q = PoleCheck.Cues.of(now, c, 0.02, 0.015);
        assertTrue(q.unsteady);
        assertEquals(PoleCheck.Colour.AMBER, PoleCheck.colour(c, q));
    }

    @Test public void redWinsOverCues() {
        PointRow now = held(50.1, 0.031);
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 50.0)), 0.03);
        PoleCheck.Cues q = PoleCheck.Cues.of(now, c, 0.02, 0.015);
        assertTrue(q.unsteady);
        assertEquals(PoleCheck.Colour.RED, PoleCheck.colour(c, q));
    }

    @Test public void voidedRefIsNeverAVisit() {
        PointRow now = ref(0, 50.000);
        PointRow good = ref(0.02, 50.001), bad = ref(0.01, 50.100);   // bad: the last one in file order
        bad.voided = true;
        List<PointRow> earlier = Arrays.asList(good, bad);
        assertEquals(1, PoleCheck.matches(now, earlier).size());
        PoleCheck c = PoleCheck.check(now, earlier, 0.03);
        assertEquals(1, c.visits);
        assertEquals(good, c.last);
        assertTrue(c.ok);
        assertNull(PoleCheck.check(now, Arrays.asList(bad), 0.03));
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

    @Test public void infiniteGroundHeightIsNoHeight() {
        // Double.parseDouble reads "Infinity", so a corrupt refs.csv row can carry one
        PointRow infNow = ref(0, 50);
        infNow.hGround = Double.POSITIVE_INFINITY;
        assertNull(PoleCheck.check(infNow, Arrays.asList(ref(0, 50)), 0.03));
        PointRow infEarlier = ref(0, 50);
        infEarlier.hGround = Double.NEGATIVE_INFINITY;
        assertTrue(PoleCheck.matches(ref(0, 50), Arrays.asList(infEarlier)).isEmpty());
        PoleCheck c = PoleCheck.check(ref(0, 50), Arrays.asList(infEarlier, ref(0.1, 50.01)), 0.03);
        assertEquals(1, c.visits);
        assertEquals(50.01, c.reference, 1e-9);
    }

    @Test public void youngEarlierRefIsNeverAReference() {
        PointRow now = ref(0, 50.000);
        PointRow good = ref(0.02, 50.001), young = ref(0.01, 50.080);   // young: still settling, last in file order
        young.fixYoung = 1;
        PoleCheck c = PoleCheck.check(now, Arrays.asList(good, young), 0.03);
        assertEquals(1, c.visits);
        assertEquals(good, c.last);
        assertTrue(c.ok);
        assertNull(PoleCheck.check(now, Arrays.asList(young), 0.03));
    }

    @Test public void youngNewRefIsStillChecked() {
        PointRow now = ref(0, 50.080);
        now.fixYoung = 1;
        PoleCheck c = PoleCheck.check(now, Arrays.asList(ref(0, 50.0)), 0.03);
        assertEquals(1, c.visits);
        assertFalse(c.ok);   // shown: the surveyor sees the young ref disagree
    }

    @Test public void olderRowWithoutTheYoungColumnStaysUsable() {
        PointRow now = ref(0, 50.000);
        PointRow old = PointRow.parse(PointRow.split(
                "s1,3,ref,0,,10.0,20.0,51.8,1.8,50.0,4,30,0.001,0.001,0.002,1.0,,,12.0,0.200,0,ref,,,,,,,0,"), 1);
        assertEquals(-1, old.fixYoung);
        assertEquals(1, PoleCheck.check(now, Arrays.asList(old), 0.03).visits);
    }
}
