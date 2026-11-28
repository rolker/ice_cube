package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class NoteRuleTest {
    static final long S = 1_000_000_000L;

    private static PointRow capture(int n, String cls, long ns) {
        PointRow r = new PointRow();
        r.n = n; r.cls = cls; r.elapsedNs = ns;
        return r;
    }

    @Test public void attachesByThePhotoWindow() {
        PointRow last = capture(4, PointRow.SURFACE, 100 * S);
        assertEquals(4, NoteRule.attachTo(last, 100 * S));
        assertEquals(4, NoteRule.attachTo(last, 160 * S));             // window edge
        assertEquals(-1, NoteRule.attachTo(last, 160 * S + 1));        // stale: own row
        assertEquals(-1, NoteRule.attachTo(last, 99 * S));             // clock backwards
        assertEquals(-1, NoteRule.attachTo(null, 100 * S));            // no capture yet
        last.voided = true;
        assertEquals(-1, NoteRule.attachTo(last, 101 * S));            // flagged last capture
        assertEquals(-1, NoteRule.attachTo(capture(5, PointRow.PHOTO, 100 * S), 101 * S));   // a photo row takes none
    }

    @Test public void targetLineIsThePhotoOne() {
        PointRow last = capture(12, PointRow.REF, 100 * S);
        assertEquals("R12 (14 s)", NoteRule.targetText(last, 114 * S));
        assertEquals("own row", NoteRule.targetText(last, 161 * S));
    }

    @Test public void fileNameAndList() {
        assertEquals("note_7_20261111T140322Z.m4a", NoteRule.fileName(7, "20261111T140322Z"));
        assertEquals("a.m4a", NoteRule.add("", "a.m4a"));
        assertEquals("a.m4a", NoteRule.add(null, "a.m4a"));
        assertEquals("a.m4a;b.m4a", NoteRule.add("a.m4a", "b.m4a"));
    }

    @Test public void keepsOnlyRealRecordings() {
        assertTrue(NoteRule.keep(4000, 1200));
        assertTrue(NoteRule.keep(1, NoteRule.MIN_MS));
        assertFalse(NoteRule.keep(0, 5000));                 // empty file
        assertFalse(NoteRule.keep(4000, NoteRule.MIN_MS - 1)); // a double tap
    }

    @Test public void recordingLine() {
        assertEquals("0:00/1:00", NoteRule.recordingText(0));
        assertEquals("0:09/1:00", NoteRule.recordingText(9_999));
        assertEquals("0:42/1:00", NoteRule.recordingText(42_000));
        assertEquals("1:00/1:00", NoteRule.recordingText(75_000));   // capped
        assertEquals("0:00/1:00", NoteRule.recordingText(-5));
        assertEquals(60_000, NoteRule.MAX_MS);
        assertEquals(32_000, NoteRule.BITRATE);
    }

    @Test public void duplicateNamesCarryOneSuffix() {
        assertEquals("note_7_20261111T140322Z_2.m4a", NoteRule.fileName(7, "20261111T140322Z", 2));
        assertEquals("note_7_20261111T140322Z_3.m4a", NoteRule.fileName(7, "20261111T140322Z", 3));
        java.util.regex.Pattern importer =   // analysis/qgis/logger_session.py NOTE_RE
                java.util.regex.Pattern.compile("note_(\\d+)_(\\d{8}T\\d{6}Z)(?:_\\d+)?\\.m4a$");
        for (int k = 2; k < 6; k++) {
            assertTrue(importer.matcher(NoteRule.fileName(7, "20261111T140322Z", k)).matches());
        }
    }
}
