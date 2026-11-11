package rink.icecube.logger.core;

/**
 * The false-fix guard: follows the runs ("stretches") of RTK fixed GGA epochs, so a
 * capture can be held back while a new fix settles and flagged when the fix began with a
 * suspicious position jump.
 *
 * A stretch starts at an epoch of quality 4 when
 * - the previous epoch was not fixed (float, single, no position, ...), or there was none;
 * - the previous epoch was fixed but this one is more than {@link #inFixLimit} away from it
 *   (0.5 m, more for slow GGA rates: a step a moving pole cannot make between two epochs); or
 * - the previous epoch is more than {@link #GAP_NS} (2 s) older (the GPS went quiet).
 *
 * The jump at stretch start is the 3D distance (m) from the previous epoch with a position
 * to the first fixed epoch, if that previous epoch is at most 2 s older; otherwise it is
 * unknown (NaN) because the pole may have moved in between. Field data: false fixes began
 * with 1.3 to 2.9 m jumps; a normal float-to-fix step is usually a few decimetres.
 *
 * Settling: for settleNs after a stretch starts, captures are held back. Suspect: a stretch
 * whose jump is over suspectJumpM; it stays suspect until it ends. Epochs must be fed in
 * time order. Not thread-safe (Survey holds its lock).
 */
public final class FixGuard {
    public static final double IN_FIX_JUMP_M = 0.5, IN_FIX_SPEED_MPS = 2.0;
    public static final long GAP_NS = 2_000_000_000L;

    /** One stretch of fixed epochs. jump is NaN when unknown. */
    public static final class Stretch {
        public final int index;
        public final long startNs;
        public final double jump;
        public final boolean suspect, withinFix;

        Stretch(int index, long startNs, double jump, boolean suspect, boolean withinFix) {
            this.index = index; this.startNs = startNs; this.jump = jump; this.suspect = suspect;
            this.withinFix = withinFix;
        }

        /** Seconds since the stretch started, at nowNs. */
        public double ageSeconds(long nowNs) { return (nowNs - startNs) / 1e9; }

        /** "fix stretch start jump=1.234 m" (with "(within fix)" or "unknown" as needed), for events.csv. */
        public String eventText() {
            String j = Double.isNaN(jump) ? "unknown" : String.format(java.util.Locale.US, "%.3f m", jump);
            return "fix stretch start jump=" + j + (withinFix ? " (jump between fixed epochs)" : "")
                    + (suspect ? " suspect" : "");
        }
    }

    private long settleNs;
    private double suspectJumpM;
    private Epoch prev;            // the previous epoch with a position
    private boolean prevFixed;
    private Stretch current, last;
    private int count;

    public FixGuard(long settleNs, double suspectJumpM) {
        this.settleNs = settleNs;
        this.suspectJumpM = suspectJumpM;
    }

    /** The largest step (m) between consecutive fixed epochs dt seconds apart that is still the same stretch. */
    public static double inFixLimit(double dtSeconds) { return Math.max(IN_FIX_JUMP_M, IN_FIX_SPEED_MPS * dtSeconds); }

    /** 3D distance in metres; horizontal only if either height is missing. */
    public static double distance(Epoch a, Epoch b) {
        boolean noH = Double.isNaN(a.h) || Double.isNaN(b.h);
        double[] x = LocalFrame.toEcef(a.lat, a.lon, noH ? 0 : a.h), y = LocalFrame.toEcef(b.lat, b.lon, noH ? 0 : b.h);
        return Math.sqrt((x[0] - y[0]) * (x[0] - y[0]) + (x[1] - y[1]) * (x[1] - y[1]) + (x[2] - y[2]) * (x[2] - y[2]));
    }

    /** Feeds one epoch. Returns the new stretch if this epoch started one, else null. */
    public Stretch onEpoch(Epoch e) {
        if (e == null) return null;
        Stretch started = null;
        if (e.fixed()) {
            boolean recent = prev != null && e.elapsedNs - prev.elapsedNs <= GAP_NS;
            double d = recent ? distance(prev, e) : Double.NaN;
            boolean withinFix = recent && prevFixed && current != null
                    && d > inFixLimit((e.elapsedNs - prev.elapsedNs) / 1e9);
            if (current == null || !recent || !prevFixed || withinFix) {
                started = new Stretch(++count, e.elapsedNs, d, !Double.isNaN(d) && d > suspectJumpM, withinFix);
                current = started;
                last = started;
            }
        } else {
            current = null;
        }
        if (e.hasPosition()) {
            prev = e;
            prevFixed = e.fixed();
        } else {
            prevFixed = false;
        }
        return started;
    }

    /** The stretch in progress (the latest epoch was fixed), or null. */
    public Stretch current() { return current; }

    /** The most recent stretch, ended or not, or null. */
    public Stretch last() { return last; }

    /** Number of stretches so far. */
    public int count() { return count; }

    /** New settings: the settle time applies at once, the jump threshold from the next stretch. */
    public void setLimits(long settleNs, double suspectJumpM) {
        this.settleNs = settleNs;
        this.suspectJumpM = suspectJumpM;
    }

    public long settleNs() { return settleNs; }
    public double suspectJumpM() { return suspectJumpM; }

    /** Whether the stretch in progress is still settling at nowNs. */
    public boolean settling(long nowNs) { return current != null && nowNs - current.startNs < settleNs; }

    /** Whether a stretch started at s is still settling at nowNs. */
    public boolean settling(Stretch s, long nowNs) { return s != null && nowNs - s.startNs < settleNs; }

    /** Whole seconds of settling left at nowNs (rounded up), 0 if not settling. */
    public int settleRemainingSeconds(long nowNs) {
        if (!settling(nowNs)) return 0;
        return (int) Math.ceil((settleNs - (nowNs - current.startNs)) / 1e9);
    }
}
