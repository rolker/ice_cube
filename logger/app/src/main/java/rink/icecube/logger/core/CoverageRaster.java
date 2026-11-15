package rink.icecube.logger.core;

import java.util.Collection;

/**
 * The coverage overlay's raster without Android (logger 0.7, issue #6): which grid nodes
 * the map's bitmap holds (one pixel per node, a window of at most maxSide nodes a side) and
 * the colour of each, handed to the UI thread as immutable {@link Frame}s.
 *
 * Window. While the in-area box (Coverage.minI..maxI, minJ..maxJ) plus a margin fits in
 * maxSide on an axis, the window covers it, and moves (grows) only when the area leaves it.
 * Once it does not fit, the window is maxSide long on that axis and holds the newest
 * capture's touched nodes: it stays put while they are inside it, and otherwise re-centres
 * on them. Every window change repaints all the
 * nodes in the new window from Coverage.nodes(), so a node that comes into view never reads
 * as clear just because it was not painted; between changes only the touched nodes are sent.
 *
 * Owned by one thread (the coverage worker); a Frame, once returned, is never changed.
 */
public final class CoverageRaster {
    /** Translucent red (no data or disagreement) and amber (sigma above the target), ARGB. */
    public static final int RED = 0x66D32F2F, AMBER = 0x55FFA000;
    /** Largest window side, in nodes (1 km at 0.5 m), and the slack added around the area. */
    public static final int MAX_SIDE = 2048, MARGIN = 32;

    /**
     * One hand-off to the UI thread. Pixel (x, y) is node (i0 + x, j1 - y). A full frame
     * (pixels non-null, width * height ARGB values, all zero-sized when there is no area yet)
     * replaces the bitmap; a patch (index and color non-null) sets pixels index[k] = y * width
     * + x of a bitmap with the same window.
     */
    public static final class Frame {
        public final int gen;
        public final double cell;
        public final int i0, j1, width, height;
        public final int[] pixels, index, color;
        /** Coverage.full() at this frame: the legend's area-limit line. */
        public final boolean areaLimit;

        Frame(int gen, double cell, int i0, int j1, int width, int height, int[] pixels, int[] index, int[] color,
                boolean areaLimit) {
            this.gen = gen; this.cell = cell; this.i0 = i0; this.j1 = j1; this.width = width; this.height = height;
            this.pixels = pixels; this.index = index; this.color = color; this.areaLimit = areaLimit;
        }

        public boolean isFull() { return pixels != null; }
    }

    private final int maxSide, margin;
    private boolean has;
    private int i0, i1, j0, j1;

    public CoverageRaster() { this(MAX_SIDE, MARGIN); }

    /** Smaller sides and margins are for tests. */
    public CoverageRaster(int maxSide, int margin) { this.maxSide = maxSide; this.margin = margin; }

    public static int color(Coverage.Status s) {
        switch (s) {
            case NO_DATA: case DISAGREE: return RED;
            case THIN: return AMBER;
            default: return 0;
        }
    }

    /** {i0, i1, j0, j1} of the nodes, or null for none. */
    public static int[] box(Collection<Coverage.Node> nodes) {
        if (nodes.isEmpty()) return null;
        int[] b = {Integer.MAX_VALUE, Integer.MIN_VALUE, Integer.MAX_VALUE, Integer.MIN_VALUE};
        for (Coverage.Node nd : nodes) {
            b[0] = Math.min(b[0], nd.i); b[1] = Math.max(b[1], nd.i);
            b[2] = Math.min(b[2], nd.j); b[3] = Math.max(b[3], nd.j);
        }
        return b;
    }

    /** The window as {i0, i1, j0, j1}, or null before the first frame with an area. */
    public int[] window() { return has ? new int[] {i0, i1, j0, j1} : null; }

    /**
     * Places the window and paints every in-window node: after a rebuild. `newest` is the
     * touched box of the last capture fed (null: none).
     */
    public Frame repaint(int gen, Coverage c, int[] newest) {
        has = false;
        if (!place(c, newest)) return empty(gen, c);
        return full(gen, c);
    }

    /**
     * After a feed: `touched` are the nodes it changed, `newest` the last capture's touched
     * box (null: none). A full frame when the window moved, else a patch of the touched nodes
     * in the window.
     */
    public Frame update(int gen, Coverage c, Collection<Coverage.Node> touched, int[] newest) {
        int[] before = window();
        if (!place(c, newest)) return empty(gen, c);
        if (before == null || before[0] != i0 || before[1] != i1 || before[2] != j0 || before[3] != j1) return full(gen, c);
        int w = i1 - i0 + 1;
        int[] index = new int[touched.size()], color = new int[touched.size()];
        int k = 0;
        for (Coverage.Node nd : touched) {
            if (nd.i < i0 || nd.i > i1 || nd.j < j0 || nd.j > j1) continue;
            index[k] = (j1 - nd.j) * w + (nd.i - i0);
            color[k] = color(c.status(nd));
            k++;
        }
        if (k < index.length) { index = java.util.Arrays.copyOf(index, k); color = java.util.Arrays.copyOf(color, k); }
        return new Frame(gen, c.params.cell, i0, j1, w, j1 - j0 + 1, null, index, color, c.full());
    }

    private Frame empty(int gen, Coverage c) {
        has = false;
        return new Frame(gen, c.params.cell, 0, 0, 0, 0, new int[0], null, null, c.full());
    }

    private Frame full(int gen, Coverage c) {
        int w = i1 - i0 + 1, h = j1 - j0 + 1;
        int[] px = new int[w * h];
        for (Coverage.Node nd : c.nodes()) {
            if (nd.i < i0 || nd.i > i1 || nd.j < j0 || nd.j > j1) continue;
            px[(j1 - nd.j) * w + (nd.i - i0)] = color(c.status(nd));
        }
        return new Frame(gen, c.params.cell, i0, j1, w, h, px, null, null, c.full());
    }

    /** Sets the window for the area and the newest box; false when there is no area. */
    private boolean place(Coverage c, int[] newest) {
        if (c.minI > c.maxI) return false;
        int[] t = newest != null ? newest
                : new int[] {(c.minI + c.maxI) >> 1, (c.minI + c.maxI) >> 1, (c.minJ + c.maxJ) >> 1, (c.minJ + c.maxJ) >> 1};
        int[] x = axis(has, i0, i1, c.minI, c.maxI, t[0], t[1]);
        int[] y = axis(has, j0, j1, c.minJ, c.maxJ, t[2], t[3]);
        i0 = x[0]; i1 = x[1]; j0 = y[0]; j1 = y[1];
        has = true;
        return true;
    }

    /** One axis of the window: [w0, w1] for the current window, the area [a0, a1] and the newest [t0, t1]. */
    int[] axis(boolean has, int w0, int w1, int a0, int a1, int t0, int t1) {
        int need0 = a0 - margin, need1 = a1 + margin;
        if (need1 - need0 + 1 <= maxSide) {
            if (has && w0 <= a0 && a1 <= w1 && w1 - w0 + 1 <= maxSide) return new int[] {w0, w1};   // margin is slack
            return new int[] {need0, need1};
        }
        if (has && w1 - w0 + 1 == maxSide && w0 <= t0 && t1 <= w1) return new int[] {w0, w1};
        // Centred on the newest box, not clamped to the area: walking outward, half the window
        // lies ahead, so it moves about every maxSide / 2 nodes rather than every capture.
        int s = ((t0 + t1) >> 1) - maxSide / 2;
        s = Math.min(s, t0);                       // the newest box always inside
        s = Math.max(s, t1 - maxSide + 1);
        return new int[] {s, s + maxSide - 1};
    }
}
