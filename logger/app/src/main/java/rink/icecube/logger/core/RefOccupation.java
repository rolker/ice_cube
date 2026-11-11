package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.List;

/**
 * A reference occupation: after MARK in Ref mode, RTK fixed epochs are collected until
 * they cover the set duration, then averaged.
 *
 * Time counts only while fixed: each fixed epoch adds the time since the previous counted
 * moment (the start, or the previous fixed epoch) if that is at most 2 s. A longer gap
 * (the fix dropped below fixed, or the GPS went quiet, for more than 2 s) is a pause: it
 * is not counted, and counting resumes at the next fixed epoch. Only fixed epochs are
 * averaged. Feed epochs in time order.
 */
public final class RefOccupation {
    public static final long PAUSE_NS = 2_000_000_000L;

    public enum Change { NONE, PAUSED, RESUMED, DONE }

    public final long startNs, durationNs;
    private final List<Epoch> fixed = new ArrayList<>();
    private long lastCountNs, activeNs;
    private boolean paused, done;
    private int pauses;

    public RefOccupation(long startNs, long durationNs) {
        this.startNs = startNs;
        this.durationNs = durationNs;
        lastCountNs = startNs;
    }

    public Change onEpoch(Epoch e) {
        if (done || e == null || e.elapsedNs < lastCountNs) return Change.NONE;
        if (e.fixed()) {
            long gap = e.elapsedNs - lastCountNs;
            Change c = Change.NONE;
            if (gap <= PAUSE_NS) activeNs += gap;
            else if (!paused) pauses++;
            if (paused || gap > PAUSE_NS) c = Change.RESUMED;
            paused = false;
            lastCountNs = e.elapsedNs;
            fixed.add(e);
            if (activeNs >= durationNs) { done = true; return Change.DONE; }
            return c;
        }
        if (!paused && e.elapsedNs - lastCountNs > PAUSE_NS) {
            paused = true;
            pauses++;
            return Change.PAUSED;
        }
        return Change.NONE;
    }

    /** Whether counting is paused at nowNs (no fixed epoch for more than 2 s). */
    public boolean pausedAt(long nowNs) { return !done && (paused || nowNs - lastCountNs > PAUSE_NS); }

    public boolean done() { return done; }
    public int pauses() { return pauses; }
    public int epochs() { return fixed.size(); }
    public double activeSeconds() { return activeNs / 1e9; }
    public double remainingSeconds() { return Math.max(0, (durationNs - activeNs) / 1e9); }
    public double progress() { return durationNs <= 0 ? 1 : Math.min(1, activeNs / (double) durationNs); }

    /** The average of the fixed epochs so far, or null if there are none. */
    public EpochAverage average() { return EpochAverage.of(fixed); }
}
