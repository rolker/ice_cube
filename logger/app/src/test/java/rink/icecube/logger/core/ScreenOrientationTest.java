package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;

import android.content.pm.ActivityInfo;

import org.junit.Test;

public class ScreenOrientationTest {
    @Test public void mapsKeysToAndroidValues() {
        // ActivityInfo constants are compile-time constants, so this runs on the JVM.
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, ScreenOrientation.requested("auto"));
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, ScreenOrientation.requested("portrait"));
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, ScreenOrientation.requested("landscape"));
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE, ScreenOrientation.requested("reverse_landscape"));
    }

    @Test public void unknownOrMissingIsAuto() {
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, ScreenOrientation.requested(null));
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, ScreenOrientation.requested("sideways"));
        assertEquals(0, ScreenOrientation.index(null));
        assertEquals(ScreenOrientation.DEFAULT, ScreenOrientation.KEYS[ScreenOrientation.index("")]);
    }

    @Test public void keysAndLabelsLineUp() {
        assertEquals(ScreenOrientation.KEYS.length, ScreenOrientation.LABELS.length);
        for (int i = 0; i < ScreenOrientation.KEYS.length; i++) assertEquals(i, ScreenOrientation.index(ScreenOrientation.KEYS[i]));
        assertEquals("Auto (follow the system)", ScreenOrientation.LABELS[0]);
    }
}
