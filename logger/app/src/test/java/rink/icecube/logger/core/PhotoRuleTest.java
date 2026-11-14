package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class PhotoRuleTest {
    static final long S = 1_000_000_000L;

    @Test public void attachesWithinSixtySeconds() {
        assertEquals(4, PhotoRule.attachTo(4, 100 * S, 100 * S));
        assertEquals(4, PhotoRule.attachTo(4, 100 * S, 160 * S));
        assertEquals(-1, PhotoRule.attachTo(4, 100 * S, 160 * S + 1));
        assertEquals(-1, PhotoRule.attachTo(0, 100 * S, 101 * S));       // no capture yet
        assertEquals(-1, PhotoRule.attachTo(4, 100 * S, 99 * S));        // clock went backwards
    }

    @Test public void namesAndColumn() {
        assertEquals("photo_12_20261111T140322Z.jpg", PhotoRule.fileName(12, "20261111T140322Z"));
        assertEquals("a.jpg", PhotoRule.add("", "a.jpg"));
        assertEquals("a.jpg;b.jpg", PhotoRule.add("a.jpg", "b.jpg"));
    }
}
