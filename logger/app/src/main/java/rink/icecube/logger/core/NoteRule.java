package rink.icecube.logger.core;

/**
 * Voice notes (logger 0.14.0, #16): a NOTE press records mono AAC in an .m4a in the
 * session's notes/ folder. Where a note goes is the photo rule ({@link PhotoRule}): onto
 * the most recent live capture taken at most 60 s before NOTE was pressed, otherwise into
 * a points.csv row of its own (class photo, holding only the note). The voice column
 * lists a row's notes, separated by ';'. A flagged capture's notes move with its photos
 * (see {@link VoidRule}). No speech-to-text: the audio is the record.
 */
public final class NoteRule {
    /** A note stops by itself after this long (with its own cue). */
    public static final int MAX_MS = 60_000;
    /** A note shorter than this (a double tap) is not kept. */
    public static final long MIN_MS = 500;
    /** Mono AAC at a speech bitrate: about 0.25 MB per minute. */
    public static final int BITRATE = 32_000, SAMPLE_RATE = 16_000;

    private NoteRule() { }

    /** The capture number to attach to, or -1 for a row of its own (PhotoRule's window and live-row rule). */
    public static int attachTo(PointRow last, long pressNs) { return PhotoRule.attachTo(last, pressNs); }

    /** Where NOTE would put a note pressed at nowNs: "S12 (14 s)" or "own row". */
    public static String targetText(PointRow last, long nowNs) { return PhotoRule.targetText(last, nowNs); }

    /**
     * note_<n>_<utc>.m4a, utc as yyyyMMddTHHmmssZ, n the capture planned when NOTE was
     * pressed (or the next row number). As for photos, a unique, time-ordered label only:
     * the row whose voice column lists the file is the link.
     */
    public static String fileName(int n, String utcCompact) { return "note_" + n + "_" + utcCompact + ".m4a"; }

    /** The voice column after adding a file name. */
    public static String add(String voice, String name) { return PhotoRule.add(voice, name); }

    /** Whether a finished recording is kept: a non-empty file at least {@link #MIN_MS} long. */
    public static boolean keep(long bytes, long durationMs) { return bytes > 0 && durationMs >= MIN_MS; }

    /** NOTE's second line while recording: "0:12 / 1:00". */
    public static String recordingText(long elapsedMs) {
        long s = Math.max(0, Math.min(elapsedMs, MAX_MS)) / 1000, m = MAX_MS / 1000;
        return s / 60 + ":" + (s % 60 < 10 ? "0" : "") + s % 60 + " / " + m / 60 + ":" + (m % 60 < 10 ? "0" : "") + m % 60;
    }
}
