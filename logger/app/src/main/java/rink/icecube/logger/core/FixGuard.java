package rink.icecube.logger.core;

/**
 * The false-fix guard: follows the runs ("stretches") of RTK fixed GGA epochs, so a
 * capture from a young fix gets a warning and the fix is flagged when it began in a way
 * false fixes begin.
 *
 * A stretch starts at an epoch of quality 4 when
 * - the previous epoch was not fixed (float, single, no position, ...), or there was none;
 * - the previous epoch was fixed but this one is more than {@link #inFixLimit} away from it
 *   (0.5 m, more for slow GGA rates: a step a moving pole cannot make between two epochs); or
 * - the previous epoch is more than {@link #GAP_NS} (2 s) older (the GPS went quiet).
 *
 * The jump at stretch start is the 3D distance (m) from the previous epoch with a position
 * to the first fixed epoch, only if that previous epoch was float or fixed and is at most
 * 2 s older; otherwise it is unknown (NaN). After single or DGPS epochs (no corrections
 * yet) the first fix always "jumps" by metres, which is not a false-fix signature (logger
 * 0.11.0, #15); unknown is neither suspect nor clean: {@link Stretch#prior} says why.
 *
 * Suspect, for the whole stretch, when either
 * - the jump is over suspectJumpM (field: false fixes began with 1.3 to 2.9 m jumps), or
 * - the stretch starts right after a float spell of at least floatSpellNs (0: rule off):
 *   a fix that took long to come may be wrong by decimetres with only a small jump (field,
 *   2026-11-21: 0.48 m jump after a long float spell, a false fix).
 *
 * Young: for youngNs after a stretch starts the fix may still be settling by centimetres
 * (field, 2026-11-26: +6 to +9 cm at 95-113 s). Captures go ahead with a warning; the
 * rows record the age. Epochs must be fed in time order. Not thread-safe (Survey holds
 * its lock).
 */
public final class FixGuard {
    public static final double IN_FIX_JUMP_M = 0.5, IN_FIX_SPEED_MPS = 2.0;
    public static final long GAP_NS = 2_000_000_000L;
    /** What came before a stretch (points.csv fix_start). */
    public static final String PRIOR_FLOAT = "float", PRIOR_SINGLE = "single", PRIOR_NONE = "none",
            PRIOR_FIXED_JUMP = "fixed-jump", PRIOR_GAP = "gap";
    /** Why a stretch is suspect (points.csv suspect_reason); both join as "jump;float_spell". */
    public static final String REASON_JUMP = "jump", REASON_FLOAT_SPELL = "float_spell";

    /** One stretch of fixed epochs. jump is NaN when unknown; floatSpellS NaN unless prior is float. */
    public static final class Stretch {
        public final int index;
        public final long startNs;
        public final double jump, floatSpellS;
        public final boolean suspect, withinFix;
        public final String prior, reason;

        Stretch(int index, long startNs, double jump, String prior, double floatSpellS, String reason) {
            this.index = index; this.startNs = startNs; this.jump = jump; this.prior = prior;
            this.floatSpellS = floatSpellS; this.reason = reason;
            suspect = !reason.isEmpty();
            withinFix = PRIOR_FIXED_JUMP.equals(prior);
        }

        /** Seconds since the stretch started, at nowNs. */
        public double ageSeconds(long nowNs) { return (nowNs - startNs) / 1e9; }

        /**
         * "fix stretch start jump=1.234 m after float 45 s suspect: jump;float_spell", for
         * events.csv ("jump=unknown after single", "(jump between fixed epochs)", ...).
         */
        public String eventText() {
            String j = Double.isNaN(jump) ? "unknown" : String.format(java.util.Locale.US, "%.3f m", jump);
            String after = withinFix ? " (jump between fixed epochs)"
                    : PRIOR_FLOAT.equals(prior) ? String.format(java.util.Locale.US, " after float %.0f s", floatSpellS)
                    : " after " + prior;
            return "fix stretch start jump=" + j + after + (suspect ? " suspect: " + reason : "");
        }
    }

    private long youngNs, floatSpellNs;
    private double suspectJumpM;
    private Epoch prev;            // the previous epoch with a position
    private Epoch last;            // the previous epoch, with a position or not
    private boolean prevFixed;
    private long floatSinceNs = -1;   // first epoch of the current float run, -1 if the last epoch was not float
    private Stretch current, lastStretch;
    private int count;

    /** youngNs: young time; suspectJumpM: jump threshold; floatSpellNs: float spell threshold (0: off). */
    public FixGuard(long youngNs, double suspectJumpM, long floatSpellNs) {
        this.youngNs = youngNs;
        this.suspectJumpM = suspectJumpM;
        this.floatSpellNs = floatSpellNs;
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
                // the jump counts only from a float or fixed epoch (corrections in use)
                double jump = recent && (prev.quality == 4 || prev.quality == 5) ? d : Double.NaN;
                String prior;
                long spellNs = -1;
                if (last == null || prev == null) prior = PRIOR_NONE;
                else if (e.elapsedNs - last.elapsedNs > GAP_NS || !recent) prior = PRIOR_GAP;
                else if (withinFix) prior = PRIOR_FIXED_JUMP;
                else if (!last.hasPosition()) prior = PRIOR_NONE;
                else if (last.quality == 5) {
                    prior = PRIOR_FLOAT;
                    spellNs = floatSinceNs < 0 ? 0 : e.elapsedNs - floatSinceNs;
                } else prior = PRIOR_SINGLE;
                StringBuilder why = new StringBuilder();
                if (!Double.isNaN(jump) && jump > suspectJumpM) why.append(REASON_JUMP);
                if (floatSpellNs > 0 && spellNs >= floatSpellNs) {
                    why.append(why.length() > 0 ? ";" : "").append(REASON_FLOAT_SPELL);
                }
                started = new Stretch(++count, e.elapsedNs, jump, prior,
                        spellNs < 0 ? Double.NaN : spellNs / 1e9, why.toString());
                current = started;
                lastStretch = started;
            }
        } else {
            current = null;
        }
        boolean isFloat = e.quality == 5 && e.hasPosition();
        if (!isFloat) floatSinceNs = -1;
        else if (floatSinceNs < 0 || last == null || e.elapsedNs - last.elapsedNs > GAP_NS) floatSinceNs = e.elapsedNs;
        if (e.hasPosition()) {
            prev = e;
            prevFixed = e.fixed();
        } else {
            prevFixed = false;
        }
        last = e;
        return started;
    }

    /** The stretch in progress (the latest epoch was fixed), or null. */
    public Stretch current() { return current; }

    /** The most recent stretch, ended or not, or null. */
    public Stretch last() { return lastStretch; }

    /** Number of stretches so far. */
    public int count() { return count; }

    /** New settings: the young time applies at once, the thresholds from the next stretch. */
    public void setLimits(long youngNs, double suspectJumpM, long floatSpellNs) {
        this.youngNs = youngNs;
        this.suspectJumpM = suspectJumpM;
        this.floatSpellNs = floatSpellNs;
    }

    public long youngNs() { return youngNs; }
    public double suspectJumpM() { return suspectJumpM; }
    public long floatSpellNs() { return floatSpellNs; }

    /** Whether the stretch in progress is still young at nowNs. */
    public boolean young(long nowNs) { return young(current, nowNs); }

    /** Whether a stretch started at s is still young at nowNs. */
    public boolean young(Stretch s, long nowNs) { return s != null && nowNs - s.startNs < youngNs; }

    /** Whole seconds since the stretch in progress started (rounded down) if it is young, else -1. */
    public int youngAgeSeconds(long nowNs) {
        if (!young(nowNs)) return -1;
        return (int) Math.floor(current.ageSeconds(nowNs));
    }
}
