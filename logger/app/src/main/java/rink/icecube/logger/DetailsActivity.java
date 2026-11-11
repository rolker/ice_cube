package rink.icecube.logger;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.content.res.Configuration;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.util.List;
import java.util.Locale;

import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.PoleHeight;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.Rtcm3Parser;
import rink.icecube.logger.core.StreamParser;

/**
 * Details: the full status (links, counts, caster statistics, heights, frame, folder) and
 * the link, session and share buttons. Opened from the map's fix chip or menu; the map
 * (MainActivity) is the main screen. Back returns to the map.
 */
public final class DetailsActivity extends Activity {
    private TextView status, big, heights, banner;
    private Button gpsButton, casterButton, sessionButton, mapButton, settingsButton, shareButton, sessionsButton;
    static final int BUTTON_DP = 68;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            refresh();
            ui.postDelayed(this, 500);
        }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Screen.applyOrientation(this);
        setTitle("Details");
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        int pad = Screen.dp(this, 16);
        big = new TextView(this);
        big.setTextSize(30);
        big.setTypeface(Typeface.DEFAULT_BOLD);
        big.setPadding(0, 0, 0, pad / 2);
        heights = new TextView(this);
        heights.setTextSize(17);
        heights.setTypeface(Typeface.MONOSPACE);
        heights.setPadding(0, 0, 0, pad / 2);
        banner = new TextView(this);
        banner.setBackgroundColor(0xFFC62828);
        banner.setTextColor(0xFFFFFFFF);
        banner.setTypeface(Typeface.DEFAULT_BOLD);
        banner.setPadding(pad / 2, pad / 2, pad / 2, pad / 2);
        banner.setVisibility(View.GONE);
        gpsButton = button(v -> onGps());
        casterButton = button(v -> onCaster());
        sessionButton = button(v -> onSession());
        mapButton = button(v -> finish());
        mapButton.setText("Map");
        settingsButton = button(v -> startActivity(new Intent(this, SettingsActivity.class)));
        settingsButton.setText("Settings");
        shareButton = button(v -> shareLast());
        shareButton.setText("Share last session");
        sessionsButton = button(v -> startActivity(new Intent(this, SessionsActivity.class)));
        sessionsButton.setText("Sessions");
        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextIsSelectable(true);
        status.setPadding(0, pad / 2, 0, 0);
        arrange();
    }

    /** Rotation does not recreate this screen (see Screen); the same views are rearranged. */
    @Override public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        arrange();
        refresh();
    }

    /**
     * Portrait: banner on top, then one scrolling column (status, buttons, details).
     * Landscape: banner across the top, status scrolling on the left, large buttons in
     * pairs on the right. The action bar is hidden in landscape to give the rows room.
     */
    private void arrange() {
        boolean land = Screen.landscape(this);
        int pad = Screen.dp(this, 16);
        if (getActionBar() != null) { if (land) getActionBar().hide(); else getActionBar().show(); }
        for (View v : new View[] {big, heights, banner, status, gpsButton, casterButton, sessionButton,
                mapButton, settingsButton, shareButton, sessionsButton}) Screen.detach(v);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.addView(banner, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        if (!land) {
            LinearLayout c = column(pad, pad, pad, pad);
            c.addView(big);
            c.addView(heights);
            for (Button x : new Button[] {gpsButton, casterButton, sessionButton}) c.addView(x, full());
            pair(c, mapButton, settingsButton);
            pair(c, shareButton, sessionsButton);
            c.addView(status);
            root.addView(scroll(c), new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        } else {
            LinearLayout cols = new LinearLayout(this);
            cols.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout left = column(pad, pad / 2, pad / 2, pad / 2);
            left.addView(big);
            left.addView(heights);
            left.addView(status);
            cols.addView(scroll(left), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 55));
            LinearLayout right = column(pad / 2, pad / 2, pad, pad / 2);
            pair(right, gpsButton, casterButton);
            pair(right, sessionButton, mapButton);
            pair(right, settingsButton, sessionsButton);
            right.addView(shareButton, full());
            cols.addView(scroll(right), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 45));
            root.addView(cols, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        }
        setContentView(root);
    }

    private LinearLayout column(int l, int t, int r, int b) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(l, t, r, b);
        return c;
    }

    private ScrollView scroll(View v) {
        ScrollView s = new ScrollView(this);
        s.addView(v);
        return s;
    }

    private static LinearLayout.LayoutParams full() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private void pair(LinearLayout c, Button a, Button b) {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.addView(a, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        r.addView(b, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        c.addView(r, full());
    }

    /** A button for gloves: 68 dp tall, so the drawn button (Material insets 6 dp top and bottom) is 56 dp. */
    private Button button(View.OnClickListener l) {
        Button x = new Button(this);
        x.setMinHeight(Screen.dp(this, BUTTON_DP));
        x.setMinimumHeight(Screen.dp(this, BUTTON_DP));
        x.setOnClickListener(l);
        return x;
    }

    private void shareLast() {
        List<File> all = Storage.sessions(this);
        if (all.isEmpty()) { big.setText("No sessions yet"); return; }
        Sharer.share(this, all.get(0));
    }

    private void showHeights(LoggerService s) {
        double pole = s != null ? s.poleHeight() : Settings.poleHeight(Settings.prefs(this));
        Gga g = s == null || s.currentFix() == null ? null : s.fix();
        StringBuilder t = new StringBuilder();
        if (g == null || Double.isNaN(g.height)) {
            t.append("antenna   --\nground    --");
            if (g != null && !Double.isNaN(g.alt)) t.append(String.format(Locale.US, "\n(GGA has no geoid separation; alt %.3f m)", g.alt));
        } else {
            t.append(String.format(Locale.US, "antenna   %.3f m\nground    %.3f m", g.height, g.height - pole));
        }
        t.append("\npole      ").append(PoleHeight.format(pole)).append(" m");
        t.append("\n(ellipsoidal = GGA alt + geoid sep)\n").append(Status.frameText(s, this));
        String ok = Status.layerOkText(s);
        if (ok != null) t.append('\n').append(ok);
        heights.setText(t);
        String w = Status.warnings(s);
        banner.setVisibility(w == null ? View.GONE : View.VISIBLE);
        if (w != null) banner.setText(w);
    }

    @Override protected void onResume() {
        super.onResume();
        Screen.applyOrientation(this);
        ui.post(tick);
    }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }

    private static LoggerService live() { return Control.live(); }

    private void onGps() {
        LoggerService s = live();
        if (s != null && s.gpsActive()) { Control.disconnectGps(this); return; }
        String why = Control.connectGps(this);
        if (why != null) big.setText(why);
    }

    private void onCaster() {
        LoggerService s = live();
        if (s != null && s.caster() != null) { Control.disconnectCaster(this); return; }
        Control.connectCaster(this);
    }

    private void onSession() {
        if (live() != null) Control.stopAll(this); else Control.startSession(this);
    }

    private void refresh() {
        LoggerService s = live();
        LoggerService last = LoggerService.current;
        boolean session = s != null && s.sessionOpen();
        boolean gpsOn = s != null && s.gpsActive();
        NtripClient n = s == null ? null : s.caster();
        gpsButton.setText(gpsOn ? "Disconnect GPS" : "Connect GPS");
        casterButton.setText(n != null ? "Disconnect caster" : "Connect caster");
        sessionButton.setText(s != null ? "Stop session" : "Start session");
        Control.askUsbPermissionIfNeeded(this);

        if (s != null) s.layersMaybeChanged();
        showHeights(s);
        SharedPreferences p = Settings.prefs(this);
        StringBuilder t = new StringBuilder();
        if (s == null) {
            big.setText(last == null ? "Ready" : "Stopped");
            t.append("gps link  ").append(Settings.bluetooth(p) ? "Bluetooth " + p.getString(Settings.BT_NAME, "(choose in Settings)") : "USB").append('\n');
            t.append("caster    ").append(p.getString(Settings.HOST, "").trim().isEmpty() ? "(set in Settings)" : p.getString(Settings.MOUNT, "")).append('\n');
            if (last != null && last.folder() != null) t.append("last folder\n  ").append(last.folder()).append('\n');
            if (last != null && last.error != null) t.append("ERROR     ").append(last.error).append('\n');
            status.setText(t);
            return;
        }
        Gga g = s.fix();
        big.setText(!s.gpsConnected() ? (gpsOn ? "Waiting for GPS" : n != null ? "Caster only" : "Session open")
                : g == null ? "No GGA yet" : g.qualityName());
        if (session) {
            long secs = s.sessionSeconds();
            t.append(String.format(Locale.US, "session   %d:%02d:%02d\n", secs / 3600, secs / 60 % 60, secs % 60));
        } else {
            t.append("session   starts when the GPS connects\n");
        }
        t.append("\nGPS       ").append(s.gpsStatus()).append('\n');
        String link = s.gpsLinkName();
        if (link != null) t.append("  ").append(link).append('\n');
        double since = s.gpsSecondsSinceData();
        if (since >= 0) t.append(String.format(Locale.US, "  last data %.1f s ago\n", since));
        if (g != null) t.append(String.format(Locale.US, "  sats %d  age %.1f s\n", g.sats, g.age));
        StreamParser sp = s.parser;
        t.append(String.format(Locale.US, "  bytes %.2f MB\n", sp.bytes / 1e6));
        t.append(String.format(Locale.US, "  RAWX %d  lost %.2f%%  max gap %.2f s\n", sp.rawxEpochs, sp.rawxLossPct(), sp.rawxMaxGap));
        t.append(String.format(Locale.US, "  GGA  %d  lost %.2f%%  max gap %.2f s\n", sp.ggaCount, sp.ggaLossPct(), sp.ggaMaxGap));
        t.append(String.format(Locale.US, "  bad UBX %d  bad NMEA %d\n", sp.ubxBad, sp.nmeaBad));
        t.append(String.format(Locale.US, "  config acks %d  naks %d\n", sp.valsetAcks, sp.valsetNaks));

        t.append("\nCaster    ");
        if (n == null) t.append("off\n");
        else {
            t.append(n.connected ? "connected" : n.status).append('\n');
            Rtcm3Parser r = s.rtcmParser;
            double ds = n.secondsSinceData();
            t.append(String.format(Locale.US, "  received %.1f kB  last %s\n", n.rtcmBytes / 1e3,
                    ds < 0 ? "never" : String.format(Locale.US, "%.1f s ago", ds)));
            t.append(String.format(Locale.US, "  frames %d  crc errors %d  gga sent %d\n", r.frames, r.crcErrors, n.ggaSent));
            t.append(String.format(Locale.US, "  to GPS %.1f kB  write errors %d\n", s.rtcmForwarded / 1e3, s.rtcmWriteErrors));
            t.append("  logging   ").append(s.loggingCorrections() ? "on" : "off").append('\n');
            String types = s.rtcmStats.summary(SystemClock.elapsedRealtime());
            if (!types.isEmpty()) t.append("  type   count   rate\n").append(types);
        }
        t.append(String.format(Locale.US, "\nIMU       %d samples\n", s.imuSamples()));
        Survey sv = s.survey();
        if (sv != null) {
            t.append(String.format(Locale.US, "\nCaptures  %d (points.csv)  mode %s\n", sv.captures(),
                    Settings.mode(p).label));
            Survey.RefView rv = sv.refView();
            if (rv != null) t.append(String.format(Locale.US, "  ref running: %.0f s to go, %d epochs%s\n",
                    rv.remainingS, rv.epochs, rv.paused ? ", PAUSED (not fixed)" : ""));
        }
        if (s.folder() != null) t.append("folder\n  ").append(s.folder()).append('\n');
        if (s.error != null) t.append("ERROR     ").append(s.error).append('\n');
        status.setText(t);
    }
}
