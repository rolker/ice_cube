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

    /** photo_<n>_<utc>.jpg, utc as yyyyMMddTHHmmssZ. */
    public static String fileName(int n, String utcCompact) { return "photo_" + n + "_" + utcCompact + ".jpg"; }

    /** The photo column after adding a file name. */
    public static String add(String photos, String name) {
        return photos == null || photos.isEmpty() ? name : photos + ";" + name;
    }
}
