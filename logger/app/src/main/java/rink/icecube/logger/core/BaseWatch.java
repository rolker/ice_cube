package rink.icecube.logger.core;

import java.util.Locale;

/**
 * Watches the base position in a session's 1005/1006 messages. The first message of each
 * type is reported; a later one that moves more than 0.01 m from the first, or a new
 * station ID, raises a warning that stays until the next session. A moved position is
 * reported once each time it changes again by more than the tolerance, not on every message.
 */
public final class BaseWatch {
    public static final double TOLERANCE_M = 0.01;

    private StationArp first, lastReported;
    private boolean seen1005, seen1006;
    private volatile StationArp latest;
    private volatile String warning;

    /** Returns the events.csv lines this message causes (may be empty). */
    public synchronized java.util.List<String> onStation(StationArp s) {
        java.util.List<String> out = new java.util.ArrayList<>();
        latest = s;
        boolean firstOfType = s.type == 1005 ? !seen1005 : !seen1006;
        if (s.type == 1005) seen1005 = true; else seen1006 = true;
        if (first == null) {
            first = s;
            lastReported = s;
            out.add(s.eventText());
            return out;
        }
        if (firstOfType) out.add(s.eventText());
        if (s.stationId != first.stationId) {
            if (s.stationId != lastReported.stationId) {
                warning = String.format(Locale.US, "base moved: station ID %d -> %d", first.stationId, s.stationId);
                out.add(warning + " " + s.eventText());
                lastReported = s;
            }
            return out;
        }
        double d = s.distanceTo(first);
        if (d > TOLERANCE_M && s.distanceTo(lastReported) > TOLERANCE_M) {
            warning = String.format(Locale.US, "base moved %.4f m (dx %.4f dy %.4f dz %.4f) from first message",
                    d, s.x - first.x, s.y - first.y, s.z - first.z);
            out.add(warning + " " + s.eventText());
            lastReported = s;
        }
        return out;
    }

    public StationArp latest() { return latest; }
    public synchronized StationArp first() { return first; }
    public String warning() { return warning; }
}
