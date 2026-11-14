package rink.icecube.logger;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;

import rink.icecube.logger.core.ScreenOrientation;

/**
 * Shared screen helpers: the orientation lock from Settings > Display (every activity calls
 * {@link #applyOrientation} in onCreate and onResume), the landscape test the code-built
 * layouts use, and a container that caps the content width on wide screens.
 *
 * Rotation: every activity declares configChanges for orientation and size in the manifest,
 * so a rotation does not recreate it. Views, typed text, open dialogs and the map's zoom,
 * centre and follow mode stay as they are; MainActivity (the map) and DetailsActivity rearrange the same
 * views in onConfigurationChanged.
 */
final class Screen {
    private Screen() { }

    /** Applies the Screen orientation setting. Auto leaves it to the system and its auto-rotate toggle. */
    static void applyOrientation(Activity a) {
        int want = ScreenOrientation.requested(Settings.prefs(a).getString(Settings.ORIENTATION, ScreenOrientation.DEFAULT));
        if (a.getRequestedOrientation() != want) a.setRequestedOrientation(want);
    }

    static boolean landscape(Context c) {
        return c.getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
    }

    static int dp(Context c, float v) { return Math.round(v * c.getResources().getDisplayMetrics().density); }

    /** Removes a view from its parent so it can be placed in a new layout. */
    static <T extends View> T detach(T v) {
        if (v.getParent() instanceof ViewGroup) ((ViewGroup) v.getParent()).removeView(v);
        return v;
    }

    /** Widest content on Settings and Sessions, so a landscape screen keeps readable line lengths. */
    static final int MAX_WIDTH_DP = 640;

    /** A FrameLayout no wider than MAX_WIDTH_DP; give it gravity CENTER_HORIZONTAL in its parent to centre it. */
    static final class MaxWidth extends FrameLayout {
        private final int maxPx;

        MaxWidth(Context c) {
            super(c);
            maxPx = dp(c, MAX_WIDTH_DP);
        }

        @Override protected void onMeasure(int widthSpec, int heightSpec) {
            if (MeasureSpec.getSize(widthSpec) > maxPx) {
                int mode = MeasureSpec.getMode(widthSpec);
                widthSpec = MeasureSpec.makeMeasureSpec(maxPx, mode == MeasureSpec.UNSPECIFIED ? MeasureSpec.AT_MOST : mode);
            }
            super.onMeasure(widthSpec, heightSpec);
        }
    }
}
