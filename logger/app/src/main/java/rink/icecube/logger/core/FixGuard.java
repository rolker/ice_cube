package rink.icecube.logger.core;

/**
 * The false-fix guard: follows the runs ("stretches") of RTK fixed GGA epochs, so a
 * capture from a young fix gets a warning and the fix is flagged when it began in a way
 * false fixes begin.
 *
 * A stretch starts at an epoch of quality 4 unless it continues the last one: the last
 * fixed epoch is at most {@link #GAP_S} (2 s) older, no float epoch came between, and this
 * one is at most {@link #inFixLimit} away from it (0.5 m, more for slow GGA rates: a step a
 * moving pole cannot make between two epochs). So a dropout of 2 s or less inside a fix (no
 * position, single or DGPS epochs) does not end the stretch; a longer one, a float epoch, a
 * quiet GPS over 2 s or a bigger step does (#15; soundings.fix_stretches is the same).
 *
 * Clock: every 2 s test and the float spell length use the GGA time of day ({@link #dt}, the
 * receiver's clock, as soundings.fix_stretches does), with {@link #GAP_TOL_S} of slack so a
 * whole-epoch dropout that lands on 2 s reads the same on both sides; the phone time only
 * stands in when a GGA has no time. Stretch ages (young) run on the phone time.
 *
 * The jump at stretch start is the 3D distance (m) from the last float or fixed epoch to the
 * first fixed epoch, if that epoch is at most 2 s older; so it carries over a dropout of 2 s
 * or less (no position, single or DGPS epochs) between float and fix (#15). Otherwise it is
 * unknown (NaN): after single or DGPS epochs (no corrections yet) the first fix always
 * "jumps" by metres, which is not a false-fix signature (logger 0.11.0, #15); unknown is
 * neither suspect nor clean: {@link Stretch#prior} says why.
 *
 * Suspect, for the whole stretch, when either
 * - the jump is over suspectJumpM (field: false fixes began with 1.3 to 2.9 m jumps), or
 * - the stretch starts right after a float spell of at least floatSpellNs (0: rule off):
 *   a fix that took long to come may be wrong by decimetres with only a small jump (field,
 *   2026-11-21: 0.48 m jump after a long float spell, a false fix). The spell runs from its
 *   first float epoch to the fix, and carries over a dropout of 2 s or less (non-float
 *   epochs, or none), so a one-epoch dropout just before the fix cannot hide it.
 *
 * Young: for youngNs after a stretch starts the fix may still be settling by centimetres
 * (field, 2026-11-26: +6 to +9 cm at 95-113 s). Captures go ahead with a warning; the
 * rows record the age. Epochs must be fed in time order. Not thread-safe (Survey holds
 * its lock).
 */
public final class FixGuard {
    public static final double IN_FIX_JUMP_M = 0.5, IN_FIX_SPEED_MPS = 2.0;
    /** Longest dropout (s) a stretch, a float spell and the start jump carry over. */
    public static final double GAP_S = 2.0;
    /** Slack (s) on the 2 s tests: GGA times are to 0.01 s, so this only absorbs rounding. */
    public static final double GAP_TOL_S = 1e-3;
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
    private Epoch lastCorr;        // the last float or fixed epoch (the start jump's origin)
    private Epoch lastFix;         // the last fixed epoch of the open stretch; null after a float epoch
    private Epoch floatSince;      // first epoch of the float spell, null if none (fix since)
    private Epoch lastFloat;       // last float epoch of that spell
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

    /**
     * Seconds from a to b on the GGA clock (time of day, midnight rollover handled), or on
     * the phone clock when either GGA has no time.
     */
    public static double dt(Epoch a, Epoch b) {
        if (Double.isNaN(a.tod) || Double.isNaN(b.tod)) return (b.elapsedNs - a.elapsedNs) / 1e9;
        double d = b.tod - a.tod;
        if (d < -43200) d += 86400;
        else if (d > 43200) d -= 86400;
        return d;
    }

    /**
     * Whether b is at most 2 s after a ({@link #dt}, with {@link #GAP_TOL_S} of slack). A
     * backward step of the receiver clock is never within, so it starts a stretch and ends a
     * float spell (#15; soundings.fix_stretches is the same).
     */
    static boolean within2s(Epoch a, Epoch b) {
        double d = dt(a, b);
        return d >= 0 && d <= GAP_S + GAP_TOL_S;
    }

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
        boolean isFloat = e.quality == 5 && e.hasPosition();
        // a float spell lives on through a dropout of up to 2 s after its last float epoch
        boolean spellOn = floatSince != null && within2s(lastFloat, e);
        if (e.fixed()) {
            boolean cont = lastFix != null && lastStretch != null && within2s(lastFix, e);
            double dFix = cont ? distance(lastFix, e) : Double.NaN;
            boolean withinFix = cont && dFix > inFixLimit(dt(lastFix, e));
            if (cont && !withinFix) {
                current = lastStretch;   // same stretch, also after a short dropout
            } else {
                boolean recent = prev != null && within2s(prev, e);
                // the jump counts only from a float or fixed epoch (corrections in use), also
                // across a dropout of 2 s or less (#15)
                double jump = withinFix ? dFix
                        : lastCorr != null && within2s(lastCorr, e) ? distance(lastCorr, e) : Double.NaN;
                String prior;
                double spellS = Double.NaN;
                if (withinFix) prior = PRIOR_FIXED_JUMP;
                else if (last == null || prev == null) prior = PRIOR_NONE;
                else if (spellOn) {
                    prior = PRIOR_FLOAT;
                    spellS = dt(floatSince, e);
                } else if (!within2s(last, e) || !recent) prior = PRIOR_GAP;
                else if (!last.hasPosition()) prior = PRIOR_NONE;
                else prior = PRIOR_SINGLE;
                StringBuilder why = new StringBuilder();
                if (!Double.isNaN(jump) && jump > suspectJumpM) why.append(REASON_JUMP);
                if (floatSpellNs > 0 && !Double.isNaN(spellS) && spellS * 1e9 >= floatSpellNs) {
                    why.append(why.length() > 0 ? ";" : "").append(REASON_FLOAT_SPELL);
                }
                started = new Stretch(++count, e.elapsedNs, jump, prior, spellS, why.toString());
                current = started;
                lastStretch = started;
            }
            lastFix = e;
            lastCorr = e;
            floatSince = null;
        } else {
            current = null;
            if (isFloat) {
                lastFix = null;   // a float epoch ends the stretch
                if (!spellOn) floatSince = e;
                lastFloat = e;
                lastCorr = e;
            }
        }
        if (e.hasPosition()) prev = e;
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
