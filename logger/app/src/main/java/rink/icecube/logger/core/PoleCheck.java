package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Compares a new ref capture with earlier ref captures at the same mark (within 0.30 m
 * horizontally): the ground height difference to the most recent earlier visit and to the
 * mean of all earlier visits. A wrong pole height setting shows as a difference; the check
 * fails when |new - most recent| is over the tolerance.
 */
public final class PoleCheck {
    public static final double MATCH_RADIUS_M = 0.30;

    public final int visits;                  // earlier matching refs
    public final double diffLast, diffMean;   // new minus most recent, new minus mean of earlier (m)
    public final double tolerance;
    public final boolean ok;
    public final PointRow last;

    private PoleCheck(int visits, double diffLast, double diffMean, double tolerance, PointRow last) {
        this.visits = visits; this.diffLast = diffLast; this.diffMean = diffMean; this.tolerance = tolerance;
        this.last = last;
        ok = Math.abs(diffLast) <= tolerance + 1e-9;   // float rounding at the boundary
    }

    /** Earlier refs within MATCH_RADIUS_M of now that have a ground height, in the given order. */
    public static List<PointRow> matches(PointRow now, List<PointRow> earlier) {
        List<PointRow> out = new ArrayList<>();
        if (!now.hasPosition()) return out;
        LocalFrame f = new LocalFrame(now.lat, now.lon, Double.isNaN(now.hAnt) ? 0 : now.hAnt);
        double[] enu = new double[3];
        for (PointRow r : earlier) {
            if (r == now || !r.hasPosition() || Double.isNaN(r.hGround)) continue;
            f.toEnu(r.lat, r.lon, f.h0, enu);
            if (Math.hypot(enu[0], enu[1]) <= MATCH_RADIUS_M) out.add(r);
        }
        return out;
    }

    /**
     * The check for a new ref against earlier refs (oldest first, e.g. refs.csv order; the
     * last match is the most recent visit). Null if the new ref has no ground height or no
     * earlier ref matches.
     */
    public static PoleCheck check(PointRow now, List<PointRow> earlier, double tolerance) {
        if (Double.isNaN(now.hGround)) return null;
        List<PointRow> m = matches(now, earlier);
        if (m.isEmpty()) return null;
        double sum = 0;
        for (PointRow r : m) sum += r.hGround;
        PointRow last = m.get(m.size() - 1);
        return new PoleCheck(m.size(), now.hGround - last.hGround, now.hGround - sum / m.size(), tolerance, last);
    }

    private static String cm(double m) { return String.format(Locale.US, "%+.1f cm", m * 100); }

    /** "pole check OK (±0.8 cm)" or "pole check: +4.2 cm vs last visit", with the mean of earlier visits. */
    public String describe() {
        String mean = visits > 1 ? String.format(Locale.US, "; %s vs mean of %d visits", cm(diffMean), visits) : "";
        if (ok) return String.format(Locale.US, "pole check OK (±%.1f cm vs last visit%s)", Math.abs(diffLast) * 100, mean);
        return "pole check: " + cm(diffLast) + " vs last visit" + mean;
    }
}
