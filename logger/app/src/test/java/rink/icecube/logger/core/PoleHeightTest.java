package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import org.junit.Test;

public class PoleHeightTest {
    @Test public void parsesValidValues() {
        assertEquals(1.8, PoleHeight.parse("1.8"), 0);
        assertEquals(1.755, PoleHeight.parse(" 1,755 "), 0);
        assertEquals(2.0, PoleHeight.parse("2"), 0);
        assertEquals(0.0, PoleHeight.parse("0"), 0);
        assertEquals(5.0, PoleHeight.parse("5.000"), 0);
        assertEquals(0.5, PoleHeight.parse(".5"), 0);
    }

    @Test public void rejectsInvalidValues() {
        for (String s : new String[] {"", "  ", null, "abc", "-1", "5.001", "1.2345", "1.8m", "NaN", "Infinity", ".", "1..2", "1e0"}) {
            try {
                PoleHeight.parse(s);
                fail("accepted " + s);
            } catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void formatAndPresets() {
        assertEquals("1.800", PoleHeight.format(1.8));
        assertEquals("0.005", PoleHeight.format(0.005));
        assertEquals(0, PoleHeight.presetIndex(1.8));
        assertEquals(4, PoleHeight.presetIndex(PoleHeight.parse("1.400")));
        assertEquals(-1, PoleHeight.presetIndex(1.75));
        assertEquals(PoleHeight.DEFAULT, PoleHeight.parseOrDefault("bad"), 0);
        assertEquals(1.234, PoleHeight.parseOrDefault("1.234"), 0);
    }
}
