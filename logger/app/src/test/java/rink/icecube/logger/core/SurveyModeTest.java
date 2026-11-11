package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;

import org.junit.Test;

public class SurveyModeTest {
    @Test public void cyclesSurfaceObjectRef() {
        assertEquals(SurveyMode.OBJECT, SurveyMode.SURFACE.next());
        assertEquals(SurveyMode.REF, SurveyMode.OBJECT.next());
        assertEquals(SurveyMode.SURFACE, SurveyMode.REF.next());
    }

    @Test public void keysBeepsAndColours() {
        for (SurveyMode m : SurveyMode.values()) assertEquals(m, SurveyMode.fromKey(m.key));
        assertEquals(SurveyMode.SURFACE, SurveyMode.fromKey(null));
        assertEquals(SurveyMode.SURFACE, SurveyMode.fromKey("bogus"));
        assertEquals("surface", SurveyMode.SURFACE.key);
        assertEquals(PointRow.OBJECT, SurveyMode.OBJECT.key);
        assertEquals(PointRow.REF, SurveyMode.REF.key);
        assertEquals(1, SurveyMode.SURFACE.beeps);
        assertEquals(2, SurveyMode.OBJECT.beeps);
        assertEquals(3, SurveyMode.REF.beeps);
        assertNotEquals(SurveyMode.SURFACE.color, SurveyMode.OBJECT.color);
        assertNotEquals(SurveyMode.OBJECT.color, SurveyMode.REF.color);
    }

    @Test public void limits() {
        assertEquals(30, SurveyLimits.refSecondsOrDefault("x"));
        assertEquals(10, SurveyLimits.parseRefSeconds(" 10 "));
        assertEquals(300, SurveyLimits.parseRefSeconds("300"));
        assertEquals(2.5, SurveyLimits.parseToleranceCm("2,5"), 0);
        for (String s : new String[] {"9", "301", "", "1.5", "-5"}) {
            try { SurveyLimits.parseRefSeconds(s); org.junit.Assert.fail(s); } catch (IllegalArgumentException ok) { }
        }
        for (String s : new String[] {"0.4", "21", "abc", "2.55"}) {
            try { SurveyLimits.parseToleranceCm(s); org.junit.Assert.fail(s); } catch (IllegalArgumentException ok) { }
        }
    }

    @Test public void linkStates() {
        assertEquals(LinkState.OFF, LinkState.gps(false, false, "off"));
        assertEquals(LinkState.CONNECTED, LinkState.gps(true, true, "connected"));
        assertEquals(LinkState.CONNECTING, LinkState.gps(true, false, "lost: timeout"));
        assertEquals(LinkState.ERROR, LinkState.gps(true, false, "waiting: no USB GPS attached"));
        assertEquals(LinkState.ERROR, LinkState.caster(true, false, "error: refused"));
        assertEquals(LinkState.CONNECTING, LinkState.caster(true, false, "connecting"));
    }
}
