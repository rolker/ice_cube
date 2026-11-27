package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Flagging a capture (#27): undo window, reasons, where photos move, refs.csv rows. */
public class VoidRuleTest {
    static final long MS = 1_000_000L;

    static PointRow row(int n, String cls) {
        PointRow r = new PointRow();
        r.n = n;
        r.cls = cls;
        return r;
    }

    @Test public void undoWindow() {
        assertTrue(VoidRule.undoOpen(1000 * MS, 1000 * MS));
        assertTrue(VoidRule.undoOpen(1000 * MS, (1000 + VoidRule.UNDO_WINDOW_MS) * MS));
        assertFalse(VoidRule.undoOpen(1000 * MS, (1000 + VoidRule.UNDO_WINDOW_MS) * MS + 1));
        assertFalse(VoidRule.undoOpen(1000 * MS, 999 * MS));   // clock went backwards
        assertTrue(VoidRule.UNDO_ARM_MS < VoidRule.UNDO_WINDOW_MS);
    }

    @Test public void reasonsAreSafeInCsvAndEvents() {
        assertEquals(VoidRule.ACCIDENTAL, VoidRule.REASONS.get(0));
        for (String r : VoidRule.REASONS) assertEquals(r, VoidRule.reason(r));
        assertEquals("tripped sorry", VoidRule.reason("tripped, sorry"));
        assertEquals("a b c d", VoidRule.reason(" a,b;c\nd "));
        assertEquals("x y", VoidRule.reason("x=y"));
        assertEquals("other", VoidRule.reason(" , "));
        assertEquals("other", VoidRule.reason(null));
    }

    @Test public void photosGoToNearestEarlierLiveCapture() {
        PointRow s1 = row(1, PointRow.SURFACE), r2 = row(2, PointRow.REF), o3 = row(3, PointRow.OBJECT),
                p4 = row(4, PointRow.PHOTO), s5 = row(5, PointRow.SURFACE), o6 = row(6, PointRow.OBJECT);
        o3.voided = true;
        List<PointRow> rows = Arrays.asList(s1, r2, o3, p4, s5, o6);
        assertSame(s5, VoidRule.photoTarget(rows, 6));   // no time window
        s5.voided = true;                                // the target itself voided later: same rule again
        assertSame(r2, VoidRule.photoTarget(rows, 6));   // skips the photo row and voided rows; a ref counts
        assertSame(r2, VoidRule.photoTarget(rows, 5));
        assertNull(VoidRule.photoTarget(rows, 1));       // none earlier: a photo row of their own
        assertSame(o6, VoidRule.lastLive(rows));
        assertEquals(3, VoidRule.liveCount(rows));
        o6.voided = true;
        assertSame(r2, VoidRule.lastLive(rows));
        assertNull(VoidRule.lastLive(Arrays.asList(p4, o3)));
    }

    @Test public void photoRowAtTheVoidedPosition() {
        PointRow v = row(7, PointRow.OBJECT);
        v.lat = 1; v.lon = 2; v.hAnt = 3; v.pole = 1.8; v.hGround = 1.2; v.fix = 4; v.utc = "u";
        v.method = PointRow.SINGLE; v.photo = "a.jpg;b.jpg"; v.voided = true; v.voidReason = "accidental";
        PointRow p = VoidRule.photoRowFor(v, 9);
        assertEquals(9, p.n);
        assertEquals(PointRow.PHOTO, p.cls);
        assertEquals("a.jpg;b.jpg", p.photo);
        assertEquals(1.2, p.hGround, 0);
        assertEquals(4, p.fix);
        assertEquals("", p.method);
        assertFalse(p.voided);
        assertFalse(p.isLive());
    }

    @Test public void refsCsvRowsRewrittenInPlace() {
        PointRow a = row(3, PointRow.REF), b = row(3, PointRow.REF), c = row(4, PointRow.REF);
        a.session = "s1"; b.session = "s2"; c.session = "s2";
        String oldHeader = "session," + String.join(",", PointRow.split(PointRow.HEADER).subList(0, PointRow.V06_COLUMNS));
        String aLine = String.join(",", PointRow.split(a.toRefsCsv()).subList(0, PointRow.V06_COLUMNS + 1));
        List<String> lines = new ArrayList<>(Arrays.asList(oldHeader, aLine, b.toRefsCsv(), "junk", c.toRefsCsv(),
                b.toRefsCsv()));
        assertEquals(2, VoidRule.setRefsVoid(lines, "s2", 3, true, "moved"));
        assertEquals(PointRow.REFS_HEADER, lines.get(0));
        assertEquals(aLine, lines.get(1));               // other session, same n: unchanged
        assertEquals("junk", lines.get(3));
        assertEquals(c.toRefsCsv(), lines.get(4));
        for (int i : new int[] {2, 5}) {
            PointRow r = PointRow.parse(PointRow.split(lines.get(i)), 1);
            assertTrue(r.voided);
            assertEquals("moved", r.voidReason);
        }
        assertEquals(2, VoidRule.setRefsVoid(lines, "s2", 3, false, "moved"));
        assertFalse(PointRow.parse(PointRow.split(lines.get(2)), 1).voided);
        assertEquals("", PointRow.parse(PointRow.split(lines.get(2)), 1).voidReason);
        assertEquals(1, VoidRule.setRefsVoid(lines, "s1", 3, true, null));   // an older row gains the columns
        assertEquals(PointRow.COLUMNS + 1, PointRow.split(lines.get(1)).size());
        assertEquals(0, VoidRule.setRefsVoid(lines, "s9", 3, true, "x"));
    }

    static PointRow ref(String session, int n, boolean voided) {
        PointRow r = row(n, PointRow.REF);
        r.session = session;
        r.voided = voided;
        return r;
    }

    @Test public void liveRefsDropARefVoidedInAnyCopy() {
        // two refs.csv copies: the voided ref's row in the second could not be rewritten
        PointRow a1 = ref("s1", 1, false), b1 = ref("s1", 2, true), c1 = ref("s2", 2, false);
        PointRow a2 = ref("s1", 1, false), b2 = ref("s1", 2, false);
        List<PointRow> live = VoidRule.liveRefs(Arrays.asList(a1, b1, c1, a2, b2));
        assertEquals(Arrays.asList(a1, c1, a2), live);   // same n in another session stays
    }

    @Test public void refsVoidEventReportsPartialWrites() {
        assertEquals("ref n=3 voided in refs.csv (1 row)", VoidRule.refsVoidEvent(3, true, 1, 1, 1, 0));
        assertEquals("ref n=3 unvoided in refs.csv (2 rows)", VoidRule.refsVoidEvent(3, false, 2, 2, 2, 0));
        String none = VoidRule.refsVoidEvent(3, true, 1, 0, 0, 0);
        assertTrue(none, none.startsWith("error: ref n=3 not voided in refs.csv (no row rewritten"));
        String part = VoidRule.refsVoidEvent(3, true, 2, 1, 1, 0);
        assertTrue(part, part.startsWith("error: ref n=3 voided in 1 of 2 refs.csv copies holding it (1 row)"));
        assertTrue(part, part.contains("stays voided"));
        String unflag = VoidRule.refsVoidEvent(3, false, 2, 1, 1, 0);
        assertTrue(unflag, unflag.contains("keeps it voided"));
        String unread = VoidRule.refsVoidEvent(3, true, 1, 1, 1, 1);
        assertTrue(unread, unread.startsWith("error: ") && unread.contains("1 refs.csv copy could not be read"));
    }
}
