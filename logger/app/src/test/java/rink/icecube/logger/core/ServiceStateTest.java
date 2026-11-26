package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class ServiceStateTest {
    private static final long TWO_MIN = 120_000;
    private static final String S = "2026-11-26_10-00-00";

    // session, gpsActive, gpsConnected, gpsWaiting, casterActive, casterConnected
    private static ServiceState st(String session, boolean gps, boolean gpsUp, boolean waiting, boolean caster, boolean casterUp) {
        return new ServiceState(session, gps, gpsUp, waiting, caster, casterUp);
    }

    @Test public void waitingForTheRover() {
        ServiceState s = st(null, true, false, true, false, false);
        assertEquals("Waiting for the rover\u2026 (up to 2 min)", s.text(TWO_MIN));
        assertEquals("Waiting for the rover\u2026 (up to 0.5 min)", s.text(30_000));
        assertTrue(s.waitingForRover());
        assertFalse(s.idle());
        assertEquals("Stop", s.stopLabel());
    }

    @Test public void noSessionTexts() {
        assertEquals("GPS connected, no session", st(null, true, true, true, false, false).text(TWO_MIN));
        assertEquals("Connecting the GPS\u2026", st(null, true, false, false, false, false).text(TWO_MIN));
        assertEquals("Caster connected, no GPS", st(null, false, false, false, true, true).text(TWO_MIN));
        assertEquals("Caster connecting, no GPS", st(null, false, false, false, true, false).text(TWO_MIN));
        assertEquals("Stopping", st(null, false, false, false, false, false).text(TWO_MIN));
    }

    @Test public void sessionTexts() {
        assertEquals("Logging session " + S + ": GPS and caster", st(S, true, true, false, true, true).text(TWO_MIN));
        assertEquals("Logging session " + S + ": GPS", st(S, true, true, false, false, false).text(TWO_MIN));
        assertEquals("Logging session " + S + ": GPS, caster connecting", st(S, true, true, false, true, false).text(TWO_MIN));
        assertEquals("Logging session " + S + ": GPS not connected (retrying), caster connected",
                st(S, true, false, false, true, true).text(TWO_MIN));
        assertEquals("Caster connected, no GPS \u00b7 session " + S, st(S, false, false, false, true, true).text(TWO_MIN));
        assertEquals("Session " + S + " open, no GPS or caster", st(S, false, false, false, false, false).text(TWO_MIN));
    }

    @Test public void noStopActionWhileASessionIsOpen() {
        assertNull(st(S, false, false, false, false, false).stopLabel());
        assertNull(st(S, true, true, false, true, true).stopLabel());
        assertNull(st(S, false, false, false, true, true).stopLabel());
        assertNull(st(S, true, false, true, false, false).stopLabel());
    }

    @Test public void stopActionWithoutASession() {
        assertEquals("Stop", st(null, true, false, true, false, false).stopLabel());   // waiting for the rover
        assertEquals("Stop", st(null, false, false, false, true, true).stopLabel());   // caster only
        assertEquals("Stop", st(null, true, true, false, false, false).stopLabel());   // GPS connected, no session
    }

    @Test public void aWaitingGpsWithASessionIsNotWaitingForTheRover() {
        ServiceState s = st(S, true, false, true, false, false);
        assertFalse(s.waitingForRover());
        assertFalse(s.waitExpired(0, 10 * TWO_MIN, TWO_MIN));
        assertTrue(s.text(TWO_MIN).startsWith("Logging session"));
    }

    @Test public void waitExpiresAtTheTimeout() {
        ServiceState s = st(null, true, false, true, false, false);
        assertFalse(s.waitExpired(1000, 1000, TWO_MIN));
        assertFalse(s.waitExpired(1000, 1000 + TWO_MIN - 1, TWO_MIN));
        assertTrue(s.waitExpired(1000, 1000 + TWO_MIN, TWO_MIN));
        assertTrue(s.waitExpired(1000, 1000 + 5 * TWO_MIN, TWO_MIN));
    }

    @Test public void waitNeverExpiresOnceConnectedOrForAManualConnect() {
        assertFalse(st(null, true, true, true, false, false).waitExpired(0, 10 * TWO_MIN, TWO_MIN));
        assertFalse(st(null, true, false, false, false, false).waitExpired(0, 10 * TWO_MIN, TWO_MIN));
        assertFalse(st(null, false, false, true, false, false).waitExpired(0, 10 * TWO_MIN, TWO_MIN));
    }

    @Test public void idle() {
        assertTrue(st(null, false, false, false, false, false).idle());
        assertFalse(st(S, false, false, false, false, false).idle());
        assertFalse(st(null, false, false, false, true, false).idle());
        assertFalse(st(null, true, false, false, false, false).idle());
    }

    @Test public void taskRemovalStopsOnlyWithoutASession() {
        assertTrue(st(null, true, false, true, false, false).stopOnTaskRemoved());
        assertTrue(st(null, true, true, false, true, true).stopOnTaskRemoved());
        assertFalse(st(S, true, true, false, true, true).stopOnTaskRemoved());
        assertFalse(st(S, false, false, false, false, false).stopOnTaskRemoved());
    }

    @Test public void minutes() {
        assertEquals("2 min", ServiceState.minutes(120_000));
        assertEquals("0.5 min", ServiceState.minutes(30_000));
        assertEquals("60 min", ServiceState.minutes(3_600_000));
        assertEquals("1.5 min", ServiceState.minutes(90_000));
    }

    @Test public void gpsWaitLimit() {
        assertEquals(2, Limit.GPS_WAIT_MIN.def, 0);
        assertEquals(0.5, Limit.GPS_WAIT_MIN.parse("0.5"), 0);
        assertEquals(60, Limit.GPS_WAIT_MIN.parse("60"), 0);
        assertEquals(2, Limit.GPS_WAIT_MIN.orDefault("0"), 0);
        assertEquals(2, Limit.GPS_WAIT_MIN.orDefault("61"), 0);
    }
}
