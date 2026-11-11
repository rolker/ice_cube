package rink.icecube.logger;

import android.app.Activity;
import android.graphics.Typeface;
import android.content.res.Configuration;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import rink.icecube.logger.core.GeoJson;
import rink.icecube.logger.core.LocalFrame;
import rink.icecube.logger.core.TrackPoint;

/**
 * Full-screen local map: switched-on layers, the live position coloured by fix quality,
 * a 2-minute trail and this session's control marks. Buttons along the bottom (portrait)
 * or down the right edge (landscape): Center (follow the position), Fit, Mark control
 * point. The screen stays on.
 *
 * Origin: the first point of a kind="base" feature in the switched-on layers; otherwise
 * the session's first GPS fix; otherwise, so layers can still be viewed, the first vertex
 * of the first layer (the map moves to the fix origin once one arrives).
 */
public final class MapActivity extends Activity {
    private MapView map;
    private Button center, fit, mark;
    private TextView info, banner;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private List<Layers.LayerFile> files = new ArrayList<>();
    private String layerKey = "";
    private int layerErrors;
    private double[] baseOrigin;          // lon, lat, h of the base point in switched-on layers
    private String baseName;
    private double[] firstVertex;
    private String originKind = "";

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            refresh();
            ui.postDelayed(this, 250);
        }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Screen.applyOrientation(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        int pad = Screen.dp(this, 8);
        info = new TextView(this);
        info.setTypeface(Typeface.MONOSPACE);
        info.setTextSize(13);
        info.setPadding(pad, pad / 2, pad, pad / 2);
        banner = new TextView(this);
        banner.setBackgroundColor(0xFFC62828);
        banner.setTextColor(0xFFFFFFFF);
        banner.setTypeface(Typeface.DEFAULT_BOLD);
        banner.setPadding(pad, pad / 2, pad, pad / 2);
        banner.setVisibility(View.GONE);
        map = new MapView(this);
        map.setId(R.id.map);   // lets the map save its view if the screen is ever recreated
        center = button("Center", v -> map.recenter());
        fit = button("Fit", v -> map.fit());
        mark = button("Mark control", v -> {
            LoggerService s = LoggerService.current;
            if (s == null || !s.sessionOpen()) { Toast.makeText(this, "No session open", Toast.LENGTH_SHORT).show(); return; }
            int n = s.markControl();
            Toast.makeText(this, s.currentFix() == null ? "Marked C" + n + " (no fix: not on the map)" : "Marked C" + n,
                    Toast.LENGTH_SHORT).show();
        });
        arrange();
    }

    /**
     * Rotation does not recreate this screen (see Screen): the same MapView, with its zoom,
     * centre and follow mode, is moved into the layout for the new orientation.
     */
    @Override public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        arrange();
    }

    /**
     * Portrait: info and banner on top, map, buttons in a row along the bottom. Landscape:
     * buttons in a column on the right edge, info, banner and map to their left, status bar
     * hidden. The legend, scale bar and origin cross are drawn inside the map, so they never
     * sit under a button.
     */
    private void arrange() {
        boolean land = Screen.landscape(this);
        for (View v : new View[] {info, banner, map, center, fit, mark}) Screen.detach(v);
        showStatusBar(!land);
        LinearLayout left = new LinearLayout(this);
        left.setOrientation(LinearLayout.VERTICAL);
        left.addView(info);
        left.addView(banner);
        left.addView(map, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(land ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        for (Button x : new Button[] {center, fit, mark}) {
            buttons.addView(x, land ? new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1)
                    : new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        }
        if (land) {
            LinearLayout root = new LinearLayout(this);
            root.setOrientation(LinearLayout.HORIZONTAL);
            root.addView(left, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1));
            root.addView(buttons, new LinearLayout.LayoutParams(Screen.dp(this, 128), LinearLayout.LayoutParams.MATCH_PARENT));
            setContentView(root);
        } else {
            left.addView(buttons);
            setContentView(left);
        }
    }

    private void showStatusBar(boolean show) {
        if (Build.VERSION.SDK_INT < 30) return;
        WindowInsetsController w = getWindow().getInsetsController();
        if (w == null) return;
        if (show) w.show(WindowInsets.Type.statusBars());
        else {
            w.hide(WindowInsets.Type.statusBars());
            w.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        }
    }

    private Button button(String t, View.OnClickListener l) {
        Button x = new Button(this);
        x.setText(t);
        x.setMinHeight(Screen.dp(this, MainActivity.BUTTON_DP));
        x.setMinimumHeight(Screen.dp(this, MainActivity.BUTTON_DP));
        x.setOnClickListener(l);
        return x;
    }

    @Override protected void onResume() {
        super.onResume();
        Screen.applyOrientation(this);
        Layers.getAsync(this, l -> { files = l; applyLayers(); });
        ui.post(tick);
    }

    @Override protected void onPause() {
        super.onPause();
        ui.removeCallbacks(tick);
    }

    /** Re-projects only when the set of switched-on layers changed. */
    private void applyLayers() {
        Set<String> off = Settings.layersOff(Settings.prefs(this));
        List<GeoJson.Feature> fs = new ArrayList<>();
        StringBuilder key = new StringBuilder().append(Layers.generation()).append('|');
        baseOrigin = null;
        baseName = null;
        firstVertex = null;
        layerErrors = 0;
        for (Layers.LayerFile lf : files) {
            if (lf.layer == null) { layerErrors++; continue; }
            if (off.contains(lf.name())) continue;
            key.append(lf.name()).append('|');
            fs.addAll(lf.layer.features);
            if (baseOrigin == null) {
                for (GeoJson.Feature f : lf.layer.features) {
                    if (f.isBase()) {
                        double[] p = f.parts.get(0);
                        baseOrigin = new double[] {p[0], p[1], p[2]};
                        baseName = (f.name != null ? "'" + f.name + "' in " : "") + lf.name();
                        break;
                    }
                }
            }
            if (firstVertex == null && !lf.layer.features.isEmpty()) {
                double[] p = lf.layer.features.get(0).parts.get(0);
                firstVertex = new double[] {p[0], p[1], p[2]};
            }
        }
        if (!key.toString().equals(layerKey)) {
            layerKey = key.toString();
            originKind = "";   // forces the origin to be chosen again
            chooseOrigin(LoggerService.current);
            map.setFeatures(fs);
        }
    }

    private void chooseOrigin(LoggerService s) {
        TrackPoint first = s == null ? null : s.firstFix();
        String kind;
        double lat, lon, h;
        String note;
        if (baseOrigin != null) {
            kind = "base"; lon = baseOrigin[0]; lat = baseOrigin[1]; h = baseOrigin[2];
            note = "origin: base point " + baseName;
        } else if (first != null) {
            kind = "fix" + first.elapsedMs; lat = first.lat; lon = first.lon; h = first.h;
            note = "origin: first fix of this session";
        } else if (firstVertex != null) {
            kind = "vertex"; lon = firstVertex[0]; lat = firstVertex[1]; h = firstVertex[2];
            note = "origin: first layer vertex (no base point, no fix yet)";
        } else {
            return;
        }
        if (kind.equals(originKind)) return;
        originKind = kind;
        map.setFrame(new LocalFrame(lat, lon, h), note);
    }

    private void refresh() {
        LoggerService s = LoggerService.current;
        chooseOrigin(s);
        TrackPoint fix = s == null ? null : s.currentFix();
        map.setLive(fix, s == null ? new TrackPoint[0] : s.trail(), s == null ? new TrackPoint[0] : s.marks());
        if (s != null) s.layersMaybeChanged();

        StringBuilder t = new StringBuilder();
        double pole = s != null ? s.poleHeight() : Settings.poleHeight(Settings.prefs(this));
        rink.icecube.logger.core.Gga g = s == null ? null : s.fix();
        if (fix == null) t.append(s != null && s.gpsConnected() ? "no fix" : "GPS not connected");
        else t.append(String.format(Locale.US, "%s  sats %d", g != null ? g.qualityName() : "fix", g != null ? g.sats : 0));
        if (fix != null && !Double.isNaN(fix.h)) {
            t.append(String.format(Locale.US, "\nground %.3f m  (antenna ell. %.3f - pole %.3f)", fix.h - pole, fix.h, pole));
        }
        t.append('\n').append(MainActivity.frameText(s, this));
        t.append('\n').append(map.frame() == null ? "origin: none yet" : map.originNote());
        if (layerErrors > 0) t.append(String.format(Locale.US, "\n%d layer file(s) have errors (see Settings)", layerErrors));
        String okLine = MainActivity.layerOkText(s);
        if (okLine != null) t.append('\n').append(okLine);
        info.setText(t);
        String w = MainActivity.warnings(s);
        banner.setVisibility(w == null ? View.GONE : View.VISIBLE);
        if (w != null) banner.setText(w);
    }
}
