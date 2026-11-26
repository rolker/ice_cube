package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.List;

/** The overlay window and its frames: what is painted always matches the coverage (#6 review). */
public class CoverageRasterTest {
    private static Coverage.Params params() { return new Coverage.Params(0.5, 0.75, 2.0, 0.03, 0.015, 0.015); }

    /** Feeds one sounding and hands its frame to the screen; returns the newest touched box. */
    private static int[] feed(Coverage c, CoverageRaster r, CoverageScreen s, double x, double y, double z) {
        c.add(x, y, z, 0.02, 0.03);
        List<Coverage.Node> t = c.drainTouched();
        int[] box = CoverageRaster.box(t);
        s.apply(r.update(1, c, t, box));
        return box;
    }

    @Test public void growsWithTheAreaAndRepaintsOnlyWhenItMoves() {
        Coverage c = new Coverage(params());
        CoverageRaster r = new CoverageRaster(400, 8);
        CoverageScreen s = new CoverageScreen();
        for (int k = 0; k < 60; k++) {
            int[] box = feed(c, r, s, k * 0.5, 0, 0.1);
            assertTrue(s.contains(box));
            s.assertMatches(c);
        }
        // the margin is slack: 60 captures 1 cell apart move the window about 60 / 8 times, not 60
        assertTrue("full frames " + s.fullFrames, s.fullFrames <= 9);
        assertEquals(60, s.fullFrames + s.patches);
    }

    @Test public void cappedWindowFollowsTheNewestCapturesAndRepaintsAll() {
        Coverage c = new Coverage(params());
        CoverageRaster r = new CoverageRaster(40, 2);   // 20 m a side at 0.5 m
        CoverageScreen s = new CoverageScreen();
        // out east 60 m, then back west over the same line with different heights (red nodes)
        for (int k = 0; k <= 120; k++) {
            int[] box = feed(c, r, s, k * 0.5, 0, 0.1);
            assertTrue("newest in view at " + k, s.contains(box));
            s.assertMatches(c);
            assertTrue(s.width <= 40 && s.height <= 40);
        }
        int fullOut = s.fullFrames;
        assertTrue("window moves, not every capture: " + fullOut, fullOut < 20);
        for (int k = 120; k >= 0; k -= 3) {
            int[] box = feed(c, r, s, k * 0.5, 0.25, 0.5);
            assertTrue("newest in view on the way back at " + k, s.contains(box));
            s.assertMatches(c);   // nodes that came back into view are painted, not left clear
        }
        assertTrue(s.fullFrames > fullOut);
    }

    @Test public void repaintPlacesTheWindowOnTheNewestBox() {
        Coverage c = new Coverage(params());
        int[] last = null;
        for (int k = 0; k <= 200; k++) {
            c.add(k * 0.5, 0, 0.1, 0.02, 0.03);
            last = CoverageRaster.box(c.drainTouched());
        }
        CoverageRaster r = new CoverageRaster(40, 2);
        CoverageScreen s = new CoverageScreen();
        s.apply(r.repaint(3, c, last));
        assertTrue(s.contains(last));
        assertEquals(40, s.width);
        s.assertMatches(c);
        assertEquals((last[0] + last[1]) / 2 - 20, s.i0);   // centred on the newest box
    }

    @Test public void noAreaGivesAnEmptyFullFrame() {
        Coverage c = new Coverage(params());
        CoverageRaster.Frame f = new CoverageRaster().repaint(5, c, null);
        assertTrue(f.isFull());
        assertEquals(0, f.width);
        assertEquals(5, f.gen);
        assertFalse(f.areaLimit);
        assertNull(new CoverageRaster().window());
    }

    @Test public void axisKeepsOrRecentres() {
        CoverageRaster r = new CoverageRaster(10, 1);
        assertArrayEquals(new int[] {-1, 5}, r.axis(false, 0, 0, 0, 4, 0, 4));        // fits: area plus margin
        assertArrayEquals(new int[] {-1, 5}, r.axis(true, -1, 5, 0, 5, 3, 5));        // still holds the area
        assertArrayEquals(new int[] {-1, 8}, r.axis(true, -1, 5, 0, 7, 5, 7));        // too long: capped, newest in
        assertArrayEquals(new int[] {-1, 8}, r.axis(true, -1, 8, 0, 20, 6, 8));       // newest inside: kept
        assertArrayEquals(new int[] {7, 16}, r.axis(true, -1, 8, 0, 20, 11, 13));     // outside: centred on it
        assertArrayEquals(new int[] {14, 23}, r.axis(true, -1, 8, 0, 20, 18, 20));    // ahead of the area too
    }

    /** A gap and a disagreement are red, the edge with no data a faint neutral, OK clear. */
    @Test public void colours() {
        assertEquals(CoverageRaster.RED, CoverageRaster.color(Coverage.Status.NO_DATA));
        assertEquals(CoverageRaster.RED, CoverageRaster.color(Coverage.Status.DISAGREE));
        assertEquals(CoverageRaster.AMBER, CoverageRaster.color(Coverage.Status.THIN));
        assertEquals(CoverageRaster.FRINGE, CoverageRaster.color(Coverage.Status.FRINGE));
        assertEquals(0, CoverageRaster.color(Coverage.Status.OK));
        assertEquals(0, CoverageRaster.color(Coverage.Status.NONE));
        assertTrue((CoverageRaster.FRINGE >>> 24) < (CoverageRaster.RED >>> 24));   // fainter than red
    }
}
