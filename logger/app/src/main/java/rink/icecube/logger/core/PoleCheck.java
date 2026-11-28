package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Compares a new ref capture with a robust reference formed from earlier ref captures at
 * the same mark (the visits, see {@link #matches}). The rule is written once, in the
 * README ("Refs and the pole check"):
 * <ul>
 * <li>1 visit: the reference is that visit ({@link RefKind#SINGLE}).</li>
 * <li>2 visits: their mean ({@link RefKind#PAIR}).</li>
 * <li>3 or more: the median of their ground heights ({@link RefKind#MEDIAN}; with an even
 *     count the mean of the middle two), so one bad earlier visit does not fail a good one.</li>
 * </ul>
 * The reference is doubtful when the visits that form it disagree by more than the
 * tolerance: with an even count (a pair included) the middle two differ by more; with an
 * odd count of 3 or more neither neighbour of the median (in height order) is within it.
 * A failed check against a doubtful reference is amber, not red.
 * The check passes when |new - reference| is at most the tolerance. The difference to the
 * most recent visit and to the mean of all visits are kept as information.
 * <p>
 * {@link Cues} adds two cues that are not comparisons of heights: the pole off the mark
 * centre (horizontal offset to the visits' median position) and the pole moving during a
 * held ref (its sd_u).
 */
public final class PoleCheck {
    public static final double MATCH_RADIUS_M = 0.30;

    /** How the reference was formed from the earlier visits. */
    public enum RefKind { SINGLE, PAIR, MEDIAN }

    /** The notice colour for a pole check result: red is a failed check (alarm, warnings bar). */
    public enum Colour { GREEN, AMBER, RED }

    public final int visits;                  // earlier matching refs
    public final RefKind refKind;
    public final double reference;            // ground height of the reference (m)
    public final double diffRef;              // new minus reference (m): the decision
    public final double diffLast, diffMean;   // new minus most recent, new minus mean of earlier (m): information
    /**
     * How far apart the visits that form the reference are (m): even count, the middle two;
     * odd count of 3 or more, the median to its nearer neighbour; NaN for one visit.
     */
    public final double spread;
    /** Horizontal distance (m) from the new ref to the median east/north of the visits. */
    public final double placeOffset;
    public final double tolerance;
    public final boolean ok;
    /** The spread is over the tolerance: the reference itself is in doubt. */
    public final boolean doubtful;
    public final PointRow last;

    private PoleCheck(int visits, RefKind refKind, double reference, double diffRef, double diffLast, double diffMean,
            double spread, double placeOffset, double tolerance, PointRow last) {
        this.visits = visits; this.refKind = refKind; this.reference = reference; this.diffRef = diffRef;
        this.diffLast = diffLast; this.diffMean = diffMean; this.spread = spread;
        this.placeOffset = placeOffset; this.tolerance = tolerance; this.last = last;
        ok = Math.abs(diffRef) <= tolerance + 1e-9;   // float rounding at the boundary
        doubtful = spread > tolerance + 1e-9;   // NaN (one visit): false
    }

    /** A failed check that counts: red, the alarm and the warnings bar. A doubtful reference never fails red. */
    public boolean failed() { return !ok && !doubtful; }

    /**
     * Earlier refs within MATCH_RADIUS_M of now that have a ground height, are not voided and
     * were not taken while the fix was young, in the given order. A voided ref is never a
     * visit, so never part of the reference either (#27). A young ref (fix_young 1, logger
     * 0.11.0, #15) may still be settling by centimetres, so it is never a reference either;
     * it is itself checked against older refs. A row without the column (young unknown)
     * stays usable.
     * <p>
     * One visit is one ref: refs.csv can sit in several storage roots, so the same row
     * (session, n) may come once per copy; only its first copy counts. The matches are
     * ordered by session then n (session folder names are start times), so the last one is
     * the most recent visit whatever order the copies were read in. Rows without a session
     * keep their given order and are not merged.
     */
    public static List<PointRow> matches(PointRow now, List<PointRow> earlier) {
        List<PointRow> out = new ArrayList<>();
        if (!now.hasPosition()) return out;
        LocalFrame f = frame(now);
        double[] enu = new double[3];
        Set<String> seen = new HashSet<>();
        for (PointRow r : earlier) {
            if (r == now || r.voided || r.fixYoung == 1 || !r.hasPosition() || Double.isNaN(r.hGround)) continue;
            f.toEnu(r.lat, r.lon, f.h0, enu);
            if (!(Math.hypot(enu[0], enu[1]) <= MATCH_RADIUS_M)) continue;   // a NaN distance is no match
            if (r.session != null && !r.session.isEmpty() && !seen.add(r.session + "/" + r.n)) continue;
            out.add(r);
        }
        // stable: rows with equal keys (no session) keep their order
        out.sort(Comparator.comparing((PointRow r) -> r.session == null ? "" : r.session).thenComparingInt(r -> r.n));
        return out;
    }

    private static LocalFrame frame(PointRow now) {
        return new LocalFrame(now.lat, now.lon, Double.isNaN(now.hAnt) ? 0 : now.hAnt);
    }

    /** The median of the values (mean of the middle two for an even count); NaN if empty. */
    public static double median(double[] v) {
        if (v.length == 0) return Double.NaN;
        double[] s = v.clone();
        Arrays.sort(s);
        int m = s.length / 2;
        return s.length % 2 == 1 ? s[m] : (s[m - 1] + s[m]) / 2;
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
        int k = m.size();
        double[] h = new double[k], east = new double[k], north = new double[k];
        LocalFrame f = frame(now);
        double[] enu = new double[3];
        double sum = 0;
        for (int i = 0; i < k; i++) {
            PointRow r = m.get(i);
            h[i] = r.hGround;
            sum += r.hGround;
            f.toEnu(r.lat, r.lon, f.h0, enu);
            east[i] = enu[0];
            north[i] = enu[1];
        }
        RefKind kind = k == 1 ? RefKind.SINGLE : k == 2 ? RefKind.PAIR : RefKind.MEDIAN;
        double reference = median(h);   // 1: the visit, 2: the mean
        double spread = spread(h);
        double place = Math.hypot(median(east), median(north));
        PointRow last = m.get(k - 1);
        return new PoleCheck(k, kind, reference, now.hGround - reference, now.hGround - last.hGround,
                now.hGround - sum / k, spread, place, tolerance, last);
    }

    /**
     * The spread of the values around their median: even count, the middle two apart; odd
     * count of 3 or more, the median to its nearer neighbour; NaN for fewer than 2.
     */
    public static double spread(double[] v) {
        if (v.length < 2) return Double.NaN;
        double[] s = v.clone();
        Arrays.sort(s);
        int m = s.length / 2;
        if (s.length % 2 == 0) return s[m] - s[m - 1];
        return Math.min(s[m] - s[m - 1], s[m + 1] - s[m]);
    }

    private static String cm(double m) { return String.format(Locale.US, "%+.1f cm", m * 100); }

    /** "1 earlier visit", "mean of 2 visits", "median of 4 visits" */
    private String against() {
        switch (refKind) {
            case SINGLE: return "1 earlier visit";
            case PAIR: return "mean of 2 visits";
            default: return "median of " + visits + " visits";
        }
    }

    /**
     * "pole check OK (±0.8 cm vs median of 3 visits; +2.1 cm vs last visit)", "pole check:
     * +4.2 cm vs 1 earlier visit", or for a doubtful pair "pole check: +4.2 cm vs mean of 2
     * visits that differ by 3.5 cm; shoot again" (a median: "median of 4 visits that
     * disagree by 3.5 cm around it").
     */
    public String describe() {
        String ref = against();
        if (doubtful) ref += String.format(Locale.US, refKind == RefKind.PAIR ? " that differ by %.1f cm"
                : " that disagree by %.1f cm around it", spread * 100);
        String lastInfo = visits > 1 ? "; " + cm(diffLast) + " vs last visit" : "";
        if (ok) return String.format(Locale.US, "pole check OK (±%.1f cm vs %s%s)", Math.abs(diffRef) * 100, ref, lastInfo);
        return "pole check: " + cm(diffRef) + " vs " + ref + (doubtful ? "; shoot again" : lastInfo);
    }

    /**
     * The placement and steadiness cues for a new ref: neither compares heights, so the
     * steadiness cue also applies on a first visit. A limit of 0 turns its cue off.
     */
    public static final class Cues {
        /** Horizontal offset (m) to the visits' median position; NaN with no visits. */
        public final double placeOffset;
        /** sd_u (m) of a held (occupation) ref; NaN for a tilt ref or none. */
        public final double sdU;
        public final boolean offMark, unsteady;

        private Cues(double placeOffset, double sdU, boolean offMark, boolean unsteady) {
            this.placeOffset = placeOffset; this.sdU = sdU; this.offMark = offMark; this.unsteady = unsteady;
        }

        /**
         * The cues for ref now with its check c (null on a first visit), with the placement
         * and steadiness limits in metres. Steadiness covers a held (occupation) ref only: a
         * tilt ref's sd_u is the RMS of the per-lean scatters, which the tilt gates already judge.
         */
        public static Cues of(PointRow now, PoleCheck c, double placeLimit, double steadyLimit) {
            double place = c == null ? Double.NaN : c.placeOffset;
            double sd = PointRow.OCCUPATION.equals(now.method) ? now.sdU : Double.NaN;
            boolean off = placeLimit > 0 && place > placeLimit + 1e-9;
            boolean moved = steadyLimit > 0 && sd > steadyLimit + 1e-9;
            return new Cues(place, sd, off, moved);
        }

        public boolean any() { return offMark || unsteady; }

        /** "; pole may be off the mark centre (2.5 cm); re-shoot" and/or the steadiness cue, or "". */
        public String describe() {
            StringBuilder s = new StringBuilder();
            if (offMark) s.append(String.format(Locale.US, "; pole may be off the mark centre (%.1f cm); re-shoot", placeOffset * 100));
            if (unsteady) s.append(String.format(Locale.US, "; pole moved during the ref (sd %.1f cm); re-shoot", sdU * 100));
            return s.toString();
        }
    }

    /** Red for a failed check; amber for a doubtful reference or a cue; else green. c may be null. */
    public static Colour colour(PoleCheck c, Cues q) {
        if (c != null && c.failed()) return Colour.RED;
        if ((c != null && c.doubtful) || (q != null && q.any())) return Colour.AMBER;
        return Colour.GREEN;
    }
}
