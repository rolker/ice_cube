package rink.icecube.logger;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import rink.icecube.logger.core.Gga;
import rink.icecube.logger.core.NtripClient;
import rink.icecube.logger.core.Rtcm3Parser;
import rink.icecube.logger.core.StreamParser;

/** Main screen: status and the controls. Settings are on their own screen. */
public final class MainActivity extends Activity {
    private TextView status, big;
    private Button gpsButton, casterButton, sessionButton, markButton;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private String usbAsked;               // device name we already asked USB permission for
    private boolean gpsAutostartPending;   // waiting for the Bluetooth permission answer

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            refresh();
            ui.postDelayed(this, 500);
        }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        c.setPadding(pad, pad, pad, pad);
        big = new TextView(this);
        big.setTextSize(30);
        big.setTypeface(Typeface.DEFAULT_BOLD);
        big.setPadding(0, 0, 0, pad / 2);
        c.addView(big);
        gpsButton = button(c, v -> onGps());
        casterButton = button(c, v -> onCaster());
        sessionButton = button(c, v -> onSession());
        markButton = button(c, v -> {
            LoggerService s = LoggerService.current;
            if (s != null && s.sessionOpen()) s.mark("control");
        });
        markButton.setText("Mark control point");
        Button settings = button(c, v -> startActivity(new Intent(this, SettingsActivity.class)));
        settings.setText("Settings");
        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextIsSelectable(true);
        status.setPadding(0, pad / 2, 0, 0);
        c.addView(status);
        ScrollView s = new ScrollView(this);
        s.addView(c);
        setContentView(s);

        requestRuntimePermissions();
        if (b == null) autostart(getIntent(), true);
    }

    private Button button(LinearLayout c, android.view.View.OnClickListener l) {
        Button x = new Button(this);
        x.setOnClickListener(l);
        c.addView(x);
        return x;
    }

    private void requestRuntimePermissions() {
        List<String> want = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)) {
            want.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (Build.VERSION.SDK_INT >= 31 && !granted(Manifest.permission.BLUETOOTH_CONNECT)) {
            want.add(Manifest.permission.BLUETOOTH_CONNECT);
        }
        if (!want.isEmpty()) requestPermissions(want.toArray(new String[0]), 1);
    }

    private boolean granted(String p) { return checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED; }

    static boolean bluetoothAllowed(Context c) {
        return Build.VERSION.SDK_INT < 31
                || c.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (gpsAutostartPending && bluetoothAllowed(this)) {
            gpsAutostartPending = false;
            send(LoggerService.ACTION_GPS_CONNECT, true);
        }
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        autostart(i, false);
    }

    /** The two launch options from Settings. A USB re-attach while open only restarts the GPS. */
    private void autostart(Intent launch, boolean appLaunch) {
        SharedPreferences p = Settings.prefs(this);
        LoggerService s = LoggerService.current;
        boolean usbLaunch = launch != null && UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(launch.getAction());
        if (appLaunch && p.getBoolean(Settings.CASTER_AT_LAUNCH, false)
                && !p.getString(Settings.HOST, "").trim().isEmpty() && (s == null || s.caster() == null)) {
            send(LoggerService.ACTION_CASTER_CONNECT, false);
        }
        if (!p.getBoolean(Settings.LOG_ON_GPS_CONNECT, false) || (s != null && s.gpsActive())) return;
        boolean bt = Settings.bluetooth(p);
        if (bt && !usbLaunch) {
            if (bluetoothAllowed(this)) send(LoggerService.ACTION_GPS_CONNECT, true);
            else gpsAutostartPending = true;
        } else if (!bt && usbLaunch) {
            send(LoggerService.ACTION_GPS_CONNECT, true);
        }
    }

    private void send(String action, boolean wait) {
        startForegroundService(new Intent(this, LoggerService.class).setAction(action)
                .putExtra(LoggerService.EXTRA_WAIT, wait));
    }

    @Override protected void onResume() { super.onResume(); ui.post(tick); }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }

    private static LoggerService live() {
        LoggerService s = LoggerService.current;
        return s != null && (s.sessionOpen() || s.gpsActive() || s.caster() != null) ? s : null;
    }

    private void onGps() {
        LoggerService s = live();
        if (s != null && s.gpsActive()) { send(LoggerService.ACTION_GPS_DISCONNECT, false); return; }
        if (Settings.bluetooth(Settings.prefs(this)) && !bluetoothAllowed(this)) {
            big.setText("Allow Bluetooth");
            requestRuntimePermissions();
            return;
        }
        usbAsked = null;
        send(LoggerService.ACTION_GPS_CONNECT, false);
    }

    private void onCaster() {
        LoggerService s = live();
        if (s != null && s.caster() != null) { send(LoggerService.ACTION_CASTER_DISCONNECT, false); return; }
        if (Settings.prefs(this).getString(Settings.HOST, "").trim().isEmpty()) {
            startActivity(new Intent(this, SettingsActivity.class));
            return;
        }
        send(LoggerService.ACTION_CASTER_CONNECT, false);
    }

    private void onSession() {
        LoggerService s = live();
        send(s != null ? LoggerService.ACTION_STOP : LoggerService.ACTION_START_SESSION, false);
    }

    /** Asks once per attached device for USB permission while the service waits for it. */
    private void askUsbPermission() {
        UsbManager m = (UsbManager) getSystemService(Context.USB_SERVICE);
        UsbDevice d = UsbLink.findGps(m);
        if (d == null || m.hasPermission(d) || d.getDeviceName().equals(usbAsked)) return;
        usbAsked = d.getDeviceName();
        Intent i = new Intent("rink.icecube.logger.USB_PERMISSION").setPackage(getPackageName());
        m.requestPermission(d, PendingIntent.getBroadcast(this, 0, i, PendingIntent.FLAG_MUTABLE));
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
        markButton.setEnabled(session);
        if (s != null && s.usbNeedsPermission()) askUsbPermission();

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
        if (s.folder() != null) t.append("folder\n  ").append(s.folder()).append('\n');
        if (s.error != null) t.append("ERROR     ").append(s.error).append('\n');
        status.setText(t);
    }
}
