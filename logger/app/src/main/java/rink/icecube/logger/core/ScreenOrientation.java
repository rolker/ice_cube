package rink.icecube.logger.core;

/**
 * The Display > Screen orientation setting: its stored keys, the labels shown in Settings
 * and the value passed to Activity.setRequestedOrientation. Auto follows the system
 * (including its auto-rotate toggle); the others lock the screen to the phone's mount so
 * a tilting pole never flips it, whatever the system toggle says.
 */
public final class ScreenOrientation {
    public static final String AUTO = "auto", PORTRAIT = "portrait", LANDSCAPE = "landscape",
            REVERSE_LANDSCAPE = "reverse_landscape";
    /** Stored keys, in the order of LABELS. */
    public static final String[] KEYS = {AUTO, PORTRAIT, LANDSCAPE, REVERSE_LANDSCAPE};
    public static final String[] LABELS = {"Auto (follow the system)", "Portrait", "Landscape", "Reverse landscape"};
    public static final String DEFAULT = AUTO;

    // android.content.pm.ActivityInfo.SCREEN_ORIENTATION_* values (checked in the test).
    static final int UNSPECIFIED = -1, ANDROID_PORTRAIT = 1, ANDROID_LANDSCAPE = 0, ANDROID_REVERSE_LANDSCAPE = 8;

    private ScreenOrientation() { }

    /** Index of a stored key in KEYS; an unknown or missing key counts as Auto. */
    public static int index(String key) {
        for (int i = 0; i < KEYS.length; i++) if (KEYS[i].equals(key)) return i;
        return 0;
    }

    /** The ActivityInfo.SCREEN_ORIENTATION_* value for a stored key (unknown means Auto). */
    public static int requested(String key) {
        switch (KEYS[index(key)]) {
            case PORTRAIT: return ANDROID_PORTRAIT;
            case LANDSCAPE: return ANDROID_LANDSCAPE;
            case REVERSE_LANDSCAPE: return ANDROID_REVERSE_LANDSCAPE;
            default: return UNSPECIFIED;
        }
    }
}
