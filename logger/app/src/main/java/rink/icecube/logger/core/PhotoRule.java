package rink.icecube.logger.core;

/**
 * Where a photo goes: onto the most recent capture (surface, object or ref) if that was
 * taken at most 60 s before PHOTO was pressed, otherwise into a points.csv row of its own
 * (class photo). A capture can carry several photos, separated by ';'. A voided capture
 * takes no photos (see {@link VoidRule} for where its photos move).
 */
public final class PhotoRule {
    public static final long ATTACH_WINDOW_NS = 60_000_000_000L;

    private PhotoRule() { }

    /** The capture number to attach to, or -1 for a row of its own. lastN <= 0 means no capture yet. */
    public static int attachTo(int lastN, long lastCaptureNs, long photoNs) {
        if (lastN <= 0) return -1;
        long d = photoNs - lastCaptureNs;
        return d >= 0 && d <= ATTACH_WINDOW_NS ? lastN : -1;
    }

    /**
     * The same for the session's last capture row: -1 when there is none or it is not live
     * (voided, see {@link VoidRule}). After an undo the caller passes the latest live capture,
     * so a photo goes to the earlier capture only while that is within the window.
     */
    public static int attachTo(PointRow last, long photoNs) {
        if (last == null || !last.isLive()) return -1;
        return attachTo(last.n, last.elapsedNs, photoNs);
    }

    /**
     * Where PHOTO would put a photo pressed at nowNs, for the button's second line:
     * "S12 (14 s)" (the capture and its age) or "own row" (none, stale or flagged).
     */
    public static String targetText(PointRow last, long nowNs) {
        if (attachTo(last, nowNs) < 0) return "own row";
        return PointRow.label(last.cls) + last.n + " (" + (nowNs - last.elapsedNs) / 1_000_000_000L + " s)";
    }

    /**
     * photo_<n>_<utc>.jpg, utc as yyyyMMddTHHmmssZ. The number is the capture planned when
     * PHOTO was pressed (or the next row number); it is a unique, time-ordered label only.
     * Files are never renamed: after a flag moves a photo, the row whose photo column lists
     * the file is the link, not the number in its name.
     */
    public static String fileName(int n, String utcCompact) { return "photo_" + n + "_" + utcCompact + ".jpg"; }

    /** The photo column after adding a file name. */
    public static String add(String photos, String name) {
        return photos == null || photos.isEmpty() ? name : photos + ";" + name;
    }
}
