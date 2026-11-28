package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Flagging a capture as bad (void, logger 0.9.0, issue #27): the undo window, the reasons,
 * and where a voided capture's photos go. Nothing is deleted: a voided row keeps its
 * number and stays in points.csv with void=1; events.csv records each flag and unflag.
 *
 * Photos and voice notes. A voided row's photos and notes (0.14.0, #16) move together (only
 * the photo and voice columns change, the files stay) to the nearest earlier live capture
 * (surface, object or ref, lower n, not voided), with no time limit, because the user has
 * said the capture was a mistake. With none, they go to a new photo row at the voided row's
 * position. Unflagging does not move them back.
 */
public final class VoidRule {
    /** UNDO is offered for this long after a capture (its notice stays up longer). */
    public static final long UNDO_WINDOW_MS = 8000;
    /** A tap on UNDO pressed down this soon after it appears is ignored (a double tap, a press under way). */
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
     * True once UNDO has been on screen long enough to take a tap: shownNs is when it first
     * showed, nowNs when the tap's press went down (same clock), so a press already under way
     * when it appears, or a double tap, falls inside {@link #UNDO_ARM_MS} and is ignored
     * however long it is held; a clock going backwards is not armed.
     */
    public static boolean armed(long shownNs, long nowNs) {
        return nowNs - shownNs >= UNDO_ARM_MS * 1_000_000L;
    }

    /**
     * True when a touch event on UNDO ends its press with no click to follow, so the press-down
     * it recorded must be forgotten: a cancel, or a lift (up) when the button is no longer
     * pressed (View cleared it on a move past the touch slop, even if the finger came back) or
     * lands outside the button and its slop. pressed is the button's state before View sees the
     * lift. A lift while only prepressed (a tap shorter than the tap timeout, inside a scrolling
     * container, which UNDO is not in) is also forgotten; its click is then timed at the click,
     * at most that timeout after the press.
     */
    public static boolean pressEndsWithoutClick(boolean cancel, boolean up, boolean pressed, boolean outsideSlop) {
        return cancel || (up && (!pressed || outsideSlop));
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

    /**
     * The list of recent captures: the k newest capture rows (surface, object, ref; flagged
     * ones included, photo rows left out), newest (highest n) first.
     */
    public static List<PointRow> recent(List<PointRow> rows, int k) {
        List<PointRow> out = new ArrayList<>();
        for (PointRow r : rows) if (r.isCapture()) out.add(r);
        Collections.sort(out, (a, b) -> Integer.compare(b.n, a.n));
        return out.size() > k ? new ArrayList<>(out.subList(0, k)) : out;
    }

    /** Flagged capture rows. */
    public static int flaggedCount(List<PointRow> rows) {
        int k = 0;
        for (PointRow r : rows) if (r.isCapture() && r.voided) k++;
        return k;
    }

    /** An age for the recent-captures list: "14 s", "5 min", "2 h"; "?" for a negative one. */
    public static String ageText(long ageNs) {
        if (ageNs < 0) return "?";
        long s = ageNs / 1_000_000_000L;
        if (s < 60) return s + " s";
        if (s < 3600) return (s / 60) + " min";
        return (s / 3600) + " h";
    }

    /** One line of the recent-captures list: "S12  surface  14 s ago", plus "  FLAGGED: reason". */
    public static String recentLine(PointRow r, long nowNs) {
        String t = PointRow.label(r.cls) + r.n + "  " + r.cls + "  " + ageText(nowNs - r.elapsedNs) + " ago";
        return r.voided ? t + "  FLAGGED: " + (r.voidReason.isEmpty() ? "other" : r.voidReason) : t;
    }

    /** How the map draws a row: its class symbol, a flagged cross, or a photo row's camera. */
    public enum Glyph { LIVE, FLAGGED, PHOTO }

    /**
     * The map glyph of a row: PHOTO for any photo row (a row a flag moved photos to keeps its
     * camera, and so does a row holding only a voice note: an own row of either), FLAGGED for a voided capture (a grey cross, never its class symbol or camera),
     * LIVE otherwise.
     */
    public static Glyph glyph(PointRow r) {
        if (!r.isCapture()) return Glyph.PHOTO;
        return r.voided ? Glyph.FLAGGED : Glyph.LIVE;
    }

    /** Live captures (photo rows and voided rows not counted). */
    public static int liveCount(List<PointRow> rows) {
        int k = 0;
        for (PointRow r : rows) if (r.isLive()) k++;
        return k;
    }

    /**
     * A new photo row (class photo, number n) holding the photos and voice notes of a voided
     * row, at its position: the position, height, fix and spread fields are copied; method,
     * tilt and guard fields are left empty. Its note says what it holds: "photos of voided
     * n=5", "notes of voided n=5" or "photos and notes of voided n=5".
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
        r.voice = voided.voice;
        boolean p = !voided.photo.isEmpty(), v = !voided.voice.isEmpty();
        r.note = (p && v ? "photos and notes" : v ? "notes" : "photos") + " of voided n=" + voided.n;
        return r;
    }

    /** Appends the photos and voice notes of from to to's columns and empties from's. */
    public static void moveAttachments(PointRow from, PointRow to) {
        for (String f : from.photo.split(";")) if (!f.isEmpty()) to.photo = PhotoRule.add(to.photo, f);
        for (String f : from.voice.split(";")) if (!f.isEmpty()) to.voice = NoteRule.add(to.voice, f);
        from.photo = "";
        from.voice = "";
    }

    /**
     * The events.csv line for a move of voided row n's photos and notes to row to (own: a new
     * row of their own): "photo moved n=5 to=3 photos=a.jpg", with " notes=b.m4a" when it had
     * notes, or "note moved n=5 to=3 notes=b.m4a" when it had notes only (one line either way).
     */
    public static String moveEvent(int n, int to, boolean own, String photos, String notes) {
        String head = (photos.isEmpty() ? "note" : "photo") + " moved n=" + n + " to=" + to + (own ? " own row" : "");
        return head + (photos.isEmpty() ? "" : " photos=" + photos) + (notes.isEmpty() ? "" : " notes=" + notes);
    }

    /** One refs.csv copy: its rows in file order and the file's modification time (ms). */
    public static final class RefsCopy {
        public final long mtime;
        public final List<PointRow> rows;

        public RefsCopy(long mtime, List<PointRow> rows) {
            this.mtime = mtime;
            this.rows = rows;
        }
    }

    /**
     * The refs.csv rows (of every copy, in order) of refs that are not voided. A (session, n)
     * takes its flag from the newest copy (by file time) whose row for it has a void value;
     * copies of that same time that disagree give voided, the safe side. A row from before
     * 0.9.0 (no void value) never decides. A ref decided voided is left out of every copy,
     * and so is a voided row of a ref decided live (an older copy that a later unflag did
     * not reach). This is the importer's rule too (import_sessions.import_refs_csv), so the
     * phone and the gpkg agree after a partial write; {@link #refsVoidEvent} says when a
     * write was partial.
     */
    public static List<PointRow> liveRefs(List<RefsCopy> copies) {
        Map<String, Long> when = new HashMap<>();
        Map<String, Boolean> flag = new HashMap<>();
        for (RefsCopy c : copies) {
            for (PointRow r : c.rows) {
                if (!r.voidKnown) continue;
                String k = r.session + "/" + r.n;
                Long t = when.get(k);
                if (t == null || c.mtime > t) {
                    when.put(k, c.mtime);
                    flag.put(k, r.voided);
                } else if (c.mtime == t && r.voided) {
                    flag.put(k, true);
                }
            }
        }
        List<PointRow> out = new ArrayList<>();
        for (RefsCopy c : copies) {
            for (PointRow r : c.rows) {
                if (!r.voided && !Boolean.TRUE.equals(flag.get(r.session + "/" + r.n))) out.add(r);
            }
        }
        return out;
    }

    /**
     * The events.csv line after a refs.csv void write for ref n: holding is the number of
     * refs.csv copies that hold the row, rewritten how many of them were written back
     * (rows: rows changed in those), unreadable how many copies could not be read at all.
     * Starts with "error: " unless every copy that holds the row was rewritten and none was
     * unreadable.
     */
    public static String refsVoidEvent(int n, boolean voided, int holding, int rewritten, int rows, int unreadable) {
        String what = voided ? "voided" : "unvoided";
        String skipped = unreadable > 0 ? "; " + unreadable + " refs.csv copy" + (unreadable == 1 ? "" : " copies")
                + " could not be read" : "";
        if (rewritten == 0) {
            return "error: ref n=" + n + " not " + what + " in refs.csv (no row rewritten" + skipped
                    + "; points.csv and this event are the record)";
        }
        String rowsText = rows + " row" + (rows == 1 ? "" : "s");
        if (rewritten < holding || unreadable > 0) {
            String effect = "readers take the flag from the newest copy, so a copy not rewritten"
                    + " brings the old flag back if it is written to later";
            return "error: ref n=" + n + " " + what + " in " + rewritten + " of " + holding
                    + " refs.csv copies holding it (" + rowsText + skipped + "); " + effect;
        }
        return "ref n=" + n + " " + what + " in refs.csv (" + rowsText + ")";
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
