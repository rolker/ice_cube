package rink.icecube.logger.core;

import java.util.ArrayDeque;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** Counts RTCM3 messages per type and their rate over a recent window. Times are caller-supplied ms. */
public final class RtcmStats {
    private static final class Entry { long count; final ArrayDeque<Long> recent = new ArrayDeque<>(); }

    private final long windowMs;
    private final TreeMap<Integer, Entry> types = new TreeMap<>();
    private long firstMs = -1;

    public RtcmStats(long windowMs) { this.windowMs = windowMs; }

    public synchronized void add(int type, long nowMs) {
        if (firstMs < 0) firstMs = nowMs;
        Entry e = types.get(type);
        if (e == null) { e = new Entry(); types.put(type, e); }
        e.count++;
        e.recent.addLast(nowMs);
        trim(e, nowMs);
    }

    public synchronized long count(int type) {
        Entry e = types.get(type);
        return e == null ? 0 : e.count;
    }

    /** Messages per second over the window, or over the time since the first message if shorter. */
    public synchronized double rate(int type, long nowMs) {
        Entry e = types.get(type);
        if (e == null) return 0;
        trim(e, nowMs);
        long span = Math.max(1000, Math.min(windowMs, nowMs - firstMs));
        return e.recent.size() * 1000.0 / span;
    }

    /** One line per type: "type count rate/s". */
    public synchronized String summary(long nowMs) {
        StringBuilder s = new StringBuilder();
        for (Map.Entry<Integer, Entry> t : types.entrySet()) {
            s.append(String.format(Locale.US, "  %4d %7d %6.2f/s\n", t.getKey(), t.getValue().count, rate(t.getKey(), nowMs)));
        }
        return s.toString();
    }

    private void trim(Entry e, long nowMs) {
        while (!e.recent.isEmpty() && e.recent.peekFirst() <= nowMs - windowMs) e.recent.removeFirst();
    }
}
