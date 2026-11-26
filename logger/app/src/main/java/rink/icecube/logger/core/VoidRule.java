package rink.icecube.logger.core;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Flagging a capture as bad (void, logger 0.9.0, issue #27): the undo window, the reasons,
 * and where a voided capture's photos go. Nothing is deleted: a voided row keeps its
 * number and stays in points.csv with void=1; events.csv records each flag and unflag.
 *
 * Photos. A voided row's photos move (only the photo column changes, the files stay) to the
 * nearest earlier live capture (surface, object or ref, lower n, not voided), with no time
 * limit, because the user has said the capture was a mistake. With none, they go to a new
 * photo row at the voided row's position. Unflagging does not move them back.
 */
public final class VoidRule {
    /** UNDO is offered for this long after a capture (its notice stays up longer). */
    public static final long UNDO_WINDOW_MS = 8000;
    /** Taps on UNDO are ignored this long after it appears (a double tap or a press in progress). */
    public static final long UNDO_ARM_MS = 750;

    public static final String ACCIDENTAL = "accidental";
    /** The reasons offered when flagging; free text is sanitised with {@link #reason}. */
    public static final List<String> REASONS = Collections.unmodifiableList(
            Arrays.asList(ACCIDENTAL, "bad pole placement", "moved", "other"));

    /** via: the UNDO button, or a later flag from the list of recent captures. */
    public static final String VIA_UNDO = "undo", VIA_LIST = "list";

    private VoidRule() { }

    /** True while a capture taken at captureNs can still be undone at nowNs (same clock). */
    public static boolean undoOpen(long captureNs, long nowNs) {
        long d = nowNs - captureNs;
        return d >= 0 && d <= UNDO_WINDOW_MS * 1_000_000L;
    }

    /**
     * A reason fit for points.csv and an events.csv line: commas, semicolons, '=' and line
     * breaks become spaces, runs of spaces collapse, trimmed; empty becomes "other".
     */
    public static String reason(String r) {
        if (r == null) return "other";
        String s = r.replaceAll("[,;=\"\\r\\n\\t]", " ").replaceAll(" +", " ").trim();
        return s.isEmpty() ? "other" : s;
    }

    /**
     * Where the photos of voided row n go: the nearest earlier live capture (highest n below
     * n, not voided, not a photo row), or null for a new photo row of their own.
     */
    public static PointRow photoTarget(List<PointRow> rows, int n) {
        PointRow best = null;
        for (PointRow r : rows) {
            if (r.n < n && r.isLive() && (best == null || r.n > best.n)) best = r;
        }
        return best;
    }

    /** The most recent live capture (highest n), or null. */
    public static PointRow lastLive(List<PointRow> rows) {
        PointRow best = null;
        for (PointRow r : rows) if (r.isLive() && (best == null || r.n > best.n)) best = r;
        return best;
    }

    /** Live captures (photo rows and voided rows not counted). */
    public static int liveCount(List<PointRow> rows) {
        int k = 0;
        for (PointRow r : rows) if (r.isLive()) k++;
        return k;
    }

    /**
     * A new photo row (class photo, number n) holding the photos of a voided row, at its
     * position: the position, height, fix and spread fields are copied; method, tilt and
     * guard fields are left empty.
     */
    public static PointRow photoRowFor(PointRow voided, int n) {
        PointRow r = new PointRow();
        r.n = n;
        r.cls = PointRow.PHOTO;
        r.elapsedNs = voided.elapsedNs;
        r.utc = voided.utc;
        r.lat = voided.lat; r.lon = voided.lon; r.hAnt = voided.hAnt; r.pole = voided.pole; r.hGround = voided.hGround;
        r.fix = voided.fix; r.nEpochs = voided.nEpochs;
        r.sdE = voided.sdE; r.sdN = voided.sdN; r.sdU = voided.sdU; r.age = voided.age;
        r.photo = voided.photo;
        r.note = "photos of voided n=" + voided.n;
        return r;
    }

    /**
     * Sets the void columns of the refs.csv rows of (session, n) in lines (a whole refs.csv,
     * header first or not). The header, when it is an older one, becomes REFS_HEADER; a
     * matching row is rewritten from its parsed fields (so an older row gains the newer
     * columns; its photo column stays as appended, since points.csv is the record of photo
     * names); every other line is kept as it is. Returns the number of rows matched.
     */
    public static int setRefsVoid(List<String> lines, String session, int n, boolean voided, String reason) {
        int changed = 0;
        for (int i = 0; i < lines.size(); i++) {
            String l = lines.get(i);
            if (l.startsWith("session,")) {
                if (!l.equals(PointRow.REFS_HEADER)) lines.set(i, PointRow.REFS_HEADER);
                continue;
            }
            if (l.isEmpty()) continue;
            PointRow r;
            try { r = PointRow.parse(PointRow.split(l), 1); }
            catch (IllegalArgumentException e) { continue; }
            if (r.n != n || !session.equals(r.session)) continue;
            r.voided = voided;
            r.voidReason = voided ? reason(reason) : "";
            lines.set(i, r.toRefsCsv());
            changed++;
        }
        return changed;
    }
}
