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

    @Test public void undoArmDelay() {
        long shown = 5000 * MS;
        assertFalse(VoidRule.armed(shown, shown));                                  // the frame it appears
        assertFalse(VoidRule.armed(shown, shown + VoidRule.UNDO_ARM_MS * MS - 1));  // a double tap
        assertTrue(VoidRule.armed(shown, shown + VoidRule.UNDO_ARM_MS * MS));
        assertFalse(VoidRule.armed(shown, shown - MS));                             // clock went backwards
        // a ref's notice can appear late: the window still counts from the row's own time
        long capture = 1000 * MS, appeared = capture + 7500 * MS;
        assertTrue(VoidRule.armed(appeared, appeared + VoidRule.UNDO_ARM_MS * MS));
        assertFalse(VoidRule.undoOpen(capture, appeared + VoidRule.UNDO_ARM_MS * MS));
    }

    @Test public void recentCapturesNewestFirst() {
        List<PointRow> rows = new ArrayList<>();
        for (int n = 1; n <= 14; n++) rows.add(row(n, n % 4 == 0 ? PointRow.PHOTO : PointRow.SURFACE));
        rows.get(12).voided = true;   // n=13
        rows.get(12).voidReason = "moved";
        List<PointRow> r = VoidRule.recent(rows, 10);
        assertEquals(10, r.size());
        assertEquals(14, r.get(0).n);
        assertEquals(13, r.get(1).n);                       // a flagged one stays in the list
        for (PointRow x : r) assertTrue(x.isCapture());    // photo rows left out
        assertEquals(2, r.get(r.size() - 1).n);
        assertEquals(3, VoidRule.recent(rows.subList(0, 4), 10).size());   // fewer than k; n=4 is a photo row
        assertEquals(1, VoidRule.flaggedCount(rows));
        assertEquals("S13  surface  14 s ago  FLAGGED: moved", VoidRule.recentLine(rows.get(12), 14_500 * MS));
        assertEquals("S14  surface  2 min ago", VoidRule.recentLine(rows.get(13), 150_000 * MS));
        assertEquals("1 h", VoidRule.ageText(3_700_000 * MS));
        assertEquals("?", VoidRule.ageText(-1));
    }

    @Test public void mapGlyphs() {
        PointRow s = row(1, PointRow.SURFACE), r = row(2, PointRow.REF), p = row(3, PointRow.PHOTO);
        assertEquals(VoidRule.Glyph.LIVE, VoidRule.glyph(s));
        assertEquals(VoidRule.Glyph.LIVE, VoidRule.glyph(r));
        assertEquals(VoidRule.Glyph.PHOTO, VoidRule.glyph(p));
        s.voided = true;
        r.voided = true;
        assertEquals(VoidRule.Glyph.FLAGGED, VoidRule.glyph(s));
        assertEquals(VoidRule.Glyph.FLAGGED, VoidRule.glyph(r));
        PointRow moved = VoidRule.photoRowFor(s, 4);   // a photo row made by a flag keeps its camera
        assertEquals(VoidRule.Glyph.PHOTO, VoidRule.glyph(moved));
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
        r.voidKnown = true;
        return r;
    }

    static VoidRule.RefsCopy copy(long mtime, PointRow... rows) {
        return new VoidRule.RefsCopy(mtime, Arrays.asList(rows));
    }

    @Test public void liveRefsNewestCopyDecides() {
        // a flag that reached only the newer copy: voided in both
        PointRow a1 = ref("s1", 1, false), b1 = ref("s1", 2, true), c1 = ref("s2", 2, false);
        PointRow a2 = ref("s1", 1, false), b2 = ref("s1", 2, false);
        assertEquals(Arrays.asList(a1, c1, a2),
                VoidRule.liveRefs(Arrays.asList(copy(200, a1, b1, c1), copy(100, a2, b2))));
        // an unflag that reached only the newer copy: live, the older voided row left out
        PointRow u1 = ref("s1", 2, false), u2 = ref("s1", 2, true);
        assertEquals(Arrays.asList(u1), VoidRule.liveRefs(Arrays.asList(copy(200, u1), copy(100, u2))));
        assertEquals(Arrays.asList(u1), VoidRule.liveRefs(Arrays.asList(copy(100, u2), copy(200, u1))));
    }

    @Test public void liveRefsTiesGiveVoided() {
        PointRow x = ref("s1", 2, false), y = ref("s1", 2, true);
        assertEquals(0, VoidRule.liveRefs(Arrays.asList(copy(100, x), copy(100, y))).size());
        assertEquals(0, VoidRule.liveRefs(Arrays.asList(copy(100, y), copy(100, x))).size());
    }

    @Test public void liveRefsPre09RowNeverDecides() {
        PointRow old = row(2, PointRow.REF);
        old.session = "s1";                             // no void value: voidKnown false
        PointRow flagged = ref("s1", 2, true);
        assertEquals(0, VoidRule.liveRefs(Arrays.asList(copy(300, old), copy(100, flagged))).size());
        PointRow alone = row(3, PointRow.REF);
        alone.session = "s1";
        assertEquals(Arrays.asList(alone), VoidRule.liveRefs(Arrays.asList(copy(300, alone))));
    }

    @Test public void parseSetsVoidKnown() {
        PointRow r = ref("s1", 3, true);
        PointRow back = PointRow.parse(PointRow.split(r.toRefsCsv()), 1);
        assertTrue(back.voided && back.voidKnown);
        r.voided = false;
        back = PointRow.parse(PointRow.split(r.toRefsCsv()), 1);
        assertTrue(!back.voided && back.voidKnown);
    }

    @Test public void refsVoidEventReportsPartialWrites() {
        assertEquals("ref n=3 voided in refs.csv (1 row)", VoidRule.refsVoidEvent(3, true, 1, 1, 1, 0));
        assertEquals("ref n=3 unvoided in refs.csv (2 rows)", VoidRule.refsVoidEvent(3, false, 2, 2, 2, 0));
        // holding 0 stays an error: a flag can no longer be queued before its ref's append
        // (Survey.onEpoch queues the pole check under the lock), so no row means the append failed
        assertTrue(VoidRule.refsVoidEvent(3, true, 0, 0, 0, 0).startsWith("error: "));
        String none = VoidRule.refsVoidEvent(3, true, 1, 0, 0, 0);
        assertTrue(none, none.startsWith("error: ref n=3 not voided in refs.csv (no row rewritten"));
        String part = VoidRule.refsVoidEvent(3, true, 2, 1, 1, 0);
        assertTrue(part, part.startsWith("error: ref n=3 voided in 1 of 2 refs.csv copies holding it (1 row)"));
        assertTrue(part, part.contains("newest copy"));
        String unflag = VoidRule.refsVoidEvent(3, false, 2, 1, 1, 0);
        assertTrue(unflag, unflag.contains("newest copy"));
        String unread = VoidRule.refsVoidEvent(3, true, 1, 1, 1, 1);
        assertTrue(unread, unread.startsWith("error: ") && unread.contains("1 refs.csv copy could not be read"));
    }
}
