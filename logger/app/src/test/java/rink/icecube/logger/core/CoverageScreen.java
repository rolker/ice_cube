package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

/** What MapView does with CoverageRaster frames, on an int array instead of a Bitmap (tests only). */
final class CoverageScreen {
    int i0, j1, width, height, fullFrames, patches;
    int[] px = new int[0];

    void apply(CoverageRaster.Frame f) {
        if (f.isFull()) {
            i0 = f.i0; j1 = f.j1; width = f.width; height = f.height;
            px = f.pixels.clone();
            fullFrames++;
            return;
        }
        assertEquals("patch on the same window", i0, f.i0);
        assertEquals(j1, f.j1);
        assertEquals(width, f.width);
        assertEquals(height, f.height);
        for (int k = 0; k < f.index.length; k++) px[f.index[k]] = f.color[k];
        patches++;
    }

    /** The screen equals a fresh paint of every node of `c` in its window. */
    void assertMatches(Coverage c) {
        int[] want = new int[width * height];
        for (Coverage.Node nd : c.nodes()) {
            int x = nd.i - i0, y = j1 - nd.j;
            if (x < 0 || y < 0 || x >= width || y >= height) continue;
            want[y * width + x] = CoverageRaster.color(c.status(nd));
        }
        assertArrayEquals(want, px);
    }

    boolean contains(int[] box) {
        return box[0] >= i0 && box[1] < i0 + width && box[3] <= j1 && box[2] > j1 - height;
    }
}
