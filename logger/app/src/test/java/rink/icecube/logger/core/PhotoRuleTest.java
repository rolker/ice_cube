package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class PhotoRuleTest {
    static final long S = 1_000_000_000L;

    @Test public void attachesWithinSixtySeconds() {
        assertEquals(4, PhotoRule.attachTo(4, 100 * S, 100 * S));
        assertEquals(4, PhotoRule.attachTo(4, 100 * S, 160 * S));
        assertEquals(-1, PhotoRule.attachTo(4, 100 * S, 160 * S + 1));
        assertEquals(-1, PhotoRule.attachTo(0, 100 * S, 101 * S));       // no capture yet
        assertEquals(-1, PhotoRule.attachTo(4, 100 * S, 99 * S));        // clock went backwards
    }

    @Test public void voidedLastCaptureTakesNoPhoto() {
        PointRow last = new PointRow();
        last.n = 4; last.cls = PointRow.OBJECT; last.elapsedNs = 100 * S;
        assertEquals(4, PhotoRule.attachTo(last, 103 * S));
        last.voided = true;
        assertEquals(-1, PhotoRule.attachTo(last, 103 * S));
        assertEquals(-1, PhotoRule.attachTo((PointRow) null, 103 * S));
        PointRow earlier = new PointRow();   // the live capture before an undone one
        earlier.n = 3; earlier.cls = PointRow.SURFACE; earlier.elapsedNs = 50 * S;
        assertEquals(3, PhotoRule.attachTo(earlier, 103 * S));
        assertEquals(-1, PhotoRule.attachTo(earlier, 111 * S));   // stale: a row of its own, as before
    }

    @Test public void targetLine() {
        PointRow last = new PointRow();
        last.n = 12; last.cls = PointRow.SURFACE; last.elapsedNs = 100 * S;
        assertEquals("S12 (14 s)", PhotoRule.targetText(last, 114 * S));
        assertEquals("S12 (60 s)", PhotoRule.targetText(last, 160 * S));
        assertEquals("own row", PhotoRule.targetText(last, 160 * S + 1));   // stale
        assertEquals("own row", PhotoRule.targetText(null, 114 * S));        // no capture yet
        last.voided = true;                                                 // last capture flagged
        assertEquals("own row", PhotoRule.targetText(last, 114 * S));
    }

    @Test public void flaggedPlannedCaptureReasksFromThePress() {
        // photoTaken: the planned capture was flagged while the camera was open; the latest
        // live capture is asked with the press time, under the same 60 s window as planPhoto
        PointRow planned = new PointRow();
        planned.n = 5; planned.cls = PointRow.OBJECT; planned.elapsedNs = 200 * S; planned.voided = true;
        PointRow older = new PointRow();
        older.n = 4; older.cls = PointRow.SURFACE; older.elapsedNs = 150 * S;
        long press = 203 * S;
        assertEquals(-1, PhotoRule.attachTo(planned, press));
        assertEquals(4, PhotoRule.attachTo(older, press));                  // inside the window from the press
        assertEquals(-1, PhotoRule.attachTo(older, 211 * S));               // outside: a row of its own
    }

    @Test public void namesAndColumn() {
        assertEquals("photo_12_20261111T140322Z.jpg", PhotoRule.fileName(12, "20261111T140322Z"));
        assertEquals("a.jpg", PhotoRule.add("", "a.jpg"));
        assertEquals("a.jpg;b.jpg", PhotoRule.add("a.jpg", "b.jpg"));
    }
}
