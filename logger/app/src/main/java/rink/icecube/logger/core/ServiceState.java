package rink.icecube.logger.core;

import java.util.Locale;

/**
 * What the logger service is doing, as its ongoing notification says it, and when it should
 * end (#30). LoggerService fills one from its fields; the rules are here so they are unit
 * tested without Android.
 *
 * Waiting for the rover is a GPS started by Log on GPS connect (autostart) that has not
 * connected yet, with no session open. That wait is bounded: {@link #waitExpired} says when
 * to give up. A session is never ended by the wait or by removing the app's task.
 */
public final class ServiceState {
    /** Session folder name, or null when no session is open. */
    public final String session;
    public final boolean gpsActive, gpsConnected, gpsWaiting, casterActive, casterConnected;

    public ServiceState(String session, boolean gpsActive, boolean gpsConnected, boolean gpsWaiting,
            boolean casterActive, boolean casterConnected) {
        this.session = session;
        this.gpsActive = gpsActive;
        this.gpsConnected = gpsActive && gpsConnected;
        this.gpsWaiting = gpsActive && !gpsConnected && gpsWaiting;
        this.casterActive = casterActive;
        this.casterConnected = casterActive && casterConnected;
    }

    /** No session, no GPS and no caster: the service should stop. */
    public boolean idle() { return session == null && !gpsActive && !casterActive; }

    /** Waiting for the rover with no session (the only state the wait timeout ends). */
    public boolean waitingForRover() { return session == null && gpsWaiting; }

    /**
     * True when the wait for the rover has lasted `timeoutMs` or more. Never with a session
     * open or once the GPS has connected.
     */
    public boolean waitExpired(long waitStartMs, long nowMs, long timeoutMs) {
        return waitingForRover() && nowMs - waitStartMs >= timeoutMs;
    }

    /** Removing the app's task (swiping it away) stops the service unless a session is open. */
    public boolean stopOnTaskRemoved() { return session == null; }

    /**
     * The notification's Stop action label, or null for no Stop action: never while a
     * session is open, so a stray tap cannot end it; the app's REC (with its question) does.
     */
    public String stopLabel() { return session == null ? "Stop" : null; }

    /** The notification text. `waitTimeoutMs` is shown while waiting for the rover. */
    public String text(long waitTimeoutMs) {
        if (session == null) {
            if (gpsWaiting) return "Waiting for the rover\u2026 (up to " + minutes(waitTimeoutMs) + ")";
            if (gpsConnected) return "GPS connected, no session";
            if (gpsActive) return casterActive ? "Connecting the GPS\u2026, " + casterText().toLowerCase(Locale.US) : "Connecting the GPS\u2026";
            if (casterActive) return casterText() + ", no GPS";
            return "Stopping";
        }
        String s = "session " + session;
        if (gpsConnected) return "Logging " + s + ": GPS" + (casterConnected ? " and caster" : casterActive ? ", caster connecting" : "");
        if (gpsActive) return "Logging " + s + ": GPS not connected (retrying)" + (casterActive ? ", " + casterText().toLowerCase(Locale.US) : "");
        if (casterActive) return casterText() + ", no GPS \u00b7 " + s;
        return "Session " + session + " open, no GPS or caster";
    }

    private String casterText() { return casterConnected ? "Caster connected" : "Caster connecting"; }

    /** "2 min", "0.5 min", "90 min". */
    static String minutes(long ms) {
        double m = ms / 60000.0;
        return (m == Math.rint(m) ? String.valueOf((long) m) : String.format(Locale.US, "%.1f", m)) + " min";
    }
}
