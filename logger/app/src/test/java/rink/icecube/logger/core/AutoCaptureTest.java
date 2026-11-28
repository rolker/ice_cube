package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Synthetic 1 Hz epochs in local metres; defaults 2°, 2 s, 2 cm (#35). */
public class AutoCaptureTest {
    private static final long S = 1_000_000_000L;

    private static AutoCapture auto() { return new AutoCapture(2, 2, 0.02); }

    /** A good epoch at t seconds, plumb (0.5°), at (e, n). */
    private static AutoCapture.Input at(double t, double e, double n) {
        AutoCapture.Input in = new AutoCapture.Input();
        in.tNs = Math.round(t * S);
        in.e = e;
        in.n = n;
        in.plumbDeg = 0.5;
        return in;
    }

    /** Feeds one epoch and, at a fire, reports the capture as taken (what Survey does once it captured). */
    private static AutoCapture.Decision feed(AutoCapture a, AutoCapture.Input in) {
        AutoCapture.Decision d = a.onEpoch(in);
        if (d.fire) a.taken(d.e, d.n);
        return d;
    }

    /** Feeds good epochs at (e, n) from t0 to t1 (1 s apart), taking each fire; returns the number of fires. */
    private static int hold(AutoCapture a, double t0, double t1, double e, double n) {
        int fires = 0;
        for (double t = t0; t <= t1 + 1e-9; t += 1) if (feed(a, at(t, e, n)).fire) fires++;
        return fires;
    }

    @Test public void firesAfterTheHoldTime() {
        AutoCapture a = auto();
        AutoCapture.Decision d = a.onEpoch(at(0, 0, 0));
        assertFalse(d.fire);
        assertEquals(AutoCapture.Why.HOLDING, d.why);
        d = a.onEpoch(at(1, 0.003, 0));
        assertFalse(d.fire);
        assertEquals(1.0, d.heldS, 1e-9);
        d = a.onEpoch(at(2, 0, 0.003));
        assertTrue(d.fire);
        assertEquals(AutoCapture.Why.CAPTURED, d.why);
        assertEquals(3, d.epochs);
        assertEquals(2.0, d.heldS, 1e-9);
        assertTrue(d.spreadM < 0.003);
        assertEquals(0.001, d.e, 1e-12);   // the window's mean
        assertEquals(0.001, d.n, 1e-12);
        assertFalse(a.latched());   // not until the caller says it captured
    }

    /** A fire the caller could not capture (no RTK fixed average) leaves the stop untaken. */
    @Test public void aFireIsNotLatchedUntilTaken() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.onEpoch(at(1, 0, 0));
        assertTrue(a.onEpoch(at(2, 0, 0)).fire);   // skipped: no taken()
        assertEquals(AutoCapture.Why.HOLDING, a.onEpoch(at(3, 0, 0)).why);
        a.onEpoch(at(4, 0, 0));
        AutoCapture.Decision d = a.onEpoch(at(5, 0, 0));
        assertTrue(d.fire);
        a.taken(d.e, d.n);
        assertTrue(a.latched());
        assertEquals(AutoCapture.Why.CAPTURED, a.onEpoch(at(6, 0, 0)).why);
    }

    /** Undoing the latched capture (Survey calls reset) lets the same stop be shot again. */
    @Test public void resetClearsTheLatch() {
        AutoCapture a = auto();
        assertEquals(1, hold(a, 0, 2, 0, 0));
        assertTrue(a.latched());
        a.reset();
        assertFalse(a.latched());
        assertEquals(1, hold(a, 3, 5, 0, 0));
    }

    @Test public void oneCapturePerStop() {
        AutoCapture a = auto();
        assertEquals(1, hold(a, 0, 30, 0, 0));
        AutoCapture.Decision d = a.onEpoch(at(31, 0.01, 0));
        assertFalse(d.fire);
        assertEquals(AutoCapture.Why.CAPTURED, d.why);
        assertTrue(a.latched());
    }

    @Test public void tiltAtOneEpochRestartsTheHold() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.onEpoch(at(1, 0, 0));
        AutoCapture.Input tilted = at(2, 0, 0);
        tilted.plumbDeg = 2.1;
        AutoCapture.Decision d = a.onEpoch(tilted);
        assertFalse(d.fire);
        assertEquals(AutoCapture.Why.NOT_PLUMB, d.why);
        assertFalse(a.onEpoch(at(3, 0, 0)).fire);
        assertFalse(a.onEpoch(at(4, 0, 0)).fire);
        assertTrue(a.onEpoch(at(5, 0, 0)).fire);
    }

    @Test public void unknownPlumbIsNotPlumb() {
        AutoCapture a = auto();
        AutoCapture.Input in = at(0, 0, 0);
        in.plumbDeg = Double.NaN;
        assertEquals(AutoCapture.Why.NOT_PLUMB, a.onEpoch(in).why);
    }

    @Test public void scatterOverTheLimitRestartsTheHold() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.onEpoch(at(1, 0, 0));
        AutoCapture.Decision d = a.onEpoch(at(2, 0.07, 0));   // 4.7 cm from the mean of three
        assertFalse(d.fire);
        assertEquals(AutoCapture.Why.MOVING, d.why);
        a.onEpoch(at(3, 0.07, 0));
        assertTrue(a.onEpoch(at(4, 0.07, 0)).fire);   // held 2 s from the moved epoch
    }

    @Test public void eachUnmetGateReportsItsWhyAndNeverFires() {
        String[] gates = {"enabled", "surface", "calibrating", "calibrated", "tilt", "ref", "fixed", "young", "suspect"};
        AutoCapture.Why[] whys = {AutoCapture.Why.OFF_SETTING, AutoCapture.Why.NOT_SURFACE,
                AutoCapture.Why.CALIBRATING, AutoCapture.Why.NO_CALIBRATION, AutoCapture.Why.TILT_SET,
                AutoCapture.Why.REF_RUNNING, AutoCapture.Why.NO_FIX, AutoCapture.Why.FIX_YOUNG,
                AutoCapture.Why.FIX_SUSPECT};
        for (int k = 0; k < gates.length; k++) {
            AutoCapture a = auto();
            for (int t = 0; t < 10; t++) {
                AutoCapture.Input in = at(t, 0, 0);
                switch (gates[k]) {
                    case "enabled": in.enabled = false; break;
                    case "surface": in.surface = false; break;
                    case "calibrating": in.calibrating = true; break;
                    case "calibrated": in.calibrated = false; break;
                    case "tilt": in.tiltRunning = true; break;
                    case "ref": in.refRunning = true; break;
                    case "fixed": in.fixed = false; break;
                    case "young": in.young = true; break;
                    default: in.suspect = true; break;
                }
                AutoCapture.Decision d = a.onEpoch(in);
                assertFalse(gates[k], d.fire);
                assertEquals(gates[k], whys[k], d.why);
                assertFalse(d.why.armed());
            }
        }
    }

    @Test public void gateUnmetMidHoldRestartsIt() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.onEpoch(at(1, 0, 0));
        AutoCapture.Input young = at(2, 0, 0);
        young.young = true;
        assertFalse(a.onEpoch(young).fire);
        assertFalse(a.onEpoch(at(3, 0, 0)).fire);
        assertFalse(a.onEpoch(at(4, 0, 0)).fire);
        assertTrue(a.onEpoch(at(5, 0, 0)).fire);
    }

    @Test public void rearmsAfterMovingAwayPastThreeTimesTheStillLimit() {
        AutoCapture a = auto();   // 2 cm still limit: re-arm past 6 cm
        assertEquals(1, hold(a, 0, 2, 0, 0));
        assertEquals(0, hold(a, 3, 10, 0.059, 0));   // 5.9 cm: still this stop
        assertEquals(AutoCapture.Why.CAPTURED, a.last());
        assertEquals(1, hold(a, 11, 13, 0.061, 0));  // 6.1 cm: a new stop, held again
    }

    /** Tilting (as in a tilt set or a lean about the tip) never re-arms: only moving away does. */
    @Test public void tiltingAtTheSamePointDoesNotRearm() {
        AutoCapture a = auto();
        assertEquals(1, hold(a, 0, 2, 0, 0));
        for (int t = 3; t <= 8; t++) {
            AutoCapture.Input tilted = at(t, t % 2 == 0 ? 0.47 : 0.066, 0);   // leaning 15° or 2.1°
            tilted.plumbDeg = t % 2 == 0 ? 15 : 2.1;
            assertEquals(AutoCapture.Why.CAPTURED, a.onEpoch(tilted).why);
            assertTrue(a.latched());
        }
        assertEquals(0, hold(a, 9, 20, 0, 0));   // stood plumb again on the same point
        assertEquals(AutoCapture.Why.CAPTURED, a.last());
    }

    /** A NaN gravity reading (plumb unknown) or a NaN position never clears the latch. */
    @Test public void nanReadingsNeverClearTheLatch() {
        AutoCapture a = auto();
        assertEquals(1, hold(a, 0, 2, 0, 0));
        AutoCapture.Input noGravity = at(3, 0, 0);
        noGravity.plumbDeg = Double.NaN;
        assertEquals(AutoCapture.Why.CAPTURED, a.onEpoch(noGravity).why);
        AutoCapture.Input noPosition = at(4, Double.NaN, Double.NaN);
        assertEquals(AutoCapture.Why.CAPTURED, a.onEpoch(noPosition).why);
        assertTrue(a.latched());
        assertEquals(0, hold(a, 5, 10, 0, 0));
    }

    /** A capture reported at a tilt set's or a calibration's tip latches the stop like MARK. */
    @Test public void aTipTakenLatchesThePlumbStopOnIt() {
        AutoCapture a = auto();
        AutoCapture.Input tilt = at(0, 0.3, 0);   // leaning: the antenna 30 cm off the tip
        tilt.tiltRunning = true;
        a.onEpoch(tilt);
        a.taken(0, 0);   // the set finished: its tip
        AutoCapture.Input leaning = at(1, 0.3, 0);   // still leaning after the set: 30 cm off the tip
        leaning.plumbDeg = 10;
        assertEquals(AutoCapture.Why.CAPTURED, a.onEpoch(leaning).why);
        assertTrue(a.latched());   // a leaning antenna says nothing about the tip
        assertEquals(0, hold(a, 2, 10, 0.005, 0));   // stood up plumb on the tip: no duplicate
        assertEquals(AutoCapture.Why.CAPTURED, a.last());
        assertEquals(1, hold(a, 11, 13, 1, 0));   // plumb at the next point
    }

    @Test public void manualMarkCountsAsThisStopsCapture() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.taken(0.002, 0);   // MARK pressed while holding
        assertEquals(0, hold(a, 1, 20, 0, 0));
        assertEquals(AutoCapture.Why.CAPTURED, a.last());
        assertEquals(1, hold(a, 21, 23, 1, 0));   // next point
    }

    @Test public void epochGapRestartsTheHold() {
        AutoCapture a = auto();
        a.onEpoch(at(0, 0, 0));
        a.onEpoch(at(1, 0, 0));
        assertFalse(a.onEpoch(at(3.5, 0, 0)).fire);   // 2.5 s gap
        assertFalse(a.onEpoch(at(4.5, 0, 0)).fire);
        assertTrue(a.onEpoch(at(5.5, 0, 0)).fire);
    }

    @Test public void settingsChangesApply() {
        AutoCapture a = auto();
        a.setLimits(5, 4, 0.05);
        AutoCapture.Input in = at(0, 0, 0);
        in.plumbDeg = 4;
        assertEquals(AutoCapture.Why.HOLDING, a.onEpoch(in).why);
        assertEquals(0, hold(a, 1, 3, 0.04, 0));   // 4 cm moves within 5 cm; 4 s not yet held
        assertTrue(a.onEpoch(at(4, 0.04, 0)).fire);
        assertEquals(0.15, a.rearmM(), 1e-12);
    }

    @Test public void cuesOnlyFixChangesThatLastOneSecond() {
        AutoCapture a = auto();
        assertEquals(AutoCapture.Cue.NONE, a.onEpoch(at(0, 0, 0)).cue);   // first epoch: no cue
        AutoCapture.Input lost = at(1, 0, 0);
        lost.fixed = false;
        assertEquals(AutoCapture.Cue.NONE, a.onEpoch(lost).cue);   // 0 s off: not yet
        assertEquals(AutoCapture.Cue.NONE, a.onEpoch(at(1.5, 0, 0)).cue);   // back within 1 s: no cue
        lost = at(2, 0, 0);
        lost.fixed = false;
        a.onEpoch(lost);
        lost = at(3, 0, 0);
        lost.fixed = false;
        assertEquals(AutoCapture.Cue.OFF, a.onEpoch(lost).cue);
        lost = at(4, 0, 0);
        lost.young = true;   // still off, now young: no new cue
        assertEquals(AutoCapture.Cue.NONE, a.onEpoch(lost).cue);
        a.onEpoch(at(5, 0, 0));
        assertEquals(AutoCapture.Cue.ARMED, a.onEpoch(at(6, 0, 0)).cue);
        // a tilt set is the user's own doing: no cue either way
        for (int t = 7; t <= 9; t++) {
            AutoCapture.Input tilt = at(t, 0, 0);
            tilt.tiltRunning = true;
            assertEquals(AutoCapture.Cue.NONE, a.onEpoch(tilt).cue);
        }
        for (int t = 10; t <= 12; t++) assertEquals(AutoCapture.Cue.NONE, a.onEpoch(at(t, 5, 0)).cue);
        // plumb and still are not gates: tilting the pole never cues
        for (int t = 13; t <= 16; t++) {
            AutoCapture.Input tilted = at(t, 9, 0);
            tilted.plumbDeg = 20;
            assertEquals(AutoCapture.Cue.NONE, a.onEpoch(tilted).cue);
        }
    }

    @Test public void armedCoversThePoleStates() {
        assertTrue(AutoCapture.Why.NOT_PLUMB.armed());
        assertTrue(AutoCapture.Why.MOVING.armed());
        assertTrue(AutoCapture.Why.HOLDING.armed());
        assertTrue(AutoCapture.Why.CAPTURED.armed());
        assertFalse(AutoCapture.Why.FIX_SUSPECT.armed());
        assertTrue(AutoCapture.Why.FIX_YOUNG.byFix());
        assertFalse(AutoCapture.Why.TILT_SET.byFix());
    }
}
